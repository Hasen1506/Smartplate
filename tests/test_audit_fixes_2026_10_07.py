"""Regression tests for the 2026-10-07 audit (REPORT.md H-*/M-*/L-*) and the live QA findings.
Every Swiggy interaction here uses the in-repo fake MCP server; nothing reaches Swiggy."""
import datetime as dt
import json
import os
import re
import threading
import time

import pytest
from test_followups import APP, _allow_app_host, _connect, _secure_profile
from test_swiggy_live import FakeLive, _prepared_checkout

from smartplate import clock, config, db, ratelimit
from smartplate.app import create_app
from smartplate.domain import allergens, household, profile, reverse_mode
from smartplate.integrations import calendar_sync, swiggy_connect, swiggy_live
from smartplate.kernel import optimizer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NEW = {"name": "A", "weekly_budget": 2000, "cook": "often", "diet": "nonveg"}


@pytest.fixture
def client(seeded):
    app = create_app()
    app.config["TESTING"] = True
    return app.test_client()


@pytest.fixture
def swiggy(monkeypatch):
    fake = FakeLive()
    monkeypatch.setattr(swiggy_connect, "_http", fake)
    return fake


def _profile(client, **extra):
    r = client.post("/api/profiles", json={**NEW, **extra})
    assert r.status_code == 201, r.get_json()
    v = r.get_json()
    return v["user"]["id"], {"X-SmartPlate-Key": v["access_key"]}, v


def _cells(view):
    return [m for d in view["grid"] for m in d["meals"].values()]


# --------------------------------------------------------------------------- H-01
def test_h01_callback_from_another_browser_is_refused(client, swiggy):
    _allow_app_host()
    _secure_profile(client, 1)
    url = client.post("/api/user/1/swiggy/connect", json={}, base_url=APP).get_json()["authorize_url"]
    q = swiggy.approve(url)
    victim = client.application.test_client()                      # no cookie from the attacker's start
    r = victim.get(f"/swiggy/callback?state={q['state']}&code=code-1", base_url=APP)
    assert r.status_code == 302 and r.headers["Location"].endswith("swiggy_error=other_browser")
    assert client.get("/api/user/1/swiggy").get_json()["connected"] is False
    # the state was burned: even the attacker's own browser can't use it afterwards
    again = client.get(f"/swiggy/callback?state={q['state']}&code=code-1", base_url=APP)
    assert "swiggy_error" in again.headers["Location"]


def test_h01_cookie_is_httponly_lax_and_cleared(client, swiggy):
    _allow_app_host()
    _secure_profile(client, 1)
    r = client.post("/api/user/1/swiggy/connect", json={}, base_url=APP)
    cookie = r.headers["Set-Cookie"]
    assert "HttpOnly" in cookie and "SameSite=Lax" in cookie and "Secure" in cookie
    q = swiggy.approve(r.get_json()["authorize_url"])
    done = client.get(f"/swiggy/callback?state={q['state']}&code=code-1", base_url=APP)
    assert done.headers["Location"].endswith("swiggy=connected")
    assert "sp_swiggy_oauth=;" in done.headers["Set-Cookie"]           # cleared after use


# --------------------------------------------------------------------------- H-02
@pytest.mark.parametrize("allergen", profile.ALLERGENS)
def test_h02_every_recipe_respects_every_allergen(allergen):
    user = {"diet": "nonveg", "allergens": [allergen], "medical": []}
    for meal in ("breakfast", "lunch", "dinner"):
        for r in reverse_mode.safe_recipes(user, meal):
            assert allergen not in r["allergens"]
        c = reverse_mode.cook_candidate(user, meal)
        assert c is None or allergen not in c["allergens"]


def test_h02_recipes_carry_allergen_data_and_celiac_rule():
    assert all("allergens" in r and "tags" in r for r in reverse_mode.RECIPES)
    celiac = {"diet": "nonveg", "allergens": [], "medical": ["celiac"]}
    assert reverse_mode.unsafe_reason(celiac, reverse_mode.recipe("egg_curry"))
    assert reverse_mode.unsafe_reason({"diet": "vegan", "allergens": [], "medical": []}, reverse_mode.recipe("egg_curry"))


