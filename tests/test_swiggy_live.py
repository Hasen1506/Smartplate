"""Documented Food contracts and failure cases against a fake MCP server.
No real account, cart or order is used by these tests."""
import json

import pytest
from test_followups import FakeSwiggy, _connect

from smartplate import config, db
from smartplate.app import create_app
from smartplate.integrations import swiggy_connect, swiggy_live
from smartplate.integrations.swiggy_connect import SwiggyError

SCHEMAS = {
    "get_addresses": {"type": "object", "properties": {"page": {"type": "number"}, "pageSize": {"type": "number"}}},
    "search_restaurants": {"type": "object", "required": ["query", "addressId"],
                           "properties": {"query": {"type": "string"}, "addressId": {"type": "string"}}},
    "get_restaurant_menu": {"type": "object", "required": ["restaurantId", "addressId"],
                            "properties": {"restaurantId": {"type": "string"}, "addressId": {"type": "string"}}},
    "search_menu": {"type": "object", "required": ["query", "addressId"],
                    "properties": {"query": {"type": "string"}, "addressId": {"type": "string"},
                                   "restaurantIdOfAddedItem": {"type": "string"}}},
    "update_food_cart": {"type": "object", "required": ["cartItems", "restaurantId", "addressId"], "properties": {
        "cartItems": {"type": "array", "items": {"type": "object", "required": ["menu_item_id", "quantity"],
                                                 "properties": {"menu_item_id": {"type": "string"},
                                                                "quantity": {"type": "integer"}}}},
        "restaurantId": {"type": "string"}, "addressId": {"type": "string"}, "restaurantName": {"type": "string"}}},
    "get_food_cart": {"type": "object", "required": ["addressId"], "properties": {
        "addressId": {"type": "string"}, "restaurantName": {"type": "string"}}},
    "get_payment_options": {"type": "object", "properties": {"addressId": {"type": "string"}}},
    "place_food_order": {"type": "object", "required": ["addressId", "paymentMethod"],
                         "properties": {"addressId": {"type": "string"}, "paymentMethod": {"type": "string"}}},
    "track_food_order": {"type": "object", "required": ["orderId"],
                         "properties": {"orderId": {"type": "string"}}},
    "get_food_orders": {"type": "object", "required": ["addressId"],
                        "properties": {"addressId": {"type": "string"}}},
}


