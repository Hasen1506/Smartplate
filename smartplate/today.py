"""Today (v3): favourites first, rules second, the solver only when asked.

What the founder's sketch (13 Oct 2026) asks for, as one read model:
  * today's budget for the meals the person has today ("₹600 (B+L)"), changed with ± or a preset;
  * today's weather (domain/weather.py; "unknown" when there is no live feed — never invented);
  * the person's rules: diet + allergies (hard), minimum rating (default 4★) and maximum delivery
    time (default 30 min) (soft, shown as greyed-out items with the reason);
  * breakfast / lunch / dinner pools: dishes the person chose. Two kinds:
      - "swiggy": a dish from a restaurant's live Swiggy menu (stored by domain/meal_pools.py,
        the same pool the week planner reads), plus the rating/ETA Swiggy showed when it was added;
      - "typed": a dish the person typed in (name, place, price they pay). Labelled as theirs;
        it can't be put in a Swiggy cart, only shared or cooked;
  * today's picks: one pool dish (× quantity) per meal, chosen by the person;
  * "Decide for me": only when tapped, in one of four modes (MODES). Picks only from
    candidates that pass every hard rule and fit the budget.

Nothing here invents a price, a rating or a delivery time: unknown stays unknown. Tables are
created on first use (init_schema), like integrations/swiggy_connect.py does.
"""
from __future__ import annotations

import datetime as dt
import json

from . import clock, db

MEALS = ("breakfast", "lunch", "dinner")
LETTER = {"breakfast": "B", "lunch": "L", "dinner": "D"}
MODES = {
    "favourites": "From my favourites only",
    "favourites_cook": "Favourites + cook at home",
    "favourites_new": "Favourites + new places at or above my rating",
    "surprise": "Surprise me (within my rules)",
}
DEFAULT_MIN_RATING = 4.0
DEFAULT_MAX_ETA = 30
BUDGET_PRESETS = (300, 600, 1000)
MAX_QTY = 8
MAX_TYPED_PER_MEAL = 20
NEW_PLACES_PER_MEAL = 12

SCHEMA = """
CREATE TABLE IF NOT EXISTS today_settings (       -- the person's Today rules (v3 UI)
    user_id INTEGER PRIMARY KEY,
    min_rating REAL,
    max_eta INTEGER,
    nutrition INTEGER NOT NULL DEFAULT 0,
    updated_ts TEXT
);
CREATE TABLE IF NOT EXISTS today_budget (         -- a budget set for one day
    user_id INTEGER NOT NULL,
    day TEXT NOT NULL,
    amount REAL NOT NULL,
    PRIMARY KEY (user_id, day)
);
CREATE TABLE IF NOT EXISTS today_picks (          -- what the person (or "Decide for me") picked
    user_id INTEGER NOT NULL,
    day TEXT NOT NULL,
    meal TEXT NOT NULL,
    ref TEXT NOT NULL,
    qty INTEGER NOT NULL DEFAULT 1,
    payload TEXT NOT NULL,
    how TEXT NOT NULL DEFAULT 'you',
    PRIMARY KEY (user_id, day, meal)
);
CREATE TABLE IF NOT EXISTS pool_typed (           -- dishes the person typed in themselves
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL,
    meal TEXT NOT NULL,
    name TEXT NOT NULL,
    restaurant_name TEXT NOT NULL DEFAULT '',
    price REAL,
    veg INTEGER,
    kind TEXT NOT NULL DEFAULT 'order',
    added_ts TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS today_cart_bills (     -- the real Swiggy cart bill of a pick, once its cart is filled
    user_id INTEGER NOT NULL,
    day TEXT NOT NULL,
    restaurant_id TEXT NOT NULL,
    item_id TEXT NOT NULL,
    qty INTEGER NOT NULL,
    to_pay REAL NOT NULL,
    PRIMARY KEY (user_id, day, restaurant_id, item_id, qty)
);
CREATE TABLE IF NOT EXISTS pool_meta (            -- quantity + what Swiggy showed when a dish was added
    user_id INTEGER NOT NULL,
    meal TEXT NOT NULL,
    ref TEXT NOT NULL,
    qty INTEGER NOT NULL DEFAULT 1,
    rating REAL,
    eta_min INTEGER,
    PRIMARY KEY (user_id, meal, ref)
);
"""

def init_schema() -> None:
    with db.cursor() as cur:
        cur.executescript(SCHEMA)


# --------------------------------------------------------------------------- helpers
def _user(user_id: int) -> dict:
    from .domain import models
    user = models.get_user(user_id)
    if not user:
        raise KeyError("Profile not found")
    return user


def _today() -> str:
    return clock.today().isoformat()


def _meal(meal) -> str:
    if meal not in MEALS:
        raise ValueError("Choose breakfast, lunch or dinner")
    return meal


def _qty(value) -> int:
    try:
        q = int(value)
    except (TypeError, ValueError):
        raise ValueError("Quantity must be a whole number") from None
    if isinstance(value, bool) or not 1 <= q <= MAX_QTY:
        raise ValueError(f"Quantity must be between 1 and {MAX_QTY}")
    return q


def _num(value, lo, hi, what):
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError(f"{what} must be a number")
    try:
        n = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{what} must be a number") from None
    if not lo <= n <= hi:
        raise ValueError(f"{what} must be between {lo:g} and {hi:g}")
    return n


