"""Plan-ahead carts: "auto-fill my cart" for a meal or a day (founder decision, 14 Oct 2026).

Swiggy keeps one live cart per account and an order placed there is delivered at once, so
a week can't be ordered ahead. What Ziggy can do: when a planned meal's cart window opens,
put that meal into the person's Swiggy cart and tell them "Your lunch cart is ready — pay
in Swiggy". It is opt-in per meal (or per day), and it never pays.

  1. Opt in. The person reviews the meal's live Swiggy item (GET /api/session/<id>/
     swiggy-cart/preview) and turns auto-fill on with that preview's fingerprint. The
     fingerprint is stored: it is the exact dish, restaurant, address, quantity and price
     they saw.
  2. The cart window opens. /cron/tick (push.tick) runs `run_due`: a fresh preview must
     still match the stored fingerprint (else nothing is added and they are asked to review
     again); a cart that already holds other items is never overwritten (they are told);
     otherwise the reviewed dish goes into the cart through the same checked path as the
     "Order" button (week_orders.check_single_cart: the real bill is read back and kept),
     and a push says it's ready to pay in Swiggy.
  3. Nothing is ever paid or placed here.

States: armed → filled | changed | blocked | missed | failed | off.
"""
from __future__ import annotations

import datetime as dt
import logging

from .. import clock, config, db
from . import models

log = logging.getLogger("smartplate.autofill")

SCHEMA = """
CREATE TABLE IF NOT EXISTS auto_cart (
    session_id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL,
    plan_id INTEGER NOT NULL,
    fingerprint TEXT NOT NULL,
    item TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL DEFAULT 'armed',
    note TEXT NOT NULL DEFAULT '',
    created_ts TEXT NOT NULL,
    updated_ts TEXT NOT NULL
);
"""
MEAL_WORD = {"breakfast": "breakfast", "lunch": "lunch", "dinner": "dinner"}


def init_schema() -> None:
    with db.cursor() as cur:
        cur.executescript(SCHEMA)


def _now() -> str:
    return clock.now().isoformat(timespec="seconds")


def _session(session_id: int) -> tuple[dict, dict]:
    s = models.get_session(session_id)
    if not s:
        raise KeyError("Meal not found")
    return s, models.get_plan(s["plan_id"])


def arm(session_id: int, expected_fingerprint: str | None) -> dict:
    """Turn auto-fill on for one meal, bound to the live item the person just reviewed."""
    from ..integrations import swiggy_live
    if not config.swiggy_allows("live_cart"):
        raise ValueError("Auto-filling your Swiggy cart needs Ziggy's live Swiggy cart, which is off on this server.")
    if not isinstance(expected_fingerprint, str) or not expected_fingerprint:
        raise swiggy_live.CartChanged("Review this meal's Swiggy item first, then turn on auto-fill.")
    s, plan = _session(session_id)
    preview = swiggy_live.cart_preview(session_id)          # fresh: the exact item, price and address
    if preview["fingerprint"] != expected_fingerprint:
        raise swiggy_live.CartChanged("The live Swiggy item or your plan changed. Review it again before turning on auto-fill.")
    init_schema()
    now = _now()
    with db.cursor() as cur:
        cur.execute("INSERT OR REPLACE INTO auto_cart(session_id, user_id, plan_id, fingerprint, item, state, note, "
                    "created_ts, updated_ts) VALUES (?,?,?,?,?, 'armed', '', ?, ?)",
                    (session_id, plan["user_id"], plan["id"], expected_fingerprint,
                     f"{preview.get('quantity', 1)}× {preview['item']} from {preview['restaurant']}", now, now))
    try:
        from .. import push
        push.invalidate()
    except Exception:
        pass
    return status(session_id)


def disarm(session_id: int) -> dict:
    init_schema()
    with db.cursor() as cur:
        cur.execute("UPDATE auto_cart SET state='off', updated_ts=? WHERE session_id=? AND state='armed'",
                    (_now(), session_id))
    return status(session_id)


