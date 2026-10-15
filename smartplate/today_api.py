"""Thin JSON routes for the v3 Today screen (smartplate/today.py). Registered by app.py;
ownership, JSON-only POSTs and per-profile locking come from app.validate_request."""
from flask import Blueprint, jsonify, request

from . import ratelimit, today

bp = Blueprint("today", __name__)
def _tables(_state) -> None:
    """Tables created on first use exist before export/delete read them."""
    from .domain import autofill
    today.init_schema()
    autofill.init_schema()                              # plan-ahead carts (opt-in auto-fill)


bp.record_once(_tables)


def _body() -> dict:
    return request.get_json(silent=True) or {}


@bp.get("/api/user/<int:user_id>/today")
def today_view(user_id):
    return jsonify(today.view(user_id))


@bp.post("/api/user/<int:user_id>/today/budget")
def today_budget(user_id):
    return jsonify(today.set_budget(user_id, _body().get("amount")))


@bp.post("/api/user/<int:user_id>/today/rules")
def today_rules(user_id):
    return jsonify(today.set_rules(user_id, _body()))


@bp.post("/api/user/<int:user_id>/today/pick")
def today_pick(user_id):
    b = _body()
    return jsonify(today.pick(user_id, b.get("meal"), b.get("ref"), b.get("qty")))


@bp.post("/api/user/<int:user_id>/today/decide")
def today_decide(user_id):
    ratelimit.check(f"today-decide:{user_id}", 60, 3600)
    return jsonify(today.decide(user_id, _body().get("mode")))


@bp.post("/api/user/<int:user_id>/today/pool")
def today_pool_add(user_id):
    b = _body()
    ratelimit.check(f"pools:{user_id}", 60, 3600)
    if b.get("source") == "swiggy":
        return jsonify(today.add_swiggy(user_id, b))
    return jsonify(today.add_typed(user_id, b))


@bp.post("/api/user/<int:user_id>/today/pool/suggested")
def today_pool_suggested(user_id):
    """Ticked suggestions from the Swiggy order history (W7) into the Today pools (W5)."""
    ratelimit.check(f"pools:{user_id}", 60, 3600)
    return jsonify(today.add_suggested(user_id, _body()))


@bp.post("/api/user/<int:user_id>/today/pool/remove")
def today_pool_remove(user_id):
    b = _body()
    return jsonify(today.remove(user_id, b.get("meal"), b.get("ref")))


@bp.post("/api/user/<int:user_id>/today/pool/qty")
def today_pool_qty(user_id):
    b = _body()
    return jsonify(today.set_qty(user_id, b.get("meal"), str(b.get("ref") or ""), b.get("qty")))
