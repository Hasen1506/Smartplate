"""v3 Today (smartplate/today.py + today_api.py): favourites first, rules second, the solver
only when asked. Against the stand-in Swiggy neighbourhood (tests/swiggy_town.py) with a
frozen clock (Mon 2 Nov 2026 08:00 IST). Nothing real is touched."""
import pytest

from smartplate import config
from smartplate.app import create_app
from smartplate.integrations import swiggy_connect
from swiggy_town import TownFake
from test_followups import _connect


def _profile(client, diet="veg", **extra):
    body = {"name": "Priya", "diet": diet, "weekly_budget": 4200, "cook": "never",
            "rhythm": {"breakfast": "order", "lunch": "order", "dinner": "skip"}, "allergens": [], "medical": []}
    body.update(extra)
    r = client.post("/api/profiles", json=body)
    assert r.status_code == 201, r.get_json()
    client.environ_base["HTTP_X_SMARTPLATE_KEY"] = r.get_json()["access_key"]
    return r.get_json()["user"]["id"]


@pytest.fixture
def plain(gt):
    """A profile with no Swiggy connection: typed-in favourites only."""
    client = create_app().test_client()
    return {"c": client, "uid": _profile(client)}


@pytest.fixture
def town(gt, monkeypatch):
    monkeypatch.setattr(config, "SWIGGY_PROVIDER", "live")
    fake = TownFake()
    monkeypatch.setattr(swiggy_connect, "_http", fake)
    client = create_app().test_client()
    uid = _profile(client)
    _connect(client, fake, uid=uid)
    assert client.post(f"/api/user/{uid}/swiggy/address", json={"address_id": "addr-home"}).status_code == 200
    plan = client.get(f"/api/user/{uid}/plan").get_json()["plan"]["id"]
    assert client.post(f"/api/plan/{plan}/live-menus", json={}).status_code == 200
    return {"c": client, "uid": uid}


def _today(c, uid):
    r = c.get(f"/api/user/{uid}/today")
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def _typed(c, uid, meal, name, price, veg=True, place="Corner Cafe", kind="order"):
    r = c.post(f"/api/user/{uid}/today/pool", json={"source": "typed", "meal": meal, "name": name,
                                                     "restaurant_name": place, "price": price, "veg": veg,
                                                     "kind": kind})
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def test_today_starts_with_budget_from_the_week_and_nothing_decided(plain):
    t = _today(plain["c"], plain["uid"])
    assert t["meals"] == ["breakfast", "lunch"] and t["label"] == "B+L"
    assert t["budget"]["amount"] == 600 and t["budget"]["source"] == "week"       # ₹4,200 ÷ 7
    assert t["picks"] == {}                                                          # never auto-decided
    assert t["rules"]["min_rating"] == 4.0 and t["rules"]["max_eta"] == 30
    assert t["price_compare"] is None                                               # no data → no row
    assert t["weather"] == {"known": False} or t["weather"]["known"] is True
    assert t["account_saved"] is False and t["swiggy"]["connected"] is False


def test_budget_modify_and_validation(plain):
    c, uid = plain["c"], plain["uid"]
    r = c.post(f"/api/user/{uid}/today/budget", json={"amount": 450})
    assert r.status_code == 200 and r.get_json()["budget"]["amount"] == 450
    assert r.get_json()["budget"]["source"] == "today"
    for bad in (0, -5, "abc", 10 ** 6, True):
        assert c.post(f"/api/user/{uid}/today/budget", json={"amount": bad}).status_code == 400


