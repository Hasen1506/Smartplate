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


# --------------------------------------------------------------------------- #
# Swiggy sign-in + read-only discovery, against a strict fake server
# --------------------------------------------------------------------------- #
import base64
import hashlib
import json
import urllib.parse

from smartplate.integrations import swiggy_connect

TOOLS = ["get_addresses", "search_restaurants", "get_restaurant_menu", "search_menu", "update_food_cart",
         "get_food_cart", "flush_food_cart", "fetch_food_coupons", "apply_food_coupon", "place_food_order",
         "get_food_orders", "track_food_order", "get_payment_options", "report_error"]


class FakeSwiggy:
    base = "https://mcp.swiggy.com"

    def __init__(self):
        self.registrations, self.codes, self.calls = 0, {}, []
        self.token, self.session = "tok-abc", "sess-1"

    def __call__(self, method, url, headers, body):
        self.calls.append((method, url, dict(headers), body))
        path = url.split("mcp.swiggy.com", 1)[1].split("?")[0]
        if path == "/.well-known/oauth-authorization-server":
            return 200, {}, json.dumps({"issuer": self.base, "authorization_endpoint": f"{self.base}/auth/authorize",
                                        "token_endpoint": f"{self.base}/auth/token",
                                        "registration_endpoint": f"{self.base}/auth/register",
                                        "code_challenge_methods_supported": ["S256"]}).encode()
        if path == "/auth/register":
            self.registrations += 1
            meta = json.loads(body)
            assert meta["token_endpoint_auth_method"] == "none"
            return 201, {}, json.dumps({"client_id": "client-1", **meta}).encode()
        if path == "/auth/token":
            form = dict(urllib.parse.parse_qsl(body.decode()))
            challenge = self.codes.pop(form["code"], None)
            if not challenge:
                return 400, {}, b'{"error":"invalid_grant"}'
            got = base64.urlsafe_b64encode(hashlib.sha256(form["code_verifier"].encode()).digest()).rstrip(b"=").decode()
            if got != challenge or form["client_id"] != "client-1":
                return 400, {}, b'{"error":"invalid_grant"}'
            return 200, {}, json.dumps({"access_token": self.token, "token_type": "Bearer", "expires_in": 432000}).encode()
        if path == "/food":
            if headers.get("Authorization") != f"Bearer {self.token}":
                return 401, {}, b""
            msg = json.loads(body)
            if msg["method"] == "initialize":
                reply = {"jsonrpc": "2.0", "id": msg["id"], "result": {
                    "protocolVersion": "2025-03-26", "serverInfo": {"name": "swiggy-food", "version": "1.0"},
                    "capabilities": {"tools": {}}}}
                return 200, {"content-type": "text/event-stream", "mcp-session-id": self.session}, \
                    f"event: message\ndata: {json.dumps(reply)}\n\n".encode()
            assert headers.get("Mcp-Session-Id") == self.session
            if msg["method"] == "notifications/initialized":
                return 202, {}, b""
            if msg["method"] == "tools/list":
                page2 = (msg.get("params") or {}).get("cursor") == "p2"
                names = TOOLS[7:] if page2 else TOOLS[:7]
                tools = [{"name": n, "description": f"{n} tool", "inputSchema": {"type": "object"}} for n in names]
                for t in tools:                     # the server's hint beats the name heuristic
                    if t["name"] == "fetch_food_coupons":
                        t["annotations"] = {"readOnlyHint": False}
                result = {"tools": tools} if page2 else {"tools": tools, "nextCursor": "p2"}
                return 200, {"content-type": "application/json"}, json.dumps({"jsonrpc": "2.0", "id": msg["id"],
                                                                              "result": result}).encode()
        return 404, {}, b""

    def approve(self, authorize_url):
        """What Swiggy's sign-in page does: issue a code bound to the PKCE challenge."""
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(authorize_url).query))
        assert q["code_challenge_method"] == "S256" and q["scope"] == "mcp:tools"
        self.codes["code-1"] = q["code_challenge"]
        return q


@pytest.fixture
def swiggy(monkeypatch):
    fake = FakeSwiggy()
    monkeypatch.setattr(swiggy_connect, "_http", fake)
    return fake


