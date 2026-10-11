"""Hungry now (a meal the week leaves out, ordered in two taps) and Swiggy's own dish
photos on planned dishes, against a fake Swiggy MCP server (nothing real is touched)."""
import datetime as dt

import pytest

from smartplate import config, db
from smartplate.app import create_app
from smartplate.integrations import swiggy_connect, swiggy_live
from test_followups import _connect
from test_swiggy_live import FakeLive

PHOTO = "https://media-assets.swiggy.com/swiggy/image/upload/fl_lossy/masala-dosa.jpg"


class PhotoFake(FakeLive):
    """search_menu shows a Swiggy CDN photo for Masala Dosa only (and one off-CDN URL)."""

    def tool(self, name, args):
        reply = super().tool(name, args)
        data = reply.get("structuredContent", {}).get("data") if isinstance(reply, dict) else None
        if name == "search_menu" and data:
            for item in data["items"]:
                if item["name"] == "Masala Dosa":
                    item["imageUrl"] = PHOTO
                if item["name"] == "Veg Meals":
                    item["imageUrl"] = "https://evil.example/meals.jpg"
        return reply


@pytest.fixture
def world(gt, monkeypatch):
    monkeypatch.setattr(config, "SWIGGY_PROVIDER", "live")
    fake = PhotoFake()
    fake.dishes = {"Masala Dosa": 12000, "Veg Meals": 18000, "Paneer Butter Masala": 22000}
    monkeypatch.setattr(swiggy_connect, "_http", fake)
    client = create_app().test_client()
    uid = client.post("/api/profiles", json={"name": "Arun", "diet": "veg", "weekly_budget": 3000,
        "rhythm": {"breakfast": "skip", "lunch": "order", "dinner": "order"}, "cook": "sometimes",
        "allergens": [], "medical": [], "favourites": []}).get_json()["user"]["id"]
    _connect(client, fake, uid=uid)
    assert client.post(f"/api/user/{uid}/swiggy/address", json={"address_id": "addr-home"}).status_code == 200
    assert client.post(f"/api/user/{uid}/swiggy/favourites", json={
        "restaurant_id": "r-1", "restaurant_name": "Hotel Saravana Bhavan (Adyar)"}).status_code == 200
    plan = client.get(f"/api/user/{uid}/plan").get_json()["plan"]["id"]
    assert client.post(f"/api/plan/{plan}/live-menus", json={}).status_code == 200
    return {"client": client, "uid": uid, "plan": plan, "fake": fake}


def _dish_ids(uid):
    with db.cursor() as cur:
        return {r["name"]: r["id"] for r in cur.execute(
            "SELECT m.name, m.id FROM menu_items m JOIN restaurants r ON r.id=m.restaurant_id WHERE r.city=?",
            (f"live:{uid}",))}


def test_hungry_now_adds_todays_left_out_breakfast_and_rebalances(world):
    c, plan = world["client"], world["plan"]
    before = c.get(f"/api/plan/{plan}").get_json()
    assert "breakfast" not in before["grid"][0]["meals"]              # Monday 08:00; the rhythm skips breakfast
    r = c.post(f"/api/plan/{plan}/eat-now", json={"meal": "breakfast"})
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    cell = body["plan"]["grid"][0]["meals"]["breakfast"]
    assert cell["session_id"] == body["session_id"] and cell["status"] == "active"
    # the fixture's 3-dish menu would leave breakfast skipped (variety cap): hungry keeps a dish
    assert cell["kind"] == "delivery" and cell["cost"] <= body["plan"]["budget"]["budget"]
    assert body["plan"]["next_up"]["meal"] == "breakfast" and body["plan"]["next_up"]["when"] == "Today"
    # a second tap reuses the same meal; nothing is duplicated
    again = c.post(f"/api/plan/{plan}/eat-now", json={"meal": "breakfast"}).get_json()
    assert again["session_id"] == body["session_id"]
    assert sum(1 for d in again["plan"]["grid"] if "breakfast" in d["meals"]) == 1