def test_h02_options_choose_and_plan_never_offer_unsafe_cooking(client):
    uid, h, v = _profile(client, allergens=["egg", "gluten"], medical=["celiac"])
    cell = next(m for m in _cells(v) if m["status"] == "active" and m.get("session_id"))
    opts = client.get(f"/api/session/{cell['session_id']}/options", headers=h).get_json()
    assert "egg_curry" not in [c["recipe_key"] for c in opts["cook"]]
    r = client.post(f"/api/session/{cell['session_id']}/choose", json={"recipe_key": "egg_curry"}, headers=h)
    assert r.status_code == 400 and "Not safe" in r.get_json()["error"]
    view = client.get(f"/api/user/{uid}/plan", headers=h).get_json()
    assert not [m for m in _cells(view) if "Egg curry" in m["item"] or "oats" in m["item"].lower()]


def test_h02_pinned_recipe_is_dropped_when_profile_becomes_allergic(client):
    uid, h, v = _profile(client)
    cell = None
    for m in _cells(v):
        if m["status"] != "active" or not m.get("session_id"):
            continue
        opts = client.get(f"/api/session/{m['session_id']}/options", headers=h).get_json()
        if "egg_curry" in [c["recipe_key"] for c in opts["cook"]]:
            cell = m
            break
    assert cell, "a lunch or dinner session offers egg curry to a non-allergic profile"
    assert client.post(f"/api/session/{cell['session_id']}/choose", json={"recipe_key": "egg_curry"},
                       headers=h).status_code == 200
    view = client.patch(f"/api/user/{uid}/setup", json={"allergens": ["egg"]}, headers=h).get_json()
    picked = next(m for m in _cells(view) if m["session_id"] == cell["session_id"])
    assert "Egg curry" not in picked["item"]


# --------------------------------------------------------------------------- H-03
def _late_week(client, monkeypatch):
    monday = clock.today() - dt.timedelta(days=clock.today().weekday())
    monkeypatch.setattr(optimizer, "now", lambda: dt.datetime.combine(monday, dt.time(7)))
    client.get("/api/user/3/plan")
    monkeypatch.setattr(optimizer, "now", lambda: dt.datetime.combine(monday + dt.timedelta(days=5), dt.time(22)))
    return client.get("/api/user/3/plan").get_json()


def test_h03_review_and_execute_skip_past_meals(client, monkeypatch):
    v = _late_week(client, monkeypatch)
    pid = v["plan"]["id"]
    past = {m["session_id"] for m in _cells(v) if m["status"] == "past"}
    assert past
    prev = client.get(f"/api/plan/{pid}/execute/preview").get_json()
    assert not past & {i["session_id"] for i in prev["items"]}
    ex = client.post(f"/api/plan/{pid}/execute", json={"expected_fingerprint": prev["fingerprint"],
                                                      "max_total": prev["total"]}).get_json()
    assert not past & {r["session_id"] for r in ex["results"]}
    rec = client.get("/api/receipts/3").get_json()
    assert rec["total"] <= prev["total"] + 0.01


# --------------------------------------------------------------------------- M-01
def test_m01_failure_before_order_is_sent_does_not_lock_checkout(client, swiggy, monkeypatch):
    review = _prepared_checkout(client, swiggy, monkeypatch)
    real = swiggy_live._checkout_state

    def then_drop_session(uid):
        out = real(uid)
        swiggy_live.forget_session(uid)
        monkeypatch.setattr(swiggy_connect, "_http", flaky)
        return out
    original = swiggy

    def flaky(method, url, headers, body):
        if url.endswith("/food") and json.loads(body).get("method") == "initialize":
            return 503, {}, b""
        return original(method, url, headers, body)
    monkeypatch.setattr(swiggy_live, "_checkout_state", then_drop_session)
    r = client.post("/api/user/3/swiggy/checkout", json={"expected_fingerprint": review["fingerprint"]})
    assert r.status_code == 502 and "place_food_order" not in swiggy.tool_calls()
    monkeypatch.setattr(swiggy_live, "_checkout_state", real)
    monkeypatch.setattr(swiggy_connect, "_http", swiggy)
    assert client.get("/api/user/3/swiggy/checkout/preview").status_code == 200
    states = [a["state"] for a in client.get("/api/user/3/swiggy/order-history").get_json()["attempts"]]
    assert states == ["failed"]


