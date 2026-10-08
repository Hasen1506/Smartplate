"""Proposal 1 — the real Swiggy bill corrects the plan (each test failed on main: a checked
cart's total only showed on the order row; the week's budget, the planner and the plan's
delivery fee never learned from it, and an over-budget cart could be approved silently).

Swiggy is the Food MCP contract fake (tests/test_swiggy_live.FakeLive). Its cart replies use
the envelope recorded in tests/fixtures/swiggy_food_recording.json, with the fee lines from
tests/fixtures/swiggy_cart_bills.json. The clock is frozen (the `gt` world), so every plan
and every total below is deterministic.
"""
import copy
import json
import os

import pytest

from test_roadmap_b_live_menus import RECORDED_MENU
from test_swiggy_live import FakeLive, _connect
from smartplate import config, db
from smartplate.app import create_app
from smartplate.domain import allergens, live_catalog, models
from smartplate.integrations import swiggy_connect, swiggy_live

BILLS = json.load(open(os.path.join(os.path.dirname(__file__), "fixtures", "swiggy_cart_bills.json")))
PLACE = "Hotel Saravana Bhavan (Adyar)"


class BilledCart(FakeLive):
    """get_food_cart answers in the recorded envelope with one of the fixture bills."""

    bill = "itemised_rupees"

    def tool(self, name, args):
        reply = super().tool(name, args)
        if name == "get_food_cart" and self.cart is not None:
            live = reply["structuredContent"]["data"]["data"]
            out = copy.deepcopy(BILLS["envelope"])
            cart = out["structuredContent"]["data"]
            cart["addressId"] = reply["structuredContent"]["data"]["addressId"]
            cart["data"]["items"] = live["items"]
            cart["data"]["restaurant"] = live["restaurant"]
            spec = BILLS["bills"][self.bill]
            item = live["pricing"]["item_total"]
            fees = sum(spec["fees"].values())
            if spec["unit"] == "paise":
                cart["data"]["pricing"] = {"itemTotal": round(item * 100), **spec["fees"],
                                           "to_pay_in_paise": round(item * 100 + fees)}
            else:
                cart["data"]["pricing"] = {"item_total": item, **spec["fees"], "to_pay": round(item + fees, 2)}
            return out
        return reply


@pytest.fixture
def client(gt):
    app = create_app()
    app.config["TESTING"] = True
    return app.test_client()


@pytest.fixture
def swiggy(monkeypatch):
    fake = BilledCart()
    monkeypatch.setattr(swiggy_connect, "_http", fake)
    fake.dishes = dict(RECORDED_MENU)
    return fake


def _cells(view):
    return [c for d in view["grid"] for c in d["meals"].values()]


def _live_week(client, swiggy, budget=None, uid=3):
    """A connected user whose week is planned from the recorded live menu."""
    _connect(client, swiggy, uid=uid)
    client.post(f"/api/user/{uid}/swiggy/address", json={"address_id": "addr-home"})
    client.post(f"/api/user/{uid}/swiggy/favourites", json={"restaurant_id": "r-1", "restaurant_name": PLACE})
    pid = client.get(f"/api/user/{uid}/plan").get_json()["plan"]["id"]
    view = client.post(f"/api/plan/{pid}/live-menus", json={}).get_json()
    if budget is not None:
        view = client.patch(f"/api/user/{uid}/setup", json={"weekly_budget": budget}).get_json()
    return pid, view


def _check_cart(client, pid, sid):
    client.post(f"/api/plan/{pid}/order-queue", json={"meals": ["breakfast", "lunch", "dinner"], "days": list(range(7))})
    preview = client.get(f"/api/session/{sid}/swiggy-cart/preview").get_json()
    r = client.post(f"/api/session/{sid}/order/cart", json={"expected_fingerprint": preview["fingerprint"]})
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def test_bill_fee_is_read_in_rupees_from_every_recorded_shape():
    def cart(name, item=130.0):
        spec = BILLS["bills"][name]
        fees = sum(spec["fees"].values())
        if spec["unit"] == "paise":
            return {"pricing": {"itemTotal": item * 100, **spec["fees"], "to_pay_in_paise": item * 100 + fees}}
        return {"pricing": {"item_total": item, **spec["fees"], "to_pay": item + fees}}
    assert swiggy_live.billed_delivery_fee(cart("itemised_rupees"), 130) == 41
    assert swiggy_live.billed_delivery_fee(cart("itemised_paise"), 130) == 41      # paise read as rupees
    assert swiggy_live.billed_delivery_fee(cart("free_delivery"), 130) == 0         # free delivery is a real fee
    assert swiggy_live.billed_delivery_fee(cart("no_delivery_line"), 130) is None   # never guessed


