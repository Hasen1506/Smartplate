"""W7 · platform truth (grill #7, #17, #25, #34, #46, #48, #49) and the two W10 scenario
gaps (address change keeps out-of-area places; raw provider JSON shown to the user).

Deterministic: frozen clock, no network; Swiggy is tests/swiggy_town.py plus a few
addresses with address-dependent delivery defined here."""
from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys

import pytest

import swiggy_town
from swiggy_town import PLACES, TownFake
from test_swiggy_live import ok

from smartplate import config, db, providers
from smartplate.integrations import swiggy_history, swiggy_rules

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Which places deliver where: Minjur is thin (two places), OMR is dense.
ADDRESSES = {
    "addr-minjur": ("Home", "DVS Nagar, Minjur", ["r-101", "r-104"]),
    "addr-omr": ("Work", "OMR, Perungudi", ["r-101", "r-102", "r-103", "r-104", "r-105"]),
}
ORDERS = [   # newest first, as Swiggy sends them (fields per get_food_orders.md)
    {"orderId": "o-9", "restaurantId": "r-101", "restaurantName": "Annapoorna Tiffin Room",
     "orderTotal": "₹212", "orderStatus": "DELIVERED", "orderType": "REGULAR", "isActiveOrder": False,
     "orderedItems": "1 x Masala Dosa, 1 x Filter Coffee", "orderedTime": "2026-11-01T08:40:00+05:30",
     "actions": [{"type": "REORDER", "priority": 1, "title": "Reorder", "isEnabled": True, "reorderMeta": {
         "orderItems": [{"menu_item_id": "r-101-i1", "name": "Masala Dosa", "quantity": 1},
                        {"menu_item_id": "r-101-i10", "name": "Filter Coffee", "quantity": 1}]}}]},
    {"orderId": "o-8", "restaurantId": "r-102", "restaurantName": "Biryani Junction",
     "orderTotal": "₹330", "orderStatus": "DELIVERED", "orderType": "REGULAR", "isActiveOrder": False,
     "orderedItems": "1 x Veg Biryani", "orderedTime": "2026-10-30T20:10:00+05:30", "actions": []},
    {"orderId": "o-7", "restaurantId": "r-102", "restaurantName": "Biryani Junction",
     "orderTotal": "₹330", "orderStatus": "CANCELLED", "orderType": "REGULAR", "isActiveOrder": False,
     "orderedItems": "1 x Paneer Biryani", "orderedTime": "2026-10-29T20:10:00+05:30", "actions": []},
    {"orderId": "o-6", "restaurantId": "r-102", "restaurantName": "Biryani Junction",
     "orderTotal": "₹330", "orderStatus": "DELIVERED", "orderType": "REGULAR", "isActiveOrder": False,
     "orderedItems": "2 x Veg Biryani", "orderedTime": "2026-10-20T21:00:00+05:30", "actions": []},
    {"orderId": "o-5", "restaurantId": "r-105", "restaurantName": "Wok Street",
     "orderTotal": "₹190", "orderStatus": "DELIVERED", "orderType": "REGULAR", "isActiveOrder": False,
     "orderedItems": "1 x Veg Hakka Noodles", "orderedTime": "2026-08-01T23:30:00+05:30", "actions": []},
]


class AddressTown(TownFake):
    """TownFake with two addresses, address-dependent delivery and an order history."""

    def __init__(self):
        super().__init__()
        self.order_args = []

    def tool(self, name, args):
        if name == "get_addresses":
            rows = [{"id": aid, "addressTag": tag, "addressLine": line}
                    for aid, (tag, line, _) in ADDRESSES.items()]
            return ok({"addresses": rows, "pagination": {"page": 1, "hasMore": False}})
        if name == "get_food_orders":
            self.order_args.append(dict(args))
            return ok({"orders": ORDERS})
        near = ADDRESSES.get(args.get("addressId"), (None, None, list(PLACES)))[2]
        if name == "search_restaurants":
            reply = super().tool(name, args)
            data = reply["structuredContent"]["data"]
            data["restaurants"] = [r for r in data["restaurants"] if r["id"] in near]
            return reply
        if name in ("get_restaurant_menu", "search_menu"):
            rid = args.get("restaurantId") or args.get("restaurantIdOfAddedItem")
            if rid and rid not in near:
                return {"structuredContent": {"success": False, "error": {
                    "message": "This restaurant does not deliver to the selected address"}}, "isError": True}
        return super().tool(name, args)


