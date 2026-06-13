"""Coverage that each remaining gap feature (5.1–5.3) produces its real effect."""
import datetime as dt

from smartplate import service
from smartplate.domain import (carbon, festivals, health, leftovers, models,
                               nutrition, reverse_mode, sentiment, weather)
from smartplate.integrations import calendar_sync
from smartplate.kernel import optimizer, scheduler


# §5.1.2 calendar
def test_calendar_travel_suspends_day(seeded):
    optimizer.optimize(seeded["plan_id"])
    grid = service.plan_view(seeded["plan_id"])["grid"]
    thursday = grid[3]["meals"]                       # Bangalore offsite seeded on Thu
    assert all(m["kind"] == "skip" for m in thursday.values())


def test_calendar_conflict_detected(seeded):
    evs = calendar_sync.events_for(1)
    assert calendar_sync.conflicts_with_peak(evs, 1, "lunch")     # Tue client lunch
    assert calendar_sync.day_is_travel(evs, 3)


# §5.1.5 pause / snooze
def test_snooze_removes_from_planning(seeded):
    pid = seeded["plan_id"]
    sessions = models.sessions_for_plan(pid)
    s = sessions[0]
    scheduler.set_status(s["id"], "snoozed", "sick")
    optimizer.optimize(pid)
    grid = service.plan_view(pid)["grid"]
    cell = grid[s["day"]]["meals"][s["meal"]]
    assert cell["kind"] == "snoozed"


# §5.2.6 nutrition
def test_nutrition_penalty_rewards_on_target(seeded):
    user = models.get_user(1)
    on = {"kcal": nutrition.meal_target(user, "lunch")["kcal"], "protein_g": 30}
    off = {"kcal": 1500, "protein_g": 2}
    assert nutrition.penalty(user, "lunch", on) < nutrition.penalty(user, "lunch", off)


# §5.2.8 leftovers
def test_leftover_forces_zero_cost_cook(seeded):
    optimizer.optimize(seeded["plan_id"])
    grid = service.plan_view(seeded["plan_id"])["grid"]
    wed_dinner = grid[2]["meals"]["dinner"]           # seeded leftover dal
    assert wed_dinner["kind"] == "cook"
    assert wed_dinner["cost"] == 0


# §5.2.9 festivals
def test_festival_fast_suspends_daytime(seeded):
    fests = festivals.for_week(seeded["week_start"])
    fast = next(f for f in fests.values() if f["effect"] == "fast")
    assert festivals.suspends_session(fast, "lunch")
    assert not festivals.suspends_session(fast, "dinner")


# §5.2.10 weather
def test_weather_biases_comfort_on_rain(seeded):
    comfort = {"tags": ["comfort"]}
    light = {"tags": ["light"]}
    assert weather.taste_bias("rain", comfort) < 0      # rewarded
    assert weather.taste_bias("hot", light) < 0


# §5.2.11 surge
def test_surge_higher_on_rain_and_shift_saves(seeded):
    clear = surge_mult("clear")
    rain = surge_mult("rain")
    assert rain > clear
    shift = __import__("smartplate.domain.surge", fromlist=["x"]).time_shift_option(
        "Chennai", 1, "dinner", "rain", 250)
    assert shift and shift["saving"] > 0


def surge_mult(cond):
    from smartplate.domain import surge
    return surge.predict("Chennai", 1, "dinner", cond)


# §5.3.12 reverse mode
def test_cook_candidate_respects_diet(seeded):
    vegan = models.get_user(2)
    r = reverse_mode.cook_candidate(vegan, "lunch")
    assert r and r["veg"] == 1


# §5.3.14 receipts
def test_receipts_autotag_business_lunch(seeded):
    from smartplate.domain.receipts import autocategory
    assert autocategory(1, "lunch") == "business"      # weekday lunch
    assert autocategory(5, "dinner") == "personal"     # weekend dinner


# §5.3.15 health
def test_fasting_window_blocks_in_window(seeded):
    user = models.get_user(1)   # fasting 21:30->08:00 (overnight)
    assert health.in_fasting_window(user, 1380)         # 23:00 inside
    assert not health.in_fasting_window(user, 780)      # 13:00 outside


# §5.3.16 carbon
def test_carbon_band_ordering(seeded):
    assert carbon.band(0.5) == "low"
    assert carbon.band(3.0) == "high"
    assert carbon.estimate({"carbon_kg": 4.2}) == 4.2


# §3.1 sentiment (local, no API)
def test_sentiment_local_scoring(seeded):
    pos = sentiment.aggregate(["amazing and fresh", "loved it, best ever"])
    neg = sentiment.aggregate(["cold and stale", "worst, soggy and late"])
    assert pos["score"] > 0 > neg["score"]


# §5.3.13 community
def test_community_adopt_increments(seeded):
    before = service.list_community()[0]
    after = service.adopt_template(before["id"])
    assert after["adopts"] == before["adopts"] + 1
