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


def nested_caps(*, daily=None, weekly=None, monthly=None,
                spent_today=0.0, spent_week=0.0, spent_month=0.0,
                days_left_in_week=1, days_left_in_month=1) -> dict:
    """Day / week / month budgets coexist — the **tightest remaining** binds, and
    underspend **rolls forward** (docs/optimization-and-ux.md §1).

    Today's effective cap is the smallest of each level's remaining-per-day. Crucially
    we surface `banked_today` *explicitly* (today's cap minus the flat daily share) so
    a credit earned by underspending earlier is shown to the user, never silently
    "passed over".
    """
    levels = {}
    if daily is not None:
        levels["day"] = {"cap": round(daily, 2), "spent": round(spent_today, 2),
                         "remaining": round(daily - spent_today, 2), "per_day": daily - spent_today}
    if weekly is not None:
        rem = weekly - spent_week
        levels["week"] = {"cap": round(weekly, 2), "spent": round(spent_week, 2),
                          "remaining": round(rem, 2), "per_day": rem / max(1, days_left_in_week)}
    if monthly is not None:
        rem = monthly - spent_month
        levels["month"] = {"cap": round(monthly, 2), "spent": round(spent_month, 2),
                           "remaining": round(rem, 2), "per_day": rem / max(1, days_left_in_month)}
    if not levels:
        return {"levels": {}, "effective_today_cap": 0.0, "binding": None, "banked_today": 0.0}

    binding = min(levels, key=lambda k: levels[k]["per_day"])
    effective = max(0.0, levels[binding]["per_day"])
    # the flat daily share you'd get with no rollover — the baseline to compare against
    flat = (weekly / 7 if weekly is not None else
            daily if daily is not None else monthly / 30)
    return {
        "levels": {k: {**v, "per_day": round(v["per_day"], 2)} for k, v in levels.items()},
        "effective_today_cap": round(effective, 2),
        "binding": binding,
        "banked_today": round(effective - flat, 2),   # +ve = extra today from underspending
    }
