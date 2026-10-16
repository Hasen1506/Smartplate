"""Fix round 3, "today-truth" (usab audit 16 Oct 2026): one source of truth for today (#1),
per-person meal times (#14), cart-open reminders (#21), per-meal reminders with an .ics
fallback (#22), a week that holds only what the person chose (#24), honest Decide notes (#28),
Ziggy / learned rule defaults (#10) and the eggetarian and Jain diets (#11).

Frozen clock (Mon 2 Nov 2026 08:00 IST, the `gt` fixture); the stand-in Swiggy is
tests/swiggy_town.py. Nothing real is touched."""
import datetime as dt

import pytest
from icalendar import Calendar

from smartplate import db, push, today
from smartplate.app import create_app
from smartplate.domain import allergens
from test_push import _browser_subscription
from test_today_v3 import _profile, _today, _typed, plain, town  # noqa: F401  (fixtures)


def _plan(c, uid):
    return c.get(f"/api/user/{uid}/plan").get_json()


def _today_cell(view, meal):
    day = next(d for d in view["grid"] if d.get("date") == "2026-11-02")
    return day["meals"].get(meal)


# --------------------------------------------------------------------------- #1 one truth for today
def test_today_pick_is_the_weeks_meal_its_money_and_its_reminder(plain):
    c, uid = plain["c"], plain["uid"]
    t = _typed(c, uid, "lunch", "Veg Thali", 180, place="Corner Cafe")
    ref = t["pools"]["lunch"][0]["ref"]
    t = c.post(f"/api/user/{uid}/today/pick", json={"meal": "lunch", "ref": ref}).get_json()
    v = _plan(c, uid)
    cell = _today_cell(v, "lunch")
    assert cell["item"] == "Veg Thali" and cell["restaurant"] == "Corner Cafe" and cell["today_pick"]
    assert cell["kind"] == "delivery" and cell["cost"] == 180 and cell["usual"] is True
    # Today and Week give the same order-by and cart time for it
    when = t["picks"]["lunch"]["when"]
    assert when["order_at"] == cell["order"]["order_at"] and when["cart_opens_label"] == cell["order"]["cart_opens_label"]
    assert when["session_id"] == cell["session_id"]
    # the reminder names the dish picked on Today, at the moment its cart opens
    rows = c.get(f"/api/user/{uid}/reminders").get_json()
    mine = [r for r in rows if r["session_id"] == cell["session_id"]]
    assert mine and mine[0]["title"] == "Cart open: order Veg Thali · Corner Cafe · ₹180"
    assert mine[0]["at"].endswith(when["cart_opens_label"]) and mine[0]["order_by"].endswith(when["order_at"])
    # "Decide for me" on the Week leaves a meal already picked on Today alone
    pid = v["plan"]["id"]
    out = c.post(f"/api/plan/{pid}/decide", json={"mode": "surprise_me"}).get_json()
    assert cell["session_id"] not in [p["session_id"] for p in out["picks"]]
    # clearing the pick on Today hands the meal back to the Week
    c.post(f"/api/user/{uid}/today/pick", json={"meal": "lunch", "ref": None})
    assert not _today_cell(_plan(c, uid), "lunch").get("today_pick")


def test_a_week_pick_for_today_replaces_the_today_pick(plain):
    c, uid = plain["c"], plain["uid"]
    t = _typed(c, uid, "lunch", "Veg Thali", 180)
    c.post(f"/api/user/{uid}/today/pick", json={"meal": "lunch", "ref": t["pools"]["lunch"][0]["ref"]})
    today.clear_pick(uid, "lunch")                     # what a Week pin of today's lunch does
    assert "lunch" not in _today(c, uid)["picks"] and "lunch" not in today.picked_today(uid)


