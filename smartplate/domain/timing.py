"""When to order — the one number a person actually needs from a food plan.

Food apps show an ETA *after* you order. SmartPlate works backwards from when you
want to eat: arrive time (the off-peak slot when that dodges surge, else the usual
meal time) − the outlet's ETA − a buffer (bigger on rainy days, when riders run late)
= "order by". It also builds the hand-off link used until live Swiggy checkout is
connected: it opens Swiggy's own search for that restaurant + dish, where the person
checks the real price and places the order themselves.
"""
import datetime as _dt
import os as _os
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


# --------------------------------------------------------------------------- #
# Absolute times: "order by" belongs to the meal's own date, not today's clock
# --------------------------------------------------------------------------- #
# The Swiggy cart opens this long before a meal's order-by time. Earlier than that,
# paying would deliver now, not at the meal, so we offer a reminder instead.
CART_WINDOW_MIN = int(_os.environ.get("SMARTPLATE_CART_WINDOW_MIN", "90"))
# The API refuses cart fills outside the window. Tests that aren't about timing (and
# fill carts at an arbitrary instant) switch the refusal off; the window is still shown.
CART_WINDOW_ENFORCED = _os.environ.get("SMARTPLATE_CART_WINDOW", "on") != "off"
# A meal stays orderable for a while after its time (late lunch); same grace as
# kernel/scheduler.PAST_GRACE, restated here so this module stays import-light.
LATE_GRACE_MIN = 90


class CartWindowClosed(ValueError):
    """A cart fill for a meal that can't be ordered right now (too early or long past)."""

    code = "cart_window"

    def __init__(self, message: str, *, opens_at: str | None = None, closed: bool = False):
        super().__init__(message)
        self.opens_at = opens_at
        self.closed = closed


def _tz():
    from .. import clock
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(clock.TZ_NAME)
    except Exception:
        return None


def _iso(at: _dt.datetime) -> str:
    """Local naive → ISO with the app timezone's offset, so a browser in any zone
    counts down to the same instant."""
    tz = _tz()
    return (at.replace(tzinfo=tz) if tz else at).isoformat(timespec="minutes")


def day_label(d: _dt.date, today: _dt.date) -> str:
    if d == today:
        return "Today"
    if d == today + _dt.timedelta(days=1):
        return "Tomorrow"
    return d.strftime("%a")


def order_by_at(date: _dt.date, order: dict) -> _dt.datetime:
    """The order-by instant of a meal on `date` (order_at_min may be <0: the day before)."""
    return _dt.datetime.combine(date, _dt.time()) + _dt.timedelta(minutes=int(order["order_at_min"]))


def order_status(date: _dt.date, order: dict, at: _dt.datetime, *, meal_min: int | None = None,
                 window_min: int | None = None) -> dict:
    """Everything the UI needs to say *when* about one delivery meal, computed from the
    meal's own date (never from today's clock reading of "12:15"):

    order_by_at   absolute instant, with the app's UTC offset
    minutes_left  minutes until order-by; None once it has passed (no countdown to the past)
    label         "12:15" today, "Tomorrow 12:15" / "Mon 12:15" later, so a later meal
                  never reads like a countdown
    cart_open     whether "Add to Swiggy cart" is offered now (window before order-by,
                  until the meal is long past)
    cart_opens_at when it will open (the "Remind me at …" time), None once open/closed
    """
    window = CART_WINDOW_MIN if window_min is None else window_min
    by = order_by_at(date, order)
    meal_at = (_dt.datetime.combine(date, _dt.time()) + _dt.timedelta(minutes=meal_min)) if meal_min is not None else by
    opens = by - _dt.timedelta(minutes=window)
    closes = max(meal_at, by) + _dt.timedelta(minutes=LATE_GRACE_MIN)
    secs = (by - at).total_seconds()
    minutes_left = int(secs // 60) if secs >= 0 else None
    today = at.date()
    hm = by.strftime("%H:%M")
    label = hm if by.date() == today else f"{day_label(by.date(), today)} {hm}"
    state = "early" if at < opens else ("closed" if at > closes else "open")
    return {"order_by_at": _iso(by), "minutes_left": minutes_left, "label": label,
            "late": secs < 0 and state == "open",
            "cart_open": state == "open", "cart_state": state,
            "cart_opens_at": _iso(opens) if state == "early" else None,
            "cart_opens_label": (opens.strftime("%H:%M") if opens.date() == today
                                 else f"{day_label(opens.date(), today)} {opens.strftime('%H:%M')}")
                                if state == "early" else None,
            "window_min": window}


def require_cart_window(status: dict, *, what: str = "This meal") -> None:
    """Refuse a cart fill outside the meal's window, with words a person can act on."""
    if not CART_WINDOW_ENFORCED:
        return
    if status["cart_state"] == "early":
        raise CartWindowClosed(
            f"{what} can go in your Swiggy cart from {status['cart_opens_label']} "
            f"({status['window_min']} min before its order-by time, {status['label']}). "
            "Paying in Swiggy now would deliver it now, not at the meal. We'll remind you then.",
            opens_at=status["cart_opens_at"])
    if status["cart_state"] == "closed":
        raise CartWindowClosed(f"{what}'s time has passed, so it can't be ordered from the plan any more. "
                               "Use “Hungry now?” to order something for right now.", closed=True)


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
