"""Suggested favourites from the user's own Swiggy order history (grill #7, #17).

Day 0 should already know the user's usual places. Swiggy's `get_food_orders` returns the
most recent food orders (up to 15, newest first, across ALL saved addresses; the addressId
argument only resolves coordinates). Ziggy reads it ONCE, only when the user taps
"Suggest from my Swiggy orders" after seeing CONSENT, and turns it into suggestions:

  * one row per (restaurant, dish) with how many times it was ordered, the time-of-day
    bucket it is usually ordered in (breakfast / lunch / dinner / late), the last order
    date and, when Ziggy already knows it locally, the restaurant's rating;
  * a score = times ordered + recency (half-life 30 days) + a small rating bonus.

Data rules (Swiggy Builders Club data-and-compliance; founder answer #14, Oct 2026):
  * Swiggy is the data fiduciary; its data may only serve the user's current task. This is
    that task (seed my favourites), on the user's explicit tap. No analytics, no learning
    loop, no cross-user use: Ziggy's learning stays with what the user types in.
  * Stored fields are the minimum: restaurant id + name, dish name + menu item id, bucket,
    count, last order DATE, rating, score. No order ids, totals, addresses or item prices.
  * Nothing becomes a favourite until the user ticks it (the UI posts the suggestion to the
    existing pools / favourites endpoints). DELETE removes every suggestion at once.

Fields verified against https://mcp.swiggy.com/builders/docs/reference/food/get_food_orders.md
(14 Oct 2026): orders[].restaurantId, restaurantName, orderedItems (a string), orderedTime,
orderStatus, actions[].reorderMeta.orderItems[] {menu_item_id|item_id, name, quantity}.
The order rows carry no rating; a rating is used only when Ziggy already has it locally.
"""
from __future__ import annotations

import datetime as dt
import json
import re

from .. import clock, db
from . import swiggy_connect as sc
from .swiggy_connect import SwiggyError

ORDER_COUNT = 15                      # Swiggy's maximum for get_food_orders
HALF_LIFE_DAYS = 30.0
RATING_W = 0.5
CONSENT = ("Ziggy will read your last 15 Swiggy orders once, to suggest favourite places and dishes. "
           "It keeps only the restaurant, the dish names, how often and at what time of day you ordered "
           "them, and the date of the last order: no order numbers, totals or addresses. Nothing becomes "
           "a favourite until you tick it, and you can delete these suggestions at any time.")
CONSENT_SHORT = "Reads your last 15 Swiggy orders once, on this tap. Nothing is added until you tick it."
# Pool a bucket goes to when the user ticks it. Late-night orders stay dinner (Ziggy has no
# per-person dinner time yet); tea-time orders go to the optional snack list (r3 #6).
POOL_FOR = {"breakfast": "breakfast", "lunch": "lunch", "snack": "snack", "dinner": "dinner", "late": "dinner"}
POOLS = ("breakfast", "lunch", "snack", "dinner")
BUCKETS = ("breakfast", "lunch", "snack", "dinner", "late")
MAX_COMBOS = 12
_QTY = re.compile(r"^\s*(\d+)\s*[x×]\s*", re.I)
_LOOSE_TIME = re.compile(r"(\d{4}-\d{2}-\d{2})[ T]+(\d{1,2}):(\d{2})(?::\d{2})?\s*(AM|PM)?", re.I)
# Name hints, used only to break a tie between two meals (never to override the clock).
SNACK_NAMES = re.compile(r"\b(tea|chai|coffee|juice|shake|lassi|milkshake|samosa|bajji|bajji|bonda|puffs?|pakoras?|"
                         r"kesari|cakes?|pastry|pastries|brownie|ice ?cream|chaat|pani ?puri|bhel|sundal|cutlet|"
                         r"cookies?|biscuits?|donuts?|waffles?|smoothie|falooda|kulfi)\b", re.I)

# A drink goes with a meal: counted as a side (like Swiggy's "Beverages" section does).
DRINK_NAMES = re.compile(r"\b(tea|chai|coffee|juice|shake|lassi|buttermilk|chaas|soda|coke|pepsi|sprite|"
                         r"lime ?water|water|smoothie|mojito)\b", re.I)


