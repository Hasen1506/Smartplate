"""§5.2.10 — Weather adaptation, with a live forecast.

Rain biases toward comfort food and raises surge; heat biases toward lighter meals.

Source order for a given date:
  1. the cached live forecast (Open-Meteo, free and key-less; CC BY 4.0 — attribution
     shown in the UI; commercial use needs their paid plan),
  2. a fresh fetch when SMARTPLATE_WEATHER=live (the default outside tests),
  3. the synthetic weekly pattern seeded for the demo (always available offline).

A failed fetch backs off for a while so an offline machine never slows planning.
"""
import datetime as dt
import json
import time
import urllib.parse
import urllib.request

from .. import config, db

CITY_COORDS = {
    "Chennai": (13.0827, 80.2707), "Bengaluru": (12.9716, 77.5946), "Mumbai": (19.0760, 72.8777),
    "Delhi": (28.6139, 77.2090), "Hyderabad": (17.3850, 78.4867), "Pune": (18.5204, 73.8567),
    "Kolkata": (22.5726, 88.3639), "Ahmedabad": (23.0225, 72.5714),
}
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
CACHE_HOURS = 6
FAIL_BACKOFF_S = 30 * 60
_last_failure = {"ts": 0.0}


def classify(code: int, rain_prob: float, temp_max: float) -> str:
    """Map a WMO weather code + rain probability + max temperature to our 4 conditions."""
    if code >= 95:
        return "storm"
    if (51 <= code <= 67) or (80 <= code <= 82) or rain_prob >= 60:
        return "rain"
    if temp_max >= 36:
        return "hot"
    return "clear"


def _fetch_live(city: str) -> bool:
    """Fetch a 16-day daily forecast into the cache. Never raises."""
    coords = CITY_COORDS.get(city)
    if not coords or time.time() - _last_failure["ts"] < FAIL_BACKOFF_S:
        return False
    q = urllib.parse.urlencode({
        "latitude": coords[0], "longitude": coords[1], "timezone": "Asia/Kolkata", "forecast_days": 16,
        "daily": "weather_code,temperature_2m_max,precipitation_probability_max"})
    try:
        with urllib.request.urlopen(f"{FORECAST_URL}?{q}", timeout=config.WEATHER_TIMEOUT_S) as r:
            data = json.load(r)
        daily = data["daily"]
        rows = []
        for i, iso in enumerate(daily["time"]):
            code = int(daily["weather_code"][i] or 0)
            tmax = float(daily["temperature_2m_max"][i] or 30)
            prob = float((daily.get("precipitation_probability_max") or [0] * len(daily["time"]))[i] or 0)
            rows.append((city, iso, classify(code, prob, tmax), tmax, prob, dt.datetime.now().isoformat()))
    except Exception:  # network, proxy, schema drift — degrade to the synthetic feed
        _last_failure["ts"] = time.time()
        return False
    with db.cursor() as cur:
        cur.executemany("INSERT OR REPLACE INTO weather_cache(city, iso_date, condition, temp_c, "
                        "rain_prob, fetched_ts) VALUES (?,?,?,?,?,?)", rows)
    return True


def _cached(city: str, iso: str) -> dict | None:
    with db.cursor() as cur:
        row = cur.execute("SELECT * FROM weather_cache WHERE city=? AND iso_date=?", (city, iso)).fetchone()
    if not row:
        return None
    age_h = (dt.datetime.now() - dt.datetime.fromisoformat(row["fetched_ts"])).total_seconds() / 3600
    return {"condition": row["condition"], "temp_c": row["temp_c"], "rain_prob": row["rain_prob"],
            "source": "live", "stale": age_h > CACHE_HOURS}


def _simulated(city: str, day: int) -> dict:
    with db.cursor() as cur:
        row = cur.execute("SELECT condition, temp_c FROM weather WHERE city=? AND day=?", (city, day)).fetchone()
    if not row:
        return {"condition": "clear", "temp_c": 30.0, "rain_prob": 0, "source": "sample"}
    return {"condition": row["condition"], "temp_c": row["temp_c"],
            "rain_prob": 80 if row["condition"] in ("rain", "storm") else 10, "source": "sample"}


def week(city: str, week_start: str) -> dict:
    """{day_index: forecast} for the 7 days of a plan week (one fetch at most)."""
    start = dt.date.fromisoformat(week_start)
    dates = [(start + dt.timedelta(days=i)).isoformat() for i in range(7)]
    cached = {i: _cached(city, iso) for i, iso in enumerate(dates)}
    today = dt.date.today()
    horizon = [i for i in range(7) if 0 <= (start + dt.timedelta(days=i) - today).days <= 15]
    wants_fetch = any(cached[i] is None or cached[i]["stale"] for i in horizon)
    if config.WEATHER_PROVIDER == "live" and wants_fetch and _fetch_live(city):
        cached = {i: _cached(city, iso) for i, iso in enumerate(dates)}
    # The seeded feed is a Mon..Sun pattern, so index it by weekday, not offset.
    return {i: cached[i] or _simulated(city, (start.weekday() + i) % 7) for i in range(7)}


def for_day(city: str, day: int, week_start: str | None = None) -> dict:
    if week_start:
        return week(city, week_start)[day]
    return _simulated(city, day)


def taste_bias(condition: str, item: dict) -> float:
    """Small reward (negative number lowers objective) for weather-appropriate food."""
    tags = item.get("tags", [])
    if condition in ("rain", "storm") and "comfort" in tags:
        return -0.25
    if condition == "hot" and "light" in tags:
        return -0.25
    if condition == "hot" and "comfort" in tags:
        return 0.1   # mild penalty: heavy food on a hot day
    return 0.0


def note(condition: str) -> str:
    return {
        "rain": "rainy — comfort-food bias, surge likely",
        "storm": "stormy — comfort bias, high surge",
        "hot": "hot — lighter meals favoured",
        "clear": "clear",
    }.get(condition, condition)
