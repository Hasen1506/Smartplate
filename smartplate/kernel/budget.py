"""Budget envelope — planned vs actual + variance alerts.

Domain-agnostic: the same logic the brainstorm shares with the Debt/EMI tool
(§2). Here it tracks a weekly food envelope and flags when actual diverges from
plan so the planner can tighten later sessions.
"""


def envelope(budget: float, decisions: list[dict]) -> dict:
    spend = sum(d.get("cost", 0) for d in decisions)
    remaining = budget - spend
    return {
        "budget": round(budget, 2),
        "spend": round(spend, 2),
        "remaining": round(remaining, 2),
        "pct_used": round(100 * spend / budget, 1) if budget else 0.0,
        "over": spend > budget,
    }


def variance(planned: list[dict], actual: list[dict]) -> dict:
    """Planned-vs-actual once orders execute (substitutions/skips shift cost)."""
    p = sum(d.get("cost", 0) for d in planned)
    a = sum(d.get("cost", 0) for d in actual)
    delta = a - p
    level = "ok"
    if p and abs(delta) / p > 0.15:
        level = "high"
    elif p and abs(delta) / p > 0.05:
        level = "watch"
    return {"planned": round(p, 2), "actual": round(a, 2),
            "delta": round(delta, 2), "level": level}


def per_session_cap(remaining_budget: float, remaining_sessions: int) -> float:
    if remaining_sessions <= 0:
        return remaining_budget
    return max(0.0, remaining_budget / remaining_sessions)
