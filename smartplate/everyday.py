"""The everyday flows — what a person actually does with SmartPlate.

The optimiser, ledger and constraint engine stay underneath; this module is the thin,
opinionated surface on top of them:

  • set up in five answers            → create_profile / update_setup / suggest_budget
  • see only your usual places        → options (a capped shortlist, never an endless feed)
  • pick, move or confirm a meal      → choose / swap / confirm / rate
  • know what's next and what's coming → next_up / heads_up

Every write re-optimises the rest of the week, so one tap never leaves the budget or
the hard rules inconsistent.
"""
import datetime as dt

from . import access, clock, db, service
from .domain import (allergens, epicure, festivals, flavour, intake, learning, models, profile,
                     reverse_mode, taste)
from .kernel import optimizer, recommender, scheduler

CITY = "Chennai"                 # profiles' default city; restaurant dishes come only from live Swiggy menus
SHORTLIST_PLACES = 4
SHORTLIST_DISHES = 3
SHORTLIST_NEW = 3
COOK_BY_ANSWER = {"never": 0, "sometimes": 2, "often": 5, "most": 10}


# --------------------------------------------------------------------------- #
# Set-up
# --------------------------------------------------------------------------- #
RHYTHM_CHOICES = ("order", "cook", "skip")
KCAL_RANGE, PROTEIN_RANGE, SHARE_RANGE, REPEAT_RANGE = (1000, 4500), (20, 250), (0.1, 0.7), (1, 7)


def _pop_rhythm(body: dict) -> dict | None:
    """The user's real eating rhythm: for each meal, do they order it, cook it, or skip
    it? (Instead of assuming three ordered meals.) Returns None when not given."""
    if not isinstance(body, dict) or "rhythm" not in body:
        return None
    r = body.pop("rhythm")
    if not isinstance(r, dict) or not r or set(r) - set(profile.MEALS):
        raise ValueError("Rhythm must say, for breakfast, lunch and dinner, whether you order, cook or skip it")
    if any(v not in RHYTHM_CHOICES for v in r.values()):
        raise ValueError("For each meal choose order, cook or skip")
    full = {m: r.get(m, "skip") for m in profile.MEALS}
    if all(v == "skip" for v in full.values()):
        raise ValueError("Pick at least one meal you order or cook")
    return full


def _pop_tuning(body: dict) -> dict | None:
    """User-editable planner settings: calorie split, daily targets, variety cap."""
    if not isinstance(body, dict) or "tuning" not in body:
        return None
    t = body.pop("tuning")
    if not isinstance(t, dict) or set(t) - {"meal_share", "kcal", "protein_g", "max_repeat"}:
        raise ValueError("Tuning takes meal_share, kcal, protein_g and max_repeat")
    out = {}

    def num(key, lo, hi, cast=float):
        v = t[key]
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not lo <= v <= hi:
            raise ValueError(f"{key} must be between {lo} and {hi}")
        return cast(v)
    if "meal_share" in t:
        share = t["meal_share"]
        if not isinstance(share, dict) or not share or set(share) - set(profile.MEALS):
            raise ValueError("meal_share maps breakfast/lunch/dinner to a fraction of the day's calories")
        for k, v in share.items():
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not SHARE_RANGE[0] <= v <= SHARE_RANGE[1]:
                raise ValueError(f"Each meal's share must be between {SHARE_RANGE[0]:.0%} and {SHARE_RANGE[1]:.0%}")
        total = sum(share.values())
        if len(share) == len(profile.MEALS) and abs(total - 1) > 0.02:
            raise ValueError("The calorie split must add up to 100%")
        out["meal_share"] = {k: round(float(v), 3) for k, v in share.items()}
    if "kcal" in t:
        out["kcal"] = num("kcal", *KCAL_RANGE, cast=int)
    if "protein_g" in t:
        out["protein_g"] = num("protein_g", *PROTEIN_RANGE, cast=int)
    if "max_repeat" in t:
        out["max_repeat"] = num("max_repeat", *REPEAT_RANGE, cast=int)
    return out


def json_or(raw, fallback: dict) -> dict:
    import json
    return dict(json.loads(raw)) if raw else dict(fallback or {})


def _apply_tuning(nt: dict, ht: dict, tuning: dict | None) -> None:
    if not tuning:
        return
    for k in ("meal_share", "kcal", "protein_g"):
        if k in tuning:
            nt[k] = tuning[k]
    if "protein_g" in tuning:
        ht["protein_floor_g"] = tuning["protein_g"]
    if "max_repeat" in tuning:
        ht["max_item_repeat"] = tuning["max_repeat"]
def _targets_blob(data: dict, existing: dict | None = None) -> tuple[dict, dict, list[str]]:
    targets, working = profile.targets_from_goal(data.get("goal", "none"), data.get("body"))
    nt = dict(existing or {})
    nt.update(targets)
    if data.get("variety"):
        nt["variety"] = data["variety"]
    return nt, targets, working


