"""Application services — orchestrate the kernel + domain into API-shaped views.

Thin layer between Flask routes and the engine. Assembles the full weekly plan
view (decisions, budget envelope, nutrition/carbon roll-ups, cooking coach,
household split, weather/festival annotations) so the UI can render everything
the 17 features produce.
"""
import datetime as dt
import math

from . import clock, config, db
from .domain import (carbon, checkout, community, festivals, health, household, intake, learning,
                     live_catalog,
                     ledger, models, nutrition, profile, receipts, reverse_mode, taste, timing, weather)
from .kernel import (agent_brain, budget, explainability, optimizer, recommender,
                     scheduler, variance)


# --------------------------------------------------------------------------- #
# Plan lifecycle
# --------------------------------------------------------------------------- #
def create_plan(user_id: int, mode: str | None = None, week_start: str | None = None) -> int:
    """Plan the current week (the rest of it, budget prorated) — or, when that week
    is already planned or nearly over, the next one."""
    user = models.get_user(user_id)
    if not user:
        raise ValueError('Profile not found')
    mode = mode or user["mode"]
    if mode not in config.MODE_LABELS:
        raise ValueError('Unknown planning mode')
    ws = week_start or _plan_week_start(user_id)
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO plans(user_id, week_start, mode, status, created_ts) VALUES (?,?,?,?,?)",
            (user_id, ws, mode, "active", optimizer.now().isoformat()))
        plan_id = cur.lastrowid
    scheduler.build_week(plan_id, ws, meals=profile.meals_planned(user))
    optimizer.optimize(plan_id)
    return plan_id


def _plan_week_start(user_id: int) -> str:
    today = optimizer.now().date()
    monday = today - dt.timedelta(days=today.weekday())
    start = monday if today.weekday() <= 5 else monday + dt.timedelta(days=7)   # Sunday → next week
    with db.cursor() as cur:
        taken = {r["week_start"] for r in cur.execute("SELECT week_start FROM plans WHERE user_id=?", (user_id,))}
    while start.isoformat() in taken:                    # "plan another week" → the following one
        start += dt.timedelta(days=7)
    return start.isoformat()


def reoptimize(plan_id: int, mode: str | None = None) -> dict:
    if mode:
        if mode not in config.MODE_LABELS:
            raise ValueError('Unknown planning mode')
        with db.cursor() as cur:
            changed = cur.execute("SELECT mode FROM plans WHERE id=?", (plan_id,)).fetchone()["mode"] != mode
            cur.execute("UPDATE plans SET mode=? WHERE id=?", (mode, plan_id))
        if changed:                       # a new mode is an explicit ask for a different week
            return optimizer.optimize(plan_id, stable=False)
    return optimizer.optimize(plan_id)


def current_plan(user_id):
    """The plan to show now: the latest one, rolled forward once its week is over."""
    with db.cursor() as cur:
        row = cur.execute('SELECT id, week_start FROM plans WHERE user_id=? ORDER BY week_start DESC, id DESC LIMIT 1',
                          (user_id,)).fetchone()
    if row and dt.date.fromisoformat(row['week_start']) + dt.timedelta(days=7) <= optimizer.now().date():
        row = None
    return plan_view(row['id'] if row else create_plan(user_id))


