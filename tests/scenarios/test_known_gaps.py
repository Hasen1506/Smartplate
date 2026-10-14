"""Known gaps the persona simulations hit today, each pinned as a strict xfail with the
workstream expected to fix it. `raises=AssertionError` keeps a broken simulation step
(an import error, a 500) from hiding as an "expected failure": only the invariant itself
may fail. When the fixing branch merges, the test passes, strict mode turns that into a
failure, and the marker is removed. Owners are W10's best reading of PLAN.md's table;
the integrator should re-point a reason if another branch ends up fixing it."""
import datetime as dt
import json

import pytest

from harness import town, run_week  # noqa: F401  (fixtures)
from harness import PERSONAS, PLACES, dishonest_messages, open_each_evening, today_answer


def gap(owner, why):
    return pytest.mark.xfail(strict=True, raises=AssertionError, reason=f"fixed by {owner}: {why}")


def _planned(run_week, key):
    w = run_week(PERSONAS[key])
    w.open_app()
    w.pick_favourites()
    w.plan_week()
    return w


# ---- W1 · safety: fail-closed filters + Tamil/Hindi lexicon ------------------------------
@gap("W1", "usab #1 / grill #38: an unmarked Tamil non-veg dish (Kozhi Varuval = chicken) is "
           "accepted as a vegan's dinner")
def test_unmarked_tamil_nonveg_is_refused_for_a_vegan(run_week):
    w = _planned(run_week, "vegan_diabetic_peanut")
    for dish in ("Kozhi Varuval", "Eral Thokku", "Nandu Masala"):
        r = w.choose_live(w.next_open_cell(), "r-201", dish)
        assert r.status_code != 200, f"{dish} accepted for a vegan"


@gap("W1", "usab #2 / grill #39: peanut dishes under Tamil names or sauces (Verkadalai Sundal, "
           "Satay Veg Bowl) are accepted for a peanut allergy")
def test_peanut_dishes_are_refused_for_a_peanut_allergy(run_week):
    w = _planned(run_week, "vegan_diabetic_peanut")
    for dish in ("Verkadalai Sundal", "Satay Veg Bowl"):
        assert w.choose_live(w.next_open_cell(), "r-201", dish).status_code != 200, dish
        assert w.try_pool("dinner", "r-201", dish).status_code != 200, dish


@gap("W1", "usab #3 / grill #37: a sweet with no sugar estimate (Rasmalai) goes into a "
           "diabetic's pool; unknown must mean unsafe")
def test_unknown_sugar_sweet_is_refused_for_a_diabetic(run_week):
    w = _planned(run_week, "vegan_diabetic_peanut")
    assert w.try_pool("dinner", "r-201", "Rasmalai").status_code != 200


@gap("W1", "grill #38: cashew-and-cream Kaju Kari is accepted for a vegan (dairy unknown "
           "must mean excluded)")
def test_dairy_curry_is_refused_for_a_vegan(run_week):
    w = _planned(run_week, "vegan_diabetic_peanut")
    assert w.choose_live(w.next_open_cell(), "r-201", "Kaju Kari").status_code != 200


# ---- W2 · time & Today correctness ------------------------------------------------------
@gap("W2", "usab #8: opened at Sunday noon, the plan starts Monday and Today has nothing "
           "for today's lunch or dinner")
def test_sunday_noon_first_open_has_an_answer_for_today(run_week):
    w = _planned(run_week, "sunday_noon")
    assert w.clock.at.strftime("%a %H:%M") == "Sun 12:00"
    assert today_answer(w, w.view()), w.view().get("next_up")


@gap("W2", "usab #9: tomorrow's meal can be put in the Swiggy cart at Sunday noon; paying "
           "would deliver now")
def test_a_cart_for_tomorrow_is_not_filled_today(run_week):
    w = _planned(run_week, "sunday_noon")
    first = w.delivery_cells(w.view())[0]
    pre, fill = w.cart(first)
    assert fill is None or fill.status_code != 200, "tomorrow's meal went into today's cart"


@gap("W2", "W10 simulation: 'I had it' is accepted at 08:00 for a lunch that hasn't happened, "
           "so the recap counts it as eaten and spent")
def test_a_meal_cannot_be_confirmed_before_it_happens(run_week):
    w = _planned(run_week, "student_veg")
    lunch = next(c for c in w.delivery_cells(w.view()) if c["status"] == "active")
    assert w.clock.at.strftime("%a %H:%M") == "Mon 08:00"
    assert w.confirm(lunch).status_code != 200


