"""Founder decisions, 14 Oct 2026 (round 2):

* favourites take priority whenever the money allows; Ziggy never silently swaps a
  favourite for a cheaper dish or skips it. When a meal's favourites don't fit, the meal
  is left open and a "build-out" set of scenarios is offered, applied only on a pick;
* a pin that takes the week over budget is flagged (usab #17);
* a brand-new profile is never told "your usual week costs…" (usab #18).
"""
import dataclasses

from harness import town, run_week  # noqa: F401  (fixtures)
from harness import PERSONAS, PLACES, dish_id

from smartplate.kernel import optimizer


def _ravi(run_week, budget):
    p = dataclasses.replace(PERSONAS["salaried_nonveg"], weekly_budget=budget)
    w = run_week(p)
    w.open_app()
    assert all(r["status"] == 200 for r in w.pick_favourites())
    w.plan_week()
    return w


def _favourite_names(meal):
    return {d for m, _, d in PERSONAS["salaried_nonveg"].favourites if m == meal}


def test_favourites_that_fit_are_planned_and_nothing_is_open(run_week):
    w = _ravi(run_week, 6000)
    v = w.view()
    assert v["buildout"]["needed"] is False
    for day in v["grid"]:
        for meal, c in day["meals"].items():
            if c["kind"] == "delivery":
                assert c["item"] in _favourite_names(meal), (meal, c["item"])


def test_favourites_that_dont_fit_are_never_swapped_silently(run_week):
    w = _ravi(run_week, 1500)
    v = w.view()
    # every planned delivery is one of the person's own favourites for that meal
    for day in v["grid"]:
        for meal, c in day["meals"].items():
            if c["kind"] == "delivery":
                assert c["item"] in _favourite_names(meal), (meal, c["item"])
    bo = v["buildout"]
    assert bo["needed"] and bo["count"] >= 1
    opened = [c for day in v["grid"] for c in day["meals"].values()
              if c["kind"] == "skip" and (c.get("reasons") or [""])[0].startswith(optimizer.BUILDOUT_PREFIX)]
    assert len(opened) == bo["count"]
    assert "didn't swap in something cheaper" in opened[0]["reasons"][0]
    assert "buildout" in {h["kind"] for h in v["heads_up"]}
    assert v["budget"]["spend"] <= v["budget"]["budget"] + 0.01


def test_build_out_scenarios_each_in_rupees_and_applied_only_on_a_pick(run_week):
    w = _ravi(run_week, 1500)
    before = w.view()
    r = w.c.get(f"/api/plan/{w.plan}/buildout")
    assert r.status_code == 200
    bo = r.get_json()
    assert bo["needed"] and bo["gap"] > 0 and "₹" in bo["note"]
    kinds = {s["kind"] for s in bo["scenarios"]}
    assert {"raise", "skip"} <= kinds, kinds
    for s in bo["scenarios"]:
        assert s["id"] and s["title"] and "₹" in (s["title"] + s["detail"])
    # looking changed nothing
    assert w.view()["grid"] == before["grid"]
    # a made-up choice is refused
    assert w.c.post(f"/api/plan/{w.plan}/buildout", json={"id": "raise:1"}).status_code == 400
    assert w.c.post(f"/api/plan/{w.plan}/buildout", json={}).status_code == 400
    # raising by the offered amount makes every favourite fit
    raise_ = next(s for s in bo["scenarios"] if s["kind"] == "raise")
    after = w.c.post(f"/api/plan/{w.plan}/buildout", json={"id": raise_["id"]})
    assert after.status_code == 200, after.get_json()
    a = after.get_json()
    assert a["buildout_applied"]["id"] == raise_["id"]
    assert a["user"]["weekly_budget"] == 1500 + raise_["amount"]
    assert a["buildout"]["needed"] is False, a["buildout"]
    for day in a["grid"]:
        for meal, c in day["meals"].items():
            if c["status"] == "active" and c["kind"] != "past":
                assert c["kind"] == "delivery" and c["item"] in _favourite_names(meal), (meal, c)


def test_skip_scenario_frees_the_money_for_a_favourite(run_week):
    w = _ravi(run_week, 1500)
    bo = w.c.get(f"/api/plan/{w.plan}/buildout").get_json()
    skip = next(s for s in bo["scenarios"] if s["kind"] == "skip")
    a = w.c.post(f"/api/plan/{w.plan}/buildout", json={"id": skip["id"]}).get_json()
    cell = next(c for day in a["grid"] for c in day["meals"].values() if c["session_id"] == skip["session_id"])
    assert cell["status"] == "skipped"
    assert a["buildout"]["gap"] <= bo["gap"]


def test_cheaper_dish_only_when_picked(run_week):
    w = _ravi(run_week, 1500)
    bo = w.c.get(f"/api/plan/{w.plan}/buildout").get_json()
    cheaper = [s for s in bo["scenarios"] if s["kind"] == "cheaper"]
    if not cheaper:
        return
    s = cheaper[0]
    a = w.c.post(f"/api/plan/{w.plan}/buildout", json={"id": s["id"]}).get_json()
    cell = next(c for day in a["grid"] for c in day["meals"].values() if c["session_id"] == s["session_id"])
    assert cell["kind"] == "delivery" and cell["item"] not in _favourite_names("lunch") | _favourite_names("dinner")


def test_pin_over_budget_is_flagged(run_week):
    """usab #17: a pin of their own that takes the week over budget is said, never hidden."""
    w = run_week(dataclasses.replace(PERSONAS["low_budget"], weekly_budget=300))
    w.open_app()
    w.pick_favourites()
    w.plan_week()
    cells = [c for _, _, c in w.cells(w.view()) if c["status"] == "active"]
    for c in cells[:3]:
        w.choose_live(c, "r-104", "Veg Thali")
    v = w.view()
    if not v["budget"]["over"]:
        return                         # the picks were refused up front: also fine
    warned = [h for h in v["heads_up"] if h["kind"] == "budget" and "over" in (h["title"] + h["body"]).lower()]
    assert warned and "picked yourself" in warned[0]["body"], [h["title"] for h in v["heads_up"]]
    assert warned[0]["actions"][0]["act"] == "raise-budget"


def test_new_profile_never_hears_your_usual_week(run_week):
    """usab #18: no order history, no "usual"."""
    for key in ("student_veg", "low_budget", "salaried_nonveg"):
        w = run_week(PERSONAS[key])
        w.open_app()
        w.pick_favourites()
        w.plan_week()
        for v in w.views:
            for h in v.get("heads_up", []):
                assert "usual week" not in (h.get("title", "") + h.get("body", "")).lower(), h