def update_preferences(user_id, body):
    """Validate the entire edit before writing. Replan only unplaced meals."""
    user = models.get_user(user_id)
    if not user:
        raise KeyError("Profile not found")
    allowed = {'name', 'weekly_budget', 'rating_floor', 'diet', 'allergens', 'medical',
               'kcal', 'protein_g', 'max_cook_per_week'}
    if set(body) - allowed:
        raise ValueError('Unsupported preference field')
    updates = {}
    if 'name' in body:
        if not isinstance(body['name'], str) or not 1 <= len(body['name'].strip()) <= 80:
            raise ValueError('Enter a name of 1–80 characters')
        updates['name'] = body['name'].strip()
    if 'diet' in body:
        if body['diet'] not in ('veg', 'nonveg', 'vegan'):
            raise ValueError('Choose vegetarian, non-vegetarian, or vegan')
        updates['diet'] = body['diet']
    if 'name' in body and not user.get('access_hash'):
        raise ValueError("Sample profiles are shared by every visitor, so their name can't be changed. "
                         "Set up your own profile instead.")
    for key, lo, hi in [('weekly_budget', profile.BUDGET_MIN, profile.BUDGET_MAX), ('rating_floor', 0, 5),
                         ('kcal', 1, 20000), ('protein_g', 1, 1000), ('max_cook_per_week', 0, 21)]:
        if key not in body:
            continue
        value = body[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not lo <= value <= hi:
            raise ValueError(f'{key} must be a number between {lo} and {hi}')
        if key == 'max_cook_per_week':
            if int(value) != value:
                raise ValueError('Cooking meals must be a whole number')
            user['health_targets'][key] = int(value)
            updates['health_targets'] = db.jd(user['health_targets'])
        elif key in ('kcal', 'protein_g'):
            user['nutrition_targets'][key] = value
            updates['nutrition_targets'] = db.jd(user['nutrition_targets'])
            if key == 'protein_g':
                user['health_targets']['protein_floor_g'] = value
                updates['health_targets'] = db.jd(user['health_targets'])
        else:
            updates[key] = value
    for key, values in [('allergens', {'peanut', 'dairy', 'gluten', 'egg', 'soy', 'shellfish', 'fish', 'sesame', 'tree_nut'}),
                        ('medical', {'diabetes', 'hypertension', 'celiac'})]:
        if key in body:
            if not isinstance(body[key], list) or any(not isinstance(v, str) or v not in values for v in body[key]):
                raise ValueError(f'Unknown {key} selection')
            updates[key] = db.jd(sorted(set(body[key])))
    if not updates:
        raise ValueError('No preferences supplied')
    profile.check_caps(updates.get('weekly_budget', user['weekly_budget']), user['prefs'].get('daily_cap'))
    with db.cursor() as cur:
        cur.execute('UPDATE users SET ' + ', '.join(f'{key}=?' for key in updates) + ' WHERE id=?',
                    (*updates.values(), user_id))
        row = cur.execute('SELECT id FROM plans WHERE user_id=? ORDER BY id DESC LIMIT 1', (user_id,)).fetchone()
    pid = row['id'] if row else create_plan(user_id)
    reoptimize(pid)
    return plan_view(pid)


def set_session_status(session_id: int, status: str, note: str = "") -> None:
    before = models.get_session(session_id)
    scheduler.set_status(session_id, status, note)
    if before and before["status"] == "confirmed" and status != "confirmed":
        with db.cursor() as cur:        # undoing "I had it" also removes its logged intake
            cur.execute("DELETE FROM intake_log WHERE note=?", (f"session:{session_id}",))
            # ...and its expense receipt: a meal never had is not an expense (L-01)
            cur.execute("DELETE FROM receipts WHERE decision_id IN (SELECT id FROM decisions WHERE session_id=?)",
                        (session_id,))


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
    at = optimizer.now()
    for d in decisions:
        d["past"] = d["session_status"] == "active" and scheduler.is_past(d, at)
    # A meal whose real Swiggy cart was checked counts at the bill's total, not the estimate.
    from .domain import week_orders
    real = week_orders.real_totals(plan_id)
    for d in decisions:
        r = real.get(d["session_id"])
        if r and d["chosen_kind"] == "delivery":
            d["planned_cost"] = r["planned_cost"] if r["planned_cost"] is not None else d["cost"]
            d["cost"] = r["to_pay"]
            d["real_bill"] = True
    # a past meal the user never confirmed is unknown, not spent (reconcile, don't assume)
    spend_rows = [d for d in decisions if d["chosen_kind"] in ("delivery", "cook") and not d["past"]]
    cap = optimizer.week_cap(user, plan)
    env = budget.envelope(cap, spend_rows)
    env["weekly_budget"] = user["weekly_budget"]
    env["prorated"] = cap < user["weekly_budget"]
    env["daily_cap"] = profile.daily_cap(user)

    nut = nutrition.summary([d["nutrition"] for d in spend_rows if d.get("nutrition")])
    targets = nutrition.targets_for(user)
    # average over the days that actually have planned meals (a plan made on Friday
    # covers 3 days; dividing by 7 would make every target look badly missed)
    planned_days = max(1, len({d["day"] for d in spend_rows}))
    daily_nut = {k: round(v / planned_days, 1) for k, v in nut.items()}

    surge_saved = sum((d.get("time_shift") or {}).get("saving", 0) for d in decisions)
    carbon_total = round(sum(d.get("carbon_kg", 0) for d in spend_rows), 2)
    counts = _counts(decisions)

    coach = _coach(decisions, user=user, plan_id=plan_id)
    fests = festivals.for_week(plan["week_start"])
    wx = weather.week(user["city"], plan["week_start"])
    sig = taste.signals(user["id"])
    grid = _grid(decisions, plan=plan, user=user, wx=wx, sig=sig)

    view = {
        "plan": {**plan, "mode_label": config.MODE_LABELS.get(plan["mode"], plan["mode"])},
        "user": {**{k: user[k] for k in ("id", "name", "city", "diet", "weekly_budget",
                                         "rating_floor", "mode", "allergens", "medical", "observances",
                                         "health_targets", "nutrition_targets", "carbon_pref", "prefs")},
                 "favourites": sorted(sig["favourites"]), "meals": profile.meals_planned(user)},
        "budget": env,
        "nutrition": {"week": nut, "daily_avg": daily_nut, "daily_target": targets, "days": planned_days},
        "carbon": {"total_kg": carbon_total, "band": carbon.band(carbon_total / max(1, len(spend_rows)))},
        "surge_saved": round(surge_saved, 2),
        "counts": counts,
        "summary_reasons": explainability.plan_summary_reasons({
            "spend": env["spend"], "budget": env["budget"],
            "skipped": counts["skip"], "cooked": counts["cook"], "surge_saved": surge_saved}),
        "coach": coach,
        "grid": grid,
        "week_context": _week_context(user, plan, fests, wx),
        "weather_source": "live" if any(w["source"] == "live" for w in wx.values()) else wx[0].get("source", "none"),
        "learning": {"ratings": sig["ratings"], "orders": sig["orders"], "favourites": len(sig["favourites"])},
        # inverse-optimisation budget band for the planned sessions (docs §5):
        # don't make the user guess the cap — recommend it.
        "recommendation": recommender.recommend(user, [d["meal"] for d in decisions]),
    }
    if user.get("household_id"):
        view["household"] = _household_view(user, env["spend"], spend_rows)
        eat = {d["session_id"]: household.eaters(d, view["household"]["_members"], user) for d in decisions}
        for day in grid:
            for cell in day["meals"].values():
                cell["eaters"] = eat.get(cell["session_id"], [])
        view["household"].pop("_members")
    # Swiggy's own photo of a planned dish, once Swiggy has shown it (search_menu)
    from .integrations import swiggy_live
    cells = [c for day in grid for c in day["meals"].values() if c["kind"] == "delivery" and c.get("item_id")]
    photos = swiggy_live.known_photos(user["id"], [c["item_id"] for c in cells]) if cells else {}
    for c in cells:
        if photos.get(c["item_id"]):
            c["image"] = photos[c["item_id"]]
    from . import everyday
    view["next_up"] = everyday.next_up(view, at)
    view["heads_up"] = everyday.heads_up(view, user, plan, decisions, at)
    view["learned"] = learning.chips(user["id"])
    view["source"] = live_catalog.source_for(user["id"])
    return view


def recommend_budget(plan_id: int) -> dict:
    """Standalone budget recommendation for a plan's sessions (Floor/Usual/Variety)."""
    plan = models.get_plan(plan_id)
    if not plan:
        return {}
    user = models.get_user(plan["user_id"])
    meals = [s["meal"] for s in models.sessions_for_plan(plan_id)]
    return recommender.recommend(user, meals)


def _meal_text(text) -> str:
    if not isinstance(text, str):
        raise ValueError("Describe what you ate in words, e.g. \"2 idli and sambar\"")
    return text


def estimate_intake(text: str) -> dict:
    """Free-text 'I made X' → nutrition estimate to confirm (docs §7), no persistence."""
    return intake.parse(_meal_text(text))


def log_intake(user_id: int, text: str, *, iso_date: str | None = None, meal: str = "",
               source: str = "manual", plan_id: int | None = None) -> dict:
    """Parse free text AND persist it so the rolling ledger accumulates across days."""
    text = _meal_text(text)
    iso_date, meal, source = intake.validate_entry(iso_date, meal, source)
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
        k = {'skipped': 'skip', 'cooked': 'cook'}.get(d['chosen_kind'], d['chosen_kind'])
        c[k] = c.get(k, 0) + 1
        if d.get('session_status') == 'ordered' and k != 'ordered':
            c['ordered'] += 1
    return c


def _grid(decisions, *, plan=None, user=None, wx=None, sig=None):
    """Shape decisions into a day→meals grid for the UI."""
    grid = {i: {"day": models.DAYS[i], "meals": {}} for i in range(7)}
    if plan:
        for i in range(7):
            grid[i]["date"] = models.session_date(plan, i)
    ratings, reasons = {}, {}
    if user:
        with db.cursor() as cur:
            ratings = {r["session_id"]: r["score"] for r in cur.execute(
                "SELECT session_id, score FROM ratings WHERE user_id=? ORDER BY id", (user["id"],))}
            reasons = {}
            for r in cur.execute("SELECT session_id, reason FROM rating_reasons WHERE user_id=? ORDER BY id", (user["id"],)):
                reasons.setdefault(r["session_id"], []).append(r["reason"])
    etas = {}
    with db.cursor() as cur:
        etas = {r["id"]: r["eta_min"] for r in cur.execute("SELECT id, eta_min FROM restaurants")}
    for d in decisions:
        extra = {}
        if plan is not None:
            status = "past" if d.get("past") else d["session_status"]
            extra = {"status": status, "pinned": bool(d.get("pinned")), "restaurant_id": d.get("restaurant_id"),
                     "item_id": d.get("item_id"), "recipe_key": d.get("recipe_key"),
                     "rating_given": ratings.get(d["session_id"]),
                     "reasons_given": reasons.get(d["session_id"], []),
                     "usual": bool(sig and d.get("restaurant_id") in sig["favourites"])}
            if d["chosen_kind"] == "delivery":
                cond = (wx or {}).get(d["day"], {}).get("condition", "clear")
                extra["order"] = timing.order_plan(d["meal"], eta_min=etas.get(d.get("restaurant_id")),
                                                   time_shift=d.get("time_shift"), condition=cond)
                extra["handoff_url"] = timing.swiggy_handoff(d.get("restaurant_name") or "", d.get("item_name") or "")
                if d.get("delivery_fee") is not None:   # live dish: estimated until a real bill shows the fee
                    extra["delivery_fee"] = {"amount": d["delivery_fee"], "estimated": bool(d.get("fee_estimated"))}
                if d.get("real_bill"):
                    extra["real_bill"] = True
                    extra["planned_cost"] = round(d["planned_cost"], 2)
            elif d["chosen_kind"] == "cook":
                extra["cost_basis"] = reverse_mode.COST_BASIS
        grid[d["day"]]["meals"][d["meal"]] = {
            "kind": d["chosen_kind"], "item": d["item_name"],
            "restaurant": d.get("restaurant_name") or "",
            "cost": round(d.get("cost", 0), 2), "rating": d.get("rating", 0),
            "substituted": bool(d.get("substituted")),
            "time_shift": d.get("time_shift"), "reasons": d.get("reasons", []),
            "nutrition": d.get("nutrition", {}), "carbon_kg": d.get("carbon_kg", 0),
            "session_id": d["session_id"], "status": d['session_status'],
            **extra,
        }
    return [grid[i] for i in range(7)]


def _coach(decisions, user=None, plan_id=None):
    members = models.get_household_members(user["household_id"]) if user and user.get("household_id") else []
    cook = [{"recipe_key": d.get("recipe_key"), "label": f"{models.DAYS[d['day']]} {d['meal']}",
             "servings": len(household.eaters(d, members, user)) if members else 1,
             "upcoming": d.get("session_status") == "active" and not d.get("past")}
            for d in decisions if d["chosen_kind"] == "cook" and d.get("recipe_key")]
    from .domain import cooking_coach
    return cooking_coach.coach(cook, user=user, swaps=grocery_swaps(plan_id) if plan_id else None,
                                have=grocery_have(plan_id) if plan_id else ())


def grocery_swaps(plan_id: int) -> dict:
    with db.cursor() as cur:
        rows = cur.execute("SELECT token, swap_token, reason FROM grocery_swaps WHERE plan_id=?", (plan_id,)).fetchall()
    return {r["token"]: {"swap_token": r["swap_token"], "reason": r["reason"]} for r in rows}


def _plan_ingredients(plan_id: int) -> set[str]:
    """Ingredients of this week's cook meals (recipe ingredients and grocery lines)."""
    out = set()
    for d in models.decisions_for_plan(plan_id):
        r = reverse_mode.recipe(d.get("recipe_key") or "") if d["chosen_kind"] == "cook" else None
        if r:
            out |= {i["token"] for i in r.get("ingredients", [])}
            out |= {b["token"] for b in r["basket"] if b.get("token")}
    return out


def swap_options(plan_id: int, token: str) -> dict:
    """Safe swaps for one ingredient of this week's cooking, for everyone at the table."""
    from .domain import ingredients
    plan = models.get_plan(plan_id)
    user = models.get_user(plan["user_id"])
    if token not in _plan_ingredients(plan_id):
        raise ValueError("That ingredient isn't in this week's cooking")
    return ingredients.substitutes(token, ingredients.people_of(user))


def set_grocery_swap(plan_id: int, body: dict) -> dict:
    """Use `swap_token` instead of `token` this week (or undo with swap_token null)."""
    from .domain import epicure, ingredients
    token, swap_token, reason = body.get("token"), body.get("swap_token"), body.get("reason", "swap")
    if not isinstance(token, str) or token not in _plan_ingredients(plan_id):
        raise ValueError("That ingredient isn't in this week's cooking")
    if reason not in ("swap", "out_of_stock"):
        raise ValueError("Reason must be swap or out_of_stock")
    with db.cursor() as cur:
        cur.execute("DELETE FROM grocery_swaps WHERE plan_id=? AND token=?", (plan_id, token))
    if swap_token is not None:
        if epicure.get() is None:
            raise ValueError("Ingredient swaps aren't available right now")
        user = models.get_user(models.get_plan(plan_id)["user_id"])
        allowed = {o["token"] for o in ingredients.substitutes(token, ingredients.people_of(user),
                                                               k=len(ingredients.PANTRY))["options"]}
        if swap_token not in allowed:
            raise ValueError("That swap isn't safe for everyone eating, or doesn't do the same job in the dish")
        with db.cursor() as cur:
            cur.execute("INSERT INTO grocery_swaps(plan_id, token, swap_token, reason, created_ts) VALUES (?,?,?,?,?)",
                        (plan_id, token, swap_token, reason, clock.now().isoformat(timespec="seconds")))
    return plan_view(plan_id)


def grocery_have(plan_id: int) -> set[str]:
    with db.cursor() as cur:
        return {r["item"] for r in cur.execute("SELECT item FROM grocery_have WHERE plan_id=?", (plan_id,))}


def set_grocery_have(plan_id: int, body: dict) -> dict:
    """Tick a grocery line you already have at home (it stays listed, costs nothing)."""
    item, have = body.get("item"), body.get("have")
    if not isinstance(have, bool):
        raise ValueError("Say whether you have it (true or false)")
    view = plan_view(plan_id)
    if not isinstance(item, str) or item not in {b["name"] for b in view["coach"]["basket"]["items"]}:
        raise ValueError("That item isn't on this week's grocery list")
    with db.cursor() as cur:
        if have:
            cur.execute("INSERT OR IGNORE INTO grocery_have(plan_id, item) VALUES (?,?)", (plan_id, item))
        else:
            cur.execute("DELETE FROM grocery_have WHERE plan_id=? AND item=?", (plan_id, item))
    return plan_view(plan_id)


def _household_view(user, spend, meals=None):
    members = models.get_household_members(user["household_id"])
    hh = household.get(user["household_id"])
    merged = household.merged_profile(members)
    return {
        "name": hh["name"], "members": [m["name"] for m in members],
        "people": [{"id": m["id"], "name": m["name"], "diet": m["diet"], "allergens": m["allergens"],
                    "medical": m["medical"], "you": m["id"] == user["id"], "managed": household.is_managed(m),
                    "meals": household.member_meals(m, user)} for m in members],
        "rules": household.combined_rules(members),
        "merged_allergens": merged["allergens"], "merged_medical": merged["medical"],
        "diet": merged["diet"], "split_method": hh["split"],
        "split": household.split_cost(hh, members, spend, meals, user),
        "_members": members,
    }


# --------------------------------------------------------------------------- #
# Household: set-up and people (backend in domain/household.py)
# --------------------------------------------------------------------------- #
def _household_changed(user_id: int) -> dict:
    """Everyone's rules may have changed: re-plan the open meals and return the view."""
    view = current_plan(user_id)
    optimizer.optimize(view["plan"]["id"])
    return plan_view(view["plan"]["id"])


def household_action(user_id: int, action: str, body: dict | None = None, member_id: int | None = None) -> dict:
    user = models.get_user(user_id)
    if not user:
        raise KeyError("Profile not found")
    if not user.get("access_hash"):
        raise ValueError("Sample profiles are shared by every visitor. Set up your own profile to share a household.")
    body = body or {}
    if action == "create":
        household.create(user, body.get("name"))
    elif action == "update":
        household.update(user, body)
    elif action == "add":
        household.add_member(user, body)
    elif action == "edit":
        household.update_member(user, member_id, body)
    elif action == "remove":
        household.remove_member(user, member_id)
    elif action == "leave":
        household.leave(user)
    return _household_changed(user_id)


def set_eaters(session_id: int, value) -> dict:
    session = models.get_session(session_id)
    plan = models.get_plan(session["plan_id"])
    user = models.get_user(plan["user_id"])
    if not user.get("household_id"):
        raise ValueError("Only a household plan has people to tick")
    household.set_eaters(session, models.get_household_members(user["household_id"]), value)
    return plan_view(plan["id"])


def _week_context(user, plan, fests, wx=None):
    wx = wx or weather.week(user["city"], plan["week_start"])
    all_fests = festivals.for_week_all(plan["week_start"])
    out = []
    for i in range(7):
        w = wx[i]
        f = fests.get(i)
        fast = festivals.fast_for(all_fests.get(i), user)
        if f and f["effect"] == "fast" and not festivals.applies_to(f, user):
            f = None                                   # someone else's fast is not this user's news
        out.append({
            "day": models.DAYS[i], "date": models.session_date(plan, i),
            "weather": w["condition"], "temp_c": w["temp_c"], "rain_prob": w.get("rain_prob", 0),
            "weather_source": w.get("source", "none"), "weather_note": weather.note(w["condition"]),
            "festival": f["name"] if f else None, "festival_effect": f["effect"] if f else None,
            "festival_note": f.get("note") if f else None, "festival_approx": bool(f and f.get("approx")),
            "your_fast": fast["name"] if fast else None,
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
    result = variance.execute_plan(plan_id)
    record_receipts(plan_id)
    return result


def grocery_basket(plan_id: int) -> dict:
    """The week's grocery list: the upcoming cook meals, scaled to who eats them."""
    return plan_view(plan_id)["coach"]["basket"]


ANONYMOUS_AUTHOR = "A SmartPlate user"


def list_community(city=None):
    return community.list_templates(city)


def adopt_template(template_id: int, count: bool = True):
    return community.adopt(template_id, count=count)


def save_template(plan_id: int, title: str, show_name: bool = False):
    plan = models.get_plan(plan_id)
    user = models.get_user(plan["user_id"]) if plan else None
    if not plan or not user:
        raise KeyError("Plan not found")
    if not user.get("access_hash"):
        raise ValueError("Sample profiles are shared by every visitor. Set up your own profile to share a week.")
    if not isinstance(title, str) or not 1 <= len(title.strip()) <= 80:
        raise ValueError("Give the week a title of 1–80 characters")
    title = title.strip()
    decisions = [d for d in models.decisions_for_plan(plan_id) if d["chosen_kind"] in ("delivery", "cook")]
    meta = {"city": user["city"], "budget": user["weekly_budget"], "mode": plan["mode"]}
    # The display name is published only when the person explicitly opts in (L-14).
    author = user["name"] if show_name is True else ANONYMOUS_AUTHOR
    return community.save_template(author, title, meta, decisions, author_user_id=user["id"])


def record_receipts(plan_id: int) -> dict:
    plan = models.get_plan(plan_id)
    user = models.get_user(plan["user_id"])
    from .domain import week_orders
    real = week_orders.real_totals(plan_id)
    n = 0
    for d in models.decisions_for_plan(plan_id):
        if d["chosen_kind"] == "delivery" and d['session_status'] in ('ordered', 'confirmed'):
            iso = (dt.date.fromisoformat(plan["week_start"]) + dt.timedelta(days=d["day"])).isoformat()
            bill = real.get(d["session_id"])
            # A checked Swiggy cart's real total is the expense, not the planner's estimate.
            rid = receipts.record(user["id"], {**d, "cost": bill["to_pay"]} if bill else d, iso)
            if bill:
                with db.cursor() as cur:
                    cur.execute("UPDATE receipts SET amount=? WHERE id=?", (bill["to_pay"], rid))
            n += 1
    return {"recorded": n}


def order_history(plan_id):
    with db.cursor() as cur:
        rows = cur.execute('''SELECT o.*, d.item_name, d.substituted, s.day, s.meal
            FROM orders o JOIN decisions d ON d.id=o.decision_id
            JOIN sessions s ON s.id=d.session_id WHERE d.plan_id=? ORDER BY o.id''', (plan_id,)).fetchall()
    results = [{'item': r['item_name'], 'day': r['day'], 'meal': r['meal'],
                'cost': r['amount'], 'placed': r['state'] == 'placed', 'state': r['state'],
                'provider_order_id': r['provider_order_id'], 'idempotency_key': r['idempotency_key'],
                'substituted': bool(r['substituted']), 'substitution': None,
                'log': db.jl(r['log'])} for r in rows]
    return {'results': results, 'attempted': len(results),
            'placed': sum(r['placed'] for r in results),
            'substituted': sum(r['substituted'] for r in results),
            'failed': sum(not r['placed'] for r in results)}


def receipts_view(user_id: int):
    from .domain import week_orders
    rows = receipts.list_for(user_id)
    # Which amounts are a real Swiggy bill (a checked cart's total) and which are estimates.
    with db.cursor() as cur:
        billed = {r["id"]: r["to_pay"] for r in cur.execute(
            "SELECT r.id, q.to_pay FROM receipts r JOIN decisions d ON d.id=r.decision_id "
            "JOIN order_queue q ON q.session_id=d.session_id WHERE r.user_id=? AND q.to_pay IS NOT NULL "
            "AND q.state IN (%s)" % ",".join("?" * len(week_orders.CHECKED)),
            (user_id, *week_orders.CHECKED)).fetchall()}
    for r in rows:
        r["real"] = r["id"] in billed and abs(float(billed[r["id"]]) - float(r["amount"])) < 0.005
    business = sum(r["amount"] for r in rows if r["category"] == "business")
    return {"rows": rows, "business_total": round(business, 2),
            "total": round(sum(r["amount"] for r in rows), 2)}



# --------------------------------------------------------------------------- #
# Weekly recap: what was spent and eaten, against the plan and the targets
# --------------------------------------------------------------------------- #
def week_recap(plan_id: int) -> dict:
    """The week so far, from what actually happened: meals marked as had (or ordered)
    are spent; open meals are still planned; past meals nobody marked are unknown and
    count as neither. Nutrition comes from the intake log for the plan's seven days."""
    plan = models.get_plan(plan_id)
    user = models.get_user(plan["user_id"])
    at = optimizer.now()
    decisions = models.decisions_for_plan(plan_id)
    had, ahead, unknown, skipped = [], [], [], 0
    for d in decisions:
        if d["session_status"] in ("ordered", "confirmed") and d["chosen_kind"] in ("delivery", "cook"):
            had.append(d)
        elif d["session_status"] == "skipped" or d["chosen_kind"] == "skip":
            skipped += 1
        elif d["session_status"] == "active" and scheduler.is_past(d, at):
            unknown.append(d)
        elif d["session_status"] == "active" and d["chosen_kind"] in ("delivery", "cook"):
            ahead.append(d)
    cap = optimizer.week_cap(user, plan)
    spent = round(sum(d["cost"] for d in had), 2)
    planned = round(sum(d["cost"] for d in ahead), 2)
    places = {}
    for d in had:
        if d["chosen_kind"] == "delivery" and d.get("restaurant_name"):
            places[d["restaurant_name"]] = places.get(d["restaurant_name"], 0) + 1
    top = max(places.items(), key=lambda kv: (kv[1], kv[0])) if places else None

    start = dt.date.fromisoformat(plan["week_start"])
    days = [(start + dt.timedelta(days=i)).isoformat() for i in range(7)]
    with db.cursor() as cur:
        rows = cur.execute("SELECT * FROM intake_log WHERE user_id=? AND iso_date>=? AND iso_date<=? ORDER BY iso_date, id",
                           (user["id"], days[0], days[-1])).fetchall()
    logged = intake.by_day([db.row_to_dict(r) for r in rows])
    targets = nutrition.targets_for(user)
    protein_goal = health.targets_for(user).get("protein_floor_g") or targets["protein_g"]
    n_days = len(logged)
    avg = {k: round(sum(v[k] for v in logged.values()) / n_days, 1) for k in ("kcal", "protein_g")} if n_days else None

    prev = None
    with db.cursor() as cur:
        row = cur.execute("SELECT id FROM plans WHERE user_id=? AND week_start=? ORDER BY id DESC LIMIT 1",
                          (user["id"], (start - dt.timedelta(days=7)).isoformat())).fetchone()
    if row:
        prev = round(sum(d["cost"] for d in models.decisions_for_plan(row["id"])
                         if d["session_status"] in ("ordered", "confirmed")
                         and d["chosen_kind"] in ("delivery", "cook")), 2)

    out = {
        "week_start": plan["week_start"],
        "spend": {"budget": cap, "spent": spent, "planned": planned,
                  "left": round(cap - spent - planned, 2),
                  "delivery": round(sum(d["cost"] for d in had if d["chosen_kind"] == "delivery"), 2),
                  "cooked": round(sum(d["cost"] for d in had if d["chosen_kind"] == "cook"), 2),
                  "last_week": prev},
        "meals": {"had": len(had), "ordered": sum(1 for d in had if d["chosen_kind"] == "delivery"),
                  "cooked": sum(1 for d in had if d["chosen_kind"] == "cook"),
                  "ahead": len(ahead), "skipped": skipped, "unmarked": len(unknown)},
        "top_place": {"name": top[0], "times": top[1]} if top else None,
        "nutrition": {"days_logged": n_days, "avg": avg,
                      "target": {"kcal": targets["kcal"], "protein_g": protein_goal},
                      "protein_days": sum(1 for v in logged.values() if v["protein_g"] >= protein_goal)},
    }
    if user.get("household_id"):
        members = models.get_household_members(user["household_id"])
        hh = household.get(user["household_id"])
        out["household"] = {"name": hh["name"], "split_method": hh["split"],
                            "split": household.split_cost(hh, members, spent, had, user)}
    return out