def create_profile(body: dict) -> dict:
    # Optional sign-in set in the same step, so the profile is never tied to one browser.
    login = password = None
    if isinstance(body, dict) and ("login" in body or "password" in body):
        from . import accounts
        login, password = accounts.check_new_login(body.pop("login", None), body.pop("password", None))
    cook = body.pop("cook", "never") if isinstance(body, dict) else "never"
    if cook not in COOK_BY_ANSWER:
        raise ValueError("Choose how often you cook")
    rhythm = _pop_rhythm(body)
    tuning = _pop_tuning(body)
    if rhythm:
        body["meals"] = [m for m in profile.MEALS if rhythm[m] != "skip"]
    data = profile.validate_setup(body)
    if "weekly_budget" not in data:
        raise ValueError("Set a weekly food budget")
    data.setdefault("name", "Me")
    data.setdefault("goal", "none")
    data.setdefault("meals", list(profile.MEALS))
    nt, targets, working = _targets_blob(data)
    key, key_hash = access.new_key()
    ht = {"protein_floor_g": targets["protein_g"], "max_cook_per_week": COOK_BY_ANSWER[cook]}
    _apply_tuning(nt, ht, tuning)
    prefs = {"setup_done": True, "meals": data["meals"], "daily_cap": data.get("daily_cap"),
             "goal": data["goal"], "body": data.get("body"), "area": data.get("area", ""),
             "cook": cook, "targets_working": working}
    if rhythm:
        prefs["rhythm"] = rhythm
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO users(name, city, diet, weekly_budget, rating_floor, mode, allergens, medical, "
            "nutrition_targets, health_targets, carbon_pref, observances, prefs, access_hash) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (data["name"], CITY, data.get("diet", "nonveg"), data["weekly_budget"],
             data.get("rating_floor", 4.0), "balanced", db.jd(data.get("allergens", [])),
             db.jd(data.get("medical", [])), db.jd(nt), db.jd(ht), 0.0,
             db.jd(data.get("observances", [])), db.jd(prefs), key_hash))
        user_id = cur.lastrowid
        if login:
            from . import accounts
            accounts.insert_login(cur, user_id, login, password)
    taste.set_favourites(user_id, data.get("favourites", []))
    pid = service.create_plan(user_id)
    # the key is returned exactly once; only its hash is stored
    return {**service.plan_view(pid), "access_key": key, "recovery_code": f"{user_id}.{key}", "login": login}


def update_setup(user_id: int, body: dict) -> dict:
    user = models.get_user(user_id)
    if not user:
        raise KeyError("Profile not found")
    cook = body.pop("cook", None) if isinstance(body, dict) else None
    if cook is not None and cook not in COOK_BY_ANSWER:
        raise ValueError("Choose how often you cook")
    rhythm = _pop_rhythm(body)
    tuning = _pop_tuning(body)
    if rhythm:
        body["meals"] = [m for m in profile.MEALS if rhythm[m] != "skip"]
    data = profile.validate_setup(body)
    if not data and cook is None and tuning is None:
        raise ValueError("Nothing to update")
    prefs = dict(user["prefs"])
    if "name" in data and not user.get("access_hash"):
        raise ValueError("Sample profiles are shared by every visitor, so their name can't be changed. "
                         "Set up your own profile instead.")
    profile.check_caps(data.get("weekly_budget", user["weekly_budget"]),
                       data["daily_cap"] if "daily_cap" in data else prefs.get("daily_cap"))
    updates = {}
    for key in ("name", "diet", "weekly_budget", "rating_floor"):
        if key in data:
            updates[key] = data[key]
    for key in ("allergens", "medical", "observances"):
        if key in data:
            updates[key] = db.jd(data[key])
    for key in ("meals", "daily_cap", "area", "cuisine_tilt"):
        if key in data:
            prefs[key] = data[key]
    ht = dict(user["health_targets"])
    if cook is not None:
        prefs["cook"] = cook
        ht["max_cook_per_week"] = COOK_BY_ANSWER[cook]
    if "goal" in data or "body" in data or "variety" in data:
        goal = data.get("goal", prefs.get("goal", "none"))
        body_stats = data["body"] if "body" in data else prefs.get("body")
        nt, targets, working = _targets_blob({"goal": goal, "body": body_stats, "variety": data.get("variety")},
                                             user["nutrition_targets"])
        if "goal" in data or "body" in data:
            prefs.update(goal=goal, body=body_stats, targets_working=working)
            ht["protein_floor_g"] = targets["protein_g"]
        else:
            nt = {**user["nutrition_targets"], "variety": data["variety"]}
        updates["nutrition_targets"] = db.jd(nt)
    if rhythm:
        prefs["rhythm"] = rhythm
    elif "meals" in data and prefs.get("rhythm"):      # a plain meal list keeps cook/order for those meals
        prefs["rhythm"] = {m: (prefs["rhythm"].get(m, "order") if m in data["meals"] else "skip")
                           for m in profile.MEALS}
    if tuning:
        nt_now = json_or(updates.get("nutrition_targets"), user["nutrition_targets"])
        _apply_tuning(nt_now, ht, tuning)
        updates["nutrition_targets"] = db.jd(nt_now)
    updates["health_targets"] = db.jd(ht)
    prefs["setup_done"] = True
    updates["prefs"] = db.jd(prefs)
    with db.cursor() as cur:
        cur.execute("UPDATE users SET " + ", ".join(f"{k}=?" for k in updates) + " WHERE id=?",
                    (*updates.values(), user_id))
    if "favourites" in data:
        taste.set_favourites(user_id, data["favourites"])
    view = service.current_plan(user_id)
    if "meals" in data:
        _sync_meals(view["plan"]["id"], data["meals"])
    optimizer.optimize(view["plan"]["id"])
    return service.plan_view(view["plan"]["id"])


