"""One chosen delivery address for the Swiggy cart, live menus and planning (Oct 8, 2026).

Live finding on the deployed app: "Refresh Swiggy cart" kept answering "Swiggy returned the
cart for a different delivery address" even after an address was chosen, so no real bill
could be read; the user's current address was missing and could not be added or refreshed
from SmartPlate; and changing the address kept planning from the old address's menus.

Swiggy keeps one cart per account and its `get_food_cart` reply echoes the `addressId` that
cart is tied to (documented output field `addressId`). The fake below does exactly that: the
cart remembers the address of the last `update_food_cart`, and starts out tied to another of
the user's addresses. Addresses come back in the documented `get_addresses` shape
(`addressLine`, `addressTag`, `pagination`). Every test failed on main before this change.
No real account, cart or order is used.
"""
import pytest

from test_swiggy_live import FakeLive, _connect
from smartplate import db
from smartplate.app import create_app
from smartplate.domain import live_catalog
from smartplate.integrations import swiggy_connect, swiggy_live

MENU = {"Veg Meals": 18000, "Mini Tiffin": 12500}
ITEM = {"restaurant_id": "r-1", "restaurant_name": "Hotel Saravana Bhavan (Adyar)",
        "item_id": "m1", "item_name": "Mini Tiffin"}


class FakeAccountCart(FakeLive):
    """One Swiggy cart per account, tied to the address it was last updated for."""

    def __init__(self):
        super().__init__()
        self.saved = [{"id": "addr-home", "addressTag": "Home", "addressLine": "12 Lake View Rd, Adyar"},
                      {"id": "addr-work", "addressTag": "Work", "addressLine": "OMR, Perungudi"}]
        self.cart_address = "addr-work"           # the user last used Swiggy for Work
        self.keep_cart_address = False            # Swiggy ignores the addressId of an update
        self.updates = []

    def tool(self, name, args):
        if name == "get_addresses":
            return {"structuredContent": {"success": True, "data": {
                "addresses": [dict(a) for a in self.saved],
                "pagination": {"page": 1, "pageSize": 10, "total": len(self.saved), "totalPages": 1,
                               "hasMore": False}}}}
        if name == "update_food_cart":
            self.updates.append(args["addressId"])
            if not self.keep_cart_address:
                self.cart_address = args["addressId"]
        reply = super().tool(name, args)
        if name == "get_food_cart":
            reply["structuredContent"]["data"]["addressId"] = self.cart_address
        return reply


@pytest.fixture
def client(seeded):
    app = create_app()
    app.config["TESTING"] = True
    return app.test_client()


@pytest.fixture
def swiggy(monkeypatch):
    fake = FakeAccountCart()
    fake.dishes = dict(MENU)
    monkeypatch.setattr(swiggy_connect, "_http", fake)
    return fake


def _home_user(client, swiggy, uid=3):
    _connect(client, swiggy, uid=uid)
    r = client.post(f"/api/user/{uid}/swiggy/address", json={"address_id": "addr-home"})
    assert r.status_code == 200, r.get_json()


def _add_item(client, uid=3):
    preview = client.post(f"/api/user/{uid}/swiggy/live-cart/preview", json=ITEM).get_json()
    return client.post(f"/api/user/{uid}/swiggy/live-cart", json={**ITEM, "expected_fingerprint": preview["fingerprint"]})


# --------------------------------------------------------------------------- #
# The cart follows the chosen address
# --------------------------------------------------------------------------- #
def test_empty_cart_tied_to_another_address_is_empty_not_an_error(client, swiggy):
    _home_user(client, swiggy)
    r = client.get("/api/user/3/swiggy/live-cart")
    assert r.status_code == 200, r.get_json()                    # main: 502 "different delivery address"
    body = r.get_json()
    assert body["empty"] is True and body["cart"] is None and body["address"].startswith("Home")


def test_add_to_cart_uses_the_chosen_address_and_reads_the_real_bill(client, swiggy):
    _home_user(client, swiggy)
    r = _add_item(client)
    assert r.status_code == 200, r.get_json()                    # main: 502 before anything was added
    added = r.get_json()
    assert swiggy.updates == ["addr-home"] and swiggy.cart_address == "addr-home"
    assert added["to_pay"] == 160.0 and added["bill"]
    # the delivery fee is learned for the chosen address, not the one the cart was tied to
    assert live_catalog.learned_fees(3) == {"r-1": {"fee": 35.0, "seen": live_catalog.learned_fees(3)["r-1"]["seen"]}}
    with db.cursor() as cur:
        fees = cur.execute("SELECT address_id FROM swiggy_delivery_fees WHERE user_id=3").fetchall()
    assert [f["address_id"] for f in fees] == ["addr-home"]
    cart = client.get("/api/user/3/swiggy/live-cart").get_json()["cart"]
    assert cart["orderable"] is True and cart["other_address"] is None and cart["to_pay"] == 160.0


