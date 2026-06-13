"""§5.2.6 — Nutrition layer. kcal + macros as SOFT constraints.

Per-day targets are split across meals; the optimiser pays a penalty proportional
to how far a chosen item lands from its meal's share of the daily target. Soft, not
hard — a missed macro should never make a plan infeasible.
"""

DEFAULT_TARGETS = {"kcal": 2000, "protein_g": 60, "carbs_g": 250, "fat_g": 65, "sugar_g": 40}

# Share of the daily target attributed to each meal.
MEAL_SHARE = {"breakfast": 0.25, "lunch": 0.40, "dinner": 0.35}


def targets_for(user: dict) -> dict:
    t = dict(DEFAULT_TARGETS)
    t.update(user.get("nutrition_targets") or {})
    return t


def meal_target(user: dict, meal: str) -> dict:
    share = MEAL_SHARE.get(meal, 0.33)
    return {k: v * share for k, v in targets_for(user).items()}


def penalty(user: dict, meal: str, item: dict) -> float:
    """Normalised distance from the meal's kcal+protein target (0 = on target)."""
    tgt = meal_target(user, meal)
    kcal_t = max(tgt["kcal"], 1)
    protein_t = max(tgt["protein_g"], 1)
    kcal_gap = abs(item.get("kcal", 0) - kcal_t) / kcal_t
    protein_gap = max(0.0, (protein_t - item.get("protein_g", 0)) / protein_t)
    return round(0.6 * kcal_gap + 0.4 * protein_gap, 4)


def summary(items: list[dict]) -> dict:
    return {
        "kcal": round(sum(i.get("kcal", 0) for i in items)),
        "protein_g": round(sum(i.get("protein_g", 0) for i in items)),
        "carbs_g": round(sum(i.get("carbs_g", 0) for i in items)),
        "fat_g": round(sum(i.get("fat_g", 0) for i in items)),
        "sugar_g": round(sum(i.get("sugar_g", 0) for i in items)),
    }