def _sync_meals(plan_id: int, meals: list[str]) -> None:
    """Add/remove open sessions so the plan matches the meals the user wants planned.
    Spent meals (ordered / confirmed) are never removed."""
    plan = models.get_plan(plan_id)
    sessions = models.sessions_for_plan(plan_id)
    have = {(s["day"], s["meal"]) for s in sessions}
    with db.cursor() as cur:
        for s in sessions:
            if s["meal"] not in meals and s["status"] not in ("ordered", "confirmed"):
                cur.execute("DELETE FROM decisions WHERE session_id=?", (s["id"],))
                cur.execute("DELETE FROM sessions WHERE id=?", (s["id"],))
        for day in range(7):
            for meal in meals:
                if (day, meal) not in have:
                    cur.execute("INSERT INTO sessions(plan_id, day, meal, scheduled_ts, status) "
                                "VALUES (?,?,?,?, 'active')",
                                (plan_id, day, meal, scheduler.trigger_ts(plan["week_start"], day, meal)))


def suggest_budget(body: dict) -> dict:
    """Before a profile exists: "your usual places cost about ₹X a week for these meals"."""
    data = profile.validate_setup({k: v for k, v in (body or {}).items() if k != "cook"})
    targets, _ = profile.targets_from_goal(data.get("goal", "none"), data.get("body"))
    user = {"id": 0, "city": CITY, "diet": data.get("diet", "nonveg"), "allergens": data.get("allergens", []),
            "medical": data.get("medical", []), "rating_floor": data.get("rating_floor", 4.0),
            "nutrition_targets": {**targets, "variety": data.get("variety", "light")}}
    menu = models.menu_for_city(CITY)
    favs = set(data.get("favourites", []))
    if favs and any(it["restaurant_id"] in favs for it in menu):
        menu = [it for it in menu if it["restaurant_id"] in favs]
    meals = data.get("meals", list(profile.MEALS)) * 7
    rec = recommender.recommend(user, meals, menu=menu)
    round50 = lambda v: int(round(v / 50.0)) * 50                              # noqa: E731
    if rec.get("feasible"):
        rec["suggested"] = round50(rec["usual"]["total"])
        rec["tight"] = round50(rec["floor"]["total"])
        rec["roomy"] = round50(rec["variety"]["total"] * 1.1)
    return rec


def list_restaurants(user_id: int | None = None, *, diet=None, allergens_=None, medical=None) -> list[dict]:
    """The favourites picker: each nearby place with how many of its dishes fit you."""
    who = models.get_user(user_id) if user_id else {
        "diet": diet or "nonveg", "allergens": allergens_ or [], "medical": medical or []}
    favs = taste.favourites(user_id) if user_id else set()
    menu = models.menu_for_city(CITY)
    out = []
    for r in models.restaurants_for_city(CITY):
        items = [it for it in menu if it["restaurant_id"] == r["id"]]
        fit = [it for it in items if not allergens.violates(who, it)]
        prices = [it["price"] for it in fit]
        out.append({"id": r["id"], "name": r["name"], "rating": r["rating"], "cuisines": r["cuisines"],
                    "eta_min": r["eta_min"], "delivery_fee": r["delivery_fee"],
                    "dishes_total": len(items), "dishes_fit": len(fit),
                    "price_from": min(prices) if prices else None, "price_to": max(prices) if prices else None,
                    "examples": [it["name"] for it in fit[:2]], "favourite": r["id"] in favs})
    out.sort(key=lambda r: (not r["favourite"], r["dishes_fit"] == 0, -r["rating"]))
    return out


def toggle_favourite(user_id: int, restaurant_id: int) -> dict:
    state = taste.toggle_favourite(user_id, restaurant_id)
    view = service.current_plan(user_id)
    optimizer.optimize(view["plan"]["id"])
    return {"favourite": state, "plan": service.plan_view(view["plan"]["id"])}


# --------------------------------------------------------------------------- #
# The shortlist — "show me the menu", without the endless scroll
# --------------------------------------------------------------------------- #
def _session_bundle(session_id: int):
    session = models.get_session(session_id)
    if not session:
        raise KeyError("Meal not found")
    plan = models.get_plan(session["plan_id"])
    user = models.get_user(plan["user_id"])
    return session, plan, user


def _limits(plan, user, session) -> dict:
    """Money left for this one meal if everything else in the week stays as planned."""
    decisions = models.decisions_for_plan(plan["id"])
    at = optimizer.now()
    others = [d for d in decisions if d["session_id"] != session["id"] and d["chosen_kind"] in ("delivery", "cook")
              and not (d["session_status"] == "active" and scheduler.is_past(d, at))]
    left_week = optimizer.week_cap(user, plan) - sum(d["cost"] for d in others)
    out = {"left_week": round(left_week, 2), "left_day": None}
    cap = profile.daily_cap(user)
    if cap:
        out["left_day"] = round(cap - sum(d["cost"] for d in others if d["day"] == session["day"]), 2)
    return out


