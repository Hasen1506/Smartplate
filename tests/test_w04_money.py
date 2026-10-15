"""W4 money truth: fees in every estimate, real spend, recap, budget guard rails, cart cap.

Grill critiques 6, 24, 27, 33, 47, 50, 51, 52 and usability findings 19, 22-24, 55.
Deterministic: frozen clock (conftest), no network, recorded/fake Swiggy only.
"""
import datetime as dt

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from test_delivery_address import FakeAccountCart, ITEM, MENU, _add_item, _home_user  # noqa: F401
from smartplate import clock, db
from smartplate.app import create_app
from smartplate.domain import checkout, fees, models, spend
from smartplate.integrations import swiggy_connect

# The repo's one real bill (docs/IDEAS.md, 8 Oct 2026): ₹160 of food → ₹185.06.
REAL_BILL = {"lines": [{"label": "Item Total", "amount": 160.0}, {"label": "Delivery Fee", "amount": 6.0},
                       {"label": "GST & Other Charges", "amount": 19.06}], "to_pay": 185.06}


@pytest.fixture
def client(seeded):
    app = create_app()
    app.config["TESTING"] = True
    return app.test_client()


@pytest.fixture
def swiggy(monkeypatch):
    fake = FakeAccountCart()
    fake.dishes = dict(MENU)
    monkeypatch.setattr(swiggy_connect, "_http", fake)
    return fake


def _connect_addr(user_id=3, address="addr-home"):
    from smartplate.integrations import swiggy_connect as sc
    sc.init_schema()
    with db.cursor() as cur:
        cur.execute("INSERT OR REPLACE INTO swiggy_connections(user_id, access_token, connected_ts, address_id) "
                    "VALUES (?,?,?,?)", (user_id, "sealed-test-token", "2026-11-01T08:00", address))


# --------------------------------------------------------------------------- #
# 50 — fees, GST, packaging, delivery: learned from bills, labelled defaults
# --------------------------------------------------------------------------- #
def test_real_bill_160_to_185_is_split_and_learned():
    comp = fees.components(REAL_BILL)
    assert comp == {"item_total": 160.0, "delivery": 6.0, "other": 19.06, "to_pay": 185.06, "discount": 0.0}
    assert fees.fixed_part(comp) == 11.06                    # 19.06 − 5% GST on ₹160
    m = {"restaurants": {"r-1": {"fixed": 11.06, "delivery": 6.0, "bills": 1}}, "user": None}
    item = {"price": 160, "fees": fees.pricing_for("r-1", m)}
    # the same order again is priced at exactly the real bill
    assert round(160 + item["fees"]["delivery"]["amount"] + fees.charges(item, 160), 2) == 185.06
    b = fees.breakdown(item, 300)                            # a bigger order there: GST scales, the rest doesn't
    assert b["total"] == 332.06 and b["estimated"] is False and b["range"] == [332.06, 332.06]
    assert "from your bills here" in b["label"]


def test_no_bill_yet_uses_the_research_default_labelled_with_a_range():
    p = fees.pricing_for("r-new", {"restaurants": {}, "user": None})
    assert p["delivery"] == {"amount": fees.DELIVERY, "basis": "default", "estimated": True, "range": [0.0, 60.0]}
    assert p["fixed"]["amount"] == fees.DEFAULT_FIXED == round(17.58 + 10 * 1.05, 2)
    b = fees.breakdown({"fees": p}, 160)
    assert b["estimated"] and b["range"][0] < b["total"] < b["range"][1]
    assert "estimate" in b["label"]
    # the old flat ₹35 delivery placeholder is gone (usability 24: real bill was ₹6)
    from smartplate.domain import live_catalog
    assert live_catalog.LIVE_DELIVERY_FEE_ESTIMATE == fees.DELIVERY < 35


