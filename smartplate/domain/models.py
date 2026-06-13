"""Thin data-access helpers and shared constants.

Everything returns plain dicts/lists so the rest of the engine stays simple and
the optimiser can treat features uniformly.
"""
from .. import db

# Meal windows in minutes-from-midnight: (open, peak, offpeak, close).
# The off-peak slot is the cheaper time the surge engine can shift into (§3.3).
MEAL_WINDOWS = {
    "breakfast": (480, 540, 495, 600),    # 08:00 / 09:00 / 08:15 / 10:00
    "lunch": (750, 780, 760, 840),        # 12:30 / 13:00 / 12:40 / 14:00
    "dinner": (1170, 1230, 1185, 1290),   # 19:30 / 20:30 / 19:45 / 21:30
}
MEALS = ("breakfast", "lunch", "dinner")
DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def get_user(user_id: int) -> dict | None:
    with db.cursor() as cur:
        row = cur.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    if not row:
        return None
    u = db.row_to_dict(row)
    u["allergens"] = db.jl(u["allergens"])
    u["medical"] = db.jl(u["medical"])
    u["nutrition_targets"] = db.jl(u["nutrition_targets"], {})
    u["health_targets"] = db.jl(u["health_targets"], {})
    return u


def list_users() -> list[dict]:
    with db.cursor() as cur:
        rows = cur.execute("SELECT id, name, city, mode FROM users ORDER BY id").fetchall()
    return [db.row_to_dict(r) for r in rows]


def get_household_members(household_id: int) -> list[dict]:
    with db.cursor() as cur:
        rows = cur.execute("SELECT * FROM users WHERE household_id=? ORDER BY id", (household_id,)).fetchall()
    out = []
    for r in rows:
        u = db.row_to_dict(r)
        u["allergens"] = db.jl(u["allergens"])
        u["medical"] = db.jl(u["medical"])
        out.append(u)
    return out


def menu_for_city(city: str) -> list[dict]:
    """All open restaurants' items in a city, joined and ready for candidate build."""
    with db.cursor() as cur:
        rows = cur.execute(
            """
            SELECT m.*, r.name AS restaurant_name, r.rating AS restaurant_rating,
                   r.delivery_fee, r.eta_min, r.is_open, r.flaky, r.city
            FROM menu_items m JOIN restaurants r ON r.id = m.restaurant_id
            WHERE r.city = ? AND r.is_open = 1
            """,
            (city,),
        ).fetchall()
    items = []
    for r in rows:
        d = db.row_to_dict(r)
        d["allergens"] = db.jl(d["allergens"])
        d["tags"] = db.jl(d["tags"])
        d["reviews"] = db.jl(d["reviews"])
        items.append(d)
    return items


def get_plan(plan_id: int) -> dict | None:
    with db.cursor() as cur:
        row = cur.execute("SELECT * FROM plans WHERE id=?", (plan_id,)).fetchone()
    return db.row_to_dict(row) if row else None


def sessions_for_plan(plan_id: int) -> list[dict]:
    with db.cursor() as cur:
        rows = cur.execute(
            "SELECT * FROM sessions WHERE plan_id=? ORDER BY day, "
            "CASE meal WHEN 'breakfast' THEN 0 WHEN 'lunch' THEN 1 ELSE 2 END",
            (plan_id,),
        ).fetchall()
    return [db.row_to_dict(r) for r in rows]


def decisions_for_plan(plan_id: int) -> list[dict]:
    with db.cursor() as cur:
        rows = cur.execute(
            "SELECT d.*, s.day AS day, s.meal AS meal, s.status AS session_status "
            "FROM decisions d JOIN sessions s ON s.id = d.session_id "
            "WHERE d.plan_id=? ORDER BY s.day, "
            "CASE s.meal WHEN 'breakfast' THEN 0 WHEN 'lunch' THEN 1 ELSE 2 END",
            (plan_id,)).fetchall()
    out = []
    for r in rows:
        d = db.row_to_dict(r)
        d["reasons"] = db.jl(d["reasons"])
        d["nutrition"] = db.jl(d["nutrition"], {})
        d["time_shift"] = db.jl(d["time_shift"], None) if d.get("time_shift") else None
        out.append(d)
    return out
