"""Safe checkout preflight for autonomous ordering.

The planner can change between a user's review and execution (availability,
substitution, or a manual re-optimisation).  A compact fingerprint and optional
spend ceiling let clients bind consent to the exact plan they displayed without
coupling this domain layer to a payment provider.
"""
import hashlib
import json


class CheckoutConflict(ValueError):
    """The reviewed checkout no longer matches the executable plan."""


def preview(plan_id: int, decisions: list[dict]) -> dict:
    deliveries = [d for d in decisions if d["chosen_kind"] == "delivery"]
    items = [{
        "decision_id": d["id"],
        "session_id": d["session_id"],
        "day": d.get("day"),
        "meal": d.get("meal") or "",
        "item": d.get("item_name") or "",
        "restaurant": d.get("restaurant_name") or "",
        "amount": round(float(d.get("cost", 0)), 2),
    } for d in deliveries]
    return {
        "plan_id": plan_id,
        "order_count": len(items),
        "total": round(sum(item["amount"] for item in items), 2),
        "currency": "INR",
        "fingerprint": _fingerprint(items),
        "items": items,
        "disclosure": "Final price and availability are revalidated by the provider at placement.",
    }


def validate(review: dict, *, expected_fingerprint: str | None = None,
             max_total: float | None = None) -> None:
    """Validate optional client consent controls against a fresh preview."""
    if expected_fingerprint is not None and expected_fingerprint != review["fingerprint"]:
        raise CheckoutConflict("plan changed since checkout review; review the updated total")
    if max_total is not None:
        try:
            ceiling = float(max_total)
        except (TypeError, ValueError) as exc:
            raise CheckoutConflict("max_total must be a non-negative number") from exc
        if ceiling < 0:
            raise CheckoutConflict("max_total must be a non-negative number")
        if review["total"] > ceiling:
            raise CheckoutConflict(
                f"checkout total INR {review['total']:.2f} exceeds approved maximum INR {ceiling:.2f}")


def _fingerprint(items: list[dict]) -> str:
    payload = json.dumps(items, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()[:24]