def meals_today(user: dict) -> list[str]:
    prefs = user.get("prefs") or {}
    rhythm = prefs.get("rhythm") or {}
    if rhythm:
        meals = [m for m in MEALS if rhythm.get(m, "skip") != "skip"]
    else:
        meals = [m for m in prefs.get("meals") or MEALS if m in MEALS]
    return meals or list(MEALS)


def settings(user_id: int) -> dict:
    init_schema()
    with db.cursor() as cur:
        row = cur.execute("SELECT * FROM today_settings WHERE user_id=?", (user_id,)).fetchone()
    row = dict(row) if row else {}
    return {"min_rating": row.get("min_rating") if row.get("min_rating") is not None else DEFAULT_MIN_RATING,
            "max_eta": row.get("max_eta") if row.get("max_eta") is not None else DEFAULT_MAX_ETA,
            "nutrition": bool(row.get("nutrition"))}


def set_rules(user_id: int, body: dict) -> dict:
    _user(user_id)
    now = settings(user_id)
    if "min_rating" in body:
        r = _num(body["min_rating"], 0, 5, "Minimum rating")
        now["min_rating"] = 0.0 if r is None else round(r, 1)
    if "max_eta" in body:
        e = _num(body["max_eta"], 0, 180, "Maximum delivery time")
        now["max_eta"] = 0 if e is None else int(e)          # 0 = no limit
    if "nutrition" in body:
        now["nutrition"] = bool(body["nutrition"])
    with db.cursor() as cur:
        cur.execute("INSERT OR REPLACE INTO today_settings(user_id, min_rating, max_eta, nutrition, updated_ts) "
                    "VALUES (?,?,?,?,?)", (user_id, now["min_rating"], now["max_eta"], int(now["nutrition"]),
                                            clock.now().isoformat(timespec="seconds")))
    return view(user_id)


def budget_for(user: dict) -> dict:
    """Today's budget: what the person set for today, else their weekly budget ÷ 7 (said so)."""
    init_schema()
    with db.cursor() as cur:
        row = cur.execute("SELECT amount FROM today_budget WHERE user_id=? AND day=?",
                          (user["id"], _today())).fetchone()
    weekly = float(user.get("weekly_budget") or 0)
    default = round(weekly / 7) if weekly else 0
    if row:
        return {"amount": float(row["amount"]), "source": "today", "default": default}
    return {"amount": float(default), "source": "week", "default": default,
            "basis": f"Your weekly ₹{weekly:,.0f} ÷ 7" if weekly else None}


def presets_for(daily) -> list[int]:
    """Today's quick amounts around the person's own day (weekly ÷ 7), not fixed rupees (ux #15)."""
    if not daily or daily < 50:
        return list(BUDGET_PRESETS)
    r = lambda x: max(50, int(round(x / 50.0)) * 50)                          # noqa: E731
    return sorted({r(daily * 0.75), r(daily), r(daily * 1.25)})


def _eaters(user_id: int) -> int:
    """Default portions for a new usual dish: "How many eat?" from onboarding (ux #15)."""
    from .domain import profile
    return profile.eaters(_user(user_id))


def follow_week(user_id: int) -> None:
    """The weekly budget just changed: today goes back to following it (weekly ÷ 7), so Today
    and Week never show two unrelated budgets (r3 one budget model)."""
    init_schema()
    with db.cursor() as cur:
        cur.execute("DELETE FROM today_budget WHERE user_id=? AND day=?", (user_id, _today()))


def set_budget(user_id: int, amount) -> dict:
    _user(user_id)
    a = _num(amount, 50, 20000, "Today's budget")
    if a is None:
        raise ValueError("Enter today's budget")
    init_schema()
    with db.cursor() as cur:
        cur.execute("INSERT OR REPLACE INTO today_budget(user_id, day, amount) VALUES (?,?,?)",
                    (user_id, _today(), round(a)))
    return view(user_id)


# --------------------------------------------------------------------------- pools
def _swiggy_pool(user_id: int) -> dict:
    """{meal: [dish]} from domain/meal_pools (needs a connected Swiggy address)."""
    from .domain import meal_pools
    try:
        return meal_pools.view(user_id).get("meals") or {}
    except Exception:                      # not connected / Swiggy tables not there yet
        return {}


def _meta(user_id: int) -> dict:
    with db.cursor() as cur:
        return {(r["meal"], r["ref"]): dict(r) for r in
                cur.execute("SELECT * FROM pool_meta WHERE user_id=?", (user_id,)).fetchall()}


def _typed(user_id: int) -> list[dict]:
    with db.cursor() as cur:
        return [dict(r) for r in cur.execute("SELECT * FROM pool_typed WHERE user_id=? ORDER BY added_ts, id",
                                             (user_id,)).fetchall()]


def _w7_typed(user_id: int) -> list[dict]:
    """W7's typed favourites (domain/typed_in.py): the same kind of dish, typed in from the
    classic screens or before Swiggy; shown in the Today pools as one list."""
    try:
        with db.cursor() as cur:
            return [dict(r) for r in cur.execute(
                "SELECT id, meal, restaurant_name, item_name, price, platform FROM typed_favourites "
                "WHERE user_id=? ORDER BY id", (user_id,)).fetchall()]
    except Exception:                      # table not there (older database): nothing typed there
        return []


