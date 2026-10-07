"""Roadmap PR A — the two open decisions (each test failed before its fix).

A-1  Tight Week skipped meals while money was left: a flat skip penalty of 1.6 let any
     dish dearer than ~1.6x the average meal lose to skipping. A ₹1,000 vegetarian week
     skipped 19-21 of 21 meals and spent under ₹100.
A-2  Skips cost nothing nutritionally, so the planner did not care which meal it emptied.
A-3  Tight Week offered no cheap home-cook before a skip once the user's cook count was
     used, and a skip said only "No safe option within your rating floor and budget".
A-4  An unconnected (or expired) Swiggy answered 502, the status for an upstream outage.
"""
import pytest

from gt_support import MONDAY_8AM, check_plan
from hypothesis import given, settings
from hypothesis import strategies as st
from smartplate import everyday, service
from smartplate.domain import models, nutrition, profile, reverse_mode
from smartplate.kernel import optimizer

THREE = ["breakfast", "lunch", "dinner"]


@pytest.fixture
def app(seeded):
    from smartplate.app import create_app
    a = create_app()
    a.config["TESTING"] = True
    return a


@pytest.fixture
def client(app):
    return app.test_client()


def _tight(body):
    view = everyday.create_profile(dict(body))
    pid = view["plan"]["id"]
    service.reoptimize(pid, "survival")
    return view["user"]["id"], pid


def _rows(pid):
    return models.decisions_for_plan(pid)


def _spend(pid):
    return sum(d["cost"] for d in _rows(pid) if d["chosen_kind"] in ("delivery", "cook"))


@pytest.mark.parametrize("cook", ["never", "sometimes"])
def test_a1_tight_week_spends_the_money_before_skipping(gt, cook):
    uid, pid = _tight({"name": "Tight", "diet": "veg", "weekly_budget": 1000, "meals": THREE, "cook": cook})
    rows = _rows(pid)
    assert [d for d in rows if d["chosen_kind"] == "skip"] == []
    cap = optimizer.week_cap(models.get_user(uid), models.get_plan(pid))
    assert cap - 35 < _spend(pid) <= cap                   # less than the cheapest meal is left
    assert not check_plan(pid)


def test_a1_extra_cooks_only_stand_in_for_skips(gt):
    """With money for deliveries, a never-cook user's Tight Week has no home-cooks."""
    _, pid = _tight({"name": "Roomy", "diet": "nonveg", "weekly_budget": 4000, "meals": THREE, "cook": "never"})
    kinds = [d["chosen_kind"] for d in _rows(pid)]
    assert kinds.count("skip") == 0 and kinds.count("cook") == 0, kinds


def test_a2_a_skip_is_charged_its_nutrition_shortfall(gt):
    user = {"nutrition_targets": {}}
    empty = nutrition.penalty(user, "lunch", {}, tol=0.28)
    assert empty > 0.8                                       # the whole meal is missing
    skip = optimizer._skip(forced=False, reason="")
    skip["nutri"] = empty
    w = {"nutrition": 0.7}
    assert optimizer._objective(skip, w, 100, 0, 1.6) == pytest.approx(1.6 + 0.7 * empty)
    forced = optimizer._skip(forced=True, reason="fast")
    assert optimizer._objective(forced, w, 100, 0, 1.6) == 0.0          # a fast or a trip is free


def test_a3_unavoidable_skips_are_labelled_with_the_money_left(gt):
    """₹700 cannot feed 21 meals even at ₹35 a cook: the few skips left say why, in rupees."""
    uid, pid = _tight({"name": "Broke", "diet": "veg", "weekly_budget": 700, "meals": THREE, "cook": "never"})
    rows = _rows(pid)
    skips = [d for d in rows if d["chosen_kind"] == "skip"]
    assert 0 < len(skips) <= 4
    for d in skips:
        assert d["item_name"] == "Budget skip"
        assert d["reasons"][0].startswith("Budget skip: ₹") and "cheapest safe option here is ₹" in d["reasons"][0]
    extra = [d for d in rows if d["chosen_kind"] == "cook" and "instead of skipping" in d["reasons"][0]]
    assert extra, "home-cooks beyond the usual count say they replace a skip"
    assert 700 - _spend(pid) < 35
    heads = service.plan_view(pid)["heads_up"]               # the heads-up still counts them
    assert any(h["kind"] == "budget" and "didn't fit" in h["title"] for h in heads), heads


@settings(max_examples=10)
@given(budget=st.integers(300, 2500), diet=st.sampled_from(profile.DIETS),
       meals=st.lists(st.sampled_from(THREE), unique=True, min_size=1),
       cook=st.sampled_from(sorted(everyday.COOK_BY_ANSWER)))
def test_a1_property_no_tight_week_skip_while_a_safe_cook_fits(gt, budget, diet, meals, cook):
    """Every money skip in a Tight Week has less money left (that week, or under that
    day's cap) than the cheapest safe home-cook for that meal."""
    from smartplate import ratelimit
    ratelimit.reset()
    gt.set(MONDAY_8AM)
    uid, pid = _tight({"name": "P", "diet": diet, "weekly_budget": budget, "meals": meals, "cook": cook})
    user, plan = models.get_user(uid), models.get_plan(pid)
    rows = _rows(pid)
    left = optimizer.week_cap(user, plan) - _spend(pid)
    for d in rows:
        if d["chosen_kind"] != "skip" or d["session_status"] != "active":
            continue
        r = reverse_mode.cook_candidate(user, d["meal"])
        if r is None:
            continue
        assert r["cost"] > left + 1e-6, (d["day"], d["meal"], left, r["cost"], d["reasons"])


# --------------------------------------------------------------------------- #
# A-4: Swiggy not connected is a 409 with a Connect action
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("path", ["/api/user/1/swiggy/addresses", "/api/user/1/swiggy/restaurants?query=dosa",
                                  "/api/user/1/swiggy/menu?restaurant=Saravana"])
def test_a4_unconnected_swiggy_is_409_with_connect(client, path):
    r = client.get(path)
    body = r.get_json()
    assert r.status_code == 409, (r.status_code, body)
    assert body["error"] == "swiggy_not_connected" and body["code"] == "swiggy_not_connected"
    assert body["action"] == {"label": "Connect Swiggy", "act": "swiggy-connect"}
    assert body["connect_url"] == "/api/user/1/swiggy/connect"


def test_a4_real_upstream_failures_stay_502(app):
    from smartplate.integrations import swiggy_connect

    @app.get("/__boom")
    def boom():
        raise swiggy_connect.SwiggyError("Swiggy's MCP server returned HTTP 500", http_status=500)
    r = app.test_client().get("/__boom")
    assert r.status_code == 502 and "HTTP 500" in r.get_json()["error"]