class FakeLive(FakeSwiggy):
    """Adds tools/call. Addresses come back as JSON text, menus as structuredContent,
    the cart in the documented data.data envelope with pricing.to_pay."""

    def __init__(self):
        super().__init__()
        self.cart, self.dishes = None, {}
        self.stock, self.has_variants, self.has_addons, self.is_veg = True, False, False, True
        self.search_error = None
        self.order_uncertain = False

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
                {"restaurantId": "r-1", "name": q if q.endswith(" (Adyar)") else f"{q} (Adyar)", "avgRating": 4.3,
                 "sla": "30 mins", "availabilityStatus": "OPEN"},
                {"restaurantId": "r-2", "name": "Completely Different Kitchen", "avgRating": 4.0}]}}
        if name == "get_restaurant_menu":
            assert args["restaurantId"] == "r-1" and args["addressId"] == "addr-home"
            items = [{"itemId": f"m{i}", "name": n, "priceInPaise": p, "isVeg": 1} for i, (n, p) in enumerate(self.dishes.items())]
            return {"structuredContent": {"restaurant": {"id": "r-1", "name": "Hotel Saravana Bhavan (Adyar)",
                                                        "isOpen": True},
                                          "menu": {"categories": [{"title": "Mains", "items": items}]}}}
        if name == "search_menu":
            assert args["addressId"] == "addr-home" and args["restaurantIdOfAddedItem"] == "r-1"
            if self.search_error:
                return {"structuredContent": {"success": False, "error": {"message": self.search_error}}}
            items = [{"menu_item_id": f"m{i}", "name": n, "priceInPaise": p,
                      "restaurant_id": "r-1", "inStock": self.stock,
                      "hasVariants": self.has_variants, "hasAddons": self.has_addons,
                      "isVeg": self.is_veg} for i, (n, p) in enumerate(self.dishes.items())]
            return {"structuredContent": {"success": True, "data": {"items": items}}}
        if name == "update_food_cart":
            item = args["cartItems"][0]
            assert item["quantity"] == 1 and args["restaurantId"] == "r-1"
            self.cart = item["menu_item_id"]
            return {"content": [{"type": "text", "text": "Cart updated"}]}
        if name == "get_food_cart":
            if self.cart is None:
                return {"structuredContent": {"success": True, "data": {"addressId": args["addressId"], "data": {"items": []}}}}
            price = next(p for i, (n, p) in enumerate(self.dishes.items()) if f"m{i}" == self.cart) / 100
            name = next(n for i, (n, p) in enumerate(self.dishes.items()) if f"m{i}" == self.cart)
            return {"structuredContent": {"success": True, "data": {"addressId": args["addressId"], "data": {
                "restaurant": {"id": "r-1", "name": "Hotel Saravana Bhavan (Adyar)"},
                "items": [{"menu_item_id": self.cart, "name": name, "quantity": 1, "total": price,
                           "is_veg": self.is_veg, "in_stock": self.stock}],
                "pricing": {"item_total": price, "delivery_charge": 35, "to_pay": price + 35}}}}}
        if name == "get_payment_options":
            return {"structuredContent": {"success": True, "data": {"cod": {
                "available": True, "id": "Cash", "displayName": "Cash on Delivery"}}}}
        if name == "place_food_order":
            assert args == {"addressId": "addr-home", "paymentMethod": "Cash"}
            if self.order_uncertain:
                return {"structuredContent": {"success": True, "data": {"status": "UNKNOWN"}}}
            self.cart = None
            return {"structuredContent": {"success": True, "data": {
                "orderId": "real-order-17", "normalizedStatus": "success", "status": "CONFIRMED"}}}
        if name == "get_food_orders":
            assert args["addressId"] == "addr-home"
            return {"structuredContent": {"success": True, "data": {"orders": [{
                "orderId": "real-order-17", "restaurantName": "Hotel Saravana Bhavan",
                "orderedItems": "Mini Tiffin (1)", "orderTotal": "160",
                "orderStatus": "PREPARING", "orderedTime": "2026-09-30T12:00:00Z"}]}}}
        if name == "track_food_order":
            assert args["orderId"] == "real-order-17"
            return {"structuredContent": {"success": True, "data": {"orders": [{
                "orderId": "real-order-17", "orderStatus": "PREPARING", "title": "Food is being prepared",
                "subtitle": "The restaurant is working on your order", "etaText": "Arrives in 25 minutes"}]}}}
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


def _next_delivery(client, uid=1):
    v = client.get(f"/api/user/{uid}/plan").get_json()
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


def test_real_address_search_favourites_menu_and_exact_item_cart(client, swiggy):
    _connect(client, swiggy, uid=3)
    client.post("/api/user/3/swiggy/address", json={"address_id": "addr-home"})
    found = client.get("/api/user/3/swiggy/restaurants?query=Hotel%20Saravana%20Bhavan").get_json()
    assert found["address_id"] == "addr-home" and found["restaurants"][0]["id"] == "r-1"
    assert found["restaurants"][0]["availability"] == "OPEN"
    fav = client.post("/api/user/3/swiggy/favourites", json={
        "restaurant_id": "r-1", "restaurant_name": found["restaurants"][0]["name"]}).get_json()
    assert fav["favourite"] and fav["restaurants"][0]["id"] == "r-1"
    swiggy.dishes = {"Veg Meals": 18000, "Mini Tiffin": 12500}
    params = "restaurant_id=r-1&restaurant_name=Hotel%20Saravana%20Bhavan%20%28Adyar%29"
    menu = client.get(f"/api/user/3/swiggy/live-menu?{params}").get_json()
    assert [x["id"] for x in menu["items"]] == ["m0", "m1"]
    body = {"restaurant_id": "r-1", "restaurant_name": menu["restaurant"]["name"],
            "item_id": "m1", "item_name": "Mini Tiffin"}
    preview = client.post("/api/user/3/swiggy/live-cart/preview", json=body).get_json()
    assert preview["item_id"] == "m1" and preview["menu_price"] == 125.0
    assert swiggy.cart is None
    assert client.post("/api/user/3/swiggy/live-cart", json=body).status_code == 409
    added = client.post("/api/user/3/swiggy/live-cart", json={
        **body, "expected_fingerprint": preview["fingerprint"]}).get_json()
    assert added["item"] == "Mini Tiffin" and added["to_pay"] == 160.0
    assert swiggy.cart == "m1" and "place_food_order" not in swiggy.tool_calls()


