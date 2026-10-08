"""The learning loop: one-tap rating reasons that each move one specific planner weight.

A thumbs-up/down says *whether*; a reason says *why*, and each why changes a different
thing (docs/value-and-simplicity.md: the user can see and undo what was learned):

  late          → that restaurant pays a delivery-reliability penalty
  small         → that dish's portion is scaled down (its kcal/protein count for less)
  spicy         → that dish pays a spice penalty (and, faintly, other dishes tagged spicy)
  pricey        → the user's cost weight rises (cheaper picks win more often) and that
                  dish pays a small value penalty
  great         → that dish gets a taste bonus and its restaurant a small one

Taps are stored, never folded into opaque numbers, so every effect is recomputed on read
and "undo" is deleting the taps behind one chip. Effects are bounded (MAX_TAPS) so a few
taps can't swamp the rest of the objective.
"""
from .. import clock, db

REASONS = {"late": "Arrived late", "small": "Small portion", "spicy": "Too spicy",
           "pricey": "Too pricey", "great": "Great"}
MAX_TAPS = 3
LATE_STEP = 0.25            # per tap, on every dish from that restaurant
PORTION_STEP = 0.85         # portion factor per "small" tap (0.85, 0.72, 0.61)
SPICY_STEP = 0.3            # per tap, on that dish
SPICY_TAG_STEP = 0.1        # per tap, on other dishes tagged "spicy"
PRICEY_COST_STEP = 0.15     # cost weight × (1 + 0.15 per tap)
PRICEY_ITEM_STEP = 0.1
GREAT_STEP = 0.12           # taste bonus per tap (same size as a 👍)
GREAT_PLACE_STEP = 0.04


def record(user_id: int, session_id: int, reasons: list, *, item_id=None, restaurant_id=None) -> list[str]:
    reasons = [r for r in dict.fromkeys(reasons or [])]
    bad = [r for r in reasons if r not in REASONS]
    if bad:
        raise ValueError(f"Unknown reason {bad[0]!r}; use one of: {', '.join(REASONS)}")
    with db.cursor() as cur:
        cur.execute("DELETE FROM rating_reasons WHERE user_id=? AND session_id=?", (user_id, session_id))
        for r in reasons:
            cur.execute("INSERT INTO rating_reasons(user_id, session_id, reason, item_id, restaurant_id, created_ts) "
                        "VALUES (?,?,?,?,?,?)", (user_id, session_id, r, item_id, restaurant_id,
                                                 clock.now().isoformat(timespec="seconds")))
    return reasons


def _taps(user_id: int) -> list[dict]:
    with db.cursor() as cur:
        return [dict(r) for r in cur.execute("SELECT * FROM rating_reasons WHERE user_id=? ORDER BY id", (user_id,))]


def weights(user_id: int) -> dict:
    """What the planner reads: per-restaurant, per-dish and global adjustments."""
    late, small, spicy, pricey_items, great, great_places = {}, {}, {}, {}, {}, {}
    n_spicy = n_pricey = 0
    for t in _taps(user_id):
        r, item, place = t["reason"], t["item_id"], t["restaurant_id"]
        if r == "late" and place:
            late[place] = late.get(place, 0) + 1
        elif r == "small" and item:
            small[item] = small.get(item, 0) + 1
        elif r == "spicy" and item:
            spicy[item] = spicy.get(item, 0) + 1
            n_spicy += 1
        elif r == "pricey":
            n_pricey += 1
            if item:
                pricey_items[item] = pricey_items.get(item, 0) + 1
        elif r == "great" and item:
            great[item] = great.get(item, 0) + 1
            if place:
                great_places[place] = great_places.get(place, 0) + 1
    cap = lambda n: min(n, MAX_TAPS)        # noqa: E731
    return {
        "late_pen": {k: round(LATE_STEP * cap(n), 4) for k, n in late.items()},
        "portion": {k: round(PORTION_STEP ** cap(n), 4) for k, n in small.items()},
        "spicy_pen": {k: round(SPICY_STEP * cap(n), 4) for k, n in spicy.items()},
        "spicy_tag_pen": round(SPICY_TAG_STEP * cap(n_spicy), 4),
        "cost_mult": round(1 + PRICEY_COST_STEP * cap(n_pricey), 4),
        "pricey_pen": {k: round(PRICEY_ITEM_STEP * cap(n), 4) for k, n in pricey_items.items()},
        "great_bonus": {k: round(GREAT_STEP * cap(n), 4) for k, n in great.items()},
        "great_place_bonus": {k: round(GREAT_PLACE_STEP * cap(n), 4) for k, n in great_places.items()},
    }



