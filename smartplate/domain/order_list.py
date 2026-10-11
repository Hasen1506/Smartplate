"""The order list: every dish from the person's saved places and meal pools as one
plain list (dish × price × quantity), not a feed to scroll.

Only dishes that pass their hard rules are listed (the count of the rest is shown).
Rows are sorted for the meal asked about: dishes in that meal's pool first, then
dishes the restaurant files under that meal ("Tiffin" for breakfast, "Meals" for
lunch), then by price. Sides and treats are listed too but marked, because people
order a curry with a bread; the planner only leaves them out of whole meals.
"""
from __future__ import annotations

from . import allergens, live_catalog, meal_pools, models

MEALS = ("breakfast", "lunch", "dinner")
MAX_QUANTITY = 8            # swiggy_live.MAX_PORTIONS: more is a party order, made in Swiggy


def view(user_id: int, meal: str | None = None, session_id: int | None = None) -> dict:
    from .. import everyday
    from ..kernel import optimizer
    user = models.get_user(user_id)
    if not user:
        raise ValueError("Profile not found")
    if meal is not None and meal not in MEALS:
        raise ValueError("meal must be breakfast, lunch or dinner")
    limits, session_label = None, None
    if session_id is not None:
        session, plan, owner = everyday._session_bundle(session_id)
        if owner["id"] != user_id:
            raise ValueError("That meal isn't in this profile's plan")
        meal = meal or session["meal"]
        limits = everyday._limits(plan, owner, session)
        session_label = f"{models.DAYS[session['day']]} {session['meal']}"
    menu = models.menu_for_user(user)
    safe = allergens.safe_items(user, menu)
    pools = meal_pools.for_planner(user_id)
    pooled = {iid: [m for m, p in pools.items() if iid in p["ids"]] for iid in {i for p in pools.values() for i in p["ids"]}}
    fit = optimizer.meal_fit_pen(safe, meal) if meal else (lambda it: 0.0)
    rows = []
    for it in safe:
        tags = it.get("tags") or []
        rows.append({
            "id": it["id"], "name": it["name"], "price": it["price"],
            "delivery_fee": it.get("delivery_fee") or 0, "fee_estimated": it.get("delivery_fee_estimated", True),
            "restaurant": it["restaurant_name"], "restaurant_id": it.get("restaurant_provider_id"),
            "provider_item_id": it.get("provider_item_id"),
            "veg": bool(it.get("veg")), "side": not optimizer.meal_suitable(it, meal or "dinner"),
            "pooled": pooled.get(it["id"], []),
            "fits_meal": bool(meal) and fit(it) == 0.0 and optimizer.meal_suitable(it, meal),
        })
    rows.sort(key=lambda r: (0 if meal and meal in r["pooled"] else 1, 0 if r["fits_meal"] else 1,
                             1 if r["side"] else 0, r["price"], r["name"].lower()))
    places = sorted({r["restaurant"] for r in rows})
    return {"meal": meal, "session_id": session_id, "session_label": session_label, "limits": limits,
            "dishes": rows, "hidden": len(menu) - len(safe), "places": places,
            "live": live_catalog.has_live(user_id), "max_quantity": MAX_QUANTITY}