def test_a_live_dish_says_estimated_until_a_real_bill_teaches_the_fee(client, swiggy):
    pid, view = _live_week(client, swiggy)
    live = [c for c in _cells(view) if c["kind"] == "delivery" and c["status"] == "active"]
    assert live and all(c["delivery_fee"] == {"amount": live_catalog.LIVE_DELIVERY_FEE_ESTIMATE, "estimated": True}
                        for c in live)
    assert any("estimate until a Swiggy bill" in r for r in live[0]["reasons"])
    cart = _check_cart(client, pid, live[0]["session_id"])
    dish_price = RECORDED_MENU[live[0]["item"]] / 100
    assert cart["to_pay"] == dish_price + 41 + 10 + 15 + 12.5
    # until the next plan, the week still says which fee it was priced with
    still = client.get(f"/api/plan/{pid}").get_json()
    assert all(c["delivery_fee"]["estimated"] for c in _cells(still) if c["kind"] == "delivery"
               and c["status"] == "active" and c["session_id"] != live[0]["session_id"])
    view = client.post(f"/api/plan/{pid}/optimize", json={}).get_json()
    others = [c for c in _cells(view) if c["kind"] == "delivery" and c["status"] == "active"
              and c["session_id"] != live[0]["session_id"]]
    assert others, "the rest of the week still has live deliveries"
    for c in others:
        assert c["delivery_fee"]["amount"] == 41 and c["delivery_fee"]["estimated"] is False
        # the next plan prices the dish at Swiggy's price plus the fee Swiggy billed
        assert c["cost"] == RECORDED_MENU[c["item"]] / 100 + 41, c
        assert any("from your last Swiggy bill" in r for r in c["reasons"]), c["reasons"]


def test_a_bill_without_a_delivery_line_teaches_nothing(client, swiggy):
    swiggy.bill = "no_delivery_line"
    pid, view = _live_week(client, swiggy)
    sid = next(c["session_id"] for c in _cells(view) if c["kind"] == "delivery" and c["status"] == "active")
    _check_cart(client, pid, sid)
    assert live_catalog.learned_fees(3) == {}
    view = client.get(f"/api/plan/{pid}").get_json()
    assert all(c["delivery_fee"]["estimated"] for c in _cells(view)
               if c["kind"] == "delivery" and c["session_id"] != sid and c["status"] == "active")


def test_the_checked_total_replaces_the_estimate_in_the_week(client, swiggy):
    pid, view = _live_week(client, swiggy)
    before = view["budget"]["spend"]
    cell = next(c for c in _cells(view) if c["kind"] == "delivery" and c["status"] == "active")
    cart = _check_cart(client, pid, cell["session_id"])
    after = client.get(f"/api/plan/{pid}").get_json()
    assert after["budget"]["spend"] == round(before - cell["cost"] + cart["to_pay"], 2)
    row = next(c for c in _cells(after) if c["session_id"] == cell["session_id"])
    assert row["cost"] == cart["to_pay"] and row["real_bill"] and row["planned_cost"] == cell["cost"]
    q = client.get(f"/api/plan/{pid}/order-queue").get_json()
    assert next(m for m in q["meals"] if m["session_id"] == cell["session_id"])["planned_cost"] == cell["cost"]
    assert cart["budget"]["over"] is False and cart["budget"]["week_total"] == after["budget"]["spend"]


# A ₹1,130 week leaves ₹20 unplanned; the real bill adds ₹43.50 to the planned dish.
TIGHT = 1130


def _over_budget_cart(client, swiggy):
    pid, view = _live_week(client, swiggy, budget=TIGHT)
    b = view["budget"]
    cell = next(c for c in _cells(view) if c["kind"] == "delivery" and c["status"] == "active")
    assert b["budget"] - b["spend"] < 43.5, b
    cart = _check_cart(client, pid, cell["session_id"])
    return pid, view, cell, cart


