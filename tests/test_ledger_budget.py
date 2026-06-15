"""Nested day/week/month budgets + the nutrient-specific nutrition ledger.

Pins the corrected model: protein is DAILY (no carry), calories carry WEEKLY (with an
explicit credit), micros run on a ~30-DAY clock, medical caps are daily-hard."""
import datetime as dt

from smartplate import service
from smartplate.domain import intake, ledger
from smartplate.kernel import budget


# --------------------------------------------------------------------------- #
# Nested budgets — tightest binds, underspend rolls forward, credit is explicit
# --------------------------------------------------------------------------- #
def test_tightest_level_binds():
    r = budget.nested_caps(daily=200, weekly=2000, spent_week=0, days_left_in_week=7)
    assert r["binding"] == "day" and r["effective_today_cap"] == 200


def test_underspend_rolls_forward_as_explicit_credit():
    # 3 days in, only ₹200 spent of the week → the rest of the week has extra/day
    r = budget.nested_caps(weekly=2000, spent_week=200, days_left_in_week=4)
    assert r["banked_today"] > 0                      # surplus surfaced, not hidden
    assert r["effective_today_cap"] > 2000 / 7        # more than the flat daily share


def test_flat_week_has_no_phantom_credit():
    r = budget.nested_caps(weekly=2000, spent_week=0, days_left_in_week=7)
    assert abs(r["banked_today"]) < 1.0               # ~0 when nothing was banked


# --------------------------------------------------------------------------- #
# Ledger — nutrient-specific timescales
# --------------------------------------------------------------------------- #
def test_timescales_are_nutrient_specific():
    assert ledger.timescale("protein_g") == "daily" and not ledger.carries("protein_g")
    assert ledger.carries("kcal") and ledger.carries("iron_mg")
    assert ledger.timescale("sodium_mg") == "daily_cap" and not ledger.carries("sodium_mg")


def test_calories_carry_with_explicit_credit():
    r = ledger.calorie_credit([1500, 1500], 2000)     # under-ate two days
    assert r["carries"] and r["credit_today"] > 0
    assert r["today_allowance"] > 2000                # the credit widens today's room


def test_calories_overshoot_is_a_debt_not_a_credit():
    r = ledger.calorie_credit([2200, 2200], 2000)
    assert r["balance"] < 0 and r["credit_today"] == 0.0


def test_protein_is_daily_not_carried():
    r = ledger.protein_adherence([70, 40, 80, 30, 35], 60)
    assert r["carries"] is False and r["timescale"] == "daily"
    assert r["days_hit"] == 2 and r["adherence_pct"] == 40
    assert r["chronic_miss"] is True                  # a pattern to nudge, not a lump to repay


def test_protein_distribution_flags_backloading():
    even = ledger.protein_distribution([20, 20, 20], 60)
    assert even["even_enough"] and even["per_meal_target_g"] == 20
    backloaded = ledger.protein_distribution([5, 5, 50], 60)   # total met, spread poor
    assert backloaded["total_meets_daily"] and not backloaded["even_enough"]
    assert backloaded["meals_under"] == 2


def test_micros_run_monthly_and_medical_caps_daily():
    m = ledger.micro_status(700, 1000)
    assert m["timescale"] == "monthly" and m["in_deficit"]
    cap = ledger.medical_cap_status(25, 20)
    assert not cap["ok"] and cap["over_by"] == 5


# --------------------------------------------------------------------------- #
# Persistence — intake accumulates across days and feeds the rolling ledger
# --------------------------------------------------------------------------- #
def test_intake_persists_and_ledger_accumulates(seeded):
    y = (dt.date.today() - dt.timedelta(days=1)).isoformat()
    t = dt.date.today().isoformat()
    service.log_intake(1, "dal + 2 rotis", iso_date=y, meal="lunch")   # a light prior day
    service.log_intake(1, "oats", iso_date=t, meal="breakfast")
    assert len(intake.recent(1, 7)) == 2

    led = service.nutrition_ledger(1)
    assert led["days_logged"] == 2
    assert led["calories"]["credit_today"] > 0          # light yesterday ⇒ banked credit today
    assert led["protein"]["carries"] is False           # protein stays daily, even persisted
    assert led["today_distribution"] is not None         # today has a logged meal
