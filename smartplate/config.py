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
