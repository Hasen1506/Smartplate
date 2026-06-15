"""Variance & substitution engine — where plan ≠ reality.

The defensible core (§3.1). At execution time we place each delivery decision on
the (simulated) Swiggy MCP. When a restaurant's menu fails to load (§1.2), we do
NOT silently drop below the rating floor — we pick the next-best candidate above
the floor, record exactly what changed, and re-place with the same idempotency
discipline. "We never silently substitute below your rating floor." (§3.1)
"""
from .. import db
from ..domain import models
from ..integrations import swiggy_mcp
from . import optimizer


def execute_plan(plan_id: int, *, provider=None) -> dict:
    plan = models.get_plan(plan_id)
    user = models.get_user(plan["user_id"])
    ctx = optimizer.build_context(user, plan)
    provider = provider or swiggy_mcp.get_provider()

    sessions = {s["id"]: s for s in models.sessions_for_plan(plan_id)}
    results = []
    for d in models.decisions_for_plan(plan_id):
        if d["chosen_kind"] != "delivery":
            continue
        session = sessions[d["session_id"]]
        results.append(_execute_one(user, plan, session, d, ctx, provider))

    placed = [r for r in results if r["placed"]]
    return {
        "plan_id": plan_id,
        "attempted": len(results),
        "placed": len(placed),
        "substituted": sum(1 for r in results if r["substituted"]),
        "failed": sum(1 for r in results if not r["placed"]),
        "results": results,
    }


def _execute_one(user, plan, session, decision, ctx, provider):
    restaurant = _restaurant(decision["restaurant_id"])
    item = {"id": decision["item_id"], "name": decision["item_name"]}
    trigger_ts = session["scheduled_ts"]

    res = swiggy_mcp.place_order(
        decision, restaurant, item, user_id=user["id"], plan_id=plan["id"],
        session_id=session["id"], trigger_ts=trigger_ts, provider=provider)

    substituted = False
    sub_info = None
    if not res.ok and res.error == "menu_load":
        # substitute to next-best above the rating floor (never below) -------- #
        alt = _next_best(user, plan, session, ctx, exclude_restaurant=decision["restaurant_id"])
        if alt and alt["kind"] == "delivery":
            substituted = True
            sub_info = {"from": decision["item_name"], "to": alt["item_name"],
                        "reason": "menu failed to load"}
            decision = _apply_substitution(decision, alt, sub_info)
            restaurant = _restaurant(alt["restaurant_id"])
            item = {"id": alt["item_id"], "name": alt["item_name"]}
            res = swiggy_mcp.place_order(
                decision, restaurant, item, user_id=user["id"], plan_id=plan["id"],
                session_id=session["id"], trigger_ts=trigger_ts, provider=provider)
        else:
            _mark_skip(decision, "no in-range option above rating floor")

    if session["status"] == "active" and res.ok:
        with db.cursor() as cur:
            cur.execute("UPDATE sessions SET status='ordered' WHERE id=?", (session["id"],))

    return {
        "session_id": session["id"], "day": session["day"], "meal": session["meal"],
        "item": decision["item_name"], "cost": decision["cost"],
        "placed": res.ok, "deduped": res.deduped, "substituted": substituted,
        "substitution": sub_info, "idempotency_key": res.idempotency_key,
        "provider_order_id": res.provider_order_id, "state": res.state, "log": res.log,
    }


def _next_best(user, plan, session, ctx, exclude_restaurant):
    cands = optimizer.build_candidates(user, plan, session, ctx)
    w = ctx["weights"]
    ref = max(1.0, user["weekly_budget"] / 21)
    pool = [c for c in cands
            if c["kind"] == "delivery" and c["restaurant_id"] != exclude_restaurant
            # the promise is hard at execution time regardless of the planner's
            # rating-floor mode: never substitute below the user's chosen ★.
            and c.get("rating", 0) >= user["rating_floor"]]
    if not pool:
        return next((c for c in cands if c["kind"] in ("cook", "skip")), None)
    pool.sort(key=lambda c: optimizer._objective(c, w, ref, user["carbon_pref"], 3.0))
    return pool[0]


def _apply_substitution(decision, alt, sub_info):
    reasons = list(decision.get("reasons", []))
    reasons.insert(0, f"Substituted {sub_info['from']} → {sub_info['to']} ({sub_info['reason']}); "
                      f"stayed above your rating floor.")
    updated = {**decision, "restaurant_id": alt["restaurant_id"], "item_id": alt["item_id"],
               "item_name": alt["item_name"], "cost": alt["cost"], "substituted": 1,
               "reasons": reasons}
    with db.cursor() as cur:
        cur.execute(
            "UPDATE decisions SET restaurant_id=?, item_id=?, item_name=?, cost=?, "
            "substituted=1, reasons=? WHERE id=?",
            (alt["restaurant_id"], alt["item_id"], alt["item_name"], alt["cost"],
             db.jd(reasons), decision["id"]))
    return updated


def _mark_skip(decision, reason):
    with db.cursor() as cur:
        cur.execute("UPDATE decisions SET chosen_kind='skip', reasons=? WHERE id=?",
                    (db.jd([f"Skipped: {reason}."]), decision["id"]))


def _restaurant(restaurant_id):
    with db.cursor() as cur:
        row = cur.execute("SELECT * FROM restaurants WHERE id=?", (restaurant_id,)).fetchone()
    return db.row_to_dict(row) if row else {"id": restaurant_id, "name": "?", "flaky": 0}
