"""Fix round 3, "menus-offers" (UX audit 16 Oct 2026: #3 #9 #13 #16 #19 #20 #26): menus that
open at once, offers and best value, the one-cart rule. Against the stand-in Swiggy town
(tests/swiggy_town.py), paged like the live menu where it matters. Nothing real is touched."""
import pytest

from smartplate import config, db
from smartplate.app import create_app
from smartplate.integrations import swiggy_connect, swiggy_menu_cache, swiggy_offers
import test_swiggy_live
from swiggy_town import TownFake
from test_followups import _connect

PAGED_MENU = {"type": "object", "required": ["addressId", "restaurantId"],
              "properties": {"addressId": {"type": "string"}, "restaurantId": {"type": "string"},
                             "page": {"type": "number"}, "pageSize": {"type": "number"}}}
COUPONS = {"type": "object", "properties": {"addressId": {"type": "string"}}}


@pytest.fixture(autouse=True)
def _no_stray_threads():
    yield
    swiggy_menu_cache.wait_idle()


def _world(monkeypatch, *, paged=False, deals=False, diet="nonveg", budget=4200, refresh=True):
    monkeypatch.setattr(config, "SWIGGY_PROVIDER", "live")
    if paged:
        monkeypatch.setitem(test_swiggy_live.SCHEMAS, "get_restaurant_menu", PAGED_MENU)
    monkeypatch.setitem(test_swiggy_live.SCHEMAS, "fetch_food_coupons", COUPONS)
    fake = TownFake(paged=paged, deals=deals)
    monkeypatch.setattr(swiggy_connect, "_http", fake)
    c = create_app().test_client()
    r = c.post("/api/profiles", json={"name": "Asha", "diet": diet, "weekly_budget": budget, "cook": "never",
                                      "rhythm": {"breakfast": "order", "lunch": "order", "dinner": "order"},
                                      "allergens": [], "medical": [],
                                      "consent": {"account": True, "health": True, "learning": True}})
    uid = r.get_json()["user"]["id"]
    _connect(c, fake, uid=uid)
    assert c.post(f"/api/user/{uid}/swiggy/address", json={"address_id": "addr-home"}).status_code == 200
    plan = c.get(f"/api/user/{uid}/plan").get_json()["plan"]["id"]
    if refresh:
        assert c.post(f"/api/plan/{plan}/live-menus", json={}).status_code == 200
    return {"c": c, "uid": uid, "plan": plan, "fake": fake}


def _menu(w, rid, name="x", fresh=False):
    r = w["c"].get(f"/api/user/{w['uid']}/swiggy/live-menu?restaurant_id={rid}&restaurant_name={name}"
                   + ("&fresh=1" if fresh else ""))
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def _reads(w):
    return w["fake"].tool_calls().count("get_restaurant_menu")


# ------------------------------------------------------------------ #3 instant menus
def test_a_menu_read_once_opens_again_with_no_swiggy_call(gt, monkeypatch):
    w = _world(monkeypatch, refresh=False)
    first = _menu(w, "r-101", "Annapoorna Tiffin Room")
    assert first["source"] == "swiggy" and len(first["items"]) == 12
    before = len(w["fake"].tool_calls())
    again = _menu(w, "r-101", "Annapoorna Tiffin Room")
    assert again["source"] == "cache" and again["cached"] and len(again["items"]) == 12
    assert len(w["fake"].tool_calls()) == before                       # no Swiggy call at all
    assert not again["refreshing"]


def test_the_catalogue_refresh_fills_the_menus_so_the_first_tap_is_instant(gt, monkeypatch):
    w = _world(monkeypatch)                                             # live-menus read the places
    before = len(w["fake"].tool_calls())
    m = _menu(w, "r-101", "Annapoorna Tiffin Room")
    assert m["source"] == "cache" and len(w["fake"].tool_calls()) == before


def test_a_kept_menu_is_revalidated_in_the_background_when_it_is_old(gt, monkeypatch):
    w = _world(monkeypatch, refresh=False)
    _menu(w, "r-101", "A")
    gt.advance(minutes=30)
    w["fake"].places["r-101"][6].append(("New Podi Dosa", 15000, True, "Dosa", False, False))
    stale = _menu(w, "r-101", "A")
    assert stale["source"] == "cache" and stale["refreshing"] and len(stale["items"]) == 12   # shown at once
    swiggy_menu_cache.wait_idle()
    fresh = _menu(w, "r-101", "A")
    assert len(fresh["items"]) == 13 and not fresh["refreshing"]
    gt.advance(hours=7)                                                 # past the 6 h TTL: read again
    n = _reads(w)
    assert _menu(w, "r-101", "A")["source"] == "swiggy" and _reads(w) == n + 1
    w["fake"].places["r-101"][6].pop()


