"""Fix round 4: budget model v2, the weekly envelope (smartplate/domain/envelope.py).

The weekly number is an envelope, not a ration; a day's share is a guide, never a wall. The
person's own picks and pins are never blocked or dropped: Ziggy shows what they cost and
rebalances the days ahead. Today and Week read one function (the old flat weekly ÷ 7 on Today
against the Week's rolling share was a bug). Frozen clock: Mon 2 Nov 2026 08:00 IST.
"""
import datetime as dt

import pytest
from hypothesis import given
from hypothesis import strategies as st

from smartplate.app import create_app
from smartplate.domain import envelope

WEEK = [(dt.date(2026, 11, 2) + dt.timedelta(days=i)).isoformat() for i in range(7)]


def _days(states=None, committed=None, spent=None, sets=None, weight=3):
    states, committed, spent, sets = states or {}, committed or {}, spent or {}, sets or {}
    return [{"date": d, "state": states.get(i, "plan"), "spent": spent.get(i, 0.0), "committed": committed.get(i, 0.0),
             "set": sets.get(i), "mode": "soft", "weight": weight, "guide_weight": weight, "off_meals": 0}
            for i, d in enumerate(WEEK)]


def _chips(a):
    return [d["target"] + d["reserve"] for d in a["days"]]


# --------------------------------------------------------------------------- pure: golden walkthroughs
def test_golden_biryani_walkthrough():
    """₹2,000 a week; biryani pinned on Monday at ₹350 incl. fees: no block, the rest of the
    week shares ₹1,650 (₹275 a day) and the card offers to take it from the week (default)
    or make just this week bigger."""
    before = envelope.allocate(2000, _days())
    assert [d["guide"] for d in before["days"]] == [286, 286, 286, 286, 286, 285, 285]   # ₹2,000 ÷ 7, whole rupees
    after = envelope.allocate(2000, _days(committed={0: 350}))
    assert after["days"][0]["target"] == 350 and not after["is_over"]
    assert [d["target"] for d in after["days"][1:]] == [275] * 6
    assert sum(_chips(after)) == 2000
    imp = envelope.impact(before, after, WEEK[0], today=WEEK[0])
    # the design note rounds the share to ₹285 ("₹65 above"); in paise the share is ₹285.71
    assert imp["text"] == "₹64 above Monday's share. Rest of week ₹1,650 over 6 days ≈ ₹275/day."
    assert [o["act"] for o in imp["options"]] == ["take_from_week", "raise_week"] and imp["default"] == "take_from_week"
    assert imp["options"][1]["amount"] == 2065 and imp["options"][1]["label"] == "Make this week ₹2,065"


def test_golden_all_off_weekend_scope_all_and_planned():
    a = envelope.allocate(2000, _days(states={5: "off", 6: "off"}))
    sat, sun = a["days"][5], a["days"][6]
    assert sat["state"] == sun["state"] == "off" and sat["target"] == sun["target"] == 0
    assert a["reserve"] == 571.43 and sat["reserve"] + sun["reserve"] in (570, 571)   # their share kept aside
    assert [d["target"] for d in a["days"][:5]] == [286] * 5 and sat["reserve"] + sun["reserve"] == 570
    assert sum(_chips(a)) == 2000
    b = envelope.allocate(2000, _days(states={5: "off", 6: "off"}), scope="planned")
    assert [d["target"] for d in b["days"][:5]] == [400] * 5 and b["reserve"] == 0
    assert b["days"][5]["guide"] == 0 and sum(_chips(b)) == 2000


def test_golden_surge_is_absorbed_by_the_days_ahead():
    """Monday's real bill came to ₹400 against a ₹286 share (rain surge): the rest share the rest."""
    a = envelope.allocate(2000, _days(spent={0: 400}))
    assert a["days"][0]["target"] == 400
    assert sum(d["target"] for d in a["days"][1:]) == 1600 and {d["target"] for d in a["days"][1:]} <= {266, 267}
    assert sum(_chips(a)) == 2000


