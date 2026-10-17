"""A simulated week for each persona (critiques #31, #71: "simulate scenarios", "switch
5 plans to try out"). Each test opens the app, sets a budget, taps favourites, plans,
swaps, tries a cart, confirms a meal and reads the Sunday recap, then checks the outcome
invariants in harness.py. Known bugs that other workstreams are fixing are in
test_known_gaps.py as strict xfails, so they flip to failures-to-fix-the-marker once
those branches merge."""
import datetime as dt

import pytest

from harness import town, run_week  # noqa: F401  (fixtures)
from harness import (PERSONAS, PLACES, open_each_evening, over_budget, today_answer,
                     unsafe_in_week)

CART_BLOCKED = ("allergens", "medical")


def _invariants(w):
    """Hold on every view this persona saw, today."""
    assert unsafe_in_week(w) == [], unsafe_in_week(w)
    for v in w.views:
        b = v["budget"]
        assert over_budget(v) == 0.0, (b, w.p.key)                 # only the user's own pins can go over
        assert b["over"] == (b["spend"] > b["budget"] + 0.011), b   # the over flag tells the truth
        assert abs(b["remaining"] - (b["budget"] - b["spend"])) < 0.02, b
        skipped = v["counts"]["skip"]
        said = [h for h in v["heads_up"] if h["kind"] == "budget" and "didn't fit" in h["title"]]
        if said:                    # a stated skip count never exceeds the week's skips (past ones aren't news)
            assert int(said[0]["title"].split()[0]) <= skipped, (said, v["counts"])


def _full_week(w):
    w.open_app()
    favs = w.pick_favourites()
    v = w.plan_week()
    first = w.view()
    assert first["plan"]["week_start"]
    swapped = w.swap_first_two_dinners()
    tried = w.try_different()
    return favs, v, swapped, tried


@pytest.mark.parametrize("key", ["student_veg", "salaried_nonveg", "vegan_diabetic_peanut", "household_mixed",
                                 "low_budget", "sunday_noon", "minjur_periurban"])
def test_a_week_in_the_life(run_week, key):
    p = PERSONAS[key]
    w = run_week(p)
    favs, v, _, _ = _full_week(w)

    # favourites: every tap is either accepted or refused with a reason the user can read
    for f in favs:
        assert f["status"] == 200 or (f["body"] or {}).get("error"), f

    # a cart: refused up front for profiles Swiggy's menu can't verify, otherwise the
    # cart total is the bill Swiggy returned and it is checked against the week
    cells = w.delivery_cells(w.view())
    if cells:
        pre, fill = w.cart(cells[0])
        if p.allergens or p.medical or p.diet == "vegan" or any(m["diet"] == "vegan" for m in p.household):
            assert pre.status_code in (409, 502) and fill is None
            assert "cannot verify" in pre.get_json()["error"]
            assert "update_food_cart" not in w.fake.tool_calls()
        else:
            assert pre.status_code == 200, pre.get_json()
            assert fill.status_code == 200, fill.get_json()
            bill = fill.get_json()["bill"]
            assert bill["to_pay"] == pytest.approx(sum(line["amount"] for line in bill["lines"]))
            assert fill.get_json()["budget"]["cart_total"] == bill["to_pay"]

    # never an order placed by a simulation
    assert "place_food_order" not in w.fake.tool_calls()

    # confirm the first planned meal after it happened, then read Sunday's recap
    v = w.view()
    planned = [c for c in w.delivery_cells(v) if c["status"] == "active"]
    start = dt.date.fromisoformat(v["plan"]["week_start"])
    if planned:
        day = next(i for i, d in enumerate(v["grid"]) for c in d["meals"].values()
                   if c["session_id"] == planned[0]["session_id"])
        w.clock.set(dt.datetime.combine(start + dt.timedelta(days=day), dt.time(22, 0)))
        r = w.confirm(planned[0])
        assert r.status_code == 200, r.get_json()
    w.clock.set(dt.datetime.combine(start + dt.timedelta(days=6), dt.time(22, 30)))
    final = w.view()
    recap = w.recap()
    meals = recap["meals"]
    assert sum(meals[k] for k in ("had", "ahead", "skipped", "unmarked")) <= sum(final["counts"].values())
    assert recap["spend"]["spent"] == pytest.approx(sum(c["cost"] for _, _, c in w.cells(final)
                                                        if c["status"] in ("confirmed", "ordered")))
    if planned:
        assert meals["had"] + meals.get("ordered", 0) >= 1
    # unconfirmed meals are reported as unknown, never silently counted as eaten or saved
    open_past = sum(1 for _, _, c in w.cells(final) if c["kind"] in ("delivery", "cook") and c["status"] == "active")
    assert meals["unmarked"] == open_past or open_past == 0

    _invariants(w)


