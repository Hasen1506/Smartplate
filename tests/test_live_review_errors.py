"""Live finding, 8 Oct 2026: Review on a real Swiggy dish showed only "⚠ not found", and
every later call (Find dishes, a new restaurant search, Retry) showed the same.

Root cause: the free Render server keeps SQLite on a temporary disk. A redeploy erased it
mid-session, the browser kept the old profile id, and the server answered every
/api/user/<id>/… call with a bare {"error": "not found"} 404. Retry re-ran an unrelated
plan reload that could never succeed.

These tests pin the fix, offline and deterministic (fake MCP server, no real account):
  • a missing profile / plan / meal is a named 404 with a code and a reason, never a
    bare "not found";
  • Review works when Swiggy's browse id and its cart id (menu_item_id) differ;
  • a failed Swiggy call never poisons the calls after it.
"""
import json

import pytest
from test_followups import _connect
from test_swiggy_live import FakeLive, ok  # noqa: F401  (ok documents the reply envelope)

from smartplate import config
from smartplate.app import create_app
from smartplate.integrations import swiggy_connect


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


GONE = 987654                                   # an id this server does not have


@pytest.mark.parametrize("method,path,code", [
    ("post", f"/api/user/{GONE}/swiggy/live-cart/preview", "profile_missing"),
    ("get", f"/api/user/{GONE}/swiggy/dishes?restaurant_id=r-1&restaurant_name=x&query=biryani", "profile_missing"),
    ("get", f"/api/user/{GONE}/swiggy/restaurants?query=Sangeetha", "profile_missing"),
    ("get", f"/api/plan/{GONE}", "plan_missing"),
    ("get", f"/api/session/{GONE}/options", "session_missing"),
])
def test_a_missing_profile_plan_or_meal_is_named_never_a_bare_not_found(client, monkeypatch, method, path, code):
    monkeypatch.setattr(config, "storage_status", lambda: {"engine": "sqlite", "persistent": False})
    r = getattr(client, method)(path, **({"json": {"restaurant_id": "r-1", "restaurant_name": "x",
                                                   "item_id": "m0", "item_name": "Veg Biryani"}}
                                         if method == "post" else {}))
    body = r.get_json()
    assert r.status_code == 404
    assert body["code"] == code
    assert body["error"].strip().lower() != "not found"
    assert "no longer on this server" in body["error"]
    assert "restart or redeploy" in body["error"]          # the honest reason on a temporary disk


def test_on_persistent_storage_the_reason_does_not_blame_a_redeploy(client, monkeypatch):
    monkeypatch.setattr(config, "storage_status", lambda: {"engine": "postgres", "persistent": True})
    body = client.get(f"/api/user/{GONE}/swiggy").get_json()
    assert body["code"] == "profile_missing" and "redeploy" not in body["error"]
    assert "recovery code" in body["error"]


def _menu(client, uid):
    params = "restaurant_id=r-1&restaurant_name=Minjur%20Bhavan"
    r = client.get(f"/api/user/{uid}/swiggy/live-menu?{params}")
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def test_review_works_when_the_browse_id_is_not_the_cart_id(client, swiggy):
    """get_restaurant_menu's compact `id` is not documented to equal search_menu's
    `menu_item_id`, the id update_food_cart takes. Review must still find the dish and
    put the cart id, not the browse id, into the cart."""
    _connect(client, swiggy, uid=3)
    client.post("/api/user/3/swiggy/address", json={"address_id": "addr-home"})
    swiggy.dishes = {"Veg Biryani": 16000, "Mushroom Biryani": 24500}
    swiggy.browse_ids = {0: "118263455", 1: "118263456"}
    menu = _menu(client, 3)
    assert [i["id"] for i in menu["items"]] == ["118263455", "118263456"]
    body = {"restaurant_id": "r-1", "restaurant_name": menu["restaurant"]["name"],
            "item_id": "118263455", "item_name": "Veg Biryani"}
    r = client.post("/api/user/3/swiggy/live-cart/preview", json=body)
    assert r.status_code == 200, r.get_json()
    preview = r.get_json()
    assert preview["orderable"] and preview["item"] == "Veg Biryani" and preview["item_id"] == "m0"
    added = client.post("/api/user/3/swiggy/live-cart", json={**body, "expected_fingerprint": preview["fingerprint"]})
    assert added.status_code == 200, added.get_json()
    assert swiggy.cart == "m0" and "place_food_order" not in swiggy.tool_calls()


