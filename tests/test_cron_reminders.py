"""Reminders that survive a sleeping server (grill #22): POST /cron/tick, idempotent
sending, late reminders sent as late, missed ones recorded instead of silently dropped."""
import datetime as dt
import threading

import pytest

from smartplate import config, db, push
from smartplate.app import create_app
from test_push import FakeService, _browser_subscription

KEY = "cron-test-key-123"


@pytest.fixture
def client(seeded, monkeypatch):
    monkeypatch.setattr(config, "CRON_KEY", KEY)
    push._backoff.clear()
    push.invalidate()
    app = create_app()
    app.config["TESTING"] = True
    return app.test_client()


def _subscribe(client, created_ts="2000-01-01T00:00:00"):
    client.post("/api/user/1/push/subscribe", json={"subscription": _browser_subscription()})
    with db.cursor() as cur:                          # opted in long before any reminder
        cur.execute("UPDATE push_subscriptions SET created_ts=?", (created_ts,))
    return client.get("/api/user/1/reminders").get_json()[0]


def _outcomes():
    with db.cursor() as cur:
        return {(r["session_id"]): r["outcome"] for r in cur.execute("SELECT * FROM push_sent")}


def test_cron_tick_is_off_without_a_key_and_needs_it(client, monkeypatch):
    assert client.post("/cron/tick", json={}).status_code == 401
    assert client.get("/cron/tick", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert client.get("/cron/tick", headers={"X-Cron-Key": KEY}).status_code == 200
    monkeypatch.setattr(config, "CRON_KEY", "")
    assert client.get("/cron/tick", headers={"X-Cron-Key": KEY}).status_code == 404


def test_cron_tick_sends_due_reminders_once(client, monkeypatch):
    first = _subscribe(client)
    when = dt.datetime.fromisoformat(first["at"])
    svc = FakeService()
    monkeypatch.setattr(push.webpush, "send", svc)
    monkeypatch.setattr(push.clock, "now", lambda: when + dt.timedelta(minutes=2))
    r = client.post("/cron/tick", json={}, headers={"Authorization": f"Bearer {KEY}"}).get_json()
    assert r["ok"] and r["sent"] >= 1
    tags = [p["tag"] for _, p in svc.calls]
    assert f"smartplate-{first['session_id']}" in tags and not svc.calls[0][1]["title"].startswith("Late")
    again = client.post("/cron/tick?force=1", json={}, headers={"Authorization": f"Bearer {KEY}"}).get_json()
    assert again.get("sent", 0) == 0 and len(svc.calls) == len(tags)          # never twice


def test_a_reminder_after_a_sleep_is_sent_late_with_a_note(client):
    first = _subscribe(client)
    when = dt.datetime.fromisoformat(first["at"])
    svc = FakeService()
    stats = push.deliver(when + push.LATE_OK + dt.timedelta(minutes=5), sender=svc)
    payload = {p["tag"]: p for _, p in svc.calls}[f"smartplate-{first['session_id']}"]
    assert stats["late"] >= 1
    assert payload["title"] == "Late reminder: " + first["title"]
    assert "25 min late" in payload["body"] and first["body"] in payload["body"]
    assert _outcomes()[first["session_id"]] == "late"


def test_a_reminder_after_the_meal_window_is_recorded_missed_not_sent(client):
    first = _subscribe(client)
    closes = push.meal_closes(first)
    svc = FakeService()
    stats = push.deliver(closes + dt.timedelta(minutes=1), sender=svc)
    assert f"smartplate-{first['session_id']}" not in [p["tag"] for _, p in svc.calls]
    assert stats["missed"] >= 1 and _outcomes()[first["session_id"]] == "missed"
    assert push.deliver(closes + dt.timedelta(minutes=2), sender=svc)["missed"] == 0   # recorded once


def test_reminders_due_before_the_device_subscribed_are_not_its_own(client):
    first = _subscribe(client, created_ts="2100-01-01T00:00:00")
    assert push.due(dt.datetime.fromisoformat(first["at"]) + dt.timedelta(minutes=1)) == []


def test_classify_boundaries():
    r = {"at": "2026-11-02T19:00", "meal": "dinner", "date": "2026-11-02"}
    at = dt.datetime(2026, 11, 2, 19, 0)
    assert push.classify(r, at + push.LATE_OK) == "on_time"
    assert push.classify(r, at + push.LATE_OK + dt.timedelta(seconds=1)) == "late"
    assert push.classify(r, dt.datetime(2026, 11, 2, 21, 30)) == "missed"        # dinner window closed


def test_a_failed_delivery_releases_its_claim_for_the_next_tick(client):
    first = _subscribe(client)
    at = dt.datetime.fromisoformat(first["at"])

    def down(*a, **kw):
        raise OSError("no route")
    assert push.deliver(at, sender=down)["failed"] >= 1
    assert first["session_id"] not in _outcomes()
    assert push.send_due(at + dt.timedelta(minutes=1), sender=FakeService()) >= 1


def test_two_ticks_at_once_send_each_reminder_once(client):
    """The in-process thread and /cron/tick can overlap: the push_sent claim decides."""
    first = _subscribe(client)
    at = dt.datetime.fromisoformat(first["at"]) + dt.timedelta(minutes=1)
    pairs = push.due(at)                               # both ticks saw the same snapshot
    assert pairs
    calls, gate = [], threading.Barrier(2)

    def slow_sender(endpoint, p256dh, auth, payload, **kw):
        calls.append(payload["tag"])
        return 201
    results = []

    def run():
        gate.wait()
        results.append(push.deliver(at, sender=slow_sender, pairs=pairs))
    threads = [threading.Thread(target=run) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert sorted(calls) == sorted(set(calls))          # no tag twice
    assert sum(r["sent"] for r in results) == len(pairs)


def test_tick_skips_the_database_while_nothing_can_be_due(client, monkeypatch):
    first = _subscribe(client)
    when = dt.datetime.fromisoformat(first["at"])
    early = when - dt.timedelta(hours=2)
    r = push.tick(early, sender=FakeService())
    assert r["next_check"] == min(when, early + push.QUIET_MAX).isoformat(timespec="seconds")
    reads = []
    monkeypatch.setattr(push, "due", lambda *a, **kw: reads.append(1) or [])
    assert push.tick(early + dt.timedelta(minutes=5))["skipped"] == "nothing due"
    assert reads == []
    push.invalidate()                                   # a plan changed
    push.tick(early + dt.timedelta(minutes=6))
    assert reads == [1]


def test_a_plan_change_through_the_api_invalidates_the_quiet_window(client):
    push._quiet_until[0] = dt.datetime(2100, 1, 1)
    client.patch("/api/user/1", json={"weekly_budget": 2100})
    assert push._quiet_until[0] is None
