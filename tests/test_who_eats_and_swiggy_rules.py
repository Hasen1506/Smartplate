"""Who's eating sets the portions (cost, Swiggy cart quantity, safety checks), and every
Swiggy problem is explained by the rule it came from and kept on the profile. Against a
fake Swiggy MCP server; nothing real is touched."""
import pytest

from smartplate import config, db
from smartplate.app import create_app
from smartplate.integrations import swiggy_connect, swiggy_live, swiggy_rules
from smartplate.integrations.swiggy_connect import SwiggyError
from conftest import solo
from test_followups import _connect
from test_swiggy_live import FakeLive, _next_delivery


@pytest.fixture
def client(seeded):
    app = create_app()
    app.config["TESTING"] = True
    return app.test_client()                       # Arjun (3) still shares Flat 3B with Meera (2)


@pytest.fixture
def swiggy(monkeypatch):
    fake = FakeLive()
    monkeypatch.setattr(swiggy_connect, "_http", fake)
    return fake


def _arjun(client, swiggy):
    _connect(client, swiggy, uid=3)
    assert client.post("/api/user/3/swiggy/address", json={"address_id": "addr-home"}).status_code == 200


def _meera_has_no_rules():
    with db.cursor() as cur:
        cur.execute("UPDATE users SET diet='nonveg', allergens='[]', medical='[]' WHERE id=2")


# --------------------------------------------------------------------------- #
# The rules
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("message,rule", [
    ("Swiggy sign-in expired. Connect Swiggy again.", "sign_in"),
    ("Your Swiggy cart already has items from another restaurant.", "one_cart"),
    ("The live Swiggy item or your plan changed. Review it again before adding to cart.", "you_review"),
    ("Ziggy cannot verify your household's ingredient or medical rules from a menu.", "ingredients"),
    ("Swiggy did not return a valid payable total within its ₹1,000 Builders Club limit.", "cart_cap"),
    ("Cash on Delivery is not offered for this cart.", "payment"),
    ("Real order placement is disabled until Swiggy access and durable storage are approved.", "approval"),
    ("An earlier order attempt is unresolved. Check your Swiggy orders before retrying.", "no_blind_retry"),
    ("Swiggy has no exact live match for Masala Dosa.", "exact_dish"),
    ("Choose a Swiggy delivery address first.", "address"),
    ("This dish has add-ons. Choose it in Swiggy.", "choices"),
    ("The dish is no longer in stock. Refresh your cart.", "open_in_stock"),
    ("Swiggy returned HTTP 503", "swiggy_down"),
])
def test_each_problem_maps_to_one_plain_rule(message, rule):
    assert swiggy_rules.classify(SwiggyError(message)) == rule
    explained = swiggy_rules.explain(SwiggyError(message))
    assert explained["id"] == rule and explained["plain"] and explained["fix"]
    assert explained["whose"] in ("swiggy", "ziggy")


def test_error_codes_win_over_wording():
    assert swiggy_rules.classify(SwiggyError("anything", code="swiggy_rate_limited")) == "rate_limit"
    assert swiggy_rules.classify(SwiggyError("an address", code="swiggy_auth_expired")) == "sign_in"


def test_every_swiggy_error_carries_its_rule_and_is_kept_on_the_profile(client, swiggy):
    solo(3)
    _connect(client, swiggy, uid=3)
    cell = _next_delivery(client, uid=3)
    r = client.get(f"/api/session/{cell['session_id']}/swiggy-cart/preview")
    assert r.status_code == 502
    body = r.get_json()
    assert body["rule"]["id"] == "address" and body["rule"]["fix"]
    seen = client.get("/api/user/3/swiggy/rules").get_json()
    assert {x["id"] for x in seen["rules"]} == set(swiggy_rules.BY_ID)
    assert next(x for x in seen["rules"] if x["id"] == "address")["hits_7d"] == 1
    assert seen["issues"][0]["rule"] == "address" and "address" in seen["issues"][0]["message"]
    # Disconnecting forgets the problems too
    assert client.post("/api/user/3/swiggy/disconnect", json={}).status_code == 200
    assert client.get("/api/user/3/swiggy/rules").get_json()["issues"] == []


def test_not_connected_explains_the_sign_in_rule(client):
    solo(3)
    cell = _next_delivery(client, uid=3)
    r = client.get(f"/api/session/{cell['session_id']}/swiggy-cart/preview")
    assert r.status_code == 409 and r.get_json()["rule"]["id"] == "sign_in"


