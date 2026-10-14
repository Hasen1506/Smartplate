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
# Pool a bucket goes to when the user ticks it (pools have breakfast / lunch / dinner).
POOL_FOR = {"breakfast": "breakfast", "lunch": "lunch", "dinner": "dinner", "late": "dinner"}
_QTY = re.compile(r"^\s*(\d+)\s*[x×]\s*", re.I)
_LOOSE_TIME = re.compile(r"(\d{4}-\d{2}-\d{2})[ T]+(\d{1,2}):(\d{2})(?::\d{2})?\s*(AM|PM)?", re.I)


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
    """Time-of-day bucket in IST: breakfast 5-11, lunch 11-16, dinner 16-22, late 22-5."""
    h = at.hour
    if 5 <= h < 11:
        return "breakfast"
    if 11 <= h < 16:
        return "lunch"
    if 16 <= h < 22:
        return "dinner"
    return "late"


def _items(row: dict) -> list[dict]:
    """The dishes of one order: the reorder list when Swiggy sends it (ids + names), else
    the `orderedItems` text split on commas ("2 x Masala Dosa, Filter Coffee")."""
    for action in row.get("actions") or []:
        meta = action.get("reorderMeta") if isinstance(action, dict) else None
        items = meta.get("orderItems") if isinstance(meta, dict) else None
        if isinstance(items, list) and items:
            out = []
            for it in items:
                if isinstance(it, dict) and str(it.get("name") or "").strip():
                    mid = it.get("menu_item_id") or it.get("item_id")
                    out.append({"name": str(it["name"]).strip()[:80], "menu_item_id": str(mid) if mid else None})
            if out:
                return out
    raw = row.get("orderedItems")
    if isinstance(raw, list):            # older shape seen in tests: a list of {name}
        names = [str(i.get("name") or i.get("itemName") or "") for i in raw if isinstance(i, dict)]
    else:
        names = str(raw or "").split(",")
    return [{"name": _QTY.sub("", n).strip()[:80], "menu_item_id": None} for n in names if _QTY.sub("", n).strip()]


def _known_ratings(user_id: int) -> dict[str, float]:
    """Ratings Ziggy already holds for this user's live restaurants (no extra Swiggy calls)."""
    from ..domain import live_catalog
    with db.cursor() as cur:
        rows = cur.execute("SELECT provider_id, rating FROM restaurants WHERE city=? AND provider_id IS NOT NULL",
                           (live_catalog.city_key(user_id),)).fetchall()
    return {str(r["provider_id"]): float(r["rating"]) for r in rows if r["rating"]}


def score_orders(orders: list, *, now: dt.datetime, ratings: dict | None = None) -> list[dict]:
    """Pure: order rows -> suggestions, best first. Cancelled orders and unreadable times
    are skipped (a guess about when someone eats is worse than no suggestion)."""
    ratings = ratings or {}
    acc: dict[tuple, dict] = {}
    for row in orders:
        if not isinstance(row, dict) or not row.get("restaurantId") or not row.get("restaurantName"):
            continue
        if "CANCEL" in str(row.get("orderStatus") or "").upper():
            continue
        at = _when(row.get("orderedTime"))
        if at is None:
            continue
        age = max(0.0, (now - at).total_seconds() / 86400)
        rid = str(row["restaurantId"])
        for it in _items(row):
            key = (rid, it["name"].lower())
            s = acc.setdefault(key, {"restaurant_id": rid, "restaurant_name": str(row["restaurantName"])[:80],
                                     "item_name": it["name"], "menu_item_id": it["menu_item_id"],
                                     "times": 0, "recency": 0.0, "buckets": {}, "last": at})
            s["times"] += 1
            s["recency"] += 0.5 ** (age / HALF_LIFE_DAYS)
            b = bucket(at)
            s["buckets"][b] = s["buckets"].get(b, 0) + 1
            s["last"] = max(s["last"], at)
            s["menu_item_id"] = s["menu_item_id"] or it["menu_item_id"]
    out = []
    order = ("breakfast", "lunch", "dinner", "late")
    for s in acc.values():
        slot = max(order, key=lambda b: (s["buckets"].get(b, 0), -order.index(b)))
        rating = ratings.get(s["restaurant_id"])
        bonus = RATING_W * min(1.5, max(0.0, rating - 3.5)) if rating else 0.0
        out.append({"restaurant_id": s["restaurant_id"], "restaurant_name": s["restaurant_name"],
                    "item_name": s["item_name"], "menu_item_id": s["menu_item_id"], "slot": slot,
                    "times": s["times"], "last_date": s["last"].date().isoformat(), "rating": rating,
                    "score": round(s["times"] + s["recency"] + bonus, 4)})
    out.sort(key=lambda r: (-r["score"], r["restaurant_name"].lower(), r["item_name"].lower()))
    return out


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
        rows = cur.execute("SELECT restaurant_id, restaurant_name, item_name, menu_item_id, slot, times, last_date, "
                           "rating, score, imported_ts FROM swiggy_history_suggestions WHERE user_id=?",
                           (user_id,)).fetchall()
    out = [dict(r) for r in rows]
    out.sort(key=lambda r: (-r["score"], r["restaurant_name"].lower(), r["item_name"].lower()))
    return out


