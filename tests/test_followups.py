"""Follow-ups after the everyday flows: reminders, installability, profile keys,
and the read-only Swiggy connect/discovery client."""
import datetime as dt

import pytest
from icalendar import Calendar

from smartplate.app import create_app
from smartplate.domain import reminders
from smartplate.kernel import optimizer


@pytest.fixture
def client(seeded):
    app = create_app()
    app.config["TESTING"] = True
    return app.test_client()


# --------------------------------------------------------------------------- #
# Reminders
# --------------------------------------------------------------------------- #
def test_reminders_fire_at_order_by_and_before_cooking(client):
    v = client.get("/api/plan/1").get_json()
    rows = client.get("/api/user/1/reminders").get_json()
    assert rows and rows == sorted(rows, key=lambda r: r["at"])
    cells = {c["session_id"]: c for d in v["grid"] for c in d["meals"].values()}
    for r in rows:
        c = cells[r["session_id"]]
        if c["kind"] == "delivery":
            assert r["at"].endswith(c["order"]["order_at"]) and r["link"].startswith("https://www.swiggy.com/")
        else:
            assert c["kind"] == "cook" and r["title"].startswith("Start cooking")


def test_reminders_skip_confirmed_and_past_meals(client, monkeypatch):
    rows = client.get("/api/user/1/reminders").get_json()
    first = rows[0]["session_id"]
    client.post(f"/api/session/{first}/confirm", json={})
    assert first not in [r["session_id"] for r in client.get("/api/user/1/reminders").get_json()]
    later = dt.datetime.fromisoformat(rows[-1]["at"]) + dt.timedelta(minutes=1)
    monkeypatch.setattr(optimizer, "now", lambda: later)
    view = client.get("/api/plan/1").get_json()
    assert reminders.upcoming(view, later) == []


def test_reminder_calendar_is_valid_ics_with_alarms(client):
    r = client.get("/api/user/1/reminders.ics")
    assert r.status_code == 200 and r.mimetype == "text/calendar"
    cal = Calendar.from_ical(r.data)
    events = [c for c in cal.walk("VEVENT")]
    assert events and len(cal.walk("VTIMEZONE")) == 1
    assert all(len(e.walk("VALARM")) == 1 for e in events)
    uids = [str(e["UID"]) for e in events]
    assert len(uids) == len(set(uids))                                  # stable, unique per meal
    again = Calendar.from_ical(client.get("/api/user/1/reminders.ics").data)
    assert [str(e["UID"]) for e in again.walk("VEVENT")] == uids       # re-import replaces, not duplicates


# --------------------------------------------------------------------------- #
# Installable app
# --------------------------------------------------------------------------- #
def test_manifest_and_service_worker_are_served_for_install(client):
    m = client.get("/manifest.webmanifest")
    assert m.status_code == 200 and m.mimetype == "application/manifest+json"
    data = m.get_json(force=True)
    assert data["display"] == "standalone" and data["start_url"].startswith("/")
    sizes = {i["sizes"] for i in data["icons"] if i["type"] == "image/png"}
    assert {"192x192", "512x512"} <= sizes
    for icon in data["icons"]:
        assert client.get(icon["src"]).status_code == 200
    sw = client.get("/sw.js")
    assert sw.status_code == 200 and "javascript" in sw.mimetype and sw.headers["Cache-Control"] == "no-cache"
    assert b'startsWith("/api/")' in sw.data                           # plans/budgets are never cached
    page = client.get("/").data
    assert b'rel="manifest"' in page and b"serviceWorker" in page