def test_learning_precedence_this_restaurant_then_your_bills_then_default(seeded):
    _connect_addr()
    fees.record_bill(3, "addr-home", "r-1", "A", REAL_BILL)
    fees.record_bill(3, "addr-home", "r-2", "B", {"lines": [{"label": "Item Total", "amount": 200.0},
                                                            {"label": "Delivery Fee", "amount": 20.0}],
                                                  "to_pay": 250.0})
    m = fees.model(3)
    one = fees.pricing_for("r-1", m)
    assert one["delivery"] == {"amount": 6.0, "basis": "bill", "estimated": False}
    assert one["fixed"]["basis"] == "bill" and one["fixed"]["amount"] == 11.06
    other = fees.pricing_for("r-9", m)                       # no bill from r-9: the user's own bills, still an estimate
    assert other["delivery"]["basis"] == "your_bills" and other["delivery"]["estimated"]
    assert other["delivery"]["amount"] == 13.0 and other["delivery"]["range"] == [6.0, 20.0]
    assert other["fixed"]["basis"] == "your_bills"
    # a bill for another address teaches this address nothing
    assert fees.model(3, "addr-work")["restaurants"] == {}


def test_free_delivery_discount_and_unreadable_bills():
    free = fees.components({"lines": [{"label": "Item Total", "amount": 100.0}], "to_pay": 125.0})
    assert free["delivery"] is None and free["other"] == 25.0
    disc = fees.components({"lines": [{"label": "Item Total", "amount": 200.0}, {"label": "Discount", "amount": -40.0},
                                      {"label": "Delivery Fee", "amount": 10.0}], "to_pay": 200.0})
    assert disc["other"] == 30.0                             # the coupon isn't learned as "negative fees"
    assert fees.components({"lines": [], "to_pay": 150.0}) is None              # no item total: learn nothing
    assert fees.components({"lines": [], "to_pay": 150.0}, item_total=120.0)["other"] == 30.0
    assert fees.components({"lines": [{"label": "Item Total", "amount": 200.0}], "to_pay": 150.0}) is None
    assert fees.components(None) is None


def test_old_bills_are_pruned(seeded, monkeypatch):
    _connect_addr()
    t0 = dt.datetime(2026, 11, 2, 8, 0)
    for i in range(fees.KEEP_BILLS + 3):
        monkeypatch.setattr(clock, "now", lambda i=i: t0 + dt.timedelta(minutes=i))
        fees.record_bill(3, "addr-home", "r-1", "A", REAL_BILL)
    with db.cursor() as cur:
        n = cur.execute("SELECT COUNT(*) AS n FROM swiggy_bill_fees WHERE user_id=3").fetchone()["n"]
    assert n == fees.KEEP_BILLS


def test_a_real_cart_bill_teaches_the_fee_model(client, swiggy):
    _home_user(client, swiggy)
    r = _add_item(client)
    assert r.status_code == 200, r.get_json()
    m = fees.model(3)
    assert "r-1" in m["restaurants"]
    p = fees.pricing_for("r-1", m)
    assert p["fixed"]["basis"] == "bill" and p["delivery"]["estimated"] is False


@settings(max_examples=60, deadline=None)
@given(items=st.floats(min_value=20, max_value=900), delivery=st.floats(min_value=0, max_value=80),
       other=st.floats(min_value=0, max_value=120), later=st.floats(min_value=20, max_value=900))
def test_property_learned_fees_reproduce_the_bill_and_scale_only_gst(items, delivery, other, later):
    items, delivery, other, later = (round(v, 2) for v in (items, delivery, other, later))
    bill = {"lines": [{"label": "Item Total", "amount": items}, {"label": "Delivery Fee", "amount": delivery}],
            "to_pay": round(items + delivery + other, 2)}
    comp = fees.components(bill)
    m = {"restaurants": {"r": {"fixed": fees.fixed_part(comp), "delivery": comp["delivery"], "bills": 1}}}
    row = {"fees": fees.pricing_for("r", m)}
    same = fees.breakdown(row, items)
    if other >= 0.05 * items:              # the bill covers 5% GST: it is reproduced to the paisa
        assert abs(same["total"] - bill["to_pay"]) <= 0.02
    assert fees.charges(row, later) + 0.005 >= 0.05 * later                       # GST is never left out
    b = fees.breakdown(row, later)
    assert abs(b["total"] - (later + b["delivery"] + b["fees"] + b["gst"])) <= 0.02
    assert b["range"][0] <= b["total"] <= b["range"][1]


