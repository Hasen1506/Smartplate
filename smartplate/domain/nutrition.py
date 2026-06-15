"""§5.2.6 — Nutrition layer. kcal + macros as SOFT constraints.

Per-day targets are split across meals; the optimiser pays a penalty for how far a
chosen item lands from its meal's share of the daily target. Soft, not hard — a
missed macro should never make a plan infeasible (the gap is logged as debt to
repair later; see the ledger spec in SPEC.md §1).

The penalty is a *band*, not a point (docs/optimization-and-ux.md §2): calories
inside ±`tol` of target cost nothing, and overshooting is penalised a little less
than undershooting (an under-fed day is the worse failure). Protein is a one-sided
floor — only being *under* is penalised. `tol` widens with the mode that's willing
to let nutrition give (Tight Week) and tightens for Comfort.
"""

DEFAULT_TARGETS = {"kcal": 2000, "protein_g": 60, "carbs_g": 250, "fat_g": 65, "sugar_g": 40}

# Share of the daily target attributed to each meal.
MEAL_SHARE = {"breakfast": 0.25, "lunch": 0.40, "dinner": 0.35}

# Default ±fraction of the kcal target that counts as "on target" (no penalty).
# The optimiser overrides this per mode via config.MODE_META; this is the fallback
# so callers that don't care about mode (tests, nutrition flags) still get a band.
DEFAULT_KCAL_TOL = 0.15
# Overshooting calories hurts less than undershooting them.
OVER_KCAL_WEIGHT = 0.6


def targets_for(user: dict) -> dict:
    t = dict(DEFAULT_TARGETS)
    t.update(user.get("nutrition_targets") or {})
    return t


def meal_target(user: dict, meal: str) -> dict:
    share = MEAL_SHARE.get(meal, 0.33)
    return {k: v * share for k, v in targets_for(user).items()}


def penalty(user: dict, meal: str, item: dict, tol: float = DEFAULT_KCAL_TOL) -> float:
    """Banded distance from the meal's kcal+protein target (0 = on target / inside band)."""
    tgt = meal_target(user, meal)
    kcal_t = max(tgt["kcal"], 1)
    protein_t = max(tgt["protein_g"], 1)

    # kcal: free inside the band; outside, under is penalised more than over.
    delta = item.get("kcal", 0) - kcal_t
    margin = tol * kcal_t
    if delta > margin:                       # over the band
        kcal_gap = OVER_KCAL_WEIGHT * (delta - margin) / kcal_t
    elif delta < -margin:                    # under the band
        kcal_gap = (-delta - margin) / kcal_t
    else:
        kcal_gap = 0.0

    protein_gap = max(0.0, (protein_t - item.get("protein_g", 0)) / protein_t)
    return round(0.6 * kcal_gap + 0.4 * protein_gap, 4)


def shortfall(items: list[dict], targets: dict, days: int = 7) -> dict:
    """Period roll-up vs target — feeds the feasibility/shortfall surface (docs §1).

    Returns the kcal and protein gap *per day* (negative = under target) so the
    caller can say "you're 14g protein short → repair meal" or price a budget
    recommendation against what the nutrition target actually costs.
    """
    got = summary(items)
    days = max(1, days)
    kcal_day = got["kcal"] / days
    protein_day = got["protein_g"] / days
    return {
        "kcal_per_day": round(kcal_day),
        "kcal_gap_per_day": round(kcal_day - targets.get("kcal", DEFAULT_TARGETS["kcal"])),
        "protein_per_day": round(protein_day),
        "protein_gap_per_day": round(protein_day - targets.get("protein_g", DEFAULT_TARGETS["protein_g"])),
    }


def summary(items: list[dict]) -> dict:
    return {
        "kcal": round(sum(i.get("kcal", 0) for i in items)),
        "protein_g": round(sum(i.get("protein_g", 0) for i in items)),
        "carbs_g": round(sum(i.get("carbs_g", 0) for i in items)),
        "fat_g": round(sum(i.get("fat_g", 0) for i in items)),
        "sugar_g": round(sum(i.get("sugar_g", 0) for i in items)),
    }
