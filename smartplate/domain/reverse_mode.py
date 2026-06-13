"""§5.3.12 — Reverse mode: Instamart for cook-yourself days.

A small library of home-cook recipes, each with an Instamart-style grocery basket.
A cook candidate is offered to the optimiser per session; when chosen it is cheaper
and usually healthier than delivery. No first-party Swiggy agent will recommend
cooking — that's the point (widens the moat).
"""

# Each recipe: per-serving cost, nutrition, and the grocery basket to buy.
RECIPES = [
    {
        "key": "dal_rice", "name": "Dal + rice", "cost": 45, "veg": 1,
        "kcal": 520, "protein_g": 18, "carbs_g": 82, "fat_g": 9, "sugar_g": 3, "carbon_kg": 0.6,
        "basket": [{"name": "Toor dal 500g", "qty": 1, "price": 90},
                   {"name": "Rice 1kg", "qty": 1, "price": 70},
                   {"name": "Onion/tomato", "qty": 1, "price": 40}],
        "steps": ["Pressure-cook dal with turmeric", "Temper with cumin + garlic", "Serve over rice"],
    },
    {
        "key": "veg_pulao", "name": "Veg pulao", "cost": 60, "veg": 1,
        "kcal": 600, "protein_g": 14, "carbs_g": 95, "fat_g": 14, "sugar_g": 5, "carbon_kg": 0.8,
        "basket": [{"name": "Basmati rice 1kg", "qty": 1, "price": 120},
                   {"name": "Mixed veg 500g", "qty": 1, "price": 60},
                   {"name": "Whole spices", "qty": 1, "price": 50}],
        "steps": ["Saute spices + veg", "Add rinsed rice + water", "Cook 12 min"],
    },
    {
        "key": "egg_curry", "name": "Egg curry + roti", "cost": 70, "veg": 0,
        "kcal": 640, "protein_g": 28, "carbs_g": 60, "fat_g": 26, "sugar_g": 6, "carbon_kg": 1.1,
        "basket": [{"name": "Eggs (6)", "qty": 1, "price": 60},
                   {"name": "Atta 1kg", "qty": 1, "price": 55},
                   {"name": "Onion gravy base", "qty": 1, "price": 45}],
        "steps": ["Boil eggs", "Simmer in onion-tomato gravy", "Serve with roti"],
    },
    {
        "key": "oats_bowl", "name": "Masala oats + veg", "cost": 35, "veg": 1,
        "kcal": 380, "protein_g": 13, "carbs_g": 58, "fat_g": 8, "sugar_g": 4, "carbon_kg": 0.4,
        "basket": [{"name": "Oats 1kg", "qty": 1, "price": 110},
                   {"name": "Mixed veg 250g", "qty": 1, "price": 35}],
        "steps": ["Saute veg", "Add oats + water", "Cook 5 min"],
    },
]

RECIPE_BY_MEAL = {
    "breakfast": ["oats_bowl"],
    "lunch": ["dal_rice", "veg_pulao", "egg_curry"],
    "dinner": ["dal_rice", "veg_pulao", "egg_curry"],
}


def recipe(key: str) -> dict | None:
    return next((r for r in RECIPES if r["key"] == key), None)


def cook_candidate(user: dict, meal: str) -> dict | None:
    """Pick the cheapest meal-appropriate recipe that fits the user's diet."""
    keys = RECIPE_BY_MEAL.get(meal, [])
    best = None
    for k in keys:
        r = recipe(k)
        if not r:
            continue
        if user.get("diet") in ("veg", "vegan") and not r["veg"]:
            continue
        if best is None or r["cost"] < best["cost"]:
            best = r
    return best


def basket_for_recipes(keys: list[str]) -> dict:
    items, total, seen = [], 0.0, set()
    for k in keys:
        r = recipe(k)
        if not r:
            continue
        for b in r["basket"]:
            sig = b["name"]
            if sig in seen:
                continue
            seen.add(sig)
            items.append({**b, "recipe": r["name"]})
            total += b["price"]
    return {"items": items, "total": round(total, 2)}
