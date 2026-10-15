"""UPI checkout through Swiggy's Payment stage (R2 item 8), against fakes only.

get_payment_options → place_food_order(intentApp | generateUPIQR) → PENDING_PAYMENT →
check_payment_status → confirm_order(orderId, addressId, lat, lng) → PLACED. Only in mode
live_order, only for the exact cart fingerprint + total the person approved, and "Pay in
Swiggy" (a plain message + Swiggy's checkout link) whenever the stage isn't available.
"""
import pytest

import test_followups
from smartplate import config, db
from test_swiggy_live import SCHEMAS, FakeLive, _connect, client  # noqa: F401
from smartplate.integrations import swiggy_connect

BODY = {"restaurant_id": "r-1", "restaurant_name": "Hotel Saravana Bhavan (Adyar)",
        "item_id": "m0", "item_name": "Mini Tiffin"}


class FakeUPI(FakeLive):
    def __init__(self):
        super().__init__()
        self.pay_state, self.confirm, self.upi_offered, self.placed_args = "PENDING", "PLACED", True, []

    def tool(self, name, args):
        if name == "get_addresses":
            reply = super().tool(name, args)
            for row in reply["structuredContent"]["data"]["addresses"]:
                row.update(lat=13.0067, lng=80.2570)
            return reply
        if name == "get_payment_options":
            data = {"cod": {"available": True, "id": "Cash", "displayName": "Cash on Delivery"}}
            if self.upi_offered:
                data["upi"] = {"available": True, "id": "UPI", "displayName": "UPI", "apps": ["gpay", "phonepe"]}
            return {"structuredContent": {"success": True, "data": data}}
        if name == "place_food_order" and args.get("paymentMethod") == "UPI":
            self.placed_args.append(args)
            return {"structuredContent": {"success": True, "data": {
                "orderId": "upi-order-5", "status": "PENDING_PAYMENT", "intentUrl": "upi://pay?pa=swiggy@x&am=160",
                "secretInternal": "never shown"}}}
        if name == "check_payment_status":
            assert args == {"orderId": "upi-order-5"}
            return {"structuredContent": {"success": True, "data": {"paymentStatus": self.pay_state}}}
        if name == "confirm_order":
            assert args == {"orderId": "upi-order-5", "addressId": "addr-home", "lat": 13.0067, "lng": 80.257}
            return {"structuredContent": {"success": True, "data": {"status": self.confirm}}}
        return super().tool(name, args)


UPI_SCHEMAS = {
    "place_food_order": {"type": "object", "required": ["addressId", "paymentMethod"], "properties": {
        "addressId": {"type": "string"}, "paymentMethod": {"type": "string"}, "intentApp": {"type": "string"},
        "generateUPIQR": {"type": "boolean"}}},
    "check_payment_status": {"type": "object", "required": ["orderId"], "properties": {"orderId": {"type": "string"}}},
    "confirm_order": {"type": "object", "required": ["orderId", "addressId", "lat", "lng"], "properties": {
        "orderId": {"type": "string"}, "addressId": {"type": "string"}, "lat": {"type": "number"},
        "lng": {"type": "number"}}},
}


@pytest.fixture
def upi(monkeypatch):
    fake = FakeUPI()
    monkeypatch.setattr(swiggy_connect, "_http", fake)
    monkeypatch.setattr(test_followups, "TOOLS", test_followups.TOOLS + ["check_payment_status", "confirm_order"])
    for name, schema in UPI_SCHEMAS.items():
        monkeypatch.setitem(SCHEMAS, name, schema)
    return fake


def _carted(client, fake, monkeypatch, live=True):
    monkeypatch.setattr(config, "LIVE_ORDERS", live)
    _connect(client, fake, uid=3)
    client.post("/api/user/3/swiggy/address", json={"address_id": "addr-home"})
    fake.dishes = {"Mini Tiffin": 12500}
    p = client.post("/api/user/3/swiggy/live-cart/preview", json=BODY).get_json()
    assert client.post("/api/user/3/swiggy/live-cart", json={**BODY, "expected_fingerprint": p["fingerprint"]}).status_code == 200


