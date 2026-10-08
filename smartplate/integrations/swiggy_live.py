"""Swiggy gates 2 and 3: real addresses, restaurants and menus, and filling the cart.

Builds on swiggy_connect (sign-in + discovery). Every call goes through `call()`,
which refuses any tool not on ALLOWED. SmartPlate can read live menus, prepare
an exact item in the cart, and place an explicitly approved Cash on Delivery
order only when LIVE_ORDERS is enabled. Other payments stay in Swiggy.

Written from Swiggy's public docs (docs/vendor/swiggy/README.md). The exact argument
and reply shapes are only known after a real sign-in, so this module:
  • builds arguments from each tool's discovered `inputSchema` (it matches documented
    names like `restaurantId`, `addressId`, `cartItems` and common spellings, and names
    any required field it can't fill),
  • reads replies from `structuredContent`, or JSON inside text content, and finds the
    records it needs by their fields,
  • keeps the last reply shape per tool in `samples` (field names and types only, no
    values) so the first real run shows what to adjust.
Tested against a fake server (tests/test_swiggy_live.py), not the live service.
"""
import datetime as dt
import difflib
import hashlib
import json
import logging
import math
import re
import secrets
import statistics
import threading

from .. import clock, config, db
from . import swiggy_connect as sc
from .swiggy_connect import SwiggyError

ALLOWED = frozenset({"get_addresses", "search_restaurants", "get_restaurant_menu", "search_menu",
                     "get_food_cart", "update_food_cart", "flush_food_cart", "get_payment_options",
                     "place_food_order", "get_food_orders", "track_food_order"})
# Tools that only read: safe to send again on a fresh MCP session after a session error.
READ_ONLY = frozenset({"get_addresses", "search_restaurants", "get_restaurant_menu", "search_menu",
                       "get_food_cart", "get_payment_options", "get_food_orders", "track_food_order"})
MENU_TTL = dt.timedelta(hours=6)
CHECKOUT_QUOTE_TTL = dt.timedelta(minutes=5)
MATCH_RESTAURANT = 0.6
CHECKOUT_URL = "https://www.swiggy.com/checkout"

ALIASES = {
    "query": ["query", "searchQuery", "search_query", "keyword", "q", "restaurantName", "restaurant_name"],
    "address": ["addressId", "address_id"],
    "restaurant": ["restaurantId", "restaurant_id", "restId", "rest_id"],
    "restaurant_scope": ["restaurantIdOfAddedItem", "restaurant_id_of_added_item"],
    "restaurant_name": ["restaurantName", "restaurant_name"],
    "cart_items": ["cartItems", "cart_items", "items"],
    "item_id": ["menu_item_id", "menuItemId", "itemId", "item_id", "id"],
    "quantity": ["quantity", "qty", "count"],
    "payment_method": ["paymentMethod", "payment_method"],
    "order_id": ["orderId", "order_id"],
    "page": ["page"],
    "page_size": ["pageSize"],
    "offset": ["offset"],
    "veg_filter": ["vegFilter"],
}
FIELDS = {
    "id": ["id", "addressId", "address_id", "restaurantId", "restaurant_id", "restId", "itemId", "item_id",
           "menuItemId", "menu_item_id"],
    "menu_item_id": ["menu_item_id", "menuItemId"],
    "name": ["name", "restaurantName", "itemName", "title"],
    "label": ["annotation", "label", "tag", "addressTag", "addressCategory", "addressType", "type"],
    "text": ["formattedAddress", "address", "addressLine", "addressLine1", "displayAddress", "area", "locality"],
    "price": ["priceInPaise", "price_in_paise", "priceInRupees", "price_in_rupees", "price", "finalPrice", "defaultPrice", "cost"],
    "rating": ["avgRating", "rating", "avg_rating"],
    "eta": ["deliveryTimeMinutes", "deliveryTimeRange", "deliveryTime", "eta", "sla", "slaString", "delivery_time"],
    "area": ["areaName", "locality", "area", "cuisines"],
    "veg": ["isVeg", "veg", "is_veg"],
    "to_pay": ["to_pay", "toPay", "totalPayable", "grandTotal", "billTotal"],
    "stock": ["inStock", "in_stock"],
    "variants": ["hasVariants", "has_variants"],
    "addons": ["hasAddons", "has_addons"],
    "restaurant_id": ["restaurant_id", "restaurantId"],
    "availability": ["availabilityStatus", "availability_status"],
    "distance": ["distance", "distanceKm", "distance_km"],
}


class CartChanged(SwiggyError):
    """The live item no longer matches the user's reviewed cart preview."""


class NotSent(SwiggyError):
    """The tool request never reached Swiggy (or Swiggy refused it unprocessed), so an
    order attempt that fails this way is definitely not placed (M-01)."""

    def __init__(self, error: SwiggyError):
        super().__init__(str(error), code=error.code, retry_after=error.retry_after,
                         http_status=getattr(error, "http_status", None))


# One MCP session per profile and token, reused briefly instead of a fresh
# initialize + notifications/initialized for every tool call (M-06).
SESSION_TTL_S = 300
ADDRESS_TTL = dt.timedelta(seconds=60)
_sessions: dict[int, tuple[str, str, float]] = {}
_address_cache: dict[int, tuple[str, dt.datetime, list]] = {}
_cache_lock = threading.Lock()
# HTTP statuses that mean Swiggy did not process the tools/call at all.
UNPROCESSED = {400, 401, 403, 404, 405, 406, 409, 413, 415, 422, 429}