def _when(text) -> dt.datetime | None:
    """`orderedTime` as IST wall time: the formats swiggy_live knows, then a loose
    "2026-09-30 5:30 PM IST (UTC+05:30)" reading (seen in the fakes; the schema only says string)."""
    from .swiggy_live import _order_time
    at = _order_time(text)
    if at is not None:
        return at
    m = _LOOSE_TIME.search(str(text or ""))
    if not m:
        return None
    hour, minute, ampm = int(m.group(2)), int(m.group(3)), (m.group(4) or "").upper()
    if ampm == "PM" and hour < 12:
        hour += 12
    if ampm == "AM" and hour == 12:
        hour = 0
    try:
        return dt.datetime.fromisoformat(m.group(1)).replace(hour=hour, minute=minute)
    except ValueError:
        return None


def bucket(at: dt.datetime) -> str:
    """Time-of-day bucket in IST: breakfast 5-11, lunch 11-15:30, snack 15:30-18:30,
    dinner 18:30-22, late 22-5."""
    m = at.hour * 60 + at.minute
    if 5 * 60 <= m < 11 * 60:
        return "breakfast"
    if 11 * 60 <= m < 15 * 60 + 30:
        return "lunch"
    if 15 * 60 + 30 <= m < 18 * 60 + 30:
        return "snack"
    if 18 * 60 + 30 <= m < 22 * 60:
        return "dinner"
    return "late"


def _qty(value) -> int:
    try:
        return max(1, min(8, int(value)))
    except (TypeError, ValueError):
        return 1


def _items(row: dict) -> list[dict]:
    """The dishes of one order with their quantity: the reorder list when Swiggy sends it
    (ids + names), else the `orderedItems` text split on commas ("2 x Masala Dosa, Filter Coffee")."""
    for action in row.get("actions") or []:
        meta = action.get("reorderMeta") if isinstance(action, dict) else None
        items = meta.get("orderItems") if isinstance(meta, dict) else None
        if isinstance(items, list) and items:
            out = []
            for it in items:
                if isinstance(it, dict) and str(it.get("name") or "").strip():
                    mid = it.get("menu_item_id") or it.get("item_id")
                    out.append({"name": str(it["name"]).strip()[:80], "menu_item_id": str(mid) if mid else None,
                                "qty": _qty(it.get("quantity") or 1)})
            if out:
                return out
    raw = row.get("orderedItems")
    if isinstance(raw, list):            # older shape seen in tests: a list of {name}
        names = [(str(i.get("name") or i.get("itemName") or ""), i.get("quantity") or 1)
                 for i in raw if isinstance(i, dict)]
    else:
        names = []
        for n in str(raw or "").split(","):
            m = _QTY.match(n)
            names.append((n, m.group(1) if m else 1))
    return [{"name": _QTY.sub("", n).strip()[:80], "menu_item_id": None, "qty": _qty(q)}
            for n, q in names if _QTY.sub("", n).strip()]


def norm(name) -> str:
    """A dish name for matching: lower case, letters and digits only."""
    return " ".join(re.sub(r"[^a-z0-9]+", " ", str(name or "").lower()).split())


def _local_menu(user_id: int) -> dict[tuple[str, str], dict]:
    """(Swiggy restaurant id, normalised dish name) -> {item_id, tags} from the user's live
    catalogue (no Swiggy call). Empty when nothing has been read for this address."""
    from ..domain import live_catalog
    try:
        with db.cursor() as cur:
            rows = cur.execute("SELECT r.provider_id, m.name, m.provider_item_id, m.tags FROM menu_items m "
                               "JOIN restaurants r ON r.id=m.restaurant_id WHERE r.city=? AND r.provider_id IS NOT NULL",
                               (live_catalog.city_key(user_id),)).fetchall()
    except Exception:                    # older database without the live columns
        return {}
    out = {}
    for r in rows:
        try:
            tags = json.loads(r["tags"] or "[]")
        except (TypeError, ValueError):
            tags = []
        out[(str(r["provider_id"]), norm(r["name"]))] = {"item_id": str(r["provider_item_id"]) if r["provider_item_id"] else None,
                                                         "tags": tags}
    return out


def _is_side(name: str, tags=()) -> bool:
    from ..domain import live_catalog
    return "side" in live_catalog.section_tags(tags, [], name) or bool(DRINK_NAMES.search(name or ""))


