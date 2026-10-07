"""Golden snapshots of planner output for fixed profiles.

Each profile is planned at a frozen instant against the seeded Chennai catalogue
and compared, meal by meal, with tests/fixtures/golden/planner_<name>.json: what is
planned, where from, at what price, plus the budget envelope and the budget
recommendation. Breakfast-only, three-meal, fast-day, daily-cap, prorated and
medical profiles are covered. The planner plans breakfast, lunch and dinner; snacks
are logged, not planned, so the snack golden pins the after-meal snack log and the
nutrition ledger it feeds.

Regenerate deliberately: SMARTPLATE_UPDATE_GOLDEN=1 pytest tests/test_gt_golden_planner.py,
then review the JSON diff like any other code change.
"""
import datetime as dt

import pytest

from gt_support import MONDAY_8AM, NAVRATRI_MONDAY, assert_golden, check_plan, plan_snapshot
from smartplate import everyday, service

PROFILES = {
    "veg_peanut_three_meals": ({"name": "Asha", "diet": "veg", "allergens": ["peanut"], "weekly_budget": 2500,
                                "meals": ["breakfast", "lunch", "dinner"], "cook": "sometimes"}, MONDAY_8AM, None),
    "breakfast_only_nonveg": ({"name": "Ravi", "diet": "nonveg", "weekly_budget": 900, "meals": ["breakfast"],
                               "cook": "never"}, MONDAY_8AM, None),
    "vegan_lunch_dinner_usuals": ({"name": "Meera", "diet": "vegan", "allergens": ["dairy"], "weekly_budget": 2000,
                                   "meals": ["lunch", "dinner"], "favourites": [5, 8, 11], "variety": "usual",
                                   "cook": "sometimes"}, MONDAY_8AM, None),
    "celiac_diabetic_cook_often": ({"name": "Kiran", "diet": "nonveg", "medical": ["celiac", "diabetes"],
                                    "weekly_budget": 3000, "meals": ["breakfast", "lunch", "dinner"],
                                    "cook": "often", "goal": "protein"}, MONDAY_8AM, None),
    "navratri_fast_week": ({"name": "Lakshmi", "diet": "veg", "observances": ["navratri"], "weekly_budget": 2000,
                            "meals": ["breakfast", "lunch", "dinner"], "cook": "never"}, NAVRATRI_MONDAY, None),
    "tight_week_daily_cap": ({"name": "Dev", "diet": "nonveg", "weekly_budget": 1400, "daily_cap": 250,
                              "meals": ["breakfast", "lunch", "dinner"], "cook": "sometimes"}, MONDAY_8AM, "survival"),
    "midweek_prorated_comfort": ({"name": "Zoya", "diet": "nonveg", "allergens": ["egg"], "weekly_budget": 2100,
                                  "meals": ["lunch", "dinner"], "cook": "never"},
                                 dt.datetime(2026, 11, 4, 12, 0), "comfort"),
}


@pytest.mark.parametrize("name", sorted(PROFILES))
def test_planner_golden(gt, name):
    body, at, mode = PROFILES[name]
    gt.set(at)
    view = everyday.create_profile(dict(body))
    pid = view["plan"]["id"]
    if mode:
        service.reoptimize(pid, mode)
        view = service.plan_view(pid)
    assert not check_plan(pid)
    rec = view["recommendation"]
    snap = plan_snapshot(view)
    snap["recommendation"] = ({k: rec[k]["total"] for k in ("floor", "usual", "variety")}
                              | {"suggested_rating_floor": rec["suggested_rating_floor"]}
                              if rec.get("feasible") else {"feasible": False})
    snap["heads_up"] = sorted(h["kind"] + ": " + h["title"] for h in view["heads_up"])
    assert_golden(f"planner_{name}", snap)


def test_snack_log_golden(gt):
    """Snacks are logged after the fact (not planned): the estimate and the ledger."""
    view = everyday.create_profile({"name": "Snacker", "diet": "veg", "weekly_budget": 1500, "meals": ["dinner"]})
    uid = view["user"]["id"]
    logged = [service.log_intake(uid, text, meal="snack") for text in
              ("1 samosa and chai", "2 idli", "a banana and peanut chikki")]
    gt.advance(days=1)
    service.log_intake(uid, "masala dosa", meal="breakfast")
    ledger = service.nutrition_ledger(uid)
    assert_golden("planner_snack_log", {
        "snacks": [{k: e[k] for k in sorted(e) if k != "entry_id"} for e in logged],
        "ledger": ledger})