def _name_allergens(name: str, veg) -> list[str]:
    from .domain import live_catalog
    est = live_catalog.estimate(name, veg)
    return list(est["allergens"]) if est else []


def _hard_reason(user: dict, name: str, veg, allergens=None) -> str | None:
    """Diet + allergy, fail-closed: a dish not marked veg is not veg."""
    from .domain import allergens as allergen_rules
    item = {"name": name, "veg": veg if veg is not None else 0,
            "allergens": allergens if allergens is not None else _name_allergens(name, veg)}
    reason = allergen_rules.violates(user, item)
    # unmarked (typed, no veg answer) is "not marked", never "marked non-veg" (W1's wording for veg=0)
    if reason and veg is None and reason.lower().startswith(("not ", "marked non-veg")):
        return f"Not marked {user.get('diet')}"
    return cap(reason) if reason else None


def cap(s: str) -> str:
    return s[:1].upper() + s[1:] if s else s


def _soft_reasons(rules: dict, rating, eta) -> list[str]:
    out = []
    if rules["min_rating"] and rating is not None and rating < rules["min_rating"]:
        out.append(f"Rated {rating:g}★, below your {rules['min_rating']:g}★")
    if rules["max_eta"] and eta is not None and eta > rules["max_eta"]:
        out.append(f"{eta} min away, over your {rules['max_eta']} min")
    return out


def pools(user: dict, rules: dict | None = None) -> dict:
    """{meal: [entry]} — every dish the person put in a pool, each with ok + reasons."""
    from .domain import live_catalog
    uid = user["id"]
    rules = rules or settings(uid)
    meta = _meta(uid)
    live = live_catalog.has_live(uid)
    out = {m: [] for m in MEALS}
    for meal, dishes in _swiggy_pool(uid).items():
        for d in dishes:
            ref = f"sw:{d['restaurant_id']}:{d['item_id']}"
            m = meta.get((meal, ref), {})
            reasons = []
            hard = _hard_reason(user, d["name"], d.get("veg"))
            if hard:
                reasons.append(hard)
            if live and not d.get("on_menu"):
                reasons.append("Not on the menu for your address right now")
            reasons += _soft_reasons(rules, m.get("rating"), m.get("eta_min"))
            out[meal].append({"ref": ref, "source": "swiggy", "kind": "order", "name": d["name"],
                              "restaurant": d.get("restaurant_name"), "restaurant_id": d["restaurant_id"],
                              "item_id": d["item_id"], "price": d.get("price"), "veg": d.get("veg"),
                              "rating": m.get("rating"), "eta_min": m.get("eta_min"), "qty": m.get("qty") or 1,
                              "hard": bool(hard), "reasons": reasons})
    for t in _w7_typed(uid):            # typed in elsewhere (W7 "typed favourites", no platform needed)
        ref = f"tf:{t['id']}"
        m = meta.get((t["meal"], ref), {})
        hard = _hard_reason(user, t["item_name"], None)
        out[t["meal"]].append({"ref": ref, "source": "typed", "kind": "order", "name": t["item_name"],
                               "restaurant": t["restaurant_name"] or None, "price": t["price"], "veg": None,
                               "platform": t.get("platform"), "rating": None, "eta_min": None,
                               "qty": m.get("qty") or 1, "hard": bool(hard), "reasons": [hard] if hard else []})
    for t in _typed(uid):
        ref = f"t:{t['id']}"
        m = meta.get((t["meal"], ref), {})
        veg = None if t["veg"] is None else bool(t["veg"])
        hard = _hard_reason(user, t["name"], veg)
        out[t["meal"]].append({"ref": ref, "source": "typed", "kind": t["kind"], "name": t["name"],
                               "restaurant": t["restaurant_name"] or None, "price": t["price"], "veg": veg,
                               "rating": None, "eta_min": None, "qty": m.get("qty") or 1,
                               "hard": bool(hard), "reasons": [hard] if hard else []})
    return out


def add_typed(user_id: int, body: dict) -> dict:
    user = _user(user_id)
    meal = _meal(body.get("meal"))
    name = str(body.get("name") or "").strip()
    if not 2 <= len(name) <= 80:
        raise ValueError("Type the dish name (2–80 characters)")
    place = str(body.get("restaurant_name") or "").strip()[:80]
    kind = body.get("kind") or "order"
    if kind not in ("order", "cook"):
        raise ValueError("A dish is ordered or cooked")
    price = _num(body.get("price"), 1, 20000, "Price")
    veg = body.get("veg")
    if veg not in (True, False, None):
        raise ValueError("Say whether the dish is vegetarian")
    init_schema()
    del user                                 # only checked that the profile exists
    if sum(1 for t in _typed(user_id) if t["meal"] == meal) >= MAX_TYPED_PER_MEAL:
        raise ValueError(f"Your {meal} pool is full. Remove a dish first.")
    with db.cursor() as cur:
        cur.execute("INSERT INTO pool_typed(user_id, meal, name, restaurant_name, price, veg, kind, added_ts) "
                    "VALUES (?,?,?,?,?,?,?,?)", (user_id, meal, name, place, price,
                                                 None if veg is None else int(veg), kind,
                                                 clock.now().isoformat(timespec="microseconds")))
    return view(user_id)


