"""The everyday flows: set-up, usual places, shortlist, pick, swap, confirm, rate,
timing, weather, holidays and heads-ups. The clock is pinned so results never
depend on the day the suite runs."""
import datetime as dt
import json

import pytest

from smartplate import config, db, everyday
from smartplate.app import create_app
from smartplate.domain import festivals, models, profile, taste, timing, weather
from smartplate.kernel import optimizer

FRIDAY_NOON = dt.datetime(2026, 9, 25, 11, 0)          # a Friday


@pytest.fixture
def client(seeded):
    app = create_app()
    app.config["TESTING"] = True
    return app.test_client()


@pytest.fixture
def friday(monkeypatch):
    monkeypatch.setattr(optimizer, "now", lambda: FRIDAY_NOON)
    return FRIDAY_NOON


ASHA = {"name": "Asha", "diet": "veg", "allergens": ["peanut"], "weekly_budget": 2100,
        "meals": ["lunch", "dinner"], "goal": "protein", "favourites": [6, 8, 9], "cook": "sometimes",
        "body": {"weight_kg": 62, "height_cm": 165, "age": 29, "sex": "female", "activity": "light"}}


def _cells(view):
    return [(i, meal, cell) for i, day in enumerate(view["grid"]) for meal, cell in day["meals"].items()]


# --------------------------------------------------------------------------- #
# Profile maths + validation
# --------------------------------------------------------------------------- #
def test_mifflin_st_jeor_targets_and_safety_floor():
    t, working = profile.targets_from_goal("maintain", {"weight_kg": 70, "height_cm": 175, "age": 30,
                                                        "sex": "male", "activity": "moderate"})
    # BMR = 700 + 1093.75 - 150 + 5 = 1648.75; × 1.55 = 2555.6 → 2560
    assert t["kcal"] == 2560 and t["protein_g"] == 70
    assert "1649" in working[1] or "1648" in working[1]
    low, _ = profile.targets_from_goal("lose", {"weight_kg": 40, "height_cm": 145, "age": 60,
                                                "sex": "female", "activity": "sedentary"})
    assert low["kcal"] == profile.MIN_KCAL["female"]           # never plans below the floor


def test_no_body_stats_uses_honest_defaults():
    t, working = profile.targets_from_goal("none", None)
    assert t == {"kcal": 2000, "protein_g": 60}
    assert any("typical adult" in w for w in working)


@pytest.mark.parametrize("bad", [
    {"weekly_budget": -5}, {"diet": "keto"}, {"allergens": ["gluten", "nuts"]}, {"meals": []},
    {"body": {"weight_kg": 60, "height_cm": 160, "age": 15}}, {"favourites": [True]},
    {"surprise": 1}, {"daily_cap": 10, "weekly_budget": 3000},
])
def test_setup_validation_rejects_whole_payload(bad):
    with pytest.raises(ValueError):
        profile.validate_setup(bad)


def test_variety_pref_no_longer_breaks_meal_targets(seeded):
    user = models.get_user(1)
    user["nutrition_targets"] = {"variety": "mixed", "kcal": 1800}
    from smartplate.domain import nutrition
    assert nutrition.meal_target(user, "lunch")["kcal"] == pytest.approx(720)


# --------------------------------------------------------------------------- #
# Onboarding → a real plan
# --------------------------------------------------------------------------- #
def test_onboarding_creates_prorated_plan_for_rest_of_week(client, friday):
    r = client.post("/api/profiles", json=dict(ASHA))
    assert r.status_code == 201
    v = r.get_json()
    assert v["plan"]["week_start"] == "2026-09-21"            # this week's Monday
    assert v["budget"]["prorated"] and v["budget"]["budget"] == pytest.approx(2100 * 3 / 7)
    assert v["budget"]["spend"] <= v["budget"]["budget"] + 0.01
    meals = {meal for _, meal, _ in _cells(v)}
    assert meals == {"lunch", "dinner"}                       # only the meals asked for
    assert all(c["status"] == "past" for i, _, c in _cells(v) if i < 4)   # Mon–Thu already gone
    assert v["nutrition"]["daily_target"]["protein_g"] == 99
    assert v["user"]["favourites"] == [6, 8, 9]
    assert v["next_up"]["when"] == "Today"
    user = models.get_user(v["user"]["id"])
    assert user["health_targets"]["max_cook_per_week"] == 2 and user["prefs"]["setup_done"]


