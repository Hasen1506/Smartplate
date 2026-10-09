"""Meal pools and picking any dish nearby, against the stand-in Swiggy neighbourhood
(tests/swiggy_town.py). Nothing real is touched."""
import pytest

from smartplate import config, db
from smartplate.app import create_app
from smartplate.integrations import swiggy_connect
from swiggy_town import TownFake
from test_followups import _connect


@pytest.fixture
def town(gt, monkeypatch):
    monkeypatch.setattr(config, "SWIGGY_PROVIDER", "live")
    fake = TownFake()
    monkeypatch.setattr(swiggy_connect, "_http", fake)
    client = create_app().test_client()
    uid = client.post("/api/profiles", json={"name": "Priya", "diet": "veg", "weekly_budget": 3500,
        "rhythm": {"breakfast": "order", "lunch": "order", "dinner": "order"}, "cook": "never",
        "allergens": [], "medical": [], "favourites": []}).get_json()["user"]["id"]
    _connect(client, fake, uid=uid)
    assert client.post(f"/api/user/{uid}/swiggy/address", json={"address_id": "addr-home"}).status_code == 200
    plan = client.get(f"/api/user/{uid}/plan").get_json()["plan"]["id"]
    assert client.post(f"/api/plan/{plan}/live-menus", json={}).status_code == 200
    return {"c": client, "uid": uid, "plan": plan, "fake": fake}


def _cells(c, plan, meal):
    return [d["meals"][meal] for d in c.get(f"/api/plan/{plan}").get_json()["grid"]
            if meal in d["meals"] and d["meals"][meal]["status"] == "active"]


def _add(c, uid, meal, rid, item, name="x"):
    return c.post(f"/api/user/{uid}/pools", json={"meal": meal, "restaurant_id": rid,
                                                  "restaurant_name": name, "item_id": item})


def test_a_breakfast_pool_plans_every_breakfast_from_it(town):
    c, uid, plan = town["c"], town["uid"], town["plan"]
    r = _add(c, uid, "breakfast", "r-101", "r-101-i5")             # Ven Pongal
    assert r.status_code == 200, r.get_json()
    pools = r.get_json()["meals"]
    assert [d["name"] for d in pools["breakfast"]] == ["Ven Pongal"] and pools["breakfast"][0]["on_menu"]
    breakfasts = [x for x in _cells(c, plan, "breakfast") if x["kind"] == "delivery"]
    assert breakfasts and all(x["item"] == "Ven Pongal" and x["pooled"] for x in breakfasts)
    # a one-dish pool repeats every morning (not capped at twice a week)
    assert len(breakfasts) == len(_cells(c, plan, "breakfast")) > 2
    # its restaurant is now a saved place
    favs = c.get(f"/api/user/{uid}/swiggy/favourites").get_json()
    assert any(f["id"] == "r-101" for f in favs)


def test_a_dish_with_no_nutrition_template_is_planned_without_made_up_numbers(town):
    c, uid, plan = town["c"], town["uid"], town["plan"]
    assert _add(c, uid, "lunch", "r-103", "r-103-i0").status_code == 200        # Quinoa Veg Bowl: matches "bowl"
    assert _add(c, uid, "lunch", "r-102", "r-102-i5").status_code == 200        # Gobi Manchurian: no template
    lunches = [x for x in _cells(c, plan, "lunch") if x["kind"] == "delivery"]
    assert {x["item"] for x in lunches} <= {"Quinoa Veg Bowl", "Gobi Manchurian"}
    gobi = [x for x in lunches if x["item"] == "Gobi Manchurian"]
    assert gobi and all(x["nutrition"] == {} and x["nutrition_unknown"] for x in gobi)
    view = c.get(f"/api/plan/{plan}").get_json()
    assert view["nutrition"]["unknown_meals"] == len(gobi)
    # outside its pool, an un-estimated dish is never planned
    dinners = [x for x in _cells(c, plan, "dinner") if x["kind"] == "delivery"]
    assert all(x["item"] != "Gobi Manchurian" for x in dinners)