# --------------------------------------------------------------------------- #
# 51 — units explicit; a unit flip on re-pricing halts and asks
# --------------------------------------------------------------------------- #
@settings(max_examples=80, deadline=None)
@given(paise=st.integers(min_value=100, max_value=5_000_000))
def test_property_named_units_agree_and_bare_big_numbers_are_never_guessed(paise):
    rupees = paise / 100
    assert spend.order_total({"orderTotalInPaise": paise}) == (round(rupees, 2), "named")
    assert spend.order_total({"orderTotalInRupees": rupees}) == (round(rupees, 2), "named")
    assert spend.order_total({"orderTotal": f"₹{rupees:,.2f}"}) == (round(rupees, 2), "symbol")
    amount, how = spend.order_total({"orderTotal": paise})
    if paise >= spend.PAISE_AMBIGUOUS:
        assert (amount, how) == (None, "ambiguous")       # could be ₹paise or ₹paise/100: not counted
    else:
        assert (amount, how) == (float(paise), "small")


def test_order_total_edge_cases():
    assert spend.order_total({"orderTotal": "160"}) == (160.0, "display")
    assert spend.order_total({"orderTotal": 185.06}) == (185.06, "fraction")
    assert spend.order_total({"orderTotal": True}) == (None, "missing")
    assert spend.order_total({"orderTotal": "free"}) == (None, "unreadable")
    assert spend.order_total({}) == (None, "missing")


@settings(max_examples=60, deadline=None)
@given(prices=st.lists(st.integers(min_value=50, max_value=600), min_size=1, max_size=8),
       factor=st.sampled_from([0.01, 0.2, 0.9, 1.0, 1.3, 2.5, 3.0, 5.0, 100.0]))
def test_property_reprice_guard_refuses_unit_flips_only(prices, factor):
    old = {("r", str(i)): float(p) for i, p in enumerate(prices)}
    new = {k: round(v * factor, 2) for k, v in old.items()}
    flip = factor >= fees.REPRICE_LIMIT or factor <= 1 / fees.REPRICE_LIMIT
    if flip:
        with pytest.raises(swiggy_connect.SwiggyError) as exc:
            fees.reprice_guard(old, new)
        assert exc.value.code == "price_unit_changed"
    else:
        fees.reprice_guard(old, new)
    fees.reprice_guard({}, new)                               # a first read has nothing to compare


def test_menu_refresh_with_a_unit_flip_keeps_the_previous_catalogue(client, monkeypatch):
    from test_roadmap_b_live_menus import RECORDED_MENU, _live_user
    from test_swiggy_live import FakeLive
    fake = FakeLive()
    fake.dishes = dict(RECORDED_MENU)
    monkeypatch.setattr(swiggy_connect, "_http", fake)
    pid = _live_user(client, fake, 3)
    assert client.post(f"/api/plan/{pid}/live-menus", json={}).status_code == 200
    before = {it["name"]: it["price"] for it in models.menu_for_user(models.get_user(3))}
    fake.dishes = {k: v * 100 for k, v in RECORDED_MENU.items()}       # Swiggy's bare prices jump 100×
    r = client.post(f"/api/plan/{pid}/live-menus", json={})
    assert r.status_code == 409 and r.get_json()["code"] == "price_unit_changed"
    assert {it["name"]: it["price"] for it in models.menu_for_user(models.get_user(3))} == before


# --------------------------------------------------------------------------- #
# Real spend ledger, 24 recap, 33 target vs actual
# --------------------------------------------------------------------------- #
def _orders(*rows):
    return [{"orderId": oid, "restaurantName": "X", "orderTotal": total, "orderStatus": status,
             "orderedTime": when} for oid, total, status, when in rows]


def test_order_history_feeds_the_ledger_without_cancelled_or_ambiguous(seeded):
    out = spend.record_swiggy_orders(3, _orders(
        ("a", "₹185.06", "DELIVERED", "Mon 2026-11-02 1:10 PM IST"),
        ("b", "₹240", "CANCELLED", "2026-11-02T20:00:00+05:30"),
        ("c", 2500, "DELIVERED", "2026-11-01T20:00:00+05:30"),        # bare whole ≥ 1000: unit unknown
        ("d", "120", "DELIVERED", "nonsense")))                        # no readable date
    assert out["kept"] == 1
    assert {s["why"] for s in out["skipped"]} == {"cancelled", "unit", "unreadable"}
    rows = spend.entries(3, "2026-10-01", "2026-12-01")
    assert [(r["ref"], r["amount"], r["iso_date"]) for r in rows] == [("swiggy:a", 185.06, "2026-11-02")]
    # an order cancelled later is removed on the next read
    spend.record_swiggy_orders(3, _orders(("a", "₹185.06", "CANCELLED", "2026-11-02")))
    assert spend.entries(3, "2026-10-01", "2026-12-01") == []


