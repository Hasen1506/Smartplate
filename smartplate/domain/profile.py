"""Profile: the handful of answers a new user gives, turned into planner inputs.

Onboarding asks at most five things (diet + allergies, weekly budget, which meals,
an *optional* goal, usual restaurants). Everything else has a computed default.
This module owns the translation:

  • goal + optional body stats → daily kcal / protein targets (Mifflin–St Jeor),
  • which meals to plan, an optional per-day cap, the delivery area,
  • validation, so a bad onboarding payload never half-writes a profile.

Nutrition here is general wellness arithmetic, not medical advice: the formula is
for adults, and pregnancy, childhood and medical conditions need a professional.
"""
import math

from . import nutrition

MEALS = ("breakfast", "lunch", "dinner")
DIETS = ("veg", "nonveg", "vegan")
ALLERGENS = ("peanut", "dairy", "gluten", "egg", "soy", "shellfish", "fish", "sesame", "tree_nut")
MEDICAL = ("diabetes", "hypertension", "celiac")
OBSERVANCES = ("navratri", "ramadan", "karva_chauth")
VARIETY = ("usual", "light", "mixed", "adventurous")

# Canonical Mifflin–St Jeor activity multipliers (5 levels).
ACTIVITY = {"sedentary": 1.2, "light": 1.375, "moderate": 1.55, "active": 1.725, "athlete": 1.9}

# goal → (kcal adjustment/day, protein g per kg body weight, plain-language line)
GOALS = {
    "none": (0, None, "No nutrition goal — we just keep meals balanced."),
    "maintain": (0, 1.0, "Keep your weight steady."),
    "lose": (-450, 1.2, "A gentle deficit (about 0.4 kg a week)."),
    "gain": (300, 1.6, "A modest surplus for building muscle."),
    "protein": (0, 1.6, "Same calories, more protein."),
}
# Never plan below these even when losing weight (general-population safety floors).
MIN_KCAL = {"male": 1500, "female": 1200, "unspecified": 1350}


def bmr(weight_kg: float, height_cm: float, age: int, sex: str) -> float:
    """Mifflin–St Jeor basal metabolic rate. 'unspecified' uses the midpoint constant."""
    constant = {"male": 5, "female": -161}.get(sex, -78)
    return 10 * weight_kg + 6.25 * height_cm - 5 * age + constant


def targets_from_goal(goal: str, body: dict | None) -> tuple[dict, list[str]]:
    """Daily targets for the planner + the human-readable working shown to the user.

    With no body stats (the default — we don't make people type their weight to
    order dinner) we fall back to the population defaults and say so."""
    goal = goal if goal in GOALS else "none"
    adj, per_kg, line = GOALS[goal]
    if not body:
        t = {"kcal": nutrition.DEFAULT_TARGETS["kcal"], "protein_g": nutrition.DEFAULT_TARGETS["protein_g"]}
        if goal == "protein":
            t["protein_g"] = 80
        elif goal == "lose":
            t["kcal"] = 1700
        elif goal == "gain":
            t["kcal"] = 2300
            t["protein_g"] = 75
        return t, [line, "Using typical adult targets — add height and weight in Settings to personalise."]
    w, h, a = body["weight_kg"], body["height_cm"], body["age"]
    sex = body.get("sex", "unspecified")
    factor = ACTIVITY.get(body.get("activity", "light"), ACTIVITY["light"])
    base = bmr(w, h, a, sex)
    tdee = base * factor
    kcal = max(MIN_KCAL.get(sex, MIN_KCAL["unspecified"]), round(tdee + adj, -1))
    protein = round(max(45.0, w * (per_kg or 0.9)))
    working = [line,
               f"Base burn {base:.0f} kcal × activity {factor} = {tdee:.0f} kcal/day"
               + (f", {adj:+d} for your goal" if adj else "") + f" → {kcal:.0f} kcal.",
               f"Protein {protein} g/day ({per_kg or 0.9} g per kg)."]
    return {"kcal": kcal, "protein_g": protein}, working


def meals_planned(user: dict) -> list[str]:
    meals = (user.get("prefs") or {}).get("meals")
    return [m for m in MEALS if m in meals] if meals else list(MEALS)


def daily_cap(user: dict) -> float | None:
    cap = (user.get("prefs") or {}).get("daily_cap")
    return float(cap) if cap else None


