"""Taste memory: usual places, 👍/👎 ratings and the user's own order history.

Why we keep this ourselves: Swiggy's Food MCP `get_food_orders` returns only the
last ~5 orders as prose (Swiggy/swiggy-mcp-server-manifest#74), so "your usual
favourites" cannot be imported from the platform. Instead the user taps their usual
places once at onboarding, and every meal they rate or confirm sharpens it.

Rules the planner reads from here:
  • favourites  → the default candidate pool (mostly-usual, a few discoveries)
  • 👎 on a dish → that dish is excluded for DISLIKE_DAYS (an explicit "not again")
  • 👍 on a dish → a small taste bonus
  • history     → recency/frequency familiarity for the fatigue model
"""
import datetime as dt

from .. import db

DISLIKE_DAYS = 45
LIKE_BONUS = 0.12


def favourites(user_id: int) -> set[int]:
    with db.cursor() as cur:
        rows = cur.execute("SELECT restaurant_id FROM favourites WHERE user_id=?", (user_id,)).fetchall()
    return {r["restaurant_id"] for r in rows}


def set_favourites(user_id: int, restaurant_ids: list[int]) -> None:
    with db.cursor() as cur:
        known = {r["id"] for r in cur.execute("SELECT id FROM restaurants").fetchall()}
        unknown = [r for r in restaurant_ids if r not in known]
        if unknown:
            raise ValueError(f"Unknown restaurant id {unknown[0]}")
        cur.execute("DELETE FROM favourites WHERE user_id=?", (user_id,))
        cur.executemany("INSERT INTO favourites(user_id, restaurant_id) VALUES (?,?)",
                        [(user_id, r) for r in restaurant_ids])


def toggle_favourite(user_id: int, restaurant_id: int) -> bool:
    """Flip one restaurant in/out of the usual list; returns the new state."""
    favs = favourites(user_id)
    with db.cursor() as cur:
        if restaurant_id in favs:
            cur.execute("DELETE FROM favourites WHERE user_id=? AND restaurant_id=?", (user_id, restaurant_id))
            return False
        if not cur.execute("SELECT id FROM restaurants WHERE id=?", (restaurant_id,)).fetchone():
            raise ValueError("Unknown restaurant")
        cur.execute("INSERT INTO favourites(user_id, restaurant_id) VALUES (?,?)", (user_id, restaurant_id))
        return True


def rate(user_id: int, *, session_id: int, score: int, item_id=None, restaurant_id=None,
         recipe_key=None, iso_date: str | None = None) -> int:
    if score not in (1, -1):
        raise ValueError("Rate with 1 (liked) or -1 (not again)")
    with db.cursor() as cur:
        # one rating per meal: re-rating replaces the earlier tap
        cur.execute("DELETE FROM ratings WHERE user_id=? AND session_id=?", (user_id, session_id))
        cur.execute(
            "INSERT INTO ratings(user_id, session_id, item_id, restaurant_id, recipe_key, score, "
            "iso_date, created_ts) VALUES (?,?,?,?,?,?,?,datetime('now'))",
            (user_id, session_id, item_id, restaurant_id, recipe_key, score,
             iso_date or dt.date.today().isoformat()))
        return cur.lastrowid


def rating_for_session(user_id: int, session_id: int) -> int | None:
    with db.cursor() as cur:
        row = cur.execute("SELECT score FROM ratings WHERE user_id=? AND session_id=?",
                          (user_id, session_id)).fetchone()
    return row["score"] if row else None


def signals(user_id: int, today: str | None = None) -> dict:
    """Everything the planner needs in one read: disliked items (still inside the
    exclusion window), liked items, and a familiarity history keyed by item id."""
    today_d = dt.date.fromisoformat(today) if today else dt.date.today()
    with db.cursor() as cur:
        ratings = cur.execute("SELECT * FROM ratings WHERE user_id=?", (user_id,)).fetchall()
        orders = cur.execute(
            "SELECT d.item_id, p.week_start, s.day FROM decisions d "
            "JOIN sessions s ON s.id=d.session_id JOIN plans p ON p.id=d.plan_id "
            "WHERE p.user_id=? AND s.status IN ('ordered','confirmed') AND d.item_id IS NOT NULL",
            (user_id,)).fetchall()
    disliked, liked = set(), set()
    history: dict[int, dict] = {}

    def seen(item_id, iso):
        age = max(0, (today_d - dt.date.fromisoformat(iso)).days)
        h = history.setdefault(item_id, {"count": 0, "days_since": age})
        h["count"] += 1
        h["days_since"] = min(h["days_since"], age)

    for r in ratings:
        if not r["item_id"]:
            continue
        age = (today_d - dt.date.fromisoformat(r["iso_date"])).days
        if r["score"] < 0 and age <= DISLIKE_DAYS:
            disliked.add(r["item_id"])
        elif r["score"] > 0:
            liked.add(r["item_id"])
    for o in orders:
        iso = (dt.date.fromisoformat(o["week_start"]) + dt.timedelta(days=o["day"])).isoformat()
        seen(o["item_id"], iso)
    return {"disliked": disliked, "liked": liked - disliked, "history": history,
            "favourites": favourites(user_id), "ratings": len(ratings), "orders": len(orders)}
