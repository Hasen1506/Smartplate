"""Safe checkout preflight for autonomous ordering.

The planner can change between a user's review and execution (availability,
substitution, or a manual re-optimisation).  A compact fingerprint and optional
spend ceiling let clients bind consent to the exact plan they displayed without
coupling this domain layer to a payment provider.
"""
import hashlib
import json
import math


class CheckoutConflict(ValueError):
    """The reviewed checkout no longer matches the executable plan."""


def preview(plan_id: int, decisions: list[dict], at=None) -> dict:
    """Only upcoming, active deliveries: a meal whose time has passed is never ordered
    (the rest of the app treats an unconfirmed past meal as unknown, not spent)."""
    from ..kernel import optimizer, scheduler
    at = at or optimizer.now()
    deliveries = [d for d in decisions if d["chosen_kind"] == "delivery"
                  and d.get('session_status', 'active') == 'active'
                  and not scheduler.is_past(d, at)]
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
        "disclosure": "Simulation only. No payment or restaurant order. Substitutions stay within each reviewed meal's price ceiling.",
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
        if not math.isfinite(ceiling) or ceiling < 0:
            raise CheckoutConflict("max_total must be a non-negative number")
        if review["total"] > ceiling:
            raise CheckoutConflict(
                f"checkout total INR {review['total']:.2f} exceeds approved maximum INR {ceiling:.2f}")


def _fingerprint(items: list[dict]) -> str:
    payload = json.dumps(items, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()[:24]


# --------------------------------------------------------------------------- #
# Swiggy Builders Club's ₹1,000 cart cap (critique 47)
# --------------------------------------------------------------------------- #
CART_CAP = 1000.0     # ₹ payable, Builders Club Food orders (checked again on Swiggy's real bill)


def split_for_cap(price: float, quantity: int, item: dict | None = None, *, cap: float = CART_CAP) -> dict:
    """Does `quantity` × a ₹`price` dish fit one capped Swiggy cart once delivery, fees and
    GST are on the bill? If not, how to order it: split into the fewest carts that each fit
    (the default since 14 Oct 2026: sequential carts, each approved; every cart pays its own
    delivery and platform fee, so splitting costs extra and says how much), or hand off to
    the Swiggy app only when even one portion can't fit.

    Fees come from the dish's fee model (domain/fees: learned from the user's bills, else a
    labelled estimate); a row without one is checked on the item total plus the default
    fees, so the check never under-counts."""
    from . import fees
    quantity = int(quantity)
    if quantity < 1 or price is None or price <= 0:
        raise ValueError("Quantity and price must be positive")
    model = (item or {}).get("fees") or fees.pricing_for(None, {})

    def bill(q: int) -> float:
        row = {"fees": model}
        return round(price * q + model["delivery"]["amount"] + fees.charges(row, price * q), 2)

    one = bill(quantity)
    if one <= cap:
        return {"fits": True, "mode": "one_cart", "carts": [{"quantity": quantity, "estimate": one}],
                "estimate_total": one, "extra_fees": 0.0, "cap": cap}
    per_cart = max((q for q in range(1, quantity + 1) if bill(q) <= cap), default=0)
    if per_cart == 0:
        return {"fits": False, "mode": "handoff", "carts": [], "cap": cap, "estimate_total": one,
                "extra_fees": 0.0,
                "message": (f"One portion comes to about ₹{bill(1):,.0f} with fees, above Swiggy's ₹{cap:,.0f} limit "
                            "for orders placed from Ziggy. Order it in the Swiggy app.")}
    n = math.ceil(quantity / per_cart)
    base, rest = divmod(quantity, n)                     # even split: 5 → 3 + 2, never 4 + 1
    sizes = [base + (1 if i < rest else 0) for i in range(n)]
    carts = [{"quantity": q, "estimate": bill(q)} for q in sizes]
    total = round(sum(c["estimate"] for c in carts), 2)
    extra = round(total - one, 2)
    return {"fits": False, "mode": "split", "carts": carts, "cap": cap, "estimate_total": total,
            "extra_fees": extra,
            "message": (f"{quantity} × this dish is about ₹{one:,.0f} with fees, above Swiggy's ₹{cap:,.0f} limit for "
                        f"orders placed from Ziggy, so Ziggy splits it into {n} carts ({' + '.join(str(q) for q in sizes)}), "
                        f"one after the other, each for your approval: about ₹{extra:,.0f} more in delivery and "
                        "platform fees.")}