def test_multiple_addresses_dense_vs_periurban(run_week):
    """Minjur (peri-urban) sees two places, OMR six; favourites are kept per address and
    a place that doesn't deliver to the chosen address can't be pooled."""
    w = run_week(PERSONAS["minjur_periurban"])
    w.open_app()
    near = w.c.get(f"/api/user/{w.uid}/swiggy/restaurants", query_string={"query": "meals"}).get_json()
    assert {r["id"] for r in near["restaurants"]} == {"r-101", "r-104"}
    r = w.try_pool("dinner", "r-102", "Veg Biryani")                 # Biryani Junction: OMR only
    assert r.status_code in (400, 409, 502) and r.get_json()["error"]
    assert w.pick_favourites() and w.plan_week()
    minjur_pools = w.c.get(f"/api/user/{w.uid}/pools").get_json()["meals"]
    assert any(minjur_pools.values())
    planned = {c["restaurant"] for c in w.delivery_cells(w.view())}
    assert planned <= {PLACES["r-101"][0], PLACES["r-104"][0]}, planned

    assert w.c.post(f"/api/user/{w.uid}/swiggy/address", json={"address_id": "addr-omr"}).status_code == 200
    dense = w.c.get(f"/api/user/{w.uid}/swiggy/restaurants", query_string={"query": "meals"}).get_json()
    assert len(dense["restaurants"]) == 6
    assert w.try_pool("dinner", "r-102", "Veg Biryani").status_code == 200
    w.plan_week()
    assert w.c.post(f"/api/user/{w.uid}/swiggy/address", json={"address_id": "addr-minjur"}).status_code == 200
    back = w.c.get(f"/api/user/{w.uid}/pools").get_json()["meals"]
    # r3 #2: favourites saved at another address are kept, greyed (`elsewhere`), never planned here
    names = lambda pools: {m: [d["name"] for d in ds if not d.get("elsewhere")] for m, ds in pools.items()}   # noqa: E731
    assert names(back) == names(minjur_pools)                        # Minjur's favourites come back
    assert any(d["name"] == "Veg Biryani" and d.get("elsewhere") for d in back["dinner"])   # kept, greyed
    _invariants(w)


def test_switch_five_plans_to_try_out(run_week):
    """Critique #31: "switch 5 plans to try out". One student, five different ways of
    planning the same week; every one keeps the hard rules."""
    w = run_week(PERSONAS["student_veg"])
    w.open_app()
    w.pick_favourites()
    w.plan_week()
    tried = []
    for mode in ("balanced", "survival", "comfort"):
        r = w.c.post(f"/api/plan/{w.plan}/optimize", json={"mode": mode})
        assert r.status_code == 200, r.get_json()
        tried.append((mode, w._saw(r.get_json())))
    tried.append(("try different", w.try_different()))
    tried.append(("budget ₹2,200", w.set_budget(2200)))
    assert len(tried) == 5
    for label, v in tried:
        assert over_budget(v) == 0.0, label
        assert v["counts"]["delivery"] + v["counts"]["cook"] > 0, label        # never an empty week
    more_money = tried[-1][1]
    assert more_money["counts"]["skip"] <= tried[0][1]["counts"]["skip"]          # more budget never plans fewer meals
    _invariants(w)


def test_first_open_on_monday_morning_has_an_answer_for_today(run_week):
    w = run_week(PERSONAS["student_veg"])
    w.open_app()
    w.pick_favourites()
    v = w.plan_week()
    assert today_answer(w, v), v.get("next_up")


def test_evenings_across_the_week_when_every_dinner_fits(run_week):
    """A budget that fits every dinner: Today has an answer every evening."""
    w = run_week(PERSONAS["minjur_periurban"])
    w.open_app()
    w.pick_favourites()
    w.plan_week()
    w.set_budget(4000)
    said = open_each_evening(w)
    assert len(said) == 7 and all(answer for _, answer in said), said