def test_hungry_now_brings_back_a_skipped_meal_and_refuses_past_or_bad_meals(world, gt):
    c, plan = world["client"], world["plan"]
    lunch = c.get(f"/api/plan/{plan}").get_json()["grid"][0]["meals"]["lunch"]["session_id"]
    assert c.post(f"/api/session/{lunch}/status", json={"status": "skipped"}).status_code == 200
    r = c.post(f"/api/plan/{plan}/eat-now", json={"meal": "lunch"}).get_json()
    assert r["session_id"] == lunch and r["plan"]["grid"][0]["meals"]["lunch"]["status"] == "active"
    assert c.post(f"/api/plan/{plan}/eat-now", json={"meal": "brunch"}).status_code == 400
    gt.set(dt.datetime(2026, 11, 2, 15, 0))                                    # lunch time + 90 min has passed
    late = c.post(f"/api/plan/{plan}/eat-now", json={"meal": "lunch"})
    assert late.status_code == 400 and "past lunch time" in late.get_json()["error"]


def test_dish_photos_come_from_swiggy_search_and_are_cached(world):
    c, uid = world["client"], world["uid"]
    ids = _dish_ids(uid)
    r = c.post(f"/api/user/{uid}/swiggy/photos", json={"item_ids": [ids["Masala Dosa"], ids["Veg Meals"]]})
    assert r.status_code == 200
    photos = r.get_json()["photos"]
    assert photos[str(ids["Masala Dosa"])] == PHOTO
    assert photos[str(ids["Veg Meals"])] is None                       # only Swiggy's own CDN is shown
    calls = len([x for x in world["fake"].tool_calls() if x == "search_menu"])
    again = c.post(f"/api/user/{uid}/swiggy/photos", json={"item_ids": [ids["Masala Dosa"], ids["Veg Meals"]]}).get_json()
    assert again["photos"] == photos                                    # hit and miss both cached
    assert len([x for x in world["fake"].tool_calls() if x == "search_menu"]) == calls
    # the plan and the Change sheet carry the photo once Swiggy has shown it
    view = c.get(f"/api/plan/{world['plan']}").get_json()
    cells = [m for d in view["grid"] for m in d["meals"].values() if m.get("item_id") == ids["Masala Dosa"]]
    assert all(m["image"] == PHOTO for m in cells)
    sid = next(m["session_id"] for d in view["grid"] for m in d["meals"].values() if m["kind"] == "delivery")
    opts = c.get(f"/api/session/{sid}/options").get_json()
    dishes = [x for g in opts["usual"] for x in g["dishes"]] + opts["new"]
    assert any(x["image"] == PHOTO for x in dishes if x["name"] == "Masala Dosa")


def test_photo_requests_are_bounded_and_ignore_other_profiles_dishes(world):
    c, uid = world["client"], world["uid"]
    assert c.post(f"/api/user/{uid}/swiggy/photos", json={"item_ids": list(range(13))}).status_code == 400
    assert c.post(f"/api/user/{uid}/swiggy/photos", json={"item_ids": "1"}).status_code == 400
    with db.cursor() as cur:
        other = cur.execute("SELECT id FROM menu_items WHERE source != 'live' LIMIT 1").fetchone()["id"]
    assert c.post(f"/api/user/{uid}/swiggy/photos", json={"item_ids": [other]}).get_json()["photos"] == {}


def test_photos_are_forgotten_on_disconnect_and_profile_delete(world):
    c, uid = world["client"], world["uid"]
    ids = _dish_ids(uid)
    c.post(f"/api/user/{uid}/swiggy/photos", json={"item_ids": [ids["Masala Dosa"]]})
    assert swiggy_live.known_photos(uid, [ids["Masala Dosa"]])
    assert c.post(f"/api/user/{uid}/swiggy/disconnect", json={}).status_code == 200
    with db.cursor() as cur:
        assert cur.execute("SELECT COUNT(*) AS n FROM swiggy_photos WHERE user_id=?", (uid,)).fetchone()["n"] == 0

