"""The planner — a real MILP (PuLP/CBC) that selects one option per session.

This is "the agent." It is a constraint solver, not an LLM: planning a week costs
milliseconds of CPU (FEASIBILITY.md §1). Every one of the 17 gap features enters
here as either a HARD filter on the candidate set (allergens, diet, rating floor,
fasting, calendar/festival suspension) or a SOFT term in the objective (taste,
nutrition, health, carbon, surge, weather, festival bias).

Objective per candidate (minimised):
    w_cost·cost_norm  − w_taste·taste  + w_nutrition·nutri  + w_health·health
    + w_carbon·carbon·(0.5+pref)  + w_surge·surge_premium  + weather_bias + festival_bias
Budget is a HARD constraint; 'skip' is the always-feasible relief valve.
"""
import pulp

from .. import config, db
from ..domain import (allergens, carbon, fatigue, festivals, health, leftovers,
                      models, nutrition, reverse_mode, sentiment, surge, weather)
from ..integrations import calendar_sync
from . import explainability, scheduler

MAX_DELIVERY_CANDIDATES = 6
# People cook a few nights, not every meal (§1.3 "cook 3 nights and order 2").
# These are DEFAULT priors, not fixed truths (docs/optimization-and-ux.md §2): a
# global "everyone cooks ≤6" and "cooking always costs 0.35" are exactly the fake
# constants worth killing. They're overridable per user (learned/stated) via
# `_cook_cap` / `_cook_effort`; the constants remain only as the cold-start default.
MAX_COOK_PER_WEEK = 6
COOK_EFFORT_PENALTY = 0.35   # soft cost of cooking yourself (time/effort)
MAX_ITEM_REPEAT = 2          # variety: don't order the same dish more than twice a week


def _cook_cap(user: dict) -> int:
    """Cooks/week the user is willing to do — their stated rhythm, else the default.
    Stored in health_targets so no schema change is needed; absent ⇒ the default
    prior rather than an assumption imposed on them."""
    ht = user.get("health_targets") or {}
    try:
        return int(ht.get("max_cook_per_week", MAX_COOK_PER_WEEK))
    except (TypeError, ValueError):
        return MAX_COOK_PER_WEEK


def _cook_effort(user: dict, meal: str) -> float:
    """Personal, per-meal effort cost of cooking — not one global number."""
    ht = user.get("health_targets") or {}
    eff = ht.get("cook_effort")
    if isinstance(eff, dict):
        eff = eff.get(meal, eff.get("default"))
    if eff is None:
        return COOK_EFFORT_PENALTY
    try:
        return float(eff)
    except (TypeError, ValueError):
        return COOK_EFFORT_PENALTY


def _rating_filter(user: dict, items: list[dict]) -> list[dict]:
    """Apply the rating floor as a candidate filter.

    Default ("hard"): the user's ★ is a hard gate (current behaviour, and what the
    "never below your floor" promise rests on). Optional ("soft"): filter only at
    the low hard safety floor and let the user's aspirational ★ become a soft
    penalty (see `_rating_pen`) so a high ★ bends under budget instead of exploding
    it (docs §4)."""
    user_floor = float(user.get("rating_floor", 4.0))
    if config.RATING_FLOOR_MODE == "soft":
        floor = min(user_floor, config.HARD_SAFETY_FLOOR)
        return [it for it in items if it["restaurant_rating"] >= floor]
    return [it for it in items
            if it["restaurant_rating"] >= user_floor and it["item_rating"] >= user_floor - 0.3]


def _rating_pen(user: dict, item: dict) -> float:
    """Soft cost for a pick below the user's aspirational ★ (soft mode only)."""
    if config.RATING_FLOOR_MODE != "soft":
        return 0.0
    short = max(0.0, float(user.get("rating_floor", 4.0)) - item.get("restaurant_rating", 0))
    return round(0.8 * short, 4)


# --------------------------------------------------------------------------- #
# Context assembled once per plan
# --------------------------------------------------------------------------- #
def build_context(user: dict, plan: dict) -> dict:
    return {
        "menu": models.menu_for_city(user["city"]),
        "festivals": festivals.for_week(plan["week_start"]),
        "leftovers": leftovers.for_user(user["id"]),
        "calendar": calendar_sync.events_for(user["id"]),
        "weights": config.MODE_WEIGHTS.get(plan["mode"], config.MODE_WEIGHTS["balanced"]),
        # how far calories may drift before the nutrition term bites — the mode's
        # "what's allowed to give" knob (config.MODE_META).
        "nutri_tol": config.mode_meta(plan["mode"])["nutri_tol"],
        # variety nudge strength: the user's variety level as a fraction, or 0 when
        # the nudge is off (default) so the planner behaves exactly as before.
        "variety_frac": (fatigue.VARIETY_LEVELS.get(fatigue.variety_pref(user), 0.0)
                         if config.VARIETY_NUDGE == "on" else 0.0),
    }


