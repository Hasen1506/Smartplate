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
CHECKED = ("cart_ready", "placed", "handed_off")     # a real Swiggy bill is known for these


class OverBudget(ValueError):
    """Approving this cart would take the week over budget without the user saying so."""

    def __init__(self, budget: dict):
        super().__init__(budget["message"])
        self.budget = budget
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


def real_totals(plan_id: int) -> dict:
    """{session_id: {"to_pay", "planned_cost", "state"}} for meals whose real Swiggy cart
    total is known. The plan counts these at the real total, not the estimate."""
    with db.cursor() as cur:
        rows = cur.execute("SELECT session_id, to_pay, planned_cost, state FROM order_queue "
                           "WHERE plan_id=? AND to_pay IS NOT NULL AND state IN (%s)" % ",".join("?" * len(CHECKED)),
                           (plan_id, *CHECKED)).fetchall()
    return {r["session_id"]: dict(r) for r in rows}


def release(session_id: int) -> None:
    """The checked cart no longer fits the user's rules: it has to be checked again."""
    with db.cursor() as cur:
        cur.execute("UPDATE order_queue SET state='queued', to_pay=NULL, bill=NULL, planned_cost=NULL, updated_ts=? "
                    "WHERE session_id=? AND state='cart_ready'", (clock.now().isoformat(timespec="seconds"), session_id))


def record_cart(plan_id: int, session_id: int, result: dict) -> None:
    """Keep the real bill of a checked cart for this meal (adds it to the order list)."""
    now = clock.now().isoformat(timespec="seconds")
    with db.cursor() as cur:
        row = cur.execute("SELECT planned_cost FROM order_queue WHERE session_id=?", (session_id,)).fetchone()
        planned = row["planned_cost"] if row and row["planned_cost"] is not None else result.get("planned_cost")
        cur.execute("INSERT INTO order_queue(plan_id, session_id, state, to_pay, bill, planned_cost, updated_ts) "
                    "VALUES (?,?, 'cart_ready', ?,?,?,?) ON CONFLICT(session_id) DO UPDATE SET state='cart_ready', "
                    "to_pay=excluded.to_pay, bill=excluded.bill, planned_cost=excluded.planned_cost, "
                    "updated_ts=excluded.updated_ts",
                    (plan_id, session_id, result.get("to_pay"), db.jd(result.get("bill")), planned, now))


def budget_check(plan_id: int, session_id: int | None = None) -> dict:
    """The week with every checked cart at its real total, against the week's budget."""
    from .. import service
    view = service.plan_view(plan_id)
    b = view["budget"]
    total, cap = round(b["spend"], 2), round(b["budget"], 2)
    over_by = round(total - cap, 2)
    real = real_totals(plan_id).get(session_id) if session_id else None
    out = {"week_budget": cap, "week_total": total, "over": over_by > 0.005, "over_by": max(0.0, over_by),
           "left": max(0.0, round(cap - total, 2))}
    if real:
        out["cart_total"] = real["to_pay"]
        out["planned_cost"] = real["planned_cost"]
    if out["over"]:
        lead = f"This cart is ₹{real['to_pay']:.2f}. It takes" if real else "Your checked carts take"
        out["message"] = (f"{lead} the week ₹{out['over_by']:.2f} over your ₹{cap:.0f} budget "
                          f"(₹{total:.2f} planned in all).")
        out["actions"] = [{"act": "approve-over-budget", "label": "Approve anyway"},
                          {"act": "replan-remaining", "label": "Re-plan the remaining meals"}]
    return out


def replan_remaining(plan_id: int, session_id: int | None = None) -> dict:
    """Re-plan the open meals around what is already ordered, pinned or in a checked cart
    (at its real total), with the user's usual rules: allergies, diet and targets."""
    from .. import service
    service.reoptimize(plan_id)
    return {"plan": service.plan_view(plan_id), "budget": budget_check(plan_id, session_id),
            "queue": queue_view(plan_id)}


def queue_view(plan_id: int) -> dict:
    rows = _rows(plan_id)
    meals = []
    for c in _cells(plan_id):
        if not (_orderable(c) or c["session_id"] in rows):
            continue
        row = rows.get(c["session_id"])
        meals.append({"session_id": c["session_id"], "day": c["day"], "day_index": c["day_index"],
                      "date": c.get("date"), "meal": c["meal"], "item": c["item"], "restaurant": c.get("restaurant"),
                      "planned_cost": (row["planned_cost"] if row and row["planned_cost"] is not None
                                       else c.get("planned_cost", c["cost"])), "order_at": (c.get("order") or {}).get("order_at"),
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
    row = _row(session_id)
    result = swiggy_live.fill_cart(session_id, expected_fingerprint)
    record_cart(row["plan_id"], session_id, result)
    next_step = ({"mode": "approve_and_place", "label": f"Approve ₹{result['to_pay']:.2f} and place"}
                 if config.LIVE_ORDERS and result.get("to_pay") is not None else
                 {"mode": "tap_to_place", "label": "Cart ready — tap to place in Swiggy", "url": result["checkout_url"]})
    return {**result, "next": next_step, "budget": budget_check(row["plan_id"], session_id)}


def check_single_cart(session_id: int, expected_fingerprint: str | None) -> dict:
    """One planned meal into the Swiggy cart (the meal's own "Order" button): the same
    real bill is kept for the plan and compared with the week's budget."""
    from ..integrations import swiggy_live
    session = models.get_session(session_id)
    if not session:
        raise ValueError("Meal not found")
    result = swiggy_live.fill_cart(session_id, expected_fingerprint)
    if result.get("to_pay") is not None:
        record_cart(session["plan_id"], session_id, result)
    return {**result, "budget": budget_check(session["plan_id"], session_id)}


def place(session_id: int, user_id: int, expected_fingerprint: str | None, over_budget_ok: bool = False) -> dict:
    """Steps 2-3: place exactly the approved cart (only with a real order API). A cart
    that takes the week over budget needs the user's explicit "Approve anyway"."""
    from ..integrations import swiggy_live
    row = _row(session_id)
    if row["state"] != "cart_ready":
        raise ValueError("Check this meal's Swiggy cart and approve its total first")
    budget = budget_check(row["plan_id"], session_id)
    if budget["over"] and over_budget_ok is not True:
        raise OverBudget(budget)
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
