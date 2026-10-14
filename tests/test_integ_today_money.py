"""v3 Today carries W4's money truth (integration, Oct 2026): each Swiggy pick's whole bill
(delivery + platform fee + packaging + GST, an estimate until a real bill is seen) and, on
Sunday, the week recap from real Swiggy bills only."""
import datetime as dt

from smartplate.app import create_app
from smartplate.domain import fees
from test_today_v3 import _profile, _typed, town  # noqa: F401  (fixture)


def test_swiggy_pick_shows_its_whole_bill_typed_pick_does_not(town):  # noqa: F811
    c, uid = town["c"], town["uid"]
    t = c.post(f"/api/user/{uid}/today/pool", json={"source": "swiggy", "meal": "breakfast", "restaurant_id": "r-101",
                                                     "restaurant_name": "Annapoorna Tiffin Room", "item_id": "r-101-i5",
                                                     "rating": 4.4, "eta_min": 25}).get_json()
    ref = t["pools"]["breakfast"][0]["ref"]
    _typed(c, uid, "lunch", "Home Thali", 150)
    lunch_ref = c.get(f"/api/user/{uid}/today").get_json()["pools"]["lunch"][0]["ref"]
    c.post(f"/api/user/{uid}/today/pick", json={"meal": "lunch", "ref": lunch_ref})
    t = c.post(f"/api/user/{uid}/today/pick", json={"meal": "breakfast", "ref": ref, "qty": 2}).get_json()
    p = t["picks"]["breakfast"]
    bill = p["bill"]
    assert bill["items"] == 180 and bill["estimated"] is True                      # Ven Pongal ₹90 × 2
    expected = round(180 + fees.DELIVERY + fees.DEFAULT_FIXED + round(fees.GST_FOOD_RATE * 180, 2), 2)
    assert bill["total"] == expected and bill["range"][0] < bill["total"] < bill["range"][1]
    assert "estimate" in bill["label"]
    assert "bill" not in t["picks"]["lunch"]                                        # typed: no invented fees
    # founder 14 Oct 2026: today's money counts fees (picked/left), the dish sum stays visible
    assert t["budget"]["picked_dishes"] == 330 and t["budget"]["picked_bill"] == round(expected + 150, 2)
    assert t["budget"]["picked"] == round(expected + 150, 2) and t["budget"]["fees_included"] is True
    assert t["budget"]["left"] == round(t["budget"]["amount"] - expected - 150, 2)
    assert p["total"] == expected and p["total_basis"] == "estimate"
    assert t["budget"]["bill_estimated"] is True


def test_real_cart_bill_replaces_the_estimate_in_todays_left(town):  # noqa: F811
    from smartplate import today
    c, uid = town["c"], town["uid"]
    t = c.post(f"/api/user/{uid}/today/pool", json={"source": "swiggy", "meal": "breakfast", "restaurant_id": "r-101",
                                                     "restaurant_name": "Annapoorna Tiffin Room", "item_id": "r-101-i5",
                                                     "rating": 4.4, "eta_min": 25}).get_json()
    ref = t["pools"]["breakfast"][0]["ref"]
    t = c.post(f"/api/user/{uid}/today/pick", json={"meal": "breakfast", "ref": ref}).get_json()
    p = t["picks"]["breakfast"]
    today.record_cart_bill(uid, p["restaurant_id"], p["item_id"], 1, 131.5)      # the Swiggy cart's to_pay
    t = c.get(f"/api/user/{uid}/today").get_json()
    assert t["picks"]["breakfast"]["total"] == 131.5 and t["picks"]["breakfast"]["total_basis"] == "swiggy_cart"
    assert t["budget"]["left"] == round(t["budget"]["amount"] - 131.5, 2)


def test_decide_weighs_swiggy_dishes_with_their_fees(town):  # noqa: F811
    """Today's Decide spends today's money fees included: a ₹90 dish whose whole bill is
    ~₹127 doesn't fit ₹100 left."""
    c, uid = town["c"], town["uid"]
    c.post(f"/api/user/{uid}/today/pool", json={"source": "swiggy", "meal": "breakfast", "restaurant_id": "r-101",
                                                 "restaurant_name": "Annapoorna Tiffin Room", "item_id": "r-101-i5",
                                                 "rating": 4.4, "eta_min": 25})
    c.post(f"/api/user/{uid}/today/budget", json={"amount": 100})
    t = c.post(f"/api/user/{uid}/today/decide", json={"mode": "favourites"}).get_json()
    assert "breakfast" not in t["picks"], t["picks"]
    c.post(f"/api/user/{uid}/today/budget", json={"amount": 400})
    t = c.post(f"/api/user/{uid}/today/decide", json={"mode": "favourites"}).get_json()
    assert "breakfast" in t["picks"] and t["budget"]["picked"] > 90 and t["budget"]["left"] >= 0
    assert t["decided"]["spent"] == t["picks"]["breakfast"]["total"]


def test_no_bill_numbers_without_a_swiggy_pick(gt):
    c = create_app().test_client()
    uid = _profile(c)
    t = c.get(f"/api/user/{uid}/today").get_json()
    assert t["budget"]["picked_bill"] is None and t["recap"] is None                # Monday: no recap


def test_sunday_recap_uses_real_bills_only(gt):
    gt.set(dt.datetime(2026, 11, 8, 19, 0))                                         # Sunday evening
    c = create_app().test_client()
    uid = _profile(c)
    r = c.get(f"/api/user/{uid}/today").get_json()["recap"]
    assert r is not None and r["target"] == 4200
    assert r["actual"] == 0 and r["kept"] is None and r["share_text"] is None       # no history read: nothing claimed
    assert "order history" in r["note"]