def test_upi_order_needs_approval_of_the_exact_cart_then_confirms_after_payment(client, upi, monkeypatch):  # noqa: F811
    _carted(client, upi, monkeypatch)
    review = client.get("/api/user/3/swiggy/checkout/preview?payment=upi&app=gpay").get_json()
    assert review["mode"] == "upi" and review["to_pay"] == 160 and review["payment_method"] == "UPI"
    assert review["upi_app"] == "gpay" and review["pays"] is False and "never" in review["how"]
    assert "place_food_order" not in upi.tool_calls()                      # reviewing places nothing
    assert client.post("/api/user/3/swiggy/checkout", json={}).status_code == 409
    assert client.post("/api/user/3/swiggy/checkout", json={"expected_fingerprint": "upi.gpay.forged"}).status_code == 409
    r = client.post("/api/user/3/swiggy/checkout", json={"expected_fingerprint": review["fingerprint"]})
    assert r.status_code == 200, r.get_json()
    out = r.get_json()
    assert out["status"] == "pending_payment" and out["order_id"] == "upi-order-5"
    assert out["pay"] == {"intent_url": "upi://pay?pa=swiggy@x&am=160"} and "never sees" in out["message"]
    assert "secretInternal" not in str(out)
    assert upi.placed_args == [{"addressId": "addr-home", "paymentMethod": "UPI", "intentApp": "gpay"}]
    # the approval is spent, and a pending UPI order blocks another review
    assert client.post("/api/user/3/swiggy/checkout", json={"expected_fingerprint": review["fingerprint"]}).status_code == 409
    assert client.get("/api/user/3/swiggy/checkout/preview").status_code == 502
    # still waiting in the person's UPI app
    st = client.post("/api/user/3/swiggy/orders/upi-order-5/payment", json={}).get_json()
    assert st["status"] == "pending_payment" and "confirm_order" not in upi.tool_calls()
    upi.pay_state = "SUCCESS"
    st = client.post("/api/user/3/swiggy/orders/upi-order-5/payment", json={}).get_json()
    assert st["status"] == "confirmed" and st["to_pay"] == 160
    assert upi.tool_calls().count("confirm_order") == 1 and upi.tool_calls().count("place_food_order") == 1
    with db.cursor() as cur:
        assert cur.execute("SELECT state FROM swiggy_order_attempts WHERE order_id='upi-order-5'").fetchone()["state"] == "confirmed"
    assert client.post("/api/user/3/swiggy/orders/upi-order-5/payment", json={}).get_json()["status"] == "confirmed"
    assert upi.tool_calls().count("confirm_order") == 1                      # never confirmed twice


def test_upi_without_an_app_uses_swiggys_qr(client, upi, monkeypatch):  # noqa: F811
    _carted(client, upi, monkeypatch)
    review = client.get("/api/user/3/swiggy/checkout/preview?payment=upi").get_json()
    assert review["upi_app"] is None and "QR" in review["payment_label"]
    client.post("/api/user/3/swiggy/checkout", json={"expected_fingerprint": review["fingerprint"]})
    assert upi.placed_args == [{"addressId": "addr-home", "paymentMethod": "UPI", "generateUPIQR": True}]
    assert client.get("/api/user/3/swiggy/checkout/preview?payment=upi&app=gpay").status_code == 502  # pending blocks


def test_an_unknown_upi_app_is_refused(client, upi, monkeypatch):  # noqa: F811
    _carted(client, upi, monkeypatch)
    r = client.get("/api/user/3/swiggy/checkout/preview?payment=upi&app=bhim")
    assert r.status_code == 400 and "UPI apps Swiggy offers" in r.get_json()["error"]