def _hint(name: str, tags=()) -> str | None:
    """A meal the dish's menu section or name points at: only a tie-breaker."""
    if "breakfast" in (tags or ()):
        return "breakfast"
    if SNACK_NAMES.search(name or ""):
        return "snack"
    return None


def _slots(buckets: dict, hint: str | None) -> tuple[str, list[str]]:
    """(bucket label, pool meals best first) from per-bucket counts. Late counts as dinner.
    A tie goes to the hinted meal, else the earlier one. A second meal is kept when it was
    ordered at least half as often as the first (one dish can be both lunch and dinner)."""
    pools = {p: 0 for p in POOLS}
    for b, n in buckets.items():
        pools[POOL_FOR[b]] += n
    rank = sorted(POOLS, key=lambda p: (-pools[p], 0 if p == hint else 1, POOLS.index(p)))
    first = rank[0]
    out = [first]
    second = rank[1]
    if pools[second] and pools[second] * 2 >= pools[first]:
        out.append(second)
    label = "late" if first == "dinner" and buckets.get("late", 0) > buckets.get("dinner", 0) else first
    return label, out


def _known_ratings(user_id: int) -> dict[str, float]:
    """Ratings Ziggy already holds for this user's live restaurants (no extra Swiggy calls)."""
    from ..domain import live_catalog
    with db.cursor() as cur:
        rows = cur.execute("SELECT provider_id, rating FROM restaurants WHERE city=? AND provider_id IS NOT NULL",
                           (live_catalog.city_key(user_id),)).fetchall()
    return {str(r["provider_id"]): float(r["rating"]) for r in rows if r["rating"]}


def _orders(orders: list):
    """(row, time) for each delivered order with a readable time."""
    for row in orders:
        if not isinstance(row, dict) or not row.get("restaurantId") or not row.get("restaurantName"):
            continue
        if "CANCEL" in str(row.get("orderStatus") or "").upper():
            continue
        at = _when(row.get("orderedTime"))
        if at is not None:
            yield row, at


def score_orders(orders: list, *, now: dt.datetime, ratings: dict | None = None,
                 menu: dict | None = None) -> list[dict]:
    """Pure: order rows -> suggestions, best first. Cancelled orders and unreadable times
    are skipped (a guess about when someone eats is worse than no suggestion). `menu` is
    _local_menu(): a dish id when Swiggy sent none, and section tags (side, breakfast)."""
    ratings, menu = ratings or {}, menu or {}
    acc: dict[tuple, dict] = {}
    for row, at in _orders(orders):
        age = max(0.0, (now - at).total_seconds() / 86400)
        rid = str(row["restaurantId"])
        items = _items(row)
        tags = {it["name"]: (menu.get((rid, norm(it["name"]))) or {}).get("tags") or [] for it in items}
        has_main = any(not _is_side(it["name"], tags[it["name"]]) for it in items)
        for it in items:
            key = (rid, it["name"].lower())
            local = menu.get((rid, norm(it["name"]))) or {}
            s = acc.setdefault(key, {"restaurant_id": rid, "restaurant_name": str(row["restaurantName"])[:80],
                                     "item_name": it["name"], "menu_item_id": it["menu_item_id"] or local.get("item_id"),
                                     "times": 0, "recency": 0.0, "buckets": {}, "last": at, "qtys": {},
                                     "side": _is_side(it["name"], tags[it["name"]]), "alone": 0,
                                     "hint": _hint(it["name"], tags[it["name"]])})
            s["times"] += 1
            s["recency"] += 0.5 ** (age / HALF_LIFE_DAYS)
            b = bucket(at)
            s["buckets"][b] = s["buckets"].get(b, 0) + 1
            s["qtys"][it["qty"]] = s["qtys"].get(it["qty"], 0) + 1
            s["last"] = max(s["last"], at)
            s["menu_item_id"] = s["menu_item_id"] or it["menu_item_id"]
            if not has_main:
                s["alone"] += 1
    out = []
    for s in acc.values():
        slot, slots = _slots(s["buckets"], s["hint"])
        rating = ratings.get(s["restaurant_id"])
        bonus = RATING_W * min(1.5, max(0.0, rating - 3.5)) if rating else 0.0
        qty = max(s["qtys"], key=lambda q: (s["qtys"][q], -q))
        out.append({"restaurant_id": s["restaurant_id"], "restaurant_name": s["restaurant_name"],
                    "item_name": s["item_name"], "menu_item_id": s["menu_item_id"], "slot": slot,
                    "slots": slots, "qty": qty, "side": s["side"],
                    # a side only ever ordered with a main is suggested inside that order, not alone
                    "with_main_only": s["side"] and not s["alone"],
                    "times": s["times"], "last_date": s["last"].date().isoformat(), "rating": rating,
                    "score": round(s["times"] + s["recency"] + bonus, 4)})
    out.sort(key=lambda r: (-r["score"], r["restaurant_name"].lower(), r["item_name"].lower()))
    return out


