"""When to order — the one number a person actually needs from a food plan.

Food apps show an ETA *after* you order. SmartPlate works backwards from when you
want to eat: arrive time (the off-peak slot when that dodges surge, else the usual
meal time) − the outlet's ETA − a buffer (bigger on rainy days, when riders run late)
= "order by". It also builds the hand-off link used until live Swiggy checkout is
connected: it opens Swiggy's own search for that restaurant + dish, where the person
checks the real price and places the order themselves.
"""
import re as _re
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


# W6 note: same function W2 adds (identical code); the integrator keeps W2's copy.
# --------------------------------------------------------------------------- #
# Meal-slot suitability: a tiffin dish isn't dinner unless the person says so
# --------------------------------------------------------------------------- #
# South Indian tiffin dishes that are breakfast (and evening tiffin) food. Planned at
# dinner only when the person favourited them for dinner (their dinner pool).
BREAKFAST_ONLY = ("idli", "idly", "dosa", "dosai", "dose", "vada", "vadai", "pongal", "upma", "uppuma",
                  "uthappam", "uttapam", "oothappam", "appam", "idiyappam", "puttu", "kesari", "poha",
                  "aval upma", "rava kichadi", "bonda", "mini tiffin", "pesarattu", "paniyaram")
# …except where the word names something else (a snack, a chaat, a dinner dish).
NOT_TIFFIN = ("vada pav", "vadapav", "dahi vada", "thayir vadai", "thayir vada", "curd vada", "dosa batter",
              "idli batter", "chilli idli", "chilly idli", "idli fry", "idli manchurian", "kothu",
              "sweet pongal", "sakkarai pongal", "chakkarai pongal", "pav bhaji")
_TIFFIN_RE = _re.compile(r"\b(" + "|".join(_re.escape(w) for w in sorted(BREAKFAST_ONLY, key=len, reverse=True))
                         + r")s?\b")
_NOT_TIFFIN_RE = _re.compile(r"\b(" + "|".join(_re.escape(w) for w in NOT_TIFFIN) + r")\b")


def is_breakfast_only(name: str) -> bool:
    n = " ".join(str(name or "").lower().replace("-", " ").split())
    return bool(_TIFFIN_RE.search(n)) and not _NOT_TIFFIN_RE.search(n)


def slot_suitability(dish: dict | str, slot: str, *, favourited_for_slot: bool = False) -> bool:
    """Can `dish` be planned as `slot` (breakfast | lunch | dinner) without asking?

    Pure (no I/O). Breakfast-only tiffin dishes (idli, dosa, vada, pongal, upma…) are
    not planned as dinner, unless the person favourited that dish for dinner
    (`favourited_for_slot`, i.e. it's in their dinner pool). Lunch keeps the planner's
    existing soft "tiffin at lunch" penalty; breakfast takes anything that passed the
    other filters. Unknown slots are not suitable."""
    if slot not in ("breakfast", "lunch", "dinner"):
        return False
    if favourited_for_slot:
        return True
    name = dish if isinstance(dish, str) else (dish.get("name") or dish.get("item_name") or "")
    if slot == "dinner" and is_breakfast_only(name):
        return False
    return True