def test_fill_cart_puts_the_planned_dish_in_and_reads_to_pay(client, swiggy):
    _connect(client, swiggy, uid=3)  # profile 1 has hard allergy/medical exclusions
    client.post("/api/user/3/swiggy/address", json={"address_id": "addr-home"})
    cell = _next_delivery(client, uid=3)
    swiggy.dishes = {"Something Else": 9900, cell["item"]: 21000}
    preview = client.get(f"/api/session/{cell['session_id']}/swiggy-cart/preview").get_json()
    assert preview["item"] == cell["item"] and preview["restaurant_id"] == "r-1"
    r = client.post(f"/api/session/{cell['session_id']}/swiggy-cart",
                    json={"expected_fingerprint": preview["fingerprint"]})
    assert r.status_code == 200, r.get_json()
    cart = r.get_json()
    assert cart["item"] == cell["item"] and cart["to_pay"] == 245.0 and cart["menu_price"] == 210.0
    assert cart["over_plan"] == round(245.0 - cell["cost"], 2) and cart["checkout_url"].startswith("https://www.swiggy.com/")
    assert swiggy.cart == "m1"
    assert "place_food_order" not in swiggy.tool_calls() and "get_payment_options" not in swiggy.tool_calls()


def test_missing_dish_and_missing_address_are_explained(client, swiggy):
    _connect(client, swiggy, uid=3)
    cell = _next_delivery(client, uid=3)
    r = client.get(f"/api/session/{cell['session_id']}/swiggy-cart/preview")
    assert r.status_code == 502 and "delivery address" in r.get_json()["error"]
    client.post("/api/user/3/swiggy/address", json={"address_id": "addr-home"})
    swiggy.dishes = {"Nothing Like It": 10000}
    r = client.get(f"/api/session/{cell['session_id']}/swiggy-cart/preview")
    assert r.status_code == 502 and "exact live match" in r.get_json()["error"]


def test_cart_requires_review_and_rejects_changed_item(client, swiggy):
    _connect(client, swiggy, uid=3)
    client.post("/api/user/3/swiggy/address", json={"address_id": "addr-home"})
    cell = _next_delivery(client, uid=3)
    sid = cell["session_id"]
    swiggy.dishes = {cell["item"]: 21000}
    assert client.post(f"/api/session/{sid}/swiggy-cart", json={}).status_code == 409
    preview = client.get(f"/api/session/{sid}/swiggy-cart/preview").get_json()
    assert "update_food_cart" not in swiggy.tool_calls()
    swiggy.dishes = {"Another dish": 12000, cell["item"]: 21000}  # provider item ID changed
    changed = client.post(f"/api/session/{sid}/swiggy-cart",
                          json={"expected_fingerprint": preview["fingerprint"]})
    assert changed.status_code == 409 and "changed" in changed.get_json()["message"]
    assert "update_food_cart" not in swiggy.tool_calls()


def test_cart_fails_closed_for_hard_rules_and_unverified_item(client, swiggy):
    _connect(client, swiggy, uid=1)
    sid = _next_delivery(client)["session_id"]
    blocked = client.get(f"/api/session/{sid}/swiggy-cart/preview")
    assert blocked.status_code == 502 and "cannot verify" in blocked.get_json()["error"]
    assert "search_menu" not in swiggy.tool_calls()

    _connect(client, swiggy, uid=3)
    client.post("/api/user/3/swiggy/address", json={"address_id": "addr-home"})
    cell = _next_delivery(client, uid=3)
    sid = cell["session_id"]
    swiggy.dishes = {cell["item"]: 21000}
    for field in ("stock", "has_variants", "has_addons"):
        setattr(swiggy, field, False if field == "stock" else True)
        response = client.get(f"/api/session/{sid}/swiggy-cart/preview")
        assert response.status_code == 502
        assert "update_food_cart" not in swiggy.tool_calls()
        setattr(swiggy, field, True if field == "stock" else False)
    swiggy.search_error = "Item unavailable"
    response = client.get(f"/api/session/{sid}/swiggy-cart/preview")
    assert response.status_code == 502 and "Item unavailable" in response.get_json()["error"]