# --------------------------------------------------------------------------- #14 meal times
def test_late_dinner_time_moves_order_by_cart_window_and_reminders(gt):
    c = create_app().test_client()
    uid = _profile(c, rhythm={"breakfast": "skip", "lunch": "order", "dinner": "order"})
    t = _today(c, uid)
    assert t["meal_times"]["dinner"] == {"at": "20:30", "own": False}
    assert t["ask_meal_time"]["meal"] == "dinner" and "23:00" in t["ask_meal_time"]["options"]
    assert c.post(f"/api/user/{uid}/today/meal-times", json={"dinner": "25:00"}).status_code == 400
    assert c.post(f"/api/user/{uid}/today/meal-times", json={"dinner": "16:00"}).status_code == 400
    assert c.post(f"/api/user/{uid}/today/meal-times", json={}).status_code == 400
    t = c.post(f"/api/user/{uid}/today/meal-times", json={"dinner": "23:00"}).get_json()
    assert t["meal_times"]["dinner"] == {"at": "23:00", "own": True} and t["ask_meal_time"] is None
    typed = _typed(c, uid, "dinner", "Paneer Roll", 150)
    t = c.post(f"/api/user/{uid}/today/pick", json={"meal": "dinner", "ref": typed["pools"]["dinner"][0]["ref"]}).get_json()
    when = t["picks"]["dinner"]["when"]
    assert when["order_at"] == "22:15" and when["cart_opens_label"] == "20:45"       # 23:00 − 35 − 10; −90
    cell = _today_cell(_plan(c, uid), "dinner")
    assert cell["order"]["order_at"] == "22:15"
    with db.cursor() as cur:
        ts = cur.execute("SELECT scheduled_ts FROM sessions WHERE id=?", (cell["session_id"],)).fetchone()[0]
    assert ts.endswith("23:00:00")                     # the session (and "past") follow the time
    r = next(r for r in c.get(f"/api/user/{uid}/reminders").get_json() if r["meal"] == "dinner")
    assert r["at"].endswith("20:45") and r["closes"].endswith("23:59")      # window shifted, same day
    # back to Ziggy's time
    t = c.post(f"/api/user/{uid}/today/meal-times", json={"dinner": None}).get_json()
    assert t["meal_times"]["dinner"]["own"] is False


def test_meal_time_question_is_asked_once_and_uses_history_buckets(gt):
    from smartplate.integrations import swiggy_connect as sc
    c = create_app().test_client()
    uid = _profile(c, rhythm={"breakfast": "skip", "lunch": "skip", "dinner": "order"})
    sc.init_schema()
    with db.cursor() as cur:
        for i, slot in enumerate(("late", "late", "late", "dinner")):
            cur.execute("INSERT INTO swiggy_history_suggestions(user_id, restaurant_id, restaurant_name, item_name, "
                        "menu_item_id, slot, times, last_date, rating, score, imported_ts) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (uid, f"r{i}", f"Place {i}", f"Dish {i}", None, slot, 1, "2026-10-30", None, 1.0,
                         "2026-10-31T10:00"))
    ask = _today(c, uid)["ask_meal_time"]
    assert ask["hint"]["at"] == "22:30" and "after 10 pm" in ask["hint"]["why"]
    t = c.post(f"/api/user/{uid}/today/meal-times", json={"dismiss": True}).get_json()
    assert t["ask_meal_time"] is None and t["meal_times"]["dinner"]["own"] is False