def test_page_one_first_then_the_rest_in_the_background(gt, monkeypatch):
    w = _world(monkeypatch, paged=True, refresh=False)
    first = _menu(w, "r-101", "Annapoorna Tiffin Room")                 # 7 sections, 2 a page
    assert first["partial"] and first["refreshing"]
    assert {i["categories"][0] for i in first["items"]} == {"Dosa", "Tiffin"}
    assert w["fake"].menu_reads == [("r-101", 1)]
    swiggy_menu_cache.wait_idle()
    full = _menu(w, "r-101", "Annapoorna Tiffin Room")
    assert not full["partial"] and len(full["items"]) == 12 and full["source"] == "cache"
    assert [p for _, p in w["fake"].menu_reads] == [1, 2, 3, 4]         # each page read once
    assert full["verified_prices"] and next(i for i in full["items"] if i["name"] == "Ghee Roast Dosa")["price"] == 140


def test_with_no_kept_menu_the_catalogue_gives_a_first_look(gt, monkeypatch):
    w = _world(monkeypatch)
    with db.cursor() as cur:
        cur.execute("DELETE FROM swiggy_menus WHERE user_id=?", (w["uid"],))
    m = _menu(w, "r-104", "Punjab Da Dhaba")
    assert m["source"] == "catalogue" and m["partial"] and m["refreshing"] and m["items"]
    swiggy_menu_cache.wait_idle()
    assert _menu(w, "r-104", "Punjab Da Dhaba")["source"] == "cache"


def test_kept_menus_are_screened_for_the_person_now_and_dropped_with_the_address(gt, monkeypatch):
    w = _world(monkeypatch, refresh=False)
    assert any(i["name"] == "Chicken Dum Biryani" for i in _menu(w, "r-102", "B")["items"])
    from smartplate.domain import models
    with db.cursor() as cur:
        cur.execute("UPDATE users SET diet='veg' WHERE id=?", (w["uid"],))
    assert models.get_user(w["uid"])["diet"] == "veg"
    veg = _menu(w, "r-102", "B")
    assert veg["source"] == "cache" and not any(i["name"] == "Chicken Dum Biryani" for i in veg["items"])
    assert any(h["name"] == "Chicken Dum Biryani" for h in veg["hidden_items"])
    w["c"].post(f"/api/user/{w['uid']}/swiggy/address", json={"address_id": "addr-work"})
    raw, _ = swiggy_menu_cache.load(w["uid"], "r-102")
    assert raw is None


def test_opening_the_add_sheet_prefetches_only_the_persons_own_places(gt, monkeypatch):
    w = _world(monkeypatch, refresh=False)
    c, uid = w["c"], w["uid"]
    c.post(f"/api/user/{uid}/today/pool", json={"source": "swiggy", "meal": "lunch", "restaurant_id": "r-104",
                                                "restaurant_name": "Punjab Da Dhaba", "item_id": "r-104-i1"})
    with db.cursor() as cur:
        cur.execute("DELETE FROM swiggy_menus WHERE user_id=?", (uid,))
    r = c.post(f"/api/user/{uid}/swiggy/menus/prefetch", json={"restaurants": [{"id": "r-105", "name": "Wok Street"}]})
    assert r.status_code == 200
    assert set(r.get_json()["queued"]) == {"r-104", "r-105"}
    swiggy_menu_cache.wait_idle()
    before = len(w["fake"].tool_calls())
    assert _menu(w, "r-105", "Wok Street")["source"] == "cache" and len(w["fake"].tool_calls()) == before
    again = c.post(f"/api/user/{uid}/swiggy/menus/prefetch", json={}).get_json()
    assert again["queued"] == [] and "r-104" in again["kept"]
    assert "r-103" not in again["kept"]                                 # never a place the person doesn't use


def test_prefetch_reads_at_most_six_places(gt, monkeypatch):
    w = _world(monkeypatch, refresh=False)
    extra = [{"id": f"r-10{n}", "name": f"P{n}"} for n in range(1, 7)] + [{"id": "r-107", "name": "P7"}]
    queued = swiggy_menu_cache.prefetch(w["uid"], extra)["queued"]
    assert len(queued) <= swiggy_menu_cache.PREFETCH_MAX and "r-107" not in queued


