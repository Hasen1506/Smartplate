"""The order list (dish × price × quantity, no feed) and a quantity for a live cart."""
from test_followups import _connect
from test_swiggy_live import client, swiggy  # noqa: F401  (fixtures)

from smartplate.domain import allergens, models


def test_order_list_is_every_safe_dish_with_price_and_the_meals_money(client):
    v = client.get("/api/user/3/plan").get_json()
    cell = next(c for d in v["grid"] for c in d["meals"].values() if c and c["status"] == "active")
    r = client.get(f"/api/user/3/order-list?session_id={cell['session_id']}").get_json()
    user = models.get_user(3)
    assert r["dishes"] and r["meal"] in ("breakfast", "lunch", "dinner") and r["session_label"]
    assert r["limits"]["left_week"] is not None
    menu = {it["id"]: it for it in models.menu_for_user(user)}
    assert all(allergens.violates(user, menu[d["id"]]) is None for d in r["dishes"])
    assert r["hidden"] == len(menu) - len(r["dishes"])
    assert all(set(d) >= {"name", "price", "restaurant", "veg", "side", "fits_meal"} for d in r["dishes"])
    order = [(r["meal"] not in d["pooled"], not d["fits_meal"], d["side"], d["price"]) for d in r["dishes"]]
    assert order == sorted(order)                       # pool, then right for the meal, sides last, cheap first
    assert client.get("/api/user/3/order-list?meal=brunch").status_code == 400
    assert client.get("/api/user/3/order-list?session_id=x").status_code == 400
    other = client.get("/api/user/1/plan").get_json()
    theirs = next(c for d in other["grid"] for c in d["meals"].values() if c)["session_id"]
    assert client.get(f"/api/user/3/order-list?session_id={theirs}").status_code in (400, 401)


def test_a_live_dish_goes_in_the_cart_in_the_quantity_reviewed(client, swiggy):
    _connect(client, swiggy, uid=3)
    client.post("/api/user/3/swiggy/address", json={"address_id": "addr-home"})
    swiggy.dishes = {"Veg Meals": 18000, "Mini Tiffin": 12500}
    menu = client.get("/api/user/3/swiggy/live-menu?restaurant_id=r-1&restaurant_name=Hotel%20Saravana%20Bhavan").get_json()
    body = {"restaurant_id": "r-1", "restaurant_name": menu["restaurant"]["name"], "item_id": "m1",
            "item_name": "Mini Tiffin", "quantity": 3}
    p = client.post("/api/user/3/swiggy/live-cart/preview", json=body).get_json()
    assert p["quantity"] == 3 and p["menu_price"] == 125.0 and p["menu_total"] == 375.0
    one = client.post("/api/user/3/swiggy/live-cart/preview", json={**body, "quantity": 1}).get_json()
    assert one["fingerprint"] != p["fingerprint"]                     # the quantity is part of what was reviewed
    assert client.post("/api/user/3/swiggy/live-cart", json={**body, "quantity": 2,
                                                             "expected_fingerprint": p["fingerprint"]}).status_code == 409
    added = client.post("/api/user/3/swiggy/live-cart", json={**body, "expected_fingerprint": p["fingerprint"]})
    assert added.status_code == 200, added.get_json()
    assert swiggy.cart == "m1" and swiggy.qty == 3 and "place_food_order" not in swiggy.tool_calls()
    for bad in (0, -1, "two", True):
        assert client.post("/api/user/3/swiggy/live-cart/preview", json={**body, "quantity": bad}).status_code == 400
    big = client.post("/api/user/3/swiggy/live-cart/preview", json={**body, "quantity": 9})
    assert big.status_code >= 400 and "large order" in big.get_json()["error"]