def test_handed_off_cart_is_never_counted_until_swiggy_confirms_it(seeded):
    today = clock.today().isoformat()
    spend.record_handoff(3, 77, 185.06)
    a = spend.actual(3, today, today)
    assert a["amount"] == 0 and a["unconfirmed"] == {"amount": 185.06, "orders": 1}
    spend.record_swiggy_orders(3, _orders(("z", "₹185", "DELIVERED", today + "T13:00:00+05:30")))
    a = spend.actual(3, today, today)
    assert a["amount"] == 185.0 and a["orders"] == 1 and a["unconfirmed"]["orders"] == 0   # counted once


def test_ziggy_placed_order_and_history_are_the_same_order(seeded):
    spend.record_placed(3, None, 185.06, "ord-1", "X")
    spend.record_swiggy_orders(3, _orders(("ord-1", "₹185.06", "DELIVERED", "2026-11-02")))
    assert spend.actual(3, "2026-11-02", "2026-11-08")["orders"] == 1


def _week_of_orders(monkeypatch, amounts_by_date, synced_on="2026-11-09"):
    rows = [(f"o{i}", f"₹{a}", "DELIVERED", d) for i, (d, a) in enumerate(amounts_by_date)]
    monkeypatch.setattr(clock, "now", lambda: dt.datetime.fromisoformat(synced_on + "T09:00"))
    monkeypatch.setattr(clock, "today", lambda: dt.date.fromisoformat(synced_on))
    spend.record_swiggy_orders(3, _orders(*rows))


def test_sunday_recap_from_real_bills_only(seeded, monkeypatch):
    user = models.get_user(3)
    with db.cursor() as cur:
        cur.execute("UPDATE users SET weekly_budget=1500 WHERE id=3")
    _week_of_orders(monkeypatch, [("2026-11-02", 300), ("2026-11-04", 420), ("2026-11-06", 440)])
    r = spend.week_recap(models.get_user(3), "2026-11-02")
    assert r["finished"] and r["complete"]
    assert r["target"] == 1500 and r["actual"] == 1160 and r["kept"] == 340
    assert r["share_text"] == "₹340 kept this week on Ziggy — that's a movie."
    assert r["best_day"]["date"] == "2026-11-02" and r["no_order_days"] == 4      # best among days with orders
    assert r["worst_day"]["date"] == "2026-11-06"
    # plan estimates never become kept money: planned decisions exist but do not move `actual`
    assert user and r["basis"].startswith("Real Swiggy bills only")


def test_recap_claims_nothing_kept_without_a_fresh_order_history(seeded, monkeypatch):
    _week_of_orders(monkeypatch, [("2026-11-02", 300)], synced_on="2026-11-05")   # read mid-week
    monkeypatch.setattr(clock, "today", lambda: dt.date(2026, 11, 9))
    r = spend.week_recap(models.get_user(3), "2026-11-02")
    assert r["kept"] is None and r["share_text"] is None and "order history" in r["note"]


def test_recap_over_target_says_so(seeded, monkeypatch):
    with db.cursor() as cur:
        cur.execute("UPDATE users SET weekly_budget=500 WHERE id=3")
    _week_of_orders(monkeypatch, [("2026-11-02", 300), ("2026-11-03", 400)])
    r = spend.week_recap(models.get_user(3), "2026-11-02")
    assert r["kept"] is None and r["over"] == 200 and "over" in r["note"]


def test_recap_api_routes(client, monkeypatch):
    _week_of_orders(monkeypatch, [("2026-11-02", 300)])
    a = client.get("/api/user/3/recap/week?week=2026-11-04").get_json()
    assert a["week_start"] == "2026-11-02" and a["actual"] == 300
    b = client.get("/api/recap/week?user_id=3&week=2026-11-02").get_json()
    assert b == a
    assert client.get("/api/recap/week").status_code == 400
    assert client.get("/api/user/3/recap/week?week=nope").status_code == 400
    assert client.get("/api/recap/week?user_id=99999").status_code == 404


