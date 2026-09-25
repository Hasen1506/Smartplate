"""§5.2.9 — Festival, holiday and fasting calendar (India-first).

Effects:
  feast   → bias toward festive dishes
  holiday → a heads-up: people are home, kitchens are busy, surge runs higher
  fast    → suspend daytime meals (iftar / moonrise dinner kept) — ONLY for users
            who told us they keep that fast (`users.observances`). We never assume
            someone's religion or practice from a date.
  suspend → suspend every meal that day

`INDIA_CALENDAR` covers late 2026 → early 2027 (sources in docs/value-and-simplicity.md).
Lunar/sighting-based dates are flagged `approx` and shown as "may shift by a day".
Refresh the list yearly.
"""
import datetime as dt

from .. import db

# (iso_date, name, effect, observance, approx, note)
INDIA_CALENDAR = [
    ("2026-10-02", "Gandhi Jayanti", "holiday", "", 0, "national holiday — most people home"),
    *[((dt.date(2026, 10, 11) + dt.timedelta(days=i)).isoformat(), "Navratri", "fast", "navratri", 1,
       "fasting day for those observing — sattvic/fasting dishes at dinner")
      for i in range(9)],
    ("2026-10-20", "Dussehra", "feast", "", 0, "festive meals; kitchens busy in the evening"),
    ("2026-10-29", "Karva Chauth", "fast", "karva_chauth", 1, "day-long fast; dinner after moonrise"),
    ("2026-11-08", "Diwali", "feast", "", 0, "festive dinner; many outlets close early — order early"),
    ("2026-11-11", "Bhai Dooj", "feast", "", 1, "family meal"),
    ("2026-11-24", "Guru Nanak Jayanti", "holiday", "", 1, "public holiday"),
    ("2026-12-25", "Christmas", "feast", "", 0, "festive meals; high evening demand"),
    ("2026-12-31", "New Year's Eve", "holiday", "", 0, "peak surge night — plan dinner early"),
    ("2027-01-01", "New Year's Day", "holiday", "", 0, "many outlets open late"),
    ("2027-01-14", "Pongal / Makar Sankranti", "feast", "", 1, "festive meals"),
    ("2027-01-26", "Republic Day", "holiday", "", 0, "national holiday"),
    *[((dt.date(2027, 2, 10) + dt.timedelta(days=i)).isoformat(), "Ramadan", "fast", "ramadan", 1,
       "daytime fast for those observing — iftar dinner kept") for i in range(28)],
    ("2027-03-10", "Eid al-Fitr", "feast", "", 1, "festive meals"),
    ("2027-03-23", "Holi", "holiday", "", 1, "many outlets close till afternoon"),
]


def seed_calendar(cur) -> None:
    cur.executemany(
        "INSERT INTO festivals(iso_date, name, effect, observance, approx, note) VALUES (?,?,?,?,?,?)",
        INDIA_CALENDAR)


def for_week(week_start_iso: str) -> dict:
    """Return {day_index: festival_row} for the plan week. When a day has several
    entries, a feast/holiday wins over an opt-in fast for display; the fast still
    applies to observers via `for_week_all`."""
    by_day = for_week_all(week_start_iso)
    rank = {"suspend": 0, "feast": 1, "holiday": 2, "fast": 3}
    return {d: sorted(rows, key=lambda r: rank.get(r["effect"], 9))[0] for d, rows in by_day.items()}


def for_week_all(week_start_iso: str) -> dict:
    start = dt.date.fromisoformat(week_start_iso)
    end = (start + dt.timedelta(days=6)).isoformat()
    with db.cursor() as cur:
        rows = cur.execute("SELECT * FROM festivals WHERE iso_date BETWEEN ? AND ? ORDER BY iso_date",
                           (start.isoformat(), end)).fetchall()
    out: dict[int, list] = {}
    for r in rows:
        delta = (dt.date.fromisoformat(r["iso_date"]) - start).days
        out.setdefault(delta, []).append(db.row_to_dict(r))
    return out


def applies_to(festival: dict | None, user: dict | None) -> bool:
    """A fast binds only users who keep it; everything else applies to everyone."""
    if not festival:
        return False
    obs = festival.get("observance") or ""
    if festival.get("effect") == "fast" and obs:
        return user is not None and obs in (user.get("observances") or [])
    return True


def fast_for(day_rows: list[dict] | None, user: dict | None) -> dict | None:
    """The fast (if any) this user keeps on a day."""
    return next((f for f in (day_rows or []) if f["effect"] == "fast" and applies_to(f, user)), None)


def suspends_session(festival: dict | None, meal: str, user: dict | None = None) -> bool:
    if not festival:
        return False
    if festival.get("observance") and user is not None and not applies_to(festival, user):
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