def _taste(user: dict, item: dict) -> tuple[float, dict]:
    senti = sentiment.aggregate(item.get("reviews", []))
    base = item.get("item_rating", 4.0) / 5.0
    senti_norm = (senti["score"] + 1) / 2
    pop = item.get("popularity", 0.5)
    score = 0.45 * base + 0.35 * senti_norm + 0.20 * pop
    return round(score, 4), senti


def _delivery_candidate(user, plan, session, item, ctx):
    day, meal = session["day"], session["meal"]
    cond = weather.for_day(user["city"], day)["condition"]
    base_cost = item["price"] + item["delivery_fee"]

    # Surge + optional time-shift (calendar-aware) -------------------------- #
    peak_mult = surge.predict(user["city"], day, meal, cond)
    conflict = calendar_sync.conflicts_with_peak(ctx["calendar"], day, meal)
    shift = surge.time_shift_option(user["city"], day, meal, cond, base_cost)
    time_shift = None
    surge_mult = peak_mult
    if conflict and shift:                         # meeting at peak → move it (§5.1.2)
        time_shift = {**shift, "cause": f"clash with '{conflict['title']}'"}
        surge_mult = shift["offpeak_mult"]
    elif shift:                                    # opportunistic surge dodge (§3.3)
        time_shift = {**shift, "cause": "surge"}
        surge_mult = shift["offpeak_mult"]
    cost = round(base_cost * surge_mult, 2)

    taste, senti = _taste(user, item)
    nutri = nutrition.penalty(user, meal, item, tol=ctx["nutri_tol"])
    hp = health.protein_penalty(user, item)
    cpen = carbon.penalty(item)
    return {
        "kind": "delivery",
        "restaurant_id": item["restaurant_id"], "restaurant_name": item["restaurant_name"],
        "item_id": item["id"], "item_name": item["name"], "rating": item["restaurant_rating"],
        "cost": cost, "base_cost": base_cost, "surge_mult": round(surge_mult, 3),
        "time_shift": time_shift, "flaky": item.get("flaky", 0), "rating_pen": _rating_pen(user, item),
        "novelty_bonus": round(config.VARIETY_NUDGE_W * fatigue.novelty(item) * ctx.get("variety_frac", 0.0), 4),
        "taste": taste, "sentiment": senti, "nutri": nutri, "health": hp, "carbon_pen": cpen,
        "carbon_kg": carbon.estimate(item), "weather_cond": cond,
        "weather_bias": weather.taste_bias(cond, item),
        "festival_bias": festivals.taste_bias(ctx["festivals"].get(day), item),
        "nutrition": {k: item.get(k, 0) for k in ("kcal", "protein_g", "carbs_g", "fat_g", "sugar_g")},
        "tags": item.get("tags", []),
    }


def _cook_candidate(user, session, ctx):
    r = reverse_mode.cook_candidate(user, session["meal"])
    if not r:
        return None
    nutri = nutrition.penalty(user, session["meal"], r, tol=ctx["nutri_tol"])
    return {
        "kind": "cook", "recipe_key": r["key"], "item_name": f"Cook: {r['name']}",
        "restaurant_name": "Home kitchen", "rating": 5.0, "cost": float(r["cost"]),
        "surge_mult": 1.0, "time_shift": None, "taste": 0.62, "sentiment": {"score": 0, "n": 0, "label": ""},
        "nutri": nutri, "health": health.protein_penalty(user, r), "carbon_pen": carbon.penalty(r),
        "cook_effort": _cook_effort(user, session["meal"]),
        "carbon_kg": r["carbon_kg"], "weather_bias": 0.0, "festival_bias": 0.0,
        "nutrition": {k: r.get(k, 0) for k in ("kcal", "protein_g", "carbs_g", "fat_g", "sugar_g")},
        "tags": ["home"],
    }