def _restaurants(items: list[dict]) -> list[dict]:
    by: dict[str, dict] = {}
    for it in items:
        r = by.setdefault(it["restaurant_id"], {"restaurant_id": it["restaurant_id"],
                                                "restaurant_name": it["restaurant_name"], "times": 0,
                                                "score": 0.0, "slots": {}, "rating": it.get("rating")})
        # A place ranks by its best dish: one order of two dishes isn't two visits.
        r["times"] = max(r["times"], it["times"])
        r["score"] = max(r["score"], it["score"])
        r["slots"][it["slot"]] = r["slots"].get(it["slot"], 0) + it["times"]
    out = list(by.values())
    out.sort(key=lambda r: (-r["score"], r["restaurant_name"].lower()))
    return out


def view(user_id: int) -> dict:
    items = _stored(user_id)
    for it in items:
        it["pool_meal"] = POOL_FOR[it["slot"]]
    by_slot = {b: [i for i in items if i["slot"] == b] for b in ("breakfast", "lunch", "dinner", "late")}
    return {"consent": CONSENT, "imported": bool(items),
            "imported_at": items[0]["imported_ts"] if items else None,
            "restaurants": _restaurants(items), "items": items, "by_slot": by_slot,
            "attribution": "Powered by Swiggy" if items else None,
            "note": "Tick the ones you like to add them to your favourites. Nothing is added on its own."}


def import_history(user_id: int, consent) -> dict:
    """Read the order history once (explicit tap + consent) and keep the minimal suggestions."""
    if consent is not True:
        raise ValueError("Tap 'Suggest from my Swiggy orders' and agree to the note first.")
    if not sc.private_owner(user_id):
        raise ValueError("Suggestions from Swiggy orders need your own private Ziggy profile.")
    rows = score_orders(fetch_orders(user_id), now=clock.now(), ratings=_known_ratings(user_id))
    stamp = clock.now().isoformat(timespec="minutes")
    sc.init_schema()
    with db.cursor() as cur:
        cur.execute("DELETE FROM swiggy_history_suggestions WHERE user_id=?", (user_id,))
        for r in rows:
            cur.execute("INSERT INTO swiggy_history_suggestions(user_id, restaurant_id, restaurant_name, item_name, "
                        "menu_item_id, slot, times, last_date, rating, score, imported_ts) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (user_id, r["restaurant_id"], r["restaurant_name"], r["item_name"], r["menu_item_id"],
                         r["slot"], r["times"], r["last_date"], r["rating"], r["score"], stamp))
    return view(user_id)


def delete(user_id: int) -> dict:
    sc.init_schema()
    with db.cursor() as cur:
        cur.execute("DELETE FROM swiggy_history_suggestions WHERE user_id=?", (user_id,))
    return view(user_id)
