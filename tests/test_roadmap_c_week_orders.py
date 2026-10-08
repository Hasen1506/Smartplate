"""Roadmap PR C — order any meal and the whole week, with the true bill (each failed
before: there was no way to pick slots and days, and a cart check returned only a bare
to_pay with no delivery/platform/packaging/GST lines).

Swiggy is the Food MCP contract fake (tests/test_swiggy_live.FakeLive). `FeeCart` returns
the cart bill in the documented `pricing` shape with every fee line Swiggy can show.
"""
import pytest

from test_swiggy_live import FakeLive, _connect
from smartplate import config
from smartplate.app import create_app
from smartplate.integrations import swiggy_connect, swiggy_live


class FeeCart(FakeLive):
    """The cart bill with items, delivery, platform fee, packaging, GST and a discount."""

    def tool(self, name, args):
        reply = super().tool(name, args)
        if name == "get_food_cart" and self.cart is not None:
            cart = reply["structuredContent"]["data"]["data"]
            item = cart["pricing"]["item_total"]
            cart["pricing"] = {"item_total": item, "delivery_charge": 35, "platform_fee": 10,
                               "packaging_charge": 15, "gst": 12.5, "discount": 20,
                               "to_pay": round(item + 35 + 10 + 15 + 12.5 - 20, 2)}
        return reply


@pytest.fixture
def client(seeded):
    app = create_app()
    app.config["TESTING"] = True
    return app.test_client()


@pytest.fixture
def swiggy(monkeypatch):
    fake = FeeCart()
    monkeypatch.setattr(swiggy_connect, "_http", fake)
    return fake


def _plan(client, uid=3):
    return client.get(f"/api/user/{uid}/plan").get_json()


def test_c1_bill_breakdown_shows_every_line_swiggy_returns():
    cart = {"pricing": {"item_total": 210, "delivery_charge": 35, "platform_fee": 10, "packaging_charge": 15,
                        "gst": 12.5, "discount": 20, "to_pay": 262.5}}
    bill = swiggy_live.bill_breakdown(cart, 210)
    assert [(l["label"], l["amount"]) for l in bill["lines"]] == [
        ("Items", 210), ("Delivery", 35), ("Platform fee", 10), ("Packaging", 15), ("GST & taxes", 12.5),
        ("Discount", -20)]
    assert bill["to_pay"] == 262.5 and bill["itemised"]
    # fields Swiggy didn't send are never guessed; the gap is shown so the lines add up
    partial = swiggy_live.bill_breakdown({"pricing": {"item_total": 210, "delivery_charge": 35, "to_pay": 262.5}}, 210)
    assert partial["lines"][-1] == {"label": "Other charges (as Swiggy shows them)", "amount": 17.5}
    assert not partial["itemised"] and sum(l["amount"] for l in partial["lines"]) == 262.5
    # paise payloads are read in rupees
    paise = swiggy_live.bill_breakdown({"pricing": {"item_total": 21000, "delivery_charge": 3500,
                                                    "to_pay_in_paise": 24500}}, 210)
    assert [l["amount"] for l in paise["lines"]] == [210.0, 35.0] and paise["to_pay"] == 245.0
    assert swiggy_live.bill_breakdown({"pricing": {}}, 210) is None


def test_c2_pick_any_slots_and_days_not_just_lunch(client, swiggy):
    pid = _plan(client)["plan"]["id"]
    q = client.get(f"/api/plan/{pid}/order-queue").get_json()
    assert q["queued"] == 0 and q["scheduling"]["supported"] is False
    slots = {m["meal"] for m in q["meals"] if m["orderable"]}
    assert {"breakfast", "dinner"} & slots, slots                       # not lunch-only
    q = client.post(f"/api/plan/{pid}/order-queue", json={"meals": ["breakfast", "dinner"], "days": [2, 3, 4]}).get_json()
    queued = [m for m in q["meals"] if m["queued"]]
    assert queued and {m["meal"] for m in queued} <= {"breakfast", "dinner"}
    assert {m["day_index"] for m in queued} <= {2, 3, 4}
    assert q["planned_total"] == round(sum(m["planned_cost"] for m in queued), 2)
    # the whole week, every slot
    q = client.post(f"/api/plan/{pid}/order-queue",
                    json={"meals": ["breakfast", "lunch", "dinner"], "days": list(range(7))}).get_json()
    assert q["queued"] == sum(1 for m in q["meals"] if m["orderable"])
    r = client.post(f"/api/plan/{pid}/order-queue", json={"meals": [], "days": [1]})
    assert r.status_code == 400