@pytest.fixture
def town(gt, monkeypatch):
    from smartplate.app import create_app
    from smartplate.integrations import swiggy_connect
    fake = AddressTown()
    monkeypatch.setattr(swiggy_connect, "_http", fake)
    return {"client": create_app().test_client(), "fake": fake, "clock": gt}


def _dish(rid, dish):
    return next(f"{rid}-i{n}" for n, row in enumerate(PLACES[rid][6]) if row[0] == dish)


def _open(town, address="addr-omr"):
    from test_followups import _connect
    c = town["client"]
    r = c.post("/api/profiles", json={"name": "Karthik", "diet": "veg", "weekly_budget": 2500,   # W4 fees priced in
                                      "rhythm": {"breakfast": "skip", "lunch": "order", "dinner": "order"},
                                      "cook": "never", "allergens": [], "medical": [], "favourites": [],
                                      "consent": {"account": True}})
    assert r.status_code == 201, r.get_json()
    v = r.get_json()
    uid, plan = v["user"]["id"], v["plan"]["id"]
    _connect(c, town["fake"], uid=uid)
    assert c.post(f"/api/user/{uid}/swiggy/address", json={"address_id": address}).status_code == 200
    return c, uid, plan


# --------------------------------------------------------------------------- #
# #34 / #49 · one truthful Swiggy mode
# --------------------------------------------------------------------------- #
def test_mode_defaults_to_live_cart_and_follows_live_orders(monkeypatch):
    monkeypatch.setattr(config, "SWIGGY_MODE", "")
    monkeypatch.setattr(config, "LIVE_ORDERS", False)
    assert config.swiggy_mode() == "live_cart"
    assert config.swiggy_allows("live_read") and config.swiggy_allows("live_cart")
    assert not config.swiggy_allows("live_order")
    monkeypatch.setattr(config, "LIVE_ORDERS", True)
    assert config.swiggy_mode() == "live_order" and config.swiggy_allows("live_order")
    monkeypatch.setattr(config, "LIVE_ORDERS", False)
    for mode in ("demo", "live_read"):
        monkeypatch.setattr(config, "SWIGGY_MODE", mode)
        assert config.swiggy_mode() == mode
        assert not config.swiggy_allows("live_cart")


def _config_in_subprocess(env: dict, expr: str) -> subprocess.CompletedProcess:
    clean = {k: v for k, v in os.environ.items() if not k.startswith(("SMARTPLATE_", "RENDER"))}
    clean.pop("DATABASE_URL", None)
    return subprocess.run([sys.executable, "-c", f"from smartplate import config as c; print(repr(({expr})))"],
                          cwd=ROOT, env={**clean, **env}, capture_output=True, text=True, timeout=60)


def test_mode_env_is_validated_and_sets_live_orders():
    r = _config_in_subprocess({"SMARTPLATE_SWIGGY_MODE": "live_order"}, "c.swiggy_mode(), c.LIVE_ORDERS")
    assert r.stdout.strip() == "('live_order', True)", r.stderr
    r = _config_in_subprocess({"SMARTPLATE_SWIGGY_MODE": "live_read", "SMARTPLATE_LIVE_ORDERS": "on"},
                              "c.swiggy_mode(), c.LIVE_ORDERS")
    assert r.stdout.strip() == "('live_read', False)", r.stderr       # the mode wins over the old flag
    r = _config_in_subprocess({"SMARTPLATE_SWIGGY_MODE": "simulated"}, "c.swiggy_mode()")
    assert r.returncode != 0 and "SMARTPLATE_SWIGGY_MODE" in r.stderr