def add_swiggy(user_id: int, body: dict) -> dict:
    """Add a dish from a restaurant's live Swiggy menu (domain/meal_pools checks it), and keep
    the rating / delivery time Swiggy showed for the place when it was picked."""
    from .domain import meal_pools
    meal = _meal(body.get("meal"))
    rid, iid = str(body.get("restaurant_id") or ""), str(body.get("item_id") or "")
    meal_pools.add(user_id, meal, rid, str(body.get("restaurant_name") or ""), iid)
    rating = _num(body.get("rating"), 0, 5, "Rating")
    eta = _num(body.get("eta_min"), 0, 300, "Delivery time")
    init_schema()
    with db.cursor() as cur:
        cur.execute("INSERT OR REPLACE INTO pool_meta(user_id, meal, ref, qty, rating, eta_min) VALUES (?,?,?,?,?,?)",
                    (user_id, meal, f"sw:{rid}:{iid}", _qty(body.get("qty") or _eaters(user_id)), rating,
                     None if eta is None else int(eta)))
    return view(user_id)


MAX_SUGGESTED_AT_ONCE = 15


def add_suggested(user_id: int, body: dict) -> dict:
    """Tick suggestions from the person's Swiggy order history (integrations/swiggy_history,
    read once with consent) into the pools. Only stored suggestions can be added. A dish still
    on the restaurant's live Swiggy menu goes in as a Swiggy dish (the shared safety check
    runs; a refusal is reported, never worked around); one that can't be read off the menu now
    goes in as the person's own typed dish (name and place only: no price or veg mark is
    invented, so a veg profile sees it greyed out until they say)."""
    from .domain import meal_pools
    from .integrations import swiggy_history
    from .integrations.swiggy_connect import SwiggyError
    _user(user_id)
    ticked = body.get("items")
    if not isinstance(ticked, list) or not ticked:
        raise ValueError("Tick at least one suggestion")
    if len(ticked) > MAX_SUGGESTED_AT_ONCE:
        raise ValueError(f"Add up to {MAX_SUGGESTED_AT_ONCE} at a time")
    stored = {(str(r["restaurant_id"]), r["item_name"].lower()): r for r in swiggy_history.view(user_id)["items"]}
    added, refused, typed = [], [], []
    for t in ticked:
        if not isinstance(t, dict):
            raise ValueError("Tick a suggestion from the list")
        r = stored.get((str(t.get("restaurant_id") or ""), str(t.get("item_name") or "").lower()))
        if not r:
            raise ValueError("That suggestion isn't in your list any more. Refresh it first.")
        meal = _meal(t.get("meal") or body.get("meal") or r["pool_meal"])
        name = r["item_name"]
        if r.get("menu_item_id"):
            try:
                meal_pools.add(user_id, meal, str(r["restaurant_id"]), r["restaurant_name"], str(r["menu_item_id"]))
                init_schema()
                with db.cursor() as cur:
                    cur.execute("INSERT OR IGNORE INTO pool_meta(user_id, meal, ref, qty, rating, eta_min) "
                                "VALUES (?,?,?,?,?,?)", (user_id, meal, f"sw:{r['restaurant_id']}:{r['menu_item_id']}",
                                                         1, r.get("rating"), None))
                added.append(name)
                continue
            except ValueError as e:              # safety / diet refusal or a full pool: say so, don't work around it
                refused.append(f"{name}: {e}")
                continue
            except (SwiggyError, Exception):      # not readable on the live menu now: keep it as typed
                pass
        try:
            add_typed(user_id, {"meal": meal, "name": name, "restaurant_name": r["restaurant_name"], "kind": "order"})
            typed.append(name)
        except ValueError as e:
            refused.append(f"{name}: {e}")
    out = view(user_id)
    out["suggested_added"] = {"swiggy": added, "typed": typed, "refused": refused}
    return out


def remove(user_id: int, meal, ref: str) -> dict:
    from .domain import meal_pools
    meal = _meal(meal)
    ref = str(ref or "")
    init_schema()
    if ref.startswith("sw:"):
        _, rid, iid = ref.split(":", 2)
        meal_pools.remove(user_id, meal, rid, iid)
    elif ref.startswith("t:") and ref[2:].isdigit():
        with db.cursor() as cur:
            cur.execute("DELETE FROM pool_typed WHERE user_id=? AND id=? AND meal=?", (user_id, int(ref[2:]), meal))
    elif ref.startswith("tf:") and ref[3:].isdigit():
        with db.cursor() as cur:
            cur.execute("DELETE FROM typed_favourites WHERE user_id=? AND id=? AND meal=?",
                        (user_id, int(ref[3:]), meal))
    else:
        raise ValueError("That dish isn't in your pool")
    with db.cursor() as cur:
        cur.execute("DELETE FROM pool_meta WHERE user_id=? AND meal=? AND ref=?", (user_id, meal, ref))
        cur.execute("DELETE FROM today_picks WHERE user_id=? AND day=? AND meal=? AND ref=?",
                    (user_id, _today(), meal, ref))
    return view(user_id)


