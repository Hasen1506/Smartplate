"""Checkout consent controls protect automated ordering from stale plans/overspend."""
from smartplate import service
from smartplate.app import create_app


def test_preview_matches_delivery_decisions(seeded):
    preview = service.execution_preview(seeded["plan_id"])
    assert preview["currency"] == "INR"
    assert preview["order_count"] == len(preview["items"])
    assert preview["total"] == round(sum(i["amount"] for i in preview["items"]), 2)
    assert len(preview["fingerprint"]) == 24
    assert all(item["meal"] in ("breakfast", "lunch", "dinner") for item in preview["items"])
    assert all(0 <= item["day"] <= 6 for item in preview["items"])


def test_execute_accepts_reviewed_plan_and_spend_ceiling(seeded):
    app = create_app()
    app.config.update(TESTING=True)
    client = app.test_client()
    preview = client.get(f"/api/plan/{seeded['plan_id']}/execute/preview").get_json()
    response = client.post(f"/api/plan/{seeded['plan_id']}/execute", json={
        "expected_fingerprint": preview["fingerprint"], "max_total": preview["total"]})
    assert response.status_code == 200
    assert response.get_json()["attempted"] == preview["order_count"]


def test_execute_rejects_overspend_without_placing_orders(seeded):
    app = create_app()
    app.config.update(TESTING=True)
    client = app.test_client()
    preview = client.get(f"/api/plan/{seeded['plan_id']}/execute/preview").get_json()
    response = client.post(f"/api/plan/{seeded['plan_id']}/execute", json={"max_total": 0})
    assert response.status_code == 409
    assert response.get_json()["error"] == "checkout_conflict"
    assert response.get_json()["preview"]["fingerprint"] == preview["fingerprint"]


def test_execute_rejects_stale_fingerprint(seeded):
    app = create_app()
    app.config.update(TESTING=True)
    response = app.test_client().post(f"/api/plan/{seeded['plan_id']}/execute", json={
        "expected_fingerprint": "stale-review"})
    assert response.status_code == 409
    assert "plan changed" in response.get_json()["message"]


def test_preview_and_execute_unknown_plan_are_404(seeded):
    app = create_app()
    app.config.update(TESTING=True)
    client = app.test_client()
    assert client.get("/api/plan/99999/execute/preview").status_code == 404
    assert client.post("/api/plan/99999/execute").status_code == 404
