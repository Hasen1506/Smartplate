"""The cost seam (FEASIBILITY.md §2).

AgentBrain decides anything that *could* be done with an LLM. The default
DeterministicBrain uses the solver, local sentiment, and templates — so v1.1 has
no per-decision API spend. The opt-in LLMBrain stub was archived (smartplate/archive/
experiments.py, Oct 2026); this seam stays so a paid path can only ever be added here.
"""
from . import explainability


class DeterministicBrain:
    """Free. Solver + lexicon + templates. This is what v1.1 ships with."""

    name = "deterministic"
    cost_per_decision = 0.0

    def explain(self, decision: dict, context: dict) -> list[str]:
        return explainability.reasons_for(decision, context)

    def narrate(self, reasons: list[str]) -> str:
        return " ".join(reasons)

    def parse_request(self, text: str) -> dict:
        """Tiny rule-based NL: enough for 'skip friday', 'cooked tonight', 'tight week'."""
        t = text.lower()
        intent = {"action": None}
        if "skip" in t:
            intent["action"] = "skip"
        elif "cook" in t or "cooked" in t:
            intent["action"] = "cook"
        elif "sick" in t or "snooze" in t or "pause" in t:
            intent["action"] = "snooze"
        for i, day in enumerate(["mon", "tue", "wed", "thu", "fri", "sat", "sun"]):
            if day in t:
                intent["day"] = i
        for meal in ["breakfast", "lunch", "dinner"]:
            if meal in t:
                intent["meal"] = meal
        if "tight" in t or "survival" in t or "broke" in t:
            intent["action"] = "set_mode"; intent["mode"] = "survival"
        return intent


def get_brain():
    """Always the deterministic brain. The SMARTPLATE_BRAIN=llm stub is archived in
    smartplate/archive/experiments.py (off since June 2026, no tests)."""
    return DeterministicBrain()
