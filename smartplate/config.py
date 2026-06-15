"""Runtime configuration.

Every knob here has a safe default so the app runs with zero setup. The single
most important flag is AGENT_BRAIN: it defaults to the *deterministic* brain so
v1.1 incurs no per-decision LLM cost (see FEASIBILITY.md §2).
"""
import os

# Persistence
DB_PATH = os.environ.get("SMARTPLATE_DB", "smartplate.db")

# Brain selection — "deterministic" (free, default) or "llm" (optional, paid).
AGENT_BRAIN = os.environ.get("SMARTPLATE_BRAIN", "deterministic")

# Swiggy MCP provider — "simulated" (default) or "live" (requires real access).
SWIGGY_PROVIDER = os.environ.get("SMARTPLATE_SWIGGY", "simulated")

# Default order-execution policy. Swiggy's cautious posture (§7.1) means we
# default to an editable window rather than silent auto-placement.
ORDER_EDIT_WINDOW_MIN = int(os.environ.get("SMARTPLATE_EDIT_WINDOW", "30"))

# Optimiser objective weights per mode. Survival cranks cost; comfort cranks taste.
MODE_WEIGHTS = {
    "comfort": {"cost": 0.15, "taste": 1.0, "nutrition": 0.4, "carbon": 0.1, "health": 0.4, "surge": 0.3},
    "balanced": {"cost": 0.5, "taste": 0.6, "nutrition": 0.6, "carbon": 0.3, "health": 0.6, "surge": 0.5},
    "survival": {"cost": 1.0, "taste": 0.25, "nutrition": 0.7, "carbon": 0.15, "health": 0.5, "surge": 0.8},
}

MODE_LABELS = {  # internal name -> softer UI label (§7.4)
    "comfort": "Comfort",
    "balanced": "Balanced",
    "survival": "Tight Week",
}

# A mode is not a bag of weights — it is a statement about *which variable is the
# objective and which is the constraint that may give* (docs/optimization-and-ux.md §2).
# `objective`  : what the solve is really chasing.
# `gives`      : what is allowed to bend so the objective can win.
# `nutri_tol`  : ± fraction of a meal's kcal target that counts as "on target"
#                (no penalty). Tight Week lets calories drift; Comfort holds them close.
# `outcome`    : the plain-language line to show the user — surface the behaviour,
#                not the weights (§8: transparency is the trust moat).
MODE_META = {
    "comfort":  {"objective": "nutrition+taste", "gives": "money",     "nutri_tol": 0.08,
                 "outcome": "The best week your budget allows — money's the only limit."},
    "balanced": {"objective": "both-on-target",  "gives": "neither",   "nutri_tol": 0.15,
                 "outcome": "Best taste-for-money that stays on your nutrition target."},
    "survival": {"objective": "cost",            "gives": "nutrition", "nutri_tol": 0.28,
                 "outcome": "The cheapest week that still hits your nutrition."},
}


def mode_meta(mode: str) -> dict:
    return MODE_META.get(mode, MODE_META["balanced"])


# Rating-floor policy. The brand promise is "we never silently substitute below
# your rating floor" — so the *execution/substitution* path is always hard.
# For the *planner*, the floor can optionally be split into a low hard safety
# floor + a soft preference above it, so a high aspirational ★ bends under budget
# instead of exploding it / forcing constant toggling (docs §4 — the ★ footgun).
#   "hard" (default) : the user's ★ is a hard candidate filter (current behaviour).
#   "soft"           : filter only at HARD_SAFETY_FLOOR; the user's ★ becomes a weight.
RATING_FLOOR_MODE = os.environ.get("SMARTPLATE_RATING_FLOOR", "hard")
HARD_SAFETY_FLOOR = float(os.environ.get("SMARTPLATE_SAFETY_FLOOR", "3.5"))

# Variety nudge. When "on", the planner softly biases toward novel (less-familiar)
# delivery picks, scaled by the user's variety level (domain/fatigue). OFF by default
# so it never silently shifts a plan until the user opts into more variety — the
# honest version of the "composition constraint" in docs §4.
VARIETY_NUDGE = os.environ.get("SMARTPLATE_VARIETY", "off")
VARIETY_NUDGE_W = float(os.environ.get("SMARTPLATE_VARIETY_W", "0.6"))

# Protein evenness: a day-level penalty for backloading protein into one meal (the
# per-meal even target is daily_protein / meals-that-day). Set 0 to disable.
PROTEIN_EVEN_W = float(os.environ.get("SMARTPLATE_PROTEIN_EVEN", "0.25"))
