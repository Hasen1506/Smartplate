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
from . import models

COOK_LEAD_MIN = 45
EVENT_MIN = 10


def _tz():
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(clock.TZ_NAME)
    except Exception:                                   # no tz database: floating local time
        return None


def upcoming(view: dict, at: dt.datetime | None = None) -> list[dict]:
    """The reminders a plan view implies, soonest first."""
    at = at or clock.now()
    out = []
    for day in view["grid"]:
        if "date" not in day:
            continue
        date = dt.date.fromisoformat(day["date"])
        for meal in models.MEALS:
            c = day["meals"].get(meal)
            if not c or c.get("status") != "active":
                continue
            if c["kind"] == "delivery" and c.get("order"):
                start = dt.datetime.combine(date, dt.time()) + dt.timedelta(minutes=c["order"]["order_at_min"])
                title = f"Order {c['item']} · {c['restaurant']} · ₹{c['cost']:.0f}"
                body = f"Order by {c['order']['order_at']}: {c['order']['why']}."
                link = c.get("handoff_url")
            elif c["kind"] == "cook" and c.get("recipe_key"):
                meal_min = models.MEAL_WINDOWS[meal][1]
                start = dt.datetime.combine(date, dt.time()) + dt.timedelta(minutes=meal_min - COOK_LEAD_MIN)
                title = f"Start cooking: {c['item'].removeprefix('Cook: ')}"
                body = f"{meal.capitalize()} at {models.MEAL_WINDOWS[meal][1] // 60:02d}:{models.MEAL_WINDOWS[meal][1] % 60:02d}."
                link = None
            else:
                continue
            if start <= at:
                continue
            out.append({"session_id": c["session_id"], "meal": meal, "day": day["day"], "date": day["date"],
                        "at": start.isoformat(timespec="minutes"), "title": title, "body": body, "link": link})
    out.sort(key=lambda r: r["at"])
    return out


def to_ics(reminders: list[dict], *, plan_id: int, name: str = "SmartPlate") -> bytes:
    cal = Calendar()
    cal.add("prodid", "-//SmartPlate//order reminders//EN")
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