def test_m01_rate_limited_order_call_is_not_sent_and_not_blocking(client, swiggy, monkeypatch):
    review = _prepared_checkout(client, swiggy, monkeypatch)
    original = swiggy

    def limited(method, url, headers, body):
        msg = json.loads(body) if body else {}
        if url.endswith("/food") and msg.get("method") == "tools/call" and msg["params"]["name"] == "place_food_order":
            return 429, {"retry-after": "30"}, b""
        return original(method, url, headers, body)
    monkeypatch.setattr(swiggy_connect, "_http", limited)
    assert client.post("/api/user/3/swiggy/checkout", json={"expected_fingerprint": review["fingerprint"]}).status_code == 429
    monkeypatch.setattr(swiggy_connect, "_http", swiggy)
    assert client.get("/api/user/3/swiggy/checkout/preview").status_code == 200


def test_m01_uncertain_attempt_can_be_resolved_after_cooldown(client, swiggy, monkeypatch):
    review = _prepared_checkout(client, swiggy, monkeypatch)
    swiggy.order_uncertain = True
    assert client.post("/api/user/3/swiggy/checkout", json={"expected_fingerprint": review["fingerprint"]}).status_code == 502
    assert client.get("/api/user/3/swiggy/checkout/preview").status_code == 502
    assert client.post("/api/user/3/swiggy/attempts/resolve", json={"confirmation": "yes"}).status_code == 400
    early = client.post("/api/user/3/swiggy/attempts/resolve", json={"confirmation": "NO_ORDER_IN_SWIGGY"})
    assert early.status_code == 400 and "minute" in early.get_json()["error"]
    later = clock.now() + dt.timedelta(minutes=11)
    monkeypatch.setattr(clock, "now", lambda: later)
    done = client.post("/api/user/3/swiggy/attempts/resolve", json={"confirmation": "NO_ORDER_IN_SWIGGY"}).get_json()
    assert done["resolved"] == 1
    assert client.get("/api/user/3/swiggy/checkout/preview").status_code == 200


def test_m01_uncertain_attempt_confirmed_from_recent_order_history(client, swiggy, monkeypatch):
    review = _prepared_checkout(client, swiggy, monkeypatch)
    swiggy.order_uncertain = True
    client.post("/api/user/3/swiggy/checkout", json={"expected_fingerprint": review["fingerprint"]})
    original = swiggy.tool

    def history(name, args):
        if name == "get_food_orders":
            return {"structuredContent": {"success": True, "data": {"orders": [
                {"orderId": "old-1", "restaurantName": "X", "orderTotal": "160", "orderStatus": "DELIVERED",
                 "orderedTime": "2020-01-01T12:00:00"},                               # same total, too old
                {"orderId": "new-9", "restaurantName": "X", "orderTotal": "₹160", "orderStatus": "PLACED",
                 "orderedTime": clock.now().isoformat()}]}}}
        return original(name, args)
    monkeypatch.setattr(swiggy, "tool", history)
    attempts = client.get("/api/user/3/swiggy/order-history").get_json()["attempts"]
    assert attempts[0]["state"] == "confirmed" and attempts[0]["order_id"] == "new-9"


# --------------------------------------------------------------------------- M-02
def test_m02_household_plan_is_safe_for_every_member(client):
    v = client.get("/api/user/3/plan").get_json()
    assert v["household"]["diet"] == "vegan"
    with db.cursor() as cur:
        items = {r["id"]: dict(r) for r in cur.execute("SELECT id, name, veg, allergens FROM menu_items")}
    for m in _cells(v):
        it = items.get(m.get("item_id"))
        if m["kind"] == "delivery" and it and m["status"] == "active":
            assert it["veg"] and "dairy" not in json.loads(it["allergens"]), it["name"]
        if m["kind"] == "cook":
            assert "Egg curry" not in m["item"]


