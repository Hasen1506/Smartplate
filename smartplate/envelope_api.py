"""JSON routes for the week's envelope (budget model v2, domain/envelope.py). Registered by
app.py; ownership, JSON-only writes and per-profile locking come from app.validate_request."""
from flask import Blueprint, jsonify, request

from .domain import envelope

bp = Blueprint("envelope", __name__)


def _tables(_state) -> None:
    envelope.init_schema()


bp.record_once(_tables)


def _body() -> dict:
    return request.get_json(silent=True) or {}


def _answer(user_id: int, applied: bool, date=None, replan=True) -> dict:
    if applied and replan:
        envelope.replan(user_id, date)
    return {"applied": applied, "stale": not applied, "envelope": envelope.week(user_id, date)}


@bp.get("/api/user/<int:user_id>/envelope")
def envelope_view(user_id):
    return jsonify(envelope.week(user_id, request.args.get("week") or None))


@bp.put("/api/user/<int:user_id>/day-budget")
def day_budget(user_id):
    b = _body()
    r = envelope.set_day_budget(user_id, b.get("date"), b.get("amount"), b.get("mode") or "soft", b.get("set_at"))
    return jsonify(_answer(user_id, r["applied"], b.get("date")))


@bp.put("/api/user/<int:user_id>/week-override")
def week_override(user_id):
    b = _body()
    r = envelope.set_week_override(user_id, b.get("week_start"), b.get("amount"), b.get("set_at"))
    return jsonify(_answer(user_id, r["applied"], b.get("week_start")))


@bp.put("/api/user/<int:user_id>/day-plan")
def day_plan(user_id):
    b = _body()
    r = envelope.set_day_plan(user_id, b.get("date"), b.get("state"), b.get("meal"), b.get("eaters"),
                              b.get("set_at"))
    return jsonify(_answer(user_id, r["applied"], b.get("date")))


@bp.put("/api/user/<int:user_id>/budget-settings")
def budget_settings(user_id):
    s = envelope.set_settings(user_id, _body())
    envelope.replan(user_id)
    return jsonify({"settings": s, "envelope": envelope.week(user_id)})


@bp.post("/api/user/<int:user_id>/outside-spend")
def outside_spend(user_id):
    """A bill from another app (Zomato, Toing, a restaurant): it eats the envelope on its day."""
    from . import ratelimit
    ratelimit.check(f"outside-spend:{user_id}", 60, 3600)
    b = _body()
    r = envelope.record_outside(user_id, b)
    envelope.replan(user_id, r["date"])
    return jsonify({**r, "envelope": envelope.week(user_id, r["date"])})


@bp.post("/api/user/<int:user_id>/outside-spend/remove")
def outside_spend_remove(user_id):
    """A refund or a receipt entered by mistake: the money goes back to the flexible pool."""
    b = _body()
    date = envelope.remove_outside(user_id, str(b.get("ref") or ""))
    envelope.replan(user_id, date)
    return jsonify({"removed": True, "envelope": envelope.week(user_id, date)})