def test_ordering_gate_and_unsupported_tools_are_refused(client, swiggy):
    _connect(client, swiggy)
    with pytest.raises(SwiggyError, match="disabled"):
        swiggy_live.call(1, "place_food_order", {})
    for name in ["apply_food_coupon", "confirm_order"]:
        with pytest.raises(SwiggyError, match="does not support"):
            swiggy_live.call(1, name, {})
    assert swiggy.tool_calls() == []


def test_cod_checkout_requires_fresh_review_and_places_only_once(client, swiggy, monkeypatch):
    monkeypatch.setattr(config, "LIVE_ORDERS", True)
    _connect(client, swiggy, uid=3)
    client.post("/api/user/3/swiggy/address", json={"address_id": "addr-home"})
    swiggy.dishes = {"Mini Tiffin": 12500}
    body = {"restaurant_id": "r-1", "restaurant_name": "Hotel Saravana Bhavan (Adyar)",
            "item_id": "m0", "item_name": "Mini Tiffin"}
    p = client.post("/api/user/3/swiggy/live-cart/preview", json=body).get_json()
    assert client.post("/api/user/3/swiggy/live-cart", json={**body,
        "expected_fingerprint": p["fingerprint"]}).status_code == 200
    review = client.get("/api/user/3/swiggy/checkout/preview").get_json()
    assert review["to_pay"] == 160 and review["payment_method"] == "Cash"
    assert client.post("/api/user/3/swiggy/checkout", json={}).status_code == 409
    placed = client.post("/api/user/3/swiggy/checkout", json={
        "expected_fingerprint": review["fingerprint"]})
    assert placed.status_code == 200 and placed.get_json()["order_id"] == "real-order-17"
    assert client.post("/api/user/3/swiggy/checkout", json={
        "expected_fingerprint": review["fingerprint"]}).status_code == 409
    assert swiggy.tool_calls().count("place_food_order") == 1
    tracking = client.get("/api/user/3/swiggy/orders/real-order-17").get_json()["tracking"]
    assert tracking["status"] == "PREPARING" and tracking["eta"] == "Arrives in 25 minutes"
    history = client.get("/api/user/3/swiggy/order-history").get_json()
    assert history["provider_orders"][0]["order_id"] == "real-order-17"
    assert history["attempts"][0]["state"] == "confirmed"
    # A completed order does not permanently prohibit ordering the same favourite.
    p = client.post("/api/user/3/swiggy/live-cart/preview", json=body).get_json()
    assert client.post("/api/user/3/swiggy/live-cart", json={**body,
        "expected_fingerprint": p["fingerprint"]}).status_code == 200
    again = client.get("/api/user/3/swiggy/checkout/preview").get_json()
    assert again["fingerprint"] != review["fingerprint"]
    assert client.post("/api/user/3/swiggy/checkout", json={
        "expected_fingerprint": again["fingerprint"]}).status_code == 200
    assert swiggy.tool_calls().count("place_food_order") == 2


def test_uncertain_order_blocks_repeat_and_recovers_with_provider_history(client, swiggy, monkeypatch):
    monkeypatch.setattr(config, "LIVE_ORDERS", True)
    _connect(client, swiggy, uid=3)
    client.post("/api/user/3/swiggy/address", json={"address_id": "addr-home"})
    swiggy.dishes = {"Mini Tiffin": 12500}
    body = {"restaurant_id": "r-1", "restaurant_name": "Hotel Saravana Bhavan (Adyar)",
            "item_id": "m0", "item_name": "Mini Tiffin"}
    p = client.post("/api/user/3/swiggy/live-cart/preview", json=body).get_json()
    client.post("/api/user/3/swiggy/live-cart", json={**body, "expected_fingerprint": p["fingerprint"]})
    review = client.get("/api/user/3/swiggy/checkout/preview").get_json()
    swiggy.order_uncertain = True
    assert client.post("/api/user/3/swiggy/checkout", json={
        "expected_fingerprint": review["fingerprint"]}).status_code == 502
    assert client.get("/api/user/3/swiggy/checkout/preview").status_code == 502
    assert swiggy.tool_calls().count("place_food_order") == 1
    history = client.get("/api/user/3/swiggy/order-history").get_json()
    assert history["attempts"][0]["state"] == "unknown"


