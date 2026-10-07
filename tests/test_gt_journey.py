"""Golden end-to-end journey through the HTTP API (the same calls the UI makes).

Asha signs up (vegetarian, peanut allergy, ₹2,500 a week, three meals), gets a week,
pins and moves meals, searches a real-shaped Swiggy restaurant and reviews a dish
(recorded MCP replies, tests/fixtures/swiggy_food_recording.json), reaches the
approval step for her week's orders, checks in after a meal, reads her expenses and
nutrition, and changes her settings. Every step is compared with a golden transcript
(tests/fixtures/golden/journey_asha.json) and checked against the invariant oracle.

The clock is frozen (Mon 2 Nov 2026 08:00 IST), the network is blocked, and no real
cart or order is ever touched: Swiggy is a replay, and plan orders use the simulated
provider only after an explicit approval of the exact total.
"""
import csv as csvlib
import datetime as dt
import io

import pytest

from gt_support import assert_golden, check_plan, connect_swiggy, plan_snapshot
from smartplate.app import create_app

ASHA = {"name": "Asha", "diet": "veg", "allergens": ["peanut"], "weekly_budget": 2500,
        "meals": ["breakfast", "lunch", "dinner"], "cook": "sometimes", "favourites": [6, 8, 9],
        "goal": "protein"}
PEANUT_ITEMS = {"Peanut Chikki Sweet", "Peanut Butter Toast"}


@pytest.fixture
def world(gt, swiggy_replay):
    app = create_app()
    return {"clock": gt, "client": app.test_client(), "fake": swiggy_replay}


def _cell(view, day, meal):
    return next(d for d in view["grid"] if d["day"] == day)["meals"][meal]


def _items(view):
    return {c["item"] for d in view["grid"] for c in d["meals"].values()}