def test_plan_budget_is_a_target_with_actual_from_bills(client, monkeypatch):
    view = client.get("/api/user/3/plan").get_json()
    b = view["budget"]
    assert b["kind"] == "target" and b["actual"]["amount"] == 0 and b["actual"]["source"].startswith("Swiggy")


# --------------------------------------------------------------------------- #
# 27 — budget suggestion from real spend, with guard rails
# --------------------------------------------------------------------------- #
def _four_weeks(monkeypatch, per_week=3000, orders_per_week=10):
    start = dt.date(2026, 10, 6)
    rows = []
    for w in range(4):
        for i in range(orders_per_week):
            d = start + dt.timedelta(days=7 * w + i % 7)
            rows.append((d.isoformat(), round(per_week / orders_per_week, 2)))
    _week_of_orders(monkeypatch, rows, synced_on="2026-11-02")


def test_no_real_spend_no_suggestion(seeded):
    s = spend.suggest(models.get_user(3))
    assert s["available"] is False and "order history" in s["why"]


def test_no_automatic_reduction_target(seeded, monkeypatch):
    """Founder decision 14 Oct 2026: the facts, and no suggested cut unless asked."""
    _four_weeks(monkeypatch)
    s = spend.suggest(models.get_user(3))
    assert s["available"] and s["weekly_spend"] == 3000 and s["asked"] is False
    assert s["suggested"] is None and s["options"] == []
    assert s["headline"] == "You spent ₹12,000 in the last 4 weeks (₹3,000 a week)."
    assert "Aim for" not in s["headline"] and "ask" in s["offer"].lower()


def test_asked_to_cut_offers_10_15_20_in_rupees_with_15_highlighted(seeded, monkeypatch):
    _four_weeks(monkeypatch)
    monkeypatch.setattr(spend, "cheapest_meal", lambda user: {"cost": 120.0, "item": "Mini Tiffin",
                                                              "restaurant": "A", "estimated": False})
    s = spend.suggest(models.get_user(3), asked=True)
    assert [o["pct"] for o in s["options"]] == [10, 15, 20]
    assert [o["target"] for o in s["options"]] == [2700, 2550, 2400]
    assert [o["saving_week"] for o in s["options"]] == [300, 450, 600]        # "15% of ₹3,000 = ₹450 a week"
    assert [o["default"] for o in s["options"]] == [False, True, False] and s["default_pct"] == 15
    fifteen = s["options"][1]
    assert fifteen["label"] == "15% less: ₹2,550 a week (₹450 less)"
    assert any("cheapest favourite" in c for c in fifteen["what_changes"])
    assert any(c.startswith("Or cook") for c in fifteen["what_changes"])
    assert s["suggested"] == 2550                      # highlighted, never applied by itself


def test_own_cut_below_the_favourite_floor_is_flagged_not_obeyed(seeded, monkeypatch):
    _four_weeks(monkeypatch)
    monkeypatch.setattr(spend, "cheapest_meal", lambda user: {"cost": 280.0, "item": "Thali",
                                                              "restaurant": "A", "estimated": False})
    s = spend.suggest(models.get_user(3), cut=0.2)               # floor: ₹280 × 10 orders = ₹2,800
    assert s["suggested"] == 2400 and s["floor"] == 2800 and s["clamped"]
    assert any("cheapest favourite" in r for r in s["reasons"])
    assert s["options"][2]["below_floor"] and "only cooking more" in s["options"][2]["what_changes"][0]


def test_no_favourites_no_floor(seeded, monkeypatch):
    _four_weeks(monkeypatch)
    monkeypatch.setattr(spend, "cheapest_meal", lambda user: None)
    s = spend.suggest(models.get_user(3), asked=True)
    assert s["floor"] is None and not any(o["below_floor"] for o in s["options"])