def test_onboarding_rejects_bad_payload_without_creating_user(client):
    before = len(models.list_users())
    assert client.post("/api/profiles", json={**ASHA, "diet": "keto"}).status_code == 400
    assert client.post("/api/profiles", json={"name": "No budget"}).status_code == 400
    assert len(models.list_users()) == before


def test_suggest_budget_from_usual_places(client):
    rec = client.post("/api/suggest-budget", json={"diet": "veg", "meals": ["lunch", "dinner"],
                                                   "favourites": [6, 8]}).get_json()
    assert rec["feasible"] and rec["tight"] <= rec["suggested"] <= rec["roomy"]
    assert rec["suggested"] % 50 == 0


def test_restaurant_picker_counts_dishes_that_fit(client):
    rows = client.get("/api/restaurants?diet=vegan&allergens=dairy").get_json()
    kuppanna = next(r for r in rows if r["name"] == "Junior Kuppanna")
    assert kuppanna["dishes_fit"] == 0 and kuppanna["dishes_total"] == 3
    assert rows[-1]["dishes_fit"] == 0 or all(r["dishes_fit"] > 0 for r in rows)


# --------------------------------------------------------------------------- #
# Usual places, discovery cap, daily cap, same-dish rule
# --------------------------------------------------------------------------- #
def test_plan_is_mostly_usual_places_with_capped_discovery(seeded):
    res = optimizer.optimize(seeded["plan_id"])
    favs = taste.favourites(1)
    delivery = [d for d in res["decisions"] if d["chosen_kind"] == "delivery"]
    new = [d for d in delivery if d["restaurant_id"] not in favs]
    active = sum(1 for s in models.sessions_for_plan(seeded["plan_id"]) if s["status"] == "active")
    assert delivery and len(new) <= max(1, round(0.2 * active))


def test_same_dish_never_twice_in_a_day(seeded):
    res = optimizer.optimize(seeded["plan_id"])
    seen = set()
    for d in res["decisions"]:
        if d["chosen_kind"] == "delivery":
            assert (d["day"], d["item_id"]) not in seen
            seen.add((d["day"], d["item_id"]))


def test_daily_cap_holds_every_day(client):
    v = client.patch("/api/user/1/setup", json={"daily_cap": 380}).get_json()
    per_day = {}
    for i, _, c in _cells(v):
        if c["kind"] in ("delivery", "cook"):
            per_day[i] = per_day.get(i, 0) + c["cost"]
    assert per_day and max(per_day.values()) <= 380 + 0.01
    assert v["budget"]["daily_cap"] == 380


def test_changing_meals_rebuilds_open_sessions(client):
    v = client.patch("/api/user/1/setup", json={"meals": ["dinner"]}).get_json()
    assert {m for _, m, _ in _cells(v)} == {"dinner"}
    v = client.patch("/api/user/1/setup", json={"meals": ["breakfast", "dinner"]}).get_json()
    assert {m for _, m, _ in _cells(v)} == {"breakfast", "dinner"}


# --------------------------------------------------------------------------- #
# Shortlist → pick → swap → confirm → rate
# --------------------------------------------------------------------------- #
def _session(view, day, meal):
    return view["grid"][day]["meals"][meal]["session_id"]


def test_shortlist_is_capped_and_favourites_first(client):
    v = client.get("/api/plan/1").get_json()
    o = client.get(f"/api/session/{_session(v, 0, 'dinner')}/options").get_json()
    assert o["has_favourites"] and all(g["favourite"] for g in o["usual"])
    assert len(o["usual"]) <= everyday.SHORTLIST_PLACES
    assert all(len(g["dishes"]) <= everyday.SHORTLIST_DISHES for g in o["usual"])
    assert len(o["new"]) <= everyday.SHORTLIST_NEW
    names = [d["name"] for g in o["usual"] for d in g["dishes"]] + [d["name"] for d in o["new"]]
    assert "Peanut Chikki Sweet" not in names                 # allergen never shown
    assert o["hidden"]["not_safe"] >= 1