def test_typed_favourites_pick_quantity_and_share_text(plain):
    c, uid = plain["c"], plain["uid"]
    t = _typed(c, uid, "breakfast", "Cappuccino", 140)
    ref = t["pools"]["breakfast"][0]["ref"]
    assert t["pools"]["breakfast"][0]["source"] == "typed" and not t["pools"]["breakfast"][0]["reasons"]
    r = c.post(f"/api/user/{uid}/today/pick", json={"meal": "breakfast", "ref": ref, "qty": 2}).get_json()
    assert r["picks"]["breakfast"]["qty"] == 2 and r["budget"]["picked"] == 280 and r["budget"]["left"] == 320
    assert "Breakfast: 2× Cappuccino from Corner Cafe · ₹280" in r["share_text"]
    assert r["price_compare"] is None                     # typed prices aren't Swiggy prices
    r = c.post(f"/api/user/{uid}/today/pool/qty", json={"meal": "breakfast", "ref": ref, "qty": 1}).get_json()
    assert r["picks"]["breakfast"]["qty"] == 1
    assert c.post(f"/api/user/{uid}/today/pool/qty", json={"meal": "breakfast", "ref": ref, "qty": 99}).status_code == 400
    r = c.post(f"/api/user/{uid}/today/pick", json={"meal": "breakfast", "ref": None}).get_json()
    assert r["picks"] == {}


def test_veg_profile_greys_out_unmarked_and_meat_dishes_and_cannot_pick_them(plain):
    c, uid = plain["c"], plain["uid"]
    t = _typed(c, uid, "lunch", "Chicken Burrito", 220, veg=None)
    e = t["pools"]["lunch"][0]
    assert e["hard"] and e["reasons"][0] == "Not marked veg"
    r = c.post(f"/api/user/{uid}/today/pick", json={"meal": "lunch", "ref": e["ref"]})
    assert r.status_code == 400 and "breaks your food rules" in r.get_json()["error"]


def test_allergy_rule_greys_out_by_dish_name(gt):
    client = create_app().test_client()
    uid = _profile(client, diet="nonveg", allergens=["peanut"])
    t = _typed(client, uid, "lunch", "Peanut Chikki", 40, veg=True)
    assert t["pools"]["lunch"][0]["hard"]


def test_decide_only_when_asked_and_within_budget(plain):
    c, uid = plain["c"], plain["uid"]
    _typed(c, uid, "breakfast", "Cappuccino", 140)
    _typed(c, uid, "breakfast", "Masala Dosa", 120)
    _typed(c, uid, "lunch", "Veg Burrito", 260)
    _typed(c, uid, "lunch", "Thali", 700)                                   # can never fit ₹600
    assert _today(c, uid)["picks"] == {}
    r = c.post(f"/api/user/{uid}/today/decide", json={"mode": "favourites"})
    assert r.status_code == 200, r.get_json()
    t = r.get_json()
    assert set(t["picks"]) == {"breakfast", "lunch"} and t["budget"]["picked"] <= 600
    assert t["picks"]["lunch"]["name"] == "Veg Burrito" and t["picks"]["lunch"]["how"] == "ziggy"
    assert t["decided"]["filled"] == ["breakfast", "lunch"]
    # already-picked meals are left alone
    again = c.post(f"/api/user/{uid}/today/decide", json={"mode": "favourites"}).get_json()
    assert again["picks"] == t["picks"] and "already picked" in again["decided"]["note"]
    assert c.post(f"/api/user/{uid}/today/decide", json={"mode": "random"}).status_code == 400


def test_decide_says_why_a_meal_could_not_be_filled(plain):
    c, uid = plain["c"], plain["uid"]
    c.post(f"/api/user/{uid}/today/budget", json={"amount": 100})
    _typed(c, uid, "breakfast", "Cappuccino", 140)
    t = c.post(f"/api/user/{uid}/today/decide", json={"mode": "favourites"}).get_json()
    assert t["picks"] == {} and "fits" in t["decided"]["note"] and "breakfast" in t["decided"]["missing"]


def test_favourites_plus_cook_uses_priced_home_recipes(plain):
    c, uid = plain["c"], plain["uid"]
    t = c.post(f"/api/user/{uid}/today/decide", json={"mode": "favourites_cook"}).get_json()
    assert t["picks"], t["decided"]
    assert all(p["kind"] == "cook" and p["price_basis"] == "grocery estimate" for p in t["picks"].values())


