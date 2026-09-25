"""When to order — the one number a person actually needs from a food plan.

Food apps show an ETA *after* you order. SmartPlate works backwards from when you
want to eat: arrive time (the off-peak slot when that dodges surge, else the usual
meal time) − the outlet's ETA − a buffer (bigger on rainy days, when riders run late)
= "order by". It also builds the hand-off link used until live Swiggy checkout is
connected: it opens Swiggy's own search for that restaurant + dish, where the person
checks the real price and places the order themselves.
"""
import urllib.parse

from .models import MEAL_WINDOWS

BASE_BUFFER_MIN = 10
BAD_WEATHER_EXTRA_MIN = 15


def hhmm(minutes: int) -> str:
    minutes %= 24 * 60
    h, m = divmod(minutes, 60)
    return f"{h:02d}:{m:02d}"


def order_plan(meal: str, *, eta_min: int | None, time_shift: dict | None, condition: str = "clear") -> dict:
    peak = MEAL_WINDOWS[meal][1]
    arrive = time_shift["offpeak_min"] if time_shift else peak
    buffer = BASE_BUFFER_MIN + (BAD_WEATHER_EXTRA_MIN if condition in ("rain", "storm") else 0)
    order_at = arrive - (eta_min or 35) - buffer
    why = f"arrives about {hhmm(arrive)}"
    if time_shift:
        why += f", ahead of the {hhmm(peak)} rush (saves ₹{time_shift['saving']:.0f})"
    if condition in ("rain", "storm"):
        why += "; extra time for rain"
    return {"order_at": hhmm(order_at), "order_at_min": order_at, "arrive": hhmm(arrive), "why": why}


def swiggy_handoff(restaurant: str, item: str) -> str:
    """Swiggy's public web search for this restaurant + dish (no account data involved)."""
    return "https://www.swiggy.com/search?" + urllib.parse.urlencode({"query": f"{restaurant} {item}".strip()})