def test_cart_review_reads_one_menu_page_not_the_whole_menu(gt, monkeypatch):
    w = _world(monkeypatch, paged=True, refresh=False)
    n = len(w["fake"].menu_reads)
    r = w["c"].post(f"/api/user/{w['uid']}/swiggy/live-cart/preview", json={
        "restaurant_id": "r-101", "restaurant_name": "Annapoorna Tiffin Room", "item_id": "r-101-i0",
        "item_name": "Ghee Roast Dosa"})
    assert r.status_code == 200, r.get_json()
    assert w["fake"].menu_reads[n:] == [("r-101", 1)]


# ------------------------------------------------------------------ #9 offers and value
def test_final_price_wins_and_the_list_price_is_kept_to_strike_through(gt, monkeypatch):
    w = _world(monkeypatch, deals=True, refresh=False)
    m = _menu(w, "r-101", "A")
    dosa = next(i for i in m["items"] if i["name"] == "Masala Dosa")
    assert dosa["price"] == 99 and dosa["list_price"] == 120
    plain = next(i for i in m["items"] if i["name"] == "Rava Onion Dosa")
    assert plain["price"] == 135 and plain["list_price"] is None
    assert m["restaurant"]["offer"]["label"] == "20% off up to ₹50"
    found = w["c"].get(f"/api/user/{w['uid']}/swiggy/restaurants?query=Dhaba").get_json()["restaurants"]
    assert found[0]["offer"]["label"] == "₹100 off above ₹399"


def test_offer_text_reads_into_numbers():
    assert swiggy_offers.parse_offer("50% OFF UPTO ₹100") == {"kind": "percent", "pct": 50, "cap": 100, "min_order": None,
                                                              "label": "50% off up to ₹100", "text": "50% OFF UPTO ₹100"}
    flat = swiggy_offers.parse_offer("FLAT ₹125 OFF ABOVE ₹249")
    assert flat["kind"] == "flat" and flat["flat"] == 125 and flat["min_order"] == 249
    assert swiggy_offers.parse_offer("ITEMS AT ₹99")["items_at"] == 99
    assert swiggy_offers.parse_offer("Free delivery")["kind"] == "free_delivery"
    assert swiggy_offers.parse_offer("Pure veg") is None
    assert swiggy_offers.restaurant_offer({"aggregatedDiscountInfoV3": {"header": "60% OFF", "subHeader": "UPTO ₹120"}})["cap"] == 120
    assert swiggy_offers.offer_saving(flat, 200) == 0.0 and swiggy_offers.offer_saving(flat, 300) == 125
    assert swiggy_offers.offer_saving(swiggy_offers.parse_offer("50% OFF UPTO ₹100"), 300) == 100


def test_one_best_value_dish_per_menu_within_the_per_meal_budget(gt, monkeypatch):
    w = _world(monkeypatch, budget=4200, refresh=False)                # ₹600 a day, three meals: ₹200 a meal
    m = _menu(w, "r-101", "Annapoorna Tiffin Room")
    best = [i for i in m["items"] if i.get("best_value")]
    assert len(best) == 1 and best[0]["price"] <= 200
    assert m["value"]["per_meal"] == 200 and "₹200 a meal" in m["value"]["why"]
    assert best[0]["name"] not in ("Filter Coffee", "Kesari")           # never a drink or sweet
    assert any(i["bestseller"] for i in m["items"])                     # Swiggy's own flag kept
    tight = _world(monkeypatch, budget=350, refresh=False)              # ₹50 a day: ₹17 a meal, nothing fits
    assert not any(i.get("best_value") for i in _menu(tight, "r-101", "A")["items"])


def test_coupons_are_read_only_at_the_cart_and_never_applied(gt, monkeypatch):
    w = _world(monkeypatch, deals=True, refresh=False)
    _menu(w, "r-101", "A")
    assert "fetch_food_coupons" not in w["fake"].tool_calls()           # not on the menu
    r = w["c"].get(f"/api/user/{w['uid']}/swiggy/live-cart/offers").get_json()
    assert r["coupons"][0]["code"] == "TRYNEW" and r["coupons"][0]["discount"] == 50
    assert "apply_food_coupon" not in w["fake"].tool_calls()


# ------------------------------------------------------------------ #13 the one-cart rule
def _fill(w, item_id, name):
    body = {"restaurant_id": "r-101", "restaurant_name": "Annapoorna Tiffin Room", "item_id": item_id, "item_name": name}
    p = w["c"].post(f"/api/user/{w['uid']}/swiggy/live-cart/preview", json=body).get_json()
    return w["c"].post(f"/api/user/{w['uid']}/swiggy/live-cart", json={**body, "expected_fingerprint": p["fingerprint"]})