def status(session_id: int) -> dict:
    init_schema()
    with db.cursor() as cur:
        row = cur.execute("SELECT * FROM auto_cart WHERE session_id=?", (session_id,)).fetchone()
    if not row:
        return {"session_id": session_id, "on": False, "state": None}
    r = db.row_to_dict(row)
    return {"session_id": session_id, "on": r["state"] == "armed", "state": r["state"], "item": r["item"],
            "note": r["note"], "updated": r["updated_ts"], "pays": False,
            "how": "When this meal's cart window opens, Ziggy puts it in your Swiggy cart and tells you. "
                   "You pay in Swiggy; Ziggy never pays."}


def for_plan(plan_id: int) -> list[dict]:
    init_schema()
    with db.cursor() as cur:
        ids = [r["session_id"] for r in cur.execute("SELECT session_id FROM auto_cart WHERE plan_id=? ORDER BY session_id",
                                                     (plan_id,))]
    return [status(i) for i in ids]


def arm_day(plan_id: int, day: int, fingerprints: dict) -> dict:
    """Auto-fill every planned delivery of one day; each needs the fingerprint the person
    reviewed (`fingerprints`: {session_id: fingerprint}). Meals without one are skipped."""
    from .. import service
    out = {"armed": [], "skipped": []}
    sessions = [s for s in models.sessions_for_plan(plan_id) if int(s["day"]) == int(day)]
    for s in sessions:
        try:
            delivery = s.get("status") == "active" and service.session_order_status(s["id"]) is not None
        except Exception:
            delivery = False
        if not delivery:                               # cooked, skipped or tiffin-at-home meals have no cart
            continue
        fp = (fingerprints or {}).get(str(s["id"])) or (fingerprints or {}).get(s["id"])
        if not fp:
            out["skipped"].append({"session_id": s["id"], "why": "Not reviewed"})
            continue
        try:
            arm(s["id"], fp)
            out["armed"].append(s["id"])
        except Exception as exc:                       # one meal's problem doesn't stop the day
            out["skipped"].append({"session_id": s["id"], "why": str(exc)})
    out["meals"] = for_plan(plan_id)
    return out


def disarm_day(plan_id: int, day: int) -> dict:
    for s in models.sessions_for_plan(plan_id):
        if int(s["day"]) == int(day):
            disarm(s["id"])
    return {"meals": for_plan(plan_id)}


# --------------------------------------------------------------------------- #
# The cron side (push.tick)
# --------------------------------------------------------------------------- #
def next_opening(at: dt.datetime) -> list[dt.datetime]:
    """When the next armed meals' cart windows open (so a quiet tick wakes for them)."""
    from .. import service
    init_schema()
    with db.cursor() as cur:
        rows = [r["session_id"] for r in cur.execute("SELECT session_id FROM auto_cart WHERE state='armed'")]
    out = []
    for sid in rows:
        try:
            st = service.session_order_status(sid, at)
        except Exception:
            continue
        if st and st.get("cart_opens_at"):
            # timing gives local time with the app's offset; the clock and push work in naive local time
            when = dt.datetime.fromisoformat(st["cart_opens_at"])
            out.append(when.replace(tzinfo=None) if at.tzinfo is None else when)
    return out


def _set(session_id: int, state: str, note: str = "") -> None:
    with db.cursor() as cur:
        cur.execute("UPDATE auto_cart SET state=?, note=?, updated_ts=? WHERE session_id=?",
                    (state, note[:300], _now(), session_id))


def _notify(user_id: int, title: str, body: str, url: str, tag: str, sender=None) -> int:
    from .. import push
    from ..integrations import webpush
    sender = sender or webpush.send
    with db.cursor() as cur:
        subs = [db.row_to_dict(r) for r in cur.execute("SELECT * FROM push_subscriptions WHERE user_id=? ORDER BY id",
                                                       (user_id,))]
    ok = 0
    for sub in subs:
        try:
            code = sender(sub["endpoint"], sub["p256dh"], sub["auth"], {"title": title, "body": body, "url": url,
                                                                       "tag": tag},
                          private_b64u=push.vapid_private(), contact=config.PUSH_CONTACT)
        except Exception:
            log.exception("autofill: push to %s failed", sub["endpoint"][:40])
            continue
        ok += 200 <= code < 300
    return ok


