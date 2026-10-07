"""Regression from merging Roadmap A (Tight Week extra home-cooks) with Roadmap F (meals the
user cooks themselves). Labelling the Tight Week stand-in cooks walked the week from the end
and counted the user's own routine cooks as "extra", so a real stand-in cook for an ordered
meal lost its "instead of skipping" reason and read as an ordinary cook day."""
from gt_support import check_plan
from smartplate import everyday, service
from smartplate.domain import models


def test_tight_week_labels_only_flexible_cooks_as_stand_ins_for_a_skip(gt):
    view = everyday.create_profile({"name": "Tight cook", "diet": "veg", "weekly_budget": 700,
                                    "cook": "never",
                                    "rhythm": {"breakfast": "cook", "lunch": "order", "dinner": "order"}})
    pid = view["plan"]["id"]
    service.reoptimize(pid, "survival")
    rows = models.decisions_for_plan(pid)
    routine = [d for d in rows if d["meal"] == "breakfast"]
    flexible_cooks = [d for d in rows if d["meal"] != "breakfast" and d["chosen_kind"] == "cook"]
    assert routine and all(d["chosen_kind"] == "cook" for d in routine)
    assert all("You cook this meal yourself" in d["reasons"][0] for d in routine)
    assert flexible_cooks, "a ₹700 never-cook week needs Tight Week stand-in cooks"
    # a never-cook user's every lunch/dinner cook is a stand-in for a skip, and says so
    assert all(d["reasons"][0].startswith("Tight Week:") for d in flexible_cooks), \
        [d["reasons"][0] for d in flexible_cooks]
    assert not check_plan(pid)
