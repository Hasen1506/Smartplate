"""Swap's "Nearby now" (UX audit #26, 16 Oct 2026).

Dishes from the person's live catalogue (the menus already read for their address: no new
Swiggy call) that pass their diet and allergy rules, their minimum rating, and fit what is
left of today's budget for this meal, sorted by value (most food for the money, estimated
from the dish name). Their usual meals are left out (Swap already lists those). Two dishes
a place at most, so the list isn't one restaurant. Nothing is picked until they tap one.
"""
from .. import db

LIMIT = 5
PER_PLACE = 2


def _cap(user: dict, meal: str) -> tuple[float | None, str]:
    """(₹ this meal may cost, how that was worked out): today's budget less the other meals
    already picked, or an even share of it when nothing else is picked yet."""
    from .. import today
    budget = today.budget_for(user)["amount"]
    if not budget:
        return None, ""
    meals = today.meals_today(user)
    picks = today._picks(user["id"])
    others = sum((p.get("price") or 0) * (p.get("qty") or 1) for m, p in picks.items() if m != meal and m in meals)
    if others:
        return max(0.0, budget - others), f"₹{max(0.0, budget - others):,.0f} left today"
    share = budget / max(1, len(meals))
    return share, f"₹{share:,.0f} a meal"


def for_meal(user_id: int, meal: str) -> dict:
    from .. import today
    from . import live_catalog
    user = today._user(user_id)
    meal = today._meal(meal)
    if not live_catalog.has_live(user_id):
        return {"meal": meal, "dishes": [], "note": "Ziggy hasn't read menus for your address yet."}
    rules = today.settings(user_id)
    cap, cap_text = _cap(user, meal)
    pooled = {(str(e.get("restaurant_id")), str(e.get("item_id"))) for e in today.pools(user, rules).get(meal, [])}
    with db.cursor() as cur:
        rows = cur.execute("SELECT r.provider_id, r.name AS restaurant, r.rating, m.provider_item_id, m.name, m.price, "
                           "m.veg, m.veg_known, m.allergens, m.tags, m.kcal, m.protein_g, m.nutrition_known "
                           "FROM menu_items m JOIN restaurants r ON r.id=m.restaurant_id WHERE r.city=?",
                           (live_catalog.city_key(user_id),)).fetchall()
    out = []
    for r in rows:
        tags = set(db.jl(r["tags"], []))
        if "side" in tags or "dessert" in tags or (meal == "breakfast" and "breakfast" not in tags):
            continue
        if (str(r["provider_id"]), str(r["provider_item_id"])) in pooled or not r["price"]:
            continue
        if cap is not None and r["price"] > cap:
            continue
        if rules["min_rating"] and r["rating"] is not None and r["rating"] < rules["min_rating"]:
            continue
        veg = bool(r["veg"]) if r["veg_known"] else None
        if today._hard_reason(user, r["name"], veg, db.jl(r["allergens"], [])):
            continue
        value = ((r["kcal"] / 600 + r["protein_g"] / 25) / r["price"]) if r["nutrition_known"] else 0.0
        out.append({"restaurant_id": str(r["provider_id"]), "restaurant": r["restaurant"], "rating": r["rating"],
                    "item_id": str(r["provider_item_id"]), "name": r["name"], "price": r["price"], "veg": veg,
                    "value": round(value, 4)})
    out.sort(key=lambda d: (-d["value"], d["price"]))
    picked, per = [], {}
    for d in out:
        if per.get(d["restaurant_id"], 0) >= PER_PLACE:
            continue
        per[d["restaurant_id"]] = per.get(d["restaurant_id"], 0) + 1
        picked.append(d)
        if len(picked) >= LIMIT:
            break
    return {"meal": meal, "dishes": picked, "within": cap_text,
            "note": (f"From menus Ziggy read for your address, within your rules and {cap_text}, most food for the "
                     "money first (estimated from dish names; dish price before fees).") if cap_text else ""}