def test_golden_journey_signup_to_settings(world):
    c, fake, clock = world["client"], world["fake"], world["clock"]
    transcript = {}

    # 1. sign-up / onboarding --------------------------------------------------
    r = c.post("/api/profiles", json=ASHA)
    assert r.status_code == 201, r.get_json()
    view = r.get_json()
    uid, pid, key = view["user"]["id"], view["plan"]["id"], view["access_key"]
    h = {"X-SmartPlate-Key": key}
    assert c.get(f"/api/user/{uid}/plan").status_code == 401            # private without the key
    assert not _items(view) & PEANUT_ITEMS
    assert not check_plan(pid)
    transcript["1_week_plan"] = plan_snapshot(view)

    # 2. the options sheet for Tuesday lunch: capped shortlist, unsafe dishes hidden
    tue_lunch = _cell(view, "Tue", "lunch")["session_id"]
    sheet = c.get(f"/api/session/{tue_lunch}/options", headers=h).get_json()
    transcript["2_tuesday_lunch_options"] = {
        "usual": [{"restaurant": g["restaurant"], "dishes": [[d["name"], d["price"], d["fits"]] for d in g["dishes"]]}
                  for g in sheet["usual"]],
        "new": [[d["name"], d["restaurant"], d["price"]] for d in sheet["new"]],
        "cook": [x["recipe_key"] for x in sheet["cook"]], "hidden": sheet["hidden"], "limits": sheet["limits"]}

    # 3. pin a meal; an unsafe pick is refused --------------------------------
    veg_meals = 14
    view = c.post(f"/api/session/{tue_lunch}/choose", json={"item_id": veg_meals}, headers=h).get_json()
    assert _cell(view, "Tue", "lunch")["item"] == "Veg Meals" and _cell(view, "Tue", "lunch")["pinned"]
    r = c.post(f"/api/session/{tue_lunch}/choose", json={"item_id": 9}, headers=h)      # Peanut Butter Toast
    assert r.status_code == 400 and "Not safe for you" in r.get_json()["error"]

    # 4. swap two meals, then re-plan: the pin survives --------------------------
    wed, thu = _cell(view, "Wed", "dinner"), _cell(view, "Thu", "dinner")
    view = c.post(f"/api/plan/{pid}/swap", json={"a": wed["session_id"], "b": thu["session_id"]}, headers=h).get_json()
    assert _cell(view, "Wed", "dinner")["item"] == thu["item"] and _cell(view, "Thu", "dinner")["item"] == wed["item"]
    view = c.post(f"/api/plan/{pid}/optimize", json={"mode": "survival"}, headers=h).get_json()
    assert _cell(view, "Tue", "lunch")["item"] == "Veg Meals"
    view = c.post(f"/api/plan/{pid}/optimize", json={"mode": "balanced"}, headers=h).get_json()
    assert not check_plan(pid)
    transcript["3_after_pin_swap_replan"] = plan_snapshot(view)

    # 5. restaurant search → menu → item review (recorded Swiggy) ---------------
    connect_swiggy(c, fake, uid, key)
    found = c.get(f"/api/user/{uid}/swiggy/restaurants", query_string={"query": "Hotel Saravana Bhavan"},
                  headers=h).get_json()
    place = found["restaurants"][0]
    menu = c.get(f"/api/user/{uid}/swiggy/live-menu", query_string={"restaurant_id": place["id"],
                 "restaurant_name": place["name"]}, headers=h).get_json()
    dishes = c.get(f"/api/user/{uid}/swiggy/dishes", query_string={"restaurant_id": place["id"],
                   "restaurant_name": place["name"], "query": "tiffin"}, headers=h).get_json()
    tiffin = next(i for i in dishes["items"] if i["name"] == "Mini Tiffin")
    review = c.post(f"/api/user/{uid}/swiggy/live-cart/preview", json={
        "restaurant_id": place["id"], "restaurant_name": place["name"],
        "item_id": tiffin["id"], "item_name": tiffin["name"]}, headers=h).get_json()
    # a declared allergy can't be verified from a Swiggy menu: review, never a cart
    assert review["orderable"] is False and review["fingerprint"] is None
    r = c.post(f"/api/user/{uid}/swiggy/live-cart", json={
        "restaurant_id": place["id"], "restaurant_name": place["name"], "item_id": tiffin["id"],
        "item_name": tiffin["name"], "expected_fingerprint": "guess"}, headers=h)
    assert r.status_code in (409, 502)
    assert "update_food_cart" not in fake.tool_calls() and "place_food_order" not in fake.tool_calls()
    transcript["4_live_search_and_review"] = {
        "restaurants": [[p["id"], p["name"]] for p in found["restaurants"]],
        "menu": [[i["name"], i["price"]] for i in menu["items"]],
        "dish_search": [[i["name"], i["price"], i.get("veg")] for i in dishes["items"]],
        "review": {k: review[k] for k in ("restaurant", "item", "menu_price", "orderable", "reason", "address")}}

    # 6. approval step: nothing is ordered until the exact total is approved ----
    preview = c.get(f"/api/plan/{pid}/execute/preview", headers=h).get_json()
    assert preview["total"] == round(sum(i["amount"] for i in preview["items"]), 2)
    r = c.post(f"/api/plan/{pid}/execute", json={}, headers=h)
    assert r.status_code == 409 and r.get_json()["error"] == "checkout_conflict"
    r = c.post(f"/api/plan/{pid}/execute", json={"expected_fingerprint": preview["fingerprint"],
                                                 "max_total": preview["total"] - 1}, headers=h)
    assert r.status_code == 409
    r = c.post(f"/api/plan/{pid}/execute", json={"expected_fingerprint": "0" * 24,
                                                 "max_total": preview["total"]}, headers=h)
    assert r.status_code == 409
    assert c.get(f"/api/plan/{pid}/orders", headers=h).get_json()["attempted"] == 0
    done = c.post(f"/api/plan/{pid}/execute", json={"expected_fingerprint": preview["fingerprint"],
                                                    "max_total": preview["total"]}, headers=h).get_json()
    assert done["attempted"] == preview["order_count"]
    orders = c.get(f"/api/plan/{pid}/orders", headers=h).get_json()
    transcript["5_approval"] = {"preview": {"order_count": preview["order_count"], "total": preview["total"],
                                            "items": [[i["day"], i["meal"], i["item"], i["amount"]] for i in preview["items"]]},
                                "placed": orders["placed"], "failed": orders["failed"],
                                "substituted": orders["substituted"]}

    # 7. after-meal check-in: rate breakfast, log a snack, confirm a cooked dinner
    clock.set(clock.at.replace(hour=10, minute=0))
    view = c.get(f"/api/plan/{pid}", headers=h).get_json()
    mon_bf = _cell(view, "Mon", "breakfast")
    rated = c.post(f"/api/session/{mon_bf['session_id']}/rate", json={"score": 1}, headers=h).get_json()
    snack = c.post(f"/api/user/{uid}/intake", json={"text": "2 idli and a filter coffee", "meal": "snack"},
                   headers=h).get_json()
    cook_day, cook = next(((i, cell) for i, d in enumerate(view["grid"]) for cell in d["meals"].values()
                           if cell["kind"] == "cook" and cell.get("status") == "active"), (None, None))
    if cook:
        clock.set(dt.datetime.combine(dt.date.fromisoformat(view["plan"]["week_start"]) + dt.timedelta(days=cook_day),
                                      dt.time(22, 0)))
        view = c.post(f"/api/session/{cook['session_id']}/confirm", json={}, headers=h).get_json()
    ledger = c.get(f"/api/user/{uid}/ledger", headers=h).get_json()
    transcript["6_check_in"] = {"rated": mon_bf["item"], "suggest_favourite": rated["suggest_favourite"],
                                "snack": {k: snack[k] for k in sorted(snack) if k != "entry_id"},
                                "cooked": cook["item"] if cook else None,
                                "days_logged": ledger["days_logged"], "protein": ledger["protein"]}

    # 8. expenses and insights ---------------------------------------------------
    c.post(f"/api/plan/{pid}/receipts", json={}, headers=h)
    rec = c.get(f"/api/receipts/{uid}", headers=h).get_json()
    placed_total = round(sum(o["cost"] for o in orders["results"] if o["placed"]), 2)
    assert rec["total"] == round(sum(x["amount"] for x in rec["rows"]), 2) == placed_total
    csv = c.get(f"/api/receipts/{uid}/export.csv", headers=h).get_data(as_text=True)
    lines = [row for row in csvlib.reader(io.StringIO(csv)) if row and row[0][:4].isdigit()]
    assert len(lines) == len(rec["rows"])
    assert round(sum(float(row[2]) for row in lines), 2) == rec["total"]
    transcript["7_expenses_insights"] = {"receipts_total": rec["total"], "rows": len(rec["rows"]),
                                         "nutrition_daily_avg": view["nutrition"]["daily_avg"],
                                         "nutrition_target": view["nutrition"]["daily_target"]}

    # 9. settings: tighter budget, a daily cap and a new allergy → only open meals re-plan
    before = {(d["day"], m): cell for d in view["grid"] for m, cell in d["meals"].items()}
    view = c.patch(f"/api/user/{uid}/setup", json={"weekly_budget": 1800, "daily_cap": 450,
                                                   "allergens": ["peanut", "dairy"]}, headers=h).get_json()
    after = {(d["day"], m): cell for d in view["grid"] for m, cell in d["meals"].items()}
    for slot, cell in before.items():
        if cell.get("status") in ("ordered", "confirmed"):
            assert after[slot]["item"] == cell["item"] and after[slot]["status"] == cell["status"], slot
    assert view["budget"]["daily_cap"] == 450 and view["user"]["allergens"] == ["dairy", "peanut"]
    assert not check_plan(pid)
    transcript["8_after_settings"] = plan_snapshot(view)

    assert "place_food_order" not in fake.tool_calls()
    assert_golden("journey_asha", transcript)