def combo_label(items: list[dict]) -> str:
    """"Dal Makhani + 2 Butter Naan": mains first, then sides, quantities kept."""
    ordered = sorted(items, key=lambda i: (bool(i.get("side")), i["name"].lower()))
    return " + ".join(f"{i['qty']} {i['name']}" if i["qty"] > 1 else i["name"] for i in ordered)[:200]


def score_combos(orders: list, *, now: dt.datetime, menu: dict | None = None) -> list[dict]:
    """Pure: whole past orders of two or more dishes (with at least one main) as usual
    orders, with their quantities, best first."""
    menu = menu or {}
    acc: dict[tuple, dict] = {}
    for row, at in _orders(orders):
        rid = str(row["restaurantId"])
        items = []
        for it in _items(row):
            local = menu.get((rid, norm(it["name"]))) or {}
            items.append({"name": it["name"], "qty": it["qty"], "menu_item_id": it["menu_item_id"] or local.get("item_id"),
                          "side": _is_side(it["name"], local.get("tags") or [])})
        if len({i["name"].lower() for i in items}) < 2 or all(i["side"] for i in items):
            continue
        items.sort(key=lambda i: (i["side"], i["name"].lower()))
        label = combo_label(items)
        c = acc.setdefault((rid, label.lower()), {"restaurant_id": rid, "restaurant_name": str(row["restaurantName"])[:80],
                                                  "label": label, "items": items, "times": 0, "recency": 0.0,
                                                  "buckets": {}, "last": at})
        c["times"] += 1
        c["recency"] += 0.5 ** (max(0.0, (now - at).total_seconds() / 86400) / HALF_LIFE_DAYS)
        b = bucket(at)
        c["buckets"][b] = c["buckets"].get(b, 0) + 1
        c["last"] = max(c["last"], at)
        for old, new in zip(c["items"], items):
            old["menu_item_id"] = old["menu_item_id"] or new["menu_item_id"]
    out = []
    for c in acc.values():
        mains = [i["name"] for i in c["items"] if not i["side"]]
        slot, slots = _slots(c["buckets"], _hint(" ".join(mains)))
        out.append({"restaurant_id": c["restaurant_id"], "restaurant_name": c["restaurant_name"], "label": c["label"],
                    "items": c["items"], "slot": slot,
                    "slots": slots, "times": c["times"], "last_date": c["last"].date().isoformat(),
                    "score": round(c["times"] + c["recency"], 4)})
    out.sort(key=lambda r: (-r["score"], r["restaurant_name"].lower(), r["label"].lower()))
    return out[:MAX_COMBOS]


def fetch_orders(user_id: int) -> list:
    """Swiggy's recent food orders (read-only). Any saved address works for the call."""
    from . import swiggy_live
    conn = swiggy_live._conn(user_id)
    address_id = conn.get("address_id") or (swiggy_live.addresses(user_id) or [{}])[0].get("id")
    tool = swiggy_live._tool(conn, "get_food_orders")
    args = swiggy_live.build_args(tool, {"address": address_id})
    props = (tool.get("input_schema") or {}).get("properties") or {}
    if "orderCount" in props:
        args["orderCount"] = ORDER_COUNT
    data = swiggy_live.call(user_id, "get_food_orders", args)
    body = data.get("data", data) if isinstance(data, dict) else {}
    rows = body.get("orders") if isinstance(body, dict) else None
    if not isinstance(rows, list):
        raise SwiggyError("Swiggy did not return an order list. Try again later.")
    return rows


