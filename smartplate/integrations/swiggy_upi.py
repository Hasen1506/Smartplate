"""UPI checkout through Swiggy's Payment stage (R2 item 8, behind SMARTPLATE_SWIGGY_MODE=live_order).

Swiggy MCP's shared Payment stage (10 Jul 2026, docs/research/platform-2026-10.md):
  get_payment_options → place_food_order with `intentApp` (a UPI app) or `generateUPIQR=true`
  → the order waits in PENDING_PAYMENT → check_payment_status → confirm_order
  (Food: orderId + addressId + lat + lng) → PLACED / FAILED.

Ziggy never collects money and never asks for a VPA: the person pays Swiggy in their own UPI
app (or by scanning Swiggy's QR). Ziggy only starts it, for exactly the cart fingerprint and
total the person approved (the same short-lived approval as the COD path in swiggy_live), and
then confirms the order once Swiggy says the payment went through.

Off in every mode below live_order. When Swiggy doesn't offer the Payment stage to Ziggy's
account (tools missing, no UPI for this cart, a schema without intentApp/generateUPIQR), the
answer is "Pay in Swiggy" with a plain message and the checkout link, never an error page.
"""
from __future__ import annotations

import re

from .. import clock, config, db
from .swiggy_connect import SwiggyError
from .swiggy_schema import _find

UPI_TOOLS = ("get_payment_options", "place_food_order", "check_payment_status", "confirm_order")
PAID = {"SUCCESS", "PAID", "COMPLETED", "CAPTURED", "PAYMENT_SUCCESS"}
FAILED = {"FAILED", "FAILURE", "EXPIRED", "CANCELLED", "DECLINED", "PAYMENT_FAILED", "TIMEOUT"}
APP_RE = re.compile(r"^[a-z0-9_]{1,24}$")
TOKEN_PREFIX = "upi."

OFF = "Paying from Ziggy is off on this server. Your cart is ready in Swiggy: pay there."
NOT_ENABLED = ("Paying by UPI from Ziggy isn't switched on for your Swiggy account yet. "
               "Your cart is ready in Swiggy: pay there.")
NO_UPI = "Swiggy didn't offer UPI for this cart in Ziggy. Your cart is ready in Swiggy: pay there."


class PayInSwiggy(SwiggyError):
    """UPI from Ziggy isn't available for this cart: hand the person to Swiggy's checkout."""

    def __init__(self, message: str):
        super().__init__(message, code="pay_in_swiggy")


def pay_in_swiggy(message: str) -> dict:
    from .swiggy_live import CHECKOUT_URL
    return {"mode": "pay_in_swiggy", "upi": False, "label": "Pay in Swiggy", "url": CHECKOUT_URL,
            "message": message, "pays": False}


def _tools(conn: dict) -> dict:
    return {t.get("name"): t for t in db.jl(conn["tools"]) if isinstance(t, dict)}


def available(conn: dict) -> None:
    """Raise PayInSwiggy unless this server and this Swiggy account can run the UPI stage."""
    if not config.LIVE_ORDERS:
        raise PayInSwiggy(OFF)
    tools = _tools(conn)
    if any(name not in tools for name in UPI_TOOLS):
        raise PayInSwiggy(NOT_ENABLED)
    props = ((tools["place_food_order"].get("input_schema") or {}).get("properties") or {})
    if not (_find(props, "intent_app") or _find(props, "upi_qr")):
        raise PayInSwiggy(NOT_ENABLED)


def option(body: dict) -> dict | None:
    """The UPI entry of get_payment_options, read defensively (its exact shape is only known
    after a real sign-in): {"id", "label", "apps"} or None."""
    if not isinstance(body, dict):
        return None
    cands = [body["upi"]] if isinstance(body.get("upi"), dict) else []
    for key in ("paymentOptions", "payment_options", "options", "methods", "paymentMethods"):
        if isinstance(body.get(key), list):
            cands += [c for c in body[key] if isinstance(c, dict)]
    for c in cands:
        kind = " ".join(str(c.get(k) or "") for k in ("type", "method", "id", "displayName")).lower()
        if "upi" not in kind or c.get("available", True) is not True or not c.get("id"):
            continue
        apps = []
        for a in c.get("apps") or c.get("intentApps") or []:
            name = a.get("id") or a.get("name") if isinstance(a, dict) else a
            if isinstance(name, str) and APP_RE.match(name.strip().lower()):
                apps.append(name.strip().lower())
        return {"id": str(c["id"]), "label": str(c.get("displayName") or "UPI"), "apps": apps}
    return None


def choose(body: dict, app: str | None) -> dict:
    """The payment fields of the approved checkout for UPI (part of its fingerprint)."""
    upi = option(body)
    if not upi:
        raise PayInSwiggy(NO_UPI)
    if app is not None:
        app = str(app).strip().lower()
        if not APP_RE.match(app) or (upi["apps"] and app not in upi["apps"]):
            raise ValueError("Choose one of the UPI apps Swiggy offers for this cart.")
    return {"payment_method": upi["id"], "payment_kind": "upi", "upi_app": app,
            "payment_label": f"{upi['label']} · {app}" if app else f"{upi['label']} (Swiggy's QR)",
            "upi_apps": upi["apps"]}


