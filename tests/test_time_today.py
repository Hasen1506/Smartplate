"""W2 — time & Today correctness (usability test, 14 Oct 2026). Each test failed on main:

1. "TOMORROW · LUNCH · Order by 12:15 · in 18 min" counted down to *today's* 12:15.
2. Sunday was a dead day: the plan made on Sunday covered next Mon–Sun, so Sunday lunch
   and dinner had no pick.
3. Tomorrow's breakfast could be put in the Swiggy cart (and paid, delivering now) today.
4. "Your week is planned" was toasted over 20 skipped meals out of 21.
5. A meal the user said they *order* came out "Cook: Masala oats".
6. The Change sheet's "₹245 to spend on this meal" disagreed with Today's "₹120 left".
7. Medu Vada / idli / dosa were planned as dinner.

Deterministic: the clock is frozen (gt_support.FrozenClock), no network, Hypothesis runs
derandomized (conftest "ci" profile) with small example budgets.
"""
import datetime as dt

import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st

from gt_support import MONDAY_8AM, check_plan
from smartplate import everyday, service
from smartplate.domain import models, timing
from smartplate.kernel import optimizer, scheduler

# the cart fixtures (recorded Swiggy contract fake, connected user 3, frozen clock)
from test_real_bill_corrects_plan import _live_week, client, swiggy  # noqa: F401

SUNDAY = dt.date(2026, 11, 8)
BASE = {"name": "Time", "diet": "veg", "weekly_budget": 2500, "cook": "never",
        "meals": ["breakfast", "lunch", "dinner"]}

# every hour of two whole weeks (Mon 2 Nov – Sun 15 Nov 2026), any minute
INSTANT = st.builds(lambda d, h, m: dt.datetime(2026, 11, 2) + dt.timedelta(days=d, hours=h, minutes=m),
                    st.integers(0, 13), st.integers(0, 23), st.integers(0, 59))


def _cells(view):
    return [(day, meal, c) for day in view["grid"] for meal, c in day["meals"].items()]


def _day(view, date: dt.date):
    return next((d for d in view["grid"] if d.get("date") == date.isoformat()), None)


# --------------------------------------------------------------------------- #
# 1. Countdown targets the meal's own date
# --------------------------------------------------------------------------- #
ORDER_1215 = {"order_at": "12:15", "order_at_min": 735}


def test_tomorrows_order_by_never_counts_down_to_today():
    at = dt.datetime(2026, 11, 8, 11, 57)                     # Sunday 11:57, 18 min before 12:15
    tomorrow = timing.order_status(SUNDAY + dt.timedelta(days=1), ORDER_1215, at, meal_min=780)
    assert tomorrow["minutes_left"] == 24 * 60 + 18          # not "in 18 min"
    assert tomorrow["label"] == "Tomorrow 12:15"
    assert tomorrow["order_by_at"].startswith("2026-11-09T12:15")
    today = timing.order_status(SUNDAY, ORDER_1215, at, meal_min=780)
    assert today["minutes_left"] == 18 and today["label"] == "12:15"


def test_a_past_order_by_has_no_countdown():
    at = dt.datetime(2026, 11, 8, 12, 16)
    st_ = timing.order_status(SUNDAY, ORDER_1215, at, meal_min=780)
    assert st_["minutes_left"] is None and st_["late"] is True and st_["cart_open"] is True
    yesterday = timing.order_status(SUNDAY - dt.timedelta(days=1), ORDER_1215, at, meal_min=780)
    assert yesterday["minutes_left"] is None and yesterday["cart_state"] == "closed"


def test_order_by_before_midnight_belongs_to_the_previous_day():
    # an early breakfast whose order-by falls before midnight (negative minute of the day)
    st_ = timing.order_status(SUNDAY, {"order_at": "23:50", "order_at_min": -10},
                              dt.datetime(2026, 11, 7, 23, 0), meal_min=30)
    assert st_["order_by_at"].startswith("2026-11-07T23:50") and st_["minutes_left"] == 50
    assert st_["label"] == "23:50"


@settings(max_examples=150)
@given(at=INSTANT, day_offset=st.integers(-2, 8), order_min=st.integers(-60, 23 * 60 + 59),
       window=st.integers(0, 240))
