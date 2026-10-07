"""§5.3.17 — Cooking-mode coach.

When a plan leans heavily on cook/skip (a "cook-yourself week"), the agent
transitions into coach mode: it pulls the chosen recipes' steps and an aggregated
Instamart basket so the user can actually execute the week. The 2-3 year "personal
food agent" arc, scaffolded now.

Ingredient swaps (domain/ingredients.py, ranked by Epicure) ride along: every recipe
lists its ingredients, each swappable for something safe for the whole household; a
swap the user picks (or an out-of-stock replacement) shows in the recipe and on the
grocery list. A recipe someone can't eat is offered as "safe with a swap" when every
offending ingredient has a safe replacement.
"""
from . import epicure, ingredients, reverse_mode


def coach(cook_decisions: list[dict], user: dict | None = None, swaps: dict | None = None) -> dict:
    """cook_decisions: decisions with chosen_kind == 'cook' carrying a recipe key.
    swaps: {token: {"swap_token", "reason"}} chosen for this plan."""
    people = ingredients.people_of(user) if user else []
    # a stored swap that is no longer safe (someone's allergies changed) is dropped
    live = {t: s for t, s in (swaps or {}).items() if not people or ingredients.unsafe_for(people, s["swap_token"]) is None}
    can_swap = bool(user) and epicure.get() is not None

    def swapped(token):
        s = live.get(token) if token else None
        return {"token": s["swap_token"], "name": ingredients.name(s["swap_token"]), "reason": s["reason"]} if s else None

    keys = [d["recipe_key"] for d in cook_decisions if d.get("recipe_key")]
    recipes = []
    for d in cook_decisions:
        r = reverse_mode.recipe(d.get("recipe_key", ""))
        if r:
            recipes.append({
                "session": d.get("label", ""),
                "key": r["key"],
                "name": r["name"],
                "steps": r["steps"],
                "cost": r["cost"],
                "ingredients": [{**i, "swap": swapped(i["token"]),
                                 "swappable": can_swap and i["token"] in ingredients.PANTRY} for i in r.get("ingredients", [])],
            })
    basket = reverse_mode.basket_for_recipes(keys)
    for item in basket["items"]:
        item["swap"] = swapped(item.get("token"))
        item["swappable"] = can_swap and bool(item.get("token"))
    safe_with_swap = []
    if can_swap:
        for r in reverse_mode.RECIPES:
            if reverse_mode.unsafe_reason(user, r) is None:
                continue
            fix = ingredients.make_safe(r, people)
            if fix:
                safe_with_swap.append(fix)
    return {
        "active": len(recipes) >= 2,
        "recipes": recipes,
        "basket": basket,
        "swaps_available": can_swap,
        "safe_with_swap": safe_with_swap,
        "headline": f"{len(recipes)} cook sessions this week — grocery run ≈ ₹{basket['total']:.0f}",
    }