def token(app: str | None, raw: str) -> str:
    return f"{TOKEN_PREFIX}{app or 'qr'}.{raw}"


def parse_token(value: str) -> tuple[str, str | None]:
    """("cod", None) for a COD approval, ("upi", app-or-None) for a UPI one."""
    if not value.startswith(TOKEN_PREFIX):
        return "cod", None
    app = value[len(TOKEN_PREFIX):].split(".", 1)[0]
    return "upi", (None if app == "qr" else app)


def order_args(preview: dict) -> dict:
    """place_food_order values for UPI: the app intent, or Swiggy's QR when no app was chosen."""
    values = {"address": preview["address_id"], "payment_method": preview["payment_method"]}
    if preview.get("upi_app"):
        values["intent_app"] = preview["upi_app"]
    else:
        values["upi_qr"] = True
    return values


def _safe_link(value) -> str | None:
    if isinstance(value, str) and value.startswith(("upi://", "https://", "data:image/png;base64,")):
        return value[:4000]
    return None


def pay_step(body: dict) -> dict:
    """What the person needs to pay in their own app: an intent link and/or Swiggy's QR."""
    out = {}
    for key in ("intentUrl", "intent_url", "upiIntent", "upi_intent", "deeplink", "deepLink", "paymentUrl"):
        link = _safe_link(body.get(key))
        if link:
            out["intent_url"] = link
            break
    for key in ("qrCode", "qr_code", "upiQr", "upiQR", "qr"):
        link = _safe_link(body.get(key))
        if link:
            out["qr"] = link
            break
    return out


def payment_status(user_id: int, order_id: str) -> dict:
    """Ask Swiggy whether the UPI payment for this pending order went through; confirm the
    order when it did. Never pays anything itself."""
    from . import swiggy_live as sl
    from ..domain import spend
    if not isinstance(order_id, str) or not order_id.strip():
        raise ValueError("Which order?")
    with db.cursor() as cur:
        attempt = cur.execute("SELECT * FROM swiggy_order_attempts WHERE user_id=? AND order_id=?",
                              (user_id, order_id)).fetchone()
    if not attempt:
        raise ValueError("This order is not recorded for this profile")
    attempt = db.row_to_dict(attempt)
    if attempt["state"] == "confirmed":
        return {"status": "confirmed", "order_id": order_id, "to_pay": attempt.get("to_pay")}
    if attempt["state"] != "pending_payment":
        return {"status": attempt["state"], "order_id": order_id, **pay_in_swiggy(
            "Check this order in Swiggy: Ziggy can't confirm it from here.")}
    conn = sl._conn(user_id)
    data = sl.call(user_id, "check_payment_status",
                   sl.build_args(sl._tool(conn, "check_payment_status"), {"order_id": order_id}))
    body = data.get("data", data) if isinstance(data, dict) else {}
    state = str((body or {}).get("paymentStatus") or (body or {}).get("status") or "").upper()

    def settle(new_state: str):
        with db.cursor() as cur:
            cur.execute("UPDATE swiggy_order_attempts SET state=?, resolved_ts=? WHERE user_id=? AND order_id=? "
                        "AND state='pending_payment'", (new_state, clock.now().isoformat(), user_id, order_id))

    if state in FAILED:
        settle("failed")
        return {"status": "payment_failed", "order_id": order_id,
                "message": "The UPI payment didn't go through, so nothing was ordered. You can try again."}
    if state not in PAID:
        return {"status": "pending_payment", "order_id": order_id, "to_pay": attempt.get("to_pay"),
                "message": "Waiting for your UPI app. Pay there; Ziggy never sees your UPI details."}
    address = next((a for a in sl.addresses(user_id) if a["id"] == attempt["address_id"]), None)
    if not address or address.get("lat") is None or address.get("lng") is None:
        settle("unknown")
        return {"status": "paid_unconfirmed", "order_id": order_id, **pay_in_swiggy(
            "Swiggy took your payment, but Ziggy couldn't confirm the order without the address's location. "
            "Check your Swiggy orders; don't pay again.")}
    try:
        result = sl.call(user_id, "confirm_order", sl.build_args(sl._tool(conn, "confirm_order"), {
            "order_id": order_id, "address": attempt["address_id"], "lat": address["lat"], "lng": address["lng"]}))
    except sl.NotSent:
        raise
    except Exception:
        settle("unknown")
        raise
    done = result.get("data", result) if isinstance(result, dict) else {}
    final = str((done or {}).get("status") or (done or {}).get("orderStatus") or "").upper()
    if final in ("PLACED", "CONFIRMED", "SUCCESS"):
        settle("confirmed")
        with db.cursor() as cur:
            cur.execute("DELETE FROM swiggy_cart_intents WHERE user_id=?", (user_id,))
        spend.record_placed(user_id, None, attempt.get("to_pay"), order_id, "")
        return {"status": "confirmed", "order_id": order_id, "to_pay": attempt.get("to_pay"),
                "message": "Paid by UPI in your app and placed with Swiggy."}
    settle("failed" if final in FAILED else "unknown")
    return {"status": "not_confirmed", "order_id": order_id, **pay_in_swiggy(
        "Swiggy didn't confirm this order. Check your Swiggy orders before paying again.")}
