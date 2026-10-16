"""Order-time reminders that work today, with no push server.

A plan is only useful if you remember to act on it at the right minute. Until a
background worker and push notifications exist, the most reliable reminder already
on everyone's phone is the calendar: this module turns the plan into an .ics file
with one short event per meal that needs action, each with an alarm:

  • delivery → at the order-by time: "Order Veg Meals · Saravana Bhavan · ₹150"
    (the description carries the Swiggy hand-off link and why that time)
  • cook     → 45 minutes before the meal: "Start cooking: Dal + rice"

Events carry stable UIDs per meal, so importing an updated file replaces rather than
duplicates them in calendars that honour UID/SEQUENCE (Apple, Google, Outlook).
Meals already had, ordered, skipped or in the past are left out.
"""
import datetime as dt

from icalendar import Alarm, Calendar, Event

from .. import clock
from . import models, timing

COOK_LEAD_MIN = 45
EVENT_MIN = 10


def _tz():
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(clock.TZ_NAME)
    except Exception:                                   # no tz database: floating local time
        return None


def upcoming(view: dict, at: dt.datetime | None = None) -> list[dict]:
    """The reminders a plan view implies, soonest first.

    A delivery meal's reminder fires when its Swiggy cart opens (timing.CART_WINDOW_MIN before
    order-by), so the nudge lands when the person can act on it, not at the last minute
    (usab audit 16 Oct #21). Once that moment has passed but order-by hasn't, the reminder
    is at order-by. Today's cells already carry Today's picks (today.overlay_plan), so the
    reminder names the dish the person picked on Today (#1)."""
    at = at or clock.now()
    user = view.get("user") or {}
    out = []
    for day in view["grid"]:
        if "date" not in day:
            continue
        date = dt.date.fromisoformat(day["date"])
        midnight = dt.datetime.combine(date, dt.time())
        for meal in models.MEALS:
            c = day["meals"].get(meal)
            if not c or c.get("status") != "active":
                continue
            extra = {}
            if c["kind"] == "delivery" and c.get("order"):
                o = c["order"]
                by = midnight + dt.timedelta(minutes=o["order_at_min"])
                opens = by - dt.timedelta(minutes=int(o.get("window_min") or timing.CART_WINDOW_MIN))
                start = opens if opens > at else by
                price = f" · ₹{c['cost']:.0f}" if c.get("cost") else ""
                where = f" · {c['restaurant']}" if c.get("restaurant") else ""
                title = (f"Cart open: order {c['item']}{where}{price}" if start == opens
                         else f"Order {c['item']}{where}{price}")
                body = f"Order by {o['order_at']}: {o['why']}."
                link = c.get("handoff_url")
                extra = {"order_by": by.isoformat(timespec="minutes"), "kind": "cart_open" if start == opens else "order_by"}
            elif c["kind"] == "cook" and c.get("recipe_key"):
                meal_min = models.meal_minute(meal, user)
                start = midnight + dt.timedelta(minutes=meal_min - COOK_LEAD_MIN)
                title = f"Start cooking: {c['item'].removeprefix('Cook: ')}"
                body = f"{meal.capitalize()} at {meal_min // 60:02d}:{meal_min % 60:02d}."
                link = None
                extra = {"kind": "cook"}
            else:
                continue
            if start <= at:
                continue
            closes = midnight + dt.timedelta(minutes=models.meal_window(meal, user)[3])
            out.append({"session_id": c["session_id"], "meal": meal, "day": day["day"], "date": day["date"],
                        "at": start.isoformat(timespec="minutes"), "title": title, "body": body, "link": link,
                        "closes": closes.isoformat(timespec="minutes"), "today_pick": bool(c.get("today_pick")),
                        **extra})
    out.sort(key=lambda r: r["at"])
    return out


def for_meal(reminders: list[dict], date: str, meal: str) -> list[dict]:
    """The one meal's reminder (a per-meal .ics, usab #22)."""
    return [r for r in reminders if r["date"] == date and r["meal"] == meal]


def to_ics(reminders: list[dict], *, plan_id: int, name: str = "Ziggy") -> bytes:
    cal = Calendar()
    cal.add("prodid", "-//Ziggy//order reminders//EN")
    cal.add("version", "2.0")
    cal.add("calscale", "GREGORIAN")
    cal.add("x-wr-calname", f"{name} · meals")
    tz = _tz()
    stamp = dt.datetime.now(dt.timezone.utc)
    for r in reminders:
        start = dt.datetime.fromisoformat(r["at"])
        if tz is not None:
            start = start.replace(tzinfo=tz)
        ev = Event()
        ev.add("uid", f"smartplate-{plan_id}-{r['session_id']}@smartplate.local")
        ev.add("dtstamp", stamp)
        ev.add("dtstart", start)
        ev.add("dtend", start + dt.timedelta(minutes=EVENT_MIN))
        ev.add("summary", r["title"])
        ev.add("description", r["body"] + (f"\n{r['link']}" if r["link"] else ""))
        if r["link"]:
            ev.add("url", r["link"])
        alarm = Alarm()
        alarm.add("action", "DISPLAY")
        alarm.add("description", r["title"])
        alarm.add("trigger", dt.timedelta(minutes=0))
        ev.add_component(alarm)
        cal.add_component(ev)
    if tz is not None and reminders:
        cal.add_missing_timezones()                     # RFC 5545: every TZID needs a VTIMEZONE
    return cal.to_ical()
