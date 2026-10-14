"""Budget and "Try different dishes" from the plan itself: change the week, then undo."""
import pytest

from smartplate import db
from smartplate.app import create_app
from smartplate.domain import models


@pytest.fixture
def client(seeded):
    app = create_app()
    app.config["TESTING"] = True
    return app.test_client()


def _open_deliveries(view):
    return {c["session_id"]: c["item_id"] for day in view["grid"] for c in day["meals"].values()
            if c and c["kind"] == "delivery" and not c["pinned"] and c["status"] == "active"}


def test_try_different_changes_dishes_keeps_rules_and_undo_brings_them_back(client):
    before = client.get("/api/plan/1").get_json()
    shown = _open_deliveries(before)
    assert shown
    v = client.post("/api/plan/1/try-different", json={}).get_json()
    td = v["try_different"]
    assert td["meals"] == len(shown) and td["changed"] >= 1 and td["can_undo"]
    after = _open_deliveries(v)
    assert sum(1 for sid, iid in shown.items() if after.get(sid) != iid) == td["changed"]
    assert v["budget"]["spend"] <= v["budget"]["budget"] + 0.01               # budget is still a rule
    assert db.jl(models.get_plan(1)["avoid"]) == [sorted(set(shown.values()))]
    v = client.post("/api/plan/1/try-different/undo", json={}).get_json()
    assert db.jl(models.get_plan(1)["avoid"]) == []
    assert _open_deliveries(v) == shown                                       # the same week comes back
    assert client.post("/api/plan/1/try-different/undo", json={}).status_code == 400


def test_try_different_leaves_the_persons_own_picks(client):
    v = client.get("/api/plan/1").get_json()
    sid, item = next(iter(_open_deliveries(v).items()))
    client.post(f"/api/session/{sid}/choose", json={"item_id": item})        # their pick: pinned
    v = client.post("/api/plan/1/try-different", json={}).get_json()
    cell = next(c for day in v["grid"] for c in day["meals"].values() if c and c["session_id"] == sid)
    assert cell["item_id"] == item and cell["pinned"]


def test_try_different_with_nothing_else_keeps_the_week(client, monkeypatch):
    from smartplate.kernel import optimizer
    monkeypatch.setattr(optimizer, "AVOID_PEN", 0.0)                         # nothing better to move to
    shown = _open_deliveries(client.get("/api/plan/1").get_json())
    v = client.post("/api/plan/1/try-different", json={}).get_json()
    assert v["try_different"] == {"changed": 0, "meals": len(shown), "can_undo": False}
    assert db.jl(models.get_plan(1)["avoid"]) == []
    assert _open_deliveries(v) == shown


def test_budget_change_replans_and_can_be_put_back(client):
    weekly = models.get_user(1)["weekly_budget"]
    old = client.get("/api/plan/1").get_json()["budget"]["budget"]
    up = client.patch("/api/user/1/setup", json={"weekly_budget": weekly + 1500}).get_json()
    assert up["budget"]["budget"] > old
    back = client.patch("/api/user/1/setup", json={"weekly_budget": weekly}).get_json()     # Undo
    assert back["budget"]["budget"] == old and back["budget"]["spend"] <= old + 0.01
