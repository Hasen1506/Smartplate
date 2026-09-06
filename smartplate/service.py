"""Application services — orchestrate the kernel + domain into API-shaped views.

Thin layer between Flask routes and the engine. Assembles the full weekly plan
view (decisions, budget envelope, nutrition/carbon roll-ups, cooking coach,
household split, weather/festival annotations) so the UI can render everything
the 17 features produce.
"""
import datetime as dt

from . import config, db
from .domain import (carbon, checkout, community, festivals, health, household, intake,
                     ledger, models, nutrition, receipts, reverse_mode, weather)
from .kernel import (agent_brain, budget, explainability, optimizer, recommender,
                     scheduler, variance)


# --------------------------------------------------------------------------- #
# Plan lifecycle
# --------------------------------------------------------------------------- #
def create_plan(user_id: int, mode: str | None = None) -> int:
    user = models.get_user(user_id)
    mode = mode or user["mode"]
    ws = _next_monday()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO plans(user_id, week_start, mode, status, created_ts) VALUES (?,?,?,?,?)",
            (user_id, ws, mode, "active", dt.datetime.now().isoformat()))
        plan_id = cur.lastrowid
    scheduler.build_week(plan_id, ws)
    optimizer.optimize(plan_id)
    return plan_id


def reoptimize(plan_id: int, mode: str | None = None) -> dict:
    if mode:
        with db.cursor() as cur:
            cur.execute("UPDATE plans SET mode=? WHERE id=?", (mode, plan_id))
    return optimizer.optimize(plan_id)


def set_session_status(session_id: int, status: str, note: str = "") -> None:
    scheduler.set_status(session_id, status, note)


def command(plan_id: int, text: str) -> dict:
    """Natural-language-ish control via the deterministic brain (free)."""
    plan = models.get_plan(plan_id)
    intent = agent_brain.get_brain().parse_request(text)
    applied = {"intent": intent, "effect": "none"}
    if intent.get("action") == "set_mode" and intent.get("mode"):
        reoptimize(plan_id, intent["mode"])
        applied["effect"] = f"mode → {intent['mode']}"
        return applied
    day = intent.get("day")
    if intent.get("action") in ("skip", "snooze", "cook") and day is not None:
        status = {"skip": "skipped", "snooze": "snoozed", "cook": "cooked"}[intent["action"]]
        sessions = models.sessions_for_plan(plan_id)
        targets = [s for s in sessions if s["day"] == day
                   and (intent.get("meal") is None or s["meal"] == intent["meal"])]
        for s in targets:
            scheduler.set_status(s["id"], status, note=text)
        reoptimize(plan_id)
        applied["effect"] = f"{status} {len(targets)} session(s) on {models.DAYS[day]}"
    return applied


# --------------------------------------------------------------------------- #
# The big read: full plan view
# --------------------------------------------------------------------------- #
def plan_view(plan_id: int) -> dict:
    plan = models.get_plan(plan_id)
    if not plan:
        return {}
    user = models.get_user(plan["user_id"])
    decisions = models.decisions_for_plan(plan_id)
    spend_rows = [d for d in decisions if d["chosen_kind"] in ("delivery", "cook")]
    env = budget.envelope(user["weekly_budget"], spend_rows)

    nut = nutrition.summary([d["nutrition"] for d in spend_rows if d.get("nutrition")])
    targets = nutrition.targets_for(user)
    daily_nut = {k: round(v / 7, 1) for k, v in nut.items()}

    surge_saved = sum((d.get("time_shift") or {}).get("saving", 0) for d in decisions)
    carbon_total = round(sum(d.get("carbon_kg", 0) for d in spend_rows), 2)
    counts = _counts(decisions)

    coach = _coach(decisions)
    fests = festivals.for_week(plan["week_start"])
    grid = _grid(decisions)

    view = {
        "plan": {**plan, "mode_label": config.MODE_LABELS.get(plan["mode"], plan["mode"])},
        "user": {k: user[k] for k in ("id", "name", "city", "diet", "weekly_budget",
                                      "rating_floor", "mode", "allergens", "medical",
                                      "health_targets", "carbon_pref")},
        "budget": env,
        "nutrition": {"week": nut, "daily_avg": daily_nut, "daily_target": targets},
        "carbon": {"total_kg": carbon_total, "band": carbon.band(carbon_total / max(1, len(spend_rows)))},
        "surge_saved": round(surge_saved, 2),
        "counts": counts,
        "summary_reasons": explainability.plan_summary_reasons({
            "spend": env["spend"], "budget": env["budget"],
            "skipped": counts["skip"], "cooked": counts["cook"], "surge_saved": surge_saved}),
        "coach": coach,
        "grid": grid,
        "week_context": _week_context(user, plan, fests),
        # inverse-optimisation budget band for the planned sessions (docs §5):
        # don't make the user guess the cap — recommend it.
        "recommendation": recommender.recommend(user, [d["meal"] for d in decisions]),
    }
    if user.get("household_id"):
        view["household"] = _household_view(user, env["spend"])
    return view


def recommend_budget(plan_id: int) -> dict:
    """Standalone budget recommendation for a plan's sessions (Floor/Usual/Variety)."""
    plan = models.get_plan(plan_id)
    if not plan:
        return {}
    user = models.get_user(plan["user_id"])
    meals = [s["meal"] for s in models.sessions_for_plan(plan_id)]
    return recommender.recommend(user, meals)


def estimate_intake(text: str) -> dict:
    """Free-text 'I made X' → nutrition estimate to confirm (docs §7), no persistence."""
    return intake.parse(text)


