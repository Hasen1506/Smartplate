"""Nutrition ledger — nutrient-specific timescales, NOT one rolling balance.

The naive "everything is a weekly debt/credit" model is wrong, and protein is the
clearest case (docs/optimization-and-ux.md §1):

  • Calories  — weekly energy balance (fat IS the store) → genuinely carries; a
                light day banks a credit toward a later day. We surface that credit
                EXPLICITLY so it's never silently "passed over".
  • Protein   — daily. Muscle protein synthesis is a daily process and protein isn't
                stored, so you can't "repay" Monday's gap on Friday. We track a daily
                floor + a rolling ADHERENCE signal (how many days you hit it) that
                nudges future days — not a netted debt.
  • Micros    — ~30 days. Iron/B12/fat-solubles have real body stores, so a long
                rolling window is correct (this is the existing 30-day watch).
  • Sodium/sugar (medical) — daily hard cap, never averaged or credited away.

Pure functions over per-day intake lists; persistence + repair prescriptions layer
on top with the ledger build (SPEC §1).
"""

# The clock each nutrient actually runs on.
TIMESCALE = {
    "kcal": "weekly",
    "protein_g": "daily",
    "fat_g": "daily",
    "carbs_g": "daily",
    "fiber_g": "monthly",
    "iron_mg": "monthly",
    "sodium_mg": "daily_cap",
    "sugar_g": "daily_cap",
}

CARRIES = {"weekly", "monthly"}   # timescales where a surplus/deficit legitimately accumulates


def timescale(nutrient: str) -> str:
    return TIMESCALE.get(nutrient, "daily")


def carries(nutrient: str) -> bool:
    return timescale(nutrient) in CARRIES


def calorie_credit(intake_by_day: list[float], daily_target: float) -> dict:
    """Weekly energy balance + the credit available *today* from underspending.

    `intake_by_day` is the kcal already eaten on prior days this week. The credit is
    explicit: it's what light earlier days have banked toward today's allowance.
    """
    days = len(intake_by_day)
    consumed = sum(intake_by_day)
    expected = daily_target * days
    balance = round(expected - consumed, 1)        # +ve = under-eaten ⇒ credit; -ve = over ⇒ debt
    return {
        "carries": True, "timescale": "weekly",
        "balance": balance,
        "credit_today": max(0.0, balance),          # surfaced to the user, not hidden
        "today_allowance": round(daily_target + balance, 1),
        "note": ("banked from lighter days — extra headroom today" if balance > 0
                 else "ran ahead earlier — today trims a little" if balance < 0
                 else "on track"),
    }


def protein_adherence(intake_by_day: list[float], daily_floor: float) -> dict:
    """Daily floor adherence — NOT a carried debt. Reports how many days hit the
    floor and what today's floor is; a chronic miss biases future days up, but no
    lump 'repair' nets out a stale gap."""
    days = len(intake_by_day)
    hit = sum(1 for p in intake_by_day if p >= daily_floor)
    short_days = [round(daily_floor - p) for p in intake_by_day if p < daily_floor]
    return {
        "carries": False, "timescale": "daily",
        "today_floor_g": round(daily_floor),
        "days_hit": hit, "days_total": days,
        "adherence_pct": round(100 * hit / days) if days else 0,
        "chronic_miss": len(short_days) >= 3,        # a pattern to nudge, not a debt to repay
        "note": ("hitting protein most days" if days and hit >= max(1, days - 1)
                 else "under on protein several days — biasing upcoming meals higher"),
    }


def micro_status(intake_30d: float, target_30d: float) -> dict:
    """30-day micro coverage (iron, fibre, …) — the timescale where stores matter."""
    pct = round(100 * intake_30d / target_30d) if target_30d else 0
    return {"carries": True, "timescale": "monthly", "pct_of_goal": pct,
            "in_deficit": pct < 90}


def medical_cap_status(day_value: float, cap: float) -> dict:
    """Daily hard cap (sodium/sugar) — never averaged away across days."""
    return {"carries": False, "timescale": "daily_cap",
            "ok": day_value <= cap, "over_by": round(max(0.0, day_value - cap), 1)}