def test_required_fields_we_cannot_fill_are_named():
    tool = {"name": "search_restaurants", "input_schema": {"type": "object", "required": ["query", "lat", "lng"],
                                                           "properties": {"query": {}, "lat": {}, "lng": {}}}}
    with pytest.raises(SwiggyError, match="lat, lng"):
        swiggy_live.build_args(tool, {"query": "Dosa"})


def test_live_menu_needs_a_connection_and_is_private(client, swiggy):
    r = client.get("/api/user/1/swiggy/menu?restaurant=X")
    body = r.get_json()                       # not connected is the user's to fix: 409 + Connect, never 502
    assert r.status_code == 409 and body["error"] == "swiggy_not_connected"
    assert "Connect your Swiggy account" in body["message"] and body["action"]["act"] == "swiggy-connect"
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
    assert swiggy_live.rupees({"priceInRupees": 180}) == 180
    assert swiggy_live.rupees({"priceInPaise": 950}) == 9.5
    # Swiggy's bare `price` has no documented unit: read it in either form (live QA saw
    # every dish as "Price in cart" when bare prices were dropped).
    assert swiggy_live.rupees({"price": 180}) == 180 and swiggy_live.price_estimated({"price": 180})
    assert swiggy_live.rupees({"price": 18000}) == 180                             # paise
    assert swiggy_live.rupees({"price": "₹149"}) == 149 and not swiggy_live.price_estimated({"price": "₹149"})
    assert swiggy_live.rupees({"price": 25000, "priceInPaise": 25000}) == 250.0
    assert swiggy_live.rupees({"name": "x"}) is None
    assert swiggy_live._num({"a": {"b": [{"toPay": "₹ 212"}]}}, "to_pay") == 212.0
    assert swiggy_live._num({"cart": {"items": [{"total": 180}], "bill": {"to_pay": 225}}}, "to_pay") == 225
    nested = {"data": {"list": [{"id": 1, "name": "A"}, {"id": 2, "name": "B"}], "meta": [{"id": 9}]}}
    assert [r["name"] for r in swiggy_live.records(nested, "id", "name")] == ["A", "B"]
    assert swiggy_live._num({"to_pay": float("nan")}, "to_pay") is None
    assert swiggy_live.rupees({"priceInRupees": float("inf")}) is None


def _prepared_checkout(client, swiggy, monkeypatch):
    monkeypatch.setattr(config, "LIVE_ORDERS", True)
    with db.cursor() as cur:
        cur.execute("UPDATE users SET diet='veg' WHERE id=3")
    _connect(client, swiggy, uid=3)
    client.post("/api/user/3/swiggy/address", json={"address_id": "addr-home"})
    swiggy.dishes = {"Mini Tiffin": 12500}
    body = {"restaurant_id": "r-1", "restaurant_name": "Hotel Saravana Bhavan (Adyar)",
            "item_id": "m0", "item_name": "Mini Tiffin"}
    p = client.post("/api/user/3/swiggy/live-cart/preview", json=body).get_json()
    assert client.post("/api/user/3/swiggy/live-cart", json={**body,
        "expected_fingerprint": p["fingerprint"]}).status_code == 200
    review = client.get("/api/user/3/swiggy/checkout/preview")
    assert review.status_code == 200, review.get_json()
    return review.get_json()


