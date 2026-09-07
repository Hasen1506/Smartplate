"""Scheduling kernel — trigger windows, session lifecycle, snooze/skip/cook.

§5.1.5: a fully-automated agent must be pausable or it feels like a runaway
train. Sessions move through active → (snoozed|skipped|cooked|ordered). Only
'active' sessions enter the optimiser; the rest are honoured as-is.
"""
import datetime as dt

from .. import db
from ..domain.models import MEAL_WINDOWS, MEALS

VALID_STATES = {"active", "snoozed", "skipped", "cooked", "ordered"}


def trigger_ts(week_start_iso: str, day: int, meal: str, minute: int | None = None) -> str:
    base = dt.date.fromisoformat(week_start_iso) + dt.timedelta(days=day)
    if minute is None:
        minute = MEAL_WINDOWS[meal][1]  # peak
    h, m = divmod(minute, 60)
    return dt.datetime.combine(base, dt.time(h, m)).isoformat()


def build_week(plan_id: int, week_start_iso: str, meals=MEALS, days=7) -> None:
    """Create the session grid for a plan (idempotent per plan)."""
    with db.cursor() as cur:
        cur.execute("DELETE FROM sessions WHERE plan_id=?", (plan_id,))
        for day in range(days):
            for meal in meals:
                cur.execute(
                    "INSERT INTO sessions(plan_id, day, meal, scheduled_ts, status) "
                    "VALUES (?,?,?,?, 'active')",
                    (plan_id, day, meal, trigger_ts(week_start_iso, day, meal)),
                )


def set_status(session_id: int, status: str, note: str = "") -> None:
    if status not in VALID_STATES:
        raise ValueError(f"bad status {status!r}")
    with db.cursor() as cur:
        row = cur.execute('SELECT status FROM sessions WHERE id=?', (session_id,)).fetchone()
        if not row:
            raise ValueError('Session not found')
        if row['status'] == 'ordered':
            raise ValueError('An ordered meal cannot be changed or cancelled here')
        if status == 'ordered':
            raise ValueError('Use checkout to order a meal')
        cur.execute("UPDATE sessions SET status=?, note=? WHERE id=?", (status, note, session_id))


def snooze_day(plan_id: int, day: int, status: str = "snoozed", note: str = "") -> int:
    with db.cursor() as cur:
        cur.execute("UPDATE sessions SET status=?, note=? WHERE plan_id=? AND day=?",
                    (status, note, plan_id, day))
        return cur.rowcount