def test_whitelisted_render_callback_is_reported_approved():
    r = _config_in_subprocess({"RENDER_EXTERNAL_URL": "https://smartplate-xgxd.onrender.com"},
                              "c.swiggy_callback_url(), c.swiggy_redirect_approved()")
    assert r.stdout.strip() == "('https://smartplate-xgxd.onrender.com/swiggy/callback', True)", r.stderr
    r = _config_in_subprocess({"RENDER_EXTERNAL_URL": "https://someone-else.onrender.com"},
                              "c.swiggy_redirect_approved()")
    assert r.stdout.strip() == "False", r.stderr
    r = _config_in_subprocess({"RENDER_EXTERNAL_URL": "https://smartplate-xgxd.onrender.com",
                               "SMARTPLATE_SWIGGY_MODE": "demo"}, "c.swiggy_redirect_approved()")
    assert r.stdout.strip() == "False", r.stderr                       # demo never signs in


def test_meta_reports_the_mode_truthfully(gt, monkeypatch):
    from smartplate.app import create_app
    c = create_app().test_client()
    monkeypatch.setattr(config, "FIXTURE_DATA", False)       # production: no simulated saga possible
    monkeypatch.setattr(config, "SWIGGY_PROVIDER", "simulated")
    monkeypatch.setattr(config, "PUBLIC_URL", "https://smartplate-xgxd.onrender.com")
    m = c.get("/api/meta").get_json()
    assert m["swiggy_mode"] == "live_cart" and m["swiggy_provider"] == "live"      # was "simulated" (#34)
    assert m["swiggy_can"] == {"live_read": True, "live_cart": True, "live_order": False}
    assert m["swiggy_redirect_approved"] is True                                   # #49: whitelisted origin
    monkeypatch.setattr(config, "FIXTURE_DATA", True)
    assert c.get("/api/meta").get_json()["swiggy_provider"] == "simulated"         # test runs only
    keys = {p["key"]: p for p in c.get("/api/meta").get_json()["providers"]}
    assert keys["swiggy_food"]["wired"] and keys["swiggy_food"]["attribution"] == "Powered by Swiggy"


def test_read_only_mode_never_writes_the_cart_and_demo_never_connects(town, monkeypatch):
    from smartplate.integrations import swiggy_live
    c, uid, _ = _open(town)
    monkeypatch.setattr(config, "SWIGGY_MODE", "live_read")
    with pytest.raises(swiggy_live.SwiggyError) as exc:
        swiggy_live.call(uid, "update_food_cart", {})
    assert exc.value.code == "swiggy_read_only"
    assert "update_food_cart" not in town["fake"].tool_calls()
    swiggy_live.addresses(uid, fresh=True)                       # reads still work
    monkeypatch.setattr(config, "SWIGGY_MODE", "demo")
    r = c.post(f"/api/user/{uid}/swiggy/connect", json={})
    assert r.status_code == 403 and "demo" in r.get_json()["error"]


# --------------------------------------------------------------------------- #
# #48 · nothing pays without explicit approval
# --------------------------------------------------------------------------- #
def test_order_placement_needs_live_order_mode(town, monkeypatch):
    from smartplate.integrations import swiggy_live
    _, uid, _ = _open(town)
    monkeypatch.setattr(config, "LIVE_ORDERS", False)
    with pytest.raises(swiggy_live.SwiggyError):
        swiggy_live.call(uid, "place_food_order", {"addressId": "addr-omr", "paymentMethod": "Cash"})
    with pytest.raises(swiggy_live.SwiggyError):                 # and no approval fingerprint, no order
        swiggy_live.place_live_order(uid, None)
    assert "place_food_order" not in town["fake"].tool_calls()
    assert "approval" in swiggy_rules.BY_ID["payment"]["plain"]


