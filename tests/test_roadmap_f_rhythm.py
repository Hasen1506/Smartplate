"""Roadmap PR F — onboarding asks the user's real eating rhythm, and the planner's calorie
split, daily targets and variety cap are the user's to set (each failed before: every
profile was assumed to order every planned meal, with a fixed 25/40/35 split and a
fixed "same dish at most twice a week").
"""
import pytest

from gt_support import check_plan
from smartplate import everyday, service
from smartplate.domain import models, nutrition
from smartplate.kernel import optimizer

BASE = {"name": "Rhythm", "diet": "veg", "weekly_budget": 2500, "cook": "never"}


def _make(**extra):
    view = everyday.create_profile({**BASE, **extra})
    return view["user"]["id"], view["plan"]["id"], view


def _by_meal(pid):
    out = {}
    for d in models.decisions_for_plan(pid):
        out.setdefault(d["meal"], []).append(d)
    return out


def test_f1_rhythm_plans_cooked_meals_as_home_cooking_and_skips_skipped_ones(gt):
    uid, pid, view = _make(rhythm={"breakfast": "cook", "lunch": "skip", "dinner": "order"})
    meals = _by_meal(pid)
    assert set(meals) == {"breakfast", "dinner"}                       # lunch isn't planned at all
    assert all(d["chosen_kind"] == "cook" and d["recipe_key"] for d in meals["breakfast"])
    assert "You cook this meal yourself" in meals["breakfast"][0]["reasons"][0]
    assert all(d["chosen_kind"] == "delivery" for d in meals["dinner"])
    user = models.get_user(uid)
    assert user["prefs"]["rhythm"] == {"breakfast": "cook", "lunch": "skip", "dinner": "order"}
    assert user["prefs"]["meals"] == ["breakfast", "dinner"]
    assert not check_plan(pid)


def test_f1b_routine_cooking_does_not_use_up_flexible_cook_days(gt):
    """'cook: never' limits swapping ORDERED meals for cooking; it must not stop the
    meals the user said they cook."""
    _, pid, _ = _make(rhythm={"breakfast": "cook", "lunch": "order", "dinner": "order"}, cook="never")
    meals = _by_meal(pid)
    assert len([d for d in meals["breakfast"] if d["chosen_kind"] == "cook"]) == 7
    assert not [d for m in ("lunch", "dinner") for d in meals[m] if d["chosen_kind"] == "cook"]


def test_f1c_rhythm_is_validated(gt):
    for bad in ({"breakfast": "maybe"}, {"brunch": "order"}, {"breakfast": "skip", "lunch": "skip", "dinner": "skip"}, []):
        with pytest.raises(ValueError):
            everyday.create_profile({**BASE, "rhythm": bad})


def test_f1d_changing_rhythm_in_settings_replans(gt):
    uid, pid, _ = _make(rhythm={"breakfast": "skip", "lunch": "order", "dinner": "order"})
    everyday.update_setup(uid, {"rhythm": {"breakfast": "skip", "lunch": "cook", "dinner": "order"}})
    meals = _by_meal(service.current_plan(uid)["plan"]["id"])
    assert all(d["chosen_kind"] == "cook" for d in meals["lunch"])


def test_f2_calorie_split_is_the_users(gt):
    uid, _, _ = _make(tuning={"meal_share": {"breakfast": 0.2, "lunch": 0.3, "dinner": 0.5}})
    user = models.get_user(uid)
    assert nutrition.meal_shares(user) == {"breakfast": 0.2, "lunch": 0.3, "dinner": 0.5}
    t = nutrition.targets_for(user)["kcal"]
    assert nutrition.meal_target(user, "dinner")["kcal"] == pytest.approx(t * 0.5)
    with pytest.raises(ValueError, match="100%"):
        everyday.create_profile({**BASE, "tuning": {"meal_share": {"breakfast": 0.5, "lunch": 0.5, "dinner": 0.5}}})
    with pytest.raises(ValueError):
        everyday.create_profile({**BASE, "tuning": {"meal_share": {"breakfast": 0.05, "lunch": 0.45, "dinner": 0.5}}})


def test_f3_targets_are_the_users(gt):
    uid, _, _ = _make(tuning={"kcal": 2400, "protein_g": 110})
    user = models.get_user(uid)
    assert nutrition.targets_for(user)["kcal"] == 2400 and nutrition.targets_for(user)["protein_g"] == 110
    assert user["health_targets"]["protein_floor_g"] == 110
    everyday.update_setup(uid, {"tuning": {"kcal": 1800}})
    assert nutrition.targets_for(models.get_user(uid))["kcal"] == 1800
    with pytest.raises(ValueError):
        everyday.update_setup(uid, {"tuning": {"kcal": 300}})


def test_f4_variety_cap_is_the_users(gt):
    """Some people want the same lunch every day: max_repeat 5 lets one dish fill five
    lunches; the default keeps it at two."""
    body = {"rhythm": {"breakfast": "skip", "lunch": "order", "dinner": "skip"}, "favourites": [1],
            "variety": "usual"}
    _, pid_default, _ = _make(**body)
    uid, pid, _ = _make(**body, tuning={"max_repeat": 5})
    assert optimizer.item_repeat(models.get_user(uid)) == 5

    def most(p):
        counts = {}
        for d in models.decisions_for_plan(p):
            if d["chosen_kind"] == "delivery":
                counts[d["item_id"]] = counts.get(d["item_id"], 0) + 1
        return max(counts.values())
    assert most(pid_default) <= 2
    assert 2 < most(pid) <= 5
    assert not check_plan(pid_default)


def test_f5_without_a_rhythm_nothing_changes(gt):
    _, pid, view = _make(meals=["lunch", "dinner"])
    assert "rhythm" not in models.get_user(view["user"]["id"])["prefs"]
    assert {d["meal"] for d in models.decisions_for_plan(pid)} == {"lunch", "dinner"}