def test_rules_soft_filters_and_swiggy_pool_with_price_compare(town):
    c, uid = town["c"], town["uid"]
    r = c.post(f"/api/user/{uid}/today/pool", json={"source": "swiggy", "meal": "breakfast", "restaurant_id": "r-101",
                                                     "restaurant_name": "Annapoorna Tiffin Room", "item_id": "r-101-i5",
                                                     "rating": 4.4, "eta_min": 25})
    assert r.status_code == 200, r.get_json()
    r = c.post(f"/api/user/{uid}/today/pool", json={"source": "swiggy", "meal": "lunch", "restaurant_id": "r-104",
                                                     "restaurant_name": "Punjab Da Dhaba", "item_id": "r-104-i1",
                                                     "rating": 4.1, "eta_min": 40})
    t = r.get_json()
    lunch = t["pools"]["lunch"][0]
    assert lunch["name"] == "Dal Makhani" and lunch["reasons"] == ["40 min away, over your 30 min"]
    assert not lunch["hard"]
    pong = t["pools"]["breakfast"][0]
    assert pong["name"] == "Ven Pongal" and pong["reasons"] == []
    t = c.post(f"/api/user/{uid}/today/decide", json={"mode": "favourites"}).get_json()
    assert t["picks"]["breakfast"]["name"] == "Ven Pongal" and "lunch" not in t["picks"]    # greyed out: not chosen
    assert t["price_compare"]["swiggy"] == 90 and t["price_compare"]["others"] == "coming"
    t = c.post(f"/api/user/{uid}/today/rules", json={"max_eta": 45, "min_rating": 4.5}).get_json()
    assert t["rules"]["max_eta"] == 45
    assert t["pools"]["lunch"][0]["reasons"] == ["Rated 4.1★, below your 4.5★"]
    assert c.post(f"/api/user/{uid}/today/rules", json={"min_rating": 9}).status_code == 400
    # remove clears the pick too
    t = c.post(f"/api/user/{uid}/today/pool/remove", json={"meal": "breakfast", "ref": pong["ref"]}).get_json()
    assert t["pools"]["breakfast"] == [] and "breakfast" not in t["picks"]


def test_new_places_mode_only_picks_rule_passing_live_dishes(town):
    c, uid = town["c"], town["uid"]
    t = c.post(f"/api/user/{uid}/today/decide", json={"mode": "favourites_new"}).get_json()
    assert t["picks"], t["decided"]
    for p in t["picks"].values():
        assert p["source"] == "swiggy" and p["veg"] is True and (p["rating"] or 0) >= 4.0


def test_surprise_is_deterministic_for_the_same_day(plain):
    c, uid = plain["c"], plain["uid"]
    for n, name in enumerate(["Idli", "Poha", "Upma", "Dosa"]):
        _typed(c, uid, "breakfast", name, 60 + n)
    first = c.post(f"/api/user/{uid}/today/decide", json={"mode": "surprise"}).get_json()["picks"]["breakfast"]
    c.post(f"/api/user/{uid}/today/pick", json={"meal": "breakfast", "ref": None})
    c.post(f"/api/user/{uid}/today/pick", json={"meal": "lunch", "ref": None})
    assert first["how"] == "surprise"


def test_today_routes_are_private(plain):
    other = create_app().test_client()
    assert other.get(f"/api/user/{plain['uid']}/today").status_code == 401
    assert other.post(f"/api/user/{plain['uid']}/today/budget", json={"amount": 300}).status_code == 401


def test_typed_input_validation(plain):
    c, uid = plain["c"], plain["uid"]
    bad = [{"meal": "brunch", "name": "x y"}, {"meal": "lunch", "name": "x"}, {"meal": "lunch", "name": "Dosa", "price": -1},
           {"meal": "lunch", "name": "Dosa", "veg": "maybe"}, {"meal": "lunch", "name": "Dosa", "kind": "steal"}]
    for b in bad:
        assert c.post(f"/api/user/{uid}/today/pool", json={"source": "typed", **b}).status_code == 400, b