# --------------------------------------------------------------------------- #
# W10 scenario gaps: plain errors, and the week re-checked on address change
# --------------------------------------------------------------------------- #
def test_provider_errors_are_plain_words():
    raw = {"success": False, "error": {"message": "This restaurant does not deliver to the selected address"}}
    assert swiggy_rules.plain_message(raw) == "This restaurant doesn't deliver to your selected address."
    assert swiggy_rules.plain_message('{"success": false, "x": 1}').startswith("Swiggy couldn't complete")
    assert swiggy_rules.plain_message({"success": False}).startswith("Swiggy couldn't complete")
    assert swiggy_rules.plain_message({"error": "Item unavailable"}) == "Item unavailable"
    assert swiggy_rules.classify("Swiggy said: Location is unserviceable") == "out_of_area"
    assert "cart" not in swiggy_rules.BY_ID["out_of_area"]["fix"].lower()


def test_out_of_area_place_gets_a_plain_409(town):
    c, uid, _ = _open(town, "addr-minjur")
    r = c.post(f"/api/user/{uid}/pools", json={"meal": "dinner", "restaurant_id": "r-102",
                                               "restaurant_name": PLACES["r-102"][0],
                                               "item_id": _dish("r-102", "Veg Biryani")})
    body = r.get_json()
    text = json.dumps(body)
    assert r.status_code == 409, body
    assert '{\\"success\\"' not in text and "clear the cart" not in text.lower()
    assert "doesn't deliver to your selected address" in body["error"] and body["rule"]["id"] == "out_of_area"


def test_address_change_drops_places_that_dont_deliver_there(town):
    c, uid, plan = _open(town, "addr-omr")
    # (Masala Dosa is a lunch here: since Oct 2026 a dosa is a fine dinner and, cheaper, would
    # take every dinner from Wok Street, which this test needs in the week.)
    for meal, rid, dish in (("lunch", "r-101", "South Indian Meals"), ("lunch", "r-101", "Masala Dosa"),
                            ("dinner", "r-105", "Veg Hakka Noodles")):
        r = c.post(f"/api/user/{uid}/pools", json={"meal": meal, "restaurant_id": rid,
                                                   "restaurant_name": PLACES[rid][0], "item_id": _dish(rid, dish)})
        assert r.status_code == 200, r.get_json()
    assert c.post(f"/api/plan/{plan}/live-menus", json={}).status_code == 200
    planned = {m["restaurant"] for d in c.get(f"/api/plan/{plan}").get_json()["grid"]
               for m in d["meals"].values() if m["kind"] == "delivery" and m["status"] == "active"}
    assert PLACES["r-105"][0] in planned                                         # Wok Street was in the week
    r = c.post(f"/api/user/{uid}/swiggy/address", json={"address_id": "addr-minjur"}).get_json()
    assert r["week"]["dropped"] == [PLACES["r-105"][0]]
    assert r["week"]["note"] == "Wok Street isn't on Swiggy's list for your new address, so it was taken out of your week."
    planned = {m["restaurant"] for d in c.get(f"/api/plan/{plan}").get_json()["grid"]
               for m in d["meals"].values() if m["kind"] == "delivery" and m["status"] == "active"}
    assert planned <= {PLACES["r-101"][0], PLACES["r-104"][0]}, planned
    # choosing the same address again does nothing to the week
    assert c.post(f"/api/user/{uid}/swiggy/address", json={"address_id": "addr-minjur"}).get_json()["week"] is None