def test_golden_refund_returns_money_to_the_pool():
    with_bill = envelope.allocate(2000, _days(spent={1: 500}))
    refunded = envelope.allocate(2000, _days())
    assert with_bill["days"][2]["target"] == 250 and refunded["days"][2]["target"] == 286


def test_pin_over_the_whole_envelope_is_kept_and_offers_three_ways():
    before = envelope.allocate(2000, _days())
    after = envelope.allocate(2000, _days(committed={0: 2500}))
    assert after["is_over"] and after["over"] == 500 and after["days"][0]["target"] == 2500
    assert all(d["target"] == 0 for d in after["days"][1:])
    imp = envelope.impact(before, after, WEEK[0], today=WEEK[0])
    assert [o["act"] for o in imp["options"]] == ["raise_week", "suggest", "keep_over"]
    assert imp["options"][0]["amount"] == 2500 and "Your pick stays" in imp["text"]


def test_several_splurges_over_the_envelope_are_all_kept():
    a = envelope.allocate(2000, _days(committed={0: 800, 1: 900, 2: 600}))
    assert [d["target"] for d in a["days"][:3]] == [800, 900, 600] and a["over"] == 300
    assert sum(_chips(a)) == 2300


def test_a_set_day_reserves_its_amount_and_the_rest_rebalances():
    a = envelope.allocate(2000, _days(sets={2: 600}))
    assert a["days"][2]["target"] == 600 and a["days"][2]["set"] == 600
    assert sum(d["target"] for i, d in enumerate(a["days"]) if i != 2) == 1400


def test_past_days_keep_what_was_spent_and_mid_week_money_rolls_forward():
    a = envelope.allocate(2000, _days(states={0: "past", 1: "past"}, spent={0: 300}, committed={1: 200}))
    assert a["days"][0]["target"] == 300 and a["days"][1]["target"] == 0     # an unconfirmed past pick is unknown
    assert [d["target"] for d in a["days"][2:]] == [340] * 5


def test_largest_remainder_rounding():
    assert envelope.largest_remainder([128.57 / 3] * 3, 129) == [43, 43, 43]
    assert envelope.largest_remainder([42.857] * 7, 300) == [43, 43, 43, 43, 43, 43, 42]
    assert sum(envelope.largest_remainder([0.4, 0.4, 0.2], 1)) == 1


def test_meal_day_after_midnight_is_the_day_before():
    assert envelope.meal_day(dt.datetime(2026, 11, 3, 0, 40)) == dt.date(2026, 11, 2)
    assert envelope.meal_day(dt.datetime(2026, 11, 3, 7, 0)) == dt.date(2026, 11, 3)


# --------------------------------------------------------------------------- pure: property
DAY = st.fixed_dictionaries({
    "state": st.sampled_from(["plan", "plan", "plan", "off", "past", "before"]),
    "spent": st.sampled_from([0.0, 0.0, 120.5, 380.0]),
    "committed": st.sampled_from([0.0, 0.0, 95.25, 350.0, 1200.0]),
    "set": st.sampled_from([None, None, None, 0, 450, 900]),
    "weight": st.integers(0, 3),
})