def build_candidates(user: dict, plan: dict, session: dict, ctx: dict) -> list[dict]:
    """All options for one session, hard constraints already applied. Always
    returns at least a 'skip' so the MILP stays feasible."""
    day, meal = session["day"], session["meal"]

    # --- forced-skip conditions (hard) ----------------------------------- #
    travel = calendar_sync.day_is_travel(ctx["calendar"], day)
    if travel:
        return [_skip(forced=True, reason=f"Out of town ({travel['title']}) — day suspended.")]
    fest = ctx["festivals"].get(day)
    if festivals.suspends_session(fest, meal):
        eff = "fasting" if fest["effect"] == "fast" else "holiday"
        return [_skip(forced=True, reason=f"{fest['name']} ({eff}) — session suspended.")]

    # --- leftover forces a zero-cost cook (respect the fridge) ----------- #
    lo = leftovers.covers(ctx["leftovers"], day, meal)
    if lo:
        return [{
            "kind": "cook", "recipe_key": None, "item_name": f"Leftover: {lo['label']}",
            "restaurant_name": "Your fridge", "rating": 5.0, "cost": 0.0, "surge_mult": 1.0,
            "time_shift": None, "taste": 0.6, "sentiment": {"score": 0, "n": 0, "label": ""},
            "nutri": 0.0, "health": 0.0, "carbon_pen": 0.0, "carbon_kg": 0.1,
            "weather_bias": 0.0, "festival_bias": 0.0, "nutrition": {}, "tags": ["home"],
            "leftover": lo["label"], "forced": True,
        }]

    # --- delivery candidates (apply ALL hard filters) -------------------- #
    scheduled_min = models.MEAL_WINDOWS[meal][1]
    fasting = health.in_fasting_window(user, scheduled_min)
    cands = []
    if not fasting:
        safe = allergens.safe_items(user, ctx["menu"])             # §5.1.1 hard
        safe = _rating_filter(user, safe)                          # rating floor (hard, or soft+safety)
        # keep the most promising few (cheap-but-decent) to bound the MILP
        safe.sort(key=lambda it: (it["price"] + it["delivery_fee"]) - 40 * (it["item_rating"] / 5))
        for it in safe[: MAX_DELIVERY_CANDIDATES * 2]:
            cands.append(_delivery_candidate(user, plan, session, it, ctx))
        cands.sort(key=lambda c: c["cost"] - 60 * c["taste"])
        cands = cands[:MAX_DELIVERY_CANDIDATES]

    cook = _cook_candidate(user, session, ctx)
    if cook:
        cands.append(cook)

    skip_reason = "No safe option within your rating floor and budget — session skipped."
    if fasting:
        skip_reason = "Inside your fasting window — kept clear (cook/skip only)."
    cands.append(_skip(forced=False, reason=skip_reason))
    return cands


def _skip(forced: bool, reason: str) -> dict:
    return {
        "kind": "skip", "item_name": "Skip", "restaurant_name": "", "rating": 0.0,
        "cost": 0.0, "surge_mult": 1.0, "time_shift": None, "taste": 0.0,
        "sentiment": {"score": 0, "n": 0, "label": ""}, "nutri": 0.0, "health": 0.0,
        "carbon_pen": 0.0, "carbon_kg": 0.0, "weather_bias": 0.0, "festival_bias": 0.0,
        "nutrition": {}, "tags": [], "forced": forced, "skip_reason": reason,
    }


# --------------------------------------------------------------------------- #
# Objective + MILP
# --------------------------------------------------------------------------- #
def _objective(cand, w, ref_cost, carbon_pref, skip_penalty):
    if cand["kind"] == "skip":
        return 0.0 if cand.get("forced") else skip_penalty
    cost_norm = cand["cost"] / ref_cost if ref_cost else 0.0
    surge_premium = max(0.0, cand["surge_mult"] - 1.0)
    # cooking-yourself carries a soft, *personal* effort cost (not for free leftovers)
    effort = cand.get("cook_effort", COOK_EFFORT_PENALTY) if (cand["kind"] == "cook" and cand.get("recipe_key")) else 0.0
    # carbon is OFF by default: carbon_pref==0 ⇒ no influence (no silent 0.5 baseline).
    return round(
        w["cost"] * cost_norm
        - w["taste"] * cand["taste"]
        + w["nutrition"] * cand["nutri"]
        + w["health"] * cand["health"]
        + w["carbon"] * cand["carbon_pen"] * carbon_pref
        + w["surge"] * surge_premium
        + effort
        + cand.get("rating_pen", 0.0)
        - cand.get("novelty_bonus", 0.0)         # ↓ objective ⇒ novel picks preferred (when nudge on)
        + cand["weather_bias"] + cand["festival_bias"],
        5,
    )


SKIP_PENALTY = {"comfort": 5.0, "balanced": 3.0, "survival": 1.6}


