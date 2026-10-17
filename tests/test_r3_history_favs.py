"""Fix round 3 · "history-favs": past Swiggy orders become sensible favourites in one or two
taps (ux-audit 16 Oct 2026 #2, #4, #6, #7, #8, #30, #31).

  #4  a past dish without Swiggy's reorder id is matched by name on the live menu (read
      once per restaurant) before it falls back to a typed dish;
  #6  a snack bucket (15:30-18:30), name/section hints as a tie-breaker, one dish in two
      meals when close; late night stays dinner; snack is an optional favourites list;
  #7  whole past orders suggested as usual orders with quantities; sides tagged and not
      suggested alone when they only ever came with a main;
  #8  history chips carry the restaurant id; one tap saves the suggested places;
  #2  a change of address keeps favourites, greyed with the reason, and says what changed;
  #30 refresh from my orders (a second read on a tap), with the date of the last read;
  #31 a ★ Swiggy shows for a place (menu or search) fills a suggestion that had none.
"""
from __future__ import annotations

import datetime as dt

import pytest

import test_w07_platform as w7
from test_w07_platform import _open, town  # noqa: F401  (fixture)

from smartplate import db
from smartplate.integrations import swiggy_history as h


def _o(oid, rid, name, items, when, status="DELIVERED"):
    return {"orderId": oid, "restaurantId": rid, "restaurantName": name, "orderTotal": "₹300",
            "orderStatus": status, "orderedItems": items, "orderedTime": f"{when} IST (UTC+05:30)", "actions": []}


ORDERS = [
    _o("a1", "r-102", "Biryani Junction", "1 x Veg Biryani, 2 x Raita", "Sat 2026-10-31 1:10 PM"),
    _o("a2", "r-102", "Biryani Junction", "1 x Veg Biryani, 2 x Raita", "Thu 2026-10-29 8:40 PM"),
    _o("a3", "r-101", "Annapoorna Tiffin Room", "1 x Kesari", "Wed 2026-10-28 4:45 PM"),
    _o("a4", "r-101", "Annapoorna Tiffin Room", "2 x Idli, 1 x Medu Vada", "Tue 2026-10-27 8:15 AM"),
    _o("a5", "r-105", "Wok Street", "1 x Mystery Curry", "Mon 2026-10-26 9:00 PM"),
    _o("a6", "r-105", "Wok Street", "1 x Veg Hakka Noodles", "Sun 2026-10-25 11:40 PM"),
]
NOW = dt.datetime(2026, 11, 2, 8, 0)


# --------------------------------------------------------------------------- pure scoring
def test_snack_bucket_and_late_night_stays_dinner():
    at = lambda hh, mm: dt.datetime(2026, 1, 1, hh, mm)  # noqa: E731
    assert [h.bucket(at(*t)) for t in ((10, 59), (11, 0), (15, 29), (15, 30), (18, 29), (18, 30), (21, 59), (23, 0))] \
        == ["breakfast", "lunch", "lunch", "snack", "snack", "dinner", "dinner", "late"]
    rows = {r["item_name"]: r for r in h.score_orders(ORDERS, now=NOW)}
    assert rows["Kesari"]["slots"] == ["snack"]                       # a 16:45 sweet is a snack, not a dinner
    assert rows["Veg Hakka Noodles"]["slot"] == "late" and rows["Veg Hakka Noodles"]["slots"] == ["dinner"]