def test_refilling_the_same_dish_returns_the_cart_and_another_dish_is_409(gt, monkeypatch):
    w = _world(monkeypatch, refresh=False)
    first = _fill(w, "r-101-i0", "Ghee Roast Dosa")
    assert first.status_code == 200, first.get_json()
    writes = w["fake"].tool_calls().count("update_food_cart")
    again = _fill(w, "r-101-i0", "Ghee Roast Dosa")
    assert again.status_code == 200 and again.get_json()["to_pay"] == first.get_json()["to_pay"]
    assert w["fake"].tool_calls().count("update_food_cart") == writes   # nothing re-sent
    other = _fill(w, "r-101-i1", "Masala Dosa")
    assert other.status_code == 409 and other.get_json()["code"] == "swiggy_cart_busy"


# ------------------------------------------------------------------ #19 dishes with options
def test_a_dish_with_options_can_be_a_usual_meal_on_today(gt, monkeypatch):
    w = _world(monkeypatch, refresh=False)
    r = w["c"].post(f"/api/user/{w['uid']}/today/pool", json={"source": "swiggy", "meal": "dinner", "restaurant_id": "r-102",
                                                             "restaurant_name": "Biryani Junction", "item_id": "r-102-i8"})
    assert r.status_code == 200, r.get_json()
    assert any(e["name"] == "Family Pack Biryani" for e in r.get_json()["pools"]["dinner"])
    legacy = w["c"].post(f"/api/user/{w['uid']}/pools", json={"meal": "lunch", "restaurant_id": "r-103",
                                                             "restaurant_name": "x", "item_id": "r-103-i6"})
    assert legacy.status_code in (400, 502)                             # the classic board still says no


# ------------------------------------------------------------------ #26 Nearby now
def test_nearby_now_comes_from_the_catalogue_within_rules_and_budget(gt, monkeypatch):
    w = _world(monkeypatch, diet="veg")
    c, uid = w["c"], w["uid"]
    c.post(f"/api/user/{uid}/today/rules", json={"min_rating": 4, "max_eta": 0})
    before = len(w["fake"].tool_calls())
    r = c.get(f"/api/user/{uid}/swiggy/nearby?meal=lunch").get_json()
    assert len(w["fake"].tool_calls()) == before                       # no Swiggy call
    dishes = r["dishes"]
    assert dishes and len(dishes) <= 5 and r["within"]
    assert all(d["price"] <= 200 and d["veg"] for d in dishes)           # ₹600 a day / 3 meals
    assert all(d["restaurant"] != "Wok Street" for d in dishes)         # 3.9★ is under the 4★ rule
    assert [d["value"] for d in dishes] == sorted((d["value"] for d in dishes), reverse=True)
    assert max(sum(1 for d in dishes if d["restaurant_id"] == x["restaurant_id"]) for x in dishes) <= 2
    br = c.get(f"/api/user/{uid}/swiggy/nearby?meal=breakfast").get_json()["dishes"]
    assert br and all(d["restaurant"] == "Annapoorna Tiffin Room" for d in br)   # breakfast dishes only


# ------------------------------------------------------------------ #16 the catalogue as a job
def test_the_catalogue_refresh_runs_as_a_background_job(gt, monkeypatch):
    from smartplate import catalog_jobs
    w = _world(monkeypatch, refresh=False)
    r = w["c"].post(f"/api/plan/{w['plan']}/live-menus?background=1", json={})
    assert r.status_code == 202 and r.get_json()["state"] in ("running", "done")
    catalog_jobs.wait(w["uid"])
    s = w["c"].get(f"/api/plan/{w['plan']}/live-menus/status").get_json()
    assert s["state"] == "done", s
    assert w["c"].get(f"/api/plan/{w['plan']}").get_json()["source"]["kind"] == "live"


# ------------------------------------------------------------------ #20 a fresh live price
def test_the_review_price_is_exact_when_the_sectioned_menu_shows_the_same_price(gt, monkeypatch):
    w = _world(monkeypatch, paged=True, refresh=False)
    _menu(w, "r-101", "Annapoorna Tiffin Room")
    swiggy_menu_cache.wait_idle()
    # the town's search_menu answers in paise (unit inferred), the sectioned menu in rupees
    w["fake"].places["r-101"][6][0] = ("Ghee Roast Dosa", 14000, True, "Dosa", True, False)
    p = w["c"].post(f"/api/user/{w['uid']}/swiggy/live-cart/preview", json={
        "restaurant_id": "r-101", "restaurant_name": "Annapoorna Tiffin Room", "item_id": "r-101-i0",
        "item_name": "Ghee Roast Dosa"}).get_json()
    assert p["menu_price"] == 140 and p["price_estimated"] is False
