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
import hashlib
import itertools
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
    if reason and reason.startswith("not ") and veg is None:
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
                    (user_id, meal, f"sw:{rid}:{iid}", _qty(body.get("qty") or 1), rating,
                     None if eta is None else int(eta)))
    return view(user_id)


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


def _candidates(user: dict, rules: dict, mode: str, pool: dict) -> dict:
    """{meal: [entry]} that pass every hard and soft rule, for one Decide-for-me mode."""
    from .domain import live_catalog, reverse_mode
    out = {}
    for meal in MEALS:
        cands = [dict(e, fav=True) for e in pool[meal] if not e["reasons"] and e.get("price") is not None
                 and e["kind"] == "order"]
        if mode in ("favourites_cook", "surprise"):
            cands += [dict(e, fav=True) for e in pool[meal] if not e["reasons"] and e["kind"] == "cook"
                      and e.get("price") is not None]
            for r in reverse_mode.safe_recipes(user, meal)[:6]:
                cands.append({"ref": f"cook:{r['key']}", "source": "cook", "kind": "cook", "name": r["name"],
                              "recipe_key": r["key"], "price": r["cost"], "qty": 1, "fav": False,
                              "price_basis": "grocery estimate", "rating": None})
        if mode in ("favourites_new", "surprise") and live_catalog.has_live(user["id"]):
            from .domain import allergens as allergen_rules
            have = {e.get("item_id") for e in cands}
            with db.cursor() as cur:
                rows = cur.execute(
                    "SELECT m.name, m.price, m.veg, m.allergens, m.provider_item_id, r.provider_id, r.name AS rname, "
                    "r.rating FROM menu_items m JOIN restaurants r ON r.id=m.restaurant_id "
                    "WHERE r.city=? AND r.rating>=? ORDER BY r.rating DESC, m.price, m.name",
                    (live_catalog.city_key(user["id"]), rules["min_rating"] or 0)).fetchall()
            added = 0
            for r in rows:
                if added >= NEW_PLACES_PER_MEAL or str(r["provider_item_id"]) in have:
                    continue
                item = {"name": r["name"], "veg": r["veg"], "allergens": db.jl(r["allergens"])}
                if allergen_rules.violates(user, item):
                    continue
                cands.append({"ref": f"sw:{r['provider_id']}:{r['provider_item_id']}", "source": "swiggy",
                              "kind": "order", "name": r["name"], "restaurant": r["rname"],
                              "restaurant_id": str(r["provider_id"]), "item_id": str(r["provider_item_id"]),
                              "price": r["price"], "veg": bool(r["veg"]), "rating": r["rating"], "qty": 1,
                              "fav": False, "new": True})
                added += 1
        out[meal] = cands
    return out


def _recent_refs(user_id: int) -> set:
    since = (clock.today() - dt.timedelta(days=2)).isoformat()
    with db.cursor() as cur:
        return {r["ref"] for r in cur.execute("SELECT ref FROM today_picks WHERE user_id=? AND day>=? AND day<?",
                                              (user_id, since, _today())).fetchall()}


