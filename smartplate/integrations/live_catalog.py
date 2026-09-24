"""User-selected, address-scoped Swiggy discovery cache for planning."""
from __future__ import annotations

import asyncio
import datetime as dt
import math

from .. import db
from . import swiggy_discovery as discovery, swiggy_oauth as oauth


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _number(value, lo: float = 0, hi: float = 100000) -> float | None:
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    return n if math.isfinite(n) and lo <= n <= hi else None


def _call(user_id: int, name: str, arguments: dict) -> dict:
    return asyncio.run(discovery.SwiggyFoodClient(oauth.token_for(user_id)).call(name, arguments))


def addresses(user_id: int, page: int = 1) -> dict:
    if not 1 <= page <= 100:
        raise ValueError("Invalid address page")
    return discovery.addresses(_call(user_id, "get_addresses", {"page": page}))


def select_address(user_id: int, address_id: str) -> dict:
    # Verify the exact opaque ID against Swiggy's current, paginated list.
    page = 1
    while page <= 20:
        data = addresses(user_id, page)
        row = next((a for a in data["addresses"] if a["id"] == address_id), None)
        if row:
            with db.cursor() as cur:
                cur.execute("UPDATE swiggy_connections SET address_id=?,address_label=? WHERE user_id=?",
                            (address_id, row["label"], user_id))
                cur.execute("DELETE FROM live_menu_items WHERE live_restaurant_id IN "
                            "(SELECT id FROM live_restaurants WHERE user_id=?)", (user_id,))
                cur.execute("DELETE FROM live_restaurants WHERE user_id=?", (user_id,))
            return {"address_id": address_id, "label": row["label"]}
        if not data["pagination"].get("hasMore"):
            break
        page += 1
    raise ValueError("Choose an address from your Swiggy account")


def search(user_id: int, query: str, offset: int = 0) -> dict:
    query = query.strip()
    if not 2 <= len(query) <= 80 or not 0 <= offset <= 1000:
        raise ValueError("Enter a restaurant or cuisine (2–80 characters)")
    address_id = oauth.selected_address(user_id)
    raw = _call(user_id, "search_restaurants", {
        "addressId": address_id, "query": query, "offset": offset})
    data = discovery.restaurants(raw)
    with db.cursor() as cur:
        for returned in raw.get('restaurants', []):
            if (isinstance(returned, dict) and isinstance(returned.get('id'), str)
                    and returned.get('availabilityStatus') != 'OPEN'):
                cur.execute("UPDATE live_restaurants SET status=?,selected=0 WHERE user_id=? "
                            "AND address_id=? AND provider_id=?",
                            (returned.get('availabilityStatus') or 'UNAVAILABLE', user_id,
                             address_id, returned['id']))
        for r in data["restaurants"]:
            if not r["name"]:
                continue
            cur.execute("INSERT INTO live_restaurants(user_id,address_id,provider_id,name,rating,"
                        "cuisines,area,eta_min,status,fetched_ts) VALUES (?,?,?,?,?,?,?,?,?,?) "
                        "ON CONFLICT(user_id,address_id,provider_id) DO UPDATE SET "
                        "name=excluded.name,rating=excluded.rating,cuisines=excluded.cuisines,"
                        "area=excluded.area,eta_min=excluded.eta_min,status=excluded.status,"
                        "fetched_ts=excluded.fetched_ts",
                        (user_id, address_id, r["id"], r["name"], _number(r["rating"], 0, 5),
                         db.jd(r["cuisines"]), r["area"], r["eta_min"], r["availability"], _now()))
    return data


