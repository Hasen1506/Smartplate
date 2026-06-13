"""§5.2.11 — Surge prediction (not just reaction) + time-shift savings (§3.3).

A small predictive model returns a price multiplier for a (day, meal, weather)
slot from seeded history, with sane fallbacks. The planner uses it two ways:
  1. as a cost multiplier on delivery candidates, and
  2. to offer a cheaper OFF-PEAK time-shift when the calendar allows it.
"""
from .. import db
from .models import MEAL_WINDOWS

# Fallback multipliers when no history row exists.
_BASE = {"breakfast": 1.0, "lunch": 1.15, "dinner": 1.25}
_WEATHER_BUMP = {"clear": 0.0, "hot": 0.05, "rain": 0.30, "storm": 0.45}


def predict(city: str, day: int, meal: str, condition: str) -> float:
    with db.cursor() as cur:
        row = cur.execute(
            "SELECT multiplier FROM surge_history WHERE city=? AND day=? AND meal=? AND condition=?",
            (city, day, meal, condition),
        ).fetchone()
    if row:
        return float(row["multiplier"])
    return round(_BASE.get(meal, 1.1) + _WEATHER_BUMP.get(condition, 0.0), 3)


def offpeak_multiplier(peak_mult: float) -> float:
    """Off-peak slot clears most of the surge premium."""
    return round(1.0 + (peak_mult - 1.0) * 0.35, 3)


def time_shift_option(city: str, day: int, meal: str, condition: str, base_cost: float) -> dict | None:
    """Return a cheaper off-peak slot if it saves money, else None."""
    peak = predict(city, day, meal, condition)
    if peak <= 1.02:
        return None
    off = offpeak_multiplier(peak)
    saving = base_cost * (peak - off)
    if saving < 1.0:
        return None
    _open, _peak_min, offpeak_min, _close = MEAL_WINDOWS[meal]
    hh, mm = divmod(offpeak_min, 60)
    return {
        "offpeak_min": offpeak_min,
        "offpeak_hhmm": f"{hh:02d}:{mm:02d}",
        "peak_mult": peak,
        "offpeak_mult": off,
        "saving": round(saving, 2),
    }
