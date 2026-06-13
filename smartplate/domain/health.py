"""§5.3.15 — Health / longevity. Protein floor, veg count, fasting window.

These layer extra soft penalties (protein/veg) and one hard rule (fasting window):
an item scheduled inside a user's fasting window is excluded for that session.
"""

DEFAULT_HEALTH = {
    "protein_floor_g": 50,     # per day, encouraged
    "veg_servings": 2,         # per day, encouraged
    "fasting_start_min": None, # e.g. 1200 (20:00); None disables
    "fasting_end_min": None,   # e.g. 600  (10:00)
}


def targets_for(user: dict) -> dict:
    t = dict(DEFAULT_HEALTH)
    t.update(user.get("health_targets") or {})
    return t


def in_fasting_window(user: dict, scheduled_min: int) -> bool:
    t = targets_for(user)
    start, end = t.get("fasting_start_min"), t.get("fasting_end_min")
    if start is None or end is None:
        return False
    if start <= end:  # same-day window
        return start <= scheduled_min <= end
    return scheduled_min >= start or scheduled_min <= end  # overnight window


def protein_penalty(user: dict, item: dict) -> float:
    """Reward protein-dense items a little when a protein floor is set."""
    floor = targets_for(user).get("protein_floor_g", 0)
    if not floor:
        return 0.0
    per_meal = floor / 3.0
    return round(max(0.0, (per_meal - item.get("protein_g", 0)) / max(per_meal, 1)), 4)


def is_veg_serving(item: dict) -> bool:
    return bool(item.get("veg", 0)) or "vegetable" in item.get("tags", [])
