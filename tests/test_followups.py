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


# --------------------------------------------------------------------------- #
# Profile privacy
# --------------------------------------------------------------------------- #
NEW = {"name": "Priya", "diet": "veg", "weekly_budget": 2000, "meals": ["dinner"], "favourites": [6]}


def _private(client):
    r = client.post("/api/profiles", json=dict(NEW))
    assert r.status_code == 201
    v = r.get_json()
    return v["user"]["id"], v["access_key"], v


def test_new_profile_is_private_and_key_is_shown_once(client):
    from smartplate import db
    uid, key, v = _private(client)
    assert v["recovery_code"] == f"{uid}.{key}" and len(key) >= 24
    with db.cursor() as cur:
        stored = cur.execute("SELECT access_hash FROM users WHERE id=?", (uid,)).fetchone()["access_hash"]
    assert stored and key not in stored                               # only the hash is stored
    assert uid not in [u["id"] for u in client.get("/api/users").get_json()]
    assert "access_key" not in client.get(f"/api/user/{uid}/plan", headers={"X-SmartPlate-Key": key}).get_json()


def test_private_profile_endpoints_require_the_key(client):
    uid, key, v = _private(client)
    pid = v["plan"]["id"]
    sid = next(c["session_id"] for d in v["grid"] for c in d["meals"].values())
    good, bad = {"X-SmartPlate-Key": key}, {"X-SmartPlate-Key": "wrong-" + key}
    calls = [("get", f"/api/user/{uid}/plan", None), ("get", f"/api/plan/{pid}", None),
             ("get", f"/api/session/{sid}/options", None), ("get", f"/api/user/{uid}/reminders", None),
             ("get", f"/api/receipts/{uid}", None), ("get", f"/api/restaurants?user_id={uid}", None),
             ("post", f"/api/plan/{pid}/optimize", {}), ("post", "/api/plan", {"user_id": uid}),
             ("patch", f"/api/user/{uid}/setup", {"weekly_budget": 2100})]
    for method, url, body in calls:
        kw = {"json": body} if body is not None else {}
        assert getattr(client, method)(url, **kw).status_code == 401, url
        assert getattr(client, method)(url, headers=bad, **kw).status_code == 401, url
        assert getattr(client, method)(url, headers=good, **kw).status_code in (200, 201), url
    # plain download links carry the key as a query parameter (GET only)
    assert client.get(f"/api/user/{uid}/reminders.ics?key={key}").status_code == 200
    assert client.get(f"/api/receipts/{uid}/export.csv?key={key}").status_code == 200


def test_open_profile_id_cannot_vouch_for_a_private_one(client):
    uid, key, v = _private(client)
    assert client.get(f"/api/user/{uid}/plan?user_id=1").status_code == 401
    assert client.get(f"/api/plan/{v['plan']['id']}?user_id=1").status_code == 401
    assert client.post("/api/plan", json={"user_id": uid}, headers={}).status_code == 401


def test_sample_profiles_stay_open(client):
    assert client.get("/api/user/1/plan").status_code == 200
    assert client.post("/api/plan/1/optimize", json={}).status_code == 200
    assert {1, 2, 3} <= {u["id"] for u in client.get("/api/users").get_json()}