def _token_key(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def forget_session(user_id: int) -> None:
    with _cache_lock:
        _sessions.pop(user_id, None)
        _address_cache.pop(user_id, None)


def _mcp_session(user_id: int, token: str, *, fresh: bool = False) -> tuple[str | None, bool]:
    """(session id, reused?) — a cached live session, or a newly initialized one."""
    import time
    key = _token_key(token)
    with _cache_lock:
        cached = _sessions.get(user_id)
    if cached and not fresh and cached[0] == key and time.monotonic() - cached[2] < SESSION_TTL_S:
        return cached[1], True
    headers, _ = sc.user_rpc(user_id, token, "initialize", {"protocolVersion": sc.CLIENT_VERSION, "capabilities": {},
                                                "clientInfo": {"name": "SmartPlate", "version": "1.1.0"}}, 1)
    session_id = headers.get("mcp-session-id")
    sc.user_rpc(user_id, token, "notifications/initialized", {}, None, session_id)
    with _cache_lock:
        if session_id:                     # a server without sessions gets a fresh handshake each time
            _sessions[user_id] = (key, session_id, time.monotonic())
        else:
            _sessions.pop(user_id, None)
    return session_id, False


# --------------------------------------------------------------------------- #
# Calling tools
# --------------------------------------------------------------------------- #
def _conn(user_id: int) -> dict:
    conn = sc._connection(user_id)
    if not conn:
        raise sc.SwiggyNotConnected()
    if conn["expires_ts"] and dt.datetime.fromisoformat(conn["expires_ts"]) <= clock.now():
        raise sc.SwiggyNotConnected("Your Swiggy sign-in has expired. Connect again.", code="swiggy_auth_expired")
    if not conn["access_token"]:
        raise sc.SwiggyNotConnected("Your Swiggy sign-in can no longer be read on this server. Connect again.")
    return conn


def _tool(conn: dict, name: str) -> dict:
    tool = next((t for t in db.jl(conn["tools"]) if t.get("name") == name), None)
    if not tool:
        raise SwiggyError(f"Your Swiggy account doesn't offer {name}. Tap Refresh tools, or try later.")
    return tool


def _shape(value, depth=0):
    """Field names and types only, never values: what a reply looked like."""
    if depth > 4:
        return "…"
    if isinstance(value, dict):
        return {k: _shape(v, depth + 1) for k, v in list(value.items())[:30]}
    if isinstance(value, list):
        return [_shape(value[0], depth + 1)] if value else []
    return type(value).__name__


def call(user_id: int, name: str, arguments: dict) -> object:
    """One tools/call. Order placement also requires the explicit deployment gate."""
    if name not in ALLOWED:
        raise SwiggyError(f"SmartPlate does not support {name}.")
    if name == "place_food_order" and not config.LIVE_ORDERS:
        raise SwiggyError("Real order placement is disabled until Swiggy access and durable storage are approved.")
    try:
        conn = _conn(user_id)
        _tool(conn, name)
        token = conn["access_token"]
        session_id, reused = _mcp_session(user_id, token)
    except SwiggyError as exc:            # nothing was sent for this tool
        raise NotSent(exc) from exc
    try:
        _, result = sc.user_rpc(user_id, token, "tools/call", {"name": name, "arguments": arguments}, 2, session_id)
    except SwiggyError as exc:
        status = getattr(exc, "http_status", None)
        # Whatever went wrong, the cached MCP session is no longer trusted: one failed call
        # must never poison the calls after it (a stale session id would fail them all).
        forget_session(user_id)
        stale = status == 404 or (status == 400 and name in READ_ONLY)
        if stale and reused and name != "place_food_order":
            # MCP: 404 means the session ended and nothing was processed; some servers say
            # 400 for an unknown session. Re-open once with a fresh session (an order is
            # never re-sent automatically; a cart write only on 404, which is unprocessed).
            try:
                session_id, _ = _mcp_session(user_id, token, fresh=True)
            except SwiggyError as again:
                raise NotSent(again) from again
            try:
                _, result = sc.user_rpc(user_id, token, "tools/call", {"name": name, "arguments": arguments},
                                        2, session_id)
            except SwiggyError as again:
                forget_session(user_id)
                if getattr(again, "http_status", None) in UNPROCESSED:
                    raise NotSent(again) from again
                raise
        elif status in UNPROCESSED:
            raise NotSent(exc) from exc
        else:
            raise
    data = _payload(result)
    samples = db.jl(conn.get("samples"), {})
    samples[name] = {"arguments": sorted(arguments), "reply": _shape(data), "at": clock.now().isoformat(timespec="seconds")}
    try:
        with db.cursor() as cur:
            cur.execute("UPDATE swiggy_connections SET samples=? WHERE user_id=?", (db.jd(samples), user_id))
    except Exception:
        logging.getLogger(__name__).warning("Could not record Swiggy response shape for %s", name)
    if result.get("isError"):
        text = data.get("text") if isinstance(data, dict) else None
        raise SwiggyError(f"Swiggy said: {(text or json.dumps(data))[:240]}")
    if isinstance(data, dict) and data.get("success") is False:
        error = data.get("error") or {}
        message = error.get("message") if isinstance(error, dict) else str(error)
        raise SwiggyError(f"Swiggy said: {(message or 'The request failed')[:240]}")
    return data


def _payload(result: dict):
    if result.get("structuredContent") is not None:
        return result["structuredContent"]
    text = "\n".join(c.get("text", "") for c in result.get("content") or [] if c.get("type") == "text").strip()
    try:
        return json.loads(text)
    except ValueError:
        return {"text": text}


def _find(props: dict, group: str) -> str | None:
    lowered = {k.lower(): k for k in props}
    return next((lowered[a.lower()] for a in ALIASES[group] if a.lower() in lowered), None)


def build_args(tool: dict, values: dict) -> dict:
    """Map SmartPlate's values ({group: value}) onto the tool's own parameter names."""
    schema = tool.get("input_schema") or {}
    props, required = schema.get("properties") or {}, set(schema.get("required") or [])
    args = {}
    for group, value in values.items():
        key = _find(props, group) if props else None
        if key and value is not None:
            args[key] = value
    missing = sorted(required - set(args))
    if missing:
        raise SwiggyError(f"Swiggy's {tool.get('name')} needs {', '.join(missing)}, which SmartPlate doesn't "
                          "know how to fill yet.")
    return args


def _cart_item(tool: dict, item_id) -> dict:
    props = ((tool.get("input_schema") or {}).get("properties") or {})
    items_key = _find(props, "cart_items")
    item_props = (((props.get(items_key) or {}).get("items") or {}).get("properties") or {}) if items_key else {}
    id_key = _find(item_props, "item_id") or "menu_item_id"
    qty_key = _find(item_props, "quantity") or "quantity"
    return {id_key: item_id, qty_key: 1}


# --------------------------------------------------------------------------- #
# Reading replies
# --------------------------------------------------------------------------- #
def _get(record: dict, field: str):
    lowered = {k.lower(): k for k in record}
    for alias in FIELDS[field]:
        if alias.lower() in lowered:
            value = record[lowered[alias.lower()]]
            if value not in (None, ""):
                return value
    return None


def _flag(value) -> bool | None:
    if isinstance(value, str):
        value = value.strip().lower()
    if value in (True, 1, "1", "true"):
        return True
    if value in (False, 0, "0", "false"):
        return False
    return None


# Dish photos: only Swiggy's own image CDN over HTTPS, so the page's CSP can allow exactly
# that host (smartplate/app.py). get_restaurant_menu omits images by design (Swiggy docs);
# search_menu returns `imageUrl`. Anything else is dropped, never proxied or guessed.
IMAGE_HOSTS = ("media-assets.swiggy.com",)


def _image(record: dict) -> str | None:
    from urllib.parse import urlsplit
    for key in ("imageUrl", "image_url", "image"):
        value = record.get(key) if isinstance(record, dict) else None
        if isinstance(value, str) and len(value) <= 600:
            parts = urlsplit(value.strip())
            if parts.scheme == "https" and parts.hostname in IMAGE_HOSTS and not parts.username:
                return value.strip()
    return None


def _categories(record: dict) -> list[str]:
    cats = record.get("categories") if isinstance(record, dict) else None
    if isinstance(cats, str):
        cats = [cats]
    if not isinstance(cats, list):
        return []
    return [str(c)[:80] for c in cats if isinstance(c, (str, int)) and str(c).strip()][:6]


def _name_allergens(name: str, veg) -> list[str]:
    """Allergens a dish *name* implies (live_catalog's templates). Swiggy menus list no
    allergens, so this can flag a likely allergen but never establish that a dish is free of one."""
    from ..domain import live_catalog
    est = live_catalog.estimate(name, veg)
    return list(est["allergens"]) if est else []


def _lists(value, depth=0):
    if depth > 6:
        return
    if isinstance(value, list):
        if value and all(isinstance(v, dict) for v in value):
            yield value
        for v in value:
            yield from _lists(v, depth + 1)
    elif isinstance(value, dict):
        for v in value.values():
            yield from _lists(v, depth + 1)


def records(data, *needs: str) -> list[dict]:
    """The largest list of objects in a reply whose members carry all `needs` fields."""
    best = []
    for rows in _lists(data):
        good = [r for r in rows if all(_get(r, f) is not None for f in needs)]
        if len(good) > len(best):
            best = good
    return best


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


def _num(data, field: str):
    """The shallowest numeric `field` anywhere in a reply (e.g. the cart's amount to pay)."""
    queue = [data]
    while queue:
        nxt = []
        for node in queue:
            if isinstance(node, dict):
                v = _get(node, field)
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    return float(v) if math.isfinite(v) else None
                if isinstance(v, str) and re.fullmatch(r"₹?\s*\d+(\.\d+)?", v.strip()):
                    return float(v.strip().lstrip("₹").strip())
                nxt += list(node.values())
            elif isinstance(node, list):
                nxt += node
        queue = nxt
    return None


def _has_options(item: dict) -> bool:
    """Variants or add-ons SmartPlate can't choose. A missing hasVariants/hasAddons flag
    is not an option by itself: search_menu returns the option lists when they exist."""
    return bool(item.get("variations") or item.get("variantsV2") or item.get("addons")
                or _flag(_get(item, "variants")) is True or _flag(_get(item, "addons")) is True)


def _similar(a: str, b: str) -> float:
    norm = lambda s: re.sub(r"[^a-z0-9 ]", "", (s or "").lower()).strip()
    a, b = norm(a), norm(b)
    return 1.0 if a and (a in b or b in a) else difflib.SequenceMatcher(None, a, b).ratio()


# --------------------------------------------------------------------------- #
# What the app uses
# --------------------------------------------------------------------------- #
def addresses(user_id: int, *, fresh: bool = False) -> list[dict]:
    conn = _conn(user_id)
    key = _token_key(conn["access_token"])
    with _cache_lock:
        cached = _address_cache.get(user_id)
    if not fresh and cached and cached[0] == key and clock.now() - cached[1] < ADDRESS_TTL:
        return [dict(a) for a in cached[2]]              # checkout re-reads addresses; reuse briefly (M-06)
    tool = _tool(conn, "get_addresses")
    rows, seen = [], set()
    for page in range(1, 21):
        data = call(user_id, "get_addresses", build_args(tool, {"page": page, "page_size": 10}))
        body = data.get("data", data) if isinstance(data, dict) else {}
        page_rows = body.get("addresses") if isinstance(body, dict) else None
        if not isinstance(page_rows, list):
            raise SwiggyError("Swiggy did not return a valid address list. Try again later.")
        for row in page_rows:
            if isinstance(row, dict) and _get(row, "id") is not None and str(_get(row, "id")) not in seen:
                seen.add(str(_get(row, "id")))
                rows.append(row)
        pagination = body.get("pagination") or {}
        if not isinstance(pagination, dict):
            raise SwiggyError("Swiggy returned invalid address pagination. Try again later.")
        if pagination.get("hasMore") is not True:
            break
        if not _find((tool.get("input_schema") or {}).get("properties") or {}, "page") or page == 20:
            raise SwiggyError("Swiggy's address pagination could not be completed. Choose an address in Swiggy.")
    out = [{"id": str(_get(r, "id")), "label": str(_get(r, "label") or "Address"),
            "text": str(_get(r, "text") or "")} for r in rows]
    if not out:
        raise SwiggyError("Swiggy didn't return any saved addresses. Add one in the Swiggy app first.")
    with _cache_lock:
        _address_cache[user_id] = (key, clock.now(), [dict(a) for a in out])
    return out


def _address_label(address: dict) -> str:
    return f"{address['label']} · {address['text']}"[:120]


def _forget_address_state(cur, user_id: int) -> None:
    """Everything SmartPlate read for one delivery address: menus, quotes, the prepared cart."""
    cur.execute("DELETE FROM swiggy_menus WHERE user_id=?", (user_id,))
    cur.execute("DELETE FROM swiggy_checkout_quotes WHERE user_id=?", (user_id,))
    cur.execute("DELETE FROM swiggy_cart_intents WHERE user_id=?", (user_id,))


def choose_address(user_id: int, address_id: str) -> dict:
    """Make one of the user's Swiggy addresses the one SmartPlate delivers to. Cart,
    live menus and planning all read this one address, so changing it drops what was
    read for the old one (including the live catalogue the planner uses)."""
    match = next((a for a in addresses(user_id, fresh=True) if a["id"] == str(address_id)), None)
    if not match:
        raise ValueError("That address isn't on your Swiggy account. Refresh addresses and choose again.")
    previous = _conn(user_id).get("address_id")
    with db.cursor() as cur:
        cur.execute("UPDATE swiggy_connections SET address_id=?, address_label=? WHERE user_id=?",
                    (match["id"], _address_label(match), user_id))
        _forget_address_state(cur, user_id)                                     # menus depend on the address
    if previous != match["id"]:
        from ..domain import live_catalog
        live_catalog.clear(user_id)          # never plan from another address's menus and prices
    return sc.status(user_id)


def refresh_addresses(user_id: int) -> dict:
    """Read Swiggy's saved addresses fresh (after the user added one in Swiggy).

    The chosen address is checked against the fresh list: if Swiggy no longer has it,
    SmartPlate forgets it instead of using a stale default; if its text changed, the
    label is updated."""
    rows = addresses(user_id, fresh=True)
    conn = _conn(user_id)
    chosen, dropped = conn.get("address_id"), None
    match = next((a for a in rows if a["id"] == str(chosen)), None) if chosen else None
    with db.cursor() as cur:
        if chosen and not match:
            dropped = conn.get("address_label") or str(chosen)
            cur.execute("UPDATE swiggy_connections SET address_id=NULL, address_label=NULL WHERE user_id=?",
                        (user_id,))
            _forget_address_state(cur, user_id)
        elif match and _address_label(match) != conn.get("address_label"):
            cur.execute("UPDATE swiggy_connections SET address_label=? WHERE user_id=?",
                        (_address_label(match), user_id))
    if dropped:
        from ..domain import live_catalog
        live_catalog.clear(user_id)
    current = None if dropped else chosen
    return {"addresses": [{**a, "chosen": a["id"] == str(current)} for a in rows],
            "dropped": dropped, "status": sc.status(user_id)}


def _known_address(user_id: int, address_id: str | None) -> str | None:
    """The user's own words for an address id Swiggy echoed, if it is one of theirs."""
    if not address_id:
        return None
    try:
        match = next((a for a in addresses(user_id) if a["id"] == str(address_id)), None)
    except SwiggyError:
        return None
    return _address_label(match) if match else None


def _address(conn: dict) -> str:
    if not conn.get("address_id"):
        raise SwiggyError("Choose a delivery address first (More → Swiggy connection).")
    return conn["address_id"]


def search_live_restaurants(user_id: int, query: str) -> dict:
    """Only provider results for the selected delivery address; never seed rows."""
    query = (query or "").strip()
    if len(query) < 2 or len(query) > 80:
        raise ValueError("Search for a restaurant or cuisine using 2–80 characters")
    conn = _conn(user_id)
    address_id = _address(conn)
    data = call(user_id, "search_restaurants", build_args(_tool(conn, "search_restaurants"),
                                                         {"query": query, "address": address_id}))
    body = data.get("data", data) if isinstance(data, dict) else {}
    rows = body.get("restaurants") if isinstance(body, dict) else None
    if not isinstance(rows, list):
        raise SwiggyError("Swiggy did not return a restaurant list. Refresh tools and try again.")
    found = []
    seen = set()
    for row in rows:
        if not isinstance(row, dict) or _get(row, "id") is None or _get(row, "name") is None:
            continue
        rid = str(_get(row, "id"))
        if rid in seen:
            continue
        seen.add(rid)
        found.append({"id": rid, "name": str(_get(row, "name")), "rating": _get(row, "rating"),
                      "eta": _get(row, "eta"), "area": _get(row, "area"),
                      "availability": _get(row, "availability"), "distance": _get(row, "distance")})
    return {"address": conn.get("address_label") or address_id, "address_id": address_id,
            "query": query, "restaurants": found}


def live_favourites(user_id: int) -> list[dict]:
    address_id = _address(_conn(user_id))
    with db.cursor() as cur:
        rows = cur.execute("SELECT restaurant_id, restaurant_name FROM swiggy_favourites "
                           "WHERE user_id=? AND address_id=? ORDER BY restaurant_name", (user_id, address_id)).fetchall()
    return [{"id": r["restaurant_id"], "name": r["restaurant_name"]} for r in rows]


def toggle_live_favourite(user_id: int, restaurant_id: str, restaurant_name: str) -> dict:
    if not restaurant_id or not restaurant_name:
        raise ValueError("Choose a restaurant from live Swiggy search")
    address_id = _address(_conn(user_id))
    with db.cursor() as cur:
        exists = cur.execute("SELECT 1 FROM swiggy_favourites WHERE user_id=? AND address_id=? AND restaurant_id=?",
                             (user_id, address_id, restaurant_id)).fetchone()
    if exists:
        with db.cursor() as cur:
            cur.execute("DELETE FROM swiggy_favourites WHERE user_id=? AND address_id=? AND restaurant_id=?",
                        (user_id, address_id, restaurant_id))
        return {"favourite": False, "restaurants": live_favourites(user_id)}
    place = live_menu(user_id, restaurant_id, restaurant_name)["restaurant"]
    with db.cursor() as cur:
        cur.execute("INSERT INTO swiggy_favourites(user_id, address_id, restaurant_id, restaurant_name) "
                    "VALUES (?,?,?,?)", (user_id, address_id, restaurant_id, place["name"]))
    return {"favourite": True, "restaurants": live_favourites(user_id)}


def live_menu(user_id: int, restaurant_id: str, restaurant_name: str) -> dict:
    """Browse the exact provider restaurant ID, with a fresh menu for this address."""
    if not restaurant_id or not restaurant_name:
        raise ValueError("Choose a restaurant from live Swiggy search")
    conn = _conn(user_id)
    data = call(user_id, "get_restaurant_menu", build_args(_tool(conn, "get_restaurant_menu"),
                    {"restaurant": restaurant_id, "address": _address(conn)}))
    from ..domain import models
    user = models.get_user(user_id)
    body = data.get("data", data) if isinstance(data, dict) else {}
    provider_restaurant = body.get("restaurant") if isinstance(body, dict) else None
    if (not isinstance(provider_restaurant, dict) or str(_get(provider_restaurant, "id")) != restaurant_id
            or not _get(provider_restaurant, "name")):
        raise SwiggyError("Swiggy did not verify this exact restaurant for your address. Search again.")
    if provider_restaurant.get("isOpen") is False:
        raise SwiggyError("This restaurant is closed right now. Search again later.")
    place = {"id": restaurant_id, "name": str(_get(provider_restaurant, "name")),
             "area": _get(provider_restaurant, "area"), "rating": _get(provider_restaurant, "rating")}
    items = []
    hidden = 0
    browse = body.get("items") if isinstance(body, dict) else None
    rows = browse if isinstance(browse, list) else records(data, "id", "name")
    unit = menu_unit(rows)
    for row in rows:
        if not isinstance(row, dict) or _get(row, "id") is None or _get(row, "name") is None:
            continue
        veg = _flag(_get(row, "veg"))
        if user["diet"] in ("veg", "vegan") and veg in (False, 0):
            hidden += 1
            continue
        name = str(_get(row, "name"))
        items.append({"id": str(_get(row, "id")), "name": name,
                      "price": rupees(row, unit), "price_estimated": price_estimated(row), "veg": veg,
                      "in_stock": _flag(_get(row, "stock")),
                      "has_options": _flag(_get(row, "variants")) is True or _flag(_get(row, "addons")) is True,
                      "categories": _categories(row), "bestseller": row.get("isBestseller") is True,
                      "image": _image(row), "name_allergens": _name_allergens(name, veg)})
    labels = body.get("categoryLabels") if isinstance(body, dict) else None
    categories = [str(c)[:80] for c in labels if isinstance(c, (str, int))] if isinstance(labels, list) else []
    for item in items:                                    # keep any label the items use, in order
        for c in item["categories"]:
            if c not in categories:
                categories.append(c)
    total = body.get("totalItems") if isinstance(body, dict) else None
    return {"restaurant": place, "address": conn.get("address_label") or _address(conn),
            "items": items, "categories": categories, "hidden_nonveg": hidden,
            "total_items": total if isinstance(total, int) and not isinstance(total, bool) else None,
            "truncated": bool(body.get("truncated")) if isinstance(body, dict) else False,
            "fetched": clock.now().isoformat(timespec="minutes")}


def search_live_dishes(user_id: int, restaurant_id: str, restaurant_name: str, query: str, offset=0) -> dict:
    """Search beyond the compact browse menu using real scoped provider results."""
    query = (query or "").strip()
    if not 2 <= len(query) <= 80:
        raise ValueError("Search for a dish using 2–80 characters")
    if not isinstance(offset, int) or isinstance(offset, bool) or not 0 <= offset <= 10000:
        raise ValueError("Invalid menu page")
    menu = live_menu(user_id, restaurant_id, restaurant_name)
    conn = _conn(user_id)
    tool = _tool(conn, "search_menu")
    props = (tool.get("input_schema") or {}).get("properties") or {}
    if not _find(props, "restaurant_scope") or (offset and not _find(props, "offset")):
        raise SwiggyError("Swiggy did not offer scoped menu pagination. Refresh tools or search in Swiggy.")
    from ..domain import models
    vegetarian = models.get_user(user_id)["diet"] in ("veg", "vegan")
    result = call(user_id, "search_menu", build_args(tool, {"query": query, "address": _address(conn),
                    "restaurant_scope": restaurant_id, "offset": offset, "veg_filter": 1 if vegetarian else 0}))
    body = result.get("data", result) if isinstance(result, dict) else {}
    rows = body.get("items") if isinstance(body, dict) else None
    if not isinstance(rows, list):
        raise SwiggyError("Swiggy did not return a dish list. Try again later.")
    items, seen, hidden = [], set(), 0
    unit = menu_unit(rows)
    for row in rows:
        if not isinstance(row, dict) or not _get(row, "menu_item_id") or not _get(row, "name"):
            continue
        if _get(row, "restaurant_id") is not None and str(_get(row, "restaurant_id")) != restaurant_id:
            continue
        veg = _flag(_get(row, "veg"))
        if vegetarian and veg is False:
            hidden += 1
            continue
        item_id = str(_get(row, "menu_item_id"))
        if item_id in seen:
            continue
        seen.add(item_id)
        name = str(_get(row, "name"))
        items.append({"id": item_id, "name": name, "price": rupees(row, unit),
                      "price_estimated": price_estimated(row), "veg": veg,
                      "in_stock": _flag(_get(row, "stock")), "has_options": _has_options(row),
                      "categories": _categories(row), "bestseller": row.get("isBestseller") is True,
                      "image": _image(row), "name_allergens": _name_allergens(name, veg)})
    more = body.get("hasMore") is True
    next_offset = body.get("nextOffset") if more else None
    if more and (not isinstance(next_offset, int) or isinstance(next_offset, bool) or not offset < next_offset <= 10000):
        raise SwiggyError("Swiggy returned unusable menu pagination. Search again later.")
    return {"restaurant": menu["restaurant"], "items": items, "query": query, "has_more": more,
            "next_offset": next_offset, "hidden_nonveg": hidden, "fetched": clock.now().isoformat(timespec="minutes")}


def find_restaurant(user_id: int, name: str) -> dict:
    conn = _conn(user_id)
    tool = _tool(conn, "search_restaurants")
    data = call(user_id, "search_restaurants", build_args(tool, {"query": name, "address": _address(conn)}))
    found = [{"id": _get(r, "id"), "name": str(_get(r, "name")), "rating": _get(r, "rating"),
              "eta": _get(r, "eta"), "area": _get(r, "area")} for r in records(data, "id", "name")]
    best = max(found, key=lambda r: _similar(name, r["name"]), default=None)
    if not best or _similar(name, best["name"]) < MATCH_RESTAURANT:
        raise SwiggyError(f"Couldn't find {name} on Swiggy for your address.")
    return best


def menu(user_id: int, restaurant: str, *, fresh: bool = False) -> dict:
    """The live menu of one of the person's places (cached for a few hours)."""
    if not fresh:
        with db.cursor() as cur:
            row = cur.execute("SELECT payload, fetched_ts FROM swiggy_menus WHERE user_id=? AND restaurant=?",
                              (user_id, restaurant)).fetchone()
        if row and dt.datetime.fromisoformat(row["fetched_ts"]) > clock.now() - MENU_TTL:
            return {**db.jl(row["payload"], {}), "cached": True}
    conn = _conn(user_id)
    place = find_restaurant(user_id, restaurant)
    tool = _tool(conn, "get_restaurant_menu")
    data = call(user_id, "get_restaurant_menu",
                build_args(tool, {"restaurant": place["id"], "address": _address(conn)}))
    items = []
    rows = records(data, "id", "name")[:150]
    unit = menu_unit(rows)
    for r in rows:
        veg = _get(r, "veg")
        items.append({"id": _get(r, "id"), "name": str(_get(r, "name")), "price": rupees(r, unit),
                      "price_estimated": price_estimated(r), "veg": bool(veg) if veg is not None else None})
    if not items:
        raise SwiggyError(f"Swiggy didn't return a menu for {place['name']} right now.")
    out = {"restaurant": restaurant, "swiggy": place, "items": items,
           "fetched": clock.now().isoformat(timespec="minutes")}
    with db.cursor() as cur:
        cur.execute("INSERT OR REPLACE INTO swiggy_menus(user_id, restaurant, payload, fetched_ts) VALUES (?,?,?,?)",
                    (user_id, restaurant, db.jd(out), clock.now().isoformat()))
    return {**out, "cached": False}


def menu_for(user_id: int, restaurant: str, *, fresh: bool = False) -> dict:
    """The live menu as this person should see it: non-veg dishes hidden for vegetarians
    and vegans when Swiggy marks them. Allergens aren't on Swiggy menus, so they can't
    be filtered here; the app says so."""
    from ..domain import models
    m = menu(user_id, restaurant, fresh=fresh)
    diet = (models.get_user(user_id) or {}).get("diet")
    items = [i for i in m["items"] if not (diet in ("veg", "vegan") and i["veg"] is False)]
    return {**m, "items": items, "hidden_nonveg": len(m["items"]) - len(items), "diet": diet}


def _planned(session_id: int) -> tuple[int, dict]:
    from .. import service
    from ..domain import models
    session = models.get_session(session_id)
    view = service.plan_view(session["plan_id"])
    cell = next((c for d in view["grid"] for c in d["meals"].values() if c.get("session_id") == session_id), None)
    if (not cell or cell.get("kind") != "delivery" or not cell.get("restaurant")
            or cell.get("status") != "active" or cell.get("past")):
        raise ValueError("Only an upcoming, active planned delivery can go into a Swiggy cart")
    return view["user"]["id"], cell


def _name(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", (value or "").lower())).strip()


def cart_preview(session_id: int) -> dict:
    """Read a fresh, exact, simple live item before the user approves a cart change.

    Provider menus do not establish allergen or medical safety, and a vegetarian
    mark does not establish vegan ingredients. Those profiles use manual hand-off.
    """
    user_id, cell = _planned(session_id)
    from ..domain import models
    user = models.get_user(user_id)
    if user["allergens"] or user["medical"] or user["diet"] == "vegan":
        raise SwiggyError("SmartPlate cannot verify your ingredient or medical rules from Swiggy's menu. "
                          "Open Swiggy and confirm the dish with the restaurant before ordering.")
    conn = _conn(user_id)
    address_id = _address(conn)
    place = find_restaurant(user_id, cell["restaurant"])
    tool = _tool(conn, "search_menu")
    data = call(user_id, "search_menu", build_args(tool, {
        "query": cell["item"], "address": address_id, "restaurant_scope": place["id"]}))
    exact = [r for r in records(data, "menu_item_id", "name") if _name(str(_get(r, "name"))) == _name(cell["item"])
             and (_get(r, "restaurant_id") is None or str(_get(r, "restaurant_id")) == str(place["id"]))]
    if len(exact) != 1:
        raise SwiggyError(f"Could not verify one exact live match for {cell['item']} at {place['name']}. "
                          "Open Swiggy to choose the right dish.")
    item = exact[0]
    if _get(item, "stock") not in (True, 1):
        raise SwiggyError("Swiggy did not confirm this dish is in stock. Check it in Swiggy before ordering.")
    if _has_options(item):
        raise SwiggyError("This dish needs options or add-ons SmartPlate cannot safely choose. "
                          "Customize it in Swiggy instead.")
    if user["diet"] == "veg" and _get(item, "veg") not in (True, 1):
        raise SwiggyError("Swiggy did not verify this dish as vegetarian. Check it in Swiggy before ordering.")
    item_id = _get(item, "menu_item_id")
    details = {"session_id": session_id, "planned": cell["item"], "planned_restaurant": cell["restaurant"],
               "planned_cost": cell.get("planned_cost", cell["cost"]), "restaurant": place["name"], "restaurant_id": place["id"],
               "item": str(_get(item, "name")), "item_id": item_id, "address_id": address_id}
    fingerprint = hashlib.sha256(json.dumps({**details, "provider_price": _get(item, "price")},
                                            sort_keys=True).encode()).hexdigest()
    return {**details, "address": conn.get("address_label") or "Selected Swiggy address",
            "menu_price": rupees(item, menu_unit(records(data, "menu_item_id", "name"))),
            "price_estimated": price_estimated(item), "image": _image(item), "fingerprint": fingerprint}


def fill_cart(session_id: int, expected_fingerprint: str | None = None) -> dict:
    """Add exactly the item the user reviewed, then read the provider's current cart."""
    if not expected_fingerprint:
        raise CartChanged("Review the live Swiggy item before adding it to your cart.")
    preview = cart_preview(session_id)  # fresh provider lookup: no six-hour browse cache
    if preview["fingerprint"] != expected_fingerprint:
        raise CartChanged("The live Swiggy item or your plan changed. Review it again before adding to cart.")
    user_id, cell = _planned(session_id)
    prepared = _fill_reviewed_cart(user_id, preview)
    to_pay = prepared["to_pay"]
    planned = cell.get("planned_cost", cell["cost"])
    return {"session_id": session_id, "restaurant": preview["restaurant"], "item": preview["item"],
            "planned": cell["item"], "planned_cost": planned, "menu_price": preview["menu_price"],
            "to_pay": to_pay, "over_plan": round(to_pay - planned, 2) if to_pay is not None else None,
            "bill": prepared.get("bill"), "checkout_url": CHECKOUT_URL,
            "cancellation_note": prepared.get("cancellation_note")}


SAFETY_NOTE = ("SmartPlate cannot verify your ingredient or medical rules from Swiggy's menu, so it won't add "
               "this to your cart. Check the dish with the restaurant and order it directly in Swiggy.")


def _safety_blocked(user: dict) -> bool:
    """A live dish picked from a menu is for this person; the shared household plan is
    made safe for every member by the planner (allergens.violates)."""
    return bool(user["allergens"] or user["medical"] or user["diet"] == "vegan")


def live_cart_preview(user_id: int, restaurant_id: str, restaurant_name: str,
                      item_id: str, item_name: str) -> dict:
    """Review an item chosen from a real menu, without a seeded plan or fuzzy match.

    Always answers with a review (dish, current price, address). For profiles whose
    allergy / medical / vegan rules SmartPlate can't verify from a menu, the review is
    `orderable: False` with a Swiggy hand-off instead of an error, so the Review button
    never dead-ends."""
    from ..domain import models, timing
    user = models.get_user(user_id)
    if not user:
        raise ValueError("Profile not found")
    place = live_menu(user_id, restaurant_id, restaurant_name)["restaurant"]
    conn = _conn(user_id)
    address_id = _address(conn)
    tool = _tool(conn, "search_menu")
    exact, offset, seen_offsets, rows = [], 0, set(), []
    by_name, name_rows = {}, []
    here = lambda r: _get(r, "restaurant_id") is None or str(_get(r, "restaurant_id")) == str(restaurant_id)
    for _ in range(20):
        data = call(user_id, "search_menu", build_args(tool,
                    {"query": item_name, "address": address_id, "restaurant_scope": restaurant_id,
                     "offset": offset}))
        rows = records(data, "menu_item_id", "name")
        exact = [r for r in rows
                 if str(_get(r, "menu_item_id")) == str(item_id)
                 and _name(str(_get(r, "name"))) == _name(item_name) and here(r)]
        for r in rows:
            # get_restaurant_menu's compact `id` is not documented to be the same id space
            # as search_menu's `menu_item_id` (the one update_food_cart takes). Keep the
            # exact-name dishes of this restaurant in case the browse id is not found.
            if _name(str(_get(r, "name"))) == _name(item_name) and here(r):
                by_name.setdefault(str(_get(r, "menu_item_id")), r)
                name_rows = rows
        body = data.get("data", data) if isinstance(data, dict) else {}
        if exact or body.get("hasMore") is not True:
            break
        next_offset = body.get("nextOffset")
        if (not isinstance(next_offset, int) or isinstance(next_offset, bool) or next_offset <= offset
                or next_offset in seen_offsets or not _find((tool.get("input_schema") or {}).get("properties") or {}, "offset")):
            raise SwiggyError("Swiggy did not return a usable menu page. Refresh or choose the dish in Swiggy.")
        seen_offsets.add(offset)
        offset = next_offset
    matched_by = "id"
    if len(exact) != 1 and len(by_name) == 1:
        # One dish of exactly this name at this restaurant: that is the dish the person
        # chose, under the id Swiggy's cart accepts. The review shows it before any add.
        exact, rows, matched_by = list(by_name.values()), name_rows, "name"
    if len(exact) != 1:
        raise SwiggyError(f"Swiggy's menu search did not return exactly one “{item_name}” at {place['name']}. "
                          "Refresh the menu, or choose the dish in Swiggy.", code="swiggy_item_unmatched")
    item = exact[0]
    _note_sample(user_id, "search_menu.item_match", {"by": matched_by})
    details = {"user_id": user_id, "address_id": address_id, "restaurant_id": restaurant_id,
               "restaurant": place["name"], "item_id": str(_get(item, "menu_item_id")),
               "item": str(_get(item, "name"))}
    review = {**details, "address": conn.get("address_label") or address_id,
              "menu_price": rupees(item, menu_unit(rows)), "price_estimated": price_estimated(item),
              "image": _image(item), "handoff_url": timing.swiggy_handoff(place["name"], str(_get(item, "name")))}
    blocked = None
    if _safety_blocked(user):
        blocked = SAFETY_NOTE
    elif _get(item, "stock") not in (True, 1):
        blocked = "Swiggy did not confirm this dish is in stock. Refresh the menu or check it in Swiggy."
    elif _has_options(item):
        blocked = "This dish needs options that SmartPlate cannot choose yet. Customize it in Swiggy."
    elif user["diet"] == "veg" and _get(item, "veg") not in (True, 1):
        blocked = "Swiggy did not verify this dish as vegetarian. Check it in Swiggy."
    if blocked:
        return {**review, "orderable": False, "reason": blocked, "fingerprint": None}
    fingerprint = hashlib.sha256(json.dumps({**details, "provider_price": _get(item, "price")},
                                            sort_keys=True).encode()).hexdigest()
    return {**review, "orderable": True, "reason": None, "fingerprint": fingerprint}


def _unwrap(data: object) -> object:
    """Strip the documented {success, data, message} envelope (once)."""
    if isinstance(data, dict) and "data" in data and set(data) <= {"success", "data", "message", "error"}:
        return data["data"]
    return data


def _cart_view(data: object, address_id: str) -> dict:
    """Read the documented cart envelope `{data: {data: cart, addressId}}`.

    An empty cart may come back with no inner `data`/`items` at all; that is an empty
    cart, not an unverified one. A reply for a *different* address, or one whose items
    aren't readable, is never treated as empty. `address_verified` says whether Swiggy
    echoed the address (required before placing an order)."""
    body = _unwrap(data)
    if isinstance(body, dict) and set(body) == {"text"}:
        text = str(body["text"]).lower()
        if "empty" in text:
            return {"items": [], "address_verified": False, "cart_address_id": None, "empty_confirmed": False}
        raise SwiggyError("Swiggy did not verify the cart and delivery address. Check your cart in Swiggy.")
    if not isinstance(body, dict):
        raise SwiggyError("Swiggy did not verify the cart and delivery address. Check your cart in Swiggy.")
    inner = body.get("data") if "data" in body else body.get("cart")
    if inner is None and "items" in body:
        inner = body                                   # the cart itself, without the outer envelope
    echoed = body.get("addressId")
    if echoed is None and isinstance(inner, dict):
        echoed = inner.get("addressId") or inner.get("address_id")
    # Swiggy keeps one cart per account and echoes the address that cart is for. A cart
    # for another address is reported as such (`cart_address_id`), never as an error and
    # never as verified for the chosen address.
    other = echoed is not None and str(echoed) != str(address_id)
    verified = echoed is not None and not other
    where = {"address_verified": verified, "cart_address_id": str(echoed) if other else None,
             "empty_confirmed": False}
    if inner is None or inner == {}:
        return {"items": [], **where, "empty_confirmed": echoed is not None}
    if not isinstance(inner, dict):
        raise SwiggyError("Swiggy did not verify the cart and delivery address. Check your cart in Swiggy.")
    items = inner.get("items")
    if items is None:
        if inner.get("item_count") not in (None, 0):
            raise SwiggyError("Swiggy did not return the cart's items. Check your cart in Swiggy.")
        items = []
    if not isinstance(items, list) or not all(isinstance(i, dict) for i in items):
        raise SwiggyError("Swiggy did not verify the cart and delivery address. Check your cart in Swiggy.")
    return {**inner, "items": items, **where, "empty_confirmed": echoed is not None and not items}


def _read_cart(user_id: int, data: object, address_id: str) -> dict:
    """`_cart_view` plus what the echoed address means.

    Live finding (Oct 8, 2026): after `update_food_cart` with the chosen address, Swiggy's
    checkout showed the cart at that address, yet `get_food_cart` echoed an `addressId`
    that is none of the ids `get_addresses` returns. The echo is therefore not always in
    the same id space as the address list. Only an echo that is one of the user's *other*
    listed addresses means the cart is for another address. An echo outside the list
    proves nothing either way: the cart is read for the chosen address (Swiggy prices
    delivery from the `addressId` passed to `get_food_cart`), but it stays unverified, so
    placement still refuses it."""
    cart = _cart_view(data, address_id)
    echo = cart["cart_address_id"]
    kind = "absent" if not (cart["address_verified"] or echo or cart["empty_confirmed"]) else (
        "chosen" if cart["address_verified"] else None)
    if echo:
        known = _known_address(user_id, echo)
        kind = "listed_other" if known else "unlisted"
        if known:
            cart["cart_address_label"] = known
        else:
            cart["cart_address_id"] = None
    _note_echo(user_id, kind)
    return cart


def _note_sample(user_id: int, key: str, value: dict) -> None:
    """Keep a small fact about how a real reply was read (no values) beside the reply shapes."""
    try:
        conn = sc._connection(user_id)
        samples = db.jl(conn.get("samples"), {}) if conn else {}
        samples[key] = {**value, "at": clock.now().isoformat(timespec="seconds")}
        with db.cursor() as cur:
            cur.execute("UPDATE swiggy_connections SET samples=? WHERE user_id=?", (db.jd(samples), user_id))
    except Exception:
        logging.getLogger(__name__).warning("Could not record %s", key)


def _note_echo(user_id: int, kind: str | None) -> None:
    """Keep which kind of address echo the cart last returned (no values) beside the reply
    shapes, so a real run shows how Swiggy's cart address relates to the address list."""
    if not kind:
        return
    try:
        conn = sc._connection(user_id)
        samples = db.jl(conn.get("samples"), {}) if conn else {}
        samples["get_food_cart.address_echo"] = {"kind": kind, "at": clock.now().isoformat(timespec="seconds")}
        with db.cursor() as cur:
            cur.execute("UPDATE swiggy_connections SET samples=? WHERE user_id=?", (db.jd(samples), user_id))
    except Exception:
        logging.getLogger(__name__).warning("Could not record the cart's address echo")


def _other_address_error(cart: dict, conn: dict, what: str) -> SwiggyError:
    chosen = conn.get("address_label") or "your chosen address"
    return SwiggyError(f"{what} is for your {cart['cart_address_label']} address, not {chosen}. "
                       "Choose that address here, or clear the cart in Swiggy.", code="swiggy_cart_other_address")


def _one(value) -> bool:
    """Quantity 1 however Swiggy spells it (1, 1.0 or "1")."""
    if isinstance(value, bool):
        return False
    try:
        return float(value) == 1
    except (TypeError, ValueError):
        return False


def _cart_mismatch(cart: dict, restaurant_id: str, item_id: str) -> str | None:
    """Which part of the cart is not exactly the one reviewed dish, or None.

    Swiggy's cart may leave out the restaurant ("the cart API does not always return it",
    get_food_cart reference). Its absence proves nothing: the dish id is restaurant-scoped
    and the cart holds one restaurant. A restaurant id that is present must match."""
    items = cart["items"]
    if len(items) != 1:
        return "dishes"
    if str(_get(items[0], "menu_item_id")) != str(item_id):
        return "dish"
    if not _one(items[0].get("quantity")):
        return "quantity"
    restaurant = cart.get("restaurant")
    rid = restaurant.get("id") if isinstance(restaurant, dict) else None
    if rid not in (None, "") and str(rid) != str(restaurant_id):
        return "restaurant"
    return None


def _note_confirm(user_id: int, part: str | None) -> None:
    """Keep which part of a cart could not be confirmed (no values) beside the reply shapes."""
    try:
        conn = sc._connection(user_id)
        samples = db.jl(conn.get("samples"), {}) if conn else {}
        samples["get_food_cart.confirm"] = {"unconfirmed": part, "at": clock.now().isoformat(timespec="seconds")}
        with db.cursor() as cur:
            cur.execute("UPDATE swiggy_connections SET samples=? WHERE user_id=?", (db.jd(samples), user_id))
    except Exception:
        logging.getLogger(__name__).warning("Could not record the cart confirmation result")


CONFIRM_WORDS = {"dishes": "single dish", "dish": "dish", "quantity": "quantity of 1", "restaurant": "restaurant"}


def _intent(user_id: int) -> dict | None:
    with db.cursor() as cur:
        row = cur.execute("SELECT * FROM swiggy_cart_intents WHERE user_id=?", (user_id,)).fetchone()
    return dict(row) if row else None


def _prepared_cart(user_id: int) -> tuple[dict, dict, dict]:
    conn = _conn(user_id)
    address_id = _address(conn)
    intent = _intent(user_id)
    data = call(user_id, "get_food_cart", build_args(_tool(conn, "get_food_cart"),
                {"address": address_id, "restaurant_name": intent["restaurant_name"] if intent else None}))
    cart = _read_cart(user_id, data, address_id)
    if cart["cart_address_id"]:
        raise _other_address_error(cart, conn, "Your Swiggy cart")
    if (not intent or intent["address_id"] != address_id
            or _cart_mismatch(cart, intent["restaurant_id"], intent["item_id"])):
        raise SwiggyError("This cart differs from the item SmartPlate prepared. Review or clear it in Swiggy, "
                          "then select and review an item here again.")
    return conn, intent, cart


def current_live_cart(user_id: int) -> dict:
    conn = _conn(user_id)
    address_id = _address(conn)
    intent = _intent(user_id)
    data = call(user_id, "get_food_cart", build_args(_tool(conn, "get_food_cart"),
                {"address": address_id, "restaurant_name": intent["restaurant_name"] if intent else None}))
    cart = _read_cart(user_id, data, address_id)
    if not cart["items"]:
        # An empty cart is an answer, not an error: say so instead of a stale warning. An
        # empty cart Swiggy last tied to another address is still empty; adding an item
        # here sends the chosen address.
        return {"cart": None, "empty": True,
                "address_verified": cart["address_verified"] or cart["empty_confirmed"],
                "address": conn.get("address_label") or address_id}
    other = ({"id": cart["cart_address_id"], "label": cart["cart_address_label"]}
             if cart["cart_address_id"] else None)
    restaurant = cart.get("restaurant") or {}
    prepared = bool(not other and intent and intent["address_id"] == address_id
                    and not _cart_mismatch(cart, intent["restaurant_id"], intent["item_id"]))
    name = restaurant.get("name") if isinstance(restaurant, dict) else None
    anchor = intent.get("menu_price") if prepared and intent else None
    return {"cart": {"item": ", ".join(str(_get(i, "name") or "Unnamed item") for i in cart["items"]),
                     "restaurant": name or (intent["restaurant_name"] if prepared else "Check restaurant in Swiggy"),
                     "to_pay": cart_total(cart, anchor),
                     "bill": bill_breakdown(cart, anchor),
                     "cancellation_note": cancellation_note(data),
                     "orderable": prepared,
                     "checkout_url": CHECKOUT_URL,
                     "other_address": other},
            "address": conn.get("address_label") or address_id}


def fill_live_cart(user_id: int, restaurant_id: str, restaurant_name: str,
                   item_id: str, item_name: str, expected_fingerprint: str | None) -> dict:
    if not expected_fingerprint:
        raise CartChanged("Review the exact live Swiggy item before adding it to your cart.")
    preview = live_cart_preview(user_id, restaurant_id, restaurant_name, item_id, item_name)
    if not preview["orderable"]:
        raise SwiggyError(preview["reason"])
    if preview["fingerprint"] != expected_fingerprint:
        raise CartChanged("The live item, restaurant or address changed. Review it again.")
    return _fill_reviewed_cart(user_id, preview)


def _fill_reviewed_cart(user_id: int, preview: dict) -> dict:
    """Both legacy and live selection paths respect and verify the existing cart."""
    restaurant_id = preview["restaurant_id"]
    conn = _conn(user_id)
    current = call(user_id, "get_food_cart", build_args(_tool(conn, "get_food_cart"),
                                                       {"address": preview["address_id"]}))
    before = _read_cart(user_id, current, preview["address_id"])
    if before["items"]:
        if before["cart_address_id"]:
            raise _other_address_error(before, conn, "Your Swiggy cart already has items and")
        raise SwiggyError("Your Swiggy cart already has items. Review or clear it in Swiggy before starting a new order.")
    # An unconfirmed "empty" reply is never a safe base to add to. A structured empty
    # cart is, whichever address Swiggy last tied it to: the update below sends the
    # chosen address, and the bill is accepted only if Swiggy echoes that address back.
    if not (before["address_verified"] or before["empty_confirmed"]):
        raise SwiggyError("Swiggy did not verify the cart and delivery address. Check your cart in Swiggy.")
    tool = _tool(conn, "update_food_cart")
    call(user_id, "update_food_cart", build_args(tool, {
        "cart_items": [_cart_item(tool, preview["item_id"])], "restaurant": restaurant_id,
        "address": preview["address_id"], "restaurant_name": preview["restaurant"]}))
    cart = call(user_id, "get_food_cart", build_args(_tool(conn, "get_food_cart"),
                         {"address": preview["address_id"], "restaurant_name": preview["restaurant"]}))
    view = _read_cart(user_id, cart, preview["address_id"])
    if view["cart_address_id"]:
        raise _other_address_error(view, conn, "Swiggy added the item, but the cart")
    part = _cart_mismatch(view, restaurant_id, preview["item_id"])
    _note_confirm(user_id, part)
    if part:
        raise SwiggyError(f"SmartPlate could not confirm the {CONFIRM_WORDS[part]} in Swiggy's cart. "
                          "The item may be in your Swiggy cart: check it there before trying again.")
    with db.cursor() as cur:
        cur.execute("INSERT OR REPLACE INTO swiggy_cart_intents(user_id, address_id, restaurant_id, restaurant_name, "
                    "item_id, menu_price) VALUES (?,?,?,?,?,?)",
                    (user_id, preview["address_id"], restaurant_id, preview["restaurant"], preview["item_id"],
                     preview.get("menu_price")))
    # The bill's delivery line replaces the planner's flat estimate for this restaurant.
    from ..domain import live_catalog
    live_catalog.record_fee(user_id, preview["address_id"], restaurant_id, preview["restaurant"],
                            billed_delivery_fee(view, preview.get("menu_price")))
    return {**preview, "to_pay": cart_total(view, preview.get("menu_price")), "checkout_url": CHECKOUT_URL,
            "bill": bill_breakdown(view, preview.get("menu_price")), "orderable": True,
            "cancellation_note": cancellation_note(cart)}


def _checkout_state(user_id: int) -> dict:
    """Fresh cart, exact payable total and provider-offered COD before placement."""
    if not config.LIVE_ORDERS:
        raise SwiggyError("Real order placement is disabled until Swiggy access and durable storage are approved.")
    from ..domain import models
    user = models.get_user(user_id)
    if user["allergens"] or user["medical"] or user["diet"] == "vegan":
        raise SwiggyError("SmartPlate cannot verify your ingredient or medical rules for a real order. "
                          "Review and place it in Swiggy instead.")
    conn, intent, cart = _prepared_cart(user_id)
    address_id = _address(conn)
    items = cart["items"]
    if len(items) != 1:
        raise SwiggyError("Review one exact dish in the Swiggy cart before placing an order.")
    item = items[0]
    if not _one(item.get("quantity")) or not _get(item, "name"):
        raise SwiggyError("SmartPlate could not verify one dish and quantity in the live cart.")
    if _flag(_get(item, "stock")) is False:
        raise SwiggyError("The dish is no longer in stock. Refresh your cart.")
    if user["diet"] == "veg" and _flag(_get(item, "veg")) is not True:
        raise SwiggyError("Swiggy did not verify the cart dish as vegetarian. Check it in Swiggy.")
    if item.get("variants") or item.get("addons") or item.get("variations") or item.get("variantsV2"):
        raise SwiggyError("The cart has customizations that SmartPlate did not review. Check it in Swiggy.")
    if not cart.get("address_verified"):
        raise SwiggyError("Swiggy did not confirm the cart's delivery address. Refresh your cart or check it in Swiggy.")
    total = cart_total(cart, intent.get("menu_price"))
    if total is None or total <= 0 or total > 1000:
        raise SwiggyError("Swiggy did not return a valid payable total within its ₹1,000 Builders Club limit.")
    options = call(user_id, "get_payment_options", build_args(_tool(conn, "get_payment_options"),
                                                        {"address": address_id}))
    body = options.get("data", options) if isinstance(options, dict) else {}
    cod = body.get("cod") if isinstance(body, dict) else None
    if not isinstance(cod, dict) or cod.get("available") is not True or not cod.get("id"):
        raise SwiggyError("Cash on Delivery is not offered for this cart. Use Swiggy checkout for another payment method.")
    address = next((a for a in addresses(user_id) if a["id"] == address_id), None)
    if not address or not address["text"]:
        raise SwiggyError("Swiggy did not confirm your delivery address. Choose it again.")
    details = {"address_id": address_id, "address": address["text"],
               "restaurant_id": intent["restaurant_id"], "restaurant": intent["restaurant_name"],
               "item_id": str(_get(item, "menu_item_id")), "item": str(_get(item, "name") or "Selected dish"),
               "quantity": item.get("quantity"), "to_pay": total, "payment_method": str(cod["id"])}
    fingerprint = hashlib.sha256(json.dumps(details, sort_keys=True).encode()).hexdigest()
    return {**details, "payment_label": str(cod.get("displayName") or "Cash on Delivery"),
            "bill": bill_breakdown(cart, intent.get("menu_price")), "fingerprint": fingerprint}


def live_checkout_preview(user_id: int) -> dict:
    """Issue a short-lived approval for this exact live cart.

    An uncertain attempt blocks further placement even if address or price changes.
    Only the latest approval for this profile can be submitted.
    """
    preview = _checkout_state(user_id)
    reconcile_attempts(user_id)
    with db.cursor() as cur:
        unresolved = cur.execute("SELECT 1 FROM swiggy_order_attempts WHERE user_id=? "
                                 "AND state IN ('started','unknown') LIMIT 1", (user_id,)).fetchone()
        if unresolved:
            raise SwiggyError("An earlier order attempt is unresolved. Check your Swiggy orders "
                              "before placing it again.")
        cur.execute("DELETE FROM swiggy_checkout_quotes WHERE created_ts < ?",
                    ((clock.now() - CHECKOUT_QUOTE_TTL).isoformat(),))
        cur.execute("DELETE FROM swiggy_checkout_quotes WHERE user_id=?", (user_id,))
        approval = secrets.token_urlsafe(32)
        cur.execute("INSERT INTO swiggy_checkout_quotes(token, user_id, cart_fingerprint, created_ts) "
                    "VALUES (?,?,?,?)", (approval, user_id, preview["fingerprint"], clock.now().isoformat()))
    return {**preview, "fingerprint": approval}


def place_live_order(user_id: int, expected_fingerprint: str | None) -> dict:
    # the approval is the opaque string checkout_preview returned; anything else (a number
    # too big for SQLite, a list) is simply not that approval, never a server error
    if not isinstance(expected_fingerprint, str) or not expected_fingerprint:
        raise CartChanged("Review the current Swiggy cart, address, total and payment method first.")
    with db.cursor() as cur:
        quote = cur.execute("SELECT cart_fingerprint, created_ts FROM swiggy_checkout_quotes "
                            "WHERE user_id=? AND token=?", (user_id, expected_fingerprint)).fetchone()
    if not quote or dt.datetime.fromisoformat(quote["created_ts"]) < clock.now() - CHECKOUT_QUOTE_TTL:
        raise CartChanged("This checkout approval expired or was replaced. Review the cart again.")
    preview = _checkout_state(user_id)
    if quote["cart_fingerprint"] != preview["fingerprint"]:
        raise CartChanged("The Swiggy cart, address, total or payment method changed. Review it again.")
    with db.cursor() as cur:
        cur.execute("BEGIN IMMEDIATE")
        current_quote = cur.execute("SELECT cart_fingerprint, created_ts FROM swiggy_checkout_quotes "
                                    "WHERE user_id=? AND token=?", (user_id, expected_fingerprint)).fetchone()
        if (not current_quote or current_quote["cart_fingerprint"] != preview["fingerprint"]
                or dt.datetime.fromisoformat(current_quote["created_ts"]) < clock.now() - CHECKOUT_QUOTE_TTL):
            raise CartChanged("This checkout approval expired or was replaced. Review the cart again.")
        existing = cur.execute("SELECT state, order_id FROM swiggy_order_attempts WHERE user_id=? AND fingerprint=?",
                               (user_id, expected_fingerprint)).fetchone()
        if existing:
            raise CartChanged("This order was already attempted. Check your Swiggy orders before trying again.")
        unresolved = cur.execute("SELECT 1 FROM swiggy_order_attempts WHERE user_id=? "
                                 "AND state IN ('started','unknown') LIMIT 1", (user_id,)).fetchone()
        if unresolved:
            raise CartChanged("An earlier order attempt is unresolved. Check Swiggy orders first.")
        cur.execute("INSERT INTO swiggy_order_attempts(user_id, fingerprint, cart_fingerprint, "
                    "address_id, state, created_ts, to_pay) VALUES (?,?,?,?,?,?,?)",
                    (user_id, expected_fingerprint, preview["fingerprint"], preview["address_id"],
                     "started", clock.now().isoformat(), preview["to_pay"]))
        cur.execute("DELETE FROM swiggy_checkout_quotes WHERE user_id=?", (user_id,))
    try:
        conn = _conn(user_id)             # inside the try: an expired token here is "not sent" (M-01)
        result = call(user_id, "place_food_order", build_args(_tool(conn, "place_food_order"),
                {"address": preview["address_id"], "payment_method": preview["payment_method"]}))
        body = result.get("data", result) if isinstance(result, dict) else {}
        order_id = body.get("orderId") if isinstance(body, dict) else None
        confirmed = (isinstance(body, dict) and body.get("normalizedStatus") == "success"
                     and isinstance(order_id, str) and bool(order_id.strip())
                     and str(body.get("status", "")).upper() not in
                     ("PENDING_PAYMENT", "UNKNOWN", "FAILED", "FAILURE", "CANCELLED"))
        with db.cursor() as cur:
            cur.execute("UPDATE swiggy_order_attempts SET state=?, order_id=? WHERE user_id=? AND fingerprint=?",
                        ("confirmed" if confirmed else "unknown", str(order_id) if order_id else None,
                         user_id, expected_fingerprint))
            if confirmed:
                cur.execute("DELETE FROM swiggy_cart_intents WHERE user_id=?", (user_id,))
        if not confirmed:
            raise SwiggyError("Swiggy did not confirm a completed order. Check Swiggy orders; do not retry this cart.")
        return {"order_id": str(order_id), "status": "confirmed", "item": preview["item"],
                "to_pay": preview["to_pay"], "address": preview["address"],
                "message": str(result.get("message") or "")}
    except NotSent:
        # The order request never reached Swiggy (or was refused unprocessed): nothing was
        # placed, so this attempt must not block the next review (M-01).
        with db.cursor() as cur:
            cur.execute("UPDATE swiggy_order_attempts SET state='failed', resolved_ts=? "
                        "WHERE user_id=? AND fingerprint=? AND state='started'",
                        (clock.now().isoformat(), user_id, expected_fingerprint))
        raise
    except Exception:
        with db.cursor() as cur:
            cur.execute("UPDATE swiggy_order_attempts SET state='unknown' "
                        "WHERE user_id=? AND fingerprint=? AND state='started'", (user_id, expected_fingerprint))
        raise


def live_order_status(user_id: int, order_id: str) -> dict:
    with db.cursor() as cur:
        known = cur.execute("SELECT 1 FROM swiggy_order_attempts WHERE user_id=? AND order_id=?",
                            (user_id, order_id)).fetchone()
    if not known:
        raise ValueError("This order is not recorded for this profile")
    conn = _conn(user_id)
    data = call(user_id, "track_food_order", build_args(_tool(conn, "track_food_order"),
                                                       {"order_id": order_id, "address": _address(conn)}))
    body = data.get("data", data) if isinstance(data, dict) else {}
    rows = body.get("orders") if isinstance(body, dict) else None
    if not isinstance(rows, list):
        raise SwiggyError("Swiggy did not return order tracking. Check delivery status in Swiggy.")
    order = next((row for row in rows if isinstance(row, dict) and str(row.get("orderId")) == order_id), None)
    return {"order_id": order_id, "tracking": {
        "status": str(order.get("orderStatus") or "") if order else "",
        "title": str(order.get("title") or "") if order else "",
        "subtitle": str(order.get("subtitle") or "") if order else "",
        "eta": str(order.get("etaText") or "") if order else "",
        "message": str(body.get("statusMessage") or "")}}


def live_order_history(user_id: int) -> dict:
    """Show local attempts beside Swiggy's recent orders to recover uncertainty."""
    conn = _conn(user_id)
    address_id = _address(conn)
    data = call(user_id, "get_food_orders", build_args(_tool(conn, "get_food_orders"),
                                                        {"address": address_id}))
    body = data.get("data", data) if isinstance(data, dict) else {}
    rows = body.get("orders") if isinstance(body, dict) else None
    if not isinstance(rows, list):
        raise SwiggyError("Swiggy did not return an order list. Check the Swiggy app before retrying an order.")
    def item_names(row):
        items = row.get("orderedItems")
        if isinstance(items, list):
            return ", ".join(str(item.get("name") or item.get("itemName") or "Item")
                             for item in items if isinstance(item, dict))
        return str(items or "") if isinstance(items, str) else ""

    recent = [{"order_id": str(r.get("orderId")), "restaurant": str(r.get("restaurantName") or ""),
               "item": item_names(r), "total": str(r.get("orderTotal") or ""),
               "status": str(r.get("orderStatus") or ""), "ordered_time": str(r.get("orderedTime") or "")}
              for r in rows if isinstance(r, dict) and r.get("orderId")]
    _reconcile_with(user_id, rows)
    with db.cursor() as cur:
        attempts = cur.execute("SELECT state, order_id, address_id, created_ts, to_pay FROM swiggy_order_attempts "
                               "WHERE user_id=? ORDER BY created_ts DESC LIMIT 20", (user_id,)).fetchall()
    return {"provider_orders": recent, "attempts": [dict(r) for r in attempts],
            "address": conn.get("address_label") or address_id}


# --------------------------------------------------------------------------- #
# Recovering from an uncertain placement (M-01)
# --------------------------------------------------------------------------- #
RESOLVE_COOLDOWN = dt.timedelta(minutes=10)
_ACTIVE_STATES = ("started", "unknown")


def _amount(text) -> float | None:
    match = _PRICE_TEXT.fullmatch(str(text or ""))
    return float(match.group(2).replace(",", "")) if match else None


def _order_time(text) -> dt.datetime | None:
    """Swiggy's `orderedTime` in the formats seen in its APIs; None when unreadable."""
    if isinstance(text, (int, float)) and not isinstance(text, bool) and text > 1e9:
        stamp = text / 1000 if text > 1e12 else text
        return dt.datetime.fromtimestamp(stamp, dt.timezone.utc).astimezone(
            clock._TZ or dt.timezone.utc).replace(tzinfo=None)
    value = str(text or "").strip()
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(clock._TZ).replace(tzinfo=None) if parsed.tzinfo and clock._TZ else parsed.replace(tzinfo=None)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%d %b %Y, %I:%M %p", "%b %d, %Y, %I:%M %p", "%d %b %Y %I:%M %p"):
        try:
            return dt.datetime.strptime(value, fmt)          # noqa: DTZ007 - Swiggy shows local (IST) times
        except ValueError:
            continue
    return None


def _matching_order(attempt: dict, orders: list) -> dict | None:
    """A provider order that is this attempt: same payable total (±₹1), not cancelled,
    not claimed by another attempt, and provably recent (placed after the attempt
    started, or still active). An old order with the same total never matches."""
    if attempt.get("to_pay") is None:
        return None
    with db.cursor() as cur:
        claimed = {r["order_id"] for r in cur.execute(
            "SELECT order_id FROM swiggy_order_attempts WHERE order_id IS NOT NULL")}
    for row in orders:
        if not isinstance(row, dict) or not row.get("orderId") or str(row["orderId"]) in claimed:
            continue
        if "CANCEL" in str(row.get("orderStatus") or "").upper():
            continue
        total = _amount(row.get("orderTotal"))
        if total is None or abs(total - float(attempt["to_pay"])) > 1:
            continue
        placed = _order_time(row.get("orderedTime"))
        started = dt.datetime.fromisoformat(attempt["created_ts"]) - dt.timedelta(minutes=2)
        if (placed is not None and placed >= started) or (placed is None and row.get("isActiveOrder") is True):
            return row
    return None


def _reconcile_with(user_id: int, orders: list) -> int:
    with db.cursor() as cur:
        open_attempts = [dict(r) for r in cur.execute(
            "SELECT fingerprint, to_pay, created_ts FROM swiggy_order_attempts WHERE user_id=? "
            "AND state IN ('started','unknown') ORDER BY created_ts", (user_id,))]
    fixed = 0
    for attempt in open_attempts:
        match = _matching_order(attempt, orders)
        if match:
            with db.cursor() as cur:
                cur.execute("UPDATE swiggy_order_attempts SET state='confirmed', order_id=?, resolved_ts=? "
                            "WHERE user_id=? AND fingerprint=? AND state IN ('started','unknown')",
                            (str(match["orderId"]), clock.now().isoformat(), user_id, attempt["fingerprint"]))
                cur.execute("DELETE FROM swiggy_cart_intents WHERE user_id=?", (user_id,))
            fixed += 1
    return fixed


def _provider_orders(user_id: int) -> list:
    conn = _conn(user_id)
    data = call(user_id, "get_food_orders", build_args(_tool(conn, "get_food_orders"),
                                                        {"address": _address(conn)}))
    body = data.get("data", data) if isinstance(data, dict) else {}
    rows = body.get("orders") if isinstance(body, dict) else None
    if not isinstance(rows, list):
        raise SwiggyError("Swiggy did not return an order list. Check the Swiggy app before retrying an order.")
    return rows


def reconcile_attempts(user_id: int) -> int:
    """Mark uncertain attempts confirmed when Swiggy's order history shows them. Never
    marks anything 'not placed' on its own: absence in a lagging history proves nothing."""
    with db.cursor() as cur:
        pending = cur.execute("SELECT 1 FROM swiggy_order_attempts WHERE user_id=? "
                              "AND state IN ('started','unknown') LIMIT 1", (user_id,)).fetchone()
    if not pending or "get_food_orders" not in {t.get("name") for t in db.jl(_conn(user_id)["tools"])}:
        return 0
    try:
        return _reconcile_with(user_id, _provider_orders(user_id))
    except SwiggyError:
        return 0                                   # the unresolved block stays; nothing is assumed


def resolve_attempt(user_id: int, confirmation) -> dict:
    """The person checked Swiggy and says no order was placed. Allowed only after a
    cooldown and only when Swiggy's current order history shows no matching order."""
    if confirmation != "NO_ORDER_IN_SWIGGY":
        raise ValueError("Confirm that you checked Swiggy and no order was placed")
    with db.cursor() as cur:
        attempts = [dict(r) for r in cur.execute(
            "SELECT fingerprint, to_pay, created_ts FROM swiggy_order_attempts WHERE user_id=? "
            "AND state IN ('started','unknown') ORDER BY created_ts, fingerprint", (user_id,))]
    if not attempts:
        return {"resolved": 0, "message": "No uncertain order attempts."}
    newest = max(dt.datetime.fromisoformat(a["created_ts"]) for a in attempts)
    if clock.now() - newest < RESOLVE_COOLDOWN:
        wait = int((RESOLVE_COOLDOWN - (clock.now() - newest)).total_seconds() // 60) + 1
        raise ValueError(f"Wait about {wait} more minute(s) for Swiggy's order history to update, then check again.")
    orders = _provider_orders(user_id)
    if _reconcile_with(user_id, orders):
        return {"resolved": 0, "message": "Swiggy shows this order was placed. It is now recorded as confirmed."}
    with db.cursor() as cur:
        cur.execute("UPDATE swiggy_order_attempts SET state='not_placed', resolved_ts=? WHERE user_id=? "
                    "AND state IN ('started','unknown')", (clock.now().isoformat(), user_id))
        count = cur.rowcount
    return {"resolved": count, "message": "Marked as not placed. You can review the cart again."}