@pytest.mark.parametrize("change,expected", [
    ("price", 409), ("item", 502), ("restaurant", 502),
    ("address", 409),            # the cart moved to another address: the user's choice to make (Oct 8, 2026)
    ("quantity", 502), ("nonveg", 502), ("variants", 502), ("addons", 502),
    ("stock", 502), ("nan", 502), ("over_limit", 502), ("payment", 502),
])
def test_checkout_rechecks_external_cart_changes(client, swiggy, monkeypatch, change, expected):
    review = _prepared_checkout(client, swiggy, monkeypatch)
    original = swiggy.tool
    def edited(name, args):
        result = original(name, args)
        if name == "get_food_cart":
            envelope = result["structuredContent"]["data"]
            cart, item = envelope["data"], envelope["data"]["items"][0]
            if change == "price": cart["pricing"]["to_pay"] += 1
            if change == "item": item["menu_item_id"] = "other-item"
            if change == "restaurant": cart["restaurant"]["id"] = "other-restaurant"
            if change == "address": envelope["addressId"] = "other-address"
            if change == "quantity": item["quantity"] = 2
            if change == "nonveg": item["is_veg"] = False
            if change == "variants": item["variants"] = [{"id": "unreviewed"}]
            if change == "addons": item["addons"] = [{"id": "unreviewed"}]
            if change == "stock": item["in_stock"] = 0
            if change == "nan": cart["pricing"]["to_pay"] = float("nan")
            if change == "over_limit": cart["pricing"]["to_pay"] = 1001
        if name == "get_payment_options" and change == "payment":
            result["structuredContent"]["data"]["cod"]["available"] = False
        return result
    monkeypatch.setattr(swiggy, "tool", edited)
    r = client.post("/api/user/3/swiggy/checkout", json={"expected_fingerprint": review["fingerprint"]})
    assert r.status_code == expected, r.get_json()
    assert "place_food_order" not in swiggy.tool_calls()


def test_latest_quote_wins_and_expired_quotes_never_place(client, swiggy, monkeypatch):
    import datetime as dt

    from smartplate import clock
    first = _prepared_checkout(client, swiggy, monkeypatch)
    second = client.get("/api/user/3/swiggy/checkout/preview").get_json()
    assert client.post("/api/user/3/swiggy/checkout", json={"expected_fingerprint": first["fingerprint"]}).status_code == 409
    with db.cursor() as cur:
        cur.execute("UPDATE swiggy_checkout_quotes SET created_ts=?", ((clock.now() - dt.timedelta(minutes=6)).isoformat(),))
    assert client.post("/api/user/3/swiggy/checkout", json={"expected_fingerprint": second["fingerprint"]}).status_code == 409
    assert "place_food_order" not in swiggy.tool_calls()


def test_unknown_order_cannot_be_retried_at_a_new_price(client, swiggy, monkeypatch):
    review = _prepared_checkout(client, swiggy, monkeypatch)
    swiggy.order_uncertain = True
    assert client.post("/api/user/3/swiggy/checkout", json={"expected_fingerprint": review["fingerprint"]}).status_code == 502
    swiggy.dishes = {"Mini Tiffin": 13000}
    assert client.get("/api/user/3/swiggy/checkout/preview").status_code == 502
    assert swiggy.tool_calls().count("place_food_order") == 1


def test_prepared_cart_can_be_restored_after_reload(client, swiggy, monkeypatch):
    _prepared_checkout(client, swiggy, monkeypatch)
    other_browser = create_app().test_client()
    other_browser.environ_base["HTTP_X_SMARTPLATE_KEY"] = client.environ_base["HTTP_X_SMARTPLATE_KEY"]
    cart = other_browser.get("/api/user/3/swiggy/live-cart").get_json()["cart"]
    assert cart["item"] == "Mini Tiffin" and cart["to_pay"] == 160 and cart["orderable"] is True


def test_malformed_cart_is_not_treated_as_empty(client, swiggy, monkeypatch):
    _prepared_checkout(client, swiggy, monkeypatch)
    swiggy.cart = None
    before = swiggy.tool_calls().count("update_food_cart")
    original = swiggy.tool
    monkeypatch.setattr(swiggy, "tool", lambda name, args: {"structuredContent": {"success": True, "data": {}}}
                        if name == "get_food_cart" else original(name, args))
    body = {"restaurant_id": "r-1", "restaurant_name": "Hotel Saravana Bhavan (Adyar)",
            "item_id": "m0", "item_name": "Mini Tiffin"}
    p = client.post("/api/user/3/swiggy/live-cart/preview", json=body).get_json()
    assert client.post("/api/user/3/swiggy/live-cart", json={**body, "expected_fingerprint": p["fingerprint"]}).status_code == 502
    assert swiggy.tool_calls().count("update_food_cart") == before


