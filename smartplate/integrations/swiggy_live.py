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
import re
import secrets

from .. import clock, config, db
from . import swiggy_connect as sc
from .swiggy_connect import SwiggyError

ALLOWED = frozenset({"get_addresses", "search_restaurants", "get_restaurant_menu", "search_menu",
                     "get_food_cart", "update_food_cart", "flush_food_cart", "get_payment_options",
                     "place_food_order", "get_food_orders", "track_food_order"})
MENU_TTL = dt.timedelta(hours=6)
CHECKOUT_QUOTE_TTL = dt.timedelta(minutes=5)
MATCH_RESTAURANT = 0.6
CHECKOUT_URL = "https://www.swiggy.com/"

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
}
FIELDS = {
    "id": ["id", "addressId", "address_id", "restaurantId", "restaurant_id", "restId", "itemId", "item_id",
           "menuItemId", "menu_item_id"],
    "menu_item_id": ["menu_item_id", "menuItemId"],
    "name": ["name", "restaurantName", "itemName", "title"],
    "label": ["annotation", "label", "tag", "addressType", "type"],
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


# --------------------------------------------------------------------------- #
# Calling tools
# --------------------------------------------------------------------------- #
def _conn(user_id: int) -> dict:
    conn = sc._connection(user_id)
    if not conn:
        raise SwiggyError("Connect your Swiggy account first (More → Swiggy connection).")
    if conn["expires_ts"] and dt.datetime.fromisoformat(conn["expires_ts"]) <= clock.now():
        raise SwiggyError("Your Swiggy sign-in has expired. Connect again.")
    if not conn["access_token"]:
        raise SwiggyError("Your Swiggy sign-in can no longer be read on this server. Connect again.")
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
    conn = _conn(user_id)
    _tool(conn, name)
    token = conn["access_token"]
    headers, _ = sc._rpc(token, "initialize", {"protocolVersion": sc.CLIENT_VERSION, "capabilities": {},
                                               "clientInfo": {"name": "SmartPlate", "version": "1.1.0"}}, 1)
    session_id = headers.get("mcp-session-id")
    sc._rpc(token, "notifications/initialized", {}, None, session_id)
    _, result = sc._rpc(token, "tools/call", {"name": name, "arguments": arguments}, 2, session_id)
    data = _payload(result)
    samples = db.jl(conn.get("samples"), {})
    samples[name] = {"arguments": sorted(arguments), "reply": _shape(data), "at": clock.now().isoformat(timespec="seconds")}
    with db.cursor() as cur:
        cur.execute("UPDATE swiggy_connections SET samples=? WHERE user_id=?", (db.jd(samples), user_id))
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


def rupees(record: dict) -> float | None:
    """Only convert a price when the provider names its units."""
    lowered = {k.lower(): k for k in record}
    for alias in FIELDS["price"]:
        key = lowered.get(alias.lower())
        if key is None or not isinstance(record[key], (int, float)) or isinstance(record[key], bool):
            continue
        value = float(record[key])
        if "paise" in key.lower():
            return round(value / 100, 2)
        if "rupee" in key.lower():
            return value
    return None


def _num(data, field: str):
    """The shallowest numeric `field` anywhere in a reply (e.g. the cart's amount to pay)."""
    queue = [data]
    while queue:
        nxt = []
        for node in queue:
            if isinstance(node, dict):
                v = _get(node, field)
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    return float(v)
                if isinstance(v, str) and re.fullmatch(r"₹?\s*\d+(\.\d+)?", v.strip()):
                    return float(v.strip().lstrip("₹").strip())
                nxt += list(node.values())
            elif isinstance(node, list):
                nxt += node
        queue = nxt
    return None


def _similar(a: str, b: str) -> float:
    norm = lambda s: re.sub(r"[^a-z0-9 ]", "", (s or "").lower()).strip()
    a, b = norm(a), norm(b)
    return 1.0 if a and (a in b or b in a) else difflib.SequenceMatcher(None, a, b).ratio()


# --------------------------------------------------------------------------- #
# What the app uses
# --------------------------------------------------------------------------- #
def addresses(user_id: int) -> list[dict]:
    conn = _conn(user_id)
    data = call(user_id, "get_addresses", build_args(_tool(conn, "get_addresses"), {}))
    rows = records(data, "id")
    out = [{"id": str(_get(r, "id")), "label": str(_get(r, "label") or "Address"),
            "text": str(_get(r, "text") or "")[:160]} for r in rows]
    if not out:
        raise SwiggyError("Swiggy didn't return any saved addresses. Add one in the Swiggy app first.")
    return out


def choose_address(user_id: int, address_id: str) -> dict:
    match = next((a for a in addresses(user_id) if a["id"] == str(address_id)), None)
    if not match:
        raise ValueError("That address isn't on your Swiggy account")
    with db.cursor() as cur:
        cur.execute("UPDATE swiggy_connections SET address_id=?, address_label=? WHERE user_id=?",
                    (match["id"], f"{match['label']} · {match['text']}"[:120], user_id))
        cur.execute("DELETE FROM swiggy_menus WHERE user_id=?", (user_id,))     # menus depend on the address
    return sc.status(user_id)


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
    for row in browse if isinstance(browse, list) else records(data, "id", "name"):
        if not isinstance(row, dict) or _get(row, "id") is None or _get(row, "name") is None:
            continue
        veg = _get(row, "veg")
        if user["diet"] in ("veg", "vegan") and veg in (False, 0):
            hidden += 1
            continue
        items.append({"id": str(_get(row, "id")), "name": str(_get(row, "name")),
                      "price": rupees(row), "veg": bool(veg) if veg is not None else None,
                      "in_stock": _get(row, "stock"),
                      "has_options": bool(_get(row, "variants") or _get(row, "addons"))})
    return {"restaurant": place, "address": conn.get("address_label") or _address(conn),
            "items": items, "hidden_nonveg": hidden, "truncated": bool(body.get("truncated")) if isinstance(body, dict) else False,
            "fetched": clock.now().isoformat(timespec="minutes")}


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
    for r in records(data, "id", "name")[:150]:
        veg = _get(r, "veg")
        items.append({"id": _get(r, "id"), "name": str(_get(r, "name")), "price": rupees(r),
                      "veg": bool(veg) if veg is not None else None})
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
    if (_get(item, "variants") not in (False, 0) or _get(item, "addons") not in (False, 0)
            or item.get("variations") or item.get("variantsV2") or item.get("addons")):
        raise SwiggyError("This dish needs options or add-ons SmartPlate cannot safely choose. "
                          "Customize it in Swiggy instead.")
    if user["diet"] == "veg" and _get(item, "veg") not in (True, 1):
        raise SwiggyError("Swiggy did not verify this dish as vegetarian. Check it in Swiggy before ordering.")
    item_id = _get(item, "menu_item_id")
    details = {"session_id": session_id, "planned": cell["item"], "planned_restaurant": cell["restaurant"],
               "planned_cost": cell["cost"], "restaurant": place["name"], "restaurant_id": place["id"],
               "item": str(_get(item, "name")), "item_id": item_id, "address_id": address_id}
    fingerprint = hashlib.sha256(json.dumps({**details, "provider_price": _get(item, "price")},
                                            sort_keys=True).encode()).hexdigest()
    return {**details, "address": conn.get("address_label") or "Selected Swiggy address",
            "menu_price": rupees(item), "fingerprint": fingerprint}


def fill_cart(session_id: int, expected_fingerprint: str | None = None) -> dict:
    """Add exactly the item the user reviewed, then read the provider's current cart."""
    if not expected_fingerprint:
        raise CartChanged("Review the live Swiggy item before adding it to your cart.")
    preview = cart_preview(session_id)  # fresh provider lookup: no six-hour browse cache
    if preview["fingerprint"] != expected_fingerprint:
        raise CartChanged("The live Swiggy item or your plan changed. Review it again before adding to cart.")
    user_id, cell = _planned(session_id)
    conn = _conn(user_id)
    tool = _tool(conn, "update_food_cart")
    call(user_id, "update_food_cart", build_args(tool, {
        "cart_items": [_cart_item(tool, preview["item_id"])], "restaurant": preview["restaurant_id"],
        "address": preview["address_id"], "restaurant_name": preview["restaurant"]}))
    cart = call(user_id, "get_food_cart", build_args(_tool(conn, "get_food_cart"), {"address": _address(conn)}))
    if not any(str(_get(r, "menu_item_id")) == str(preview["item_id"])
               for r in records(cart, "menu_item_id")):
        raise SwiggyError("SmartPlate could not confirm the added item in Swiggy's cart. "
                          "Check your Swiggy cart before trying again.")
    to_pay = _num(cart, "to_pay")
    return {"session_id": session_id, "restaurant": preview["restaurant"], "item": preview["item"],
            "planned": cell["item"], "planned_cost": cell["cost"], "menu_price": preview["menu_price"],
            "to_pay": to_pay, "over_plan": round(to_pay - cell["cost"], 2) if to_pay is not None else None,
            "checkout_url": CHECKOUT_URL}


def live_cart_preview(user_id: int, restaurant_id: str, restaurant_name: str,
                      item_id: str, item_name: str) -> dict:
    """Review an item chosen from a real menu, without a seeded plan or fuzzy match."""
    from ..domain import models
    user = models.get_user(user_id)
    if user["allergens"] or user["medical"] or user["diet"] == "vegan":
        raise SwiggyError("SmartPlate cannot verify your ingredient or medical rules from Swiggy's menu. "
                          "Choose and check the dish directly in Swiggy before ordering.")
    place = live_menu(user_id, restaurant_id, restaurant_name)["restaurant"]
    conn = _conn(user_id)
    address_id = _address(conn)
    data = call(user_id, "search_menu", build_args(_tool(conn, "search_menu"),
                {"query": item_name, "address": address_id, "restaurant_scope": restaurant_id}))
    exact = [r for r in records(data, "menu_item_id", "name")
             if str(_get(r, "menu_item_id")) == str(item_id)
             and _name(str(_get(r, "name"))) == _name(item_name)
             and (_get(r, "restaurant_id") is None or str(_get(r, "restaurant_id")) == restaurant_id)]
    if len(exact) != 1:
        raise SwiggyError("The selected dish is no longer an exact live menu match. Refresh the menu.")
    item = exact[0]
    if _get(item, "stock") not in (True, 1):
        raise SwiggyError("Swiggy did not confirm this dish is in stock. Refresh the menu.")
    if (_get(item, "variants") not in (False, 0) or _get(item, "addons") not in (False, 0)
            or item.get("variations") or item.get("variantsV2") or item.get("addons")):
        raise SwiggyError("This dish needs options that SmartPlate cannot choose yet. Customize it in Swiggy.")
    if user["diet"] == "veg" and _get(item, "veg") not in (True, 1):
        raise SwiggyError("Swiggy did not verify this dish as vegetarian. Check it in Swiggy.")
    details = {"user_id": user_id, "address_id": address_id, "restaurant_id": restaurant_id,
               "restaurant": place["name"], "item_id": str(item_id), "item": str(_get(item, "name"))}
    fingerprint = hashlib.sha256(json.dumps({**details, "provider_price": _get(item, "price")},
                                            sort_keys=True).encode()).hexdigest()
    return {**details, "address": conn.get("address_label") or address_id,
            "menu_price": rupees(item), "fingerprint": fingerprint}


def fill_live_cart(user_id: int, restaurant_id: str, restaurant_name: str,
                   item_id: str, item_name: str, expected_fingerprint: str | None) -> dict:
    if not expected_fingerprint:
        raise CartChanged("Review the exact live Swiggy item before adding it to your cart.")
    preview = live_cart_preview(user_id, restaurant_id, restaurant_name, item_id, item_name)
    if preview["fingerprint"] != expected_fingerprint:
        raise CartChanged("The live item, restaurant or address changed. Review it again.")
    conn = _conn(user_id)
    current = call(user_id, "get_food_cart", build_args(_tool(conn, "get_food_cart"),
                                                       {"address": preview["address_id"]}))
    if records(current, "menu_item_id"):
        raise SwiggyError("Your Swiggy cart already has items. Review or clear it in Swiggy before starting a new order.")
    tool = _tool(conn, "update_food_cart")
    call(user_id, "update_food_cart", build_args(tool, {
        "cart_items": [_cart_item(tool, preview["item_id"])], "restaurant": restaurant_id,
        "address": preview["address_id"], "restaurant_name": preview["restaurant"]}))
    cart = call(user_id, "get_food_cart", build_args(_tool(conn, "get_food_cart"),
                                                    {"address": preview["address_id"]}))
    if not any(str(_get(r, "menu_item_id")) == preview["item_id"] for r in records(cart, "menu_item_id")):
        raise SwiggyError("SmartPlate could not confirm the item in Swiggy's cart. Check your cart before trying again.")
    return {**preview, "to_pay": _num(cart, "to_pay"), "checkout_url": CHECKOUT_URL}


def _checkout_state(user_id: int) -> dict:
    """Fresh cart, exact payable total and provider-offered COD before placement."""
    if not config.LIVE_ORDERS:
        raise SwiggyError("Real order placement is disabled until Swiggy access and durable storage are approved.")
    from ..domain import models
    user = models.get_user(user_id)
    if user["allergens"] or user["medical"] or user["diet"] == "vegan":
        raise SwiggyError("SmartPlate cannot verify your ingredient or medical rules for a real order. "
                          "Review and place it in Swiggy instead.")
    conn = _conn(user_id)
    address_id = _address(conn)
    cart = call(user_id, "get_food_cart", build_args(_tool(conn, "get_food_cart"), {"address": address_id}))
    items = records(cart, "menu_item_id")
    if len(items) != 1:
        raise SwiggyError("Review one exact dish in the Swiggy cart before placing an order.")
    item = items[0]
    if item.get("quantity") != 1 or not _get(item, "name"):
        raise SwiggyError("SmartPlate could not verify one dish and quantity in the live cart.")
    if item.get("in_stock") is False or item.get("inStock") is False:
        raise SwiggyError("The dish is no longer in stock. Refresh your cart.")
    total = _num(cart, "to_pay")
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
               "item_id": str(_get(item, "menu_item_id")), "item": str(_get(item, "name") or "Selected dish"),
               "quantity": item.get("quantity"), "to_pay": total, "payment_method": str(cod["id"])}
    fingerprint = hashlib.sha256(json.dumps(details, sort_keys=True).encode()).hexdigest()
    return {**details, "payment_label": str(cod.get("displayName") or "Cash on Delivery"),
            "fingerprint": fingerprint}


