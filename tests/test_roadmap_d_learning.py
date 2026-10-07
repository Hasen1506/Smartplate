"""Roadmap PR D — one-tap rating reasons move specific planner weights (each failed
before: a rating was only 👍/👎 on a dish, so "late", "small", "too spicy" or "too pricey"
changed nothing, or excluded the dish outright).

Every reason is checked twice: the exact weight it moves (and that nothing else moves),
and the direction it pushes a real, from-scratch re-plan.
"""
import pytest

from smartplate import config, everyday, service
from smartplate.domain import learning, models, taste
from smartplate.kernel import optimizer

WEEK = {"name": "Learner", "diet": "nonveg", "weekly_budget": 3200, "meals": ["breakfast", "lunch", "dinner"],
        "cook": "never"}
EMPTY = {"late_pen": {}, "portion": {}, "spicy_pen": {}, "spicy_tag_pen": 0.0, "cost_mult": 1.0,
         "pricey_pen": {}, "great_bonus": {}, "great_place_bonus": {}}


KEYS = {}


def _setup():
    view = everyday.create_profile(dict(WEEK))
    KEYS[view["user"]["id"]] = view["access_key"]
    return view["user"]["id"], view["plan"]["id"]


def _deliveries(pid):
    return [d for d in models.decisions_for_plan(pid) if d["chosen_kind"] == "delivery"]


def _fresh(pid):
    """A from-scratch re-plan (what "Plan next week" does). Taps re-plan the current week
    stably, so a small learned nudge does not reshuffle meals the user already saw; the
    fresh plan is where each learned weight shows in full."""
    optimizer.optimize(pid, stable=False)
    return _deliveries(pid)


def _tap(pid, reason, n=1, *, item_id=None, restaurant_id=None):
    """Tap `reason` on n planned meals (of that dish / restaurant when given)."""
    rows = [d for d in _deliveries(pid) if (item_id is None or d["item_id"] == item_id)
            and (restaurant_id is None or d["restaurant_id"] == restaurant_id)]
    assert len(rows) >= n, (reason, len(rows))
    for d in rows[:n]:
        everyday.rate(d["session_id"], None, [reason])


def _changed_keys(w):
    return {k for k, v in w.items() if v != EMPTY[k]}


def test_d0_no_reasons_no_change(gt):
    uid, _ = _setup()
    assert learning.weights(uid) == EMPTY


def test_d1_late_penalises_that_restaurant_only(gt):
    uid, pid = _setup()
    base = _fresh(pid)
    top = max({d["restaurant_id"] for d in base}, key=lambda r: sum(1 for d in base if d["restaurant_id"] == r))
    before = sum(1 for d in base if d["restaurant_id"] == top)
    _tap(pid, "late", 2, restaurant_id=top)
    w = learning.weights(uid)
    assert _changed_keys(w) == {"late_pen"} and w["late_pen"] == {top: 0.5}
    after = sum(1 for d in _fresh(pid) if d["restaurant_id"] == top)
    assert after < before, (before, after)
    # "late" is not "not again": nothing is excluded
    assert not taste.signals(uid)["disliked"]


def test_d2_small_portion_counts_that_dish_as_less_food(gt):
    uid, pid = _setup()
    d = _deliveries(pid)[0]
    item = next(it for it in models.menu_for_city("Chennai") if it["id"] == d["item_id"])
    _tap(pid, "small", 1, item_id=item["id"])
    w = learning.weights(uid)
    assert _changed_keys(w) == {"portion"} and w["portion"] == {item["id"]: 0.85}
    user, plan = models.get_user(uid), models.get_plan(pid)
    ctx = optimizer.build_context(user, plan)
    session = next(s for s in models.sessions_for_plan(pid) if s["id"] == d["session_id"])
    cand = optimizer._delivery_candidate(user, plan, session, item, ctx)
    assert cand["nutrition"]["kcal"] == round(item["kcal"] * 0.85, 1)
    assert cand["nutrition"]["protein_g"] == round(item["protein_g"] * 0.85, 1)