def _stored(user_id: int) -> list[dict]:
    sc.init_schema()
    with db.cursor() as cur:
        rows = cur.execute("SELECT restaurant_id, restaurant_name, item_name, menu_item_id, slot, slots, qty, side, "
                           "times, last_date, rating, score, imported_ts FROM swiggy_history_suggestions WHERE user_id=?",
                           (user_id,)).fetchall()
    out = []
    for r in rows:
        r = dict(r)
        r["pool_meals"] = [p for p in (r.pop("slots") or "").split(",") if p in POOLS] or [POOL_FOR[r["slot"]]]
        r["pool_meal"] = r["pool_meals"][0]
        r["qty"] = r["qty"] or 1
        r["with_main_only"] = r["side"] == 2          # 2: a side only ever ordered with a main
        r["side"] = bool(r["side"])
        out.append(r)
    out.sort(key=lambda r: (-r["score"], r["restaurant_name"].lower(), r["item_name"].lower()))
    return out


def _stored_combos(user_id: int) -> list[dict]:
    sc.init_schema()
    with db.cursor() as cur:
        rows = cur.execute("SELECT * FROM swiggy_history_combos WHERE user_id=?", (user_id,)).fetchall()
    out = []
    for r in rows:
        r = {k: r[k] for k in r.keys() if k != "user_id"}
        r["items"] = json.loads(r["items"] or "[]")
        r["pool_meals"] = [p for p in (r.pop("slots") or "").split(",") if p in POOLS] or [POOL_FOR[r["slot"]]]
        r["pool_meal"] = r["pool_meals"][0]
        out.append(r)
    out.sort(key=lambda r: (-r["score"], r["restaurant_name"].lower(), r["label"].lower()))
    return out


def _saved_places(user_id: int) -> set[str]:
    """Restaurant ids saved as favourite places for the current delivery address."""
    from . import swiggy_live
    try:
        return {f["id"] for f in swiggy_live.live_favourites(user_id)}
    except Exception:                    # not connected / no address yet
        return set()


def _restaurants(items: list[dict], saved: set | None = None) -> list[dict]:
    by: dict[str, dict] = {}
    for it in items:
        r = by.setdefault(it["restaurant_id"], {"restaurant_id": it["restaurant_id"],
                                                "restaurant_name": it["restaurant_name"], "times": 0,
                                                "score": 0.0, "slots": {}, "rating": it.get("rating"),
                                                "saved": it["restaurant_id"] in (saved or set())})
        # A place ranks by its best dish: one order of two dishes isn't two visits.
        r["times"] = max(r["times"], it["times"])
        r["score"] = max(r["score"], it["score"])
        r["rating"] = r["rating"] if r["rating"] is not None else it.get("rating")
        r["slots"][it["slot"]] = r["slots"].get(it["slot"], 0) + it["times"]
    out = list(by.values())
    out.sort(key=lambda r: (-r["score"], r["restaurant_name"].lower()))
    return out


def view(user_id: int) -> dict:
    items = _stored(user_id)
    combos = _stored_combos(user_id) if items else []
    by_slot = {b: [i for i in items if b in i["pool_meals"]] for b in POOLS}
    return {"consent": CONSENT, "consent_short": CONSENT_SHORT, "imported": bool(items),
            "imported_at": items[0]["imported_ts"] if items else None,
            "restaurants": _restaurants(items, _saved_places(user_id) if items else set()),
            "items": items, "combos": combos, "by_slot": by_slot,
            "attribution": "Powered by Swiggy" if items else None,
            "note": "Tick the ones you like to add them to your favourites. Nothing is added on its own."}


