"""Swiggy gates 2 and 3: real addresses, restaurants and menus, and filling the cart.

Builds on swiggy_connect (sign-in + discovery). Every call goes through `call()`,
which refuses any tool not on ALLOWED. SmartPlate can read, and it can put the
planned dish in the person's Swiggy cart. It never places an order, picks a payment
method or pays. The person opens Swiggy, checks the cart and pays there.

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
import json
import re

from .. import clock, db
from . import swiggy_connect as sc
from .swiggy_connect import SwiggyError

ALLOWED = frozenset({"get_addresses", "search_restaurants", "get_restaurant_menu", "search_menu",
                     "get_food_cart", "update_food_cart", "flush_food_cart"})
MENU_TTL = dt.timedelta(hours=6)
MATCH_RESTAURANT = 0.6
MATCH_ITEM = 0.55
CHECKOUT_URL = "https://www.swiggy.com/checkout"

ALIASES = {
    "query": ["query", "searchQuery", "search_query", "keyword", "q", "restaurantName", "restaurant_name"],
    "address": ["addressId", "address_id"],
    "restaurant": ["restaurantId", "restaurant_id", "restId", "rest_id"],
    "restaurant_name": ["restaurantName", "restaurant_name"],
    "cart_items": ["cartItems", "cart_items", "items"],
    "item_id": ["menu_item_id", "menuItemId", "itemId", "item_id", "id"],
    "quantity": ["quantity", "qty", "count"],
}
FIELDS = {
    "id": ["id", "addressId", "address_id", "restaurantId", "restaurant_id", "restId", "itemId", "item_id",
           "menuItemId", "menu_item_id"],
    "name": ["name", "restaurantName", "itemName", "title"],
    "label": ["annotation", "label", "tag", "addressType", "type"],
    "text": ["formattedAddress", "address", "addressLine", "addressLine1", "displayAddress", "area", "locality"],
    "price": ["price", "finalPrice", "defaultPrice", "priceInPaise", "price_in_paise", "cost"],
    "rating": ["avgRating", "rating", "avg_rating"],
    "eta": ["deliveryTime", "eta", "sla", "slaString", "delivery_time"],
    "area": ["areaName", "locality", "area", "cuisines"],
    "veg": ["isVeg", "veg", "is_veg"],
    "to_pay": ["to_pay", "toPay", "totalPayable", "grandTotal", "billTotal", "total", "totalAmount"],
}


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
    """One tools/call. Anything outside ALLOWED (placing orders, payment) is refused here."""
    if name not in ALLOWED:
        raise SwiggyError(f"SmartPlate never calls {name}. You place and pay for orders in Swiggy.")
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
    """Menu prices: rupees, or paise when the field says so or the number is that large
    (Swiggy's web data uses paise). Unverified until a real reply is seen."""
    lowered = {k.lower(): k for k in record}
    for alias in FIELDS["price"]:
        key = lowered.get(alias.lower())
        if key is None or not isinstance(record[key], (int, float)):
            continue
        value = float(record[key])
        return round(value / 100, 2) if "paise" in key.lower() or value >= 1000 else value
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
    if not cell or cell.get("kind") != "delivery" or not cell.get("restaurant"):
        raise ValueError("Only a planned delivery can go into a Swiggy cart")
    return view["user"]["id"], cell


def fill_cart(session_id: int) -> dict:
    """Put the planned dish in the person's Swiggy cart and read back what they'd pay.
    Nothing is ordered: they open Swiggy to review and pay."""
    user_id, cell = _planned(session_id)
    conn = _conn(user_id)
    live = menu(user_id, cell["restaurant"])
    best = max(live["items"], key=lambda i: _similar(cell["item"], i["name"]), default=None)
    if not best or _similar(cell["item"], best["name"]) < MATCH_ITEM:
        raise SwiggyError(f"{cell['item']} isn't on {live['swiggy']['name']}'s Swiggy menu right now. "
                          "Tap Change to pick another dish.")
    tool = _tool(conn, "update_food_cart")
    call(user_id, "update_food_cart", build_args(tool, {
        "cart_items": [_cart_item(tool, best["id"])], "restaurant": live["swiggy"]["id"],
        "address": _address(conn), "restaurant_name": live["swiggy"]["name"]}))
    cart = call(user_id, "get_food_cart", build_args(_tool(conn, "get_food_cart"), {"address": _address(conn)}))
    to_pay = _num(cart, "to_pay")
    return {"session_id": session_id, "restaurant": live["swiggy"]["name"], "item": best["name"],
            "planned": cell["item"], "planned_cost": cell["cost"], "menu_price": best["price"],
            "to_pay": to_pay, "over_plan": round(to_pay - cell["cost"], 2) if to_pay is not None else None,
            "checkout_url": CHECKOUT_URL}