def adjust(item: dict, learned: dict | None) -> tuple[dict, float]:
    """(item with its learned portion applied, learned objective penalty; negative = bonus)."""
    if not learned:
        return item, 0.0
    iid, rid = item.get("id"), item.get("restaurant_id")
    factor = learned["portion"].get(iid)
    if factor:
        item = {**item, **{k: round(item.get(k, 0) * factor, 1) for k in ("kcal", "protein_g", "carbs_g", "fat_g", "sugar_g")}}
    pen = learned["late_pen"].get(rid, 0.0) + learned["spicy_pen"].get(iid, 0.0) + learned["pricey_pen"].get(iid, 0.0)
    if "spicy" in (item.get("tags") or []) and iid not in learned["spicy_pen"]:
        pen += learned["spicy_tag_pen"]
    pen -= learned["great_bonus"].get(iid, 0.0) + learned["great_place_bonus"].get(rid, 0.0)
    return item, round(pen, 4)


def chips(user_id: int) -> list[dict]:
    """What was learned, in words, each with the key that undoes it."""
    with db.cursor() as cur:
        rows = cur.execute(
            # one chip per (reason, what it is about); the columns a chip shows are constant
            # within its group, so MAX() just names them in a way Postgres accepts too
            "SELECT t.reason, MAX(t.item_id) item_id, MAX(t.restaurant_id) restaurant_id, COUNT(*) n, "
            "MAX(m.name) item, MAX(r.name) place "
            "FROM rating_reasons t LEFT JOIN menu_items m ON m.id=t.item_id LEFT JOIN restaurants r ON r.id=t.restaurant_id "
            "WHERE t.user_id=? GROUP BY t.reason, CASE WHEN t.reason='late' THEN t.restaurant_id "
            "WHEN t.reason='pricey' THEN 0 ELSE t.item_id END "
            "ORDER BY t.reason, CASE WHEN t.reason='late' THEN t.restaurant_id "
            "WHEN t.reason='pricey' THEN 0 ELSE t.item_id END", (user_id,)).fetchall()
    out = []
    for r in rows:
        if r["reason"] == "late":
            key, text = f"late:{r['restaurant_id']}", f"{r['place'] or 'This place'} often arrives late — planned less"
        elif r["reason"] == "pricey":
            key, text = "pricey", f"Cheaper picks weigh more (you said 'too pricey' {r['n']}×)"
        elif r["reason"] == "small":
            key, text = f"small:{r['item_id']}", f"{r['item'] or 'That dish'}: small portion — counted as less food"
        elif r["reason"] == "spicy":
            key, text = f"spicy:{r['item_id']}", f"{r['item'] or 'That dish'}: too spicy — planned less"
        else:
            key, text = f"great:{r['item_id']}", f"{r['item'] or 'That dish'}: great — planned more"
        out.append({"key": key, "reason": r["reason"], "text": text, "taps": r["n"]})
    return out


def undo(user_id: int, key: str) -> None:
    reason, _, ident = (key or "").partition(":")
    if reason not in REASONS:
        raise ValueError("Unknown learned setting")
    with db.cursor() as cur:
        if reason == "pricey":
            cur.execute("DELETE FROM rating_reasons WHERE user_id=? AND reason='pricey'", (user_id,))
        elif reason == "late":
            cur.execute("DELETE FROM rating_reasons WHERE user_id=? AND reason='late' AND restaurant_id=?",
                        (user_id, int(ident)))
        else:
            cur.execute("DELETE FROM rating_reasons WHERE user_id=? AND reason=? AND item_id=?",
                        (user_id, reason, int(ident)))
