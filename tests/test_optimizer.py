"""The MILP planner: feasibility, budget, mode behaviour, and realism caps."""
from smartplate import service
from smartplate.domain import models
from smartplate.kernel import optimizer


def test_plan_is_feasible_and_complete(seeded):
    res = optimizer.optimize(seeded["plan_id"])
    assert res["status"] == "Optimal"
    decisions = models.decisions_for_plan(seeded["plan_id"])
    # 7 days x 3 meals, every session has exactly one decision
    assert len(decisions) == 21
    for d in decisions:
        assert d["chosen_kind"] in ("delivery", "cook", "skip")


def test_budget_is_a_hard_constraint(seeded):
    optimizer.optimize(seeded["plan_id"])
    user = models.get_user(1)
    view = service.plan_view(seeded["plan_id"])
    assert view["budget"]["spend"] <= user["weekly_budget"] + 1e-6


def test_survival_costs_no_more_than_comfort(seeded):
    pid = seeded["plan_id"]
    service.reoptimize(pid, "comfort")
    comfort = service.plan_view(pid)["budget"]["spend"]
    service.reoptimize(pid, "survival")
    survival = service.plan_view(pid)["budget"]["spend"]
    assert survival <= comfort


def test_cook_cap_respected(seeded):
    optimizer.optimize(seeded["plan_id"])
    decisions = models.decisions_for_plan(seeded["plan_id"])
    recipe_cooks = [d for d in decisions if d["chosen_kind"] == "cook" and d.get("recipe_key")]
    assert len(recipe_cooks) <= optimizer.MAX_COOK_PER_WEEK


def test_variety_cap_respected(seeded):
    optimizer.optimize(seeded["plan_id"])
    counts = {}
    for d in models.decisions_for_plan(seeded["plan_id"]):
        if d["chosen_kind"] == "delivery" and d["item_id"]:
            counts[d["item_id"]] = counts.get(d["item_id"], 0) + 1
    assert all(c <= optimizer.MAX_ITEM_REPEAT for c in counts.values())


def test_explainability_present_on_every_decision(seeded):
    optimizer.optimize(seeded["plan_id"])
    for d in models.decisions_for_plan(seeded["plan_id"]):
        assert isinstance(d["reasons"], list) and len(d["reasons"]) >= 1
