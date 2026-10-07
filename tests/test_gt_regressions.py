"""Regression tests for the bugs the ground-truth suite found (each failed before its fix).

GT-1  Egg dishes reached vegetarian profiles (Egg Puff + Chai was seeded veg=1).
GT-2  A week with money to spare left meals empty "for budget": every meal offered the
      same 6 cheapest dishes and each may repeat twice, so at most 12 meals were filled.
GT-3  The variety cap ("mostly my usual places") emptied meals the usual places could
      not cover, instead of letting a new place fill them.
GT-4  The weekly "same dish at most twice" cap ignored pinned meals (a swap pins both
      meals, so a dish could appear four times).
GT-5  Simulated checkout substitution could pick a dish already planned that day, or one
      already at its weekly cap.
"""
import json

from gt_support import MONDAY_8AM, check_plan
from smartplate import db, everyday, service
from smartplate.domain import allergens, models
from smartplate.kernel import optimizer

VEG_WEEK = {"name": "Reg", "diet": "veg", "allergens": ["peanut"], "weekly_budget": 3700,
            "meals": ["breakfast", "lunch", "dinner"], "cook": "never"}


def _plan(body):
    view = everyday.create_profile(dict(body))
    return view["user"]["id"], view["plan"]["id"], view


def _kinds(pid):
    return [d["chosen_kind"] for d in models.decisions_for_plan(pid)]


def test_gt1_egg_is_never_vegetarian(gt):
    egg_puff = next(it for it in models.menu_for_city("Chennai") if it["name"] == "Egg Puff + Chai")
    assert egg_puff["veg"] == 0                                         # seed fixed
    for diet in ("veg", "vegan"):
        assert allergens.violates({"diet": diet}, {**egg_puff, "veg": 1}) == f"not {diet}"   # rule, not just data
    assert allergens.violates({"diet": "nonveg"}, egg_puff) is None
    _, pid, view = _plan({**VEG_WEEK, "allergens": []})
    assert "Egg Puff + Chai" not in {c["item"] for d in view["grid"] for c in d["meals"].values()}


def test_gt2_a_week_with_money_to_spare_is_filled(gt):
    _, pid, view = _plan(VEG_WEEK)
    assert _kinds(pid).count("skip") == 0, [r for d in models.decisions_for_plan(pid) for r in d["reasons"]]
    assert view["budget"]["spend"] <= view["budget"]["budget"]
    assert not check_plan(pid)


def test_gt3_variety_cap_does_not_empty_meals(gt):
    body = {"name": "Reg", "diet": "veg", "weekly_budget": 3000, "meals": ["breakfast"], "cook": "never",
            "favourites": [1], "variety": "usual"}                     # one usual place, two safe dishes
    _, pid, _ = _plan(body)
    assert _kinds(pid).count("skip") == 0
    usual = sum(1 for d in models.decisions_for_plan(pid) if d.get("restaurant_id") == 1)
    assert usual == 4                                                   # both usual dishes, twice each
    assert not check_plan(pid)


def test_gt4_weekly_repeat_cap_counts_pins(gt):
    _, pid, _ = _plan(VEG_WEEK)
    sessions = [s for s in models.sessions_for_plan(pid) if s["meal"] == "dinner" and s["day"] >= 1]
    for s in sessions[:2]:
        everyday.choose(s["id"], {"item_id": 20})                       # Vegan Sambar Rice, pinned twice
    n = sum(1 for d in models.decisions_for_plan(pid) if d.get("item_id") == 20)
    assert n == 2, n
    assert not check_plan(pid)


def test_gt5_substitution_keeps_the_variety_rules(gt):
    uid, pid, _ = _plan(VEG_WEEK)
    preview = service.execution_preview(pid)
    result = service.execute(pid, expected_fingerprint=preview["fingerprint"], max_total=preview["total"])
    assert result["substituted"] >= 1                                  # a flaky chain forced substitutions
    rows = [d for d in models.decisions_for_plan(pid) if d["chosen_kind"] == "delivery"]
    per_day, per_week = {}, {}
    for d in rows:
        per_day.setdefault(d["day"], []).append(d["item_id"])
        per_week[d["item_id"]] = per_week.get(d["item_id"], 0) + 1
    assert all(len(items) == len(set(items)) for items in per_day.values()), per_day
    assert max(per_week.values()) <= optimizer.MAX_ITEM_REPEAT, per_week
    for r in result["results"]:                                         # within each reviewed price
        reviewed = next(i for i in preview["items"] if i["session_id"] == r["session_id"])
        assert r["cost"] <= reviewed["amount"] + 0.001