def test_one_dish_can_sit_in_two_meals_and_hints_break_ties():
    rows = {r["item_name"]: r for r in h.score_orders(ORDERS, now=NOW)}
    assert rows["Veg Biryani"]["slots"] == ["lunch", "dinner"]        # once at lunch, once at dinner
    tea = [_o("t1", "r-101", "Annapoorna Tiffin Room", "1 x Masala Chai", "Sat 2026-10-31 3:00 PM"),
           _o("t2", "r-101", "Annapoorna Tiffin Room", "1 x Masala Chai", "Fri 2026-10-30 5:00 PM")]
    assert h.score_orders(tea, now=NOW)[0]["slots"] == ["snack", "lunch"]   # tie -> the name's hint first
    pongal = [_o("p1", "r-101", "Annapoorna Tiffin Room", "1 x Ven Pongal", "Sat 2026-10-31 12:30 PM"),
              _o("p2", "r-101", "Annapoorna Tiffin Room", "1 x Ven Pongal", "Fri 2026-10-30 9:00 AM")]
    assert h.score_orders(pongal, now=NOW)[0]["slots"] == ["breakfast", "lunch"]  # earlier wins without a hint
    menu = {("r-101", "ven pongal"): {"item_id": "x", "tags": ["breakfast"]}}
    pongal2 = [pongal[0], _o("p3", "r-101", "Annapoorna Tiffin Room", "1 x Ven Pongal", "Fri 2026-10-30 8:00 PM")]
    first = h.score_orders(pongal2, now=NOW, menu=menu)[0]
    assert first["slots"][0] == "lunch" and first["menu_item_id"] == "x"   # menu id filled from the catalogue
    lone = h.score_orders([pongal[0], pongal[0], pongal[1]], now=NOW)[0]
    assert lone["slots"] == ["lunch", "breakfast"]                     # 1 of 2 is still "close"
    far = h.score_orders([pongal[0]] * 3 + [pongal[1]], now=NOW)[0]
    assert far["slots"] == ["lunch"]                                   # 1 of 3 is not


def test_whole_orders_become_usual_orders_with_quantities_and_sides():
    rows = {r["item_name"]: r for r in h.score_orders(ORDERS, now=NOW)}
    assert rows["Raita"]["side"] and rows["Raita"]["with_main_only"] and rows["Raita"]["qty"] == 2
    assert not rows["Veg Biryani"]["side"]
    assert rows["Idli"]["qty"] == 2
    combos = {c["label"]: c for c in h.score_combos(ORDERS, now=NOW)}
    assert set(combos) == {"Veg Biryani + 2 Raita", "2 Idli + Medu Vada"}
    vb = combos["Veg Biryani + 2 Raita"]
    assert vb["times"] == 2 and vb["slots"] == ["lunch", "dinner"]
    assert [(i["name"], i["qty"], i["side"]) for i in vb["items"]] == [("Veg Biryani", 1, False), ("Raita", 2, True)]
    assert h.combo_label([{"name": "Butter Naan", "qty": 2, "side": True},
                          {"name": "Dal Makhani", "qty": 1, "side": False}]) == "Dal Makhani + 2 Butter Naan"
    # a coffee with a dosa is a side of that order; on its own it is still suggested
    assert h._is_side("Filter Coffee") and not h._is_side("Masala Dosa")


# --------------------------------------------------------------------------- through the app
@pytest.fixture
def orders(monkeypatch):
    monkeypatch.setattr(w7, "ORDERS", ORDERS)
    return ORDERS


def _import(c, uid):
    v = c.post(f"/api/user/{uid}/favourites/suggested", json={"consent": True}).get_json()
    assert v["imported"], v
    return v


def test_import_stores_combos_minimal_and_snack_suggestions(town, orders):  # noqa: F811
    c, uid, _ = _open(town)
    v = _import(c, uid)
    assert v["consent_short"] and len(v["consent_short"]) < len(v["consent"])
    labels = [x["label"] for x in v["combos"]]
    assert "Veg Biryani + 2 Raita" in labels
    assert [i["item_name"] for i in v["by_slot"]["snack"]] == ["Kesari"]
    assert {"Veg Biryani"} <= {i["item_name"] for i in v["by_slot"]["lunch"]} & {i["item_name"] for i in v["by_slot"]["dinner"]}
    raita = next(i for i in v["items"] if i["item_name"] == "Raita")
    assert raita["with_main_only"] and raita["qty"] == 2
    with db.cursor() as cur:
        rows = [dict(r) for r in cur.execute("SELECT * FROM swiggy_history_combos WHERE user_id=?", (uid,)).fetchall()]
    assert rows and not {"order_id", "order_total", "address_id"} & set(rows[0])
    assert "a1" not in str(rows) and "300" not in str(rows)                 # no order ids or totals
    assert c.delete(f"/api/user/{uid}/favourites/suggested", json={}).get_json()["combos"] == []
    with db.cursor() as cur:
        assert not cur.execute("SELECT 1 FROM swiggy_history_combos WHERE user_id=?", (uid,)).fetchone()


