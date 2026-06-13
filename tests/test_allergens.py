"""§5.1.1 — the liability-critical guarantee: allergen/medical items are NEVER
selected, under any mode, and household plans satisfy every member."""
from smartplate.domain import allergens, models
from smartplate.kernel import optimizer


def _menu(city="Chennai"):
    return models.menu_for_city(city)


def test_peanut_and_diabetes_items_filtered(seeded):
    user = models.get_user(1)  # peanut allergy + diabetes
    safe = allergens.safe_items(user, _menu())
    for it in safe:
        assert "peanut" not in it["allergens"]
        assert it["sugar_g"] <= 20  # diabetes hard rule


def test_optimizer_never_picks_unsafe_in_survival(seeded):
    """Even Survival/Tight-Week mode (max budget pressure) must not violate safety."""
    optimizer.optimize(seeded["plan_id"])
    user = models.get_user(1)
    items = {i["id"]: i for i in _menu()}
    for d in models.decisions_for_plan(seeded["plan_id"]):
        if d["chosen_kind"] == "delivery" and d["item_id"]:
            assert allergens.violates(user, items[d["item_id"]]) is None


def test_vegan_excludes_dairy_and_nonveg(seeded):
    user = models.get_user(2)  # vegan + dairy allergen
    for it in allergens.safe_items(user, _menu()):
        assert it["veg"] == 1
        assert "dairy" not in it["allergens"]


def test_household_union_is_strictest(seeded):
    members = models.get_household_members(1)  # Meera (vegan/dairy) + Arjun (nonveg)
    nonveg = next(i for i in _menu() if i["veg"] == 0)
    assert allergens.household_safe(members, nonveg) is not None  # blocked for the vegan
    dairy = next(i for i in _menu() if "dairy" in i["allergens"])
    assert allergens.household_safe(members, dairy) is not None