def import_history(user_id: int, consent) -> dict:
    """Read the order history (explicit tap + consent; again on "Refresh from my orders")
    and keep the minimal suggestions: dishes, whole orders as usual orders, no prices."""
    if consent is not True:
        raise ValueError("Tap 'Suggest from my Swiggy orders' and agree to the note first.")
    if not sc.private_owner(user_id):
        raise ValueError("Suggestions from Swiggy orders need your own private Ziggy profile.")
    orders, now, menu = fetch_orders(user_id), clock.now(), _local_menu(user_id)
    rows = score_orders(orders, now=now, ratings=_known_ratings(user_id), menu=menu)
    combos = score_combos(orders, now=now, menu=menu)
    old = {r["restaurant_id"]: r["rating"] for r in _stored(user_id) if r.get("rating") is not None}
    stamp = now.isoformat(timespec="minutes")
    sc.init_schema()
    with db.cursor() as cur:
        cur.execute("DELETE FROM swiggy_history_suggestions WHERE user_id=?", (user_id,))
        cur.execute("DELETE FROM swiggy_history_combos WHERE user_id=?", (user_id,))
        for r in rows:
            cur.execute("INSERT INTO swiggy_history_suggestions(user_id, restaurant_id, restaurant_name, item_name, "
                        "menu_item_id, slot, slots, qty, side, times, last_date, rating, score, imported_ts) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (user_id, r["restaurant_id"], r["restaurant_name"], r["item_name"], r["menu_item_id"],
                         r["slot"], ",".join(r["slots"]), r["qty"], 2 if r["with_main_only"] else int(r["side"]),
                         r["times"], r["last_date"],
                         r["rating"] if r["rating"] is not None else old.get(r["restaurant_id"]), r["score"], stamp))
        for c in combos:
            cur.execute("INSERT INTO swiggy_history_combos(user_id, restaurant_id, restaurant_name, label, items, slot, "
                        "slots, times, last_date, score, imported_ts) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (user_id, c["restaurant_id"], c["restaurant_name"], c["label"], json.dumps(c["items"]),
                         c["slot"], ",".join(c["slots"]), c["times"], c["last_date"], c["score"], stamp))
    return view(user_id)


def delete(user_id: int) -> dict:
    sc.init_schema()
    with db.cursor() as cur:
        cur.execute("DELETE FROM swiggy_history_suggestions WHERE user_id=?", (user_id,))
        cur.execute("DELETE FROM swiggy_history_combos WHERE user_id=?", (user_id,))
    return view(user_id)


def note_ratings(user_id: int, ratings: dict) -> None:
    """Fill a missing ★ on suggested places from a rating Swiggy just showed (a menu or a
    search the person opened): no extra Swiggy call (r3 #31)."""
    pairs = []
    for rid, rating in (ratings or {}).items():
        try:
            value = float(rating)
        except (TypeError, ValueError):
            continue
        if 0 < value <= 5:
            pairs.append((round(value, 1), user_id, str(rid)))
    if not pairs:
        return
    try:
        with db.cursor() as cur:
            for p in pairs:
                cur.execute("UPDATE swiggy_history_suggestions SET rating=? WHERE user_id=? AND restaurant_id=? "
                            "AND rating IS NULL", p)
    except Exception:                    # table not created yet: nothing suggested to fill
        return


def save_places(user_id: int, restaurant_ids=None) -> dict:
    """One tap: save the suggested places (all, or the ids given) as favourite restaurants
    for the current delivery address. Only places from the person's own order history."""
    from . import swiggy_live
    known = {r["restaurant_id"]: r["restaurant_name"] for r in _stored(user_id)}
    if not known:
        raise ValueError("Suggest from your Swiggy orders first.")
    if restaurant_ids is None:
        wanted = list(known)
    elif isinstance(restaurant_ids, list) and all(isinstance(x, (str, int)) for x in restaurant_ids):
        wanted = [str(x) for x in restaurant_ids]
    else:
        raise ValueError("Choose places from your suggestions")
    if any(w not in known for w in wanted):
        raise ValueError("That place isn't in your suggestions any more. Refresh them first.")
    address_id = swiggy_live._address(swiggy_live._conn(user_id))
    added = []
    with db.cursor() as cur:
        for rid in wanted:
            if not cur.execute("SELECT 1 FROM swiggy_favourites WHERE user_id=? AND address_id=? AND restaurant_id=?",
                               (user_id, address_id, rid)).fetchone():
                cur.execute("INSERT INTO swiggy_favourites(user_id, address_id, restaurant_id, restaurant_name) "
                            "VALUES (?,?,?,?)", (user_id, address_id, rid, known[rid]))
                added.append(known[rid])
    return {**view(user_id), "places_saved": added}
