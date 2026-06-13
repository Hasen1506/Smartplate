"""§5.1.2 — Calendar integration (Google Calendar / Outlook).

A meeting overlapping a meal's peak slot pushes that meal to its off-peak slot;
an all-day 'travel' event suspends every session that day, so the agent never
orders lunch you can't eat. v1.1 reads events from our store and can ingest a
standard .ics file (Google/Outlook export); live OAuth sync is the drop-in next step.
"""
import datetime as dt

from .. import db
from ..domain.models import MEAL_WINDOWS


def events_for(user_id: int) -> list[dict]:
    with db.cursor() as cur:
        rows = cur.execute(
            "SELECT * FROM calendar_events WHERE user_id=? ORDER BY day, start_min", (user_id,)
        ).fetchall()
    return [db.row_to_dict(r) for r in rows]


def day_is_travel(events: list[dict], day: int) -> dict | None:
    return next((e for e in events if e["day"] == day and e["kind"] == "travel"), None)


def conflicts_with_peak(events: list[dict], day: int, meal: str) -> dict | None:
    """Return a busy event overlapping the meal's peak slot, if any."""
    open_min, peak_min, _offpeak, close_min = MEAL_WINDOWS[meal]
    for e in events:
        if e["day"] != day or e["kind"] != "busy":
            continue
        if e["start_min"] < close_min and e["end_min"] > open_min:
            return e
    return None


def ingest_ics(user_id: int, ics_text: str, week_start_iso: str) -> int:
    """Parse a .ics export and store events that fall within the plan week.

    Uses the icalendar library if available; degrades gracefully otherwise.
    """
    try:
        from icalendar import Calendar
    except ImportError:
        return 0
    start = dt.date.fromisoformat(week_start_iso)
    added = 0
    cal = Calendar.from_ical(ics_text)
    with db.cursor() as cur:
        for comp in cal.walk("VEVENT"):
            dtstart = comp.get("DTSTART").dt
            dtend = comp.get("DTEND").dt if comp.get("DTEND") else dtstart
            is_allday = not isinstance(dtstart, dt.datetime)
            date = dtstart.date() if isinstance(dtstart, dt.datetime) else dtstart
            day = (date - start).days
            if not (0 <= day <= 6):
                continue
            if is_allday:
                kind, smin, emin = "travel", 0, 1439
            else:
                kind = "busy"
                smin = dtstart.hour * 60 + dtstart.minute
                emin = dtend.hour * 60 + dtend.minute
            cur.execute(
                "INSERT INTO calendar_events(user_id, day, start_min, end_min, kind, title) "
                "VALUES (?,?,?,?,?,?)",
                (user_id, day, smin, emin, kind, str(comp.get("SUMMARY", ""))),
            )
            added += 1
    return added