def set_qty(user_id: int, meal, ref: str, qty) -> dict:
    meal, q = _meal(meal), _qty(qty)
    init_schema()
    with db.cursor() as cur:
        row = cur.execute("SELECT * FROM pool_meta WHERE user_id=? AND meal=? AND ref=?",
                          (user_id, meal, ref)).fetchone()
        if row:
            cur.execute("UPDATE pool_meta SET qty=? WHERE user_id=? AND meal=? AND ref=?", (q, user_id, meal, ref))
        else:
            cur.execute("INSERT INTO pool_meta(user_id, meal, ref, qty) VALUES (?,?,?,?)", (user_id, meal, ref, q))
        cur.execute("UPDATE today_picks SET qty=? WHERE user_id=? AND day=? AND meal=? AND ref=?",
                    (q, user_id, _today(), meal, ref))
    return view(user_id)


# --------------------------------------------------------------------------- picks
def _picks(user_id: int) -> dict:
    with db.cursor() as cur:
        rows = cur.execute("SELECT * FROM today_picks WHERE user_id=? AND day=?", (user_id, _today())).fetchall()
    return {r["meal"]: {**json.loads(r["payload"]), "qty": r["qty"], "how": r["how"]} for r in rows}


def _save_pick(cur, user_id: int, meal: str, entry: dict, qty: int, how: str) -> None:
    keep = {k: entry.get(k) for k in ("ref", "source", "kind", "name", "restaurant", "restaurant_id", "item_id",
                                      "price", "veg", "rating", "eta_min", "recipe_key", "price_basis")}
    cur.execute("INSERT OR REPLACE INTO today_picks(user_id, day, meal, ref, qty, payload, how) VALUES (?,?,?,?,?,?,?)",
                (user_id, _today(), meal, entry["ref"], qty, json.dumps(keep), how))


def pick(user_id: int, meal, ref, qty=None) -> dict:
    user = _user(user_id)
    meal = _meal(meal)
    init_schema()
    if not ref:
        with db.cursor() as cur:
            cur.execute("DELETE FROM today_picks WHERE user_id=? AND day=? AND meal=?", (user_id, _today(), meal))
        return view(user_id)
    entry = next((e for e in pools(user)[meal] if e["ref"] == ref), None)
    if not entry:
        raise ValueError("Pick a dish from your pool for this meal")
    if entry["hard"]:
        raise ValueError(f"{entry['name']} breaks your food rules: {entry['reasons'][0]}")
    with db.cursor() as cur:
        _save_pick(cur, user_id, meal, entry, _qty(qty or entry["qty"]), "you")
    return view(user_id)


def _cost(e: dict, qty: int) -> float:
    return (e.get("price") or 0) * qty


# Money on Today counts what a pick really costs (founder decision 14 Oct 2026): a Swiggy pick
# at its whole bill — dish + delivery + platform fee + packaging + GST, estimated from the
# person's own bills (domain/fees) — and at the real Swiggy cart bill once its cart is
# filled. Typed dishes and home cooking count at the price they carry (no fee is known).
def record_cart_bill(user_id: int, restaurant_id, item_id, qty, to_pay) -> None:
    """Keep the real Swiggy cart bill for today's pick of this dish (replaces the estimate)."""
    if to_pay is None or not restaurant_id or not item_id:
        return
    init_schema()
    with db.cursor() as cur:
        cur.execute("INSERT OR REPLACE INTO today_cart_bills(user_id, day, restaurant_id, item_id, qty, to_pay) "
                    "VALUES (?,?,?,?,?,?)", (user_id, _today(), str(restaurant_id), str(item_id), int(qty or 1),
                                             round(float(to_pay), 2)))


def _cart_bills(user_id: int) -> dict:
    init_schema()
    with db.cursor() as cur:
        rows = cur.execute("SELECT restaurant_id, item_id, qty, to_pay FROM today_cart_bills WHERE user_id=? AND day=?",
                           (user_id, _today())).fetchall()
    return {(r["restaurant_id"], r["item_id"], int(r["qty"])): float(r["to_pay"]) for r in rows}


def _fee_rule(user_id: int):
    from .domain import fees
    try:
        m = fees.model(user_id)
    except Exception:
        m = {"restaurants": {}, "user": None}
    return lambda rid: fees.pricing_for(rid, m)


def _total(p: dict, qty: int, bills: dict, rule) -> tuple[float | None, str]:
    """(what this pick costs in all, basis): "swiggy_cart" | "estimate" | "price"."""
    if p.get("price") is None:
        return None, "unknown"
    if p.get("source") == "swiggy" and p.get("kind", "order") == "order":
        real = bills.get((str(p.get("restaurant_id")), str(p.get("item_id")), int(qty)))
        if real is not None:
            return real, "swiggy_cart"
        from .domain import fees
        return fees.breakdown({"fees": rule(p.get("restaurant_id"))}, _cost(p, qty))["total"], "estimate"
    return round(_cost(p, qty), 2), "price"


# "Decide for me" runs on the one engine, kernel/decide.py (W6): the same modes, rules and
# objective as /api/user/<id>/decide/today and /api/plan/<id>/decide. This module only
# turns Today's own data (the pools, today's budget, the min-rating / max-time rules) into
# that engine's inputs and saves its answer as today's picks.
ENGINE_MODE = {"favourites": "favourites_only", "favourites_cook": "favourites_plus_cook",
               "favourites_new": "favourites_plus_new", "surprise": "surprise_me"}