def options(session_id: int) -> dict:
    session, plan, user = _session_bundle(session_id)
    ctx = optimizer.build_context(user, plan)
    w = ctx["weights"]
    ref = max(1.0, optimizer.week_cap(user, plan) / 21)
    limits = _limits(plan, user, session)
    ceiling = min(v for v in (limits["left_week"], limits["left_day"]) if v is not None)
    current = next((d for d in models.decisions_for_plan(plan["id"]) if d["session_id"] == session_id), None)

    menu = ctx["menu"]
    safe_all = allergens.safe_items(user, menu)
    safe = [it for it in safe_all if optimizer.meal_suitable(it, session["meal"])]
    floor = float(user["rating_floor"])
    rated_ok = [it for it in safe if it["restaurant_rating"] >= floor]
    disliked = ctx["taste"]["disliked"]
    pool = [it for it in rated_ok if it["id"] not in disliked]
    favs = ctx["taste"]["favourites"]
    shifts = []

    def dish(it):
        c = optimizer._delivery_candidate(user, plan, session, it, ctx)
        score = optimizer._objective(c, w, ref, user["carbon_pref"], 3.0)
        n = c["nutrition"]
        why = []
        why.append("fits your budget" if c["cost"] <= ceiling else f"₹{c['cost'] - ceiling:.0f} over what's left")
        if n.get("protein_g", 0) >= 25:
            why.append(f"{n['protein_g']:.0f} g protein")
        if c["weather_bias"] < 0:
            why.append({"rain": "rainy-day comfort", "storm": "rainy-day comfort",
                        "hot": "light for the heat"}.get(c["weather_cond"], "suits the weather"))
        if c["festival_bias"] < 0:
            why.append("festive pick")
        if it["id"] in ctx["taste"]["liked"]:
            why.append("you liked this")
        if c.get("time_shift"):
            shifts.append(c["time_shift"])
        return {"item_id": it["id"], "name": it["name"], "price": c["cost"], "rating": it["item_rating"],
                "kcal": n.get("kcal"), "protein_g": n.get("protein_g"), "tags": it.get("tags", []),
                "fits": c["cost"] <= ceiling, "why": " · ".join(why), "score": score,
                "nutrition_estimated": bool(it.get("nutrition_estimated")),
                "current": bool(current and current.get("item_id") == it["id"]),
                "instructions": allergens.order_instructions(user, {**n, "tags": it.get("tags", [])})}

    by_place = {}
    for it in pool:
        by_place.setdefault(it["restaurant_id"], {"meta": it, "dishes": []})["dishes"].append(dish(it))
    groups = []
    for rid, g in by_place.items():
        g["dishes"].sort(key=lambda d: (not d["fits"], d["score"]))
        meta = g["meta"]
        groups.append({"restaurant_id": rid, "restaurant": meta["restaurant_name"], "rating": meta["restaurant_rating"],
                       "eta_min": meta["eta_min"], "favourite": rid in favs, "dishes": g["dishes"][:SHORTLIST_DISHES],
                       "more": max(0, len(g["dishes"]) - SHORTLIST_DISHES), "_best": g["dishes"][0]["score"]})
    groups.sort(key=lambda g: g["_best"])
    usual = [g for g in groups if g["favourite"]] if favs else groups
    new = sorted((d | {"restaurant": g["restaurant"], "restaurant_id": g["restaurant_id"]}
                  for g in groups if favs and not g["favourite"] for d in g["dishes"]),
                 key=lambda d: (not d["fits"], d["score"]))[:SHORTLIST_NEW]
    for g in groups:
        g.pop("_best")
    from .integrations import swiggy_live
    photos = swiggy_live.known_photos(user["id"], [d["item_id"] for g in groups for d in g["dishes"]])
    for d in [d for g in groups for d in g["dishes"]] + new:
        d["image"] = photos.get(d["item_id"])
    n = optimizer.portions(user, session, ctx)
    cook_all = [r for r in reverse_mode.RECIPES if r["key"] in reverse_mode.RECIPE_BY_MEAL.get(session["meal"], [])]
    cooks = [r for r in cook_all if reverse_mode.unsafe_reason(user, r) is None]   # allergy/medical/diet: hard
    return {
        "session": {"id": session_id, "day": models.DAYS[session["day"]], "date": models.session_date(plan, session["day"]),
                    "meal": session["meal"], "status": session["status"], "pinned": bool(session.get("pinned"))},
        "limits": limits,
        "usual": usual[:SHORTLIST_PLACES], "usual_more": max(0, len(usual) - SHORTLIST_PLACES),
        "new": new, "has_favourites": bool(favs),
        "portions": n,
        "cook": [{"recipe_key": r["key"], "name": r["name"], "price": round(r["cost"] * n, 2), "kcal": r["kcal"],
                  "protein_g": r["protein_g"]} for r in cooks],
        "hidden": {"not_safe": len(menu) - len(safe_all), "cook_not_safe": len(cook_all) - len(cooks), "not_a_meal": len(safe_all) - len(safe),
                   "below_rating": len(safe) - len(rated_ok),
                   "not_again": len(rated_ok) - len(pool)},
        "current": {"item": current["item_name"], "kind": current["chosen_kind"], "cost": current["cost"]} if current else None,
        # one timing tip for the whole sheet instead of repeating it on every dish
        "timing_tip": (f"Prices include ordering a little early (arrive {shifts[0]['offpeak_hhmm']}) to skip the rush."
                       if shifts else None),
    }


# --------------------------------------------------------------------------- #
# One-tap actions (each re-balances the rest of the week)
# --------------------------------------------------------------------------- #
def _editable(session: dict, *, allow_past=False) -> None:
    if session["status"] in ("ordered", "confirmed"):
        raise ValueError("This meal is already ordered or confirmed")
    if not allow_past and scheduler.is_past(session, optimizer.now()):
        raise ValueError("This meal's time has passed — mark it as had or skipped instead")