@given(env=st.integers(0, 9000), raw=st.lists(DAY, min_size=7, max_size=7), scope=st.sampled_from(["all", "planned"]))
def test_property_targets_add_up_to_the_envelope_and_pins_are_never_dropped(env, raw, scope):
    days = []
    seen_cov = False
    for i, r in enumerate(raw):
        state = r["state"]
        if state == "before" and seen_cov:
            state = "plan"
        seen_cov = seen_cov or state != "before"
        days.append({"date": WEEK[i], "state": state, "spent": r["spent"], "committed": r["committed"],
                     "set": r["set"], "mode": "soft", "weight": r["weight"], "guide_weight": max(1, r["weight"]),
                     "off_meals": 0})
    a = envelope.allocate(env, days, scope=scope)
    chips = sum(d["target"] + d["reserve"] for d in a["days"]) + a["unallocated"]
    if a["is_over"]:
        assert chips == round(env + a["over"])
    else:
        assert chips == round(env)
        exact = sum(d["target_exact"] for d in a["days"]) + a["reserve"]
        assert exact + 0.05 >= 0 and exact <= env + 0.05
    for d_in, d in zip(days, a["days"]):
        if d_in["state"] in ("plan", "off"):            # a pick or pin is never squeezed out
            assert d["target_exact"] + 0.01 >= d_in["spent"] + d_in["committed"]
        if d_in["state"] == "plan" and d_in["set"] is not None:
            assert d["target_exact"] + 0.01 >= d_in["set"]


# --------------------------------------------------------------------------- the app: one figure, Today and Week
def _profile(c, weekly=2000, **extra):
    body = {"name": "Vijay", "diet": "nonveg", "weekly_budget": weekly, "cook": "never",
            "rhythm": {"breakfast": "order", "lunch": "order", "dinner": "order"}, "allergens": [], "medical": [],
            "consent": {"account": True, "health": True, "learning": True}}
    body.update(extra)
    r = c.post("/api/profiles", json=body)
    assert r.status_code == 201, r.get_json()
    c.environ_base["HTTP_X_SMARTPLATE_KEY"] = r.get_json()["access_key"]
    return r.get_json()["user"]["id"]


@pytest.fixture
def app(gt):
    c = create_app().test_client()
    uid = _profile(c)
    plan = c.get(f"/api/user/{uid}/plan").get_json()
    return {"c": c, "uid": uid, "plan": plan, "clock": gt}


def _env(c, uid, week=None):
    r = c.get(f"/api/user/{uid}/envelope" + (f"?week={week}" if week else ""))
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def test_today_and_week_show_the_same_figure(app):
    c, uid = app["c"], app["uid"]
    today = c.get(f"/api/user/{uid}/today").get_json()["budget"]
    week = c.get(f"/api/user/{uid}/plan").get_json()["budget"]["envelope"]
    env = _env(c, uid)
    mon = next(d for d in env["days"] if d["is_today"])
    assert today["amount"] == mon["target"] == next(d for d in week["days"] if d["date"] == WEEK[0])["target"]
    assert env["envelope"] == 2000 and sum(d["target"] + d["reserve"] for d in env["days"]) + env["unallocated"] in (
        2000, round(2000 + env["over"]))
    # setting Wednesday's amount moves today's figure on both screens alike
    r = c.put(f"/api/user/{uid}/day-budget", json={"date": WEEK[2], "amount": 900, "mode": "soft"})
    assert r.status_code == 200 and r.get_json()["applied"]
    today2 = c.get(f"/api/user/{uid}/today").get_json()["budget"]
    week2 = c.get(f"/api/user/{uid}/plan").get_json()["budget"]["envelope"]
    wed = next(d for d in week2["days"] if d["date"] == WEEK[2])
    assert wed["target"] == 900 and wed["set"] == 900
    assert today2["amount"] == next(d for d in week2["days"] if d["date"] == WEEK[0])["target"] < today["amount"]


def test_today_card_budget_is_a_soft_day_amount(app):
    c, uid = app["c"], app["uid"]
    t = c.post(f"/api/user/{uid}/today/budget", json={"amount": 450}).get_json()
    assert t["budget"]["amount"] == 450 and t["budget"]["source"] == "today"
    d = _env(c, uid)["days"][0]
    assert d["set"] == 450 and d["mode"] == "soft"


