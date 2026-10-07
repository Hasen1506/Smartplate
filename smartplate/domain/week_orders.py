"""One-tap ordering for any meal and the whole week (roadmap PR C).

The user picks which meal slots (breakfast / lunch / dinner) and which days to order.
Each picked meal then goes through the same real steps as a single order, one at a time
at its order time, because Swiggy holds one cart at a time:

  1. cart check — the exact dish goes into the user's Swiggy cart and the true bill is
     read back (items, delivery, platform/packaging fees, GST, discounts, to pay);
  2. explicit approval of that exact total (the existing checkout approval token);
  3. placement — only where a real order API allows it (Swiggy's COD order tool, behind
     LIVE_ORDERS). Otherwise the result is "cart ready, tap to place" in Swiggy.

Swiggy's MCP has no scheduled-order tool, so nothing is placed automatically at a later
time: SCHEDULING says so, and order-time reminders (web push) nudge the user instead.
"""
from .. import clock, config, db
from . import models

STATES = ("queued", "cart_ready", "placed", "handed_off")
SCHEDULING = {"supported": False,
              "why": "Swiggy's order tool places an order immediately (Cash on Delivery only); it has no "
                     "scheduled-order option. SmartPlate reminds you at each order time and gets the cart ready."}


def _cells(plan_id: int) -> list[dict]:
    from .. import service
    view = service.plan_view(plan_id)
    out = []
    for i, day in enumerate(view["grid"]):
        for meal, c in day["meals"].items():
            out.append({**c, "day_index": i, "day": day["day"], "date": day.get("date"), "meal": meal})
    return out


def _orderable(cell: dict) -> bool:
    return cell["kind"] == "delivery" and cell.get("status") == "active" and not cell.get("past")


def _rows(plan_id: int) -> dict:
    with db.cursor() as cur:
        return {r["session_id"]: dict(r) for r in cur.execute(
            "SELECT * FROM order_queue WHERE plan_id=?", (plan_id,))}


def queue_view(plan_id: int) -> dict:
    rows = _rows(plan_id)
    meals = []
    for c in _cells(plan_id):
        if not (_orderable(c) or c["session_id"] in rows):
            continue
        row = rows.get(c["session_id"])
        meals.append({"session_id": c["session_id"], "day": c["day"], "day_index": c["day_index"],
                      "date": c.get("date"), "meal": c["meal"], "item": c["item"], "restaurant": c.get("restaurant"),
                      "planned_cost": c["cost"], "order_at": (c.get("order") or {}).get("order_at"),
                      "queued": bool(row), "state": row["state"] if row else None,
                      "to_pay": row["to_pay"] if row else None, "bill": db.jl(row["bill"], None) if row else None,
                      "orderable": _orderable(c)})
    queued = [m for m in meals if m["queued"]]
    return {"plan_id": plan_id, "meals": meals, "scheduling": SCHEDULING, "order_enabled": config.LIVE_ORDERS,
            "queued": len(queued), "planned_total": round(sum(m["planned_cost"] for m in queued), 2),
            "confirmed_total": round(sum(m["to_pay"] or 0 for m in queued if m["state"] in ("cart_ready", "placed",
                                                                                         "handed_off")), 2)}


def set_queue(plan_id: int, meals: list, days: list) -> dict:
    """Queue every upcoming planned delivery in the picked slots and days (and unqueue the
    rest that are still only queued; meals already in a cart or placed are kept)."""
    meals = [m for m in (meals or []) if m in models.MEAL_WINDOWS]
    try:
        days = sorted({int(d) for d in (days or []) if 0 <= int(d) <= 6})
    except (TypeError, ValueError):
        raise ValueError("Days must be 0 (Mon) to 6 (Sun)") from None
    if not meals or not days:
        raise ValueError("Pick at least one meal and one day to order")
    picked = [c for c in _cells(plan_id) if _orderable(c) and c["meal"] in meals and c["day_index"] in days]
    rows = _rows(plan_id)
    now = clock.now().isoformat(timespec="seconds")
    with db.cursor() as cur:
        for sid, row in rows.items():
            if row["state"] == "queued" and sid not in {c["session_id"] for c in picked}:
                cur.execute("DELETE FROM order_queue WHERE session_id=?", (sid,))
        for c in picked:
            if c["session_id"] not in rows:
                cur.execute("INSERT INTO order_queue(plan_id, session_id, state, updated_ts) VALUES (?,?, 'queued', ?)",
                            (plan_id, c["session_id"], now))
    return queue_view(plan_id)


def _row(session_id: int) -> dict:
    with db.cursor() as cur:
        row = cur.execute("SELECT * FROM order_queue WHERE session_id=?", (session_id,)).fetchone()
    if not row:
        raise ValueError("Add this meal to your order list first")
    return dict(row)


def check_cart(session_id: int, expected_fingerprint: str | None) -> dict:
    """Step 1: the reviewed dish goes into the Swiggy cart; record the true bill."""
    from ..integrations import swiggy_live
    _row(session_id)
    result = swiggy_live.fill_cart(session_id, expected_fingerprint)
    with db.cursor() as cur:
        cur.execute("UPDATE order_queue SET state='cart_ready', to_pay=?, bill=?, updated_ts=? WHERE session_id=?",
                    (result.get("to_pay"), db.jd(result.get("bill")), clock.now().isoformat(timespec="seconds"),
                     session_id))
    next_step = ({"mode": "approve_and_place", "label": f"Approve ₹{result['to_pay']:.2f} and place"}
                 if config.LIVE_ORDERS and result.get("to_pay") is not None else
                 {"mode": "tap_to_place", "label": "Cart ready — tap to place in Swiggy", "url": result["checkout_url"]})
    return {**result, "next": next_step}


def place(session_id: int, user_id: int, expected_fingerprint: str | None) -> dict:
    """Steps 2-3: place exactly the approved cart (only with a real order API)."""
    from ..integrations import swiggy_live
    row = _row(session_id)
    if row["state"] != "cart_ready":
        raise ValueError("Check this meal's Swiggy cart and approve its total first")
    if not config.LIVE_ORDERS:
        with db.cursor() as cur:
            cur.execute("UPDATE order_queue SET state='handed_off', updated_ts=? WHERE session_id=?",
                        (clock.now().isoformat(timespec="seconds"), session_id))
        return {"mode": "tap_to_place", "placed": False, "url": swiggy_live.CHECKOUT_URL,
                "message": "Your cart is ready in Swiggy. Tap to place it there."}
    result = swiggy_live.place_live_order(user_id, expected_fingerprint)
    if result.get("state") == "placed" or result.get("order_id"):
        with db.cursor() as cur:
            cur.execute("UPDATE order_queue SET state='placed', updated_ts=? WHERE session_id=?",
                        (clock.now().isoformat(timespec="seconds"), session_id))
    return {"mode": "placed", **result}
