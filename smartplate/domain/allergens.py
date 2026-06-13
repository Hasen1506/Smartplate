"""§5.1.1 — Allergy & medical-condition handling as HARD constraints.

This is the liability-critical feature. Items that violate a user's allergen or
medical profile are removed from the candidate set *before* optimisation, so the
solver cannot select them under any objective — including Survival/Tight-Week
mode. Duty of care beats budget, always.
"""

# Medical conditions expressed as hard limits on a per-item basis.
MEDICAL_RULES = {
    # diabetes: cap added sugar per item; reject very high-sugar dishes outright.
    "diabetes": lambda item: item.get("sugar_g", 0) <= 20,
    # hypertension: avoid items explicitly tagged high-sodium.
    "hypertension": lambda item: "high_sodium" not in item.get("tags", []),
    # celiac: treated as a gluten allergen too, but keep an explicit rule.
    "celiac": lambda item: "gluten" not in item.get("allergens", []),
}


def violates(user: dict, item: dict) -> str | None:
    """Return a human reason string if the item is unsafe for the user, else None."""
    user_allergens = set(user.get("allergens", []))
    item_allergens = set(item.get("allergens", []))
    clash = user_allergens & item_allergens
    if clash:
        return f"contains {', '.join(sorted(clash))} (allergen)"

    for condition in user.get("medical", []):
        rule = MEDICAL_RULES.get(condition)
        if rule and not rule(item):
            return f"unsafe for {condition}"

    diet = user.get("diet", "nonveg")
    if diet in ("veg", "vegan") and not item.get("veg", 1):
        return f"not {diet}"
    if diet == "vegan" and "dairy" in item_allergens:
        return "contains dairy (vegan)"
    return None


def safe_items(user: dict, items: list[dict]) -> list[dict]:
    """Filter a candidate list down to only items safe for the user."""
    return [it for it in items if violates(user, it) is None]


def household_safe(members: list[dict], item: dict) -> str | None:
    """Group mode (§5.2.7): an item must be safe for EVERY member. The union of
    all members' allergens/medical rules is the hard constraint."""
    for m in members:
        reason = violates(m, item)
        if reason:
            return f"{m['name']}: {reason}"
    return None