UI_MODE = {v: k for k, v in ENGINE_MODE.items()}


def _engine_inputs(user: dict, rules: dict, mode: str, pool: dict) -> tuple[list, dict, dict]:
    """(menu, {meal: pooled dish ids}, {dish id: Today entry}) for the engine.

    The person's pool dishes that pass every rule are the favourites (tier "pool"). Typed
    dishes are labelled theirs, priced as they typed them, with no rating or delivery time
    (unknown stays unknown). With a live Swiggy menu, "+ new places" and "surprise" may add
    other dishes from it; a pool dish greyed out by a rule never comes back that way."""
    from .domain import live_catalog
    menu, pool_ids, entries, greyed = [], {m: set() for m in MEALS}, {}, set()
    for meal in MEALS:
        for e in pool[meal]:
            if e["reasons"] or e.get("price") is None or e["kind"] != "order":
                greyed.add(e["ref"])
                continue
            pool_ids[meal].add(e["ref"])
            if e["ref"] in entries:
                continue
            entries[e["ref"]] = e
            menu.append({"id": e["ref"], "name": e["name"], "price": float(e["price"]),
                         "veg": 1 if e.get("veg") else 0,
                         "allergens": _name_allergens(e["name"], e.get("veg")),
                         "restaurant_id": e.get("restaurant_id") or f"typed:{e.get('restaurant') or ''}",
                         "restaurant_name": e.get("restaurant") or "Your own dish",
                         "restaurant_rating": e.get("rating"), "eta_min": e.get("eta_min"),
                         "delivery_fee": 0.0, "tags": [], "qty": e.get("qty") or 1})
    greyed -= set(entries)
    if mode in ("favourites_plus_new", "surprise_me") and live_catalog.has_live(user["id"]):
        from .kernel import decide as engine
        for it in engine.gather(user)["menu"]:
            ref = f"sw:{it.get('restaurant_provider_id')}:{it.get('provider_item_id')}"
            if ref in entries or ref in greyed or not it.get("provider_item_id"):
                continue
            entries[ref] = {"ref": ref, "source": "swiggy", "kind": "order", "name": it["name"],
                            "restaurant": it.get("restaurant_name"),
                            "restaurant_id": str(it.get("restaurant_provider_id")),
                            "item_id": str(it["provider_item_id"]), "price": it["price"],
                            "veg": bool(it.get("veg")), "rating": it.get("restaurant_rating"), "qty": 1,
                            "new": True}
            menu.append({**it, "id": ref, "eta_min": None, "delivery_fee": 0.0})   # dish prices, like the pool
    return menu, pool_ids, entries


def _cook_for(user: dict, pool: dict):
    """The person's own cook dish for the meal (typed in), else the cheapest safe recipe."""
    from .domain import reverse_mode

    def cook(meal):
        mine = next((e for e in pool[meal] if e["kind"] == "cook" and not e["reasons"]
                     and e.get("price") is not None), None)
        if mine:
            return {"name": mine["name"], "key": mine["ref"], "cost": mine["price"], "_entry": mine}
        return reverse_mode.cook_candidate(user, meal)
    return cook


