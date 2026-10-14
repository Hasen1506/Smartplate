"""Ziggy without any platform account (grill #25, #46; founder answer #25, Oct 2026).

A person who doesn't connect Swiggy can still:
  * type a delivery area/address (kept on this profile only; never sent anywhere), and
  * type favourite places and dishes per meal, with the price they usually pay and the app
    they order from (Swiggy, Toing, Zomato or other).

Typed favourites are the user's own data, so they are what Ziggy may learn from (founder
#14). Ordering a typed favourite is a hand-off: Ziggy opens that app's own public search
(Swiggy) or names the app; it never fetches Toing/Zomato prices (providers/ notes why).
When the person later connects Swiggy, their Swiggy address list takes over for delivery;
the typed address stays as their own note until they delete it.
"""
from __future__ import annotations

from .. import clock, db

MEALS = ("breakfast", "lunch", "dinner")
PLATFORMS = ("swiggy", "toing", "zomato", "other")
MAX_FAVOURITES = 60


def _text(value, what: str, limit: int, required: bool = True) -> str:
    text = " ".join(str(value or "").split())
    if required and not text:
        raise ValueError(f"Type the {what}")
    if len(text) > limit:
        raise ValueError(f"Keep the {what} under {limit} characters")
    return text


# ---- address ---------------------------------------------------------------------- #
def address(user_id: int) -> dict | None:
    with db.cursor() as cur:
        row = cur.execute("SELECT label, text, updated_ts FROM manual_addresses WHERE user_id=?", (user_id,)).fetchone()
    return dict(row) if row else None


def set_address(user_id: int, body: dict) -> dict:
    label = _text(body.get("label") or "Home", "label", 30)
    text = _text(body.get("text"), "address or area", 200)
    with db.cursor() as cur:
        cur.execute("DELETE FROM manual_addresses WHERE user_id=?", (user_id,))
        cur.execute("INSERT INTO manual_addresses(user_id, label, text, updated_ts) VALUES (?,?,?,?)",
                    (user_id, label, text, clock.now().isoformat(timespec="seconds")))
    return {"address": address(user_id)}


def delete_address(user_id: int) -> dict:
    with db.cursor() as cur:
        cur.execute("DELETE FROM manual_addresses WHERE user_id=?", (user_id,))
    return {"address": None}


# ---- favourites ------------------------------------------------------------------- #
def _handoff(platform: str, restaurant: str, item: str) -> dict:
    from . import timing
    if platform == "swiggy":
        return {"label": "Order on Swiggy", "url": timing.swiggy_handoff(restaurant, item)}
    return {"label": f"Order in {platform.title()}" if platform != "other" else "Order in your app", "url": None}


def favourites(user_id: int) -> dict:
    with db.cursor() as cur:
        rows = [dict(r) for r in cur.execute(
            "SELECT id, meal, restaurant_name, item_name, price, platform, added_ts FROM typed_favourites "
            "WHERE user_id=? ORDER BY id", (user_id,)).fetchall()]
    for r in rows:
        r["handoff"] = _handoff(r["platform"], r["restaurant_name"], r["item_name"])
        r["source"] = "typed"
    return {"meals": {m: [r for r in rows if r["meal"] == m] for m in MEALS}, "count": len(rows),
            "address": address(user_id),
            "note": "Typed in by you. Prices are what you entered; Ziggy doesn't fetch them."}


def add_favourite(user_id: int, body: dict) -> dict:
    meal = str(body.get("meal") or "").lower()
    if meal not in MEALS:
        raise ValueError("Choose breakfast, lunch or dinner")
    platform = str(body.get("platform") or "swiggy").lower()
    if platform not in PLATFORMS:
        raise ValueError("Choose Swiggy, Toing, Zomato or other")
    restaurant = _text(body.get("restaurant_name"), "restaurant name", 80)
    item = _text(body.get("item_name"), "dish name", 80)
    price = body.get("price")
    if price in (None, ""):
        price = None
    else:
        try:
            price = round(float(price), 2)
        except (TypeError, ValueError):
            raise ValueError("Type the price as a number of rupees") from None
        if not 0 < price <= 5000:
            raise ValueError("Type a price between ₹1 and ₹5,000")
    with db.cursor() as cur:
        n = cur.execute("SELECT COUNT(*) AS n FROM typed_favourites WHERE user_id=?", (user_id,)).fetchone()["n"]
        if n >= MAX_FAVOURITES:
            raise ValueError(f"Keep up to {MAX_FAVOURITES} typed favourites")
        dup = cur.execute("SELECT 1 FROM typed_favourites WHERE user_id=? AND meal=? AND lower(restaurant_name)=? "
                          "AND lower(item_name)=? AND platform=?",
                          (user_id, meal, restaurant.lower(), item.lower(), platform)).fetchone()
        if not dup:
            cur.execute("INSERT INTO typed_favourites(user_id, meal, restaurant_name, item_name, price, platform, "
                        "added_ts) VALUES (?,?,?,?,?,?,?)",
                        (user_id, meal, restaurant, item, price, platform, clock.now().isoformat(timespec="seconds")))
    return favourites(user_id)


def remove_favourite(user_id: int, fav_id: int) -> dict:
    with db.cursor() as cur:
        cur.execute("DELETE FROM typed_favourites WHERE user_id=? AND id=?", (user_id, fav_id))
    return favourites(user_id)