def test_budget_suggestion_api_validates(client, monkeypatch):
    _four_weeks(monkeypatch)
    r = client.get("/api/user/3/budget/suggestion").get_json()
    assert r["available"] and r["options"] == [] and "low_budget" in r
    r = client.get("/api/user/3/budget/suggestion?ask=cut").get_json()
    assert [o["pct"] for o in r["options"]] == [10, 15, 20]
    r = client.get("/api/user/3/budget/suggestion?cut=0.15").get_json()
    assert r["available"] and r["asked"] and "low_budget" in r
    assert client.get("/api/user/3/budget/suggestion?cut=1.5").status_code == 400
    assert client.get("/api/user/3/budget/suggestion?target=-5").status_code == 400


def test_low_budget_warns_from_your_cheapest_favourite_only(client):
    """The ₹60 'typical meal' floor is gone: no favourites, no floor warning."""
    rec = client.post("/api/suggest-budget", json={"weekly_budget": 300, "meals": ["breakfast", "lunch", "dinner"],
                                                   "diet": "veg"}).get_json()
    assert rec["low_budget"] is None
    places = [r["id"] for r in client.get("/api/restaurants").get_json()][:2] \
        if client.get("/api/restaurants").status_code == 200 else [1, 2]
    rec = client.post("/api/suggest-budget", json={"weekly_budget": 300, "meals": ["breakfast", "lunch", "dinner"],
                                                   "diet": "veg", "favourites": places}).get_json()
    low = rec["low_budget"]
    assert low and low["meals"] == 21 and low["per_meal"] == round(300 / 21, 2)
    assert low["covers"] < 21 and "cook the rest" in low["body"] and "cheapest favourite" in low["body"]
    assert {a["act"] for a in low["actions"]} == {"plan-delivered", "raise-budget"}
    assert "meal_floor" not in client.get("/api/meta").get_json()
    from smartplate import config
    assert not hasattr(config, "MEAL_FLOOR_RUPEES") and not hasattr(spend, "DEFAULT_CHEAPEST_DISH")


def test_low_budget_heads_up_needs_a_favourite(client):
    view = client.patch("/api/user/1/setup", json={"weekly_budget": 300}).get_json()
    kinds = {h["kind"] for h in view["heads_up"]}
    if spend.cheapest_meal(models.get_user(1)) is None:
        assert "low_budget" not in kinds
    else:
        assert "low_budget" in kinds
    ok = client.patch("/api/user/1/setup", json={"weekly_budget": 20000}).get_json()
    assert "low_budget" not in {h["kind"] for h in ok["heads_up"]}


# --------------------------------------------------------------------------- #
# 47 — ₹1,000 Builders Club cart cap: split or hand off
# --------------------------------------------------------------------------- #
def test_cart_cap_one_cart_split_and_handoff():
    assert checkout.split_for_cap(125, 1)["mode"] == "one_cart"
    s = checkout.split_for_cap(250, 5)
    assert s["mode"] == "split" and [c["quantity"] for c in s["carts"]] == [3, 2]
    assert all(c["estimate"] <= 1000 for c in s["carts"]) and s["extra_fees"] > 0
    assert "2 carts (3 + 2)" in s["message"] and "each for your approval" in s["message"]
    h = checkout.split_for_cap(990, 1)
    assert h["mode"] == "handoff" and not h["carts"] and "Swiggy app" in h["message"]
    with pytest.raises(ValueError):
        checkout.split_for_cap(100, 0)


@settings(max_examples=80, deadline=None)
@given(price=st.integers(min_value=40, max_value=1200), qty=st.integers(min_value=1, max_value=8))
def test_property_cart_split_covers_the_order_within_the_cap(price, qty):
    s = checkout.split_for_cap(float(price), qty)
    if s["mode"] == "handoff":
        assert checkout.split_for_cap(float(price), 1)["mode"] == "handoff"
        return
    sizes = [c["quantity"] for c in s["carts"]]
    assert sum(sizes) == qty and max(sizes) - min(sizes) <= 1
    assert all(c["estimate"] <= checkout.CART_CAP for c in s["carts"])
    assert s["estimate_total"] >= s["estimate_total"] - s["extra_fees"]          # splitting never looks cheaper