def choose(session_id: int, body: dict) -> dict:
    session, plan, user = _session_bundle(session_id)
    _editable(session)
    if body.get("item_id") is not None:
        item = next((it for it in models.menu_for_user(user) if it["id"] == body["item_id"]), None)
        if not item:
            raise ValueError("That dish isn't available right now")
        if not optimizer.meal_suitable(item, session["meal"]):
            raise ValueError("That item is a treat, not a complete meal. Choose a meal instead.")
        reason = allergens.violates(user, item)
        if reason:
            raise ValueError(f"Not safe for you: {reason}. Your hard rules can't be overridden by a pick.")
        pin = {"kind": "delivery", "item_id": item["id"]}
    elif body.get("recipe_key"):
        r = reverse_mode.recipe(body["recipe_key"])
        if not r:
            raise ValueError("That recipe isn't available")
        reason = reverse_mode.unsafe_reason(user, r)
        if reason:
            raise ValueError(f"Not safe for you: {reason}. Your hard rules can't be overridden by a pick.")
        pin = {"kind": "cook", "recipe_key": r["key"]}
    elif body.get("action") == "skip":
        scheduler.set_status(session_id, "skipped", "You skipped this meal")
        optimizer.optimize(plan["id"])
        return service.plan_view(plan["id"])
    elif body.get("action") == "auto":
        pin = None
    else:
        raise ValueError("Choose a dish, a recipe, skip, or auto")
    with db.cursor() as cur:
        cur.execute("UPDATE sessions SET pinned=?, status='active', note='' WHERE id=?",
                    (db.jd(pin) if pin else None, session_id))
    optimizer.optimize(plan["id"])
    return service.plan_view(plan["id"])


def eat_now(plan_id: int, body: dict) -> dict:
    """Hungry now: today's `meal` goes into the plan even if the week skips it (a meal the
    rhythm leaves out, or one skipped earlier), so it can be ordered in two taps. The
    rest of the week re-balances around it."""
    plan = models.get_plan(plan_id)
    if not plan:
        raise KeyError("Plan not found")
    meal = (body or {}).get("meal")
    if meal not in models.MEALS:
        raise ValueError("Choose breakfast, lunch or dinner")
    at = optimizer.now()
    day = (at.date() - dt.date.fromisoformat(plan["week_start"])).days
    if not 0 <= day < 7:
        raise ValueError("This plan isn't for this week. Plan this week first")
    ts = scheduler.trigger_ts(plan["week_start"], day, meal)
    if dt.datetime.fromisoformat(ts) + scheduler.PAST_GRACE < at:
        raise ValueError(f"It's past {meal} time today. Pick your next meal instead")
    session = next((s for s in models.sessions_for_plan(plan_id) if s["day"] == day and s["meal"] == meal), None)
    with db.cursor() as cur:
        if session is None:
            cur.execute("INSERT INTO sessions(plan_id, day, meal, scheduled_ts, status) VALUES (?,?,?,?, 'active')",
                        (plan_id, day, meal, ts))
            sid = cur.lastrowid
        elif session["status"] in ("ordered", "confirmed"):
            raise ValueError(f"Today's {meal} is already ordered")
        else:
            sid = session["id"]
            cur.execute("UPDATE sessions SET status='active', note='' WHERE id=?", (sid,))
    optimizer.optimize(plan_id)
    decision = next((d for d in models.decisions_for_plan(plan_id) if d["session_id"] == sid), None)
    if not decision or decision["chosen_kind"] not in ("delivery", "cook"):
        # The week would still skip it (variety, budget): the person said they're hungry, so
        # keep the best safe dish that fits what's left, else the first safe recipe.
        o = options(sid)
        dishes = sorted([x for g in o["usual"] for x in g["dishes"]] + o["new"], key=lambda x: x["score"])
        dish = next((x for x in dishes if x["fits"]), None)
        pin = ({"kind": "delivery", "item_id": dish["item_id"]} if dish
               else {"kind": "cook", "recipe_key": o["cook"][0]["recipe_key"]} if o["cook"] else None)
        if pin:
            with db.cursor() as cur:
                cur.execute("UPDATE sessions SET pinned=? WHERE id=?", (db.jd(pin), sid))
            optimizer.optimize(plan_id)
    return {"session_id": sid, "plan": service.plan_view(plan_id)}


def _choice_of(session: dict, decision: dict | None) -> dict:
    if session["status"] in ("skipped", "snoozed", "cooked"):
        return {"status": session["status"]}
    if not decision or decision["chosen_kind"] == "skip":
        return {"status": "skipped"}
    if decision["chosen_kind"] == "delivery":
        return {"pin": {"kind": "delivery", "item_id": decision["item_id"]}}
    if decision["chosen_kind"] == "cook" and decision.get("recipe_key"):
        return {"pin": {"kind": "cook", "recipe_key": decision["recipe_key"]}}
    raise ValueError("Leftovers stay on the day you logged them")


def swap(plan_id: int, a: int, b: int) -> dict:
    """Drag one meal onto another: the two picks trade places, then the week re-balances."""
    if a == b:
        raise ValueError("Pick two different meals")
    sessions = {s["id"]: s for s in models.sessions_for_plan(plan_id)}
    if a not in sessions or b not in sessions:
        raise ValueError("Both meals must be in this plan")
    for sid in (a, b):
        _editable(sessions[sid])
    decisions = {d["session_id"]: d for d in models.decisions_for_plan(plan_id)}
    ca, cb = _choice_of(sessions[a], decisions.get(a)), _choice_of(sessions[b], decisions.get(b))
    label = lambda s: f"{models.DAYS[s['day']]} {s['meal']}"                    # noqa: E731
    with db.cursor() as cur:
        for sid, choice, origin in ((a, cb, sessions[b]), (b, ca, sessions[a])):
            if "pin" in choice:
                cur.execute("UPDATE sessions SET status='active', pinned=?, note='' WHERE id=?",
                            (db.jd(choice["pin"]), sid))
            else:
                cur.execute("UPDATE sessions SET status=?, pinned=NULL, note=? WHERE id=?",
                            (choice["status"], f"Moved from {label(origin)}", sid))
    optimizer.optimize(plan_id)
    return service.plan_view(plan_id)