def test_an_over_budget_cart_warns_before_approval_and_is_never_placed_silently(client, swiggy, monkeypatch):
    monkeypatch.setattr(config, "LIVE_ORDERS", True)
    pid, view, cell, cart = _over_budget_cart(client, swiggy)
    b = cart["budget"]
    week_total = round(view["budget"]["spend"] - cell["cost"] + cart["to_pay"], 2)
    assert b["over"] is True and b["week_total"] == week_total
    assert b["over_by"] == round(week_total - TIGHT, 2)
    assert b["message"].startswith(f"This cart is ₹{cart['to_pay']:.2f}. It takes the week ₹{b['over_by']:.2f} over")
    assert [a["label"] for a in b["actions"]] == ["Approve anyway", "Re-plan the remaining meals"]
    sid = cell["session_id"]
    approval = client.get("/api/user/3/swiggy/checkout/preview").get_json()
    r = client.post(f"/api/session/{sid}/order/place", json={"expected_fingerprint": approval["fingerprint"]})
    assert r.status_code == 409 and r.get_json()["error"] == "over_budget"
    assert "place_food_order" not in swiggy.tool_calls()
    row = next(m for m in client.get(f"/api/plan/{pid}/order-queue").get_json()["meals"] if m["session_id"] == sid)
    assert row["state"] == "cart_ready"
    # "Approve anyway" is an explicit yes, and only then is the order placed
    approval = client.get("/api/user/3/swiggy/checkout/preview").get_json()
    done = client.post(f"/api/session/{sid}/order/place",
                       json={"expected_fingerprint": approval["fingerprint"], "over_budget_ok": True}).get_json()
    assert done["mode"] == "placed" and swiggy.tool_calls().count("place_food_order") == 1


def test_hand_off_mode_also_needs_the_over_budget_yes(client, swiggy):
    pid, view, cell, cart = _over_budget_cart(client, swiggy)
    sid = cell["session_id"]
    assert client.post(f"/api/session/{sid}/order/place", json={}).status_code == 409
    r = client.post(f"/api/session/{sid}/order/place", json={"over_budget_ok": True}).get_json()
    assert r["mode"] == "tap_to_place"


def test_replan_keeps_the_cart_at_its_real_total_and_fits_the_rest_in_the_money_left(client, swiggy):
    pid, view, cell, cart = _over_budget_cart(client, swiggy)
    r = client.post(f"/api/plan/{pid}/replan-remaining", json={"session_id": cell["session_id"]})
    assert r.status_code == 200, r.get_json()
    out = r.get_json()
    plan = out["plan"]
    kept = next(c for c in _cells(plan) if c["session_id"] == cell["session_id"])
    assert kept["item"] == cell["item"] and kept["cost"] == cart["to_pay"]
    assert any("In your Swiggy cart" in x for x in kept["reasons"])
    assert out["budget"]["over"] is False and plan["budget"]["spend"] <= plan["budget"]["budget"]
    # the meals already ordered or eaten are never re-planned; open meals keep the user's rules
    user = models.get_user(3)
    menu = {it["id"]: it for it in models.menu_for_user(user)}
    for c in _cells(plan):
        if c["kind"] == "delivery" and c["status"] == "active" and c["item_id"] in menu:
            assert not allergens.violates(user, menu[c["item_id"]]), c
    # the cart is still the one the user checked; nothing was placed
    assert "place_food_order" not in swiggy.tool_calls()


def test_replan_respects_an_allergy_added_after_the_cart_was_checked(client, swiggy):
    pid, view, cell, cart = _over_budget_cart(client, swiggy)
    carted_item = cell["item"]
    allergen = "peanut" if "Peanut" not in carted_item else "dairy"
    client.patch("/api/user/3/setup", json={"allergens": [allergen]})
    plan = client.post(f"/api/plan/{pid}/replan-remaining", json={}).get_json()["plan"]
    for c in _cells(plan):
        if c["kind"] == "delivery" and c["status"] == "active":
            assert allergen not in (live_catalog.estimate(c["item"], None) or {}).get("allergens", []), c


def test_the_single_meal_cart_also_keeps_the_bill_and_checks_the_budget(client, swiggy):
    pid, view = _live_week(client, swiggy, budget=TIGHT)
    cell = next(c for c in _cells(view) if c["kind"] == "delivery" and c["status"] == "active")
    preview = client.get(f"/api/session/{cell['session_id']}/swiggy-cart/preview").get_json()
    r = client.post(f"/api/session/{cell['session_id']}/swiggy-cart", json={"expected_fingerprint": preview["fingerprint"]})
    assert r.status_code == 200, r.get_json()
    out = r.get_json()
    assert out["budget"]["over"] is True and out["budget"]["cart_total"] == out["to_pay"]
    assert client.get(f"/api/plan/{pid}").get_json()["budget"]["spend"] == out["budget"]["week_total"]


def test_learned_fees_are_private_profile_data(client, swiggy):
    from smartplate import profile_data
    assert "swiggy_delivery_fees" in profile_data.USER_TABLES
