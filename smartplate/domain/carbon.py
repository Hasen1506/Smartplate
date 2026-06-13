"""§5.3.16 — Carbon / sustainability scoring.

Per-item CO2e estimate as an OPTIONAL soft objective, weighted by the user's
carbon preference (0 = ignore). Estimates are deliberately coarse and labelled
as such — no greenwashing, per the brainstorm's caution (§5.3.16).
"""

# Rough CO2e (kg) priors by cuisine when an item lacks an explicit value.
CUISINE_PRIOR = {
    "beef": 6.0, "lamb": 5.5, "mutton": 5.5, "chicken": 1.8, "pork": 2.0,
    "seafood": 1.6, "egg": 1.1, "dairy": 1.4, "veg": 0.9, "vegan": 0.6,
    "mixed": 1.5, "biryani": 2.4, "thali": 1.2, "salad": 0.5,
}


def estimate(item: dict) -> float:
    if item.get("carbon_kg"):
        return float(item["carbon_kg"])
    return CUISINE_PRIOR.get(item.get("cuisine", "mixed"), 1.5)


def band(kg: float) -> str:
    return "low" if kg < 1.0 else "medium" if kg < 2.5 else "high"


def penalty(item: dict) -> float:
    """Normalise to ~[0,1] against a 6kg worst case."""
    return round(min(1.0, estimate(item) / 6.0), 4)
