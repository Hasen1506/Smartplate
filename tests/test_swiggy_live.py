"""Swiggy gates 2–3 against a fake MCP server: addresses, live menus, filling the cart.
The fake checks the documented argument names and that nothing is ever ordered."""
import json

import pytest

from smartplate import db
from smartplate.app import create_app
from smartplate.integrations import swiggy_connect, swiggy_live
from smartplate.integrations.swiggy_connect import SwiggyError
from test_followups import FakeSwiggy, _connect

SCHEMAS = {
    "get_addresses": {"type": "object", "properties": {}},
    "search_restaurants": {"type": "object", "required": ["query", "addressId"],
                           "properties": {"query": {"type": "string"}, "addressId": {"type": "string"}}},
    "get_restaurant_menu": {"type": "object", "required": ["restaurantId", "addressId"],
                            "properties": {"restaurantId": {"type": "string"}, "addressId": {"type": "string"}}},
    "update_food_cart": {"type": "object", "required": ["cartItems", "restaurantId", "addressId"], "properties": {
        "cartItems": {"type": "array", "items": {"type": "object", "required": ["menu_item_id", "quantity"],
                                                 "properties": {"menu_item_id": {"type": "string"},
                                                                "quantity": {"type": "integer"}}}},
        "restaurantId": {"type": "string"}, "addressId": {"type": "string"}, "restaurantName": {"type": "string"}}},
    "get_food_cart": {"type": "object", "properties": {"addressId": {"type": "string"}}},
}


class FakeLive(FakeSwiggy):
    """Adds tools/call. Addresses come back as JSON text, menus as structuredContent,
    the cart nested under bill, like the documented `to_pay` field."""

    def __init__(self):
        super().__init__()
        self.cart, self.dishes = None, {}

    def __call__(self, method, url, headers, body):
        if url.endswith("/food") and headers.get("Authorization") == f"Bearer {self.token}":
            msg = json.loads(body)
            if msg["method"] == "tools/list":
                status, h, raw = super().__call__(method, url, headers, body)
                reply = json.loads(raw)
                for t in reply["result"]["tools"]:
                    t["inputSchema"] = SCHEMAS.get(t["name"], {"type": "object"})
                return status, h, json.dumps(reply).encode()
            if msg["method"] == "tools/call":
                self.calls.append((method, url, dict(headers), body))
                assert headers.get("Mcp-Session-Id") == self.session
                return 200, {"content-type": "application/json"}, json.dumps(
                    {"jsonrpc": "2.0", "id": msg["id"], "result": self.tool(msg["params"]["name"], msg["params"]["arguments"])}).encode()
        return super().__call__(method, url, headers, body)

    def tool(self, name, args):
        for req in SCHEMAS[name].get("required", []):
            assert req in args, (name, req)
        if name == "get_addresses":
            text = json.dumps({"addresses": [{"id": "addr-home", "annotation": "Home", "address": "12 Lake View Rd, Adyar"},
                                             {"id": "addr-work", "annotation": "Work", "address": "OMR, Perungudi"}]})
            return {"content": [{"type": "text", "text": text}]}
        if name == "search_restaurants":
            q = args["query"]
            return {"structuredContent": {"restaurants": [
                {"restaurantId": "r-1", "name": f"{q} (Adyar)", "avgRating": 4.3, "sla": "30 mins"},
                {"restaurantId": "r-2", "name": "Completely Different Kitchen", "avgRating": 4.0}]}}
        if name == "get_restaurant_menu":
            assert args["restaurantId"] == "r-1" and args["addressId"] == "addr-home"
            items = [{"itemId": f"m{i}", "name": n, "price": p, "isVeg": 1} for i, (n, p) in enumerate(self.dishes.items())]
            return {"structuredContent": {"menu": {"categories": [{"title": "Mains", "items": items}]}}}
        if name == "update_food_cart":
            item = args["cartItems"][0]
            assert item["quantity"] == 1 and args["restaurantId"] == "r-1"
            self.cart = item["menu_item_id"]
            return {"content": [{"type": "text", "text": "Cart updated"}]}
        if name == "get_food_cart":
            price = next(p for i, (n, p) in enumerate(self.dishes.items()) if f"m{i}" == self.cart) / 100
            return {"structuredContent": {"cart": {"items": [{"id": self.cart, "total": price}],
                                                   "bill": {"item_total": price, "delivery_fee": 35, "to_pay": price + 35}}}}
        raise AssertionError(f"unexpected tool {name}")

    def tool_calls(self):
        return [json.loads(b)["params"]["name"] for m, u, h, b in self.calls
                if u.endswith("/food") and b and json.loads(b).get("method") == "tools/call"]


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


def _next_delivery(client):
    v = client.get("/api/plan/1").get_json()
    return next(c for d in v["grid"] for c in d["meals"].values()
                if c["kind"] == "delivery" and c["status"] == "active" and not c.get("past"))