@example(at=dt.datetime(2026, 11, 8, 11, 57), day_offset=1, order_min=735, window=90)   # the reported bug
@example(at=dt.datetime(2026, 11, 8, 23, 59), day_offset=1, order_min=0, window=90)     # midnight
@example(at=dt.datetime(2026, 11, 9, 0, 0), day_offset=0, order_min=0, window=90)
def test_property_countdown_is_to_the_meals_own_instant(at, day_offset, order_min, window):
    meal_date = at.date() + dt.timedelta(days=day_offset)
    meal_min = order_min + 45
    s = timing.order_status(meal_date, {"order_at_min": order_min}, at, meal_min=meal_min, window_min=window)
    by = dt.datetime.combine(meal_date, dt.time()) + dt.timedelta(minutes=order_min)
    # never a countdown to a past order-by, and the countdown is exact otherwise
    if by < at:
        assert s["minutes_left"] is None
    else:
        assert s["minutes_left"] == int((by - at).total_seconds() // 60)
    # a label without a day is only ever today's
    assert (" " not in s["label"]) == (by.date() == at.date())
    assert s["label"].endswith(by.strftime("%H:%M"))
    # the cart opens `window` minutes before order-by and closes 90 min after the meal
    opens = by - dt.timedelta(minutes=window)
    closes = max(by, dt.datetime.combine(meal_date, dt.time()) + dt.timedelta(minutes=meal_min)) \
        + dt.timedelta(minutes=timing.LATE_GRACE_MIN)
    assert s["cart_open"] == (opens <= at <= closes)
    assert (s["cart_opens_at"] is not None) == (at < opens)


def test_plan_cells_carry_the_meals_own_order_by(gt):
    view = everyday.create_profile(dict(BASE))
    at = MONDAY_8AM
    for day, meal, c in _cells(view):
        if c["kind"] != "delivery" or c["status"] != "active":
            continue
        o = c["order"]
        by = dt.datetime.fromisoformat(o["order_by_at"]).replace(tzinfo=None)
        assert by.date() in (dt.date.fromisoformat(day["date"]), dt.date.fromisoformat(day["date"]) - dt.timedelta(days=1))
        assert by.strftime("%H:%M") == o["order_at"]
        if day["date"] != at.date().isoformat():
            assert o["minutes_left"] is None or o["minutes_left"] > (by - at).total_seconds() // 60 - 1
            assert " " in o["label"]                       # "Tue 12:15", never a bare "12:15"


# --------------------------------------------------------------------------- #
# 2. Sunday is never a dead day
# --------------------------------------------------------------------------- #
def test_sunday_noon_plans_sundays_lunch_and_dinner(gt):
    gt.set(dt.datetime(2026, 11, 8, 11, 52))
    view = everyday.create_profile(dict(BASE))
    assert view["plan"]["week_start"] == "2026-11-02"       # this week, not next
    sun = _day(view, SUNDAY)
    assert sun and {"lunch", "dinner"} <= set(sun["meals"])
    assert all(sun["meals"][m]["status"] != "past" for m in ("lunch", "dinner"))
    assert view["next_up"] and view["next_up"]["date"] == SUNDAY.isoformat()
    assert view["next_up"]["meal"] == "lunch" and view["next_up"]["when"] == "Today"
    assert not check_plan(view["plan"]["id"])


def test_sunday_night_after_dinner_plans_next_week(gt):
    gt.set(dt.datetime(2026, 11, 8, 22, 30))                 # Sunday dinner (20:30) + 90 min grace passed
    view = everyday.create_profile(dict(BASE))
    assert view["plan"]["week_start"] == "2026-11-09"


def test_a_week_planned_ahead_still_points_today_at_todays_plan(gt):
    gt.set(dt.datetime(2026, 11, 7, 11, 0))                  # Saturday
    view = everyday.create_profile(dict(BASE))
    uid, this_week = view["user"]["id"], view["plan"]["id"]
    assert "today_plan_id" not in service.current_plan(uid)
    ahead = service.create_plan(uid)                          # "plan next week" on Saturday
    assert models.get_plan(ahead)["week_start"] == "2026-11-09"
    cur = service.current_plan(uid)
    assert cur["plan"]["id"] == ahead                         # the week just planned stays the one shown
    assert cur["today_plan_id"] == this_week                  # …and Today can still find today's meals
    gt.set(dt.datetime(2026, 11, 8, 23, 0))                   # Sunday night: nothing left today
    assert "today_plan_id" not in service.current_plan(uid)


def test_late_sunday_rolls_a_finished_current_week_forward(gt):
    gt.set(dt.datetime(2026, 11, 8, 10, 0))
    view = everyday.create_profile(dict(BASE))
    uid = view["user"]["id"]
    assert view["plan"]["week_start"] == "2026-11-02"
    gt.set(dt.datetime(2026, 11, 8, 22, 30))
    rolled = service.current_plan(uid)
    assert rolled["plan"]["week_start"] == "2026-11-09"
    assert service.current_plan(uid)["plan"]["id"] == rolled["plan"]["id"]   # once, not every call


@settings(max_examples=12, deadline=None)
@given(at=INSTANT, meals=st.lists(st.sampled_from(["breakfast", "lunch", "dinner"]), unique=True, min_size=1))
@example(at=dt.datetime(2026, 11, 8, 11, 52), meals=["breakfast", "lunch", "dinner"])   # reported Sunday
@example(at=dt.datetime(2026, 11, 8, 21, 59), meals=["dinner"])                          # last minute of Sunday dinner
@example(at=dt.datetime(2026, 11, 15, 0, 0), meals=["breakfast", "lunch", "dinner"])     # Sunday midnight
@example(at=dt.datetime(2026, 11, 9, 0, 1), meals=["lunch"])                              # Monday just after midnight
def test_property_today_always_has_its_remaining_meals(gt, at, meals):
    """At any instant, every meal of today that isn't past yet exists in the plan Today shows."""
    from smartplate import ratelimit
    ratelimit.reset()
    gt.set(at)
    uid = everyday.create_profile({**BASE, "meals": meals})["user"]["id"]
    view = service.current_plan(uid)
    today = _day(view, at.date())
    for m in meals:
        slot = dt.datetime.fromisoformat(scheduler.trigger_ts(at.date().isoformat(), 0, m))
        if slot + scheduler.PAST_GRACE >= at:
            assert today is not None, f"{at:%a %H:%M}: today isn't in the plan ({view['plan']['week_start']})"
            assert m in today["meals"] and today["meals"][m]["status"] != "past", (at, m)
    if today is None:                                        # nothing left today: the plan starts tomorrow
        assert view["plan"]["week_start"] == (at.date() + dt.timedelta(days=1)).isoformat()


# --------------------------------------------------------------------------- #
# 3. Cart only inside the meal's order window
# --------------------------------------------------------------------------- #
def _first(view, pred):
    return next(c for _, _, c in _cells(view) if c["kind"] == "delivery" and c["status"] == "active" and pred(c))


@pytest.mark.cart_window
def test_tomorrows_breakfast_cannot_be_carted_today(client, swiggy, gt):  # noqa: F811
    pid, view = _live_week(client, swiggy)
    tomorrow = (MONDAY_8AM.date() + dt.timedelta(days=1)).isoformat()
    cell = _first(view, lambda c: c["order"]["order_by_at"].startswith(tomorrow))
    assert cell["order"]["cart_open"] is False and cell["order"]["cart_state"] == "early"
    preview = client.get(f"/api/session/{cell['session_id']}/swiggy-cart/preview").get_json()
    r = client.post(f"/api/session/{cell['session_id']}/swiggy-cart", json={"expected_fingerprint": preview["fingerprint"]})
    assert r.status_code == 409
    body = r.get_json()
    assert body["code"] == "cart_window" and body["opens_at"] == cell["order"]["cart_opens_at"]
    assert "Tomorrow" in body["error"] and "deliver it now" in body["error"]
    assert swiggy.cart is None                                # nothing reached Swiggy
    # the order-list path refuses the same meal
    client.post(f"/api/plan/{pid}/order-queue", json={"meals": ["breakfast", "lunch", "dinner"], "days": list(range(7))})
    r = client.post(f"/api/session/{cell['session_id']}/order/cart", json={"expected_fingerprint": preview["fingerprint"]})
    assert r.status_code == 409 and r.get_json()["code"] == "cart_window"
    # inside the window it goes through
    gt.set(dt.datetime.fromisoformat(cell["order"]["cart_opens_at"]).replace(tzinfo=None) + dt.timedelta(minutes=1))
    preview = client.get(f"/api/session/{cell['session_id']}/swiggy-cart/preview").get_json()
    r = client.post(f"/api/session/{cell['session_id']}/swiggy-cart", json={"expected_fingerprint": preview["fingerprint"]})
    assert r.status_code == 200, r.get_json()


@pytest.mark.cart_window
def test_cart_window_is_configurable(client, swiggy, gt, monkeypatch):  # noqa: F811
    monkeypatch.setattr(timing, "CART_WINDOW_MIN", 48 * 60)
    pid, view = _live_week(client, swiggy)
    tomorrow = (MONDAY_8AM.date() + dt.timedelta(days=1)).isoformat()
    cell = _first(view, lambda c: c["order"]["order_by_at"].startswith(tomorrow))
    assert cell["order"]["cart_open"] is True


@pytest.mark.cart_window
def test_a_long_past_meal_cannot_be_carted(client, swiggy, gt):  # noqa: F811
    pid, view = _live_week(client, swiggy)
    cell = _first(view, lambda c: True)
    sid = cell["session_id"]
    preview = client.get(f"/api/session/{sid}/swiggy-cart/preview").get_json()
    gt.set(dt.datetime.fromisoformat(cell["order"]["order_by_at"]).replace(tzinfo=None) + dt.timedelta(hours=6))
    with pytest.raises(timing.CartWindowClosed):
        service.require_cart_window(sid)
    r = client.post(f"/api/session/{sid}/swiggy-cart", json={"expected_fingerprint": preview["fingerprint"]})
    assert r.status_code in (400, 409)                        # past meal: refused (cart window or "time has passed")


# --------------------------------------------------------------------------- #
# 4. An honest "plan my week" summary
# --------------------------------------------------------------------------- #
def _d(kind, reason="", status="active", past=False):
    return {"chosen_kind": kind, "session_status": status, "past": past, "reasons": [reason] if reason else []}


def test_summary_says_twenty_of_twenty_one_skipped():
    ds = [_d("cook")] + [_d("skip", "No safe option within your rating floor and budget — session skipped.")] * 20
    s = service.plan_summary(ds)
    assert (s["planned"], s["skipped"], s["total"]) == (1, 20, 21)
    assert s["message"] == "1 of 21 meals planned; 20 skipped: no safe option within your rating floor and budget."
    assert "Your week is planned" not in s["message"] and s["level"] == "warn"


def test_summary_all_planned_and_past_meals_left_out():
    ds = [_d("delivery")] * 5 + [_d("past", past=True)] * 3
    s = service.plan_summary(ds)
    assert s["message"] == "Your week is planned: all 5 meals." and s["level"] == "good"


@settings(max_examples=60)
@given(st.lists(st.tuples(st.sampled_from(["delivery", "cook", "skip", "skipped", "past"]),
                          st.sampled_from(["", "No real restaurant dishes yet — connect Swiggy and pick an address.",
                                           "Your lunch pool didn't fit the budget this time — skipped."])), max_size=21))
def test_property_summary_never_celebrates_skips(rows):
    ds = [_d(k, r, status="skipped" if k == "skipped" else "active", past=k == "past") for k, r in rows]
    s = service.plan_summary(ds)
    assert s["planned"] + s["skipped"] == s["total"] == sum(1 for k, _ in rows if k != "past")
    assert s["message"].startswith("Your week is planned") == (s["skipped"] == 0 and s["total"] > 0)
    assert sum(r["count"] for r in s["reasons"]) == s["skipped"]
    assert "session skipped" not in s["message"]


def test_plan_view_and_create_return_the_summary(gt):
    view = everyday.create_profile({**BASE, "weekly_budget": 300})
    s = view["plan_summary"]
    assert s["total"] == s["planned"] + s["skipped"] and s["total"] > 0
    if s["skipped"]:
        assert "Your week is planned" not in s["message"] and f"{s['skipped']} skipped" in s["message"]


# --------------------------------------------------------------------------- #
# 5. "Order" means order
# --------------------------------------------------------------------------- #
def test_meals_the_user_orders_are_never_turned_into_cook_days(gt):
    view = everyday.create_profile({**BASE, "cook": "often",
                                    "rhythm": {"breakfast": "order", "lunch": "order", "dinner": "order"}})
    kinds = {d["chosen_kind"] for d in models.decisions_for_plan(view["plan"]["id"])}
    assert "cook" not in kinds


def test_flexible_cook_days_only_go_to_meals_not_marked_order(gt):
    view = everyday.create_profile({**BASE, "cook": "often",
                                    "rhythm": {"breakfast": "order", "lunch": "cook", "dinner": "order"}})
    by_meal = {}
    for d in models.decisions_for_plan(view["plan"]["id"]):
        by_meal.setdefault(d["meal"], set()).add(d["chosen_kind"])
    assert "cook" not in by_meal["breakfast"] | by_meal["dinner"]


# --------------------------------------------------------------------------- #
# 6. The sheet's money for a meal agrees with Today's money left
# --------------------------------------------------------------------------- #
@settings(max_examples=4, deadline=None)
@given(budget=st.sampled_from([400, 1200, 2500, 6000]), at=st.sampled_from(
    [MONDAY_8AM, dt.datetime(2026, 11, 4, 13, 0), dt.datetime(2026, 11, 8, 11, 52)]))
def test_property_meal_room_never_exceeds_week_money_left(gt, budget, at):
    from smartplate import ratelimit
    ratelimit.reset()
    gt.set(at)
    view = everyday.create_profile({**BASE, "weekly_budget": budget})
    today_left = round(view["budget"]["budget"] - view["budget"]["spend"], 2)
    open_cells = [c for _, _, c in _cells(view) if c["status"] == "active" and c.get("kind") != "past"]
    for c in open_cells[::4]:                                 # a spread of meals (every fourth) keeps it fast
        L = everyday.options(c["session_id"])["limits"]
        assert L["week_left"] == pytest.approx(today_left, abs=0.011)          # same number as Today
        own = c["cost"] if c["kind"] in ("delivery", "cook") else 0
        assert L["this_meal"] == pytest.approx(own, abs=0.011)
        assert L["left_week"] == pytest.approx(L["week_left"] + L["this_meal"], abs=0.011)
        assert L["left_week"] <= view["budget"]["budget"] + 0.011


# --------------------------------------------------------------------------- #
# 7. Meal-slot suitability
# --------------------------------------------------------------------------- #
# Founder decision 14 Oct 2026: tiffin dishes (dosa, idli, pongal…) are a normal dinner.
# Only the snack-alone rule stays: a vada or a chai by itself is not a lunch or dinner.
@pytest.mark.parametrize("name", ["Masala Dosa", "Ghee Roast Dosai", "Idli Sambar (4 pc)", "Idlis",
                                  "Ven Pongal", "Ghee Pongal", "Rava Upma", "Mini Tiffin",
                                  "Onion Uthappam", "Pesarattu + Chutney"])
def test_tiffin_dishes_are_allowed_at_dinner(name):
    for slot in ("breakfast", "lunch", "dinner"):
        assert timing.slot_suitability({"name": name}, slot) is True
    assert optimizer.meal_suitable({"name": name, "tags": ["breakfast"]}, "dinner")
    assert timing.is_breakfast_only(name)                  # still recognised as tiffin


@pytest.mark.parametrize("name", ["Medu Vada", "Medu Vadai", "Filter Coffee + Vada", "Masala Chai", "Onion Bonda"])
def test_a_snack_alone_is_still_not_a_dinner(name):
    assert not optimizer.meal_suitable({"name": name, "tags": []}, "dinner")
    assert not optimizer.meal_suitable({"name": name, "tags": []}, "lunch")
    assert optimizer.meal_suitable({"name": name, "tags": []}, "breakfast")
    assert optimizer.meal_suitable({"name": name, "tags": []}, "dinner", chosen=True)   # their own pick


@pytest.mark.parametrize("name", ["Vada Pav", "Dahi Vada", "Chilli Idli", "Sweet Pongal", "Paneer Biryani",
                                  "Chicken Kothu Parotta", "Veg Meals", "Dosa Batter 1kg"])
def test_dinner_dishes_and_lookalikes_stay_dinner(name):
    assert timing.slot_suitability(name, "dinner") is True


def test_unknown_slot_is_not_suitable():
    assert timing.slot_suitability("Veg Meals", "brunch") is False


@settings(max_examples=80)
@given(word=st.sampled_from(["idli", "dosa", "pongal", "upma", "uthappam"]),
       pre=st.sampled_from(["", "Ghee ", "Masala ", "Mini ", "Rava ", "Podi "]),
       post=st.sampled_from(["", " Combo", " (2 pc)", " + Chutney", "s"]),
       slot=st.sampled_from(["breakfast", "lunch", "dinner"]), fav=st.booleans())
def test_property_slot_suitability(word, pre, post, slot, fav):
    name = (pre + word + post).title()
    assert timing.slot_suitability({"name": name}, slot, favourited_for_slot=fav) is True


def test_planner_never_puts_a_snack_alone_at_dinner(gt):
    for body in ({**BASE}, {**BASE, "diet": "nonveg"}, {**BASE, "weekly_budget": 900}):
        view = everyday.create_profile(dict(body))
        dinners = [d["item_name"] for d in models.decisions_for_plan(view["plan"]["id"])
                   if d["meal"] == "dinner" and d["chosen_kind"] == "delivery"]
        assert not [n for n in dinners if optimizer.is_snack(n)], dinners
        assert not check_plan(view["plan"]["id"])


def test_change_sheet_does_not_offer_a_snack_alone_for_dinner(gt):
    view = everyday.create_profile(dict(BASE))
    dinner = next(c for _, m, c in _cells(view) if m == "dinner" and c["status"] == "active")
    sheet = everyday.options(dinner["session_id"])
    offered = [d["name"] for g in sheet["usual"] for d in g["dishes"]] + [d["name"] for d in sheet["new"]]
    assert offered and not [n for n in offered if optimizer.is_snack(n)]