# --------------------------------------------------------------------------- #22 per-meal reminder + .ics
def test_remind_me_for_one_meal_only_and_its_own_calendar_file(plain):
    c, uid = plain["c"], plain["uid"]
    t = _typed(c, uid, "breakfast", "Idli", 60)
    c.post(f"/api/user/{uid}/today/pick", json={"meal": "breakfast", "ref": t["pools"]["breakfast"][0]["ref"]})
    t = _typed(c, uid, "lunch", "Veg Thali", 180)
    c.post(f"/api/user/{uid}/today/pick", json={"meal": "lunch", "ref": t["pools"]["lunch"][0]["ref"]})
    t = c.post(f"/api/user/{uid}/today/remind", json={"meal": "lunch"}).get_json()
    assert t["picks"]["lunch"]["when"]["reminder"] is True and t["picks"]["breakfast"]["when"]["reminder"] is False
    assert today.reminder_filter(uid) == {("2026-11-02", "lunch")}
    # a device that opted in from "Remind me" gets only that meal's nudge
    c.post(f"/api/user/{uid}/push/subscribe", json={"subscription": _browser_subscription(), "scope": "meals"})
    with db.cursor() as cur:
        cur.execute("UPDATE push_subscriptions SET created_ts=?", ("2000-01-01T00:00:00",))
    pairs = push.due(dt.datetime(2026, 11, 2, 23, 0))
    assert {r["meal"] for _, r in pairs} <= {"lunch"} and any(r["meal"] == "lunch" for _, r in pairs)
    assert c.get(f"/api/user/{uid}/push").get_json()["scope"] == "meals"
    # turning on reminders for every meal widens it again
    c.post(f"/api/user/{uid}/push/subscribe", json={"subscription": _browser_subscription(), "scope": "all"})
    assert today.reminder_filter(uid) is None
    # the calendar fallback for just this meal
    r = c.get(f"/api/user/{uid}/reminders.ics?meal=lunch&date=2026-11-02")
    events = Calendar.from_ical(r.data).walk("VEVENT")
    assert r.status_code == 200 and len(events) == 1 and "Veg Thali" in str(events[0]["summary"])
    assert c.post(f"/api/user/{uid}/today/remind", json={"meal": "brunch"}).status_code == 400


# --------------------------------------------------------------------------- #24 chosen-only week
@pytest.mark.chosen_week
def test_new_profile_week_holds_only_what_was_chosen(gt):
    c = create_app().test_client()
    uid = _profile(c)
    v = _plan(c, uid)
    cells = [m for d in v["grid"] for m in d["meals"].values() if m["status"] != "past"]
    assert cells and all(m["kind"] in ("skip", "skipped") and m.get("open") for m in cells)
    assert all(m["reasons"][0].startswith("Not chosen yet") for m in cells)
    assert v["user"]["prefs"]["week_fill"] == "chosen"
    # a pick on Today shows up as today's meal; the rest stays open
    t = _typed(c, uid, "lunch", "Veg Thali", 180)
    c.post(f"/api/user/{uid}/today/pick", json={"meal": "lunch", "ref": t["pools"]["lunch"][0]["ref"]})
    v = _plan(c, uid)
    assert _today_cell(v, "lunch")["item"] == "Veg Thali" and _today_cell(v, "breakfast").get("open")
    # re-planning never invents a dish
    v = c.post(f"/api/plan/{v['plan']['id']}/optimize", json={}).get_json()
    assert not [m for d in v["grid"] for m in d["meals"].values() if m["kind"] == "delivery" and not m.get("today_pick")]


@pytest.mark.chosen_week
def test_chosen_week_plans_pool_meals_and_decide_fills_the_rest(town):
    c, uid = town["c"], town["uid"]
    r = c.post(f"/api/user/{uid}/today/pool", json={"source": "swiggy", "meal": "breakfast", "restaurant_id": "r-101",
                                                     "restaurant_name": "Annapoorna Tiffin Room", "item_id": "r-101-i5",
                                                     "rating": 4.4, "eta_min": 25})
    assert r.status_code == 200
    v = _plan(c, uid)
    pid = v["plan"]["id"]
    v = c.post(f"/api/plan/{pid}/optimize", json={}).get_json()
    later = [d for d in v["grid"] if d["date"] > "2026-11-02"]
    assert all(d["meals"]["breakfast"]["kind"] == "delivery" for d in later)          # from the pool
    assert all(d["meals"]["lunch"].get("open") for d in later)                        # nothing chosen
    out = c.post(f"/api/plan/{pid}/decide", json={"mode": "surprise_me", "apply": True}).get_json()
    filled = [m for d in out["view"]["grid"] if d["date"] > "2026-11-02" for m in [d["meals"]["lunch"]]
              if m["kind"] == "delivery"]
    assert filled and all(m["pinned"] for m in filled)                                # only on Decide