def _connect(client, swiggy, uid=1):
    url = client.post(f"/api/user/{uid}/swiggy/connect", json={},
                      headers={"X-Forwarded-Proto": "https", "X-Forwarded-Host": "app.example"}).get_json()["authorize_url"]
    q = swiggy.approve(url)
    assert q["redirect_uri"] == "https://app.example/swiggy/callback"
    return client.get(f"/swiggy/callback?state={q['state']}&code=code-1"), q


def test_swiggy_sign_in_and_discovery_end_to_end(client, swiggy):
    r, q = _connect(client, swiggy)
    assert r.status_code == 302 and r.headers["Location"].endswith("swiggy=connected")
    s = client.get("/api/user/1/swiggy").get_json()
    assert s["connected"] and s["protocol_version"] == "2025-03-26" and s["server"]["name"] == "swiggy-food"
    assert [t["name"] for t in s["tools"]] == TOOLS                                 # both pages
    kinds = {t["name"]: t["kind"] for t in s["tools"]}
    assert kinds["get_addresses"] == "read" and kinds["search_restaurants"] == "read"
    assert kinds["place_food_order"] == "write" and kinds["update_food_cart"] == "write"
    assert kinds["fetch_food_coupons"] == "write"                                   # readOnlyHint=False wins
    assert swiggy.token not in json.dumps(s)                                        # token never exposed
    called = [json.loads(b)["method"] for m, u, h, b in swiggy.calls if u.endswith("/food")]
    assert called == ["initialize", "notifications/initialized", "tools/list", "tools/list"]  # nothing else


def test_swiggy_state_is_single_use_and_checked(client, swiggy):
    r, q = _connect(client, swiggy)
    replay = client.get(f"/swiggy/callback?state={q['state']}&code=code-1")
    assert "swiggy_error" in replay.headers["Location"]
    forged = client.get("/swiggy/callback?state=forged&code=code-1")
    assert "swiggy_error" in forged.headers["Location"]
    denied = client.get("/swiggy/callback?error=access_denied&error_description=User+cancelled")
    assert "User%20cancelled" in denied.headers["Location"]


def test_swiggy_registration_reused_and_pkce_enforced(client, swiggy):
    _connect(client, swiggy)
    url = client.post("/api/user/1/swiggy/connect", json={},
                      headers={"X-Forwarded-Proto": "https", "X-Forwarded-Host": "app.example"}).get_json()["authorize_url"]
    assert swiggy.registrations == 1                                                 # one client per redirect URI
    q = swiggy.approve(url)
    swiggy.codes["code-1"] = "not-the-challenge"                                    # tampered verifier/challenge
    bad = client.get(f"/swiggy/callback?state={q['state']}&code=code-1")
    assert "swiggy_error" in bad.headers["Location"]


def test_swiggy_requires_https_and_handles_expiry_and_disconnect(client, swiggy, monkeypatch):
    r = client.post("/api/user/1/swiggy/connect", json={}, headers={"X-Forwarded-Host": "evil.example"})
    assert r.status_code == 502 and "HTTPS" in r.get_json()["error"]
    _connect(client, swiggy)
    swiggy.token = "rotated"                                                        # server no longer accepts ours
    r = client.post("/api/user/1/swiggy/discover", json={})
    assert r.status_code == 502 and "expired" in r.get_json()["error"]
    from smartplate import clock
    later = clock.now() + dt.timedelta(days=6)
    monkeypatch.setattr(clock, "now", lambda: later)
    assert client.get("/api/user/1/swiggy").get_json()["expired"] is True
    assert client.post("/api/user/1/swiggy/disconnect", json={}).get_json() == {"connected": False}
    assert client.get("/api/user/1/swiggy").get_json() == {"connected": False}


def test_swiggy_connection_is_private_to_the_profile(client, swiggy):
    uid, key, _ = _private(client)
    assert client.get(f"/api/user/{uid}/swiggy").status_code == 401
    assert client.post(f"/api/user/{uid}/swiggy/connect", json={}).status_code == 401
    assert client.get(f"/api/user/{uid}/swiggy", headers={"X-SmartPlate-Key": key}).get_json() == {"connected": False}
