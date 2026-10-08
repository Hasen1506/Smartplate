"""Remove the seeded sample data from a real database (Oct 2026 "no fake data" rule).

Earlier versions seeded every empty database with three shared sample profiles
("Sample profile", "Meera", "Arjun"), a sample Chennai restaurant catalogue, a sample
weather week, invented surge history and the sample household "Flat 3B". None of it
is real, so on a production database it is removed once, at start-up, and never
seeded again (seeding needs config.FIXTURE_DATA, which production can't enable).

Only rows that are provably seeded go:
  • a user is a seeded sample only when ALL hold: its name is one of the three seeded
    names, its prefs carry "sample": true, it has no profile key (access_hash IS NULL:
    every profile a person created has one), and no sign-in. A real account matches
    none of these, so it is never touched;
  • catalogue rows only where source = 'sample' (live Swiggy rows are source 'live');
  • the sample weather and surge tables (only ever filled by the seed);
  • the household "Flat 3B" only when no remaining user belongs to it.

Real users keep everything. A real user's open meals that still point at a sample
dish lose that pick (and pins/favourites pointing at sample restaurants are dropped);
their plan is re-planned from real data on start-up. Meals already eaten or ordered
keep their history: decisions store the dish and restaurant names.
"""
import logging

from . import db

log = logging.getLogger("smartplate.cleanup")

SAMPLE_NAMES = ("Sample profile", "Meera", "Arjun")
SAMPLE_HOUSEHOLD = "Flat 3B"
OPEN_STATUSES = ("active", "snoozed")


def _seeded_user_ids(cur) -> list[int]:
    marks = ",".join("?" * len(SAMPLE_NAMES))
    rows = cur.execute(f"SELECT id, prefs FROM users WHERE name IN ({marks}) AND access_hash IS NULL",
                       SAMPLE_NAMES).fetchall()
    ids = []
    for row in rows:
        prefs = db.jl(row["prefs"], {})
        if not (isinstance(prefs, dict) and prefs.get("sample") is True):
            continue
        if cur.execute("SELECT 1 FROM logins WHERE user_id=?", (row["id"],)).fetchone():
            continue
        ids.append(row["id"])
    return ids


def remove_seeded_samples() -> dict:
    """Delete seeded sample users and data. Idempotent; returns what was removed."""
    from .profile_data import purge
    removed = {"users": [], "restaurants": 0, "menu_items": 0, "weather": 0, "surge_history": 0,
               "households": 0, "open_meals_cleared": 0, "favourites": 0, "pins": 0}
    with db.cursor() as cur:
        for uid in _seeded_user_ids(cur):
            name = cur.execute("SELECT name FROM users WHERE id=?", (uid,)).fetchone()["name"]
            purge(cur, uid, people=False)
            removed["users"].append({"id": uid, "name": name})

        sample_items = [r["id"] for r in cur.execute("SELECT id FROM menu_items WHERE source='sample'").fetchall()]
        sample_rest = [r["id"] for r in cur.execute("SELECT id FROM restaurants WHERE source='sample'").fetchall()]
        replan = set()
        if sample_items:
            marks = ",".join("?" * len(sample_items))
            st = ",".join("?" * len(OPEN_STATUSES))
            rows = cur.execute(
                f"SELECT d.id, d.plan_id FROM decisions d JOIN sessions s ON s.id = d.session_id "
                f"WHERE d.item_id IN ({marks}) AND d.chosen_kind='delivery' AND s.status IN ({st})",
                (*sample_items, *OPEN_STATUSES)).fetchall()
            for r in rows:
                cur.execute("DELETE FROM decisions WHERE id=?", (r["id"],))
                replan.add(r["plan_id"])
            removed["open_meals_cleared"] = len(rows)
            for s in cur.execute("SELECT id, plan_id, pinned FROM sessions WHERE pinned IS NOT NULL").fetchall():
                pin = db.jl(s["pinned"], {})
                if isinstance(pin, dict) and pin.get("item_id") in sample_items:
                    cur.execute("UPDATE sessions SET pinned=NULL WHERE id=?", (s["id"],))
                    replan.add(s["plan_id"])
                    removed["pins"] += 1
            removed["menu_items"] = cur.execute(f"DELETE FROM menu_items WHERE id IN ({marks})",
                                                sample_items).rowcount
        if sample_rest:
            marks = ",".join("?" * len(sample_rest))
            removed["favourites"] = cur.execute(f"DELETE FROM favourites WHERE restaurant_id IN ({marks})",
                                                sample_rest).rowcount
            removed["restaurants"] = cur.execute(f"DELETE FROM restaurants WHERE id IN ({marks})",
                                                 sample_rest).rowcount
        removed["weather"] = cur.execute("DELETE FROM weather").rowcount
        removed["surge_history"] = cur.execute("DELETE FROM surge_history").rowcount
        hh = cur.execute("SELECT id FROM households WHERE name=?", (SAMPLE_HOUSEHOLD,)).fetchall()
        for h in hh:
            if not cur.execute("SELECT 1 FROM users WHERE household_id=?", (h["id"],)).fetchone():
                removed["households"] += cur.execute("DELETE FROM households WHERE id=?", (h["id"],)).rowcount
    removed["replanned_plans"] = sorted(replan)
    if any(v for k, v in removed.items() if k != "replanned_plans") or replan:
        log.warning("Removed seeded sample data: %s", removed)
    return removed


def replan(plan_ids) -> None:
    """Re-plan real users' plans whose open meals pointed at removed sample dishes."""
    from .kernel import optimizer
    for pid in plan_ids:
        try:
            optimizer.optimize(pid)
        except Exception:                                   # never block start-up on one plan
            log.exception("Re-planning plan %s after the sample cleanup failed", pid)


# Community weeks are only what real people shared. Earlier trials seeded two invented
# members ("campus_survivor", "veg_athlete") with invented adoption counts; they are not
# seeded any more and are removed from databases that still hold them.
INVENTED_COMMUNITY = (("campus_survivor", "₹1500/week student survival"),
                      ("veg_athlete", "High-protein veg week"))


def remove_invented_community() -> int:
    with db.cursor() as cur:
        n = 0
        for author, title in INVENTED_COMMUNITY:
            n += cur.execute("DELETE FROM community_templates WHERE author=? AND title=? AND author_user_id IS NULL",
                             (author, title)).rowcount
    return n
