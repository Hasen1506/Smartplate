"""Smoke tests over the HTTP API surface."""
import pytest

from smartplate.app import create_app


@pytest.fixture()
def client(seeded):
    app = create_app()
    app.config.update(TESTING=True)
    return app.test_client()


def test_index_and_static(client):
    assert client.get("/").status_code == 200
    assert client.get("/static/app.js").status_code == 200


def test_meta_reports_zero_cost_brain(client):
    m = client.get("/api/meta").get_json()
    assert m["brain"] == "deterministic"
    assert m["brain_cost_per_decision"] == 0.0
    assert set(m["features"].keys()) == {"5.1", "5.2", "5.3"}
    assert sum(len(v) for v in m["features"].values()) == 17


def test_plan_view_shape(client):
    v = client.get("/api/plan/1").get_json()
    for key in ("plan", "budget", "nutrition", "carbon", "counts", "grid", "coach", "week_context"):
        assert key in v
    assert len(v["grid"]) == 7


def test_optimize_and_execute_flow(client):
    assert client.post("/api/plan/1/optimize", json={"mode": "balanced"}).status_code == 200
    ex = client.post("/api/plan/1/execute").get_json()
    assert ex["placed"] == ex["attempted"]


def test_idempotency_demo_endpoint(client):
    d = client.post("/api/demo/idempotency").get_json()
    assert d["same_order_id"] is True
    assert d["second_was_deduped"] is True


def test_command_endpoint(client):
    r = client.post("/api/plan/1/command", json={"text": "skip friday dinner"}).get_json()
    assert "Fri" in r["result"]["effect"]


def test_sentiment_endpoint(client):
    r = client.post("/api/sentiment", json={"reviews": ["fresh and amazing"]}).get_json()
    assert r["score"] > 0


def test_intake_log_and_ledger_endpoints(client):
    r = client.post("/api/user/1/intake", json={"text": "dal + 2 rotis", "meal": "lunch"}).get_json()
    assert r["entry_id"] and "nutrition" in r
    led = client.get("/api/user/1/ledger").get_json()
    assert "calories" in led and "protein" in led and led["days_logged"] >= 1
