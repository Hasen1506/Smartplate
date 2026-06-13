"""The cost seam (FEASIBILITY.md §2).

AgentBrain decides anything that *could* be done with an LLM. The default
DeterministicBrain uses the solver, local sentiment, and templates — so v1.1 has
no per-decision API spend. LLMBrain is a pluggable, opt-in upgrade; it is never on
the critical path. This is the concrete answer to "agentic execution costs a lot":
the expensive path is optional and isolated behind this interface.
"""
from .. import config
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


class LLMBrain(DeterministicBrain):
    """Optional upgrade. Falls back to deterministic everywhere it isn't wired,
    so enabling it can never break planning — it only adds prose/NL when a key
    and budget are present. Intentionally not called in the default code path."""

    name = "llm"
    cost_per_decision = None  # depends on provider/model; only paid when used

    def narrate(self, reasons: list[str]) -> str:  # pragma: no cover - opt-in path
        # A real implementation would call the configured model here. We keep the
        # deterministic join so the app never depends on a paid call to function.
        return super().narrate(reasons)


def get_brain():
    if config.AGENT_BRAIN == "llm":
        return LLMBrain()
    return DeterministicBrain()