def decide(user_id: int, mode: str) -> dict:
    """Fill today's open meals from candidates that pass every rule and fit the budget left.
    Objective, in order: fill as many meals as possible; prefer favourites; prefer higher-rated;
    avoid what was picked in the last two days; then spend less. "surprise" replaces the
    preference order with a seeded shuffle (same day + person → same answer; ask again → next)."""
    if mode not in MODES:
        raise ValueError("Choose how Ziggy should decide")
    user = _user(user_id)
    rules = settings(user_id)
    meals = meals_today(user)
    picks = _picks(user_id)
    open_meals = [m for m in meals if m not in picks]
    if not open_meals:
        return {**view(user_id), "decided": {"mode": mode, "filled": [], "note": "Every meal today is already picked."}}
    budget = budget_for(user)["amount"]
    left = budget - sum(_cost(p, p["qty"]) for p in picks.values())
    cands = _candidates(user, rules, mode, pools(user, rules))
    recent = _recent_refs(user_id)
    seed = None
    if mode == "surprise":
        with db.cursor() as cur:
            n = cur.execute("SELECT COUNT(*) AS n FROM today_picks WHERE user_id=? AND how=?",
                            (user_id, "surprise")).fetchone()["n"]
        seed = f"{user_id}:{_today()}:{n}"

    def score(e, meal):
        if seed:
            return int(hashlib.sha256(f"{seed}:{meal}:{e['ref']}".encode()).hexdigest()[:8], 16) / 16 ** 8
        return (2.0 if e.get("fav") else 0) + (e.get("rating") or 0) / 5 - (1.0 if e["ref"] in recent else 0)

    options = [[None] + sorted(cands[m], key=lambda e: (-score(e, m), _cost(e, e["qty"]), e["name"]))[:15]
               for m in open_meals]
    best, best_key = None, None
    for combo in itertools.product(*options):
        spend = sum(_cost(e, e["qty"]) for e in combo if e)
        if spend > left + 1e-9:
            continue
        key = (sum(1 for e in combo if e), round(sum(score(e, m) for e, m in zip(combo, open_meals) if e), 6),
               -spend)
        if best_key is None or key > best_key:
            best, best_key = combo, key
    filled, missing = [], []
    with db.cursor() as cur:
        for meal, e in zip(open_meals, best or [None] * len(open_meals)):
            if e:
                _save_pick(cur, user_id, meal, e, e["qty"], "surprise" if mode == "surprise" else "ziggy")
                filled.append(meal)
            else:
                missing.append(meal)
    note = None
    if missing:
        why = ("nothing in your pool passes your rules" if not any(cands[m] for m in missing)
               else f"nothing left fits ₹{max(0, left):,.0f}")
        note = f"Couldn't fill {', '.join(missing)}: {why}. Add dishes to the pool or change today's budget."
    return {**view(user_id), "decided": {"mode": mode, "label": MODES[mode], "filled": filled, "missing": missing,
                                         "note": note}}


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
    return {"known": True, "condition": w["condition"], "temp_c": w["temp_c"], "rain_prob": w.get("rain_prob"),
            "source": w.get("source"), "note": weather.note(w["condition"])}


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
    picked = sum(_cost(p, p["qty"]) for m, p in picks.items() if m in meals)
    unknown = [m for m, p in picks.items() if m in meals and p.get("price") is None]
    try:
        conn = swiggy_connect.status(user_id)
    except Exception:
        conn = {"connected": False}
    priced = [p for m, p in picks.items() if m in meals and p["kind"] == "order" and p.get("source") == "swiggy"
              and p.get("price") is not None]
    compare = None
    if priced:
        compare = {"swiggy": round(sum(_cost(p, p["qty"]) for p in priced), 2), "others": "coming",
                   "basis": "Dish prices on Swiggy's menu; fees, taxes and delivery show in the Swiggy cart."}
    return {
        "date": _today(), "weekday": clock.today().strftime("%A"),
        "meals": meals, "label": "+".join(LETTER[m] for m in meals),
        "budget": {**b, "presets": list(BUDGET_PRESETS), "picked": round(picked, 2),
                   "left": round(b["amount"] - picked, 2), "unpriced": unknown},
        "weather": _weather(user),
        "rules": {"diet": user.get("diet"), "allergens": user.get("allergens") or [],
                  "medical": user.get("medical") or [], **rules},
        "pools": pool, "picks": {m: picks[m] for m in meals if m in picks},
        "modes": MODES, "price_compare": compare,
        "swiggy": {"connected": bool(conn.get("connected")), "address": (conn.get("address") or {}).get("label")},
        "account_saved": _account_saved(user_id),
        "share_text": share_text(user, meals, picks, b["amount"]),
    }