def test_m02_merged_profile_keeps_vegan_strictest():
    members = [{"diet": "vegan", "allergens": ["dairy"], "medical": []}, {"diet": "nonveg", "allergens": [], "medical": []}]
    assert household.merged_profile(members)["diet"] == "vegan"
    user = {"diet": "nonveg", "allergens": [], "medical": [], "household_members": [{"name": "M", **members[0]}]}
    assert allergens.violates(user, {"veg": 1, "allergens": ["dairy"]})


# --------------------------------------------------------------------------- M-03
def _ics(events):
    body = "".join(f"BEGIN:VEVENT\r\n{e}\r\nEND:VEVENT\r\n" for e in events)
    return f"BEGIN:VCALENDAR\r\nVERSION:2.0\r\n{body}END:VCALENDAR\r\n"


def _rows(uid=3):
    with db.cursor() as cur:
        return [dict(r) for r in cur.execute("SELECT day, start_min, end_min, kind, title FROM calendar_events "
                                             "WHERE user_id=? ORDER BY day, start_min", (uid,))]


def test_m03_calendar_times_allday_multiday_repeat_and_reimport(seeded):
    ws = "2026-10-05"                                     # a Monday
    d = lambda n, t="": (dt.date(2026, 10, 5) + dt.timedelta(days=n)).strftime("%Y%m%d") + t
    ics = _ics([
        f"UID:a\r\nDTSTART:{d(1, 'T073000Z')}\r\nDTEND:{d(1, 'T083000Z')}\r\nSUMMARY:Review",
        f"UID:b\r\nDTSTART;VALUE=DATE:{d(2)}\r\nDTEND;VALUE=DATE:{d(3)}\r\nSUMMARY:Mom's birthday",
        f"UID:c\r\nDTSTART;VALUE=DATE:{d(4)}\r\nDTEND;VALUE=DATE:{d(7)}\r\nSUMMARY:Trip to Goa",
        f"UID:d\r\nDTSTART:{d(0, 'T090000')}\r\nDTEND:{d(0, 'T093000')}\r\nRRULE:FREQ=DAILY;COUNT=3\r\nSUMMARY:Standup",
        f"UID:e\r\nDTSTART:{d(5, 'T220000')}\r\nDTEND:{d(6, 'T020000')}\r\nSUMMARY:Night shift",
    ])
    calendar_sync.ingest_ics(3, ics, ws)
    calendar_sync.ingest_ics(3, ics, ws)                  # re-import replaces, never duplicates
    rows = _rows()
    review = next(r for r in rows if r["title"] == "Review")
    assert (review["day"], review["start_min"], review["end_min"]) == (1, 13 * 60, 14 * 60)   # IST
    assert next(r for r in rows if "birthday" in r["title"])["kind"] == "allday"
    assert sorted(r["day"] for r in rows if "Goa" in r["title"]) == [4, 5, 6]
    assert {r["kind"] for r in rows if "Goa" in r["title"]} == {"travel"}
    assert sorted(r["day"] for r in rows if r["title"] == "Standup") == [0, 1, 2]
    shift = [(r["day"], r["start_min"], r["end_min"]) for r in rows if r["title"] == "Night shift"]
    assert shift == [(5, 22 * 60, 23 * 60 + 59), (6, 0, 120)]
    assert all(r["end_min"] >= r["start_min"] for r in rows)
    assert calendar_sync.day_is_travel(calendar_sync.events_for(3), 2) is None    # a birthday isn't travel


