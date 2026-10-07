"""Roadmap PR B — the planner plans from live Swiggy menus when connected (each test
failed before: the plan was built only from the seeded sample catalogue, and nothing in
the plan said so).

The Swiggy side is the recorded Food MCP contract fake (tests/test_swiggy_live.FakeLive,
the same one tests/fixtures/swiggy_food_recording.json was recorded from), using the
recorded menu of "Hotel Saravana Bhavan (Adyar)" plus two more real-looking dishes.
"""
import pytest

from test_swiggy_live import FakeLive, _connect
from smartplate import db
from smartplate.app import create_app
from smartplate.domain import live_catalog, models
from smartplate.integrations import swiggy_connect

RECORDED_MENU = {"Mini Tiffin": 12500, "Ghee Pongal": 14000, "Peanut Chutney Dosa": 13000,   # recorded fixture
                 "Veg Meals": 18000, "Chicken Biryani": 22000, "Filter Coffee": 4000,
                 "Chef's Special Platter": 30000}                                         # no template: unused


@pytest.fixture
def client(seeded):
    app = create_app()
    app.config["TESTING"] = True
    return app.test_client()


@pytest.fixture
def swiggy(monkeypatch):
    fake = FakeLive()
    monkeypatch.setattr(swiggy_connect, "_http", fake)
    fake.dishes = dict(RECORDED_MENU)
    return fake


def _live_user(client, swiggy, uid):
    _connect(client, swiggy, uid=uid)
    client.post(f"/api/user/{uid}/swiggy/address", json={"address_id": "addr-home"})
    r = client.post(f"/api/user/{uid}/swiggy/favourites",
                    json={"restaurant_id": "r-1", "restaurant_name": "Hotel Saravana Bhavan (Adyar)"})
    assert r.status_code == 200, r.get_json()
    return client.get(f"/api/user/{uid}/plan").get_json()["plan"]["id"]


def _cells(view):
    return [c for d in view["grid"] for c in d["meals"].values()]


def test_b1_estimate_reads_dish_names_and_never_invents_unknown_dishes():
    dosa = live_catalog.estimate("Peanut Chutney Dosa", True)
    assert "peanut" in dosa["allergens"] and dosa["veg"] == 1 and 300 < dosa["kcal"] < 700
    biryani = live_catalog.estimate("Chicken Biryani", None)
    assert biryani["veg"] == 0 and biryani["protein_g"] >= 20              # name implies non-veg
    assert live_catalog.estimate("Chicken Biryani", True)["veg"] == 1      # Swiggy's flag wins
    assert "dairy" in live_catalog.estimate("Ghee Pongal", True)["allergens"]
    assert live_catalog.estimate("Chef's Special Platter", True) is None
    assert "dessert" in live_catalog.estimate("Filter Coffee", True)["tags"]   # not planned as a meal


def test_b2_connected_week_is_planned_from_live_menus_only(client, swiggy):
    pid = _live_user(client, swiggy, 3)
    before = client.get("/api/plan/%d" % pid).get_json()
    assert before["source"]["kind"] == "sample" and before["source"]["connected"] is True
    view = client.post(f"/api/plan/{pid}/live-menus", json={}).get_json()
    src = view["source"]
    assert src["kind"] == "live" and src["restaurants"] == 1 and src["dishes"] == 6     # the platter is skipped
    assert "estimated" in src["note"]
    delivered = [c for c in _cells(view) if c["kind"] == "delivery"]
    assert delivered, "a 2500 week with live dishes plans deliveries"
    names = {c["item"] for c in delivered}
    assert names <= set(RECORDED_MENU) - {"Filter Coffee", "Chef's Special Platter"}, names
    assert {c["restaurant"] for c in delivered} == {"Hotel Saravana Bhavan (Adyar)"}   # no sample restaurant
    assert view["budget"]["spend"] <= view["budget"]["budget"]
    rows = models.menu_for_user(models.get_user(3))
    assert all(r["source"] == "live" and r["nutrition_estimated"] == 1 and r["provider_item_id"] for r in rows)
    # the options sheet offers the same live dishes, flagged as estimated
    sid = next(c["session_id"] for c in delivered)
    sheet = client.get(f"/api/session/{sid}/options").get_json()
    offered = [d for g in sheet["usual"] for d in g["dishes"]]
    assert offered and all(d["nutrition_estimated"] for d in offered)
    assert {d["name"] for d in offered} <= set(RECORDED_MENU)


def test_b3_declared_allergy_never_gets_the_live_allergen_dish(client, swiggy):
    pid = _live_user(client, swiggy, 1)                         # Sample profile: peanut allergy, diabetes
    view = client.post(f"/api/plan/{pid}/live-menus", json={}).get_json()
    assert view["source"]["kind"] == "live"
    assert "Peanut Chutney Dosa" not in {c["item"] for c in _cells(view)}


def test_b4_not_connected_falls_back_clearly(client, swiggy):
    pid = client.get("/api/user/3/plan").get_json()["plan"]["id"]
    r = client.post(f"/api/plan/{pid}/live-menus", json={})
    body = r.get_json()                    # 409 swiggy_not_connected once PR A is in; 502 on main today
    assert r.status_code in (409, 502) and "Connect your Swiggy account" in (body.get("message") or body["error"])
    view = client.get(f"/api/plan/{pid}").get_json()
    assert view["source"] == {"kind": "sample", "connected": False, "label": "Sample dishes (not real restaurants)",
                              "note": "Connect Swiggy to plan from real restaurants near you."}


def test_b5_disconnect_returns_to_sample_and_says_so(client, swiggy):
    pid = _live_user(client, swiggy, 3)
    client.post(f"/api/plan/{pid}/live-menus", json={})
    client.post("/api/user/3/swiggy/disconnect", json={})
    view = client.post(f"/api/plan/{pid}/optimize", json={}).get_json()
    assert view["source"]["kind"] == "sample" and not live_catalog.has_live(3)
    with db.cursor() as cur:
        assert cur.execute("SELECT COUNT(*) n FROM restaurants WHERE source='live'").fetchone()["n"] == 0


def test_b6_a_failing_swiggy_keeps_the_last_live_catalogue(client, swiggy):
    pid = _live_user(client, swiggy, 3)
    client.post(f"/api/plan/{pid}/live-menus", json={})
    swiggy.dishes = {"Chef's Special Platter": 30000}           # nothing plannable comes back
    r = client.post(f"/api/plan/{pid}/live-menus", json={})
    assert r.status_code == 502 and "no dishes SmartPlate can plan" in r.get_json()["error"]
    assert client.get(f"/api/plan/{pid}").get_json()["source"]["dishes"] == 6
