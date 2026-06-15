"""The reworked optimisation model: banded nutrition, the budget recommender,
the novelty/variety model, carbon-off-by-default, and the feasibility diagnostic.

Companion to docs/optimization-and-ux.md."""
from smartplate import config
from smartplate.domain import fatigue, models, nutrition
from smartplate.kernel import optimizer, recommender


# --------------------------------------------------------------------------- #
# Nutrition — band, not a point; asymmetric over/under; protein one-sided
# --------------------------------------------------------------------------- #
def test_on_target_is_inside_the_band(seeded):
    user = models.get_user(1)
    t = nutrition.meal_target(user, "lunch")
    on = {"kcal": t["kcal"], "protein_g": t["protein_g"] + 5}
    assert nutrition.penalty(user, "lunch", on) == 0.0


def test_overshooting_kcal_hurts_less_than_undershooting(seeded):
    user = models.get_user(1)
    t = nutrition.meal_target(user, "lunch")
    swing = t["kcal"] * 0.5
    over = {"kcal": t["kcal"] + swing, "protein_g": t["protein_g"] + 5}
    under = {"kcal": t["kcal"] - swing, "protein_g": t["protein_g"] + 5}
    assert nutrition.penalty(user, "lunch", over) < nutrition.penalty(user, "lunch", under)


def test_wider_tolerance_never_increases_penalty(seeded):
    user = models.get_user(1)
    t = nutrition.meal_target(user, "lunch")
    item = {"kcal": t["kcal"] * 1.3, "protein_g": t["protein_g"]}
    tight = nutrition.penalty(user, "lunch", item, tol=0.08)
    wide = nutrition.penalty(user, "lunch", item, tol=0.28)
    assert wide <= tight


def test_shortfall_reports_per_day_gaps(seeded):
    targets = {"kcal": 2000, "protein_g": 60}
    items = [{"kcal": 1500, "protein_g": 40}] * 7      # under on both
    sf = nutrition.shortfall(items, targets, days=7)
    assert sf["kcal_gap_per_day"] < 0 and sf["protein_gap_per_day"] < 0


def test_mode_tolerance_orders_correctly():
    assert config.mode_meta("survival")["nutri_tol"] > config.mode_meta("comfort")["nutri_tol"]
    for m in ("comfort", "balanced", "survival"):
        assert config.mode_meta(m)["outcome"]


# --------------------------------------------------------------------------- #
# Carbon is OFF by default (carbon_pref == 0 ⇒ no influence)
# --------------------------------------------------------------------------- #
def test_carbon_off_when_pref_zero():
    base = dict(kind="delivery", cost=100, surge_mult=1.0, taste=0.5, nutri=0.0,
                health=0.0, weather_bias=0.0, festival_bias=0.0)
    clean = optimizer._objective({**base, "carbon_pen": 0.0}, config.MODE_WEIGHTS["balanced"], 100, 0.0, 3.0)
    dirty = optimizer._objective({**base, "carbon_pen": 1.0}, config.MODE_WEIGHTS["balanced"], 100, 0.0, 3.0)
    assert clean == dirty                                # pref 0 ⇒ carbon makes no difference
    # but with a real preference it does bite
    weighted = optimizer._objective({**base, "carbon_pen": 1.0}, config.MODE_WEIGHTS["balanced"], 100, 0.6, 3.0)
    assert weighted > dirty


# --------------------------------------------------------------------------- #
# Novelty / variety model (replaces the crude repeat-cap as the real mechanism)
# --------------------------------------------------------------------------- #
def test_novelty_is_inverse_of_popularity_at_cold_start():
    popular = {"id": 1, "popularity": 0.9}
    obscure = {"id": 2, "popularity": 0.1}
    assert fatigue.novelty(obscure) > fatigue.novelty(popular)


def test_pools_split_and_levels_scale():
    items = [{"id": i, "popularity": p} for i, p in enumerate([0.9, 0.8, 0.2, 0.1])]
    pl = fatigue.pools(items)
    assert pl["familiar"] and pl["novel"]
    assert fatigue.target_novel_count("usual", 10) == 0
    assert fatigue.target_novel_count("adventurous", 10) > fatigue.target_novel_count("light", 10)


def test_variety_pref_defaults_without_assuming(seeded):
    assert fatigue.variety_pref(models.get_user(1)) in fatigue.VARIETY_LEVELS


# --------------------------------------------------------------------------- #
# Budget recommender — Floor ≤ Usual ≤ Variety, suggests a sane ★ floor
# --------------------------------------------------------------------------- #
def test_recommend_bands_are_ordered(seeded):
    user = models.get_user(1)
    rec = recommender.recommend(user, ["breakfast", "lunch", "dinner"] * 2)
    assert rec["feasible"]
    assert rec["floor"]["total"] <= rec["usual"]["total"] <= rec["variety"]["total"]


def test_recommend_suggests_a_floor_in_range(seeded):
    user = models.get_user(3)                            # comfort, no allergens
    rec = recommender.recommend(user, ["lunch", "dinner"])
    assert rec["suggested_rating_floor"] in recommender.RATING_FLOOR_CANDIDATES


def test_recommend_handles_one_off_single_meal(seeded):
    user = models.get_user(3)
    rec = recommender.recommend(user, ["dinner"])         # a one-off order
    assert rec["sessions"] == 1 and rec["feasible"]


# --------------------------------------------------------------------------- #
# Feasibility / shortfall diagnostic on every solve
# --------------------------------------------------------------------------- #
def test_optimize_returns_diagnostics(seeded):
    res = optimizer.optimize(seeded["plan_id"])
    diag = res["diagnostics"]
    assert set(diag) >= {"spend", "budget", "headroom", "binding", "nutrition"}
    assert diag["binding"] in ("budget", "nutrition (protein)", "nutrition (calories)", "comfortable")
