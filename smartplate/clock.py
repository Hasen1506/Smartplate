"""One clock for the app, in the users' timezone.

Meal windows (13:00 lunch, 20:30 dinner) are local times in India, but servers and
Codespaces usually run in UTC — 5½ hours off, which would mark lunch "past" at the
wrong moment. Everything that asks "what time is it?" asks here.
"""
import datetime as dt
import os

TZ_NAME = os.environ.get("SMARTPLATE_TZ", "Asia/Kolkata")

try:
    from zoneinfo import ZoneInfo
    _TZ = ZoneInfo(TZ_NAME)
except Exception:  # no tz database (e.g. bare Windows Python) → the machine's local time
    _TZ = None


def now() -> dt.datetime:
    """Naive local datetime in the app's timezone (the DB stores naive local times)."""
    if _TZ is None:
        return dt.datetime.now()
    return dt.datetime.now(_TZ).replace(tzinfo=None)


def today() -> dt.date:
    return now().date()