def test_pick_is_kept_and_week_rebalances(client):
    v = client.get("/api/plan/1").get_json()
    sid = _session(v, 0, "dinner")
    assert client.post(f"/api/session/{sid}/choose", json={"item_id": 3}).status_code == 400   # peanut
    v = client.post(f"/api/session/{sid}/choose", json={"item_id": 4}).get_json()             # Chettinad
    cell = v["grid"][0]["meals"]["dinner"]
    assert cell["item"] == "Chicken Chettinad + Rice" and cell["pinned"]
    assert v["budget"]["spend"] <= v["budget"]["budget"] + 0.01
    v = client.post("/api/plan/1/optimize", json={}).get_json()                              # pin survives replans
    assert v["grid"][0]["meals"]["dinner"]["item"] == "Chicken Chettinad + Rice"
    v = client.post(f"/api/session/{sid}/choose", json={"action": "auto"}).get_json()
    assert not v["grid"][0]["meals"]["dinner"]["pinned"]


def test_stale_pin_is_dropped_when_profile_makes_it_unsafe(client):
    v = client.get("/api/plan/1").get_json()
    sid = _session(v, 0, "dinner")
    client.post(f"/api/session/{sid}/choose", json={"item_id": 6})                            # Egg Curry Meals
    v = client.patch("/api/user/1/setup", json={"allergens": ["peanut", "egg"]}).get_json()
    assert v["grid"][0]["meals"]["dinner"]["item"] != "Egg Curry Meals"
    assert models.get_session(sid)["pinned"] is None


def test_drag_and_drop_swap_trades_meals(client):
    v = client.get("/api/plan/1").get_json()
    deliveries = [(i, m, c) for i, m, c in _cells(v) if c["kind"] == "delivery"]
    (da, ma, ca) = deliveries[0]
    (db_, mb, cb) = next(d for d in deliveries if d[2]["item"] != ca["item"])
    v = client.post("/api/plan/1/swap", json={"a": ca["session_id"], "b": cb["session_id"]}).get_json()
    assert v["grid"][da]["meals"][ma]["item"] == cb["item"]
    assert v["grid"][db_]["meals"][mb]["item"] == ca["item"]
    assert v["grid"][da]["meals"][ma]["pinned"] and v["grid"][db_]["meals"][mb]["pinned"]
    assert client.post("/api/plan/1/swap", json={"a": ca["session_id"], "b": ca["session_id"]}).status_code == 400
    leftover = _session(v, 2, "dinner")                                                      # seeded fridge dal
    assert client.post("/api/plan/1/swap", json={"a": ca["session_id"], "b": leftover}).status_code == 400
    assert client.post("/api/plan/1/swap", json={"a": "1", "b": 2}).status_code == 400


def test_confirm_counts_spend_logs_intake_and_undo_reverts(client):
    v = client.get("/api/plan/1").get_json()
    sid, cell = next((c["session_id"], c) for _, _, c in _cells(v) if c["kind"] == "delivery")
    v = client.post(f"/api/session/{sid}/confirm", json={}).get_json()
    assert next(c for _, _, c in _cells(v) if c["session_id"] == sid)["status"] == "confirmed"
    assert client.post(f"/api/session/{sid}/confirm", json={}).status_code == 400
    with db.cursor() as cur:
        assert cur.execute("SELECT COUNT(*) FROM intake_log WHERE note=?", (f"session:{sid}",)).fetchone()[0] == 1
        assert cur.execute("SELECT COUNT(*) FROM receipts WHERE user_id=1").fetchone()[0] == 1
    assert optimizer.committed_spend(1)["total"] == pytest.approx(cell["cost"])
    assert client.post(f"/api/session/{sid}/status", json={"status": "active"}).status_code == 200
    with db.cursor() as cur:
        assert cur.execute("SELECT COUNT(*) FROM intake_log WHERE note=?", (f"session:{sid}",)).fetchone()[0] == 0


def test_not_again_excludes_dish_from_future_plans(client):
    v = client.get("/api/plan/1").get_json()
    sid, item = next((c["session_id"], c["item"]) for _, _, c in _cells(v) if c["kind"] == "delivery")
    r = client.post(f"/api/session/{sid}/rate", json={"score": -1}).get_json()
    assert r["plan"]["learning"]["ratings"] == 1
    assert item not in [c["item"] for _, _, c in _cells(r["plan"])]
    assert client.post(f"/api/session/{sid}/rate", json={"score": 5}).status_code == 400


def test_like_from_new_place_suggests_adding_it(client):
    taste.set_favourites(1, [6])                                                            # only Saravana
    optimizer.optimize(1)
    v = client.get("/api/plan/1").get_json()
    cell = next((c for _, _, c in _cells(v) if c["kind"] == "delivery" and not c["usual"]), None)
    if cell is None:
        pytest.skip("plan picked only usual places")
    r = client.post(f"/api/session/{cell['session_id']}/rate", json={"score": 1}).get_json()
    assert r["suggest_favourite"]["restaurant_id"] == cell["restaurant_id"]


