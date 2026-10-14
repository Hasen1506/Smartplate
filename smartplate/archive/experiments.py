"""Planner and brain experiments that were off since June 2026, archived 14 Oct 2026.

Founder decision (14 Oct 2026, docs/archive-review.md §3): archive the experiment flags that
had been off since June and had no tests. None of this is imported by the app; it is kept
verbatim (lightly trimmed) so it can be reviewed and then deleted, or revived on purpose.

  SMARTPLATE_BRAIN=llm            LLMBrain below; the app always uses DeterministicBrain
                                  (smartplate/kernel/agent_brain.py).
  SMARTPLATE_RATING_FLOOR=soft    soft_rating_filter / soft_rating_pen below; the planner's
  SMARTPLATE_SAFETY_FLOOR         floor is always hard (optimizer._rating_filter).
  SMARTPLATE_VARIETY(_W)          variety_nudge_bonus below; "Surprise me" in Decide for me
                                  covers novelty per request instead of a server flag.
  SMARTPLATE_USUAL_FIRST(_W)      not archived: became the default (config.USUAL_FIRST_W).
"""
from ..kernel.agent_brain import DeterministicBrain

HARD_SAFETY_FLOOR = 3.5          # was SMARTPLATE_SAFETY_FLOOR
VARIETY_NUDGE_W = 0.6            # was SMARTPLATE_VARIETY_W


class LLMBrain(DeterministicBrain):
    """Optional upgrade that was never wired: it fell back to the deterministic join so the
    app never depended on a paid call. Was selected by SMARTPLATE_BRAIN=llm."""

    name = "llm"
    cost_per_decision = None  # depends on provider/model; only paid when used

    def narrate(self, reasons: list[str]) -> str:  # pragma: no cover - archived
        return super().narrate(reasons)


def soft_rating_filter(user: dict, items: list[dict]) -> list[dict]:
    """The "soft" floor: filter only at the low hard safety floor and let the user's ★
    become a penalty, so a high ★ bent under budget (docs §4)."""
    floor = min(float(user.get("rating_floor", 4.0)), HARD_SAFETY_FLOOR)
    return [it for it in items if it["restaurant_rating"] >= floor]


def soft_rating_pen(user: dict, item: dict) -> float:
    """Soft cost for a pick below the user's aspirational ★ (was cand["rating_pen"])."""
    short = max(0.0, float(user.get("rating_floor", 4.0)) - item.get("restaurant_rating", 0))
    return round(0.8 * short, 4)


def variety_nudge_bonus(novelty: float, variety_frac: float) -> float:
    """Was cand["novelty_bonus"], subtracted from the objective: novel picks preferred,
    scaled by the user's variety level (fatigue.VARIETY_LEVELS)."""
    return round(VARIETY_NUDGE_W * novelty * variety_frac, 4)