def log_intake(user_id: int, text: str, *, iso_date: str | None = None, meal: str = "",
               source: str = "manual", plan_id: int | None = None) -> dict:
    """Parse free text AND persist it so the rolling ledger accumulates across days."""
    parsed = intake.parse(text)
    eid = intake.record(user_id, parsed["nutrition"], iso_date=iso_date, meal=meal,
                        source=source, plan_id=plan_id, note=text)
    return {"entry_id": eid, **parsed}


def nutrition_ledger(user_id: int) -> dict:
    """Rolling, nutrient-specific ledger from accumulated intake (docs §2.1):
    calories bank weekly, protein is daily adherence + today's distribution, sugar a cap."""
    user = models.get_user(user_id)
    if not user:
        return {}
    bd = intake.by_day(intake.recent(user_id, 30))
    targets = nutrition.targets_for(user)
    floor = (health.targets_for(user).get("protein_floor_g") or targets["protein_g"])
    return ledger.rolling_view(bd, kcal_target=targets["kcal"], protein_floor=floor,
                               sugar_cap=targets.get("sugar_g"))


def _counts(decisions):
    c = {"delivery": 0, "cook": 0, "skip": 0, "snoozed": 0, "ordered": 0}
    for d in decisions:
        k = d["chosen_kind"]
        c[k] = c.get(k, 0) + 1
    return c


def _grid(decisions):
    """Shape decisions into a day→meals grid for the UI."""
    grid = {i: {"day": models.DAYS[i], "meals": {}} for i in range(7)}
    for d in decisions:
        grid[d["day"]]["meals"][d["meal"]] = {
            "kind": d["chosen_kind"], "item": d["item_name"],
            "restaurant": d.get("restaurant_name") or "",
            "cost": round(d.get("cost", 0), 2), "rating": d.get("rating", 0),
            "substituted": bool(d.get("substituted")),
            "time_shift": d.get("time_shift"), "reasons": d.get("reasons", []),
            "nutrition": d.get("nutrition", {}), "carbon_kg": d.get("carbon_kg", 0),
            "session_id": d["session_id"],
        }
    return [grid[i] for i in range(7)]


def _coach(decisions):
    cook = [{"recipe_key": d.get("recipe_key"), "label": f"{models.DAYS[d['day']]} {d['meal']}"}
            for d in decisions if d["chosen_kind"] == "cook" and d.get("recipe_key")]
    from .domain import cooking_coach
    return cooking_coach.coach(cook)


def _household_view(user, spend):
    members = models.get_household_members(user["household_id"])
    hh = household.get(user["household_id"])
    merged = household.merged_profile(members)
    return {
        "name": hh["name"], "members": [m["name"] for m in members],
        "merged_allergens": merged["allergens"], "merged_medical": merged["medical"],
        "diet": merged["diet"], "split": household.split_cost(hh, members, spend),
    }


def _week_context(user, plan, fests):
    out = []
    for i in range(7):
        w = weather.for_day(user["city"], i)
        f = fests.get(i)
        out.append({
            "day": models.DAYS[i], "weather": w["condition"], "temp_c": w["temp_c"],
            "weather_note": weather.note(w["condition"]),
            "festival": f["name"] if f else None, "festival_effect": f["effect"] if f else None,
        })
    return out


# --------------------------------------------------------------------------- #
# Execution (orders), community, receipts, reverse mode
# --------------------------------------------------------------------------- #
def execution_preview(plan_id: int) -> dict:
    """Return the exact amount/items a client should show before authorisation."""
    plan = models.get_plan(plan_id)
    if not plan:
        return {}
    return checkout.preview(plan_id, models.decisions_for_plan(plan_id))


def execute(plan_id: int, *, expected_fingerprint: str | None = None,
            max_total: float | None = None) -> dict:
    review = execution_preview(plan_id)
    if not review:
        return {}
    checkout.validate(review, expected_fingerprint=expected_fingerprint, max_total=max_total)
    return variance.execute_plan(plan_id)


def grocery_basket(plan_id: int) -> dict:
    decisions = models.decisions_for_plan(plan_id)
    keys = [d["recipe_key"] for d in decisions if d["chosen_kind"] == "cook" and d.get("recipe_key")]
    return reverse_mode.basket_for_recipes(keys)


def list_community(city=None):
    return community.list_templates(city)


def adopt_template(template_id: int):
    return community.adopt(template_id)


def save_template(plan_id: int, title: str):
    plan = models.get_plan(plan_id)
    user = models.get_user(plan["user_id"])
    decisions = [d for d in models.decisions_for_plan(plan_id) if d["chosen_kind"] in ("delivery", "cook")]
    meta = {"city": user["city"], "budget": user["weekly_budget"], "mode": plan["mode"]}
    return community.save_template(user["name"], title, meta, decisions)


def record_receipts(plan_id: int) -> dict:
    plan = models.get_plan(plan_id)
    user = models.get_user(plan["user_id"])
    n = 0
    for d in models.decisions_for_plan(plan_id):
        if d["chosen_kind"] == "delivery":
            iso = (dt.date.fromisoformat(plan["week_start"]) + dt.timedelta(days=d["day"])).isoformat()
            receipts.record(user["id"], d, iso)
            n += 1
    return {"recorded": n}


def receipts_view(user_id: int):
    rows = receipts.list_for(user_id)
    business = sum(r["amount"] for r in rows if r["category"] == "business")
    return {"rows": rows, "business_total": round(business, 2),
            "total": round(sum(r["amount"] for r in rows), 2)}


def _next_monday() -> str:
    from .seed import demo_week_start
    return demo_week_start()