def test_ordered_or_past_meals_cannot_be_edited(client, monkeypatch):
    v = client.get("/api/plan/1").get_json()
    sid = _session(v, 0, "lunch")
    ws = dt.date.fromisoformat(v["plan"]["week_start"])
    monkeypatch.setattr(optimizer, "now", lambda: dt.datetime.combine(ws + dt.timedelta(days=1), dt.time(9)))
    r = client.post(f"/api/session/{sid}/choose", json={"item_id": 4})
    assert r.status_code == 400 and "passed" in r.get_json()["error"]
    v = client.get("/api/plan/1").get_json()
    assert v["grid"][0]["meals"]["lunch"]["status"] == "past"
    assert any(h["kind"] == "reconcile" for h in v["heads_up"])


# --------------------------------------------------------------------------- #
# Timing, weather, holidays, heads-up
# --------------------------------------------------------------------------- #
def test_order_by_time_works_back_from_arrival():
    plan = timing.order_plan("dinner", eta_min=30, time_shift=None)
    assert plan["arrive"] == "20:30" and plan["order_at"] == "19:50"
    rainy = timing.order_plan("dinner", eta_min=30, time_shift={"offpeak_min": 1185, "saving": 40}, condition="rain")
    assert rainy["arrive"] == "19:45" and rainy["order_at"] == "18:50" and "rain" in rainy["why"]
    assert timing.swiggy_handoff("Hotel Saravana Bhavan", "Veg Meals").startswith("https://www.swiggy.com/search?query=")


def test_plan_cells_carry_order_time_and_handoff(client):
    v = client.get("/api/plan/1").get_json()
    deliveries = [c for _, _, c in _cells(v) if c["kind"] == "delivery"]
    assert deliveries and all(c["order"]["order_at"] and c["handoff_url"] for c in deliveries)


def test_weather_classification():
    assert weather.classify(95, 10, 30) == "storm"
    assert weather.classify(61, 20, 30) == "rain"
    assert weather.classify(2, 70, 30) == "rain"
    assert weather.classify(1, 10, 38) == "hot"
    assert weather.classify(0, 0, 31) == "clear"


def test_live_forecast_is_used_and_offline_falls_back(seeded, monkeypatch):
    ws = seeded["week_start"]
    monkeypatch.setattr(config, "WEATHER_PROVIDER", "live")
    monkeypatch.setattr(weather, "_last_failure", {"ts": 0.0})

    def offline(*a, **k):
        raise OSError("no network")
    monkeypatch.setattr(weather.urllib.request, "urlopen", offline)
    wk = weather.week("Chennai", ws)
    assert all(w["source"] == "sample" for w in wk.values())          # offline → sample feed

    start = dt.date.fromisoformat(ws)
    days = [(start + dt.timedelta(days=i)).isoformat() for i in range(16)]
    payload = {"daily": {"time": days, "weather_code": [63] + [0] * 15,
                         "temperature_2m_max": [29] + [33] * 15, "precipitation_probability_max": [90] + [5] * 15}}

    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, *a):
            return json.dumps(payload).encode()
    monkeypatch.setattr(weather.urllib.request, "urlopen", lambda *a, **k: Resp())
    monkeypatch.setattr(weather, "_last_failure", {"ts": 0.0})
    from smartplate import clock
    monkeypatch.setattr(clock, "today", lambda: start)
    wk = weather.week("Chennai", ws)
    assert wk[0]["source"] == "live" and wk[0]["condition"] == "rain" and wk[0]["rain_prob"] == 90
    assert wk[1]["condition"] == "clear"


def test_holiday_calendar_is_real_and_fasts_are_opt_in(client, friday):
    rows = client.get("/api/user/1/calendar").get_json()
    names = [r["name"] for r in rows]
    assert names[0] == "Gandhi Jayanti" and "Diwali" in names
    assert "Navratri" not in names                                  # the sample profile doesn't keep it
    client.patch("/api/user/1/setup", json={"observances": ["navratri"]})
    names = [r["name"] for r in client.get("/api/user/1/calendar").get_json()]
    assert names.count("Navratri") == 1                            # multi-day fast listed once
    diwali = next(r for r in festivals.INDIA_CALENDAR if r[1] == "Diwali")
    assert diwali[0] == "2026-11-08"