def optimize(plan_id: int) -> dict:
    plan = models.get_plan(plan_id)
    user = models.get_user(plan["user_id"])
    ctx = build_context(user, plan)
    w = ctx["weights"]
    sessions = [s for s in models.sessions_for_plan(plan_id)]
    active = [s for s in sessions if s["status"] == "active"]

    ref_cost = max(1.0, user["weekly_budget"] / max(1, len(active)))
    skip_penalty = SKIP_PENALTY.get(plan["mode"], 3.0)

    prob = pulp.LpProblem("smartplate_week", pulp.LpMinimize)
    x = {}                     # (session_id, idx) -> binary var
    cand_map = {}              # session_id -> [candidates]
    obj_terms, budget_terms, cook_vars = [], [], []
    item_vars = {}             # item_id -> [vars] for the variety cap

    for s in active:
        cands = build_candidates(user, plan, s, ctx)
        cand_map[s["id"]] = cands
        choice_vars = []
        for i, c in enumerate(cands):
            v = pulp.LpVariable(f"x_{s['id']}_{i}", cat="Binary")
            x[(s["id"], i)] = v
            choice_vars.append(v)
            obj_terms.append(_objective(c, w, ref_cost, user["carbon_pref"], skip_penalty) * v)
            budget_terms.append(c["cost"] * v)
            if c["kind"] == "cook" and c.get("recipe_key"):   # countable cook (not free leftover)
                cook_vars.append(v)
            if c["kind"] == "delivery":
                item_vars.setdefault(c["item_id"], []).append(v)
        prob += pulp.lpSum(choice_vars) == 1            # exactly one option per session

    prob += pulp.lpSum(obj_terms)
    prob += pulp.lpSum(budget_terms) <= user["weekly_budget"]   # HARD budget envelope
    if cook_vars:
        prob += pulp.lpSum(cook_vars) <= _cook_cap(user)        # the user's cook rhythm, not a fixed 6
    for vlist in item_vars.values():                            # variety: cap repeats per dish
        if len(vlist) > MAX_ITEM_REPEAT:
            prob += pulp.lpSum(vlist) <= MAX_ITEM_REPEAT
    prob.solve(pulp.PULP_CBC_CMD(msg=False))

    decisions = _persist_decisions(plan, user, sessions, active, cand_map, x, ctx)
    return {
        "status": pulp.LpStatus[prob.status],
        "plan_id": plan_id,
        "decisions": decisions,
        "diagnostics": _diagnostics(user, decisions),
    }


def _diagnostics(user: dict, decisions: list[dict]) -> dict:
    """Feasibility + shortfall surface (docs §1): what bound, and by how much.

    Reports spend vs the budget ceiling and the per-day nutrition gap, plus the
    single binding signal the UI leads with ("you're protein-short → repair", or
    "budget is the wall here")."""
    spend_rows = [d for d in decisions if d.get("chosen_kind") in ("delivery", "cook")]
    spend = round(sum(d.get("cost", 0) for d in spend_rows), 2)
    budget = user["weekly_budget"]
    planned_days = len({d["day"] for d in spend_rows}) or 1
    sf = nutrition.shortfall([d.get("nutrition") or {} for d in spend_rows],
                             nutrition.targets_for(user), days=planned_days)
    if spend >= 0.98 * budget:
        binding = "budget"
    elif sf["protein_gap_per_day"] <= -8:
        binding = "nutrition (protein)"
    elif sf["kcal_gap_per_day"] <= -250:
        binding = "nutrition (calories)"
    else:
        binding = "comfortable"
    return {"spend": spend, "budget": round(budget, 2),
            "headroom": round(budget - spend, 2), "binding": binding, "nutrition": sf}


def _persist_decisions(plan, user, sessions, active, cand_map, x, ctx):
    decisions = []
    # Rebuild decisions for every session EXCEPT those already 'ordered' (a real
    # placed order we must not lose). Active sessions get re-planned; snooze/skip/
    # cook are user overrides that replace any stale plan decision.
    with db.cursor() as cur:
        cur.execute(
            "DELETE FROM decisions WHERE plan_id=? AND session_id IN "
            "(SELECT id FROM sessions WHERE plan_id=? AND status!='ordered')",
            (plan["id"], plan["id"]))
    for s in sessions:
        if s["status"] != "active":
            # honour snoozed/skipped/cooked/ordered sessions as-is
            decisions.append(_carry_session(plan, s))
            continue
        cands = cand_map[s["id"]]
        chosen = next((cands[i] for i in range(len(cands))
                       if x.get((s["id"], i)) and pulp.value(x[(s["id"], i)]) > 0.5), cands[-1])
        decisions.append(_write_decision(plan, user, s, chosen, ctx))
    return decisions


