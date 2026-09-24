"""Regressions found while replacing demo assumptions with provider data."""
from smartplate.domain import allergens, ledger, nutrition
from smartplate.domain import models
from smartplate.kernel import optimizer


def test_variety_preference_is_not_a_nutrient():
    targets = nutrition.meal_target({"nutrition_targets": {"variety": "light", "kcal": 1800}}, "lunch")
    assert targets["kcal"] == 720
    assert "variety" not in targets


def test_missing_safety_evidence_is_not_clearance():
    assert allergens.violates({"allergens": ["peanut"]}, {"name": "Unknown meal"})
    assert allergens.violates({"medical": ["diabetes"]}, {"allergens": []})
    assert allergens.violates({"diet": "vegan"}, {"allergens": ["egg"], "veg": True})


def test_candidate_pool_can_fill_week_beyond_six_dishes(seeded):
    user = models.get_user(1)
    user["allergens"] = []
    user["medical"] = []
    user["rating_floor"] = 0
    plan = models.get_plan(seeded["plan_id"])
    session = models.sessions_for_plan(seeded["plan_id"])[0]
    ctx = optimizer.build_context(user, plan)
    assert len([c for c in optimizer.build_candidates(user, plan, session, ctx) if c["kind"] == "delivery"]) > 6


def test_calorie_credit_uses_current_week_only():
    data = {
        "2026-09-20": {"kcal": 500, "protein_g": 10},
        "2026-09-21": {"kcal": 1800, "protein_g": 50},
    }
    view = ledger.rolling_view(data, kcal_target=2000, protein_floor=60, today="2026-09-22")
    assert view["calories"]["balance"] == 200