# --------------------------------------------------------------------------- #
# #7 / #17 · favourites suggested from order history (explicit tap, minimal, deletable)
# --------------------------------------------------------------------------- #
def test_scoring_buckets_counts_recency_and_rating():
    now = dt.datetime(2026, 11, 2, 8, 0)
    rows = swiggy_history.score_orders(ORDERS, now=now, ratings={"r-102": 4.2})
    by = {(r["restaurant_id"], r["item_name"]): r for r in rows}
    assert ("r-102", "Paneer Biryani") not in by                       # cancelled order ignored
    biryani = by[("r-102", "Veg Biryani")]
    assert biryani["times"] == 2 and biryani["slot"] == "dinner" and biryani["rating"] == 4.2
    assert biryani["last_date"] == "2026-10-30"
    dosa = by[("r-101", "Masala Dosa")]
    assert dosa["slot"] == "breakfast" and dosa["menu_item_id"] == "r-101-i1" and dosa["rating"] is None
    assert by[("r-105", "Veg Hakka Noodles")]["slot"] == "late"
    assert rows[0]["item_name"] == "Veg Biryani"                        # ordered twice, recently, rated
    assert by[("r-105", "Veg Hakka Noodles")]["score"] < dosa["score"]  # old order decays
    assert swiggy_history.bucket(dt.datetime(2026, 1, 1, 12, 30)) == "lunch"
    assert swiggy_history._items({"orderedItems": "2 x Idli, Vada"}) == [
        {"name": "Idli", "menu_item_id": None}, {"name": "Vada", "menu_item_id": None}]


def test_history_import_only_on_explicit_consent_and_deletable(town):
    c, uid, _ = _open(town)
    v = c.get(f"/api/user/{uid}/favourites/suggested").get_json()
    assert v["imported"] is False and "last 15 Swiggy orders" in v["consent"]
    assert "get_food_orders" not in town["fake"].tool_calls()           # nothing read on open
    r = c.post(f"/api/user/{uid}/favourites/suggested", json={})
    assert r.status_code == 400 and "get_food_orders" not in town["fake"].tool_calls()
    v = c.post(f"/api/user/{uid}/favourites/suggested", json={"consent": True}).get_json()
    assert v["imported"] and v["attribution"] == "Powered by Swiggy"
    assert [x["restaurant_name"] for x in v["restaurants"]][0] == "Biryani Junction"
    assert {i["pool_meal"] for i in v["items"]} <= {"breakfast", "lunch", "dinner"}
    assert town["fake"].order_args == [{"addressId": "addr-omr"}]
    with db.cursor() as cur:
        rows = [dict(r) for r in cur.execute(
            "SELECT * FROM swiggy_history_suggestions WHERE user_id=?", (uid,)).fetchall()]
    stored = json.dumps(rows)
    assert rows and not {"order_id", "order_total", "address_id"} & set(rows[0])
    assert "o-9" not in stored and "212" not in stored and "Minjur" not in stored   # minimal fields only
    # nothing became a favourite on its own
    assert c.get(f"/api/user/{uid}/swiggy/favourites").get_json() == []
    v = c.delete(f"/api/user/{uid}/favourites/suggested", json={}).get_json()
    assert v["imported"] is False and v["items"] == []


def test_order_count_is_requested_when_swiggy_offers_it(town, monkeypatch):
    from smartplate.integrations import swiggy_live
    _, uid, _ = _open(town)
    real_tool = swiggy_live._tool

    def with_count(conn, name):
        tool = real_tool(conn, name)
        if name == "get_food_orders":
            tool = {**tool, "input_schema": {"type": "object", "properties": {
                "addressId": {"type": "string"}, "orderCount": {"type": "number"}}}}
        return tool
    monkeypatch.setattr(swiggy_live, "_tool", with_count)
    swiggy_history.fetch_orders(uid)
    assert town["fake"].order_args[-1] == {"addressId": "addr-omr", "orderCount": 15}