def test_drag_between_pools_and_remove(town):
    c, uid, plan = town["c"], town["uid"], town["plan"]
    _add(c, uid, "lunch", "r-101", "r-101-i7")                                   # South Indian Meals
    moved = c.post(f"/api/user/{uid}/pools", json={"meal": "dinner", "from_meal": "lunch",
                                                   "restaurant_id": "r-101", "item_id": "r-101-i7"}).get_json()
    assert moved["meals"]["lunch"] == [] and moved["meals"]["dinner"][0]["name"] == "South Indian Meals"
    calls = len(town["fake"].tool_calls())
    assert all(x["item"] == "South Indian Meals" for x in _cells(c, plan, "dinner") if x["kind"] == "delivery")
    assert len(town["fake"].tool_calls()) == calls                                # moving needs no Swiggy call
    gone = c.post(f"/api/user/{uid}/pools/remove", json={"meal": "dinner", "restaurant_id": "r-101",
                                                         "item_id": "r-101-i7"}).get_json()
    assert gone["meals"]["dinner"] == []


def test_pools_only_take_real_safe_dishes(town):
    c, uid = town["c"], town["uid"]
    assert _add(c, uid, "brunch", "r-101", "r-101-i0").status_code == 400
    missing = _add(c, uid, "lunch", "r-101", "r-101-i99")
    assert missing.status_code == 502 and "isn't on" in missing.get_json()["error"]
    nonveg = _add(c, uid, "lunch", "r-102", "r-102-i0")                          # Chicken Dum Biryani, veg profile
    assert nonveg.status_code in (400, 502)
    options = _add(c, uid, "lunch", "r-102", "r-102-i8")                         # Family Pack: has options
    assert options.status_code in (400, 502)
    view = c.get(f"/api/user/{uid}/pools").get_json()
    assert all(not v for v in view["meals"].values())


def test_have_any_nearby_dish_for_this_meal(town):
    c, uid, plan = town["c"], town["uid"], town["plan"]
    cell = _cells(c, plan, "dinner")[0]
    r = c.post(f"/api/session/{cell['session_id']}/choose-live",
               json={"restaurant_id": "r-104", "restaurant_name": "Punjab Da Dhaba", "item_id": "r-104-i1"})
    assert r.status_code == 200, r.get_json()
    picked = next(d["meals"]["dinner"] for d in r.get_json()["grid"]
                  if d["meals"].get("dinner", {}).get("session_id") == cell["session_id"])
    assert picked["item"] == "Dal Makhani" and picked["pinned"]
    bad = c.post(f"/api/session/{cell['session_id']}/choose-live",
                 json={"restaurant_id": "r-104", "restaurant_name": "Punjab Da Dhaba", "item_id": "nope"})
    assert bad.status_code == 502


def test_pools_are_private_exported_and_deleted_with_the_profile(town):
    c, uid = town["c"], town["uid"]
    _add(c, uid, "lunch", "r-101", "r-101-i7")
    other = create_app().test_client()
    assert other.get(f"/api/user/{uid}/pools").status_code in (401, 403)
    export = c.get(f"/api/user/{uid}/data.json").get_json()
    assert any(r["name"] == "South Indian Meals" for r in export["data"]["swiggy_meal_pools"])
    assert c.delete(f"/api/user/{uid}", json={"confirmation": "DELETE"}).status_code == 200
    with db.cursor() as cur:
        assert cur.execute("SELECT COUNT(*) AS n FROM swiggy_meal_pools WHERE user_id=?", (uid,)).fetchone()["n"] == 0


def test_restaurant_search_carries_cuisines(town):
    c, uid = town["c"], town["uid"]
    found = c.get(f"/api/user/{uid}/swiggy/restaurants?query=meals").get_json()["restaurants"]
    assert {"South Indian"} <= {x for r in found for x in r["cuisines"]}


def test_swiggy_side_sections_are_not_planned_as_a_meal_unless_pooled(town):
    c, uid, plan = town["c"], town["uid"], town["plan"]
    sides = {"Butter Naan", "Lassi", "Raita", "Filter Coffee", "Kesari", "Cold Pressed Juice"}
    planned = {x["item"] for m in ("breakfast", "lunch", "dinner") for x in _cells(c, plan, m)}
    assert not planned & sides
    _add(c, uid, "breakfast", "r-101", "r-101-i10")                              # Filter Coffee: their call
    assert {x["item"] for x in _cells(c, plan, "breakfast") if x["kind"] == "delivery"} == {"Filter Coffee"}


def test_today_view_reports_which_meals_have_pools(town):
    c, uid, plan = town["c"], town["uid"], town["plan"]
    assert c.get(f"/api/plan/{plan}").get_json()["pools"] == {}
    _add(c, uid, "dinner", "r-101", "r-101-i7")
    assert c.get(f"/api/plan/{plan}").get_json()["pools"] == {"dinner": 1}
