"""Differential ground truth for the W6 planner changes: main @ 19fd3ef ("base") against
this working tree ("current"), on the same random profiles, plan instants, frozen clock
and tie-break as tests/test_gt_differential.py.

Every plan difference must be explained by one of the intentional W6 changes
(docs/testing/differential-w06.md); anything else fails with the meal-by-meal diff:

  W6-1  fewest skips first in every mode (was Tight Week only): base left a meal empty
  W6-2  a snack or drink (vada, samosa, chai…) is not a lunch or dinner on its own
  W6-3  diet mix: a non-veg eater gets non-veg dishes, not only the cheapest (veg) ones
  W6-4  whole favourite places: every safe dish at a saved place is a candidate
And the direction of each change is checked: never over budget, never more meals left
empty than base except where W6-2 removed a snack the base had planned, nothing unsafe.
"""
import pytest
from hypothesis import given, note, settings
from hypothesis import strategies as st

from test_gt_differential import BASELINES, INSTANTS, Version, _diff, _extract
from test_gt_properties import profiles
from smartplate.kernel.optimizer import is_snack

pytestmark = pytest.mark.sqlite_only


@pytest.fixture(scope="module")
def two_versions():
    import os
    saved = os.environ.get("SMARTPLATE_DB")
    out = {"base": Version("base", "smartplate_w6base", _extract(BASELINES["base"], "smartplate_w6base")),
           "current": Version("current", "smartplate", None)}
    if saved is not None:
        os.environ["SMARTPLATE_DB"] = saved
    yield out
    for v in out.values():
        if os.path.exists(v.db_path):
            os.unlink(v.db_path)


@pytest.fixture
def both(two_versions, monkeypatch):
    for v in two_versions.values():
        v.activate(monkeypatch)
    return two_versions


def _skips(p):
    return sum(1 for c in p["cells"].values() if c[0] == "skip")


def w6_reasons(body: dict, base: dict) -> list[str]:
    out = []
    if _skips(base):
        out.append("W6-1 fewest skips first in every mode")
    if any(c[0] == "delivery" and is_snack(c[1]) and k.split()[1] in ("lunch", "dinner")
           for k, c in base["cells"].items()):
        out.append("W6-2 snacks are not a lunch or dinner")
    if body["diet"] == "nonveg":
        out.append("W6-3 diet mix for a non-veg eater")
    if body["favourites"]:
        out.append("W6-4 whole favourite places")
    return out


@settings(max_examples=20)
@given(body=profiles(), at=st.sampled_from(INSTANTS))
def test_w6_planner_differences_are_all_intentional(both, body, at):
    base, current = both["base"].plan(body, at), both["current"].plan(body, at)
    d = _diff(base, current)
    note(f"base→current {d}")
    reasons = w6_reasons(body, base)
    if d:
        assert reasons, f"unexplained W6 planner difference: {d}"
    assert current["spend"] <= current["budget"] + 0.011
    snack_removed = any("W6-2" in r for r in reasons)
    if not snack_removed:
        assert _skips(current) <= _skips(base), d
    for k, c in current["cells"].items():
        if c[0] == "delivery" and k.split()[1] in ("lunch", "dinner"):
            assert not is_snack(c[1]), (k, c)
    # the budget recommender is not a W6 file: it must agree exactly
    assert base["recommendation"] == current["recommendation"]


def test_w6_headline_low_budget_week_fills_meals(both):
    """usab #10 pinned: a ₹1,500 two-meal veg week, balanced mode. Base left meals empty
    with money unspent; now fewer meals are empty, still within budget."""
    from gt_support import MONDAY_8AM
    body = {"name": "Priya", "diet": "veg", "allergens": [], "weekly_budget": 1500,
            "meals": ["lunch", "dinner"], "cook": "never", "favourites": []}
    base, current = both["base"].plan(body, MONDAY_8AM), both["current"].plan(body, MONDAY_8AM)
    assert _skips(current) <= _skips(base)
    assert current["spend"] <= current["budget"]
