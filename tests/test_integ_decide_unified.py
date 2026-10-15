"""One "Decide for me" engine (integration, Oct 2026): v3 Today's route
(/api/user/<id>/today/decide, smartplate/today.py) is a thin wrapper over kernel/decide.py,
the engine behind /api/user/<id>/decide/today and /api/plan/<id>/decide. Modes, rules and the
objective live in kernel/decide.py + kernel/objective.py only."""
import pytest

from smartplate.app import create_app
from smartplate.kernel import decide as engine
from test_today_v3 import _profile, _typed


@pytest.fixture
def plain(gt):
    client = create_app().test_client()
    return {"c": client, "uid": _profile(client)}


def test_today_decide_runs_on_the_kernel_engine(plain, monkeypatch):
    c, uid = plain["c"], plain["uid"]
    _typed(c, uid, "breakfast", "Masala Dosa", 120)
    _typed(c, uid, "lunch", "Veg Burrito", 260)
    calls = []
    real = engine.plan

    def spy(*a, **kw):
        calls.append(kw)
        return real(*a, **kw)
    monkeypatch.setattr(engine, "plan", spy)
    t = c.post(f"/api/user/{uid}/today/decide", json={"mode": "favourites"}).get_json()
    assert len(calls) == 1 and calls[0]["mode"] == "favourites_only"
    assert calls[0]["money"] == 600 and calls[0]["rules"]["min_rating"] == 4.0
    assert t["decided"]["engine_mode"] == "favourites_only" and t["decided"]["mode"] == "favourites"
    assert set(t["picks"]) == {"breakfast", "lunch"}
    # the engine's one-line reason comes through for each pick
    assert t["decided"]["why"]["lunch"].startswith("Your lunch favourite")


def test_today_route_accepts_the_engine_mode_names(plain):
    c, uid = plain["c"], plain["uid"]
    _typed(c, uid, "breakfast", "Masala Dosa", 120)
    t = c.post(f"/api/user/{uid}/today/decide", json={"mode": "favourites_only"}).get_json()
    assert t["decided"]["mode"] == "favourites" and t["picks"]["breakfast"]["name"] == "Masala Dosa"
    assert c.post(f"/api/user/{uid}/today/decide", json={"mode": "everything"}).status_code == 400


def test_typed_dish_with_unknown_rating_is_not_hidden_by_the_rating_rule():
    slot = {"day": "2026-11-02", "meal": "lunch"}
    mine = {"id": "t:1", "name": "Veg Burrito", "price": 200.0, "veg": 1, "allergens": [], "restaurant_id": "typed:x",
            "restaurant_name": "Corner Cafe", "restaurant_rating": None, "tags": []}
    other = {**mine, "id": "x:2", "restaurant_id": "r-9", "restaurant_name": "Unrated"}
    people = [{"name": "Priya", "diet": "veg", "allergens": [], "medical": []}]
    cands, _ = engine.candidates_for_slot(slot, [mine, other], mode="favourites_plus_new", people=people,
                                          places=set(), pool_ids={"t:1"}, rules={"min_rating": 4.0})
    assert [c["item_id"] for c in cands] == ["t:1"]          # an unrated stranger is still hidden


def test_max_delivery_time_rule_lives_in_the_engine():
    slot = {"day": "2026-11-02", "meal": "dinner"}
    far = {"id": 7, "name": "Dal Makhani", "price": 180.0, "veg": 1, "allergens": [], "restaurant_id": 1,
           "restaurant_name": "Dhaba", "restaurant_rating": 4.4, "eta_min": 45, "tags": []}
    people = [{"name": "Priya", "diet": "veg"}]
    cands, why = engine.candidates_for_slot(slot, [far], mode="favourites_only", people=people, places={1},
                                            pool_ids=set(), rules={"max_eta": 30})
    assert cands == [] and "over your delivery time" in why
    cands, _ = engine.candidates_for_slot(slot, [far], mode="favourites_only", people=people, places={1},
                                          pool_ids=set(), rules={})
    assert len(cands) == 1                                    # the week planner sets no time rule


def test_pool_quantity_is_the_cart_quantity(plain):
    c, uid = plain["c"], plain["uid"]
    t = _typed(c, uid, "breakfast", "Idli", 50)
    ref = t["pools"]["breakfast"][0]["ref"]
    c.post(f"/api/user/{uid}/today/pool/qty", json={"meal": "breakfast", "ref": ref, "qty": 3})
    t = c.post(f"/api/user/{uid}/today/decide", json={"mode": "favourites"}).get_json()
    assert t["picks"]["breakfast"]["qty"] == 3 and t["budget"]["picked"] == 150


def test_unsafe_typed_dish_is_never_decided(gt):
    client = create_app().test_client()
    uid = _profile(client, diet="vegan")
    _typed(client, uid, "breakfast", "Paneer Paratha", 90, veg=True)        # dairy: not vegan
    t = client.post(f"/api/user/{uid}/today/decide", json={"mode": "favourites"}).get_json()
    assert "breakfast" not in t["picks"] and "breakfast" in t["decided"]["missing"]