def test_cart_with_items_for_another_address_says_which_and_offers_it(client, swiggy):
    _home_user(client, swiggy)
    swiggy.cart = "m0"                                            # Veg Meals, put in the cart for Work
    r = client.get("/api/user/3/swiggy/live-cart")
    assert r.status_code == 200, r.get_json()                    # main: 502
    cart = r.get_json()["cart"]
    assert cart["other_address"] == {"id": "addr-work", "label": "Work · OMR, Perungudi"}
    assert cart["orderable"] is False and cart["item"] == "Veg Meals"
    blocked = _add_item(client)
    assert blocked.status_code == 409 and blocked.get_json()["code"] == "swiggy_cart_other_address"
    assert "Work · OMR, Perungudi" in blocked.get_json()["error"] and swiggy.updates == []
    # "Deliver there instead" chooses that address, and the cart is then verified for it
    client.post("/api/user/3/swiggy/address", json={"address_id": "addr-work"})
    again = client.get("/api/user/3/swiggy/live-cart").get_json()["cart"]
    assert again["other_address"] is None


def test_bill_is_refused_when_swiggy_keeps_the_cart_on_another_address(client, swiggy):
    _home_user(client, swiggy)
    swiggy.keep_cart_address = True
    r = _add_item(client)
    assert r.status_code == 409 and r.get_json()["code"] == "swiggy_cart_other_address"
    assert "Work" in r.get_json()["error"]
    assert live_catalog.learned_fees(3) == {}                     # a bill for Work never teaches Home's fee
    with db.cursor() as cur:
        assert cur.execute("SELECT 1 FROM swiggy_cart_intents WHERE user_id=3").fetchone() is None


def test_cart_tied_to_an_unknown_address_is_named_as_such(client, swiggy):
    _home_user(client, swiggy)
    swiggy.cart, swiggy.cart_address = "m0", "addr-deleted"
    cart = client.get("/api/user/3/swiggy/live-cart").get_json()["cart"]
    assert cart["other_address"] == {"id": None, "label": "an address that isn't in your Swiggy list"}


def test_unconfirmed_empty_reply_is_still_never_a_base_to_add_to(client, swiggy, monkeypatch):
    _home_user(client, swiggy)
    original = swiggy.tool
    monkeypatch.setattr(swiggy, "tool", lambda name, args: {"structuredContent": {"success": True, "data": {}}}
                        if name == "get_food_cart" else original(name, args))
    assert _add_item(client).status_code == 502 and swiggy.updates == []


def test_cart_view_reports_the_address_a_cart_is_for():
    other = swiggy_live._cart_view({"success": True, "data": {"addressId": "addr-work", "data": {"items": []}}},
                                   "addr-home")
    assert other["items"] == [] and other["address_verified"] is False
    assert other["cart_address_id"] == "addr-work" and other["empty_confirmed"] is True
    mine = swiggy_live._cart_view({"success": True, "data": {"addressId": "addr-home"}}, "addr-home")
    assert mine["address_verified"] is True and mine["cart_address_id"] is None
    text = swiggy_live._cart_view({"text": "Your cart is empty"}, "addr-home")
    assert text["empty_confirmed"] is False and text["address_verified"] is False


# --------------------------------------------------------------------------- #
# Address not listed? Add it in Swiggy, then Refresh addresses
# --------------------------------------------------------------------------- #
def test_refresh_shows_an_address_just_added_in_swiggy(client, swiggy):
    _home_user(client, swiggy)
    assert [a["id"] for a in client.get("/api/user/3/swiggy/addresses").get_json()] == ["addr-home", "addr-work"]
    swiggy.saved.insert(0, {"id": "addr-minjur", "addressTag": "Other",
                            "addressLine": "No.4, Venkateshwara Avenue, DVS Nagar, Minjur, Chennai 601203"})
    cached = client.get("/api/user/3/swiggy/addresses").get_json()
    assert "addr-minjur" not in [a["id"] for a in cached]        # the plain list is briefly cached
    r = client.post("/api/user/3/swiggy/addresses/refresh", json={})
    assert r.status_code == 200, r.get_json()                    # main: 404, no refresh
    body = r.get_json()
    assert [a["id"] for a in body["addresses"]] == ["addr-minjur", "addr-home", "addr-work"]
    assert [a["chosen"] for a in body["addresses"]] == [False, True, False] and body["dropped"] is None
    chosen = client.post("/api/user/3/swiggy/address", json={"address_id": "addr-minjur"}).get_json()
    assert chosen["address"]["id"] == "addr-minjur" and "Minjur" in chosen["address"]["label"]