def test_a_failed_payment_orders_nothing_and_frees_the_next_try(client, upi, monkeypatch):  # noqa: F811
    _carted(client, upi, monkeypatch)
    review = client.get("/api/user/3/swiggy/checkout/preview?payment=upi&app=phonepe").get_json()
    client.post("/api/user/3/swiggy/checkout", json={"expected_fingerprint": review["fingerprint"]})
    upi.pay_state = "FAILED"
    st = client.post("/api/user/3/swiggy/orders/upi-order-5/payment", json={}).get_json()
    assert st["status"] == "payment_failed" and "nothing was ordered" in st["message"]
    assert "confirm_order" not in upi.tool_calls()
    assert client.get("/api/user/3/swiggy/checkout/preview?payment=upi&app=gpay").status_code == 200


def test_a_changed_cart_total_needs_a_new_approval(client, upi, monkeypatch):  # noqa: F811
    _carted(client, upi, monkeypatch)
    review = client.get("/api/user/3/swiggy/checkout/preview?payment=upi&app=gpay").get_json()
    upi.dishes = {"Mini Tiffin": 13500}                                      # ₹10 more in Swiggy's cart
    r = client.post("/api/user/3/swiggy/checkout", json={"expected_fingerprint": review["fingerprint"]})
    assert r.status_code == 409 and "place_food_order" not in upi.tool_calls()


@pytest.mark.parametrize("why", ["tools", "schema", "no_upi"])
def test_pay_in_swiggy_when_the_payment_stage_is_not_available(client, upi, monkeypatch, why):  # noqa: F811
    if why == "tools":
        monkeypatch.setattr(test_followups, "TOOLS", [t for t in test_followups.TOOLS if t != "confirm_order"])
    if why == "schema":
        monkeypatch.setitem(SCHEMAS, "place_food_order", {"type": "object", "required": ["addressId", "paymentMethod"],
                            "properties": {"addressId": {"type": "string"}, "paymentMethod": {"type": "string"}}})
    if why == "no_upi":
        upi.upi_offered = False
    _carted(client, upi, monkeypatch)
    r = client.get("/api/user/3/swiggy/checkout/preview?payment=upi&app=gpay")
    assert r.status_code == 200
    out = r.get_json()
    assert out["mode"] == "pay_in_swiggy" and out["label"] == "Pay in Swiggy" and out["upi"] is False
    assert out["url"] == "https://www.swiggy.com/checkout" and "pay there" in out["message"]
    assert "fingerprint" not in out and "place_food_order" not in upi.tool_calls()


@pytest.mark.parametrize("mode", ["demo", "live_read", "live_cart"])
def test_off_in_every_mode_below_live_order(client, upi, monkeypatch, mode):  # noqa: F811
    _carted(client, upi, monkeypatch, live=True)
    monkeypatch.setattr(config, "LIVE_ORDERS", False)
    monkeypatch.setattr(config, "SWIGGY_MODE", mode)
    r = client.get("/api/user/3/swiggy/checkout/preview?payment=upi&app=gpay")
    assert r.status_code == 200 and r.get_json()["mode"] == "pay_in_swiggy"
    assert "off on this server" in r.get_json()["message"]
    from smartplate.integrations import swiggy_live
    with pytest.raises(swiggy_connect.SwiggyError):
        swiggy_live.call(3, "confirm_order", {})
    assert "place_food_order" not in upi.tool_calls() and "confirm_order" not in upi.tool_calls()


def test_cod_checkout_is_unchanged_with_the_payment_stage_on(client, upi, monkeypatch):  # noqa: F811
    _carted(client, upi, monkeypatch)
    review = client.get("/api/user/3/swiggy/checkout/preview").get_json()
    assert review["payment_method"] == "Cash" and not review["fingerprint"].startswith("upi.")
    r = client.post("/api/user/3/swiggy/checkout", json={"expected_fingerprint": review["fingerprint"]})
    assert r.status_code == 200 and r.get_json()["order_id"] == "real-order-17"
