"""§5.3.17 — Cooking-mode coach.

When a plan leans heavily on cook/skip (a "cook-yourself week"), the agent
transitions into coach mode: it pulls the chosen recipes' steps and an aggregated
Instamart basket so the user can actually execute the week. The 2-3 year "personal
food agent" arc, scaffolded now.
"""
from . import reverse_mode


def coach(cook_decisions: list[dict]) -> dict:
    """cook_decisions: decisions with chosen_kind == 'cook' carrying a recipe key."""
    keys = [d["recipe_key"] for d in cook_decisions if d.get("recipe_key")]
    recipes = []
    for d in cook_decisions:
        r = reverse_mode.recipe(d.get("recipe_key", ""))
        if r:
            recipes.append({
                "session": d.get("label", ""),
                "name": r["name"],
                "steps": r["steps"],
                "cost": r["cost"],
            })
    basket = reverse_mode.basket_for_recipes(keys)
    return {
        "active": len(recipes) >= 2,
        "recipes": recipes,
        "basket": basket,
        "headline": f"{len(recipes)} cook sessions this week — grocery run ≈ ₹{basket['total']:.0f}",
    }