def run_due(at: dt.datetime | None = None, sender=None) -> dict:
    """Fill the armed meals whose cart window is open now. Never pays."""
    from .. import service
    from ..integrations import swiggy_live
    from . import week_orders
    at = at or clock.now()
    init_schema()
    stats = {"filled": 0, "blocked": 0, "changed": 0, "missed": 0, "failed": 0}
    if not config.swiggy_allows("live_cart"):
        return stats
    with db.cursor() as cur:
        rows = [db.row_to_dict(r) for r in cur.execute("SELECT * FROM auto_cart WHERE state='armed' ORDER BY session_id")]
    for r in rows:
        sid, uid = r["session_id"], r["user_id"]
        try:
            st = service.session_order_status(sid, at)
        except Exception:
            st = None
        s = models.get_session(sid)
        meal = MEAL_WORD.get((s or {}).get("meal"), "meal")
        if st is None or not s or s.get("status") != "active":
            _set(sid, "off", "This meal is no longer a planned delivery.")
            continue
        if st["cart_state"] == "early":
            continue
        if st["cart_state"] == "closed":
            _set(sid, "missed", "The meal's order time passed before its cart window could be used.")
            stats["missed"] += 1
            continue
        with db.cursor() as cur:                       # claim it: two ticks never fill one meal twice
            got = cur.execute("UPDATE auto_cart SET state='filling', updated_ts=? WHERE session_id=? AND state='armed'",
                              (_now(), sid)).rowcount
        if got != 1:
            continue
        try:
            preview = swiggy_live.cart_preview(sid)
            if preview["fingerprint"] != r["fingerprint"]:
                _set(sid, "changed", "The dish, its price, the address or the plan changed since you set this up.")
                _notify(uid, f"Review your {meal} before it goes in the cart",
                        f"Your {meal} changed since you set up auto-fill, so Ziggy didn't add it. Review it in Ziggy.",
                        "/?tab=today", f"smartplate-autofill-{sid}", sender)
                stats["changed"] += 1
                continue
            cart = swiggy_live.current_live_cart(uid)
            if not cart.get("empty"):
                _set(sid, "blocked", "Your Swiggy cart already had other items, so Ziggy didn't overwrite it.")
                _notify(uid, f"Your Swiggy cart isn't empty, so your {meal} wasn't added",
                        f"Ziggy didn't touch the items already in your Swiggy cart. Clear it or add your {meal} "
                        "from Ziggy when you're ready.", "/?tab=today", f"smartplate-autofill-{sid}", sender)
                stats["blocked"] += 1
                continue
            result = week_orders.check_single_cart(sid, r["fingerprint"])
            to_pay = result.get("to_pay")
            _set(sid, "filled", f"In your Swiggy cart{f' (₹{to_pay:,.0f} to pay)' if to_pay else ''}. Pay in Swiggy.")
            _notify(uid, f"Your {meal} cart is ready — pay in Swiggy",
                    f"{r['item']}{f', ₹{to_pay:,.0f} to pay' if to_pay else ''}. Ziggy doesn't pay: open Swiggy "
                    "to pay and place it.", swiggy_live.CHECKOUT_URL, f"smartplate-autofill-{sid}", sender)
            stats["filled"] += 1
        except Exception as exc:
            msg = str(exc) if isinstance(exc, (ValueError, swiggy_live.SwiggyError)) else "Swiggy didn't answer."
            if "already has items" in msg:
                _set(sid, "blocked", "Your Swiggy cart already had other items, so Ziggy didn't overwrite it.")
                stats["blocked"] += 1
            else:
                _set(sid, "failed", msg)
                stats["failed"] += 1
                log.warning("autofill: session %s failed: %s", sid, msg)
            _notify(uid, f"Your {meal} wasn't added to your Swiggy cart", msg[:180], "/?tab=today",
                    f"smartplate-autofill-{sid}", sender)
    return stats