def test_all_address_pages_are_available_for_selection(client, swiggy, monkeypatch):
    _connect(client, swiggy)
    original = swiggy.tool
    def paged(name, args):
        if name != "get_addresses": return original(name, args)
        page = args["page"]
        return {"structuredContent": {"success": True, "data": {
            "addresses": [{"id": "addr-home" if page == 2 else "addr-first", "addressTag": "Home",
                           "addressLine": "A complete delivery address"}],
            "pagination": {"page": page, "hasMore": page == 1}}}}
    monkeypatch.setattr(swiggy, "tool", paged)
    rows = client.get("/api/user/1/swiggy/addresses").get_json()
    assert [r["id"] for r in rows] == ["addr-first", "addr-home"]
    assert client.post("/api/user/1/swiggy/address", json={"address_id": "addr-home"}).status_code == 200

@pytest.mark.parametrize("failure", ["timeout", "pending_payment"])
def test_ambiguous_submission_never_confirms_or_retries(client, swiggy, monkeypatch, failure):
    review = _prepared_checkout(client, swiggy, monkeypatch)
    original = swiggy.tool
    def failed(name, args):
        if name != "place_food_order": return original(name, args)
        if failure == "timeout":
            raise SwiggyError("Request timed out")
        return {"structuredContent": {"success": True, "data": {
            "normalizedStatus": "success", "orderId": "real-order-17", "status": "PENDING_PAYMENT"}}}
    monkeypatch.setattr(swiggy, "tool", failed)
    r = client.post("/api/user/3/swiggy/checkout", json={"expected_fingerprint": review["fingerprint"]})
    assert r.status_code == 502
    assert client.get("/api/user/3/swiggy/checkout/preview").status_code == 502
    assert swiggy.tool_calls().count("place_food_order") == 1
    assert client.get("/api/user/3/swiggy/order-history").get_json()["attempts"][0]["state"] == "unknown"


@pytest.mark.parametrize("status,code", [(401, "swiggy_auth_expired"), (429, "swiggy_rate_limited")])
def test_provider_auth_and_rate_limits_are_actionable_and_never_retried(client, swiggy, monkeypatch, status, code):
    _connect(client, swiggy)
    original = swiggy_connect._http
    failed_calls = []
    def failed(method, url, headers, body):
        if url.endswith("/food"):
            failed_calls.append(1)
            return status, {"retry-after": "45"}, b""
        return original(method, url, headers, body)
    monkeypatch.setattr(swiggy_connect, "_http", failed)
    r = client.get("/api/user/1/swiggy/addresses")
    assert r.get_json()["code"] == code and len(failed_calls) == 1
    if status == 429:
        assert r.status_code == 429 and r.headers["Retry-After"] == "45"
    else:
        state = client.get("/api/user/1/swiggy").get_json()
        assert not state["connected"] and state["expired"]


def test_exact_item_on_later_search_page_is_reviewable(client, swiggy, monkeypatch):
    _connect(client, swiggy, uid=3)
    client.post("/api/user/3/swiggy/address", json={"address_id": "addr-home"})
    swiggy.dishes = {"Mini Tiffin": 12500}
    original = swiggy.tool
    offsets = []
    def paged(name, args):
        if name != "search_menu": return original(name, args)
        offsets.append(args.get("offset", 0))
        if args.get("offset", 0) == 0:
            return {"structuredContent": {"success": True, "data": {"items": [], "hasMore": True, "nextOffset": 20}}}
        return original(name, args)
    SCHEMAS["search_menu"]["properties"]["offset"] = {"type": "number"}
    try:
        client.post("/api/user/3/swiggy/discover", json={})
        monkeypatch.setattr(swiggy, "tool", paged)
        r = client.post("/api/user/3/swiggy/live-cart/preview", json={
            "restaurant_id": "r-1", "restaurant_name": "Hotel Saravana Bhavan (Adyar)",
            "item_id": "m0", "item_name": "Mini Tiffin"})
        assert r.status_code == 200, r.get_json()
        assert offsets == [0, 20] and "update_food_cart" not in swiggy.tool_calls()
    finally:
        SCHEMAS["search_menu"]["properties"].pop("offset", None)