def _decision_context(user, session, chosen, ctx):
    day = session["day"]
    cond = chosen.get("weather_cond", weather.for_day(user["city"], day)["condition"])
    fest = ctx["festivals"].get(day)
    nut = chosen.get("nutrition", {})
    nflag = None
    if chosen["kind"] == "delivery" and nut.get("protein_g", 0) >= 25:
        nflag = f"Protein-forward ({nut['protein_g']:.0f}g) — supports your health target."
    return {
        "rating_floor": user["rating_floor"],
        "sentiment": chosen.get("sentiment"),
        "weather_note": weather.note(cond),
        "festival": fest["name"] if (fest and chosen.get("festival_bias", 0) < 0) else None,
        "leftover": chosen.get("leftover"),
        "nutrition_flag": nflag,
        "carbon_band": carbon.band(chosen.get("carbon_kg", 0)) if chosen["kind"] == "delivery" else None,
    }


def _write_decision(plan, user, session, chosen, ctx):
    dctx = _decision_context(user, session, chosen, ctx)
    reasons = explainability.reasons_for(
        {**chosen, "chosen_kind": chosen["kind"], "day": session["day"]}, dctx)
    row = {
        "session_id": session["id"], "plan_id": plan["id"], "chosen_kind": chosen["kind"],
        "restaurant_id": chosen.get("restaurant_id"), "item_id": chosen.get("item_id"),
        "item_name": chosen.get("item_name"), "cost": chosen.get("cost", 0),
        "surge_mult": chosen.get("surge_mult", 1.0), "substituted": 0,
        "reasons": db.jd(reasons), "nutrition": db.jd(chosen.get("nutrition", {})),
        "carbon_kg": chosen.get("carbon_kg", 0),
    }
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO decisions(session_id, plan_id, chosen_kind, restaurant_id, item_id, "
            "item_name, cost, surge_mult, substituted, reasons, nutrition, carbon_kg, "
            "recipe_key, time_shift, restaurant_name, rating, idempotency_key, created_ts) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'))",
            (row["session_id"], row["plan_id"], row["chosen_kind"], row["restaurant_id"],
             row["item_id"], row["item_name"], row["cost"], row["surge_mult"], 0,
             row["reasons"], row["nutrition"], row["carbon_kg"],
             chosen.get("recipe_key"), db.jd(chosen["time_shift"]) if chosen.get("time_shift") else None,
             chosen.get("restaurant_name"), chosen.get("rating", 0), None),
        )
        row["id"] = cur.lastrowid
    return {**row, "day": session["day"], "meal": session["meal"],
            "reasons": reasons, "nutrition": chosen.get("nutrition", {}),
            "time_shift": chosen.get("time_shift"), "recipe_key": chosen.get("recipe_key"),
            "restaurant_name": chosen.get("restaurant_name"), "rating": chosen.get("rating", 0),
            "status": "planned"}


def _carry_session(plan, session):
    mapping = {"snoozed": "Snoozed", "skipped": "Skipped", "cooked": "Cooked at home", "ordered": "Ordered"}
    status = session["status"]
    # A placed order keeps its persisted decision so cost/history stay in the view.
    if status == "ordered":
        with db.cursor() as cur:
            row = cur.execute(
                "SELECT * FROM decisions WHERE session_id=? ORDER BY id DESC LIMIT 1",
                (session["id"],)).fetchone()
        if row:
            d = db.row_to_dict(row)
            d["reasons"] = db.jl(d["reasons"])
            d["nutrition"] = db.jl(d["nutrition"], {})
            d["time_shift"] = db.jl(d["time_shift"], None) if d.get("time_shift") else None
            d["day"], d["meal"], d["status"] = session["day"], session["meal"], status
            return d
    # User overrides (snooze/skip/cook): persist a carry row so it shows in the grid.
    label = mapping.get(status, status)
    reasons = db.jd([session["note"] or label])
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO decisions(session_id, plan_id, chosen_kind, item_name, cost, "
            "reasons, nutrition, restaurant_name, created_ts) "
            "VALUES (?,?,?,?,0,?, '{}', '', datetime('now'))",
            (session["id"], plan["id"], status, label, reasons))
        did = cur.lastrowid
    return {
        "id": did, "session_id": session["id"], "plan_id": plan["id"],
        "chosen_kind": status, "item_name": label, "cost": 0,
        "day": session["day"], "meal": session["meal"],
        "reasons": [session["note"] or label], "nutrition": {},
        "status": status, "restaurant_name": "",
    }