def live_checkout_preview(user_id: int) -> dict:
    """Issue a short-lived approval for this exact live cart.

    The cart hash is stable so an uncertain placement can block a duplicate. The
    approval token is new each time, allowing a confirmed favourite to be ordered
    again later without reusing the previous nonrepeatable attempt.
    """
    preview = _checkout_state(user_id)
    with db.cursor() as cur:
        unresolved = cur.execute("SELECT 1 FROM swiggy_order_attempts WHERE user_id=? "
                                 "AND cart_fingerprint=? AND state IN ('started','unknown') LIMIT 1",
                                 (user_id, preview["fingerprint"])).fetchone()
        if unresolved:
            raise SwiggyError("An earlier placement of this cart is unresolved. Check your Swiggy orders "
                              "before placing it again.")
        cur.execute("DELETE FROM swiggy_checkout_quotes WHERE created_ts < ?",
                    ((clock.now() - CHECKOUT_QUOTE_TTL).isoformat(),))
        approval = secrets.token_urlsafe(32)
        cur.execute("INSERT INTO swiggy_checkout_quotes(token, user_id, cart_fingerprint, created_ts) "
                    "VALUES (?,?,?,?)", (approval, user_id, preview["fingerprint"], clock.now().isoformat()))
    return {**preview, "fingerprint": approval}


