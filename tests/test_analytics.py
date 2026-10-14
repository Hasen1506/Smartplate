"""Self-hosted product analytics (grill #21): the events table, the five events, the
D1/D7/D30 cohort query and the key-gated /admin/metrics."""
import datetime as dt

import pytest

from smartplate import analytics, config, db
from smartplate.app import create_app


@pytest.fixture
def client(seeded):
    app = create_app()
    app.config["TESTING"] = True
    return app.test_client()


def _events(name=None):
    with db.cursor() as cur:
        if name:
            return [db.row_to_dict(r) for r in cur.execute("SELECT * FROM events WHERE name=? ORDER BY id", (name,))]
        return [db.row_to_dict(r) for r in cur.execute("SELECT * FROM events ORDER BY id")]


def test_track_writes_known_events_with_safe_props_and_dedupes(seeded):
    t0 = dt.datetime(2026, 11, 2, 8, 0)
    assert analytics.track("app_open", 1, {"route": "x", "name": "Vijay", "plan_id": 3}, once_per_s=1800, at=t0)
    assert not analytics.track("app_open", 1, at=t0 + dt.timedelta(minutes=10), once_per_s=1800)
    assert analytics.track("app_open", 1, at=t0 + dt.timedelta(minutes=31), once_per_s=1800)
    rows = _events("app_open")
    assert len(rows) == 2 and db.jl(rows[0]["props"], {}) == {"route": "x", "plan_id": 3}   # no free-text name
    with pytest.raises(ValueError):
        analytics.track("page_scrolled", 1)
    # dedupe by a prop: two plans viewed in a row both count, the same plan twice doesn't
    assert analytics.track("plan_viewed", 1, {"plan_id": 1}, once_per_s=600, dedupe_key="plan_id", at=t0)
    assert analytics.track("plan_viewed", 1, {"plan_id": 2}, once_per_s=600, dedupe_key="plan_id", at=t0)
    assert not analytics.track("plan_viewed", 1, {"plan_id": 1}, once_per_s=600, dedupe_key="plan_id", at=t0)


def test_tracking_never_breaks_a_request(seeded, monkeypatch):
    def boom():
        raise RuntimeError("database gone")
    monkeypatch.setattr(db, "connect", boom)
    assert analytics.track("app_open", 1) is False


def test_api_routes_record_the_five_events(client):
    plan = client.get("/api/user/1/plan")
    assert plan.status_code == 200
    client.get("/api/user/1/plan")                                      # reload: still one open
    assert len(_events("app_open")) == 1 and _events("app_open")[0]["user_id"] == 1
    pid = plan.get_json()["plan"]["id"]
    client.get(f"/api/plan/{pid}")
    assert [e["user_id"] for e in _events("plan_viewed")] == [1]
    # cart_filled / ordered_confirmed come from the routes that mean them
    names = analytics.record_request("POST", "/api/session/<int:session_id>/order/cart", {"session_id": 1}, 200)
    names += analytics.record_request("POST", "/api/session/<int:session_id>/confirm", {"session_id": 1}, 200)
    names += analytics.record_request("POST", "/api/session/<int:session_id>/confirm", {"session_id": 1}, 409)
    assert names == ["cart_filled", "ordered_confirmed"]
    assert _events("cart_filled")[0]["user_id"] is not None


def test_a_plan_over_budget_records_budget_overrun_once_a_day(seeded):
    body = {"plan": {"id": 7}, "user": {"id": 2}, "budget": {"over": True, "budget": 500.0, "spend": 640.0}}
    assert analytics.record_request("POST", "/api/plan/<int:plan_id>/optimize", {}, 200, body) == ["budget_overrun"]
    assert analytics.record_request("POST", "/api/plan/<int:plan_id>/optimize", {}, 200, body) == []
    under = {**body, "budget": {"over": False, "budget": 500.0, "spend": 100.0}}
    assert analytics.record_request("POST", "/api/plan/<int:plan_id>/optimize", {}, 200, under) == []
    row = _events("budget_overrun")[0]
    assert row["user_id"] == 2 and db.jl(row["props"], {}) == {"plan_id": 7, "budget": 500.0, "spend": 640.0}


def test_cohorts_d1_d7_d30(seeded):
    day0 = dt.datetime(2026, 9, 1, 9, 0)                                # a Tuesday
    for uid, offsets in {101: [0, 1, 7, 30], 102: [0, 2], 103: [0, 1], 104: [0, 8, 31]}.items():
        for k in offsets:
            analytics.track("app_open", uid, at=day0 + dt.timedelta(days=k))
    analytics.track("app_open", 105, at=dt.datetime(2026, 10, 13, 9, 0))   # too new for D7/D30
    out = analytics.cohorts(today=dt.date(2026, 10, 14))
    assert out["users"] == 5
    assert out["eligible"] == {"d1": 5, "d7": 4, "d30": 4}
    assert out["retention"] == {"d1": 40.0, "d7": 25.0, "d30": 25.0}     # exact-day retention
    assert out["retained_by"] == {"d1": 80.0, "d7": 50.0, "d30": 50.0}   # came back on or after Dk
    weeks = {c["cohort_week"]: c for c in out["cohorts"]}
    assert weeks["2026-08-31"]["users"] == 4 and weeks["2026-08-31"]["d1"] == 50.0
    assert weeks["2026-10-12"]["d7"] is None and weeks["2026-10-12"]["d7_eligible"] == 0


def test_deleting_a_profile_deletes_its_events(client):
    from smartplate import profile_data
    assert "events" in profile_data.USER_TABLES
    analytics.track("app_open", 1)
    with db.cursor() as cur:
        profile_data.purge(cur, 1)
    assert not [e for e in _events() if e["user_id"] == 1]


def test_admin_metrics_is_off_without_a_key_and_needs_it(client, monkeypatch):
    monkeypatch.setattr(config, "ADMIN_KEY", "")
    assert client.get("/admin/metrics").status_code == 404
    monkeypatch.setattr(config, "ADMIN_KEY", "s3cret-admin")
    assert client.get("/admin/metrics").status_code == 401
    assert client.get("/admin/metrics", headers={"Authorization": "Bearer wrong"}).status_code == 401
    analytics.track("app_open", 1)
    r = client.get("/admin/metrics", headers={"Authorization": "Bearer s3cret-admin"})
    assert r.status_code == 200
    body = r.get_json()
    assert set(body) >= {"retention", "funnel", "reminders"}
    assert [s["event"] for s in body["funnel"]["steps"]] == list(analytics.EVENTS)
    assert body["funnel"]["steps"][0]["people"] >= 1