def test_day_budget_rules_forward_only_and_last_write_wins(app):
    c, uid = app["c"], app["uid"]
    r = c.put(f"/api/user/{uid}/day-budget", json={"date": "2026-11-01", "amount": 300})
    assert r.status_code == 400                                       # a past day: never retroactive
    r = c.put(f"/api/user/{uid}/day-budget", json={"date": WEEK[3], "amount": 500,
                                                   "set_at": "2026-11-02T08:05:00"})
    assert r.get_json()["applied"]
    stale = c.put(f"/api/user/{uid}/day-budget", json={"date": WEEK[3], "amount": 100,
                                                       "set_at": "2026-11-02T08:01:00"}).get_json()
    assert stale["applied"] is False and stale["stale"] is True
    assert next(d for d in stale["envelope"]["days"] if d["date"] == WEEK[3])["target"] == 500
    r = c.put(f"/api/user/{uid}/day-budget", json={"date": WEEK[3], "amount": None,
                                                   "set_at": "2026-11-02T08:06:00"}).get_json()
    assert next(d for d in r["envelope"]["days"] if d["date"] == WEEK[3])["set"] is None
    assert c.put(f"/api/user/{uid}/day-budget", json={"date": WEEK[3], "amount": 100, "mode": "never"}).status_code == 400


def test_week_override_is_for_that_week_only(app):
    c, uid = app["c"], app["uid"]
    r = c.put(f"/api/user/{uid}/week-override", json={"week_start": WEEK[0], "amount": 2065}).get_json()
    assert r["envelope"]["envelope"] == 2065 and r["envelope"]["override"] == 2065
    assert c.get(f"/api/user/{uid}/plan").get_json()["budget"]["budget"] == 2065
    assert _env(c, uid, "2026-11-09")["envelope"] == 2000                 # next week: the usual budget
    assert c.put(f"/api/user/{uid}/week-override", json={"week_start": WEEK[1], "amount": 2065}).status_code == 400


def test_off_days_plan_nothing_and_keep_their_share(app):
    c, uid = app["c"], app["uid"]
    for d in (WEEK[5], WEEK[6]):
        assert c.put(f"/api/user/{uid}/day-plan", json={"date": d, "state": "off"}).status_code == 200
    plan = c.get(f"/api/user/{uid}/plan").get_json()
    weekend = [d for d in plan["decisions"] if d["day"] in (5, 6)] if "decisions" in plan else []
    env = _env(c, uid)
    assert [d["state"] for d in env["days"][5:]] == ["off", "off"]
    assert env["days"][5]["reserve"] > 0 and env["days"][5]["target"] == 0
    from smartplate.domain import models
    sessions = [s for s in models.sessions_for_plan(plan["plan"]["id"]) if s["day"] in (5, 6)]
    assert sessions and all(s["status"] == "skipped" and s["note"] == envelope.OFF_NOTE for s in sessions)
    assert all(d.get("chosen_kind") != "delivery" for d in weekend)
    c.put(f"/api/user/{uid}/day-plan", json={"date": WEEK[5], "state": "plan"})
    sessions = [s for s in models.sessions_for_plan(plan["plan"]["id"]) if s["day"] == 5]
    assert all(s["status"] == "active" for s in sessions)


def test_one_meal_off_and_guests_flag(app):
    c, uid = app["c"], app["uid"]
    c.put(f"/api/user/{uid}/day-plan", json={"date": WEEK[1], "meal": "breakfast", "state": "off"})
    c.put(f"/api/user/{uid}/day-plan", json={"date": WEEK[4], "state": "plan", "eaters": 3})
    env = _env(c, uid)
    assert env["days"][1]["off_meals"] == ["breakfast"] and env["days"][1]["reserve"] > 0
    fri = env["days"][4]
    assert fri["eaters"] == 3 and any(f["kind"] == "eaters" for f in fri["flags"])
    assert fri["guide"] > env["days"][3]["guide"] * 2


def test_scope_setting_changes_how_off_days_count(app):
    c, uid = app["c"], app["uid"]
    c.put(f"/api/user/{uid}/day-plan", json={"date": WEEK[6], "state": "off"})
    a = _env(c, uid)
    r = c.put(f"/api/user/{uid}/budget-settings", json={"scope": "planned"}).get_json()
    assert r["settings"] == {"scope": "planned", "carry": False, "asked": True}
    assert r["envelope"]["days"][6]["reserve"] == 0 and a["days"][6]["reserve"] > 0
    assert c.put(f"/api/user/{uid}/budget-settings", json={"scope": "mine"}).status_code == 400