def _queued_dinner(client, swiggy):
    _connect(client, swiggy, uid=3)
    client.post("/api/user/3/swiggy/address", json={"address_id": "addr-home"})
    pid = _plan(client)["plan"]["id"]
    q = client.post(f"/api/plan/{pid}/order-queue", json={"meals": ["dinner"], "days": list(range(7))}).get_json()
    meal = next(m for m in q["meals"] if m["queued"])
    swiggy.dishes = {"Something Else": 9900, meal["item"]: 21000}
    return pid, meal


def test_c3_cart_check_shows_the_true_total_then_tap_to_place(client, swiggy):
    pid, meal = _queued_dinner(client, swiggy)
    sid = meal["session_id"]
    preview = client.get(f"/api/session/{sid}/swiggy-cart/preview").get_json()
    r = client.post(f"/api/session/{sid}/order/cart", json={"expected_fingerprint": preview["fingerprint"]})
    assert r.status_code == 200, r.get_json()
    cart = r.get_json()
    assert cart["to_pay"] == 262.5 and cart["bill"]["itemised"]
    assert {l["label"] for l in cart["bill"]["lines"]} >= {"Items", "Delivery", "Platform fee", "Packaging", "GST & taxes"}
    assert cart["next"]["mode"] == "tap_to_place"                     # no real order API enabled
    q = client.get(f"/api/plan/{pid}/order-queue").get_json()
    row = next(m for m in q["meals"] if m["session_id"] == sid)
    assert row["state"] == "cart_ready" and row["to_pay"] == 262.5 and q["confirmed_total"] == 262.5
    if cart["budget"]["over"]:      # the real bill takes this week over budget: never approved silently
        r = client.post(f"/api/session/{sid}/order/place", json={})
        assert r.status_code == 409 and r.get_json()["error"] == "over_budget"
    placed = client.post(f"/api/session/{sid}/order/place",
                         json={"over_budget_ok": cart["budget"]["over"]}).get_json()
    assert placed == {"mode": "tap_to_place", "placed": False, "url": swiggy_live.CHECKOUT_URL,
                      "message": "Your cart is ready in Swiggy. Tap to place it there."}
    assert "place_food_order" not in swiggy.tool_calls()


def test_c4_placement_needs_the_explicit_approval_of_that_exact_total(client, swiggy, monkeypatch):
    monkeypatch.setattr(config, "LIVE_ORDERS", True)
    pid, meal = _queued_dinner(client, swiggy)
    sid = meal["session_id"]
    r = client.post(f"/api/session/{sid}/order/place", json={})
    assert r.status_code == 400                                        # no cart checked yet
    preview = client.get(f"/api/session/{sid}/swiggy-cart/preview").get_json()
    cart = client.post(f"/api/session/{sid}/order/cart", json={"expected_fingerprint": preview["fingerprint"]}).get_json()
    assert cart["next"] == {"mode": "approve_and_place", "label": "Approve ₹262.50 and place"}
    assert client.post(f"/api/session/{sid}/order/place", json={}).status_code == 409   # no approval token
    assert "place_food_order" not in swiggy.tool_calls()
    approval = client.get("/api/user/3/swiggy/checkout/preview").get_json()
    assert approval["to_pay"] == 262.5 and approval["bill"]["to_pay"] == 262.5
    if cart["budget"]["over"]:      # the real bill takes this week over budget: never approved silently
        r = client.post(f"/api/session/{sid}/order/place", json={"expected_fingerprint": approval["fingerprint"]})
        assert r.status_code == 409 and r.get_json()["error"] == "over_budget"
        assert "place_food_order" not in swiggy.tool_calls()
    done = client.post(f"/api/session/{sid}/order/place", json={"expected_fingerprint": approval["fingerprint"],
                                                               "over_budget_ok": cart["budget"]["over"]}).get_json()
    assert done["mode"] == "placed" and done["order_id"] == "real-order-17"
    assert swiggy.tool_calls().count("place_food_order") == 1
    row = next(m for m in client.get(f"/api/plan/{pid}/order-queue").get_json()["meals"] if m["session_id"] == sid)
    assert row["state"] == "placed"


def test_c5_another_users_queue_is_private(client, swiggy):
    v = client.post("/api/profiles", json={"name": "P", "diet": "veg", "weekly_budget": 2000,
                                           "meals": ["dinner"], "favourites": [6]}).get_json()
    pid = v["plan"]["id"]
    assert client.get(f"/api/plan/{pid}/order-queue").status_code == 401