# --------------------------------------------------------------------------- M-04
def test_m04_forwarded_host_is_ignored_and_registrations_capped(client, swiggy, monkeypatch):
    monkeypatch.setattr(config, "PUBLIC_URL", "")
    monkeypatch.setattr(config, "ALLOWED_HOSTS", ())
    _secure_profile(client, 1)
    for host in ("evil.example", "evil2.example"):
        url = client.post("/api/user/1/swiggy/connect", json={},
                          headers={"X-Forwarded-Proto": "https", "X-Forwarded-Host": host}).get_json()["authorize_url"]
        assert swiggy.approve(url)["redirect_uri"] == "http://localhost/swiggy/callback"
    assert swiggy.registrations == 1
    r = client.post("/api/user/1/swiggy/connect", json={}, base_url="https://evil.example")   # Host header
    assert r.status_code == 502 and swiggy.registrations == 1
    assert not swiggy_connect.redirect_allowed("https://evil.example/swiggy/callback")
    monkeypatch.setattr(config, "PUBLIC_URL", "https://smartplate.example")
    assert swiggy_connect.redirect_allowed("https://smartplate.example/swiggy/callback")
    assert not swiggy_connect.redirect_allowed("https://smartplate.example/other")


# --------------------------------------------------------------------------- M-05
def test_m05_one_profiles_slow_request_does_not_block_others(client):
    from smartplate.runtime import user_lock
    lock = user_lock(3)
    lock.acquire()
    try:
        out = {}

        def other():
            t0 = time.monotonic()
            out["health"] = client.application.test_client().get("/api/health").status_code
            out["plan"] = client.application.test_client().get("/api/user/2/plan").status_code
            out["s"] = time.monotonic() - t0
        t = threading.Thread(target=other)
        t.start()
        t.join(20)
        assert out.get("health") == 200 and out.get("plan") == 200
    finally:
        lock.release()


# --------------------------------------------------------------------------- M-06
def test_m06_sessions_are_reused_and_swiggy_routes_are_throttled(client, swiggy, monkeypatch):
    review = _prepared_checkout(client, swiggy, monkeypatch)
    n = len(swiggy.calls)
    client.get("/api/user/3/swiggy/checkout/preview")
    calls = [json.loads(b).get("method") for m, u, h, b in swiggy.calls[n:] if u.endswith("/food") and b]
    assert "initialize" not in calls and len(calls) <= 3
    assert review["to_pay"] == 160
    ratelimit.reset()
    for _ in range(120):
        ratelimit.check("swiggy:3", 120, 600)
    assert client.get("/api/user/3/swiggy/live-cart").status_code == 429


# --------------------------------------------------------------------------- M-07
def test_m07_patched_cryptography_and_no_test_tools_in_production():
    with open(os.path.join(ROOT, "requirements.txt")) as f:
        reqs = f.read()
    version = re.search(r"cryptography==([\d.]+)", reqs).group(1)
    assert tuple(int(x) for x in version.split(".")) >= (50, 0, 0)
    assert "pytest" not in reqs
    with open(os.path.join(ROOT, "requirements-dev.txt")) as f:
        assert "pytest==9" in f.read()


# --------------------------------------------------------------------------- L-01 / L-02
def test_l01_receipt_removed_when_confirmed_meal_is_undone(client):
    v = client.get("/api/user/3/plan").get_json()
    cell = next(m for m in _cells(v) if m["kind"] == "delivery" and m["status"] == "active")
    client.post(f"/api/session/{cell['session_id']}/confirm", json={})
    assert client.get("/api/receipts/3").get_json()["total"] > 0
    client.post(f"/api/session/{cell['session_id']}/status", json={"status": "skipped"})
    assert client.get("/api/receipts/3").get_json()["total"] == 0


@pytest.mark.parametrize("body", [{"date": "not-a-date"}, {"date": "2001-01-01"}, {"meal": "brunch!"},
                                  {"source": "hacked"}, {"date": 20261007}])
def test_l02_intake_rejects_bad_fields(client, body):
    r = client.post("/api/user/3/intake", json={"text": "2 roti and dal", **body})
    assert r.status_code == 400
    assert client.get("/api/user/3/ledger").get_json()["days_logged"] == 0


