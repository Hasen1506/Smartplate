"""Budget recommender — inverse optimisation (docs/optimization-and-ux.md §5).

Instead of making the user guess a weekly cap, we invert the problem: given the
sessions they switched on, their nutrition target, the ★ floor, and the
serviceable menu nearby, we estimate what their week *should* cost — as a band,
not a single number:

  • Floor   — the cheapest set of picks that still clears nutrition (min-cost).
  • Usual   — what their normal week costs (popular/familiar picks).
  • Variety — Floor's discipline + a novelty premium (the adventurous picks).

It also suggests a ★ floor that won't over-constrain the budget (the footgun in
docs §4), and works for one-off orders (a single-meal "plan") via population
priors when there's no personal history (cold-start honesty).

This is an *estimate* over the candidate menu, deliberately labelled as such — it
is not a second MILP. The planner remains the source of truth for an actual plan.
"""
from ..domain import allergens, fatigue, models, nutrition, surge

MIN_OPTIONS_PER_MEAL = 3      # a ★ floor that leaves fewer than this is "too tight"
RATING_FLOOR_CANDIDATES = [4.6, 4.4, 4.2, 4.0, 3.8, 3.6]


def _expected_cost(item: dict, meal: str) -> float:
    """Delivered cost with a representative surge multiplier folded in.

    Uses a clear-weather baseline (history where it exists, else the per-meal
    prior) so the recommendation isn't optimistic about peak pricing. Rain/storm
    is surfaced separately as a contingency, not baked into the headline number.
    """
    base = item.get("price", 0) + item.get("delivery_fee", 0)
    mult = surge.predict(item.get("city", ""), 0, meal, "clear")
    return round(base * mult, 2)


def _adequate(item: dict, meal: str, tgt: dict) -> bool:
    """A non-starvation pick: a reasonable share of the meal's kcal + protein."""
    return (item.get("kcal", 0) >= 0.6 * tgt["kcal"]
            and item.get("protein_g", 0) >= 0.5 * tgt["protein_g"])


def _eligible(safe: list[dict], floor: float) -> list[dict]:
    return [it for it in safe
            if it.get("restaurant_rating", 0) >= floor and it.get("item_rating", 0) >= floor - 0.3]


def _per_meal_costs(eligible: list[dict], meal: str, user: dict, history) -> dict | None:
    """Floor / usual / variety cost for one meal slot, or None if nothing fits."""
    if not eligible:
        return None
    tgt = nutrition.meal_target(user, meal)
    adequate = [it for it in eligible if _adequate(it, meal, tgt)] or eligible  # fall back if sparse
    cheapest = min(adequate, key=lambda it: _expected_cost(it, meal))
    usual = max(adequate, key=lambda it: it.get("popularity", 0.5))             # the safe default
    pl = fatigue.pools(adequate, history)
    novel = pl["novel"][0] if pl["novel"] else usual
    return {
        "floor": _expected_cost(cheapest, meal), "floor_item": cheapest["name"],
        "usual": _expected_cost(usual, meal), "usual_item": usual["name"],
        "novel": _expected_cost(novel, meal), "novel_item": novel["name"],
        "n_options": len(adequate),
    }


def recommend(user: dict, meals: list[str], *, history: dict | None = None,
              menu: list[dict] | None = None) -> dict:
    """Recommend a budget band for the given session meals (one entry per session)."""
    menu = menu if menu is not None else models.menu_for_city(user["city"])
    safe = allergens.safe_items(user, menu)
    user_floor = float(user.get("rating_floor", 4.0))
    level = fatigue.variety_pref(user)
    n = len(meals)

    eligible = _eligible(safe, user_floor)
    per_meal = [(_per_meal_costs(eligible, m, user, history), m) for m in meals]
    feasible = [(pm, m) for pm, m in per_meal if pm]

    if not feasible:
        return {
            "sessions": n, "feasible": False,
            "assumptions": [f"No option clears your ★{user_floor:.1f} floor nearby — "
                            f"lower the floor or widen your area."],
            "suggested_rating_floor": _suggest_floor(safe, meals, user),
        }

    floor_total = round(sum(pm["floor"] for pm, _ in feasible), 2)
    usual_total = round(sum(pm["usual"] for pm, _ in feasible), 2)

    # Variety: keep the usual picks, but upgrade `k` slots (those with the biggest
    # novel-over-usual quality reach) to a novel pick and pay that premium.
    k = fatigue.target_novel_count(level, len(feasible))
    premiums = sorted((max(0.0, pm["novel"] - pm["usual"]) for pm, _ in feasible), reverse=True)
    variety_total = round(usual_total + sum(premiums[:k]), 2)

    suggested = _suggest_floor(safe, meals, user)
    return {
        "sessions": n, "feasible": True,
        "variety_level": level, "novel_picks": k,
        "floor": {"total": floor_total,
                  "line": "the cheapest week that still hits your nutrition"},
        "usual": {"total": usual_total,
                  "line": "what your normal week costs (popular picks)"},
        "variety": {"total": variety_total,
                    "line": f"room to try {k} new dish{'es' if k != 1 else ''} this period"},
        "suggested_rating_floor": suggested,
        "rain_contingency_pct": 25,   # peak/rain can add ~this much; see surge model
        "assumptions": [
            "Estimate over nearby serviceable dishes — not a full plan solve.",
            "Surge priced from a clear-weather baseline; rain/storm adds the contingency above.",
            "Familiarity proxied by popularity until your order history fills (cold-start).",
        ],
    }


def _suggest_floor(safe: list[dict], meals: list[str], user: dict) -> float:
    """Highest ★ floor that still leaves enough options every meal — so raising
    the floor past this is what 'explodes the budget / forces toggling'."""
    for floor in RATING_FLOOR_CANDIDATES:
        elig = _eligible(safe, floor)
        ok = True
        for m in set(meals):
            tgt = nutrition.meal_target(user, m)
            adequate = [it for it in elig if _adequate(it, m, tgt)]
            if len(adequate) < MIN_OPTIONS_PER_MEAL:
                ok = False
                break
        if ok:
            return floor
    return min(RATING_FLOOR_CANDIDATES)
