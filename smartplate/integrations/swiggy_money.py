"""Prices and bills from Swiggy replies: paise vs rupees, cart totals, the bill breakdown,
the delivery fee actually billed and the cancellation-fee note.

Pure functions (no network, no database). Split out of swiggy_live.py (grill #57);
every name is re-exported there.
"""
import json
import math
import re
import statistics
from .swiggy_schema import FIELDS, _num


# Swiggy documents menu `price` as a bare number with no unit. Its web data uses paise,
# the MCP cart reads in rupees; neither is guaranteed. A unit-named field wins; otherwise
# the unit is inferred (whole numbers whose typical value is ≥ 1000 are paise — a ₹1,000+
# median dish is implausible) and the price is marked as an estimate.
PAISE_THRESHOLD = 1000


_PRICE_TEXT = re.compile(r"\s*(₹|rs\.?|inr)?\s*([\d,]+(?:\.\d+)?)\s*", re.IGNORECASE)


def _price_raw(record: dict) -> tuple[float | None, str | None]:
    """(number, unit) for the first price field; unit is None when the provider doesn't name it."""
    if not isinstance(record, dict):
        return None, None
    lowered = {k.lower(): k for k in record}
    for alias in FIELDS["price"]:
        key = lowered.get(alias.lower())
        if key is None:
            continue
        value, unit = record[key], None
        if isinstance(value, bool) or value is None:
            continue
        if key.lower() == "finalprice" and (not isinstance(value, (int, float)) or value <= 0):
            continue                          # no discounted price: the list price is the price
        if isinstance(value, str):
            match = _PRICE_TEXT.fullmatch(value)
            if not match:
                continue
            unit = "rupees" if match.group(1) else None
            value = float(match.group(2).replace(",", ""))
        elif isinstance(value, (int, float)):
            value = float(value)
        else:
            continue
        if not math.isfinite(value) or value < 0:
            return None, None
        if "paise" in key.lower():
            unit = "paise"
        elif "rupee" in key.lower():
            unit = "rupees"
        return value, unit
    return None, None


def list_price(record: dict, unit_hint: str | None = None) -> float | None:
    """The list price Swiggy strikes through when `finalPrice` is lower (audit #9), else None."""
    if not isinstance(record, dict):
        return None
    final = rupees(record, unit_hint)
    lowered = {k.lower(): k for k in record}
    if "finalprice" not in lowered or final is None:
        return None
    for alias in ("price", "defaultPrice"):
        key = lowered.get(alias.lower())
        value = record.get(key) if key else None
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
            unit = unit_hint or infer_unit([value])
            full = round(value / 100, 2) if unit == "paise" else round(float(value), 2)
            return full if full > final + 0.5 else None
    return None


def infer_unit(values) -> str:
    vals = [float(v) for v in values if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0]
    if not vals or any(abs(v - round(v)) > 1e-9 for v in vals):      # paise are whole numbers
        return "rupees"
    return "paise" if statistics.median(vals) >= PAISE_THRESHOLD else "rupees"


def menu_unit(rows) -> str:
    """One unit for a whole menu, inferred from its unit-less prices together."""
    return infer_unit([v for v, u in (_price_raw(r) for r in rows if isinstance(r, dict)) if v is not None and u is None])


def rupees(record: dict, unit_hint: str | None = None) -> float | None:
    """A rupee price from either form: named units are exact, bare numbers are inferred."""
    value, unit = _price_raw(record)
    if value is None:
        return None
    unit = unit or unit_hint or infer_unit([value])
    return round(value / 100, 2) if unit == "paise" else round(value, 2)


def price_estimated(record: dict) -> bool:
    """True when the unit of this price was inferred rather than named by Swiggy."""
    value, unit = _price_raw(record)
    return value is not None and unit is None


def cart_total(cart: dict, anchor: float | None = None) -> float | None:
    """The payable total in rupees, or None when it can't be read unambiguously (L-05).

    Unit-named fields are exact. A bare `to_pay` is read as rupees or paise only when
    exactly one reading is plausible: against `anchor` (the reviewed dish price, in
    rupees) it must cover the dish and stay within fees; without an anchor, a whole
    number under 1000 is rupees (a cart under ₹10 isn't real) and anything else is
    ambiguous. Ambiguous totals are never used to approve an order."""
    pricing = cart.get("pricing") if isinstance(cart.get("pricing"), dict) else cart
    for key, unit in (("toPayInPaise", "paise"), ("to_pay_in_paise", "paise"),
                      ("toPayInRupees", "rupees"), ("to_pay_in_rupees", "rupees")):
        for node in (pricing, cart):
            value = node.get(key) if isinstance(node, dict) else None
            if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0:
                return round(value / 100, 2) if unit == "paise" else round(float(value), 2)
    raw = _num(pricing, "to_pay")
    if raw is None:
        raw = _num(cart, "to_pay")
    if raw is None or raw <= 0:
        return None
    readings = {"rupees": round(raw, 2), "paise": round(raw / 100, 2)}
    if anchor:
        plausible = [v for v in readings.values() if anchor * 0.7 <= v <= anchor * 3 + 250]
        return plausible[0] if len(plausible) == 1 else None
    if abs(raw - round(raw)) > 1e-9 or raw < PAISE_THRESHOLD:
        return readings["rupees"]
    return None