def test_l02_valid_intake_still_logs(client):
    r = client.post("/api/user/3/intake", json={"text": "2 roti and dal", "date": clock.today().isoformat(),
                                                "meal": "Lunch"})
    assert r.status_code == 200 and client.get("/api/user/3/ledger").get_json()["days_logged"] == 1


# --------------------------------------------------------------------------- L-05 + live QA prices
def test_l05_cart_total_units_are_never_guessed_for_checkout():
    assert swiggy_live.cart_total({"pricing": {"to_pay": 174}}) == 174
    assert swiggy_live.cart_total({"pricing": {"to_pay": 17400}}) is None             # ambiguous without anchor
    assert swiggy_live.cart_total({"pricing": {"to_pay": 17400}}, anchor=149) == 174  # paise, anchored
    assert swiggy_live.cart_total({"pricing": {"to_pay": 174}}, anchor=149) == 174    # rupees, anchored
    assert swiggy_live.cart_total({"pricing": {"toPayInPaise": 17400}}) == 174
    assert swiggy_live.cart_total({"pricing": {"to_pay": 1001}}, anchor=125) is None
    assert swiggy_live.cart_total({"pricing": {"to_pay": 174.5}}) == 174.5


def test_live_menu_shows_prices_in_either_unit(client, swiggy, monkeypatch):
    _connect(client, swiggy, uid=3)
    client.post("/api/user/3/swiggy/address", json={"address_id": "addr-home"})
    for prices, expected in (({"Ghee Pongal": 156, "Dosai": 95}, [156, 95]),
                             ({"Ghee Pongal": 15600, "Dosai": 9500}, [156, 95])):
        original = FakeLive.tool

        def bare(self, name, args, prices=prices, original=original):
            if name == "get_restaurant_menu":                 # documented shape: data.items[].price (no unit)
                return {"structuredContent": {"success": True, "data": {
                    "restaurant": {"id": "r-1", "name": "Hotel Saravana Bhavan (Adyar)", "isOpen": True},
                    "items": [{"id": f"m{i}", "name": n, "price": p, "isVeg": True}
                              for i, (n, p) in enumerate(prices.items())]}}}
            return original(self, name, args)
        monkeypatch.setattr(FakeLive, "tool", bare)
        menu = client.get("/api/user/3/swiggy/live-menu?restaurant_id=r-1&restaurant_name=Hotel+Saravana+Bhavan+(Adyar)").get_json()
        assert [i["price"] for i in menu["items"]] == expected
        assert all(i["price_estimated"] for i in menu["items"])


def test_live_review_for_allergy_profile_is_a_review_not_a_dead_end(client, swiggy):
    _connect(client, swiggy, uid=1)                       # profile 1 has allergy / medical rules
    client.post("/api/user/1/swiggy/address", json={"address_id": "addr-home"})
    swiggy.dishes = {"Ghee Pongal": 15600}
    body = {"restaurant_id": "r-1", "restaurant_name": "Hotel Saravana Bhavan (Adyar)",
            "item_id": "m0", "item_name": "Ghee Pongal"}
    r = client.post("/api/user/1/swiggy/live-cart/preview", json=body)
    assert r.status_code == 200
    review = r.get_json()
    assert review["orderable"] is False and review["menu_price"] == 156 and review["fingerprint"] is None
    assert review["handoff_url"].startswith("https://www.swiggy.com/search?") and "ingredient" in review["reason"]
    assert review["address"] and review["item"] == "Ghee Pongal"
    blocked = client.post("/api/user/1/swiggy/live-cart", json={**body, "expected_fingerprint": "x"})
    assert blocked.status_code == 502 and swiggy.cart is None


@pytest.mark.parametrize("reply,items,verified", [
    ({"success": True, "data": {"addressId": "addr-home"}}, 0, True),                         # empty: no inner cart
    ({"success": True, "data": {"addressId": "addr-home", "data": None}}, 0, True),
    ({"addressId": "addr-home", "data": {"items": [{"menu_item_id": "m0", "name": "x", "quantity": 1}]}}, 1, True),
    ({"success": True, "data": {"addressId": "addr-home", "data": {"result": "EMPTY"}}}, 0, True),
    ({"success": True, "data": {}}, 0, False),
])
def test_live_cart_reply_shapes(reply, items, verified):
    cart = swiggy_live._cart_view(reply, "addr-home")
    assert len(cart["items"]) == items and cart["address_verified"] is verified