def test_problem_log_keeps_only_the_newest(seeded, monkeypatch):
    import datetime as dt
    from smartplate import clock
    start = dt.datetime(2026, 10, 1, 12, 0)
    for i in range(swiggy_rules.KEEP + 5):
        monkeypatch.setattr(clock, "now", lambda i=i: start + dt.timedelta(minutes=i))
        swiggy_rules.note(3, SwiggyError(f"Swiggy returned HTTP 50{i % 10}"))
    with db.cursor() as cur:
        n = cur.execute("SELECT COUNT(*) AS n FROM swiggy_issues WHERE user_id=3").fetchone()["n"]
    assert n == swiggy_rules.KEEP
    issues = swiggy_rules.overview(3)["issues"]
    assert len(issues) == 10 and issues[0]["at"] == "2026-10-01T12:24:00"
    swiggy_rules.note(999, SwiggyError("no such profile"))         # best effort: never raises
    swiggy_rules.note(None, SwiggyError("signed out"))


# --------------------------------------------------------------------------- #
# Who's eating
# --------------------------------------------------------------------------- #
def test_a_household_member_with_rules_stops_the_cart_until_they_are_not_eating(client, swiggy):
    _arjun(client, swiggy)
    cell = _next_delivery(client, uid=3)
    sid = cell["session_id"]
    assert set(cell["eaters"]) == {2, 3} and cell["portions"] == 2
    swiggy.dishes = {cell["item"]: 21000}
    blocked = client.get(f"/api/session/{sid}/swiggy-cart/preview")
    assert blocked.status_code == 502
    assert "household's" in blocked.get_json()["error"] and blocked.get_json()["rule"]["id"] == "ingredients"
    assert "search_menu" not in swiggy.tool_calls()
    # Only Arjun eats this one: one portion, and the cart is his to fill
    view = client.post(f"/api/session/{sid}/eaters", json={"eaters": [3]}).get_json()
    cell = next(c for d in view["grid"] for c in d["meals"].values() if c.get("session_id") == sid)
    assert cell["eaters"] == [3] and cell["portions"] == 1
    swiggy.dishes = {cell["item"]: 21000}
    preview = client.get(f"/api/session/{sid}/swiggy-cart/preview")
    assert preview.status_code == 200, preview.get_json()
    assert preview.get_json()["quantity"] == 1


def test_two_eaters_put_two_portions_in_the_swiggy_cart(client, swiggy, monkeypatch):
    _meera_has_no_rules()
    _arjun(client, swiggy)
    cell = _next_delivery(client, uid=3)
    sid = cell["session_id"]
    assert cell["portions"] == 2
    swiggy.dishes = {cell["item"]: 21000}
    preview = client.get(f"/api/session/{sid}/swiggy-cart/preview").get_json()
    assert preview["quantity"] == 2
    r = client.post(f"/api/session/{sid}/swiggy-cart", json={"expected_fingerprint": preview["fingerprint"]})
    assert r.status_code == 200, r.get_json()
    cart = r.get_json()
    assert swiggy.qty == 2 and cart["quantity"] == 2
    assert cart["to_pay"] == 455.0                              # 2 × ₹210 + ₹35 delivery, Swiggy's own total
    assert "place_food_order" not in swiggy.tool_calls()
    # A preview for one portion can't fill a two-portion cart: the head count is in the fingerprint
    client.post(f"/api/session/{sid}/eaters", json={"eaters": [3]})
    again = client.post(f"/api/session/{sid}/swiggy-cart", json={"expected_fingerprint": preview["fingerprint"]})
    assert again.status_code == 409

    # Placing a two-portion order re-checks everyone in the household
    monkeypatch.setattr(config, "LIVE_ORDERS", True)
    review = client.get("/api/user/3/swiggy/checkout/preview")
    assert review.status_code == 200, review.get_json()
    assert review.get_json()["quantity"] == 2 and review.get_json()["to_pay"] == 455.0
    with db.cursor() as cur:
        cur.execute("UPDATE users SET allergens='[\"dairy\"]' WHERE id=2")
    stopped = client.get("/api/user/3/swiggy/checkout/preview")
    assert stopped.status_code == 502 and stopped.get_json()["rule"]["id"] == "ingredients"


def test_changing_who_eats_releases_a_checked_cart_and_reprices(client, swiggy):
    _meera_has_no_rules()
    _arjun(client, swiggy)
    cell = _next_delivery(client, uid=3)
    sid = cell["session_id"]
    two = cell["cost"]
    view = client.post(f"/api/session/{sid}/eaters", json={"eaters": [3]}).get_json()
    one = next(c for d in view["grid"] for c in d["meals"].values() if c.get("session_id") == sid)
    if one.get("item") == cell["item"]:
        assert one["cost"] < two                                 # same dish, one portion cheaper
    assert client.post(f"/api/session/{sid}/eaters", json={"eaters": []}).status_code == 400


def test_bill_total_is_read_against_every_portion():
    anchor = swiggy_live._anchor({"menu_price": 210, "quantity": 6})
    assert anchor == 1260
    cart = {"pricing": {"to_pay": 1295}}
    assert swiggy_live.cart_total(cart, anchor) == 1295
    assert swiggy_live.cart_total(cart, 210) is None             # one dish's price can't explain six
    assert swiggy_live._anchor({"menu_price": None, "quantity": 2}) is None
