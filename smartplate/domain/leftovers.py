"""§5.2.8 — Leftover & home-cooking awareness.

"I cooked dal for tonight, skip Wednesday dinner." A leftover that covers a
session forces that session to a zero-cost 'cook' decision, pulling the user out
of Swiggy occasionally and saving budget — "the agent that respects your fridge."
"""
from .. import db


def for_user(user_id: int) -> dict:
    """Return {(day, meal): leftover_row} the user has logged."""
    with db.cursor() as cur:
        rows = cur.execute("SELECT * FROM leftovers WHERE user_id=?", (user_id,)).fetchall()
    return {(r["day"], r["meal"]): db.row_to_dict(r) for r in rows}


def covers(leftovers: dict, day: int, meal: str) -> dict | None:
    return leftovers.get((day, meal))