BILL_FIELDS = [   # (label, keys Swiggy may use) in display order; amounts as Swiggy returns them.
    # Labels are the ones Swiggy's own bill uses ("GST & Other Charges" is Swiggy's
    # documented `taxes_and_charges`, https://mcp.swiggy.com/builders/docs/reference/food/get_food_cart/).
    ("Item Total", ("item_total", "itemTotal", "items_total", "subtotal", "sub_total")),
    ("Delivery Fee", ("delivery_charge", "deliveryCharge", "delivery_fee", "deliveryFee")),
    ("Platform Fee", ("platform_fee", "platformFee", "convenience_fee", "convenienceFee")),
    ("Packaging Charges", ("packaging_charge", "packagingCharge", "packaging_charges", "packing_charges", "packingCharges")),
    ("Small Cart Fee", ("small_cart_fee", "smallCartFee")),
    ("GST & Other Charges", ("taxes_and_charges", "taxesAndCharges", "gst_and_other_charges", "gst", "taxes", "tax",
                             "total_tax", "totalTax", "gst_and_restaurant_charges")),
    ("Discount", ("discount", "total_discount", "totalDiscount", "coupon_discount")),
]


ROUNDING_TOLERANCE = 1.0          # Swiggy rounds the payable total to the rupee


def bill_breakdown(cart: dict, anchor: float | None = None) -> dict | None:
    """The cart's bill line by line, exactly as Swiggy returned it, to the paisa: item
    total, delivery, fees, GST & Other Charges, discount and the payable total, with
    Swiggy's own labels. Lines Swiggy did not return are left out (never guessed).

    No line is ever invented. Swiggy rounds the payable total to the rupee, so the lines
    can differ from it by under ₹1 (`rounding`, shown as a note). A larger gap means
    Swiggy charged something it did not itemise: `unitemised` says so, and the bill is
    not marked itemised; the amount is never presented as a Swiggy line."""
    total = cart_total(cart, anchor)
    if total is None:
        return None
    pricing = cart.get("pricing") if isinstance(cart.get("pricing"), dict) else cart
    offers = cart.get("offers") if isinstance(cart.get("offers"), dict) else {}
    raw = []
    for label, keys in BILL_FIELDS:
        for key in keys:
            node = pricing if isinstance(pricing, dict) else {}
            value = node.get(key)
            if value is None and label == "Discount":
                value = offers.get(key)          # coupon_discount lives under offers; > 0 means applied
            if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value:
                raw.append((label, -abs(value) if label == "Discount" else float(value)))
                break
    scale = _line_scale(raw, total)
    lines = [{"label": label, "amount": round(v / scale, 2)} for label, v in raw]
    gap = round(total - sum(line["amount"] for line in lines), 2)
    out = {"lines": lines, "to_pay": total, "source": "swiggy_cart"}
    if lines and abs(gap) >= 0.01:
        if abs(gap) < ROUNDING_TOLERANCE:
            out["rounding"] = gap
        else:
            out["unitemised"] = gap
    out["itemised"] = bool(lines) and "unitemised" not in out
    return out


CANCELLATION_RE = re.compile(r"[^.\n]*cancell?ation[^.\n]*(fee|charge)[^.\n]*\.?|[^.\n]*(fee|charge)[^.\n]*cancell?ed[^.\n]*\.?",
                             re.IGNORECASE)


def cancellation_note(data: object) -> str | None:
    """Swiggy's own cancellation-fee sentence when its reply carries one (e.g. "100%
    cancellation fee if cancelled after 60 seconds"), else None. Never paraphrased."""
    stack, seen = [data], 0
    while stack and seen < 5000:
        node = stack.pop()
        seen += 1
        if isinstance(node, dict):
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
        elif isinstance(node, str) and "cancel" in node.lower():
            if node.lstrip().startswith(("{", "[")):
                try:
                    stack.append(json.loads(node))
                    continue
                except ValueError:
                    pass
            m = CANCELLATION_RE.search(node)
            if m:
                return re.sub(r"\s+", " ", m.group(0)).strip()[:200]
    return None


def _line_scale(raw: list, total: float) -> float:
    """Lines come in the same unit as the cart (rupees or paise): use the reading whose
    sum is closer to the payable total."""
    raw_sum = sum(v for _, v in raw)
    return 100.0 if raw and abs(raw_sum / 100 - total) < abs(raw_sum - total) else 1.0


def billed_delivery_fee(cart: dict, anchor: float | None = None) -> float | None:
    """The delivery fee on Swiggy's bill, in rupees: 0 when Swiggy billed free delivery,
    None when the bill has no delivery line or its total can't be read unambiguously
    (then nothing is learned)."""
    bill = bill_breakdown(cart, anchor)
    if bill is None:
        return None
    pricing = cart.get("pricing") if isinstance(cart.get("pricing"), dict) else cart
    keys = dict(BILL_FIELDS)["Delivery Fee"]
    value = next((pricing.get(k) for k in keys if isinstance(pricing, dict) and k in pricing), None)
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or value < 0:
        return None
    if value == 0:
        return 0.0
    line = next((l for l in bill["lines"] if l["label"] == "Delivery Fee"), None)
    return line["amount"] if line else None
