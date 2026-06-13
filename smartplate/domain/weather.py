"""§5.2.10 — Weather adaptation.

Rain biases toward comfort food and raises surge; heat biases toward lighter
meals. Reads a simulated per-city/day weather feed (swap for a live API later).
"""
from .. import db


def for_day(city: str, day: int) -> dict:
    with db.cursor() as cur:
        row = cur.execute(
            "SELECT condition, temp_c FROM weather WHERE city=? AND day=?", (city, day)
        ).fetchone()
    if not row:
        return {"condition": "clear", "temp_c": 30.0}
    return {"condition": row["condition"], "temp_c": row["temp_c"]}


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
