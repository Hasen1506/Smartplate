"""Thin data-access helpers and shared constants.

Everything returns plain dicts/lists so the rest of the engine stays simple and
the optimiser can treat features uniformly.
"""
from .. import config, db

# Meal windows in minutes-from-midnight: (open, peak, offpeak, close).
# The off-peak slot is the cheaper time the surge engine can shift into (§3.3).
MEAL_WINDOWS = {
    "breakfast": (480, 540, 495, 600),    # 08:00 / 09:00 / 08:15 / 10:00
    "lunch": (750, 780, 760, 840),        # 12:30 / 13:00 / 12:40 / 14:00
    "dinner": (1170, 1230, 1185, 1290),   # 19:30 / 20:30 / 19:45 / 21:30
}
MEALS = ("breakfast", "lunch", "dinner")
# When a person may say they eat each meal (usab audit 16 Oct #14: a late-night dinner has to be
# possible). Same calendar day only, so a meal never spills into tomorrow.
MEAL_TIME_RANGE = {"breakfast": (300, 690), "lunch": (660, 990), "dinner": (1020, 1425)}


def meal_minute(meal: str, user: dict | None = None) -> int:
    """Minute of the day this person eats `meal`: their own time (prefs.meal_times, set in
    Rules or inferred from their Swiggy orders), else the usual peak in MEAL_WINDOWS."""
    own = ((user or {}).get("prefs") or {}).get("meal_times") or {}
    lo, hi = MEAL_TIME_RANGE.get(meal, (0, 1439))
    try:
        m = int(own.get(meal))
        if lo <= m <= hi:
            return m
    except (TypeError, ValueError):
        pass
    return MEAL_WINDOWS[meal][1]


def meal_window(meal: str, user: dict | None = None) -> tuple[int, int, int, int]:
    """(open, peak, offpeak, close) shifted to the person's own meal time."""
    base = MEAL_WINDOWS[meal]
    shift = meal_minute(meal, user) - base[1]
    return tuple(min(1439, w + shift) for w in base)


def has_own_time(meal: str, user: dict | None) -> bool:
    """True when the person set (or confirmed) their own time for this meal."""
    own = ((user or {}).get("prefs") or {}).get("meal_times") or {}
    lo, hi = MEAL_TIME_RANGE.get(meal, (0, 1439))
    return isinstance(own.get(meal), int) and lo <= own[meal] <= hi

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
    u["observances"] = db.jl(u.get("observances"))
    u["prefs"] = db.jl(u.get("prefs"), {})
    # Household / group mode (§5.2.7): the other members' hard rules travel with the
    # profile so every safety check (allergens.violates) enforces the union of them.
    u["household_members"] = ([{k: m[k] for k in ("id", "name", "diet", "allergens", "medical")}
                               for m in get_household_members(u["household_id"]) if m["id"] != u["id"]]
                              if u.get("household_id") else [])
    return u


def list_users() -> list[dict]:
    with db.cursor() as cur:
        # private profiles are never listed; their owners' browsers remember them
        rows = cur.execute("SELECT id, name, city, mode, prefs FROM users WHERE access_hash IS NULL "
                           "ORDER BY id").fetchall()
    out = []
    for r in rows:
        u = db.row_to_dict(r)
        u["setup_done"] = bool(db.jl(u.pop("prefs"), {}).get("setup_done"))
        out.append(u)
    return out


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
                   r.delivery_fee, r.eta_min, r.is_open, r.flaky, r.city,
                   r.provider_id AS restaurant_provider_id
            FROM menu_items m JOIN restaurants r ON r.id = m.restaurant_id
            WHERE r.city = ? AND r.is_open = 1 AND (? = 1 OR (r.source = 'live' AND m.source = 'live'))
            ORDER BY m.id
            """,
            (city, 1 if config.FIXTURE_DATA else 0),
        ).fetchall()
    items = []
    for r in rows:
        d = db.row_to_dict(r)
        d["allergens"] = db.jl(d["allergens"])
        d["tags"] = db.jl(d["tags"])
        d["reviews"] = db.jl(d["reviews"])
        items.append(d)
    return items


def menu_for_user(user: dict) -> list[dict]:
    """The catalogue a user's plan is built from: their live Swiggy menus, read for their
    chosen delivery address (domain/live_catalog). Without them there are no restaurant
    dishes at all (the plan offers home-cooked meals and says how to get real dishes);
    the sample catalogue exists only as test fixture data (config.FIXTURE_DATA)."""
    from . import live_catalog
    if user.get("id") and live_catalog.has_live(user["id"]):
        return live_catalog.with_learned_fees(user["id"], menu_for_city(live_catalog.city_key(user["id"])))
    return menu_for_city(user["city"]) if config.FIXTURE_DATA else []


def restaurants_for_city(city: str) -> list[dict]:
    with db.cursor() as cur:
        rows = cur.execute("SELECT * FROM restaurants WHERE city=? AND (? = 1 OR source='live') "
                           "ORDER BY rating DESC, name", (city, 1 if config.FIXTURE_DATA else 0)).fetchall()
    out = []
    for r in rows:
        d = db.row_to_dict(r)
        d["cuisines"] = db.jl(d["cuisines"])
        out.append(d)
    return out


def get_restaurant(restaurant_id: int) -> dict | None:
    with db.cursor() as cur:
        row = cur.execute("SELECT * FROM restaurants WHERE id=?", (restaurant_id,)).fetchone()
    return db.row_to_dict(row) if row else None


def get_session(session_id: int) -> dict | None:
    with db.cursor() as cur:
        row = cur.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
    return db.row_to_dict(row) if row else None


def session_date(plan: dict, day: int) -> str:
    import datetime as _dt
    return (_dt.date.fromisoformat(plan["week_start"]) + _dt.timedelta(days=day)).isoformat()


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
            "SELECT d.*, s.day AS day, s.meal AS meal, s.status AS session_status, "
            "s.scheduled_ts AS scheduled_ts, s.pinned AS pinned, s.eaters AS eaters "
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
