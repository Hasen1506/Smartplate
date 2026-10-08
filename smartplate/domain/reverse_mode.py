"""§5.3.12 — Reverse mode: Instamart for cook-yourself days.

A small library of home-cook recipes, each with an Instamart-style grocery basket.
A cook candidate is offered to the optimiser per session; when chosen it is cheaper
and usually healthier than delivery. No first-party Swiggy agent will recommend
cooking — that's the point (widens the moat).
"""


# What a home-cooked meal's cost is: an estimate, never a live price.
COST_BASIS = ("Grocery estimate per serving, from typical Chennai shop pack prices "
              "(Toor dal, rice, vegetables); not a live Instamart price.")


def _ing(*tokens):
    """A recipe's ingredients as Epicure tokens (labels live in domain/ingredients.py)."""
    from .ingredients import name
    return [{"token": t, "name": name(t)} for t in tokens]


# Each recipe: per-serving cost, nutrition, the grocery basket to buy and the full
# ingredient list. A basket line is one shop pack: `price` for a pack of `pack` units, of
# which one serving uses `per_serving` (so the list can scale to how many people eat and
# how often), and names the pantry ingredient it is (`token`), when it is one.
RECIPES = [
    {
        "key": "dal_rice", "name": "Dal + rice", "cost": 45, "veg": 1, "allergens": [], "tags": [],
        "kcal": 520, "protein_g": 18, "carbs_g": 82, "fat_g": 9, "sugar_g": 3, "carbon_kg": 0.6,
        "basket": [{"name": "Toor dal 500g", "qty": 1, "price": 90, "per_serving": 60, "pack": 500, "unit": "g", "token": "toor_dal"},
                   {"name": "Rice 1kg", "qty": 1, "price": 70, "per_serving": 90, "pack": 1000, "unit": "g", "token": "rice"},
                   {"name": "Onion/tomato 500g", "qty": 1, "price": 40, "per_serving": 120, "pack": 500, "unit": "g", "token": None}],
        "ingredients": _ing("toor_dal", "rice", "onion", "tomato", "garlic", "cumin", "turmeric", "sunflower_oil"),
        "steps": ["Pressure-cook dal with turmeric", "Temper with cumin + garlic", "Serve over rice"],
    },
    {
        "key": "veg_pulao", "name": "Veg pulao", "cost": 60, "veg": 1, "allergens": [], "tags": [],
        "kcal": 600, "protein_g": 14, "carbs_g": 95, "fat_g": 14, "sugar_g": 5, "carbon_kg": 0.8,
        "basket": [{"name": "Basmati rice 1kg", "qty": 1, "price": 120, "per_serving": 90, "pack": 1000, "unit": "g", "token": "basmati_rice"},
                   {"name": "Mixed veg 500g", "qty": 1, "price": 60, "per_serving": 120, "pack": 500, "unit": "g", "token": "mixed_vegetable"},
                   {"name": "Whole spices pack", "qty": 1, "price": 50, "per_serving": 1, "pack": 20, "unit": "servings", "token": None}],
        "ingredients": _ing("basmati_rice", "mixed_vegetable", "onion", "cumin", "cardamom", "clove", "cinnamon",
                            "sunflower_oil"),
        "steps": ["Saute spices + veg", "Add rinsed rice + water", "Cook 12 min"],
    },
    {
        "key": "egg_curry", "name": "Egg curry + roti", "cost": 70, "veg": 0,
        "allergens": ["egg", "gluten"], "tags": ["egg", "wheat"],                 # eggs + atta roti
        "kcal": 640, "protein_g": 28, "carbs_g": 60, "fat_g": 26, "sugar_g": 6, "carbon_kg": 1.1,
        "basket": [{"name": "Eggs (6)", "qty": 1, "price": 60, "per_serving": 2, "pack": 6, "unit": "eggs", "token": "egg"},
                   {"name": "Atta 1kg", "qty": 1, "price": 55, "per_serving": 80, "pack": 1000, "unit": "g", "token": "whole_wheat_flour"},
                   {"name": "Onion gravy base", "qty": 1, "price": 45, "per_serving": 1, "pack": 4, "unit": "servings", "token": None}],
        "ingredients": _ing("egg", "whole_wheat_flour", "onion", "tomato", "ginger", "garlic", "turmeric",
                            "chili_powder", "sunflower_oil"),
        "steps": ["Boil eggs", "Simmer in onion-tomato gravy", "Serve with roti"],
    },
    {
        "key": "oats_bowl", "name": "Masala oats + veg", "cost": 35, "veg": 1,
        "allergens": ["gluten"], "tags": ["oats"],     # Indian oats are rarely certified gluten-free
        "kcal": 380, "protein_g": 13, "carbs_g": 58, "fat_g": 8, "sugar_g": 4, "carbon_kg": 0.4,
        "basket": [{"name": "Oats 1kg", "qty": 1, "price": 110, "per_serving": 50, "pack": 1000, "unit": "g", "token": "oat"},
                   {"name": "Mixed veg 250g", "qty": 1, "price": 35, "per_serving": 80, "pack": 250, "unit": "g", "token": "mixed_vegetable"}],
        "ingredients": _ing("oat", "mixed_vegetable", "onion", "green_chili", "turmeric", "sunflower_oil"),
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


def unsafe_reason(user: dict, r: dict) -> str | None:
    """The same hard allergen / medical / diet rules delivery dishes get (allergens.violates)."""
    from . import allergens
    return allergens.violates(user, r)


def safe_recipes(user: dict, meal: str) -> list[dict]:
    """Meal-appropriate recipes that pass every hard rule for this user (household included)."""
    return [r for r in (recipe(k) for k in RECIPE_BY_MEAL.get(meal, [])) if r and unsafe_reason(user, r) is None]


def cook_candidate(user: dict, meal: str) -> dict | None:
    """Pick the cheapest meal-appropriate recipe that passes the user's hard rules."""
    best = None
    for r in safe_recipes(user, meal):
        if best is None or r["cost"] < best["cost"]:
            best = r
    return best


def basket_for_meals(meals: list[dict], have=()) -> dict:
    """One grocery list for these cook meals ([{recipe_key, servings}]): each pack line
    sums what every meal needs, rounds up to whole packs, and names the recipes it is
    for. Lines in `have` (the cook already has them) stay listed but cost nothing."""
    import math
    lines: dict[str, dict] = {}
    for m in meals:
        r = recipe(m.get("recipe_key") or "")
        if not r:
            continue
        servings = max(1, int(m.get("servings") or 1))
        for b in r["basket"]:
            line = lines.setdefault(b["name"], {"name": b["name"], "unit": b["unit"], "pack": b["pack"],
                                                "unit_price": b["price"], "token": b.get("token"),
                                                "need": 0, "recipes": [], "servings": 0})
            line["need"] += b["per_serving"] * servings
            line["servings"] += servings
            if r["name"] not in line["recipes"]:
                line["recipes"].append(r["name"])
    items, total = [], 0.0
    for line in lines.values():
        packs = max(1, math.ceil(line["need"] / line["pack"]))
        price = round(packs * line["unit_price"], 2)
        owned = line["name"] in set(have)
        items.append({"name": line["name"], "qty": packs, "price": price, "unit_price": line["unit_price"],
                      "need": line["need"], "unit": line["unit"], "servings": line["servings"],
                      "recipe": ", ".join(line["recipes"]), "have": owned, "token": line["token"]})
        if not owned:
            total += price
    return {"items": items, "total": round(total, 2)}


def basket_for_recipes(keys: list[str]) -> dict:
    """One serving of each recipe key (the simple form the /basket endpoint used)."""
    return basket_for_meals([{"recipe_key": k, "servings": 1} for k in keys])