def test_live_cart_for_another_address_is_refused():
    with pytest.raises(swiggy_connect.SwiggyError, match="different delivery address"):
        swiggy_live._cart_view({"success": True, "data": {"addressId": "addr-work", "data": {"items": []}}}, "addr-home")


def test_live_empty_cart_is_reported_not_unverified(client, swiggy, monkeypatch):
    _connect(client, swiggy, uid=3)
    client.post("/api/user/3/swiggy/address", json={"address_id": "addr-home"})
    original = FakeLive.tool
    monkeypatch.setattr(FakeLive, "tool", lambda self, name, args: {"structuredContent": {
        "success": True, "data": {"addressId": args["addressId"]}}} if name == "get_food_cart" else original(self, name, args))
    r = client.get("/api/user/3/swiggy/live-cart")
    assert r.status_code == 200 and r.get_json()["cart"] is None and r.get_json()["empty"] is True


# --------------------------------------------------------------------------- L-06 / L-07 / L-08
def test_l06_decisions_and_intake_use_the_app_clock(client, monkeypatch):
    fixed = dt.datetime(2026, 10, 7, 9, 30)
    monkeypatch.setattr(clock, "now", lambda: fixed)
    client.post("/api/user/3/intake", json={"text": "dal"})
    with db.cursor() as cur:
        assert cur.execute("SELECT created_ts FROM intake_log ORDER BY id DESC").fetchone()[0].startswith("2026-10-07T09:30")


def test_l07_type_errors_are_500_without_internals(client, monkeypatch):
    from smartplate import service

    def broken(*a, **k):
        raise TypeError("'NoneType' object is not subscriptable at secret_internal_fn")
    monkeypatch.setattr(service, "recommend_budget", broken)
    r = client.get("/api/plan/1/recommend-budget")
    assert r.status_code == 500 and "secret_internal_fn" not in r.get_data(as_text=True)


@pytest.mark.parametrize("token_reply", [b"<html>oops</html>", b'{"access_token": "tok-abc", "expires_in": "soon"}'])
def test_l08_odd_token_replies_redirect_instead_of_json_400(client, swiggy, monkeypatch, token_reply):
    _allow_app_host()
    _secure_profile(client, 1)
    url = client.post("/api/user/1/swiggy/connect", json={}, base_url=APP).get_json()["authorize_url"]
    q = swiggy.approve(url)
    original = swiggy.__call__

    def odd(method, u, headers, body):
        if u.endswith("/auth/token"):
            return 200, {}, token_reply
        return original(method, u, headers, body)
    monkeypatch.setattr(swiggy_connect, "_http", odd)
    r = client.get(f"/swiggy/callback?state={q['state']}&code=code-1", base_url=APP)
    assert r.status_code == 302 and r.headers["Location"].startswith("/?tab=more&swiggy")


def test_l08_discovery_failure_after_token_keeps_the_connection(client, swiggy, monkeypatch):
    _allow_app_host()
    _secure_profile(client, 1)
    url = client.post("/api/user/1/swiggy/connect", json={}, base_url=APP).get_json()["authorize_url"]
    q = swiggy.approve(url)
    original = swiggy.__call__

    def no_tools(method, u, headers, body):
        if u.endswith("/food"):
            return 503, {}, b""
        return original(method, u, headers, body)
    monkeypatch.setattr(swiggy_connect, "_http", no_tools)
    r = client.get(f"/swiggy/callback?state={q['state']}&code=code-1", base_url=APP)
    assert r.headers["Location"].endswith("swiggy=connected")
    assert client.get("/api/user/1/swiggy").get_json()["connected"] is True