@gap("W2", "usab #8 / Vijay's sketch: on an evening whose dinner was skipped for money, "
           "Today shows tomorrow instead of today's budget and favourite picks")
def test_today_has_an_answer_every_evening(run_week):
    w = _planned(run_week, "student_veg")
    said = open_each_evening(w)
    assert all(answer for _, answer in said), [s for s in said if not s[1]]


# ---- W4 · money truth -----------------------------------------------------------------------
@gap("W4", "grill #50 / usab #19: a planned meal costs menu price + ₹35 delivery; Swiggy's "
           "₹17.58 platform fee, GST and packaging are missing")
def test_planned_cost_includes_platform_fee_and_taxes(run_week):
    w = _planned(run_week, "salaried_nonveg")
    menu = {row[0]: row[1] / 100 for p in PLACES.values() for row in p[6]}
    for c in w.delivery_cells(w.view()):
        fee = (c.get("delivery_fee") or {}).get("amount", 0)
        assert c["cost"] - menu[c["item"]] - fee >= 17, (c["item"], c["cost"], menu[c["item"]], fee)


@gap("W4", "grill #6 / #52: 'time-shifts saved ≈ ₹X against surge pricing' is claimed from "
           "a surge model the code calls a demo signal")
def test_no_surge_saving_is_claimed(run_week):
    claims = []
    for key in ("student_veg", "salaried_nonveg", "minjur_periurban"):
        w = _planned(run_week, key)
        claims += [m for v in w.views for m in dishonest_messages(v) if "surge" in m or "rush" in m]
    assert claims == []


@gap("W4", "usab #18 / grill #27: a profile made a minute ago is told 'Your usual week costs "
           "about ₹X'; there is no usual yet")
def test_no_usual_week_claim_without_order_history(run_week):
    w = _planned(run_week, "student_veg")
    assert not [m for m in dishonest_messages(w.view()) if "usual week" in m]


@gap("W4", "usab #17: on a ₹300 week a second ₹265 pin takes the week to ₹530 with no "
           "over-budget prompt or warning")
def test_a_pin_that_breaks_the_week_is_flagged(run_week):
    w = _planned(run_week, "low_budget")
    cells = [c for _, _, c in w.cells(w.view()) if c["status"] == "active"]
    w.choose_live(cells[0], "r-104", "Veg Thali")
    r = w.choose_live(cells[1], "r-104", "Veg Thali")
    if r.status_code != 200:
        return                                                 # refused / asks first: fixed
    v = w.view()
    assert v["budget"]["over"]
    warned = [h for h in v["heads_up"] if h["kind"] == "budget" and "over" in (h["title"] + h["body"]).lower()]
    assert warned, [h["title"] for h in v["heads_up"]]


# ---- W6 · planner ---------------------------------------------------------------------------
@gap("W6", "usab #16 + W10 simulation: a household of 4 with one vegan and ₹6,000 gets 12 of "
           "14 meals skipped 'to stay within budget / rating floor' with ₹5,640 unspent")
def test_household_skips_are_not_blamed_on_the_budget(run_week):
    w = _planned(run_week, "household_mixed")
    v = w.view()
    b = v["budget"]
    if b["remaining"] > 0.5 * b["budget"]:
        blamed = [r for r in v["summary_reasons"] if "budget" in r.lower() and "skip" in r.lower()]
        assert not blamed, blamed
        assert v["counts"]["delivery"] + v["counts"]["cook"] >= 7, v["counts"]


# ---- W7 · platform truth ----------------------------------------------------------------------
@gap("W7", "W10 simulation: after switching to the Minjur address the week still lists Wok "
           "Street, which doesn't deliver there, until the user re-plans by hand")
def test_address_change_drops_places_that_dont_deliver_there(run_week):
    w = _planned(run_week, "student_veg")
    assert w.c.post(f"/api/user/{w.uid}/swiggy/address", json={"address_id": "addr-minjur"}).status_code == 200
    planned = {c["restaurant"] for c in w.delivery_cells(w.view())}
    assert planned <= {PLACES["r-101"][0], PLACES["r-104"][0]}, planned


@gap("W7", "W10 simulation: a restaurant outside the address shows the user raw provider JSON "
           "('Swiggy said: {\"success\": false ...}') and a fix about clearing the cart")
def test_out_of_area_error_is_plain_language(run_week):
    w = run_week(PERSONAS["minjur_periurban"])
    w.open_app()
    body = w.try_pool("dinner", "r-102", "Veg Biryani").get_json()
    text = json.dumps(body)
    assert '{\\"success\\"' not in text and "clear the cart" not in text.lower(), body