def test_pinning_a_dish_returns_its_impact_and_the_pin_is_never_dropped(gt):
    c = create_app().test_client()
    uid = _profile(c, weekly=700)                                   # ₹100 a day
    plan = c.get(f"/api/user/{uid}/plan").get_json()
    from smartplate.domain import models
    user = models.get_user(uid)
    sid = next(s["id"] for s in models.sessions_for_plan(plan["plan"]["id"]) if s["day"] == 1 and s["meal"] == "dinner")
    from smartplate.kernel import optimizer
    dear = next(it for it in sorted(models.menu_for_user(user), key=lambda it: -it["price"])
                if optimizer.meal_suitable(it, "dinner", chosen=True))
    r = c.post(f"/api/session/{sid}/choose", json={"item_id": dear["id"]})
    assert r.status_code == 200, r.get_json()
    v = r.get_json()
    imp = v["impact"]
    assert imp and imp["date"] == WEEK[1] and imp["over_share"] > 0 and imp["options"]
    assert imp["text"]
    # re-plans (a budget change, another day's amount) never drop the pin
    c.put(f"/api/user/{uid}/day-budget", json={"date": WEEK[3], "amount": 400})
    c.patch(f"/api/user/{uid}/setup", json={"weekly_budget": 500})
    s = next(s for s in models.sessions_for_plan(plan["plan"]["id"]) if s["id"] == sid)
    assert s["pinned"] and '"item_id"' in s["pinned"]
    d = next(d for d in models.decisions_for_plan(plan["plan"]["id"]) if d["session_id"] == sid)
    assert d["chosen_kind"] == "delivery" and d["item_id"] == dear["id"]


def test_outside_bill_after_midnight_counts_for_the_day_before_and_marks_the_meal(app):
    c, uid, clock = app["c"], app["uid"], app["clock"]
    clock.set(dt.datetime(2026, 11, 3, 0, 40))
    r = c.post(f"/api/user/{uid}/outside-spend", json={"amount": 420, "app": "zomato", "meal": "dinner",
                                                       "at": "2026-11-03T00:30:00"})
    assert r.status_code == 200, r.get_json()
    out = r.get_json()
    assert out["date"] == WEEK[0] and out["marked_session"]
    mon = out["envelope"]["days"][0]
    assert mon["spent"] == 420
    from smartplate.domain import models
    s = next(s for s in models.sessions_for_plan(app["plan"]["plan"]["id"]) if s["id"] == out["marked_session"])
    assert s["status"] == "skipped" and "Zomato" in s["note"]
    # refund / entered by mistake: the money goes back to the pool
    r = c.post(f"/api/user/{uid}/outside-spend/remove", json={"ref": out["ref"]}).get_json()
    assert r["envelope"]["days"][0]["spent"] == 0
    # Swiggy's own spend line never counts another app's bill
    from smartplate.domain import spend
    c.post(f"/api/user/{uid}/outside-spend", json={"amount": 300, "app": "toing", "date": WEEK[0]})
    assert spend.actual(uid, WEEK[0], WEEK[6])["amount"] == 0


def test_hard_daily_limit_vs_soft_guide(app):
    c, uid = app["c"], app["uid"]
    from smartplate.domain import models, profile
    c.patch(f"/api/user/{uid}/setup", json={"daily_cap": 300})
    assert profile.daily_cap_mode(models.get_user(uid)) == "hard"
    from smartplate.kernel import decide
    assert decide.rules_for(models.get_user(uid))["daily_cap"] == 300
    c.patch(f"/api/user/{uid}/setup", json={"daily_cap_mode": "soft"})
    assert decide.rules_for(models.get_user(uid))["daily_cap"] is None
    assert c.patch(f"/api/user/{uid}/setup", json={"daily_cap_mode": "maybe"}).status_code == 400