def test_d3_too_spicy_pushes_that_dish_down(gt):
    uid, pid = _setup()
    counts = {}
    for d in _fresh(pid):
        counts[d["item_id"]] = counts.get(d["item_id"], 0) + 1
    dish = max(counts, key=counts.get)
    _tap(pid, "spicy", counts[dish], item_id=dish)
    w = learning.weights(uid)
    assert _changed_keys(w) == {"spicy_pen", "spicy_tag_pen"} and w["spicy_pen"] == {dish: round(0.3 * min(counts[dish], 3), 4)}
    after = sum(1 for d in _fresh(pid) if d["item_id"] == dish)
    assert after < counts[dish]


def test_d4_too_pricey_raises_the_cost_weight_and_cuts_spend(gt):
    uid, pid = _setup()
    _fresh(pid)
    spend_before = service.plan_view(pid)["budget"]["spend"]
    _tap(pid, "pricey", 3)
    w = learning.weights(uid)
    assert w["cost_mult"] == pytest.approx(1.45) and _changed_keys(w) == {"cost_mult", "pricey_pen"}
    ctx = optimizer.build_context(models.get_user(uid), models.get_plan(pid))
    assert ctx["weights"]["cost"] == pytest.approx(config.MODE_WEIGHTS["balanced"]["cost"] * 1.45)
    _fresh(pid)
    assert service.plan_view(pid)["budget"]["spend"] < spend_before


def test_d5_great_pulls_that_dish_up_and_counts_as_a_like(gt):
    uid, pid = _setup()
    counts = {}
    for d in _fresh(pid):
        counts[d["item_id"]] = counts.get(d["item_id"], 0) + 1
    dish = min(counts, key=counts.get)                    # planned once: room to grow to twice
    assert counts[dish] == 1
    _tap(pid, "great", 1, item_id=dish)
    w = learning.weights(uid)
    assert w["great_bonus"] == {dish: 0.12} and _changed_keys(w) == {"great_bonus", "great_place_bonus"}
    assert dish in taste.signals(uid)["liked"]
    assert sum(1 for d in _fresh(pid) if d["item_id"] == dish) == optimizer.MAX_ITEM_REPEAT


def test_d6_undo_restores_the_plan_exactly(gt, client_for):
    uid, pid = _setup()
    first = [(d["session_id"], d["item_id"]) for d in _fresh(pid)]
    top = _deliveries(pid)[0]["restaurant_id"]
    _tap(pid, "late", 2, restaurant_id=top)
    assert [(d["session_id"], d["item_id"]) for d in _fresh(pid)] != first
    c = client_for(uid)
    chips = c.get(f"/api/user/{uid}/learned").get_json()["learned"]
    assert [x["key"] for x in chips] == [f"late:{top}"] and "arrives late" in chips[0]["text"]
    r = c.post(f"/api/user/{uid}/learned/undo", json={"key": f"late:{top}"}).get_json()
    assert r["learned"] == [] and learning.weights(uid) == EMPTY
    assert [(d["session_id"], d["item_id"]) for d in _fresh(pid)] == first


def test_d7_reasons_are_validated_and_one_set_per_meal(gt, client_for):
    uid, pid = _setup()
    d = _deliveries(pid)[0]
    c = client_for(uid)
    assert c.post(f"/api/session/{d['session_id']}/rate", json={"reasons": ["salty"]}).status_code == 400
    c.post(f"/api/session/{d['session_id']}/rate", json={"reasons": ["late", "pricey"]})
    c.post(f"/api/session/{d['session_id']}/rate", json={"reasons": ["pricey"]})      # re-tap replaces
    assert learning.weights(uid)["late_pen"] == {} and learning.weights(uid)["cost_mult"] == pytest.approx(1.15)
    cell = next(x for day in service.plan_view(pid)["grid"] for x in day["meals"].values()
                if x.get("session_id") == d["session_id"])
    assert cell["reasons_given"] == ["pricey"]
    assert c.post(f"/api/session/{d['session_id']}/rate", json={}).status_code == 400   # still needs a score or reason


@pytest.fixture
def client_for(gt):
    from smartplate.app import create_app
    app = create_app()
    app.config["TESTING"] = True

    def make(uid):
        c = app.test_client()
        c.environ_base["HTTP_X_SMARTPLATE_KEY"] = KEYS[uid]
        return c
    return make