def test_name_match_reads_each_menu_once_and_keeps_quantities(town, orders):  # noqa: F811
    from smartplate.integrations import swiggy_live
    c, uid, _ = _open(town)
    from smartplate.domain import live_catalog
    _import(c, uid)
    live_catalog.refresh(uid)              # the one-off catalogue read a first pooled dish triggers
    before = town["fake"].tool_calls().count("get_restaurant_menu")
    r = c.post(f"/api/user/{uid}/today/pool/suggested", json={"items": [
        {"restaurant_id": "r-102", "combo": "Veg Biryani + 2 Raita", "meal": "dinner"},
        {"restaurant_id": "r-101", "item_name": "Idli", "meal": "breakfast"},
        {"restaurant_id": "r-105", "item_name": "Mystery Curry", "meal": "dinner"}]})
    assert r.status_code == 200, r.get_json()
    t = r.get_json()
    assert t["suggested_added"] == {"swiggy": ["Veg Biryani", "Raita", "Idli"], "typed": ["Mystery Curry"],
                                    "refused": []}
    used = town["fake"].tool_calls().count("get_restaurant_menu") - before
    one = town["fake"].tool_calls().count("get_restaurant_menu")
    swiggy_live.live_menu(uid, "r-101", "Annapoorna Tiffin Room")
    per_menu = town["fake"].tool_calls().count("get_restaurant_menu") - one
    # three places, at most one read each; a menu kept by the 6 h menu cache (r3 menus-offers) is not re-read
    assert used <= 3 * per_menu
    dinner = {e["name"]: e for e in t["pools"]["dinner"]}
    assert dinner["Veg Biryani"]["source"] == "swiggy" and dinner["Veg Biryani"]["qty"] == 1
    assert dinner["Raita"]["source"] == "swiggy" and dinner["Raita"]["qty"] == 2   # "2 x Raita" kept
    assert dinner["Mystery Curry"]["source"] == "typed" and dinner["Mystery Curry"]["price"] is None
    idli = t["pools"]["breakfast"][0]
    assert idli["name"] == "Idli (2 pcs)" and idli["source"] == "swiggy" and idli["qty"] == 2
    bad = c.post(f"/api/user/{uid}/today/pool/suggested",
                 json={"items": [{"restaurant_id": "r-102", "combo": "Lobster + Wine"}]})
    assert bad.status_code == 400


def test_snack_is_an_optional_favourite_never_planned(town, orders):  # noqa: F811
    from smartplate.domain import meal_pools
    c, uid, _ = _open(town)
    _import(c, uid)
    t = c.post(f"/api/user/{uid}/today/pool/suggested",
               json={"items": [{"restaurant_id": "r-101", "item_name": "Kesari"}]}).get_json()
    snack = t["pools"]["snack"]
    assert [e["name"] for e in snack] == ["Kesari"] and "snack" not in t["meals"]
    assert "snack" not in meal_pools.for_planner(uid)                     # the planner never sees it
    left = t["budget"]["left"]
    t = c.post(f"/api/user/{uid}/today/pick", json={"meal": "snack", "ref": snack[0]["ref"]}).get_json()
    assert t["picks"]["snack"]["name"] == "Kesari"
    assert t["budget"]["left"] < left and "Snack: 1× Kesari" in t["share_text"]   # a picked snack costs money
    assert c.post(f"/api/user/{uid}/today/pick", json={"meal": "elevenses", "ref": "x"}).status_code == 400