# --------------------------------------------------------------------------- #28 Decide explains
def test_decide_with_an_allergen_only_pool_says_why_and_what_to_do(gt):
    c = create_app().test_client()
    uid = _profile(c, allergens=["peanut"])
    _typed(c, uid, "lunch", "Peanut Chikki", 40)
    _typed(c, uid, "breakfast", "Idli", 60)
    t = c.post(f"/api/user/{uid}/today/decide", json={"mode": "favourites"}).get_json()
    d = t["decided"]
    assert d["missing"] == ["lunch"] and t["picks"]["breakfast"]["name"] == "Idli"
    assert d["reasons"]["lunch"].startswith("No safe lunch in your favourites (Peanut Chikki:")
    assert d["note"].endswith("Add one.") and {"act": "add", "meal": "lunch", "label": "Add a lunch"} in d["actions"]


# --------------------------------------------------------------------------- #10 defaults
def test_rule_defaults_are_ziggys_then_learned_from_own_places_then_yours(plain):
    c, uid = plain["c"], plain["uid"]
    r = _today(c, uid)["rules"]
    assert (r["min_rating"], r["max_eta"], r["source"]["min_rating"]) == (0.0, 45, "ziggy")
    today.init_schema()
    with db.cursor() as cur:
        for i, (rating, eta) in enumerate(((4.1, 40), (3.9, 35), (4.3, 45))):
            cur.execute("INSERT INTO pool_meta(user_id, meal, ref, qty, rating, eta_min) VALUES (?,?,?,?,?,?)",
                        (uid, "lunch", f"sw:r{i}:i{i}", 1, rating, eta))
    r = _today(c, uid)["rules"]
    assert r["min_rating"] == 3.5 and r["max_eta"] == 0                      # median 4.1★ / 40 min, with headroom
    assert r["source"] == {"min_rating": "learned", "max_eta": "learned"} and "4.1★" in r["defaults_basis"]
    r = c.post(f"/api/user/{uid}/today/rules", json={"min_rating": 4}).get_json()["rules"]
    assert r["source"] == {"min_rating": "you", "max_eta": "learned"} and r["min_rating"] == 4


# --------------------------------------------------------------------------- #11 diets
@pytest.mark.parametrize("name,hit", [
    ("Aloo Paratha", "potato"), ("Onion Uttapam", "onion"), ("Garlic Naan", "garlic"),
    ("Vengaya Sambar", "onion"), ("Poondu Kuzhambu", "garlic"), ("Lehsuni Dal Tadka", "garlic"),
    ("Pyaz Kachori", "onion"), ("Urulai Roast", "potato"), ("Gajar Halwa", "root vegetable"),
    ("Jain Pav Bhaji", None), ("No Onion Paneer Pulao", None), ("Eggplant Curry", None), ("Paneer Tikka", None),
])
def test_jain_lexicon_reads_english_hindi_and_tamil_names(name, hit):
    got = allergens.jain_hit(name)
    assert (got is None) if hit is None else (hit in got)


def test_eggetarian_and_jain_diets_end_to_end(gt):
    c = create_app().test_client()
    jain = _profile(c, diet="jain")
    t = _typed(c, jain, "lunch", "Aloo Paratha", 120)
    e = t["pools"]["lunch"][0]
    assert e["hard"] and e["reasons"][0] == "Aloo means potato (a root vegetable, not Jain) (Jain / no onion-garlic)"
    t = _typed(c, jain, "lunch", "Paneer Pulao", 160)
    assert not [x for x in t["pools"]["lunch"] if x["name"] == "Paneer Pulao"][0]["hard"]
    assert allergens.order_instructions({"diet": "jain", "medical": []})[0].startswith("Jain:")
    c2 = create_app().test_client()
    egg = _profile(c2, diet="egg")
    t = _typed(c2, egg, "lunch", "Egg Curry", 140, veg=False)
    t = _typed(c2, egg, "lunch", "Chicken 65", 200, veg=False)
    by = {x["name"]: x for x in t["pools"]["lunch"]}
    assert not by["Egg Curry"]["hard"] and by["Chicken 65"]["hard"]
    assert "eggetarian" in by["Chicken 65"]["reasons"][0]
    assert c2.post("/api/profiles", json={"diet": "keto", "weekly_budget": 2000,
                                          "consent": {"account": True}}).status_code == 400