def test_today_pick_returns_impact(app):
    c, uid = app["c"], app["uid"]
    t = c.post(f"/api/user/{uid}/today/pool", json={"source": "typed", "meal": "lunch", "name": "Mutton Biryani",
                                                     "restaurant_name": "SS Hyderabad", "price": 650, "veg": False})
    ref = t.get_json()["pools"]["lunch"][0]["ref"]
    r = c.post(f"/api/user/{uid}/today/pick", json={"meal": "lunch", "ref": ref}).get_json()
    assert r["budget"]["amount"] >= 650                               # the pick is the day's money, not a wall
    assert r["impact"]["over_share"] > 0 and r["impact"]["default"] == "take_from_week"
    env = _env(c, uid)
    rest = [d["target"] for d in env["days"][1:]]
    assert max(rest) - min(rest) <= 1 and max(rest) < 286              # the rest of the week gave way


def test_decide_week_reads_the_envelope_and_never_touches_pins(app):
    c, uid = app["c"], app["uid"]
    pid = app["plan"]["plan"]["id"]
    from smartplate.domain import models
    sid = next(s["id"] for s in models.sessions_for_plan(pid) if s["day"] == 2 and s["meal"] == "lunch")
    d = next(d for d in models.decisions_for_plan(pid) if d["session_id"] == sid)
    if d["chosen_kind"] == "delivery":
        c.post(f"/api/session/{sid}/choose", json={"item_id": d["item_id"]})
    c.put(f"/api/user/{uid}/day-plan", json={"date": WEEK[6], "state": "off"})
    out = c.post(f"/api/plan/{pid}/decide", json={"mode": "favourites_only"})
    assert out.status_code == 200, out.get_json()
    body = out.get_json()
    assert sid not in {p["session_id"] for p in body["picks"]}          # a pin is not Ziggy's to change
    assert all(p["day"] != 6 for p in body["picks"])                    # an Off day is not planned
    env = _env(c, uid)
    assert body["money"] <= env["envelope"] - env["reserve"] - env["spent"] - env["committed"] + 0.01


# --------------------------------------------------------------------------- the solver: soft day targets
def _cand(name, cost, score=0.8):
    return {"kind": "delivery", "name": name, "cost": cost, "score": score, "item_ids": (name,)}


@pytest.mark.parametrize("method", ["exact", "greedy", "milp"])
def test_solver_stages_meals_then_score_then_least_over_then_cheapest(method):
    from smartplate.kernel import decide
    slots = [{"day": 0, "meal": "dinner"}, {"day": 1, "meal": "dinner"}]
    cands = [[_cand("P", 150), _cand("Q", 160)], [_cand("P", 150), _cand("Q", 160)]]
    rules = {"max_repeat": 1, "day_targets": {0: 100, 1: 1000}}
    chosen, _ = decide.solve(slots, cands, 1000, rules, method=method)
    assert [c["name"] for c in chosen] == ["P", "Q"]          # the cheaper dish on the tight day
    # a day's share is a guide: a meal is still filled though it takes the day over its target
    chosen, _ = decide.solve(slots, [[_cand("P", 400)], [_cand("Q", 160)]], 1000, rules, method=method)
    assert [c["name"] for c in chosen] == ["P", "Q"]


@pytest.mark.parametrize("method", ["exact", "greedy", "milp"])
def test_solver_hard_day_amount_is_a_wall(method):
    from smartplate.kernel import decide
    slots = [{"day": 0, "meal": "dinner"}, {"day": 1, "meal": "dinner"}]
    rules = {"max_repeat": 2, "day_caps": {0: 300}}
    chosen, _ = decide.solve(slots, [[_cand("P", 400)], [_cand("Q", 160)]], 1000, rules, method=method)
    assert [c["kind"] for c in chosen] == ["skip", "delivery"]