def test_live_review_over_the_cap_splits_into_sequential_carts_by_default(client, swiggy):
    """Founder decision 14 Oct 2026: over ₹1,000 the default is a split (sequential carts,
    each reviewed and approved), not a hand-off."""
    _home_user(client, swiggy)
    ok = client.post("/api/user/3/swiggy/live-cart/preview", json={**ITEM, "quantity": 2}).get_json()
    assert ok["orderable"] and ok["cart_cap"]["mode"] == "one_cart" and "split" not in ok
    big = client.post("/api/user/3/swiggy/live-cart/preview", json={**ITEM, "quantity": 8}).get_json()
    assert big["orderable"] is True and big["fingerprint"]
    assert big["cart_cap"]["mode"] == "split" and [c["quantity"] for c in big["cart_cap"]["carts"]] == [4, 4]
    sp = big["split"]
    assert sp["part"] == 1 and sp["of"] == 2 and sp["quantity"] == 4 and sp["next_quantity"] == 4
    assert big["quantity"] == 4 and big["menu_total"] == 500.0
    assert "cart 1 of 2" in sp["message"] and "₹1,000" in sp["message"]
    # cart 1 into the (one) Swiggy cart: exactly 4 portions
    r = client.post("/api/user/3/swiggy/live-cart", json={**ITEM, "quantity": 8,
                                                          "expected_fingerprint": big["fingerprint"]})
    assert r.status_code == 200, r.get_json()
    assert swiggy.qty == 4 and r.get_json()["to_pay"] <= 1000
    # cart 2 can't go in while cart 1 sits in the cart (one live cart): paid first
    two = client.post("/api/user/3/swiggy/live-cart/preview", json={**ITEM, "quantity": 8, "part": 2}).get_json()
    assert two["orderable"] and two["split"]["part"] == 2 and two["quantity"] == 4
    assert two["fingerprint"] != big["fingerprint"] and "cart 2 of 2" in two["split"]["message"]
    blocked = client.post("/api/user/3/swiggy/live-cart", json={**ITEM, "quantity": 8, "part": 2,
                                                                "expected_fingerprint": two["fingerprint"]})
    assert blocked.status_code >= 400 and "already has items" in blocked.get_json()["error"]
    swiggy.cart = None                                   # cart 1 paid in Swiggy
    r2 = client.post("/api/user/3/swiggy/live-cart", json={**ITEM, "quantity": 8, "part": 2,
                                                           "expected_fingerprint": two["fingerprint"]})
    assert r2.status_code == 200 and swiggy.qty == 4
    assert client.post("/api/user/3/swiggy/live-cart/preview",
                       json={**ITEM, "quantity": 8, "part": 3}).status_code == 400


def test_one_portion_over_the_cap_still_hands_off(client, swiggy):
    _home_user(client, swiggy)
    swiggy.dishes = {"Veg Meals": 18000, "Mini Tiffin": 99000}
    r = client.post("/api/user/3/swiggy/live-cart/preview", json={**ITEM, "quantity": 1}).get_json()
    assert r["orderable"] is False and r["cart_cap"]["mode"] == "handoff" and "Swiggy app" in r["reason"]


def test_golden_money_week(seeded, monkeypatch):
    """Golden: one real week of bills → recap, suggestion and a fee breakdown, exactly."""
    from gt_support import assert_golden
    with db.cursor() as cur:
        cur.execute("UPDATE users SET weekly_budget=1500 WHERE id=3")
    _week_of_orders(monkeypatch, [("2026-11-02", 185.06), ("2026-11-03", 240), ("2026-11-05", 410.5),
                                  ("2026-11-07", 320)])
    monkeypatch.setattr(spend, "cheapest_meal", lambda user: {"cost": 131.25, "item": "Mini Tiffin",
                                                              "restaurant": "Hotel Saravana Bhavan", "estimated": False})
    m = {"restaurants": {"r-1": {"fixed": 11.06, "delivery": 6.0, "bills": 1}}, "user": None}
    assert_golden("money_week_w04", {
        "recap": spend.week_recap(models.get_user(3), "2026-11-02"),
        "suggestion": spend.suggest(models.get_user(3), asked=True),
        "estimate_learned": fees.breakdown({"fees": fees.pricing_for("r-1", m)}, 250),
        "estimate_default": fees.breakdown({"fees": fees.pricing_for("r-2", m)}, 250),
        "split": checkout.split_for_cap(250, 5),
    })