def place_live_order(user_id: int, expected_fingerprint: str | None) -> dict:
    if not expected_fingerprint:
        raise CartChanged("Review the current Swiggy cart, address, total and payment method first.")
    preview = _checkout_state(user_id)
    with db.cursor() as cur:
        quote = cur.execute("SELECT cart_fingerprint, created_ts FROM swiggy_checkout_quotes "
                            "WHERE user_id=? AND token=?", (user_id, expected_fingerprint)).fetchone()
    if (not quote or quote["cart_fingerprint"] != preview["fingerprint"]
            or dt.datetime.fromisoformat(quote["created_ts"]) < clock.now() - CHECKOUT_QUOTE_TTL):
        raise CartChanged("The Swiggy cart, address, total or payment method changed. Review it again.")
    with db.cursor() as cur:
        existing = cur.execute("SELECT state, order_id FROM swiggy_order_attempts WHERE user_id=? AND fingerprint=?",
                               (user_id, expected_fingerprint)).fetchone()
        if existing:
            raise CartChanged("This order was already attempted. Check your Swiggy orders before trying again.")
        unresolved = cur.execute("SELECT 1 FROM swiggy_order_attempts WHERE user_id=? "
                                 "AND cart_fingerprint=? AND state IN ('started','unknown') LIMIT 1",
                                 (user_id, preview["fingerprint"])).fetchone()
        if unresolved:
            raise CartChanged("An earlier placement of this cart is unresolved. Check Swiggy orders first.")
        cur.execute("INSERT INTO swiggy_order_attempts(user_id, fingerprint, cart_fingerprint, "
                    "address_id, state, created_ts) VALUES (?,?,?,?,?,?)",
                    (user_id, expected_fingerprint, preview["fingerprint"], preview["address_id"],
                     "started", clock.now().isoformat()))
        cur.execute("DELETE FROM swiggy_checkout_quotes WHERE user_id=? AND token=?",
                    (user_id, expected_fingerprint))
    conn = _conn(user_id)
    try:
        result = call(user_id, "place_food_order", build_args(_tool(conn, "place_food_order"),
                {"address": preview["address_id"], "payment_method": preview["payment_method"]}))
        body = result.get("data", result) if isinstance(result, dict) else {}
        order_id = body.get("orderId") if isinstance(body, dict) else None
        confirmed = isinstance(body, dict) and body.get("normalizedStatus") == "success" and order_id
        with db.cursor() as cur:
            cur.execute("UPDATE swiggy_order_attempts SET state=?, order_id=? WHERE user_id=? AND fingerprint=?",
                        ("confirmed" if confirmed else "unknown", str(order_id) if order_id else None,
                         user_id, expected_fingerprint))
        if not confirmed:
            raise SwiggyError("Swiggy did not confirm a completed order. Check Swiggy orders; do not retry this cart.")
        return {"order_id": str(order_id), "status": "confirmed", "item": preview["item"],
                "to_pay": preview["to_pay"], "address": preview["address"]}
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
    return {"order_id": order_id, "provider": data.get("data", data) if isinstance(data, dict) else data}


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
    with db.cursor() as cur:
        attempts = cur.execute("SELECT state, order_id, created_ts FROM swiggy_order_attempts "
                               "WHERE user_id=? AND address_id=? ORDER BY created_ts DESC LIMIT 20",
                               (user_id, address_id)).fetchall()
    return {"provider_orders": recent, "attempts": [dict(r) for r in attempts],
            "address": conn.get("address_label") or address_id}