def decide(user_id: int, mode: str) -> dict:
    """Fill today's open meals with kernel/decide.py, inside today's budget left and the
    person's Today rules. Objective (docs/planner-objective.md): safe > within the money >
    favourites (what the mode allows) > no silent skips > cheapest among equally liked >
    variety. "surprise" is seeded (same day + person → same answer; ask again → next)."""
    from .kernel import decide as engine
    engine_mode = ENGINE_MODE.get(mode) or (mode if mode in engine.MODES else None)
    if not engine_mode:
        raise ValueError("Choose how Ziggy should decide")
    ui_mode = UI_MODE[engine_mode]
    user = _user(user_id)
    rules = settings(user_id)
    meals = meals_today(user)
    picks = _picks(user_id)
    open_meals = [m for m in meals if m not in picks]
    if not open_meals:
        return {**view(user_id), "decided": {"mode": ui_mode, "filled": [],
                                             "note": "Every meal today is already picked."}}
    budget = budget_for(user)["amount"]
    bills, rule = _cart_bills(user_id), _fee_rule(user_id)
    # today's money left after what is picked, fees included (real cart bill once carted)
    left = max(0.0, budget - sum(_total(p, p["qty"], bills, rule)[0] or 0 for m, p in picks.items() if m in meals))
    pool = pools(user, rules)
    menu, pool_ids, entries = _engine_inputs(user, rules, engine_mode, pool)
    for it in menu:
        # a Swiggy dish is weighed at its whole bill: its delivery, platform fee, packaging and
        # GST ride on "delivery_fee" (the engine adds it once per order)
        e = entries.get(it["id"]) or {}
        if e.get("source") == "swiggy" and it.get("price") is not None:
            q = int(it.get("qty") or e.get("qty") or 1)
            total, _ = _total({**e, "price": it["price"], "kind": "order"}, q, {}, rule)
            it["delivery_fee"] = round(max(0.0, total - it["price"] * q), 2)
    seed = f"{user_id}:{_today()}"
    if engine_mode == "surprise_me":
        with db.cursor() as cur:
            n = cur.execute("SELECT COUNT(*) AS n FROM today_picks WHERE user_id=? AND how=?",
                            (user_id, "surprise")).fetchone()["n"]
        seed += f":{n}"
    eng_rules = {**engine.rules_for(user), "min_rating": rules["min_rating"] or 0, "max_eta": rules["max_eta"] or None,
                 "daily_cap": None,
                 # choosing "+ cook at home" for today is the person asking to cook today
                 "cook_cap": 99}
    me = {k: user.get(k) for k in ("id", "name", "diet", "allergens", "medical")}
    day = clock.today().isoformat()
    slots = [{"day": day, "meal": m, "eaters": [me]} for m in open_meals]
    from .domain import taste
    try:
        sig = taste.signals(user_id)
    except Exception:
        sig = {"history": {}, "liked": set(), "disliked": set()}
    cook_for = _cook_for(user, pool)
    cooks = {}

    def remember_cook(meal):
        c = cook_for(meal)
        cooks[meal] = c
        return c
    out = engine.plan(slots, menu, mode=engine_mode, people=[me], places=set(), pools=pool_ids, money=left,
                      rules=eng_rules, history=sig.get("history"), liked=sig.get("liked") or set(),
                      disliked=sig.get("disliked") or set(), cook_for=remember_cook, seed=seed,
                      money_word="today's", method="exact" if len(slots) <= engine.EXACT_MAX_SLOTS else "auto")
    filled, missing, reasons, why = [], [], {}, {}
    how = "surprise" if engine_mode == "surprise_me" else "ziggy"
    with db.cursor() as cur:
        for p in out["picks"]:
            meal = p["meal"]
            if p["kind"] == "delivery":
                e = entries[p["item_id"]]
                qty = (p.get("lines") or [{}])[0].get("qty") or e.get("qty") or 1
            elif p["kind"] == "cook":
                c = cooks.get(meal) or {}
                e = c.get("_entry") or {"ref": f"cook:{p.get('recipe_key')}", "source": "cook", "kind": "cook",
                                         "name": p["name"], "recipe_key": p.get("recipe_key"), "price": p["cost"],
                                         "price_basis": "grocery estimate", "rating": None}
                qty = 1
            else:
                missing.append(meal)
                reasons[meal] = (p.get("reason") or "").removeprefix("Skipped: ")
                continue
            _save_pick(cur, user_id, meal, e, _qty(qty), how)
            filled.append(meal)
            why[meal] = p.get("why")
    note = None
    if missing:
        note = (f"Couldn't fill {' and '.join(missing)}: "
                + " ".join(reasons[m][:1].upper() + reasons[m][1:] for m in missing)
                + " Add dishes to the pool or change today's budget.")
    return {**view(user_id), "decided": {"mode": ui_mode, "engine_mode": engine_mode, "label": MODES[ui_mode],
                                         "filled": filled, "missing": missing, "reasons": reasons, "why": why,
                                         "note": note, "spent": out["spent"], "money": out["money"]}}


# --------------------------------------------------------------------------- read model
def _weather(user: dict) -> dict:
    from . import service
    from .domain import weather
    try:
        plan = service.current_plan(user["id"])["plan"]
        start = dt.date.fromisoformat(plan["week_start"])
        idx = (clock.today() - start).days
        w = weather.week(user["city"], plan["week_start"])[idx] if 0 <= idx < 7 else None
    except Exception:
        w = None
    if not w or w.get("source") == "none" or w.get("temp_c") is None:
        return {"known": False}
    # Where and from whom (live-audit-1): the forecast is for the profile's city, not the
    # person's GPS or IP, and it comes from Open-Meteo; the card says both.
    return {"known": True, "condition": w["condition"], "temp_c": w["temp_c"], "rain_prob": w.get("rain_prob"),
            "source": w.get("source"), "note": weather.note(w["condition"]),
            "place": user.get("city"), "attribution": "Open-Meteo" if w.get("source") == "live" else None,
            "attribution_url": "https://open-meteo.com/" if w.get("source") == "live" else None}


def _account_saved(user_id: int) -> bool:
    with db.cursor() as cur:
        try:
            return cur.execute("SELECT 1 FROM logins WHERE user_id=?", (user_id,)).fetchone() is not None
        except Exception:
            return False


def share_text(user: dict, meals: list[str], picks: dict, budget: float) -> str:
    lines = [f"Today's food plan ({clock.today().strftime('%a %d %b')})"]
    for m in meals:
        p = picks.get(m)
        if not p:
            continue
        where = f" from {p['restaurant']}" if p.get("restaurant") else (" (cook at home)" if p["kind"] == "cook" else "")
        price = f" · ₹{_cost(p, p['qty']):,.0f}" if p.get("price") is not None else ""
        lines.append(f"{m.capitalize()}: {p['qty']}× {p['name']}{where}{price}")
    if len(lines) == 1:
        lines.append("Nothing picked yet.")
    lines.append(f"Budget today: ₹{budget:,.0f}")
    return "\n".join(lines)


