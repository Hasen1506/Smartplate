"""Reading Swiggy MCP replies: tool arguments, record walking, field aliases.

Pure functions over the JSON Swiggy returns (no network, no database). Split out of
swiggy_live.py (grill #57); every name is re-exported there.
"""
import difflib
import json
import math
import re
from .swiggy_connect import SwiggyError


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
    "intent_app": ["intentApp", "intent_app"],          # UPI Payment stage (swiggy_upi)
    "upi_qr": ["generateUPIQR", "generateUpiQr", "generate_upi_qr"],
    "lat": ["lat", "latitude"],
    "lng": ["lng", "lon", "longitude"],
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
    "lat": ["lat", "latitude"],
    "lng": ["lng", "lon", "longitude"],
}


def _shape(value, depth=0):
    """Field names and types only, never values: what a reply looked like."""
    if depth > 4:
        return "…"
    if isinstance(value, dict):
        return {k: _shape(v, depth + 1) for k, v in list(value.items())[:30]}
    if isinstance(value, list):
        return [_shape(value[0], depth + 1)] if value else []
    return type(value).__name__


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
        raise SwiggyError(f"Swiggy's {tool.get('name')} needs {', '.join(missing)}, which Ziggy doesn't "
                          "know how to fill yet.")
    return args


MAX_PORTIONS = 8                 # a household meal; anything bigger is a party order, made in Swiggy


def _cart_item(tool: dict, item_id, quantity: int = 1) -> dict:
    props = ((tool.get("input_schema") or {}).get("properties") or {})
    items_key = _find(props, "cart_items")
    item_props = (((props.get(items_key) or {}).get("items") or {}).get("properties") or {}) if items_key else {}
    id_key = _find(item_props, "item_id") or "menu_item_id"
    qty_key = _find(item_props, "quantity") or "quantity"
    return {id_key: item_id, qty_key: quantity}


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
