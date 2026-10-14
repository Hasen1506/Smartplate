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


def test_usual_first_preference_stays_feasible(seeded):
    # favourites first is the default since Oct 2026 (was SMARTPLATE_USUAL_FIRST=on)
    assert config.USUAL_FIRST_W > 0 and not hasattr(config, "USUAL_FIRST")
    res = optimizer.optimize(seeded["plan_id"])
    assert res["status"] == "Optimal"
    # every session still resolves to exactly one decision
    assert len(models.decisions_for_plan(seeded["plan_id"])) == 21


def test_usual_pen_without_order_history(seeded):
    # no order history ⇒ no usuals to put first ⇒ no usual premium on candidates
    user, plan = models.get_user(1), models.get_plan(seeded["plan_id"])
    ctx = optimizer.build_context(user, plan)
    assert not ctx["taste"]["history"]
    session = {"day": 0, "meal": "lunch"}          # sessions store day as 0=Mon..6=Sun
    cands = optimizer.build_candidates(user, plan, session, ctx)
    delivery = [c for c in cands if c["kind"] == "delivery"]
    # the sample profile has usual places: those carry no premium; only the few
    # "something new" options do (the mostly-usual default)
    assert delivery and all(c["usual_pen"] == 0.0 for c in delivery if not c["discovery"])
    assert all(c["usual_pen"] == optimizer.DISCOVERY_PEN for c in delivery if c["discovery"])
    assert sum(c["discovery"] for c in delivery) <= optimizer.DISCOVERY_PER_SESSION
    # the familiarity signal is still attached for the diagnostic
    assert all("is_usual" in c and "familiarity" in c for c in delivery)
    # a profile with no usual places is unchanged: no premium anywhere
    arjun = models.get_user(3)
    cands = optimizer.build_candidates(arjun, plan, session, optimizer.build_context(arjun, plan))
    assert all(c["usual_pen"] == 0.0 for c in cands if c["kind"] == "delivery")


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
    # (reason text is plain words since W1's shared checker; the rule is unchanged)
    assert allergens.violates(user, unsafe) == "About 45 g sugar (more than 20 g) (diabetes)"
    assert allergens.check(user, unsafe)["code"] == "diabetes"


def test_usual_first_premium_for_a_never_ordered_dish_once_there_is_history(seeded):
    """Favourites first by default: with order history, a dish never ordered pays
    USUAL_FIRST_W × novelty; the one ordered four times recently pays (almost) nothing."""
    user, plan = models.get_user(3), models.get_plan(seeded["plan_id"])
    ctx = optimizer.build_context(user, plan)
    session = {"day": 0, "meal": "lunch"}
    cands = [c for c in optimizer.build_candidates(user, plan, session, ctx) if c["kind"] == "delivery"]
    assert cands
    usual = cands[0]
    ctx["taste"]["history"] = {usual["item_id"]: {"count": 4, "days_since": 0}}
    cands = [c for c in optimizer.build_candidates(user, plan, session, ctx) if c["kind"] == "delivery"]
    by_id = {c["item_id"]: c for c in cands}
    others = [c for c in cands if c["item_id"] != usual["item_id"] and not c["discovery"]]
    assert by_id[usual["item_id"]]["usual_pen"] == 0.0
    assert others and all(c["usual_pen"] > 0.3 for c in others)
