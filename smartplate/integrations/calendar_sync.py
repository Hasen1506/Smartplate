"""§5.1.2 — Calendar integration (Google Calendar / Outlook).

A meeting overlapping a meal's peak slot pushes that meal to its off-peak slot;
an all-day 'travel' event suspends every session that day, so the agent never
orders lunch you can't eat. v1.1 reads events from our store and can ingest a
standard .ics file (Google/Outlook export); live OAuth sync is the drop-in next step.
"""
import datetime as dt

from .. import clock, db
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


TRAVEL_WORDS = ("travel", "trip", "flight", "vacation", "out of town", "out of office", "ooo", "on leave",
                "offsite")
MAX_OCCURRENCES = 400


def _is_travel(comp) -> bool:
    """All-day events suspend meals only when they look like being away (M-03)."""
    text = " ".join(str(comp.get(k, "")) for k in ("SUMMARY", "CATEGORIES", "LOCATION")).lower()
    if comp.get("CATEGORIES") is not None:
        cats = comp.get("CATEGORIES")
        cats = cats if isinstance(cats, list) else [cats]
        text += " " + " ".join(str(c.to_ical(), "utf-8", "replace") if hasattr(c, "to_ical") else str(c)
                               for c in cats).lower()
    return any(w in f" {text} " for w in TRAVEL_WORDS)


def _local(value):
    """Aware datetimes → naive local time in the app's timezone; floating times stay as written."""
    if isinstance(value, dt.datetime):
        if value.tzinfo is not None:
            tz = clock._TZ
            value = value.astimezone(tz).replace(tzinfo=None) if tz else value.astimezone().replace(tzinfo=None)
        return value
    return value                                        # a date (all-day)


def _occurrences(comp, dtstart, week_start: dt.date, week_end: dt.date):
    """DTSTART plus RRULE/RDATE expansion within the plan week, minus EXDATEs."""
    starts = [dtstart]
    rule = comp.get("RRULE")
    if rule:
        from dateutil.rrule import rrulestr
        base = dtstart if isinstance(dtstart, dt.datetime) else dt.datetime.combine(dtstart, dt.time())
        tz_aware = isinstance(base, dt.datetime) and base.tzinfo is not None
        text = rule.to_ical().decode()
        # UNTIL must match DTSTART's awareness for dateutil
        rr = rrulestr(text, dtstart=base, ignoretz=not tz_aware)
        lo = dt.datetime.combine(week_start - dt.timedelta(days=31), dt.time())
        hi = dt.datetime.combine(week_end + dt.timedelta(days=1), dt.time())
        if tz_aware:
            lo, hi = lo.replace(tzinfo=base.tzinfo), hi.replace(tzinfo=base.tzinfo)
        starts = []
        for occ in rr.between(lo, hi, inc=True):
            starts.append(occ if isinstance(dtstart, dt.datetime) else occ.date())
            if len(starts) >= MAX_OCCURRENCES:
                break
    for key in ("RDATE",):
        for prop in (comp.get(key) if isinstance(comp.get(key), list) else [comp.get(key)] if comp.get(key) else []):
            starts += [d.dt for d in getattr(prop, "dts", [])]
    excluded = set()
    for prop in (comp.get("EXDATE") if isinstance(comp.get("EXDATE"), list) else
                 [comp.get("EXDATE")] if comp.get("EXDATE") else []):
        excluded |= {_local(d.dt) for d in getattr(prop, "dts", [])}
    return [s for s in starts if _local(s) not in excluded]


def ingest_ics(user_id: int, ics_text: str, week_start_iso: str) -> int:
    """Parse a .ics export and store events that fall within the plan week.

    Times are converted to the app's timezone, multi-day and overnight events cover
    every day they span, repeats (RRULE) are expanded for the week, all-day events
    suspend meals only when they look like travel, and re-importing the same event
    (same UID) replaces it instead of duplicating it (M-03).
    """
    try:
        from icalendar import Calendar
    except ImportError:
        return 0
    start = dt.date.fromisoformat(week_start_iso)
    end = start + dt.timedelta(days=6)
    added = 0
    cal = Calendar.from_ical(ics_text)
    rows = []
    uids = set()
    for comp in cal.walk("VEVENT"):
        if comp.get("DTSTART") is None:
            raise ValueError("Calendar event without a start time")     # nothing from this file is saved
        if str(comp.get("STATUS", "")).upper() == "CANCELLED":
            continue
        dtstart = comp.get("DTSTART").dt
        if comp.get("DTEND") is not None:
            duration = comp.get("DTEND").dt - dtstart
        elif comp.get("DURATION") is not None:
            duration = comp.get("DURATION").dt
        else:
            duration = dt.timedelta(days=1) if not isinstance(dtstart, dt.datetime) else dt.timedelta(0)
        title = str(comp.get("SUMMARY", ""))[:200]
        uid = str(comp.get("UID", "")) or None
        is_allday = not isinstance(dtstart, dt.datetime)
        kind_allday = "travel" if (is_allday and _is_travel(comp)) else "allday"
        if comp.get("RECURRENCE-ID") is not None:
            uid = f"{uid}#{comp.get('RECURRENCE-ID').to_ical().decode()}" if uid else None
        if uid:
            uids.add(uid)
        for occ in _occurrences(comp, dtstart, start, end):
            if is_allday:
                first = occ
                last = occ + max(duration, dt.timedelta(days=1)) - dt.timedelta(days=1)    # DTEND is exclusive
                d = first
                while d <= last:
                    day = (d - start).days
                    if 0 <= day <= 6:
                        rows.append((user_id, day, 0, 1439, kind_allday, title, uid))
                    d += dt.timedelta(days=1)
                continue
            s_local = _local(occ)
            e_local = _local(occ + duration) if duration else s_local
            e_local = max(e_local, s_local)
            d = s_local.date()
            while d <= e_local.date():
                day = (d - start).days
                seg_start = s_local if d == s_local.date() else dt.datetime.combine(d, dt.time())
                seg_end = e_local if d == e_local.date() else dt.datetime.combine(d, dt.time(23, 59))
                smin = seg_start.hour * 60 + seg_start.minute
                emin = seg_end.hour * 60 + seg_end.minute
                if 0 <= day <= 6 and emin >= smin:
                    rows.append((user_id, day, smin, emin, "busy", title, uid))
                d += dt.timedelta(days=1)
    with db.cursor() as cur:
        for uid in uids:                                 # re-import replaces the same event
            cur.execute("DELETE FROM calendar_events WHERE user_id=? AND uid=?", (user_id, uid))
        for row in dict.fromkeys(rows):                  # identical occurrences once
            cur.execute(
                "INSERT INTO calendar_events(user_id, day, start_min, end_min, kind, title, uid) "
                "VALUES (?,?,?,?,?,?,?)", row)
            added += 1
    return added