def _bills(user_id: int, priced: list[dict]) -> None:
    """W4's per-order bill for each Swiggy pick (domain/fees): delivery + platform fee +
    packaging + GST on the dish total, learned from the person's real bills at this address,
    else the research default, labelled an estimate with its range. Never for typed dishes."""
    from .domain import fees
    try:
        m = fees.model(user_id)
    except Exception:
        return
    for p in priced:
        rule = fees.pricing_for(p.get("restaurant_id"), m)
        p["bill"] = fees.breakdown({"fees": rule}, _cost(p, p["qty"]))


def _real_bill(p: dict) -> dict:
    """A carted Swiggy pick's bill as Swiggy's cart gave it (`to_pay`, today_cart_bills): the
    dish total and everything else on the bill as one line, never split into guessed parts."""
    items, real = round(_cost(p, p["qty"]), 2), round(float(p["total"]), 2)
    other = round(real - items, 2)
    return {"items": items, "delivery": None, "fees": None, "gst": None, "other": other,
            "total": real, "estimated": False, "range": [real, real],
            "basis": {"delivery": "swiggy_cart", "fees": "swiggy_cart"},
            "label": f"₹{real:.0f} incl. ₹{other:.0f} delivery, fees & GST (Swiggy's cart bill)"}


def _recap(user: dict) -> dict | None:
    """On Sunday: W4's week recap (domain/spend.week_recap), real Swiggy bills only."""
    if clock.today().weekday() != 6:
        return None
    from .domain import spend
    try:
        return spend.week_recap(user)
    except Exception:
        return None


def view(user_id: int) -> dict:
    from .integrations import swiggy_connect
    init_schema()
    user = _user(user_id)
    rules = settings(user_id)
    meals = meals_today(user)
    b = budget_for(user)
    pool = pools(user, rules)
    picks = _picks(user_id)
    for meal, p in picks.items():           # a pick whose pool dish now breaks a rule says so
        now = next((e for e in pool.get(meal, []) if e["ref"] == p["ref"]), None)
        p["reasons"] = now["reasons"] if now else []
    bills, rule = _cart_bills(user_id), _fee_rule(user_id)
    for m, p in picks.items():
        p["total"], p["total_basis"] = _total(p, p["qty"], bills, rule)
    dishes = sum(_cost(p, p["qty"]) for m, p in picks.items() if m in meals)
    # "left" subtracts fees (estimated from learned bills; the real cart bill once carted)
    picked = sum(p["total"] or 0 for m, p in picks.items() if m in meals)
    unknown = [m for m, p in picks.items() if m in meals and p.get("price") is None]
    try:
        conn = swiggy_connect.status(user_id)
    except Exception:
        conn = {"connected": False}
    priced = [p for m, p in picks.items() if m in meals and p["kind"] == "order" and p.get("source") == "swiggy"
              and p.get("price") is not None]
    _bills(user_id, priced)
    for p in priced:                        # a carted pick shows Swiggy's real bill, not the estimate
        if p.get("total_basis") == "swiggy_cart":
            p["bill"] = _real_bill(p)
    # The whole bill and "left" are one sum: every pick at p["total"] (a Swiggy pick at its real
    # cart bill once carted, else the fee estimate; any other priced pick at its price), so the
    # card's "₹X with fees" and "₹Y left after fees" always add up to today's budget.
    bill_total = round(picked, 2) if priced else None
    compare = None
    if priced:
        compare = {"swiggy": round(sum(_cost(p, p["qty"]) for p in priced), 2), "others": "coming",
                   "basis": "Dish prices on Swiggy's menu; fees, taxes and delivery show in the Swiggy cart."}
    # The v3 budget card's "₹X left after fees" (estimate | real bill): only with a Swiggy pick;
    # without one there are no fees and the plain "left" is already the whole story.
    swiggy_bases = [p["total_basis"] for m, p in picks.items()
                    if m in meals and p.get("total_basis") in ("estimate", "swiggy_cart")]
    left = round(b["amount"] - picked, 2)
    return {
        "date": _today(), "weekday": clock.today().strftime("%A"),
        "meals": meals, "label": "+".join(LETTER[m] for m in meals),
        "budget": {**b, "presets": presets_for(b.get("default")), "picked": round(picked, 2),
                   "picked_dishes": round(dishes, 2),
                   "left": left, "unpriced": unknown,
                   "left_after_fees": left if swiggy_bases else None,
                   "left_estimated": any(x == "estimate" for x in swiggy_bases) if swiggy_bases else None,
                   "fees_included": True,
                   "left_basis": ("Fees included: Swiggy picks at their whole bill (an estimate from your bills "
                                  "until the Swiggy cart shows the real one)."),
                   # W4: what the Swiggy picks cost with delivery, platform fee, packaging and GST
                   "picked_bill": bill_total,
                   "bill_estimated": any(p.get("total_basis") == "estimate" for p in priced)},
        "recap": _recap(user),
        "weather": _weather(user),
        "rules": {"diet": user.get("diet"), "allergens": user.get("allergens") or [],
                  "medical": user.get("medical") or [], **rules},
        "pools": pool, "picks": {m: picks[m] for m in meals if m in picks},
        "modes": MODES, "price_compare": compare,
        "swiggy": {"connected": bool(conn.get("connected")), "address": (conn.get("address") or {}).get("label")},
        "account_saved": _account_saved(user_id),
        "share_text": share_text(user, meals, picks, b["amount"]),
    }