def browse_menu(user_id: int, provider_id: str) -> dict:
    address_id = oauth.selected_address(user_id)
    with db.cursor() as cur:
        row = cur.execute("SELECT id,name,status FROM live_restaurants WHERE user_id=? "
                          "AND address_id=? AND provider_id=?", (user_id, address_id, provider_id)).fetchone()
    if not row or row["status"] != "OPEN":
        raise ValueError("Choose an open restaurant from your search results")
    data = discovery.menu(_call(user_id, "get_restaurant_menu", {
        "addressId": address_id, "restaurantId": provider_id}), provider_id)
    with db.cursor() as cur:
        cur.execute("UPDATE live_menu_items SET available=0 WHERE live_restaurant_id=?", (row["id"],))
        for item in data["items"]:
            if not item["name"]:
                continue
            cur.execute("INSERT INTO live_menu_items(live_restaurant_id,provider_id,name,price,veg,"
                        "rating,categories,available,fetched_ts) VALUES (?,?,?,?,?,?,?,?,?) "
                        "ON CONFLICT(live_restaurant_id,provider_id) DO UPDATE SET "
                        "name=excluded.name,price=excluded.price,veg=excluded.veg,rating=excluded.rating,"
                        "categories=excluded.categories,available=excluded.available,fetched_ts=excluded.fetched_ts",
                        (row["id"], item["id"], item["name"], _number(item["price"]),
                         None if item["veg"] is None else int(bool(item["veg"])),
                         _number(item["rating"], 0, 5), db.jd(item["categories"]), 1, _now()))
    return data


def select_restaurant(user_id: int, provider_id: str, selected: bool) -> None:
    address_id = oauth.selected_address(user_id)
    with db.cursor() as cur:
        row = cur.execute("SELECT id,selected FROM live_restaurants WHERE user_id=? AND address_id=? "
                          "AND provider_id=? AND status='OPEN'", (user_id, address_id, provider_id)).fetchone()
        if not row:
            raise ValueError("Restaurant is not available for this address")
        if selected and not row['selected']:
            count = cur.execute("SELECT COUNT(*) FROM live_restaurants WHERE user_id=? AND "
                                "address_id=? AND selected=1", (user_id, address_id)).fetchone()[0]
            if count >= 8:
                raise ValueError("Choose up to eight restaurants for a weekly plan")
        cur.execute("UPDATE live_restaurants SET selected=? WHERE id=?", (int(selected), row["id"]))


def selected_restaurants(user_id: int) -> list[dict]:
    address_id = oauth.selected_address(user_id)
    with db.cursor() as cur:
        rows = cur.execute("SELECT r.id,r.provider_id,r.name,r.rating,r.area,r.eta_min,"
                           "r.fetched_ts,COUNT(m.id) AS item_count FROM live_restaurants r "
                           "LEFT JOIN live_menu_items m ON m.live_restaurant_id=r.id "
                           "AND m.available=1 AND m.price IS NOT NULL "
                           "WHERE r.user_id=? AND r.address_id=? AND r.selected=1 AND r.status='OPEN' "
                           "GROUP BY r.id ORDER BY r.name", (user_id, address_id)).fetchall()
    return [db.row_to_dict(r) for r in rows]


def refresh_selected(user_id: int) -> None:
    """Re-read selected menus before creating a plan; stale cache is not a source."""
    selected = selected_restaurants(user_id)
    if not selected:
        raise ValueError('Search Swiggy and choose a restaurant first')
    for restaurant in selected:
        browse_menu(user_id, restaurant['provider_id'])


def planning_menu(user_id: int) -> list[dict]:
    address_id = oauth.selected_address(user_id)
    with db.cursor() as cur:
        rows = cur.execute("SELECT m.*,r.name AS restaurant_name,r.rating AS restaurant_rating,"
                           "r.eta_min,r.provider_id AS provider_restaurant_id FROM live_menu_items m "
                           "JOIN live_restaurants r ON r.id=m.live_restaurant_id WHERE r.user_id=? "
                           "AND r.address_id=? AND r.selected=1 AND r.status='OPEN' "
                           "AND m.available=1 AND m.price IS NOT NULL ORDER BY r.id,m.id",
                           (user_id, address_id)).fetchall()
    out = []
    for r in rows:
        if r["restaurant_rating"] is None:
            continue
        out.append({
            "id": r["id"], "restaurant_id": r["live_restaurant_id"],
            "provider_id": r["provider_id"], "provider_restaurant_id": r["provider_restaurant_id"],
            "name": r["name"], "restaurant_name": r["restaurant_name"],
            "restaurant_rating": r["restaurant_rating"],
            "item_rating": r["rating"],
            "price": r["price"], "delivery_fee": 0.0, "veg": r["veg"],
            "allergens": None, "tags": None, "reviews": [], "popularity": 0.5,
            "cuisine": "unknown", "kcal": 0, "protein_g": 0, "carbs_g": 0,
            "fat_g": 0, "sugar_g": None, "live": True,
        })
    return out
