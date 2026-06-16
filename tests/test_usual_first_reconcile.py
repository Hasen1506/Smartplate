"""Backend counterparts of the docs decisions:

  • §5.2 usual-first — a kept/swapped diagnostic on every solve + an optional soft
    preference for the user's familiar picks (config.USUAL_FIRST).
  • §3.1 returning-after-a-gap reconcile — unknown days stay unknown (no imputation),
    roll-over nutrients aren't catch-up corrected after a short gap.
  • §1.3 / §4.3 medical exclude-AND-instruct — order-time cart instructions that ride
    along a *safe* item, the soft refinement above the hard exclusion.
"""
from smartplate import config
from smartplate.domain import allergens, ledger, models
from smartplate.kernel import optimizer


# --------------------------------------------------------------------------- #
# §5.2 — usual-first diagnostic (always computed) + gated soft preference
# --------------------------------------------------------------------------- #
def test_diagnostics_carry_usual_first_split(seeded):
    diag = optimizer.optimize(seeded["plan_id"])["diagnostics"]
    assert "usual_first" in diag
    uf = diag["usual_first"]
    # the split is exhaustive over the delivery picks
    assert uf["usual_kept"] + uf["new_swapped"] == uf["delivery_total"]
    assert uf["delivery_total"] >= 0


def test_usual_first_preference_stays_feasible_when_enabled(seeded, monkeypatch):
    monkeypatch.setattr(config, "USUAL_FIRST", "on")
    res = optimizer.optimize(seeded["plan_id"])
    assert res["status"] == "Optimal"
    # every session still resolves to exactly one decision
    assert len(models.decisions_for_plan(seeded["plan_id"])) == 21


def test_usual_pen_is_zero_when_disabled(seeded):
    # default (off) ⇒ no usual premium on candidates, so behaviour is unchanged
    assert config.USUAL_FIRST == "off"
    user, plan = models.get_user(1), models.get_plan(seeded["plan_id"])
    ctx = optimizer.build_context(user, plan)
    session = {"day": "Mon", "meal": "lunch"}
    cands = optimizer.build_candidates(user, plan, session, ctx)
    delivery = [c for c in cands if c["kind"] == "delivery"]
    assert delivery and all(c["usual_pen"] == 0.0 for c in delivery)
    # the familiarity signal is still attached for the diagnostic
    assert all("is_usual" in c and "familiarity" in c for c in delivery)


# --------------------------------------------------------------------------- #
# §3.1 — returning-after-a-gap reconcile
# --------------------------------------------------------------------------- #
def _day(n):
    return f"2026-06-{n:02d}"


def test_reconcile_does_not_impute_unknown_days():
    # window of 5 days, only 2 logged → 3 are unknown and must stay unknown
    entries = {_day(10): {"kcal": 1900, "protein_g": 60},
               _day(11): {"kcal": 2050, "protein_g": 62}}
    window = [_day(d) for d in range(10, 15)]
    r = ledger.reconcile_after_gap(entries, window, kcal_target=2000, protein_floor=60,
                                   today=_day(15))
    assert r["gap_days"] == 3 and r["known_days"] == 2
    assert r["imputed"] is False
    assert r["unknown_days"] == [_day(12), _day(13), _day(14)]
    assert r["reconcile_prompt"] is True


def test_reconcile_does_not_over_correct_rollover_nutrients():
    # a few on-target known days + a gap → no catch-up pile-on
    entries = {_day(d): {"kcal": 2000, "protein_g": 60} for d in (10, 11, 12)}
    window = [_day(d) for d in range(10, 16)]
    r = ledger.reconcile_after_gap(entries, window, kcal_target=2000, protein_floor=60,
                                   today=_day(16))
    assert r["over_correct"] is False
    assert r["nudge_needed"] is False
    # roll-over nutrients (carry on long clocks) are flagged safe; daily ones reset
    assert "kcal" in r["rollover_safe"] and "iron_mg" in r["rollover_safe"]
    assert "protein_g" in r["daily_reset"] and "sugar_g" in r["daily_reset"]


def test_reconcile_nudges_only_on_genuine_sustained_drift():
    # several known days all far UNDER target → a real drift the agent should nudge
    entries = {_day(d): {"kcal": 900, "protein_g": 60} for d in (10, 11, 12, 13)}
    window = [_day(d) for d in range(10, 15)]
    r = ledger.reconcile_after_gap(entries, window, kcal_target=2000, protein_floor=60,
                                   today=_day(15))
    assert r["nudge_needed"] is True
    assert r["over_correct"] is False     # still never a catch-up *pile-on*


# --------------------------------------------------------------------------- #
# §1.3 / §4.3 — medical exclude AND instruct
# --------------------------------------------------------------------------- #
def test_diabetic_gets_no_sugar_instruction_on_a_sweet_item():
    user = {"medical": ["diabetes"]}
    sweet_drink = {"sugar_g": 12, "tags": ["sweet"]}
    notes = allergens.order_instructions(user, sweet_drink)
    assert any("sugar" in n for n in notes)


def test_instruction_is_omitted_when_irrelevant_to_the_dish():
    user = {"medical": ["diabetes"]}
    plain_dal = {"sugar_g": 0, "tags": []}
    assert allergens.order_instructions(user, plain_dal) == []


def test_no_medical_profile_means_no_instructions():
    assert allergens.order_instructions({}, {"sugar_g": 30, "tags": ["sweet"]}) == []


def test_exclusion_still_hard_for_truly_unsafe_items():
    # instruct is the soft refinement; a >20g-sugar dish is still hard-excluded
    user = {"medical": ["diabetes"]}
    unsafe = {"sugar_g": 45, "allergens": [], "tags": ["sweet"]}
    assert allergens.violates(user, unsafe) == "unsafe for diabetes"
