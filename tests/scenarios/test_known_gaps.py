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


def gap_open(owner, why):
    """A gap owned by a branch not merged yet that already passes on the integration branch
    (side effect of other fixes). Not strict, so the owner confirms it before removing it."""
    return pytest.mark.xfail(strict=False, raises=AssertionError, reason=f"fixed by {owner}: {why}")


def _planned(run_week, key):
    w = run_week(PERSONAS[key])
    w.open_app()
    w.pick_favourites()
    w.plan_week()
    return w


# ---- W1 · safety: fail-closed filters + Tamil/Hindi lexicon ------------------------------
# Was a known gap, fixed by W1 (strict xfail removed at integration).
def test_unmarked_tamil_nonveg_is_refused_for_a_vegan(run_week):
    w = _planned(run_week, "vegan_diabetic_peanut")
    for dish in ("Kozhi Varuval", "Eral Thokku", "Nandu Masala"):
        r = w.choose_live(w.next_open_cell(), "r-201", dish)
        assert r.status_code != 200, f"{dish} accepted for a vegan"


# Was a known gap, fixed by W1 (strict xfail removed at integration).
def test_peanut_dishes_are_refused_for_a_peanut_allergy(run_week):
    w = _planned(run_week, "vegan_diabetic_peanut")
    for dish in ("Verkadalai Sundal", "Satay Veg Bowl"):
        assert w.choose_live(w.next_open_cell(), "r-201", dish).status_code != 200, dish
        assert w.try_pool("dinner", "r-201", dish).status_code != 200, dish


# Was a known gap, fixed by W1 (strict xfail removed at integration).
def test_unknown_sugar_sweet_is_refused_for_a_diabetic(run_week):
    w = _planned(run_week, "vegan_diabetic_peanut")
    assert w.try_pool("dinner", "r-201", "Rasmalai").status_code != 200


# Was a known gap, fixed by W1 (strict xfail removed at integration).
def test_dairy_curry_is_refused_for_a_vegan(run_week):
    w = _planned(run_week, "vegan_diabetic_peanut")
    assert w.choose_live(w.next_open_cell(), "r-201", "Kaju Kari").status_code != 200


# ---- W2 · time & Today correctness ------------------------------------------------------
# Was a known gap, fixed by W2 (strict xfail removed at integration).
def test_sunday_noon_first_open_has_an_answer_for_today(run_week):
    w = _planned(run_week, "sunday_noon")
    assert w.clock.at.strftime("%a %H:%M") == "Sun 12:00"
    assert today_answer(w, w.view()), w.view().get("next_up")


# Was a known gap, fixed by W2 (strict xfail removed at integration).
def test_today_has_an_answer_every_evening(run_week):
    w = _planned(run_week, "student_veg")
    said = open_each_evening(w)
    assert all(answer for _, answer in said), [s for s in said if not s[1]]


# ---- W4 · money truth -----------------------------------------------------------------------
# Was a known gap, fixed by W4 (strict xfail removed at integration).
def test_planned_cost_includes_platform_fee_and_taxes(run_week):
    w = _planned(run_week, "salaried_nonveg")
    menu = {row[0]: row[1] / 100 for p in PLACES.values() for row in p[6]}
    for c in w.delivery_cells(w.view()):
        fee = (c.get("delivery_fee") or {}).get("amount", 0)
        assert c["cost"] - menu[c["item"]] - fee >= 17, (c["item"], c["cost"], menu[c["item"]], fee)


# Was a known gap, fixed by W4 (strict xfail removed at integration).
def test_no_surge_saving_is_claimed(run_week):
    claims = []
    for key in ("student_veg", "salaried_nonveg", "minjur_periurban"):
        w = _planned(run_week, key)
        claims += [m for v in w.views for m in dishonest_messages(v) if "surge" in m or "rush" in m]
    assert claims == []


# Was a known gap (usab #18 / grill #27), fixed in round 2: "your usual week" only with order history.
def test_no_usual_week_claim_without_order_history(run_week):
    w = _planned(run_week, "student_veg")
    assert not [m for m in dishonest_messages(w.view()) if "usual week" in m]


# Was a known gap (usab #17), fixed in round 2: a pin that takes the week over the target is flagged.
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
# Was a known gap, fixed by W6 (strict xfail removed at integration).
def test_household_skips_are_not_blamed_on_the_budget(run_week):
    w = _planned(run_week, "household_mixed")
    v = w.view()
    b = v["budget"]
    if b["remaining"] > 0.5 * b["budget"]:
        blamed = [r for r in v["summary_reasons"] if "budget" in r.lower() and "skip" in r.lower()]
        assert not blamed, blamed
        assert v["counts"]["delivery"] + v["counts"]["cook"] >= 7, v["counts"]


# ---- W7 · platform truth ----------------------------------------------------------------------
# Was a known gap, fixed by W7 (non-strict xfail removed at integration).
def test_address_change_drops_places_that_dont_deliver_there(run_week):
    w = _planned(run_week, "student_veg")
    assert w.c.post(f"/api/user/{w.uid}/swiggy/address", json={"address_id": "addr-minjur"}).status_code == 200
    planned = {c["restaurant"] for c in w.delivery_cells(w.view())}
    assert planned <= {PLACES["r-101"][0], PLACES["r-104"][0]}, planned


# Was a known gap, fixed by W7 (strict xfail removed at integration).
def test_out_of_area_error_is_plain_language(run_week):
    w = run_week(PERSONAS["minjur_periurban"])
    w.open_app()
    body = w.try_pool("dinner", "r-102", "Veg Biryani").get_json()
    text = json.dumps(body)
    assert '{\\"success\\"' not in text and "clear the cart" not in text.lower(), body
