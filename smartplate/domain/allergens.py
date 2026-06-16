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

# The "instruct" half of exclude-and-instruct (docs/optimization-and-ux.md §1.3,
# design-audit §4.3): hard exclusion is the floor, but many dishes are safe *with a
# request*, so we attach order-time cart instructions to keep adjustable dishes in
# the candidate set instead of pruning the whole menu down to pre-safe items.
MEDICAL_INSTRUCTIONS = {
    "diabetes": "no added sugar / less sweet",
    "hypertension": "less salt — no extra salt",
}


def order_instructions(user: dict, item: dict | None = None) -> list[str]:
    """Cart special-instructions implied by the user's medical profile.

    These ride along on a *safe* item's order so the kitchen adjusts it (e.g. a
    diabetic's drink comes unsweetened) — the soft refinement above the hard
    exclusion in `violates`. `item` is optional: when given, an instruction is only
    added if it's actually relevant to that dish (e.g. don't say "no sugar" on a
    plain dal), keeping the note set tight.
    """
    notes: list[str] = []
    for condition in user.get("medical", []):
        msg = MEDICAL_INSTRUCTIONS.get(condition)
        if not msg:
            continue
        if item is not None and not _instruction_relevant(condition, item):
            continue
        if msg not in notes:
            notes.append(msg)
    return notes


def _instruction_relevant(condition: str, item: dict) -> bool:
    """Whether a medical instruction actually applies to this dish."""
    if condition == "diabetes":
        # relevant to anything with some sugar (drinks, desserts, sweet gravies)
        return item.get("sugar_g", 0) > 0 or "sweet" in item.get("tags", [])
    if condition == "hypertension":
        return True   # salt is near-universal in restaurant food
    return True


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