# --------------------------------------------------------------------------- more edge cases
def test_weekly_budget_changed_mid_week_applies_to_the_rest_and_spent_stays(app):
    c, uid, clock = app["c"], app["uid"], app["clock"]
    c.post(f"/api/user/{uid}/outside-spend", json={"amount": 500, "app": "zomato", "date": WEEK[0]})
    clock.set(dt.datetime(2026, 11, 4, 9, 0))                       # Wednesday
    c.put(f"/api/user/{uid}/week-override", json={"week_start": WEEK[0], "amount": 2500})
    c.patch(f"/api/user/{uid}/setup", json={"weekly_budget": 2800})
    env = _env(c, uid)
    assert env["override"] is None and env["envelope"] == 2800        # the new number; the one-week override gave way
    assert env["days"][0]["spent"] == 500 and env["days"][0]["target"] == 500   # spent stays spent
    ahead = [d for d in env["days"] if d["date"] >= WEEK[2]]
    assert sum(d["target"] for d in ahead) == 2800 - 500 - env["days"][1]["target"]


def test_fast_day_gets_a_lighter_target(app, monkeypatch):
    c, uid = app["c"], app["uid"]
    monkeypatch.setattr(envelope, "_fasts", lambda user, ws: {WEEK[3]: {"name": "Ekadashi"}})
    env = _env(c, uid)
    thu, fri = env["days"][3], env["days"][4]
    assert thu["target"] < fri["target"] and thu["guide"] < fri["guide"]
    assert any(f["kind"] == "fast" for f in thu["flags"])


def test_kept_at_week_end_and_carry_over_is_off_by_default(app):
    c, uid, clock = app["c"], app["uid"], app["clock"]
    c.post(f"/api/user/{uid}/outside-spend", json={"amount": 1500, "app": "other", "date": WEEK[0]})
    clock.set(dt.datetime(2026, 11, 8, 21, 0))                      # Sunday night
    env = _env(c, uid)
    assert env["kept"]["amount"] == 500 and env["kept"]["text"] == "Kept ₹500"
    clock.set(dt.datetime(2026, 11, 9, 9, 0))
    assert _env(c, uid)["envelope"] == 2000                          # no automatic carry-over
    c.put(f"/api/user/{uid}/budget-settings", json={"carry": True})
    # carry counts real Swiggy bills only; last week had none, so the whole budget is "kept"
    assert _env(c, uid)["carry_in"] == 2000 and _env(c, uid)["envelope"] == 4000


def test_snack_pick_counts_in_todays_money_without_a_slot(app):
    c, uid = app["c"], app["uid"]
    t = c.post(f"/api/user/{uid}/today/pool", json={"source": "typed", "meal": "snack", "name": "Samosa",
                                                     "restaurant_name": "Corner", "price": 40, "veg": True}).get_json()
    ref = t["pools"]["snack"][0]["ref"]
    c.post(f"/api/user/{uid}/today/pick", json={"meal": "snack", "ref": ref})
    mon = _env(c, uid)["days"][0]
    assert mon["committed"] == 40
    from smartplate.domain import models
    assert all(s["meal"] != "snack" for s in models.sessions_for_plan(app["plan"]["plan"]["id"]))


def test_low_money_says_so_and_never_invents_cheap_food():
    days = [{"date": WEEK[i], "state": "plan", "spent": 0, "committed": 0, "set": None, "weight": 3}
            for i in range(7)]
    a = envelope.allocate(700, [dict(d, guide_weight=3, off_meals=0, mode=None) for d in days])
    low = envelope._low(a, {"amount": 120.0}, days)
    assert low and low["fits"] == 5 and "Turn a day Off, cook a few, or raise this week" in low["text"]
    assert envelope._low(envelope.allocate(9000, [dict(d, guide_weight=3, off_meals=0, mode=None) for d in days]),
                         {"amount": 120.0}, days) is None