def test_reconnection_requires_a_new_address_and_cart_review(client, swiggy, monkeypatch):
    review = _prepared_checkout(client, swiggy, monkeypatch)
    _connect(client, swiggy, uid=3)
    status = client.get("/api/user/3/swiggy").get_json()
    assert status["connected"] and status["address"] is None
    with db.cursor() as cur:
        assert not cur.execute("SELECT 1 FROM swiggy_cart_intents WHERE user_id=3").fetchone()
        assert not cur.execute("SELECT 1 FROM swiggy_checkout_quotes WHERE token=?", (review["fingerprint"],)).fetchone()
    assert "place_food_order" not in swiggy.tool_calls()


def test_shared_legacy_connection_is_never_exposed(client, swiggy):
    _connect(client, swiggy)
    with db.cursor() as cur:
        cur.execute("UPDATE users SET access_hash=NULL WHERE id=1")
    before = len(swiggy.calls)
    state = client.get("/api/user/1/swiggy").get_json()
    assert not state["connected"] and state["requires_private_profile"]
    assert "tools" not in state and "address" not in state
    assert client.get("/api/user/1/swiggy/addresses").status_code == 409     # not connected for this profile
    assert len(swiggy.calls) == before


def test_scoped_dish_search_paginates_and_preserves_real_identity(client, swiggy, monkeypatch):
    monkeypatch.setitem(SCHEMAS["search_menu"]["properties"], "offset", {"type": "number"})
    _connect(client, swiggy, uid=3)
    client.post("/api/user/3/swiggy/address", json={"address_id": "addr-home"})
    swiggy.dishes = {"Mini Tiffin": 12500}
    original = swiggy.tool
    def pages(name, args):
        if name != "search_menu": return original(name, args)
        assert args["restaurantIdOfAddedItem"] == "r-1" and args["addressId"] == "addr-home"
        offset = args.get("offset", 0)
        rows = [{"menu_item_id": "m0" if not offset else "m1", "name": "Mini Tiffin" if not offset else "Deluxe Tiffin",
                 "restaurant_id": "r-1", "isVeg": True, "priceInRupees": 125, "inStock": 1,
                 "hasVariants": False, "hasAddons": False},
                {"menu_item_id": "other", "name": "Different place's meal", "restaurant_id": "r-other"}]
        return {"structuredContent": {"success": True, "data": {"items": rows, "hasMore": offset == 0,
                                                                "nextOffset": 20 if not offset else None}}}
    monkeypatch.setattr(swiggy, "tool", pages)
    route = "/api/user/3/swiggy/dishes?restaurant_id=r-1&restaurant_name=Hotel&query=tiffin"
    first = client.get(route).get_json()
    assert [r["id"] for r in first["items"]] == ["m0"]
    assert first["items"][0]["price"] == 125 and first["next_offset"] == 20
    second = client.get(route + "&offset=20").get_json()
    assert [r["id"] for r in second["items"]] == ["m1"] and not second["has_more"]
    assert "update_food_cart" not in swiggy.tool_calls()
    assert client.get(route + "&offset=-1").status_code == 400


def test_dish_search_never_invents_pages_or_ignores_diet(client, swiggy, monkeypatch):
    _connect(client, swiggy, uid=2)
    client.post("/api/user/2/swiggy/address", json={"address_id": "addr-home"})
    original = swiggy.tool
    monkeypatch.setattr(swiggy, "tool", lambda name, args: {"structuredContent": {"success": True, "data": {
        "items": [{"menu_item_id": "chicken", "name": "Chicken", "isVeg": False},
                  {"menu_item_id": "veg", "name": "Plain rice", "isVeg": True}], "hasMore": False}}}
        if name == "search_menu" else original(name, args))
    route = "/api/user/2/swiggy/dishes?restaurant_id=r-1&restaurant_name=Hotel&query=rice"
    result = client.get(route).get_json()
    assert [r["id"] for r in result["items"]] == ["veg"] and result["hidden_nonveg"] == 1
    monkeypatch.setattr(swiggy, "tool", lambda name, args: {"structuredContent": {"success": True, "data": {
        "items": [], "hasMore": True, "nextOffset": 0}}} if name == "search_menu" else original(name, args))
    assert client.get(route).status_code == 502
