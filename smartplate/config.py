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

# Solver limits: relative optimality gap and a hard time cap per weekly solve.
SOLVER_GAP = float(os.environ.get("SMARTPLATE_SOLVER_GAP", "0.001"))
SOLVER_TIME_LIMIT_S = float(os.environ.get("SMARTPLATE_SOLVER_TIME_LIMIT", "10"))

# Plan stability: bonus for keeping a meal's current pick on a re-plan (0 disables).
STABILITY_W = float(os.environ.get("SMARTPLATE_STABILITY", "0.3"))

# Weather — "live" (Open-Meteo forecast, cached; falls back to the sample feed when
# offline) or "simulated" (sample feed only; used by the test-suite for determinism).
WEATHER_PROVIDER = os.environ.get("SMARTPLATE_WEATHER", "live")
WEATHER_TIMEOUT_S = float(os.environ.get("SMARTPLATE_WEATHER_TIMEOUT", "3"))

# Swiggy sign-in + read-only discovery (docs/vendor/swiggy/README.md).
SWIGGY_MCP_BASE = os.environ.get("SMARTPLATE_SWIGGY_MCP", "https://mcp.swiggy.com").rstrip("/")
SWIGGY_TIMEOUT_S = float(os.environ.get("SMARTPLATE_SWIGGY_TIMEOUT", "10"))
# Public base URL for the OAuth redirect when a proxy hides it (else derived per request).
# Render sets RENDER_EXTERNAL_URL for every web service, so hosted installs need no setup.
PUBLIC_URL = (os.environ.get("SMARTPLATE_PUBLIC_URL") or os.environ.get("RENDER_EXTERNAL_URL") or "").rstrip("/")

# Server secret for encrypting stored tokens. render.yaml generates one; without it a
# random key is created once and kept in the database (fine for a trial, not for
# production, where the key must live outside the data it protects).
SECRET = os.environ.get("SMARTPLATE_SECRET", "")
# Behind one reverse proxy (Render, Codespaces): trust its X-Forwarded-* headers.
BEHIND_PROXY = os.environ.get("SMARTPLATE_BEHIND_PROXY", "1" if os.environ.get("RENDER") else "0") == "1"

# Web Push (order-time reminders). Keys are generated on first use when unset.
PUSH_CONTACT = os.environ.get("SMARTPLATE_PUSH_CONTACT", "mailto:smartplate@example.invalid")
PUSH_TICK_S = float(os.environ.get("SMARTPLATE_PUSH_TICK", "60"))
PUSH_ENABLED = os.environ.get("SMARTPLATE_PUSH", "on") == "on"
VAPID_PRIVATE = os.environ.get("SMARTPLATE_VAPID_PRIVATE", "")   # base64url P-256 key; generated if unset

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

# Usual-first (docs §5.2): a soft preference for the user's *familiar* picks so the
# planner makes the fewest substitutions that still clear the locks/targets — a novel
# pick pays a small premium and only wins when it buys real goal-fit. OFF by default
# (the kept/swapped diagnostic is always computed regardless); the budget recommender
# and the variety nudge already cover the explore side.
USUAL_FIRST = os.environ.get("SMARTPLATE_USUAL_FIRST", "off")
USUAL_FIRST_W = float(os.environ.get("SMARTPLATE_USUAL_FIRST_W", "0.5"))