def test_refresh_drops_a_default_that_is_gone_from_swiggy(client, swiggy):
    _home_user(client, swiggy)
    client.get("/api/user/3/swiggy/live-menu?restaurant_id=r-1&restaurant_name=Hotel%20Saravana%20Bhavan%20%28Adyar%29")
    swiggy.saved = [a for a in swiggy.saved if a["id"] != "addr-home"]
    body = client.post("/api/user/3/swiggy/addresses/refresh", json={}).get_json()
    assert body["dropped"].startswith("Home") and body["status"]["address"] is None
    assert [a["chosen"] for a in body["addresses"]] == [False]
    with db.cursor() as cur:
        assert cur.execute("SELECT 1 FROM swiggy_menus WHERE user_id=3").fetchone() is None
    r = client.get("/api/user/3/swiggy/live-cart")
    assert r.status_code == 502 and "Choose a delivery address" in r.get_json()["error"]


def test_refresh_updates_a_label_swiggy_changed(client, swiggy):
    _home_user(client, swiggy)
    swiggy.saved[0]["addressLine"] = "14 Lake View Rd, Adyar"
    body = client.post("/api/user/3/swiggy/addresses/refresh", json={}).get_json()
    assert body["status"]["address"] == {"id": "addr-home", "label": "Home · 14 Lake View Rd, Adyar"}


def test_choosing_an_address_not_in_swiggy_says_to_refresh(client, swiggy):
    _home_user(client, swiggy)
    r = client.post("/api/user/3/swiggy/address", json={"address_id": "addr-nowhere"})
    assert r.status_code == 400 and "Refresh addresses" in r.get_json()["error"]


# --------------------------------------------------------------------------- #
# Planning follows the chosen address
# --------------------------------------------------------------------------- #
def test_changing_address_stops_planning_from_the_old_address_menus(client, swiggy):
    _home_user(client, swiggy)
    client.post("/api/user/3/swiggy/favourites",
                json={"restaurant_id": "r-1", "restaurant_name": "Hotel Saravana Bhavan (Adyar)"})
    pid = client.get("/api/user/3/plan").get_json()["plan"]["id"]
    view = client.post(f"/api/plan/{pid}/live-menus", json={}).get_json()
    assert view["source"]["kind"] == "live" and live_catalog.has_live(3)
    with db.cursor() as cur:
        assert cur.execute("SELECT address_id FROM live_catalog_state WHERE user_id=3").fetchone()[0] == "addr-home"
    client.post("/api/user/3/swiggy/address", json={"address_id": "addr-work"})
    assert not live_catalog.has_live(3)                           # main: still planned from Adyar's menus
    source = client.get(f"/api/plan/{pid}").get_json()["source"]
    assert source["kind"] == "sample" and source["connected"] is True


def test_live_catalogue_read_for_another_address_is_never_used(client, swiggy):
    _home_user(client, swiggy)
    client.post("/api/user/3/swiggy/favourites",
                json={"restaurant_id": "r-1", "restaurant_name": "Hotel Saravana Bhavan (Adyar)"})
    pid = client.get("/api/user/3/plan").get_json()["plan"]["id"]
    client.post(f"/api/plan/{pid}/live-menus", json={})
    with db.cursor() as cur:                                      # e.g. a catalogue left from an old sign-in
        cur.execute("UPDATE live_catalog_state SET address_id='addr-old' WHERE user_id=3")
    assert not live_catalog.has_live(3)
    source = live_catalog.source_for(3)
    assert source["kind"] == "sample" and source["stale_address"] is True
    assert "another delivery address" in source["note"]


def test_choosing_the_same_address_again_keeps_the_live_catalogue(client, swiggy):
    _home_user(client, swiggy)
    client.post("/api/user/3/swiggy/favourites",
                json={"restaurant_id": "r-1", "restaurant_name": "Hotel Saravana Bhavan (Adyar)"})
    pid = client.get("/api/user/3/plan").get_json()["plan"]["id"]
    client.post(f"/api/plan/{pid}/live-menus", json={})
    client.post("/api/user/3/swiggy/address", json={"address_id": "addr-home"})
    assert live_catalog.has_live(3)