# --------------------------------------------------------------------------- #
# Validation — reject the whole payload rather than half-save it
# --------------------------------------------------------------------------- #
def _number(value, name, lo, hi, integer=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name} must be a number")
    if not lo <= value <= hi:
        raise ValueError(f"{name} must be between {lo} and {hi}")
    if integer and int(value) != value:
        raise ValueError(f"{name} must be a whole number")
    return int(value) if integer else float(value)


def _choices(value, name, allowed):
    if not isinstance(value, list) or any(not isinstance(v, str) or v not in allowed for v in value):
        raise ValueError(f"Unknown {name} selection")
    return sorted(set(value))


def validate_body(body) -> dict | None:
    if body in (None, {}):
        return None
    if not isinstance(body, dict):
        raise ValueError("body must be an object")
    out = {
        "weight_kg": _number(body.get("weight_kg"), "Weight (kg)", 30, 300),
        "height_cm": _number(body.get("height_cm"), "Height (cm)", 120, 230),
        "age": _number(body.get("age"), "Age", 18, 100, integer=True),
        "sex": body.get("sex", "unspecified"),
        "activity": body.get("activity", "light"),
    }
    if out["sex"] not in ("male", "female", "unspecified"):
        raise ValueError("Choose male, female or prefer not to say")
    if out["activity"] not in ACTIVITY:
        raise ValueError("Unknown activity level")
    return out


def validate_setup(body: dict) -> dict:
    """Normalise an onboarding / settings payload. Every field is optional except
    what a first-time profile needs; unknown keys are rejected."""
    allowed = {"name", "area", "diet", "allergens", "medical", "observances", "weekly_budget",
               "daily_cap", "meals", "goal", "body", "favourites", "variety", "rating_floor"}
    if not isinstance(body, dict):
        raise ValueError("Send a JSON object")
    extra = set(body) - allowed
    if extra:
        raise ValueError(f"Unsupported field: {sorted(extra)[0]}")
    out = {}
    if "name" in body:
        if not isinstance(body["name"], str) or not 1 <= len(body["name"].strip()) <= 80:
            raise ValueError("Enter a name of 1–80 characters")
        out["name"] = body["name"].strip()
    if "area" in body:
        if not isinstance(body["area"], str) or len(body["area"]) > 80:
            raise ValueError("Area must be at most 80 characters")
        out["area"] = body["area"].strip()
    if "diet" in body:
        if body["diet"] not in DIETS:
            raise ValueError("Choose vegetarian, non-vegetarian, or vegan")
        out["diet"] = body["diet"]
    for key, allowed_values in (("allergens", ALLERGENS), ("medical", MEDICAL), ("observances", OBSERVANCES)):
        if key in body:
            out[key] = _choices(body[key], key, allowed_values)
    if "weekly_budget" in body:
        out["weekly_budget"] = _number(body["weekly_budget"], "Weekly budget", 100, 100000)
    if "daily_cap" in body:
        out["daily_cap"] = None if body["daily_cap"] in (None, 0) else _number(body["daily_cap"], "Daily limit", 50, 20000)
    if "rating_floor" in body:
        out["rating_floor"] = _number(body["rating_floor"], "Minimum rating", 0, 5)
    if "meals" in body:
        meals = _choices(body["meals"], "meal", MEALS)
        if not meals:
            raise ValueError("Pick at least one meal to plan")
        out["meals"] = [m for m in MEALS if m in meals]
    if "goal" in body:
        if body["goal"] not in GOALS:
            raise ValueError("Unknown goal")
        out["goal"] = body["goal"]
    if "body" in body:
        out["body"] = validate_body(body["body"])
    if "variety" in body:
        if body["variety"] not in VARIETY:
            raise ValueError("Unknown variety level")
        out["variety"] = body["variety"]
    if "favourites" in body:
        favs = body["favourites"]
        if not isinstance(favs, list) or any(isinstance(f, bool) or not isinstance(f, int) for f in favs):
            raise ValueError("favourites must be a list of restaurant ids")
        if len(favs) > 12:
            raise ValueError("Pick up to 12 usual places")
        out["favourites"] = sorted(set(favs))
    if out.get("daily_cap") and out.get("weekly_budget") and out["daily_cap"] * 7 < out["weekly_budget"] * 0.3:
        raise ValueError("Daily limit is too low to use a meaningful part of the weekly budget")
    return out