# --------------------------------------------------------------------------- L-09 / L-10 / budget QA
def test_l09_sample_profiles_cannot_be_renamed_or_published(client):
    assert client.patch("/api/user/1", json={"name": "Defaced"}).status_code == 400
    assert client.patch("/api/user/1/setup", json={"name": "Defaced"}).status_code == 400
    assert client.post("/api/plan/1/save-template", json={"title": "x"}).status_code == 400
    assert "Defaced" not in json.dumps(client.get("/api/users").get_json())


@pytest.mark.parametrize("budget", [0, 50, 100001, 999999999999])
def test_l10_budget_limits_are_the_same_everywhere(client, budget):
    assert client.patch("/api/user/2", json={"weekly_budget": budget}).status_code == 400
    assert client.patch("/api/user/2/setup", json={"weekly_budget": budget}).status_code == 400
    assert client.post("/api/profiles", json={**NEW, "weekly_budget": budget}).status_code == 400


def test_daily_limit_cannot_exceed_weekly_budget(client):
    r = client.post("/api/profiles", json={**NEW, "weekly_budget": 2000, "daily_cap": 5000})
    assert r.status_code == 400 and "weekly budget" in r.get_json()["error"]
    uid, h, _v = _profile(client, weekly_budget=2000)
    assert client.patch(f"/api/user/{uid}/setup", json={"daily_cap": 5000}, headers=h).status_code == 400
    assert client.patch(f"/api/user/{uid}/setup", json={"daily_cap": 400}, headers=h).status_code == 200


# --------------------------------------------------------------------------- L-11 / L-12
def test_l11_hsts_on_https(client):
    assert "Strict-Transport-Security" in client.get("/healthz", base_url="https://app.example").headers
    assert "Strict-Transport-Security" not in client.get("/healthz").headers


def test_l12_reminder_not_repeated_after_replan_and_backoff_on_429(client, monkeypatch):
    from test_push import FakeService, _browser_subscription

    from smartplate import push
    push._backoff.clear()
    client.post("/api/user/1/push/subscribe", json={"subscription": _browser_subscription()})
    first = client.get("/api/user/1/reminders").get_json()[0]
    at = dt.datetime.fromisoformat(first["at"])
    assert push.send_due(at, sender=FakeService(429)) == 0
    svc = FakeService(201)
    assert push.send_due(at + dt.timedelta(seconds=30), sender=svc) == 0              # still backing off
    assert push.send_due(at + dt.timedelta(minutes=1), sender=svc) >= 1
    with db.cursor() as cur:                                                         # a re-plan moves the time
        cur.execute("UPDATE push_sent SET at='2000-01-01T00:00:00'")
    assert all(s != first["session_id"] for s in
               [r["session_id"] for _, r in push.due(at + dt.timedelta(minutes=2))])


# --------------------------------------------------------------------------- L-13 / L-14
def test_l14_shared_week_is_anonymous_unless_opted_in_and_adopts_deduped(client):
    uid, h, v = _profile(client, name="Priya Secret")
    pid = v["plan"]["id"]
    client.post(f"/api/plan/{pid}/save-template", json={"title": "Week"}, headers=h)
    listed = client.get("/api/community").get_json()
    mine = next(t for t in listed if t["title"] == "Week")
    assert "Priya" not in json.dumps(listed) and mine["author"] == "A SmartPlate user"
    client.post(f"/api/plan/{pid}/save-template", json={"title": "Named", "show_name": True}, headers=h)
    assert any(t["author"] == "Priya Secret" for t in client.get("/api/community").get_json())
    a = client.post(f"/api/community/{mine['id']}/adopt", json={}).get_json()["adopts"]
    b = client.post(f"/api/community/{mine['id']}/adopt", json={}).get_json()["adopts"]
    assert a == b == 1


def test_l13_frontend_maps_codes_to_fixed_text():
    with open(os.path.join(ROOT, "smartplate", "static", "app.js")) as f:
        js = f.read()
    assert "SWIGGY_ERRORS[params.get(\"swiggy_error\")]" in js
    assert "`Swiggy: ${params.get(\"swiggy_error\")}`" not in js
