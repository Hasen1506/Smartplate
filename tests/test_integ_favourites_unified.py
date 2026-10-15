"""One favourites list on v3 Today (integration, Oct 2026): W7's suggested-from-history
(read once, with consent) and W7's typed favourites feed W5's Today pools.

  * ticked suggestions go into the pools: a dish still on the live Swiggy menu as a Swiggy
    dish (shared safety check runs), anything else as the person's own typed dish with no
    invented price or veg mark;
  * typed favourites saved through /typed-favourites show in the Today pools and can be
    removed there."""
from smartplate.app import create_app
from test_today_v3 import _profile
from test_w07_platform import _open, town  # noqa: F401  (fixture)


def _import(c, uid):
    v = c.post(f"/api/user/{uid}/favourites/suggested", json={"consent": True}).get_json()
    assert v["imported"], v
    return {(i["restaurant_name"], i["item_name"]): i for i in v["items"]}


def test_ticked_suggestions_go_into_the_today_pools(town):  # noqa: F811
    c, uid, _ = _open(town)
    s = _import(c, uid)
    dosa = s[("Annapoorna Tiffin Room", "Masala Dosa")]
    biryani = s[("Biryani Junction", "Veg Biryani")]
    assert dosa["menu_item_id"] and not biryani["menu_item_id"]
    r = c.post(f"/api/user/{uid}/today/pool/suggested", json={"items": [
        {"restaurant_id": dosa["restaurant_id"], "item_name": "Masala Dosa", "meal": "lunch"},
        {"restaurant_id": biryani["restaurant_id"], "item_name": "Veg Biryani", "meal": "dinner"}]})
    assert r.status_code == 200, r.get_json()
    t = r.get_json()
    assert t["suggested_added"] == {"swiggy": ["Masala Dosa"], "typed": ["Veg Biryani"], "refused": []}
    lunch = t["pools"]["lunch"]
    assert [(e["name"], e["source"]) for e in lunch] == [("Masala Dosa", "swiggy")]
    assert lunch[0]["ref"] == f"sw:{dosa['restaurant_id']}:{dosa['menu_item_id']}" and lunch[0]["price"] is not None
    dinner = t["pools"]["dinner"]
    assert [(e["name"], e["source"], e["price"]) for e in dinner] == [("Veg Biryani", "typed", None)]
    assert dinner[0]["restaurant"] == "Biryani Junction"
    assert dinner[0]["hard"] and dinner[0]["reasons"] == ["Not marked veg"]       # no veg mark invented
    # nothing else became a favourite
    assert t["pools"]["breakfast"] == []


def test_only_stored_suggestions_can_be_ticked(town):  # noqa: F811
    c, uid, _ = _open(town)
    _import(c, uid)
    bad = [{"items": []}, {"items": "x"}, {"items": [{"restaurant_id": "r-101", "item_name": "Lobster"}]},
           {"items": [1]}]
    for body in bad:
        assert c.post(f"/api/user/{uid}/today/pool/suggested", json=body).status_code == 400, body
    c.delete(f"/api/user/{uid}/favourites/suggested", json={})
    r = c.post(f"/api/user/{uid}/today/pool/suggested",
               json={"items": [{"restaurant_id": "r-101", "item_name": "Masala Dosa"}]})
    assert r.status_code == 400                                   # deleted suggestions are gone


def test_w7_typed_favourites_show_in_today_pools_and_can_be_decided(gt):
    c = create_app().test_client()
    uid = _profile(c, diet="nonveg")
    f = c.post(f"/api/user/{uid}/typed-favourites", json={"meal": "lunch", "restaurant_name": "Annapoorna",
                                                         "item_name": "Mini Meals", "price": 180}).get_json()
    assert f["count"] == 1
    t = c.get(f"/api/user/{uid}/today").get_json()
    lunch = t["pools"]["lunch"]
    assert [(e["name"], e["source"], e["price"], e["ref"][:3]) for e in lunch] == [("Mini Meals", "typed", 180.0, "tf:")]
    t = c.post(f"/api/user/{uid}/today/decide", json={"mode": "favourites"}).get_json()
    assert t["picks"]["lunch"]["name"] == "Mini Meals"
    t = c.post(f"/api/user/{uid}/today/pool/remove", json={"meal": "lunch", "ref": lunch[0]["ref"]}).get_json()
    assert t["pools"]["lunch"] == [] and "lunch" not in t["picks"]
    assert c.get(f"/api/user/{uid}/typed-favourites").get_json()["count"] == 0
