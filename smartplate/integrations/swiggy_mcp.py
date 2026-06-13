"""Swiggy MCP adapter — Simulated (default) | Live (drop-in).

This is the single seam between SmartPlate and the delivery platform. Everything
else is built against this interface, so the day real gated MCP access lands you
swap SimulatedSwiggyProvider -> LiveSwiggyProvider and nothing else changes
(FEASIBILITY.md §4).

It implements the pieces the brainstorm flags as non-optional:
  • idempotency keys on every write (§5.1.3) — a retried network blip never double-orders
  • a saga with compensation steps, not raw retry (§6)
  • the known menu-load failure mode for chains (§1.2), so the substitution engine
    is exercised against the exact failure Swiggy itself hits
"""
import hashlib
import json
import random
import time

from .. import config, db


class MenuLoadError(RuntimeError):
    """Raised when a (flaky) restaurant's menu fails to load — triggers substitution."""


class OrderResult:
    def __init__(self, ok, idempotency_key, provider_order_id=None, state="placed",
                 amount=0.0, log=None, deduped=False, error=None):
        self.ok = ok
        self.idempotency_key = idempotency_key
        self.provider_order_id = provider_order_id
        self.state = state
        self.amount = amount
        self.log = log or []
        self.deduped = deduped
        self.error = error

    def as_dict(self):
        return {
            "ok": self.ok, "idempotency_key": self.idempotency_key,
            "provider_order_id": self.provider_order_id, "state": self.state,
            "amount": round(self.amount, 2), "deduped": self.deduped,
            "error": self.error, "log": self.log,
        }


def idempotency_key(user_id, plan_id, session_id, trigger_ts) -> str:
    """Exactly the derivation the brainstorm specifies (§5.1.3)."""
    raw = f"{user_id}:{plan_id}:{session_id}:{trigger_ts}"
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


# --------------------------------------------------------------------------- #
# Providers
# --------------------------------------------------------------------------- #
class SimulatedSwiggyProvider:
    """Simulation of the MCP write path, including the §1.2 menu-load failure.

    flaky_fail_rate defaults to 1.0 so the failure (and thus the substitution
    engine) is deterministic and reproducible for demos and tests. Set it to 0.0
    to force the happy path, or anything in between to model intermittency.
    """

    def __init__(self, seed=None, flaky_fail_rate=1.0):
        self.rng = random.Random(seed)
        self.flaky_fail_rate = flaky_fail_rate

    def load_menu(self, restaurant: dict):
        # §1.2: chain restaurants fail to load. 'flaky' rows model this.
        if restaurant.get("flaky") and self.rng.random() < self.flaky_fail_rate:
            raise MenuLoadError(f"menu for '{restaurant['name']}' failed to load")
        return True

    def create_cart(self, restaurant, item):
        return {"cart_id": f"cart_{self.rng.randint(10000, 99999)}"}

    def confirm_payment(self, amount):
        return {"txn_id": f"txn_{self.rng.randint(10000, 99999)}", "amount": amount}

    def place(self, cart_id, txn_id):
        return {"order_id": f"swg_{self.rng.randint(100000, 999999)}"}


class LiveSwiggyProvider:
    """Placeholder for the real gated MCP. Wiring it up is the only remaining
    external dependency for production order placement (FEASIBILITY.md §4)."""

    def load_menu(self, restaurant):
        raise NotImplementedError("Live Swiggy MCP access not configured")

    create_cart = confirm_payment = place = load_menu


def get_provider():
    if config.SWIGGY_PROVIDER == "live":
        return LiveSwiggyProvider()
    return SimulatedSwiggyProvider()


# --------------------------------------------------------------------------- #
# Order saga (validate -> cart -> pay -> place), idempotent + compensating
# --------------------------------------------------------------------------- #
def place_order(decision: dict, restaurant: dict, item: dict, *, user_id, plan_id,
                session_id, trigger_ts, provider=None) -> OrderResult:
    provider = provider or get_provider()
    key = idempotency_key(user_id, plan_id, session_id, trigger_ts)

    # Idempotency: if we already have a terminal order for this key, return it.
    with db.cursor() as cur:
        existing = cur.execute(
            "SELECT * FROM orders WHERE idempotency_key=?", (key,)
        ).fetchone()
    if existing and existing["state"] in ("placed", "confirmed"):
        return OrderResult(True, key, existing["provider_order_id"], existing["state"],
                           existing["amount"], db.jl(existing["log"]), deduped=True)

    amount = float(decision.get("cost", 0))
    log, state, order_id = [], "validated", None

    def record(st):
        with db.cursor() as cur:
            cur.execute(
                "INSERT OR REPLACE INTO orders(id, decision_id, idempotency_key, "
                "provider_order_id, state, amount, log, created_ts) "
                "VALUES ((SELECT id FROM orders WHERE idempotency_key=?), ?,?,?,?,?,?,?)",
                (key, decision.get("id"), key, order_id, st, amount,
                 db.jd(log), _now()),
            )

    try:
        provider.load_menu(restaurant)              # may raise MenuLoadError
        log.append("menu loaded"); state = "carted"
        cart = provider.create_cart(restaurant, item)
        log.append(f"cart {cart['cart_id']}"); record(state)

        txn = provider.confirm_payment(amount)
        state = "confirmed"; log.append(f"paid {txn['txn_id']}"); record(state)

        placed = provider.place(cart["cart_id"], txn["txn_id"])
        order_id = placed["order_id"]
        state = "placed"; log.append(f"placed {order_id}"); record(state)
        return OrderResult(True, key, order_id, state, amount, log)

    except MenuLoadError as e:
        # Compensate and bubble up so the caller can substitute (§1.2, variance engine).
        log.append(f"FAILED: {e}"); state = "failed"; record(state)
        return OrderResult(False, key, None, state, amount, log, error="menu_load")
    except Exception as e:  # pragma: no cover - defensive DLQ path (§6)
        log.append(f"FAILED: {e}"); state = "failed"; record(state)
        return OrderResult(False, key, None, state, amount, log, error=str(e))


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")