def test_review_names_the_dish_when_swiggy_cannot_match_it(client, swiggy):
    _connect(client, swiggy, uid=3)
    client.post("/api/user/3/swiggy/address", json={"address_id": "addr-home"})
    swiggy.dishes = {"Veg Biryani": 16000}
    menu = _menu(client, 3)
    r = client.post("/api/user/3/swiggy/live-cart/preview", json={
        "restaurant_id": "r-1", "restaurant_name": menu["restaurant"]["name"],
        "item_id": "gone-1", "item_name": "Paneer Biryani"})
    body = r.get_json()
    assert r.status_code == 502 and body["code"] == "swiggy_item_unmatched"
    assert "Paneer Biryani" in body["error"] and body["error"].lower() != "not found"


class StaleSessionSwiggy(FakeLive):
    """Swiggy forgot the MCP session (e.g. it restarted): a tools/call with the old
    session id gets HTTP 400 and a JSON-RPC error, as MCP servers answer for an unknown
    session. A fresh initialize works."""

    def __call__(self, method, url, headers, body):
        if url.endswith("/food") and body:
            msg = json.loads(body)
            if msg.get("method") == "tools/call" and headers.get("Mcp-Session-Id") != self.session:
                self.calls.append((method, url, dict(headers), body))
                return 400, {"content-type": "application/json"}, json.dumps({
                    "jsonrpc": "2.0", "id": msg.get("id"),
                    "error": {"code": -32000, "message": "Bad Request: No valid session ID provided"}}).encode()
        return super().__call__(method, url, headers, body)


def test_a_failed_call_never_poisons_the_calls_after_it(client, monkeypatch):
    fake = StaleSessionSwiggy()
    monkeypatch.setattr(swiggy_connect, "_http", fake)
    _connect(client, fake, uid=3)
    assert client.post("/api/user/3/swiggy/address", json={"address_id": "addr-home"}).status_code == 200
    fake.dishes = {"Veg Biryani": 16000}
    assert _menu(client, 3)["items"]
    fake.session = "sess-2"                       # Swiggy dropped the session SmartPlate cached
    r = client.get("/api/user/3/swiggy/restaurants?query=Sangeetha")
    assert r.status_code == 200, r.get_json()     # a read re-opens a fresh session once
    assert client.get("/api/user/3/swiggy/restaurants?query=meals").status_code == 200
    assert _menu(client, 3)["items"]


def test_a_swiggy_error_on_review_leaves_search_and_menus_working(client, swiggy):
    _connect(client, swiggy, uid=3)
    client.post("/api/user/3/swiggy/address", json={"address_id": "addr-home"})
    swiggy.dishes = {"Veg Biryani": 16000}
    menu = _menu(client, 3)
    swiggy.search_error = "Item lookup failed"
    body = {"restaurant_id": "r-1", "restaurant_name": menu["restaurant"]["name"],
            "item_id": "m0", "item_name": "Veg Biryani"}
    r = client.post("/api/user/3/swiggy/live-cart/preview", json=body)
    assert r.status_code == 502 and "Item lookup failed" in r.get_json()["error"]
    swiggy.search_error = None
    assert client.get("/api/user/3/swiggy/restaurants?query=Sangeetha").status_code == 200
    again = client.post("/api/user/3/swiggy/live-cart/preview", json=body)
    assert again.status_code == 200 and again.get_json()["orderable"]