def test_save_suggested_places_in_one_tap(town, orders):  # noqa: F811
    c, uid, _ = _open(town)
    v = _import(c, uid)
    assert not any(r["saved"] for r in v["restaurants"])
    assert c.post(f"/api/user/{uid}/favourites/suggested/places", json={"restaurant_ids": ["r-999"]}).status_code == 400
    r = c.post(f"/api/user/{uid}/favourites/suggested/places", json={}).get_json()
    assert sorted(r["places_saved"]) == ["Annapoorna Tiffin Room", "Biryani Junction", "Wok Street"]
    assert all(x["saved"] for x in r["restaurants"])
    assert {f["id"] for f in c.get(f"/api/user/{uid}/swiggy/favourites").get_json()} == {"r-101", "r-102", "r-105"}
    again = c.post(f"/api/user/{uid}/favourites/suggested/places", json={"restaurant_ids": ["r-101"]}).get_json()
    assert again["places_saved"] == []                                     # already saved: nothing twice


def test_history_chips_carry_restaurant_ids(town):  # noqa: F811
    c, uid, _ = _open(town)
    h_ = c.get(f"/api/user/{uid}/swiggy/order-history").get_json()
    assert {o["restaurant_id"] for o in h_["provider_orders"]} >= {"r-101", "r-102"}


def test_refresh_reads_again_and_ratings_fill_from_what_swiggy_shows(town, orders):  # noqa: F811
    c, uid, _ = _open(town)
    v = _import(c, uid)
    wok = next(r for r in v["restaurants"] if r["restaurant_id"] == "r-105")
    assert wok["rating"] is None                                            # not catalogued yet
    m = c.get(f"/api/user/{uid}/swiggy/live-menu?restaurant_id=r-105&restaurant_name=Wok%20Street")
    assert m.status_code == 200
    v = c.get(f"/api/user/{uid}/favourites/suggested").get_json()
    assert next(r for r in v["restaurants"] if r["restaurant_id"] == "r-105")["rating"] == 3.9
    first = v["imported_at"]
    town["clock"].advance(days=1)
    v = _import(c, uid)                                                     # "Refresh from my orders"
    assert v["imported_at"] > first and town["fake"].tool_calls().count("get_food_orders") == 2
    assert next(r for r in v["restaurants"] if r["restaurant_id"] == "r-105")["rating"] == 3.9   # kept


def test_address_change_keeps_favourites_greyed_and_says_what_changed(town, orders):  # noqa: F811
    c, uid, _ = _open(town)                                                 # OMR: Biryani Junction delivers
    _import(c, uid)
    c.post(f"/api/user/{uid}/today/pool/suggested",
           json={"items": [{"restaurant_id": "r-102", "item_name": "Veg Biryani", "meal": "dinner"}]})
    r = c.post(f"/api/user/{uid}/swiggy/address", json={"address_id": "addr-minjur"}).get_json()
    assert r["address_changed"] == {"kept": 1, "not_checked_here": 1}
    t = c.get(f"/api/user/{uid}/today").get_json()
    vb = t["pools"]["dinner"][0]
    assert vb["name"] == "Veg Biryani" and vb["elsewhere"] and "other address" in vb["reasons"][0]
    p = c.post(f"/api/user/{uid}/today/pick", json={"meal": "dinner", "ref": vb["ref"]})
    assert p.status_code == 400 and "other address" in p.get_json()["error"]
    back = c.post(f"/api/user/{uid}/swiggy/address", json={"address_id": "addr-omr"}).get_json()
    assert back["address_changed"]["not_checked_here"] == 0
    t = c.get(f"/api/user/{uid}/today").get_json()
    assert not t["pools"]["dinner"][0]["elsewhere"]
    # removing a kept dish removes it at every address
    c.post(f"/api/user/{uid}/today/pool/remove", json={"meal": "dinner", "ref": vb["ref"]})
    c.post(f"/api/user/{uid}/swiggy/address", json={"address_id": "addr-minjur"})
    assert c.get(f"/api/user/{uid}/today").get_json()["pools"]["dinner"] == []