def confirm(session_id: int) -> dict:
    """"I had it" — the meal happened (ordered via the Swiggy hand-off, or cooked).
    It becomes spent money and logged nutrition, and the rest of the week re-balances."""
    session, plan, user = _session_bundle(session_id)
    if session["status"] in ("ordered", "confirmed"):
        raise ValueError("Already recorded")
    decision = next((d for d in models.decisions_for_plan(plan["id"]) if d["session_id"] == session_id), None)
    if not decision or decision["chosen_kind"] not in ("delivery", "cook"):
        raise ValueError("Nothing planned for this meal — pick something first")
    with db.cursor() as cur:
        cur.execute("UPDATE sessions SET status='confirmed', pinned=NULL WHERE id=?", (session_id,))
        cur.execute("DELETE FROM intake_log WHERE note=?", (f"session:{session_id}",))
    if decision.get("nutrition"):
        intake.record(user["id"], decision["nutrition"], iso_date=models.session_date(plan, session["day"]),
                      meal=session["meal"], source="ordered" if decision["chosen_kind"] == "delivery" else "cooked",
                      plan_id=plan["id"], note=f"session:{session_id}")
    optimizer.optimize(plan["id"])
    service.record_receipts(plan["id"])
    return service.plan_view(plan["id"])


def rate(session_id: int, score, reasons: list | None = None) -> dict:
    session, plan, user = _session_bundle(session_id)
    decision = next((d for d in models.decisions_for_plan(plan["id"]) if d["session_id"] == session_id), None)
    if not decision or decision["chosen_kind"] not in ("delivery", "cook"):
        raise ValueError("Only planned dishes can be rated")
    if reasons is not None:
        if not isinstance(reasons, list):
            raise ValueError("Reasons must be a list")
        learning.record(user["id"], session_id, reasons, item_id=decision.get("item_id"),
                        restaurant_id=decision.get("restaurant_id"))
        if score is None:                  # a reason alone: "great" is a 👍, the others adjust only
            score = 1 if "great" in reasons else None
    if score is None and reasons:
        optimizer.optimize(plan["id"])
        return {"plan": service.plan_view(plan["id"]), "suggest_favourite": None}
    if isinstance(score, bool) or score not in (1, -1):
        raise ValueError("Rate with 1 (liked) or -1 (not again)")
    taste.rate(user["id"], session_id=session_id, score=score, item_id=decision.get("item_id"),
               restaurant_id=decision.get("restaurant_id"), recipe_key=decision.get("recipe_key"),
               iso_date=models.session_date(plan, session["day"]))
    if score < 0 and session["status"] == "active" and session.get("pinned"):
        with db.cursor() as cur:            # "not this" on your own pick releases it
            cur.execute("UPDATE sessions SET pinned=NULL WHERE id=?", (session_id,))
    optimizer.optimize(plan["id"])
    suggest = None
    rid = decision.get("restaurant_id")
    if score > 0 and rid and rid not in taste.favourites(user["id"]):
        suggest = {"restaurant_id": rid, "restaurant": decision.get("restaurant_name")}
    return {"plan": service.plan_view(plan["id"]), "suggest_favourite": suggest}


def saved(user_id: int) -> dict:
    """What the Saved tab lists besides places: the dishes this person rated Good and the
    meals they actually had or ordered, newest first. Only real records, never suggestions."""
    with db.cursor() as cur:
        likes = cur.execute("SELECT session_id, iso_date FROM ratings WHERE user_id=? AND score > 0 "
                            "AND session_id IS NOT NULL ORDER BY id DESC LIMIT 200", (user_id,)).fetchall()
        had = cur.execute(
            "SELECT s.id, s.day, s.meal, s.status, p.week_start FROM sessions s JOIN plans p ON p.id = s.plan_id "
            "WHERE p.user_id=? AND s.status IN ('confirmed', 'ordered') "
            "ORDER BY p.week_start DESC, s.day DESC, s.id DESC LIMIT 20", (user_id,)).fetchall()
        scores = {r["session_id"]: r["score"] for r in cur.execute(
            "SELECT session_id, score FROM ratings WHERE user_id=? AND session_id IS NOT NULL", (user_id,)).fetchall()}
        ids = sorted({r["session_id"] for r in likes} | {r["id"] for r in had})
        latest = {}
        for start in range(0, len(ids), 200):              # each meal's latest decision is what was eaten
            chunk = ids[start:start + 200]
            rows = cur.execute("SELECT session_id, chosen_kind, item_id, item_name, restaurant_id, restaurant_name, "
                               "recipe_key, cost FROM decisions WHERE session_id IN (" + ",".join("?" * len(chunk))
                               + ") ORDER BY id", tuple(chunk)).fetchall()
            for row in rows:
                latest[row["session_id"]] = dict(row)

    def dish(d: dict) -> dict:
        cook = d["chosen_kind"] == "cook"
        return {"name": d["item_name"], "kind": "cook" if cook else "delivery",
                "restaurant": None if cook else d["restaurant_name"], "restaurant_id": d["restaurant_id"],
                "item_id": d["item_id"], "recipe_key": d["recipe_key"], "price": round(float(d["cost"] or 0), 2)}

    liked, seen = [], set()
    for r in likes:
        d = latest.get(r["session_id"])
        if not d or d["chosen_kind"] not in ("delivery", "cook") or not d["item_name"]:
            continue
        key = (d["chosen_kind"], d["item_id"] or d["recipe_key"] or d["item_name"])
        if key in seen:
            continue
        seen.add(key)
        liked.append({**dish(d), "last": r["iso_date"]})
        if len(liked) == 30:
            break
    recent = []
    for s in had:
        d = latest.get(s["id"])
        if not d or not d["item_name"]:
            continue
        when = dt.date.fromisoformat(s["week_start"]) + dt.timedelta(days=s["day"])
        recent.append({**dish(d), "session_id": s["id"], "meal": s["meal"], "date": when.isoformat(),
                       "status": s["status"], "rated": scores.get(s["id"])})
    return {"liked": liked, "recent": recent}