def test_choose_address_then_live_menu_in_rupees(client, swiggy):
    _connect(client, swiggy)
    addrs = client.get("/api/user/1/swiggy/addresses").get_json()
    assert [a["label"] for a in addrs] == ["Home", "Work"] and addrs[0]["text"].startswith("12 Lake View")
    s = client.post("/api/user/1/swiggy/address", json={"address_id": "addr-home"}).get_json()
    assert s["address"]["id"] == "addr-home" and s["address"]["label"].startswith("Home")
    swiggy.dishes = {"Veg Meals": 18000, "Mini Tiffin": 12500}
    m = client.get("/api/user/1/swiggy/menu?restaurant=Hotel Saravana Bhavan").get_json()
    assert m["swiggy"]["name"] == "Hotel Saravana Bhavan (Adyar)" and not m["cached"]
    assert {i["name"]: i["price"] for i in m["items"]} == {"Veg Meals": 180.0, "Mini Tiffin": 125.0}   # paise → ₹
    again = client.get("/api/user/1/swiggy/menu?restaurant=Hotel Saravana Bhavan").get_json()
    assert again["cached"] and swiggy.tool_calls().count("get_restaurant_menu") == 1
    samples = db.jl(swiggy_connect._connection(1)["samples"], {})
    assert "12 Lake View" not in json.dumps(samples) and samples["get_addresses"]["reply"]   # shapes, not values


def test_fill_cart_puts_the_planned_dish_in_and_reads_to_pay(client, swiggy):
    _connect(client, swiggy)
    client.post("/api/user/1/swiggy/address", json={"address_id": "addr-home"})
    cell = _next_delivery(client)
    swiggy.dishes = {"Something Else": 9900, cell["item"]: 21000}
    r = client.post(f"/api/session/{cell['session_id']}/swiggy-cart", json={})
    assert r.status_code == 200, r.get_json()
    cart = r.get_json()
    assert cart["item"] == cell["item"] and cart["to_pay"] == 245.0 and cart["menu_price"] == 210.0
    assert cart["over_plan"] == round(245.0 - cell["cost"], 2) and cart["checkout_url"].startswith("https://www.swiggy.com/")
    assert swiggy.cart == "m1"
    assert "place_food_order" not in swiggy.tool_calls() and "get_payment_options" not in swiggy.tool_calls()


def test_missing_dish_and_missing_address_are_explained(client, swiggy):
    _connect(client, swiggy)
    cell = _next_delivery(client)
    r = client.post(f"/api/session/{cell['session_id']}/swiggy-cart", json={})
    assert r.status_code == 502 and "delivery address" in r.get_json()["error"]
    client.post("/api/user/1/swiggy/address", json={"address_id": "addr-home"})
    swiggy.dishes = {"Nothing Like It": 10000}
    r = client.post(f"/api/session/{cell['session_id']}/swiggy-cart", json={})
    assert r.status_code == 502 and "isn't on" in r.get_json()["error"]


def test_ordering_and_payment_tools_are_refused(client, swiggy):
    _connect(client, swiggy)
    for name in ["place_food_order", "get_payment_options", "apply_food_coupon", "confirm_order"]:
        with pytest.raises(SwiggyError, match="never calls"):
            swiggy_live.call(1, name, {})
    assert swiggy.tool_calls() == []


def test_required_fields_we_cannot_fill_are_named():
    tool = {"name": "search_restaurants", "input_schema": {"type": "object", "required": ["query", "lat", "lng"],
                                                           "properties": {"query": {}, "lat": {}, "lng": {}}}}
    with pytest.raises(SwiggyError, match="lat, lng"):
        swiggy_live.build_args(tool, {"query": "Dosa"})


def test_live_menu_needs_a_connection_and_is_private(client, swiggy):
    r = client.get("/api/user/1/swiggy/menu?restaurant=X")
    assert r.status_code == 502 and "Connect your Swiggy account" in r.get_json()["error"]
    v = client.post("/api/profiles", json={"name": "P", "diet": "veg", "weekly_budget": 2000,
                                           "meals": ["dinner"], "favourites": [6]}).get_json()
    assert client.get(f"/api/user/{v['user']['id']}/swiggy/addresses").status_code == 401


def test_vegetarians_do_not_see_non_veg_dishes(client, swiggy, monkeypatch):
    _connect(client, swiggy, uid=2)                                          # Meera is vegan
    client.post("/api/user/2/swiggy/address", json={"address_id": "addr-home"})
    swiggy.dishes = {"Chana Masala": 16000, "Chicken 65": 22000}
    orig = swiggy.tool
    def tool(name, args):
        out = orig(name, args)
        if name == "get_restaurant_menu":
            for i in out["structuredContent"]["menu"]["categories"][0]["items"]:
                i["isVeg"] = 0 if "Chicken" in i["name"] else 1
        return out
    monkeypatch.setattr(swiggy, "tool", tool)
    m = client.get("/api/user/2/swiggy/menu?restaurant=FreshMenu").get_json()
    assert [i["name"] for i in m["items"]] == ["Chana Masala"] and m["hidden_nonveg"] == 1 and m["diet"] == "vegan"


def test_reading_helpers():
    assert swiggy_live.rupees({"price": 180}) == 180 and swiggy_live.rupees({"priceInPaise": 950}) == 9.5
    assert swiggy_live.rupees({"price": 25000}) == 250.0 and swiggy_live.rupees({"name": "x"}) is None
    assert swiggy_live._num({"a": {"b": [{"toPay": "₹ 212"}]}}, "to_pay") == 212.0
    nested = {"data": {"list": [{"id": 1, "name": "A"}, {"id": 2, "name": "B"}], "meta": [{"id": 9}]}}
    assert [r["name"] for r in swiggy_live.records(nested, "id", "name")] == ["A", "B"]
