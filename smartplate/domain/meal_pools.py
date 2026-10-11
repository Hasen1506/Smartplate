"""Meal pools: the dishes a person wants for breakfast, lunch and dinner.

Swiggy menus mix whole meals with sides, drinks and sweets, and a dish name doesn't say
which meal it suits. So the person decides: they drag dishes from their saved places'
real menus (or any restaurant's, found by live search) into a breakfast, lunch or
dinner pool. When a meal has a pool, the planner picks that meal only from it, still
within the budget and never past a hard allergy or diet rule. A meal without a pool is
planned from the saved places as before.

Pools belong to the delivery address (Swiggy dish ids, prices and what's open all
depend on it). Each dish is checked against the restaurant's live menu when it is
added; nothing is typed in or guessed. A dish whose name matches no nutrition template
is still planned, with no nutrition figures (live_catalog.unestimated).
"""
from .. import clock, db

MEALS = ("breakfast", "lunch", "dinner")
MAX_PER_MEAL = 20
MAX_PLACES = 6           # pooled restaurants read on a refresh, beyond the saved places


def _address(user_id: int) -> str | None:
    from . import live_catalog
    return live_catalog._address_id(user_id)


def _rows(user_id: int) -> list[dict]:
    from ..integrations import swiggy_connect
    address = _address(user_id)
    if not address:
        return []
    swiggy_connect.init_schema()
    with db.cursor() as cur:
        return [dict(r) for r in cur.execute(
            "SELECT * FROM swiggy_meal_pools WHERE user_id=? AND address_id=? ORDER BY added_ts, item_id",
            (user_id, address)).fetchall()]


def keys(user_id: int) -> set[tuple[str, str]]:
    """(Swiggy restaurant id, Swiggy dish id) for every pooled dish at this address."""
    return {(r["restaurant_id"], r["item_id"]) for r in _rows(user_id)}


def restaurants(user_id: int) -> list[dict]:
    """The pooled dishes' restaurants, in the order they were first used."""
    out, seen = [], set()
    for r in _rows(user_id):
        if r["restaurant_id"] not in seen:
            seen.add(r["restaurant_id"])
            out.append({"id": r["restaurant_id"], "name": r["restaurant_name"]})
    return out


def _catalogue(user_id: int) -> dict:
    """(Swiggy restaurant id, Swiggy dish id) → the local live dish, for the current address."""
    from . import live_catalog
    if not live_catalog.has_live(user_id):
        return {}
    with db.cursor() as cur:
        rows = cur.execute("SELECT m.id, m.price, m.nutrition_known, r.provider_id, m.provider_item_id "
                           "FROM menu_items m JOIN restaurants r ON r.id=m.restaurant_id WHERE r.city=?",
                           (live_catalog.city_key(user_id),)).fetchall()
    return {(str(r["provider_id"]), str(r["provider_item_id"])): dict(r) for r in rows}


def for_planner(user_id: int) -> dict:
    """{meal: {"ids": local dish ids on today's menus, "size": dishes in the pool}} for each
    meal that has a pool. A pool whose dishes are all off the menu still counts: that
    meal is not planned from elsewhere, and the plan says why."""
    rows = _rows(user_id)
    if not rows:
        return {}
    cat = _catalogue(user_id)
    out = {}
    for r in rows:
        pool = out.setdefault(r["meal"], {"ids": set(), "size": 0})
        pool["size"] += 1
        local = cat.get((r["restaurant_id"], r["item_id"]))
        if local:
            pool["ids"].add(local["id"])
    return out


def view(user_id: int) -> dict:
    from ..integrations import swiggy_connect
    cat = _catalogue(user_id)
    meals = {m: [] for m in MEALS}
    for r in _rows(user_id):
        local = cat.get((r["restaurant_id"], r["item_id"]))
        meals[r["meal"]].append({
            "restaurant_id": r["restaurant_id"], "restaurant_name": r["restaurant_name"],
            "item_id": r["item_id"], "name": r["name"], "veg": None if r["veg"] is None else bool(r["veg"]),
            "price": local["price"] if local else r["price"], "on_menu": bool(local),
            "nutrition_known": bool(local["nutrition_known"]) if local else None})
    conn = swiggy_connect.status(user_id) if _address(user_id) else {}
    return {"address": (conn.get("address") or {}).get("label") or _address(user_id), "meals": meals,
            "places": _palette(user_id), "max_per_meal": MAX_PER_MEAL}


def _palette(user_id: int) -> list[dict]:
    """Where pool dishes come from: the saved places, then the places the plan already
    reads (picked from a live search when nothing is saved yet), marked as not saved."""
    from ..integrations import swiggy_live
    from . import live_catalog
    if not _address(user_id):
        return []
    try:
        saved = swiggy_live.live_favourites(user_id)
    except Exception:                              # not connected: nothing to show
        return []
    out = [{**f, "saved": True} for f in saved]
    seen = {f["id"] for f in saved}
    if live_catalog.has_live(user_id):
        with db.cursor() as cur:
            for r in cur.execute("SELECT provider_id, name FROM restaurants WHERE city=? ORDER BY rating DESC, name",
                                 (live_catalog.city_key(user_id),)).fetchall():
                if r["provider_id"] and str(r["provider_id"]) not in seen:
                    seen.add(str(r["provider_id"]))
                    out.append({"id": str(r["provider_id"]), "name": r["name"], "saved": False})
    return out