# --------------------------------------------------------------------------- #
# #25 / #46 · works without Swiggy: typed address + typed favourites
# --------------------------------------------------------------------------- #
def test_typed_address_and_favourites_work_without_swiggy(gt):
    from smartplate.app import create_app
    c = create_app().test_client()
    r = c.post("/api/profiles", json={"name": "Asha", "diet": "veg", "weekly_budget": 1200,
                                      "rhythm": {"breakfast": "skip", "lunch": "order", "dinner": "order"},
                                      "cook": "never", "allergens": [], "medical": [], "favourites": [],
                                      "consent": {"account": True}})
    uid = r.get_json()["user"]["id"]
    from test_followups import _secure_profile
    _secure_profile(c, uid)
    r = c.get(f"/api/user/{uid}/swiggy")
    assert r.get_json().get("connected") is False, r.get_json()
    a = c.put(f"/api/user/{uid}/manual-address", json={"label": "Home", "text": "DVS Nagar, Minjur"}).get_json()
    assert a["address"]["text"] == "DVS Nagar, Minjur"
    assert c.put(f"/api/user/{uid}/manual-address", json={"text": ""}).status_code == 400
    f = c.post(f"/api/user/{uid}/typed-favourites", json={"meal": "dinner", "restaurant_name": "Annapoorna",
                                                         "item_name": "Masala Dosa", "price": "120"}).get_json()
    f = c.post(f"/api/user/{uid}/typed-favourites", json={"meal": "dinner", "restaurant_name": "Annapoorna",
                                                         "item_name": "Masala Dosa", "price": 150,
                                                         "platform": "toing"}).get_json()
    dinner = f["meals"]["dinner"]
    assert [(d["platform"], d["price"]) for d in dinner] == [("swiggy", 120.0), ("toing", 150.0)]
    assert dinner[0]["handoff"]["url"].startswith("https://www.swiggy.com/")
    assert dinner[1]["handoff"]["url"] is None                  # Toing is never fetched or linked blindly
    assert f["address"]["label"] == "Home"
    assert c.post(f"/api/user/{uid}/typed-favourites", json={"meal": "tea", "restaurant_name": "x",
                                                            "item_name": "y"}).status_code == 400
    assert c.post(f"/api/user/{uid}/typed-favourites", json={"meal": "lunch", "restaurant_name": "x",
                                                            "item_name": "y", "price": -4}).status_code == 400
    f = c.delete(f"/api/user/{uid}/typed-favourites/{dinner[1]['id']}", json={}).get_json()
    assert f["count"] == 1
    assert c.delete(f"/api/user/{uid}/manual-address", json={}).get_json() == {"address": None}


# --------------------------------------------------------------------------- #
# #46 · provider seam
# --------------------------------------------------------------------------- #
def test_provider_seam_has_swiggy_wired_and_stubs_that_say_why(monkeypatch):
    keys = [p["key"] for p in providers.overview()]
    assert keys == ["swiggy_food", "manual", "swiggy_instamart", "toing", "zomato"]
    monkeypatch.setattr(config, "SWIGGY_MODE", "")
    monkeypatch.setattr(config, "LIVE_ORDERS", False)
    assert providers.get("swiggy_food").capabilities == {"read", "history", "cart"}
    monkeypatch.setattr(config, "LIVE_ORDERS", True)
    assert "order" in providers.get("swiggy_food").capabilities
    for key, word in (("zomato", "personal use"), ("toing", "typed"), ("swiggy_instamart", "Builders Club")):
        p = providers.get(key)
        assert not p.wired and word in p.note
        with pytest.raises(providers.ProviderUnavailable, match=p.label):
            p.order_history(1)
    with pytest.raises(providers.ProviderUnavailable):
        providers.get("dunzo")
    monkeypatch.setattr(config, "SWIGGY_MODE", "live_read")
    monkeypatch.setattr(config, "LIVE_ORDERS", False)
    with pytest.raises(providers.ProviderUnavailable):
        providers.get("swiggy_food").fill_cart(1, "r", "R", "i", "I", "fp")


def test_powered_by_swiggy_is_shown_with_swiggy_results_cart_and_order():
    js = open(os.path.join(ROOT, "smartplate", "static", "app.js"), encoding="utf-8").read()
    assert "Powered by Swiggy" in js
    for fn in ("function liveResultsBlock", "function liveOrderReviewDialog", "function checkoutReviewDialog"):
        body = js.split(fn, 1)[1].split("\nfunction ", 1)[0]
        assert "poweredBySwiggy()" in body, fn


def test_new_tables_are_exported_and_deleted_with_the_profile():
    from smartplate import profile_data
    for t in ("swiggy_history_suggestions", "typed_favourites", "manual_addresses"):
        assert t in profile_data.USER_TABLES