def test_holiday_raises_dinner_surge(seeded):
    ws = dt.date.fromisoformat(seeded["week_start"])
    with db.cursor() as cur:
        cur.execute("INSERT INTO festivals(name, iso_date, effect) VALUES ('Test holiday', ?, 'holiday')",
                    ((ws + dt.timedelta(days=6)).isoformat(),))
    user, plan = models.get_user(1), models.get_plan(seeded["plan_id"])
    ctx = optimizer.build_context(user, plan)
    item = next(it for it in ctx["menu"] if it["name"] == "Veg Meals")
    normal = optimizer._delivery_candidate(user, plan, {"day": 5, "meal": "dinner"}, item, ctx)
    holiday = optimizer._delivery_candidate(user, plan, {"day": 6, "meal": "dinner"}, item, ctx)
    assert holiday["base_cost"] == normal["base_cost"]
    assert (holiday["time_shift"] or {}).get("peak_mult", 0) >= 1.25 * optimizer.HOLIDAY_DINNER_SURGE - 1e-6


def test_heads_up_flags_rain_and_budget_skips(client):
    v = client.patch("/api/user/1/setup", json={"weekly_budget": 600}).get_json()
    kinds = {h["kind"] for h in v["heads_up"]}
    assert "budget" in kinds                                        # meals that didn't fit are called out
    assert "weather" in kinds                                       # seeded rain on Tuesday
    assert len(v["heads_up"]) <= 6


def test_existing_database_is_migrated_in_place(tmp_path, monkeypatch):
    import sqlite3
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT NOT NULL, city TEXT NOT NULL)")
    conn.execute("INSERT INTO users(name, city) VALUES ('Kept', 'Chennai')")
    conn.commit()
    conn.close()
    monkeypatch.setattr(config, "DB_PATH", str(path))
    db.init_db()
    with db.cursor() as cur:
        cols = {r["name"] for r in cur.execute("PRAGMA table_info(users)")}
        assert {"observances", "prefs"} <= cols
        assert cur.execute("SELECT name FROM users").fetchone()["name"] == "Kept"


def test_current_plan_rolls_to_new_week(client, monkeypatch):
    first = client.get("/api/user/1/plan").get_json()["plan"]
    ws = dt.date.fromisoformat(first["week_start"])
    monkeypatch.setattr(optimizer, "now", lambda: dt.datetime.combine(ws + dt.timedelta(days=9), dt.time(8)))
    rolled = client.get("/api/user/1/plan").get_json()["plan"]
    assert rolled["id"] != first["id"] and rolled["week_start"] == (ws + dt.timedelta(days=7)).isoformat()


def test_swap_and_replan_do_not_reshuffle_other_meals(client):
    snap = lambda v: {c["session_id"]: c["item"] for _, _, c in _cells(v)}          # noqa: E731
    v = client.get("/api/plan/1").get_json()
    before = snap(v)
    deliveries = [c for _, _, c in _cells(v) if c["kind"] == "delivery"]
    a, b = deliveries[0]["session_id"], deliveries[-1]["session_id"]
    after = snap(client.post("/api/plan/1/swap", json={"a": a, "b": b}).get_json())
    assert [k for k in before if k not in (a, b) and before[k] != after[k]] == []
    again = snap(client.post("/api/plan/1/optimize", json={}).get_json())
    assert again == after                                           # a no-op re-plan is a no-op


def test_mode_switch_still_replans_freely(client):
    comfort = client.post("/api/plan/1/optimize", json={"mode": "comfort"}).get_json()
    tight = client.post("/api/plan/1/optimize", json={"mode": "survival"}).get_json()
    assert tight["budget"]["spend"] <= comfort["budget"]["spend"]


def test_clock_uses_app_timezone_not_server_utc():
    from smartplate import clock
    utc = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    if clock._TZ is None:
        pytest.skip("no timezone database on this machine")
    offset = (clock.now() - utc).total_seconds() / 3600
    assert abs(offset - 5.5) < 0.01                                  # IST is UTC+5:30


def test_next_up_moves_on_once_meal_is_had_and_rated(client):
    v = client.get("/api/plan/1").get_json()
    first = v["next_up"]["cell"]["session_id"]
    v = client.post(f"/api/session/{first}/confirm", json={}).get_json()
    assert v["next_up"]["cell"]["session_id"] == first            # stays up to be rated
    v = client.post(f"/api/session/{first}/rate", json={"score": 1}).get_json()["plan"]
    assert v["next_up"]["cell"]["session_id"] != first