def more_like(session_id: int) -> dict:
    """"More like this" on a planned dish: remember it (the last few), re-plan the open
    meals with the similarity nudge, and offer the closest safe dishes to pick now."""
    session, plan, user = _session_bundle(session_id)
    decision = next((d for d in models.decisions_for_plan(plan["id"]) if d["session_id"] == session_id), None)
    if not decision or decision["chosen_kind"] != "delivery":
        raise ValueError("“More like this” works on a restaurant dish")
    if epicure.get() is None:
        raise ValueError("Dish similarity isn't available right now")
    dish = decision["item_name"]
    if flavour.dish_vector(dish) is None:
        raise ValueError(f"Ziggy can't tell yet what {dish} is made of, so it can't find similar dishes")
    prefs = dict(user["prefs"])
    entries = [m for m in prefs.get("more_like", []) if m.get("name") != dish]
    entries.append({"name": dish, "item_id": decision.get("item_id"), "iso_date": clock.today().isoformat()})
    prefs["more_like"] = entries[-flavour.MORE_LIKE_MAX:]
    with db.cursor() as cur:
        cur.execute("UPDATE users SET prefs=? WHERE id=?", (db.jd(prefs), user["id"]))
    optimizer.optimize(plan["id"])
    user = models.get_user(user["id"])
    menu = [it for it in allergens.safe_items(user, models.menu_for_user(user))
            if optimizer.meal_suitable(it, session["meal"]) and it["restaurant_rating"] >= float(user["rating_floor"])]
    similar = [{"item_id": it["id"], "name": it["name"], "restaurant": it["restaurant_name"], "price": it["price"],
                "similarity": it["similarity"]}
               for it in flavour.similar_items(dish, menu, k=3, exclude_ids={decision.get("item_id")})]
    return {"plan": service.plan_view(plan["id"]), "recorded": dish, "similar": similar}


def forget_more_like(user_id: int, name: str) -> dict:
    user = models.get_user(user_id)
    if not user:
        raise KeyError("Profile not found")
    prefs = dict(user["prefs"])
    before = prefs.get("more_like", [])
    prefs["more_like"] = [m for m in before if m.get("name") != name]
    if len(prefs["more_like"]) == len(before):
        raise ValueError("That dish isn't on your “more like this” list")
    with db.cursor() as cur:
        cur.execute("UPDATE users SET prefs=? WHERE id=?", (db.jd(prefs), user_id))
    view = service.current_plan(user_id)
    optimizer.optimize(view["plan"]["id"])
    return service.plan_view(view["plan"]["id"])


def upcoming_calendar(user_id: int, days: int = 60) -> list[dict]:
    """Holidays and festivals ahead (plus the fasts this user keeps), for planning ahead."""
    user = models.get_user(user_id)
    today = optimizer.now().date()
    end = today + dt.timedelta(days=max(1, min(days, 180)))
    with db.cursor() as cur:
        rows = [db.row_to_dict(r) for r in cur.execute(
            "SELECT * FROM festivals WHERE iso_date BETWEEN ? AND ? ORDER BY iso_date",
            (today.isoformat(), end.isoformat()))]
    out, seen = [], set()
    for r in rows:
        if not festivals.applies_to(r, user):
            continue
        key = (r["name"], r["effect"])
        if key in seen and r["effect"] == "fast":        # multi-day fasts: list the first day once
            continue
        seen.add(key)
        out.append({"date": r["iso_date"], "name": r["name"], "effect": r["effect"], "note": r["note"],
                    "approx": bool(r["approx"]), "when": _when(r["iso_date"], today)})
    return out


# --------------------------------------------------------------------------- #
# What's next, and what's coming
# --------------------------------------------------------------------------- #
def _slot_time(date_iso: str, meal: str) -> dt.datetime:
    h, m = divmod(models.MEAL_WINDOWS[meal][1], 60)
    return dt.datetime.combine(dt.date.fromisoformat(date_iso), dt.time(h, m))


def _when(date_iso: str, today: dt.date) -> str:
    d = dt.date.fromisoformat(date_iso)
    if d == today:
        return "Today"
    if d == today + dt.timedelta(days=1):
        return "Tomorrow"
    return d.strftime("%a %d %b")