def _check_meal(meal) -> str:
    if meal not in MEALS:
        raise ValueError("Choose breakfast, lunch or dinner")
    return meal


def _replan(user_id: int) -> None:
    from .. import service
    from ..kernel import optimizer
    plan = service.current_plan(user_id)
    optimizer.optimize(plan["plan"]["id"])


def add(user_id: int, meal: str, restaurant_id: str, restaurant_name: str, item_id: str) -> dict:
    """Add one dish from a restaurant's live menu to a meal's pool (and save the place)."""
    from ..integrations import swiggy_live
    from ..integrations.swiggy_connect import SwiggyError
    from . import live_catalog, models
    _check_meal(meal)
    restaurant_id, item_id = str(restaurant_id or ""), str(item_id or "")
    if not restaurant_id or not item_id:
        raise ValueError("Pick a dish from a restaurant's Swiggy menu")
    rows = _rows(user_id)
    if any(r["meal"] == meal and r["restaurant_id"] == restaurant_id and r["item_id"] == item_id for r in rows):
        return view(user_id)
    if sum(1 for r in rows if r["meal"] == meal) >= MAX_PER_MEAL:
        raise ValueError(f"Your {meal} pool is full ({MAX_PER_MEAL} dishes). Remove one first.")
    menu = swiggy_live.live_menu(user_id, restaurant_id, restaurant_name)
    item = next((i for i in menu["items"] if i["id"] == item_id), None)
    if not item:
        raise SwiggyError(f"That dish isn't on {menu['restaurant']['name']}'s Swiggy menu for your address now.")
    if item.get("has_options"):
        raise SwiggyError("This dish has options (sizes or add-ons), so Ziggy can't fill it into your cart. "
                          "Pick another, or order it in Swiggy.")
    user = models.get_user(user_id)
    if user["diet"] in ("veg", "vegan") and item.get("veg") is not True:
        raise ValueError("Swiggy doesn't mark this dish as vegetarian, so it can't go in your pool.")
    address = _address(user_id)
    place = menu["restaurant"]
    with db.cursor() as cur:
        cur.execute("INSERT OR REPLACE INTO swiggy_meal_pools(user_id, address_id, meal, restaurant_id, item_id, "
                    "restaurant_name, name, price, veg, added_ts) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (user_id, address, meal, restaurant_id, item_id, place["name"], item["name"], item.get("price"),
                     None if item.get("veg") is None else int(bool(item["veg"])),
                     clock.now().isoformat(timespec="microseconds")))
        # Pools come from your places: a dish's restaurant becomes a saved place.
        if not cur.execute("SELECT 1 FROM swiggy_favourites WHERE user_id=? AND address_id=? AND restaurant_id=?",
                           (user_id, address, restaurant_id)).fetchone():
            cur.execute("INSERT INTO swiggy_favourites(user_id, address_id, restaurant_id, restaurant_name) "
                        "VALUES (?,?,?,?)", (user_id, address, restaurant_id, place["name"]))
    live_catalog.add_dish(user_id, place, item)
    _replan(user_id)
    return view(user_id)


def remove(user_id: int, meal: str, restaurant_id: str, item_id: str) -> dict:
    _check_meal(meal)
    with db.cursor() as cur:
        cur.execute("DELETE FROM swiggy_meal_pools WHERE user_id=? AND address_id=? AND meal=? AND restaurant_id=? "
                    "AND item_id=?", (user_id, _address(user_id), meal, str(restaurant_id), str(item_id)))
    _replan(user_id)
    return view(user_id)


def move(user_id: int, meal: str, to_meal: str, restaurant_id: str, item_id: str) -> dict:
    """Drag a pooled dish to another meal's pool (no Swiggy call: it was checked when added)."""
    _check_meal(meal)
    _check_meal(to_meal)
    rows = _rows(user_id)
    row = next((r for r in rows if r["meal"] == meal and r["restaurant_id"] == str(restaurant_id)
                and r["item_id"] == str(item_id)), None)
    if not row:
        raise ValueError("That dish isn't in your pool any more")
    if meal == to_meal:
        return view(user_id)
    if not any(r["meal"] == to_meal and r["restaurant_id"] == row["restaurant_id"] and r["item_id"] == row["item_id"]
               for r in rows):
        if sum(1 for r in rows if r["meal"] == to_meal) >= MAX_PER_MEAL:
            raise ValueError(f"Your {to_meal} pool is full ({MAX_PER_MEAL} dishes). Remove one first.")
    with db.cursor() as cur:
        cur.execute("DELETE FROM swiggy_meal_pools WHERE user_id=? AND address_id=? AND meal=? AND restaurant_id=? "
                    "AND item_id=?", (user_id, row["address_id"], meal, row["restaurant_id"], row["item_id"]))
        cur.execute("INSERT OR REPLACE INTO swiggy_meal_pools(user_id, address_id, meal, restaurant_id, item_id, "
                    "restaurant_name, name, price, veg, added_ts) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (user_id, row["address_id"], to_meal, row["restaurant_id"], row["item_id"], row["restaurant_name"],
                     row["name"], row["price"], row["veg"], clock.now().isoformat(timespec="microseconds")))
    _replan(user_id)
    return view(user_id)
