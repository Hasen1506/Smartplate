"""§5.2.9 — Festival / cultural calendar (India-first).

Effects:
  feast   → bias toward festive items, allow a soft budget stretch
  fast    → suspend daytime sessions (e.g. Ramadan), keep iftar dinner
  suspend → suspend all sessions that day (travel/holiday closures)
"""
import datetime as dt

from .. import db


def for_week(week_start_iso: str) -> dict:
    """Return {day_index: festival_row} for festivals falling in the plan week."""
    start = dt.date.fromisoformat(week_start_iso)
    out = {}
    with db.cursor() as cur:
        rows = cur.execute("SELECT * FROM festivals").fetchall()
    for r in rows:
        fdate = dt.date.fromisoformat(r["iso_date"])
        delta = (fdate - start).days
        if 0 <= delta <= 6:
            out[delta] = db.row_to_dict(r)
    return out


def suspends_session(festival: dict | None, meal: str) -> bool:
    if not festival:
        return False
    if festival["effect"] == "suspend":
        return True
    if festival["effect"] == "fast" and meal in ("breakfast", "lunch"):
        return True
    return False


def taste_bias(festival: dict | None, item: dict) -> float:
    if festival and festival["effect"] == "feast" and "festive" in item.get("tags", []):
        return -0.4
    return 0.0