def next_up(view: dict, at: dt.datetime) -> dict | None:
    """The next meal that still needs something from the user (or is coming up)."""
    for i, day in enumerate(view["grid"]):
        for meal in models.MEALS:
            cell = day["meals"].get(meal)
            if not cell or cell.get("status") in ("past",) or "date" not in day:
                continue
            if _slot_time(day["date"], meal) + scheduler.PAST_GRACE < at:
                continue
            done = cell.get("status") in ("confirmed", "ordered")
            if done and cell.get("rating_given") is not None:
                continue                                 # had it and rated it: move on
            if cell["kind"] in ("delivery", "cook") or done:
                return {"day_index": i, "day": day["day"], "date": day["date"], "meal": meal,
                        "when": _when(day["date"], at.date()), "cell": cell}
    return None


def heads_up(view: dict, user: dict, plan: dict, decisions: list[dict], at: dt.datetime) -> list[dict]:
    """A few proactive notes for the days ahead — not a notification firehose."""
    out = []
    today = at.date()
    grid, ctx = view["grid"], view["week_context"]
    upcoming = [i for i in range(7) if "date" in grid[i] and dt.date.fromisoformat(grid[i]["date"]) >= today]

    unconfirmed = [d for d in decisions if d.get("past") and d["chosen_kind"] in ("delivery", "cook")]
    if unconfirmed:
        out.append({"level": "warn", "icon": "✓", "kind": "reconcile",
                    "title": f"{len(unconfirmed)} past meal{'s' if len(unconfirmed) != 1 else ''} not confirmed",
                    "body": "Tap “I had it” or “Skipped” so your budget and nutrition stay accurate. "
                            "We don't assume what you ate."})

    skipped_budget = [d for d in decisions if d["chosen_kind"] == "skip" and d["session_status"] == "active"
                      and not d.get("past") and any("budget" in r.lower() for r in d.get("reasons", []))]
    rec = view.get("recommendation") or {}
    if skipped_budget:
        more = ""
        if rec.get("feasible") and rec["usual"]["total"] > view["budget"]["budget"]:
            more = f" Your usual week costs about ₹{rec['usual']['total']:.0f}."
        out.append({"level": "warn", "icon": "₹", "kind": "budget",
                    "title": f"{len(skipped_budget)} meal{'s' if len(skipped_budget) != 1 else ''} didn't fit the budget",
                    "body": "Raise the weekly budget, cook one of them, or switch to Tight week." + more})

    b = view["budget"]
    if not skipped_budget and b["budget"] and b["remaining"] < 0.08 * b["budget"] and b["remaining"] >= 0:
        out.append({"level": "info", "icon": "₹", "kind": "budget",
                    "title": f"₹{b['remaining']:.0f} left this week",
                    "body": "The plan uses almost all of this week's budget, so any price rise will need a swap."})

    for i in upcoming:
        c = ctx[i]
        label = _when(c["date"], today)
        if c["weather"] in ("rain", "storm"):
            saving = sum((m.get("time_shift") or {}).get("saving", 0) for m in grid[i]["meals"].values())
            orders = [m for m in grid[i]["meals"].values() if m["kind"] == "delivery"]
            if orders:
                body = ("Delivery gets pricier and slower in rain. "
                        + (f"We moved orders earlier to save ₹{saving:.0f}; " if saving else "")
                        + "order-by times include extra time.")
                out.append({"level": "info", "icon": "🌧", "kind": "weather", "day": i,
                            "title": f"{label}: rain{' likely' if c['weather'] == 'rain' else ' and storms'}"
                                     + (f" ({c['rain_prob']:.0f}%)" if c.get("rain_prob") else ""),
                            "body": body})
        elif c["weather"] == "hot" and c["temp_c"] >= 36:
            out.append({"level": "info", "icon": "☀", "kind": "weather", "day": i,
                        "title": f"{label}: {c['temp_c']:.0f}°C",
                        "body": "Lighter meals are favoured that day. Stay hydrated."})
        if c.get("festival") and c["festival_effect"] in ("holiday", "feast"):
            out.append({"level": "info", "icon": "🎉" if c["festival_effect"] == "feast" else "📅",
                        "kind": "holiday", "day": i, "title": f"{label}: {c['festival']}"
                        + (" (date may shift a day)" if c.get("festival_approx") else ""),
                        "body": (c.get("festival_note") or "").capitalize() + "."})
        if c.get("your_fast"):
            out.append({"level": "info", "icon": "🌙", "kind": "fast", "day": i,
                        "title": f"{label}: {c['your_fast']}",
                        "body": "You told us you keep this fast, so only dinner is planned."})

    goal = (user.get("prefs") or {}).get("goal", "none")
    n = view["nutrition"]
    if goal != "none" and n["daily_avg"].get("protein_g", 0) < 0.8 * n["daily_target"]["protein_g"]:
        out.append({"level": "info", "icon": "💪", "kind": "nutrition",
                    "title": f"Protein runs low: about {n['daily_avg']['protein_g']:.0f} g a day of {n['daily_target']['protein_g']:.0f} g",
                    "body": "Planned meals cover part of the day. Pick a protein-forward dish, or log home meals to count them."})
    if view.get("surge_saved", 0) >= 20:
        out.append({"level": "good", "icon": "⏱", "kind": "saving",
                    "title": f"Ordering a little earlier saves ₹{view['surge_saved']:.0f} this week",
                    "body": "Each meal shows an order-by time that avoids the rush."})
    order = {"warn": 0, "info": 1, "good": 2}
    out.sort(key=lambda h: (order[h["level"]], h.get("day", -1)))
    return out[:6]