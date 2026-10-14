"""W1 — fail-closed safety (sp-grill #37, #38, #39, #40, #60; usability safety findings 1–6).

Layers:
- the shared food lexicon, one test per word family and language (#38, #39, #60);
- estimates never write "sugar 0" or "veg" for what isn't known (#37, #38);
- one checker for every path, with a reason per hidden dish (usability 5, 6);
- the hostile "Kadal Kozhi Mess" menu (tests/swiggy_town.py) end to end: live menu, dish
  search, have-for-dinner, meal pools, cart review and a whole planned week;
- Hypothesis properties: nothing unsafe or unknown is ever offered, planned or carted to a
  veg, vegan, allergy or diabetic profile, and inference only ever adds restrictions.
"""
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from smartplate import config
from smartplate.app import create_app
from smartplate.domain import (allergens, dish_library, food_lexicon, food_words, ingredients, live_catalog,
                               models)
from smartplate.integrations import swiggy_connect, swiggy_live
from gt_support import check_plan, item_violation
from swiggy_town import HOSTILE, TownFake
from test_followups import _connect

# --------------------------------------------------------------------------- #
# 1. The lexicon (#38, #39, #60)
# --------------------------------------------------------------------------- #
NONVEG = [("Kozhi Varuval", "chicken"), ("Koli Saaru", "chicken"), ("Nattukozhi Rasam", "chicken"),
          ("Eral Thokku", "prawn"), ("Era Varuval", "prawn"), ("Nandu Masala", "crab"),
          ("Meen Kuzhambu", "fish"), ("Karimeen Pollichathu", "fish"), ("Vanjaram Fry", "fish"),
          ("Mutton Sukka", "meat"), ("Aatu Kari Kuzhambu", "goat"), ("Kari Dosai", "meat"),
          ("Kheema Pav", "minced"), ("Gosht Korma", "meat"), ("Murgh Makhani", "chicken"),
          ("Machli Fry", "fish"), ("Jhinga Masala", "prawn"), ("Chemmeen Curry", "prawn"),
          ("Kodi Pulao", "chicken"), ("Mamsam Vepudu", "meat"), ("Erachi Pathiri", "meat"),
          ("Royyala Iguru", "prawn"), ("Muttai Parotta", "egg"), ("Anda Curry", "egg"),
          ("Egg Fried Rice", "egg"), ("Chicken 65", "chicken")]
ALLERGEN = [("Verkadalai Sundal", "peanut"), ("Kadalai Mittai", "peanut"), ("Moongphali Chaat", "peanut"),
            ("Peanut Chikki", "peanut"), ("Groundnut Chutney", "peanut"), ("Satay Veg Bowl", "peanut"),
            ("Palli Chutney", "peanut"),
            ("Kaju Curry", "tree_nut"), ("Cashew Pulao", "tree_nut"), ("Badam Milk", "tree_nut"),
            ("Pista Kulfi", "tree_nut"), ("Akhrot Halwa", "tree_nut"), ("Munthiri Payasam", "tree_nut"),
            ("Veg Korma", "tree_nut"),
            ("Butter Naan", "gluten"), ("Kothu Parotta", "gluten"), ("Tandoori Roti", "gluten"),
            ("Chapati", "gluten"), ("Maida Puri", "gluten"), ("Rava Dosa", "gluten"), ("Sooji Halwa", "gluten"),
            ("Semiya Upma", "gluten"), ("Vermicelli Kheer", "gluten"),
            ("Paneer Tikka", "dairy"), ("Ghee Roast", "dairy"), ("Curd Rice", "dairy"), ("Thayir Sadam", "dairy"),
            ("Mor Kuzhambu", "dairy"), ("Malai Kofta", "dairy"), ("Khoa Barfi", "dairy"), ("Rasmalai", "dairy"),
            ("Ellu Sadam", "sesame"), ("Til Ladoo", "sesame"), ("Soya Chunks Fry", "soy"),
            ("Eral Thokku", "shellfish"), ("Nandu Masala", "shellfish"), ("Meen Fry", "fish")]
SWEET = ["Kesari", "Rasmalai", "Gulab Jamun", "Paal Payasam", "Carrot Halwa", "Jalebi", "Mysore Pak",
         "Sakkarai Pongal", "Boondi Laddu", "Badam Halwa", "Double Ka Meetha", "Kulfi"]
NOT_WHAT_IT_SAYS = [("Eggless Chocolate Cake", "egg"), ("Brinjal Eggplant Fry", "nonveg"), ("Veg Keema", "nonveg"),
                    ("Muttai Kose Poriyal", "nonveg"), ("Kari Patta Rice", "nonveg"), ("Kadai Paneer", "nonveg"),
                    ("Peanut Butter Toast", "dairy"), ("Coconut Milk Rice", "dairy"), ("Sweet Corn Soup", "sweet"),
                    ("Rice Flour Dosa", "gluten"), ("Kara Kozhukattai", "sweet"), ("Gobi 65", "nonveg")]


@pytest.mark.parametrize("name,meaning", NONVEG)
def test_lexicon_knows_meat_fish_and_egg_in_indian_languages(name, meaning):
    hits = food_lexicon.read(name).nonveg
    assert hits, name
    assert any(meaning in h.gloss for h in hits), [h.gloss for h in hits]


@pytest.mark.parametrize("name,family", ALLERGEN)
def test_lexicon_knows_allergens_in_indian_languages(name, family):
    assert family in food_lexicon.read(name).allergens, name


@pytest.mark.parametrize("name", SWEET)
def test_lexicon_knows_desserts(name):
    assert food_lexicon.read(name).sweet, name


@pytest.mark.parametrize("name,kind", NOT_WHAT_IT_SAYS)
def test_lexicon_ignores_look_alikes(name, kind):
    r = food_lexicon.read(name)
    if kind == "nonveg":
        assert not r.nonveg, [h.words for h in r.nonveg]
    elif kind == "sweet":
        assert not r.sweet
    else:
        assert kind not in r.allergens


def test_lexicon_explains_itself_in_plain_words():
    assert food_lexicon.read("Kozhi Varuval").nonveg[0].words == "Kozhi means chicken (Tamil/Malayalam)"
    assert food_lexicon.read("Kesari").sweet[0].words == "Kesari is an Indian sweet"
    assert food_lexicon.read("Chicken Biryani").nonveg[0].words == "The name says “chicken”"


def test_one_lexicon_three_readers_agree():
    """#60: live menus, recipe imports and pantry swaps used three different lists."""
    for name, _ in NONVEG:
        token = "_".join(food_lexicon.normalise(name).split())
        assert food_words.flags([name])["veg"] is False, name                       # recipe importer
        assert ingredients.diet_class(token) in ("egg", "nonveg"), token             # pantry swaps
        est = live_catalog.estimate(name, True) or live_catalog.unestimated(name, True)
        assert est["veg"] == 0, name                                                 # live menus
    for name, fam in ALLERGEN:
        assert fam in food_words.flags([name])["allergens"], name
        assert fam in (live_catalog.estimate(name, True) or live_catalog.unestimated(name, True))["allergens"], name


# --------------------------------------------------------------------------- #
# 2. Estimates never invent "sugar 0" or "veg" (#37, #38; usability 3, 4)
# --------------------------------------------------------------------------- #
def test_unestimated_dish_has_unknown_sugar_and_unknown_veg():
    d = live_catalog.unestimated("Chef Special Thattu", None)
    assert d["sugar_g"] is None and d["nutrition_known"] == 0
    assert d["veg"] == 0 and d["veg_known"] == 0 and d["safety_basis"] == "none"


def test_sweet_words_beat_savoury_templates():
    sweet = live_catalog.estimate("Sakkarai Pongal", True)
    assert sweet["sugar_g"] >= 30 and "dessert" in sweet["tags"]                    # not the 2 g pongal template
    assert live_catalog.estimate("Kesari", True)["sugar_g"] > allergens.DIABETES_SUGAR_G


def test_a_meat_name_beats_a_veg_mark_but_a_veg_mark_never_beats_nothing():
    assert live_catalog.estimate("Murgh Biryani", True)["veg"] == 0
    assert live_catalog.estimate("Veg Biryani", None)["veg"] == 0                   # no mark: not veg
    assert live_catalog.estimate("Veg Biryani", None)["veg_known"] == 0


def test_medical_rules_fail_closed_on_unknown_sugar():
    assert allergens.MEDICAL_RULES["diabetes"]({}) is False                          # was sugar_g default 0
    assert allergens.MEDICAL_RULES["diabetes"]({"sugar_g": 5}) is True
    v = allergens.check({"medical": ["diabetes"]}, {"name": "Thattu", **live_catalog.unestimated("Thattu", True)})
    assert v["status"] == "unknown" and v["code"] == "sugar_unknown"
    v = allergens.check({"medical": ["diabetes"]}, {"name": "Rasmalai", "sugar_g": 5, "veg": 1})
    assert v["status"] == "unsafe" and "Rasmalai is an Indian sweet" in v["reason"]


def test_missing_veg_mark_is_unknown_and_egg_is_non_veg():
    for diet in ("veg", "vegan"):
        v = allergens.check({"diet": diet}, {"name": "Idli", "veg": None})
        assert (v["status"], v["code"]) == ("unknown", "veg_unknown")
        v = allergens.check({"diet": diet}, {"name": "Plain Dosa", "veg": 1, "allergens": ["egg"]})
        assert v["status"] == "unsafe"
    assert allergens.check({"diet": "nonveg"}, {"name": "Idli", "veg": None})["status"] == "ok"


def test_legacy_live_rows_are_not_trusted_for_veg():
    row = {"name": "Idli", "veg": 1, "veg_known": 1, "source": "live", "safety_basis": None}
    assert allergens.check({"diet": "veg"}, row)["code"] == "veg_unknown"


def test_allergy_profiles_are_told_not_checked_never_safe():
    peanut = {"diet": "nonveg", "allergens": ["peanut"]}
    dosa = {"name": "Masala Dosa", **live_catalog.estimate("Masala Dosa", True)}
    label = allergens.safety_label(peanut, dosa)
    assert label["status"] == "not_checked" and "Ask the restaurant" in label["note"]
    thattu = {"name": "Chef Special Thattu", **live_catalog.unestimated("Chef Special Thattu", True)}
    assert allergens.check(peanut, thattu)["code"] == "allergens_unknown"            # nothing known: hidden
    assert allergens.check({"diet": "nonveg"}, dosa)["status"] == "ok"
    med = allergens.safety_label({"medical": ["diabetes"]}, dosa)
    assert med["status"] == "not_checked" and "not medical advice" in med["note"]


def test_recipe_library_inference_only_adds():
    """Founder's answer to #37–#39: estimate from the recipe library by dish category, but
    an inference can only make a dish less safe, never certify it."""
    guess = dish_library.infer("Lemon Rice")
    assert guess and "peanut" in guess["allergens"]                                 # lemon rice: peanuts
    est = live_catalog.estimate("Lemon Rice", True)
    assert "peanut" in est["allergens"]
    assert live_catalog.unestimated("Kesari", None)["veg_known"] == 0              # recipes say veg: still unknown


# --------------------------------------------------------------------------- #
# 3. Hostile menu end to end (usability probe 22)
# --------------------------------------------------------------------------- #
UNMARKED_MEAT = {"Kozhi Varuval", "Eral Thokku", "Nandu Masala", "Meen Kuzhambu Meals", "Aatu Kari Kuzhambu",
                 "Nattukozhi Biryani", "Murgh Biryani", "Muttai Dosa"}
PEANUT = {"Verkadalai Sundal", "Satay Veg Bowl", "Moongphali Chaat Meals"}
SWEETS = {"Kesari", "Rasmalai", "Sakkarai Pongal", "Gulab Jamun", "Badam Halwa"}
PROFILES = {
    "veg": ({"diet": "veg", "allergens": [], "medical": []}, UNMARKED_MEAT | {"Chef Special Thattu"}),
    "vegan": ({"diet": "vegan", "allergens": [], "medical": []},
              UNMARKED_MEAT | {"Chef Special Thattu", "Mor Kuzhambu Rice", "Rasmalai"}),
    "peanut": ({"diet": "nonveg", "allergens": ["peanut"], "medical": []}, PEANUT | {"Chef Special Thattu"}),
    "tree_nut": ({"diet": "nonveg", "allergens": ["tree_nut"], "medical": []}, {"Kaju Kari Meals", "Badam Halwa"}),
    "gluten": ({"diet": "nonveg", "allergens": ["gluten"], "medical": []}, {"Rava Kichadi", "Kesari"}),
    "diabetes": ({"diet": "nonveg", "allergens": [], "medical": ["diabetes"]}, SWEETS | {"Chef Special Thattu"}),
}
HOSTILE_DISHES = {row[0]: f"r-107-i{n}" for n, row in enumerate(HOSTILE["r-107"][6])}


@pytest.fixture
def town(gt, monkeypatch):
    monkeypatch.setattr(config, "SWIGGY_PROVIDER", "live")
    fake = TownFake(hostile=True)
    monkeypatch.setattr(swiggy_connect, "_http", fake)
    client = create_app().test_client()

    def person(kind):
        body, _ = PROFILES[kind]
        uid = client.post("/api/profiles", json={"name": kind.title(), "weekly_budget": 3500,
                                                 "rhythm": {"breakfast": "order", "lunch": "order", "dinner": "order"},
                                                 "cook": "never", "favourites": [], **body}).get_json()["user"]["id"]
        _connect(client, fake, uid=uid)
        assert client.post(f"/api/user/{uid}/swiggy/address", json={"address_id": "addr-home"}).status_code == 200
        return uid
    return {"c": client, "fake": fake, "person": person}


def _menu(c, uid):
    return c.get(f"/api/user/{uid}/swiggy/live-menu",
                 query_string={"restaurant_id": "r-107", "restaurant_name": "Kadal Kozhi Mess"}).get_json()


@pytest.mark.parametrize("kind", sorted(PROFILES))
def test_hostile_menu_hides_every_unsafe_dish_with_its_reason(town, kind):
    c = town["c"]
    uid = town["person"](kind)
    menu = _menu(c, uid)
    shown = {i["name"] for i in menu["items"]}
    hidden = {h["name"]: h for h in menu["hidden_items"]}
    must_hide = PROFILES[kind][1]
    assert must_hide <= set(hidden), must_hide - set(hidden)
    assert not (must_hide & shown)
    assert all(h["reason"] and h["code"] for h in hidden.values())
    assert menu["hidden"] == len(hidden) and sum(menu["hidden_by"].values()) == len(hidden)
    assert "Idli Sambar" in shown                                                  # the plain control survives
    if kind == "veg":
        assert "Kozhi means chicken" in hidden["Kozhi Varuval"]["reason"]
        assert "Murgh means chicken" in hidden["Murgh Biryani"]["reason"]          # wrong veg mark caught
        assert hidden["Chef Special Thattu"]["code"] == "veg_unknown"
    if kind == "peanut":
        assert "Verkadalai means peanut" in hidden["Verkadalai Sundal"]["reason"]
        assert all(i["safety"]["status"] == "not_checked" for i in menu["items"])   # never "safe"
    if kind == "diabetes":
        assert hidden["Chef Special Thattu"]["code"] == "sugar_unknown"
        assert hidden["Kesari"]["code"] == "diabetes"


@pytest.mark.parametrize("kind", ["veg", "peanut", "diabetes"])
def test_dish_search_and_have_for_dinner_and_pools_agree_with_the_menu(town, kind):
    """usability #5: one checker, so the menu, dish search, "have this for dinner", meal pools
    and cart review never disagree about a dish."""
    c = town["c"]
    uid = town["person"](kind)
    hidden = {h["name"]: h for h in _menu(c, uid)["hidden_items"]}
    plan = c.get(f"/api/user/{uid}/plan").get_json()
    sid = next(cell["session_id"] for d in plan["grid"] for m, cell in d["meals"].items()
               if m == "dinner" and cell.get("session_id") and not cell.get("past"))
    for name in sorted(PROFILES[kind][1] & set(HOSTILE_DISHES)):
        word = name.split()[0]
        found = c.get(f"/api/user/{uid}/swiggy/dishes", query_string={
            "restaurant_id": "r-107", "restaurant_name": "Kadal Kozhi Mess", "query": word}).get_json()
        assert name not in {i["name"] for i in found["items"]}, name
        # (for vegetarians Swiggy's own veg filter may drop unmarked dishes before Ziggy sees them)
        code = {h["name"]: h["code"] for h in found["hidden_items"]}.get(name)
        assert code == hidden[name]["code"] or (code is None and kind == "veg"), name
        r = c.post(f"/api/session/{sid}/choose-live", json={
            "restaurant_id": "r-107", "restaurant_name": "Kadal Kozhi Mess", "item_id": HOSTILE_DISHES[name]})
        assert r.status_code == 400 and hidden[name]["reason"] in r.get_json()["error"], (name, r.get_json())
        r = c.post(f"/api/user/{uid}/pools", json={"meal": "dinner", "restaurant_id": "r-107",
                                                   "restaurant_name": "Kadal Kozhi Mess",
                                                   "item_id": HOSTILE_DISHES[name]})
        assert r.status_code == 400 and hidden[name]["reason"] in r.get_json()["error"], (name, r.get_json())
        review = c.post(f"/api/user/{uid}/swiggy/live-cart/preview", json={
            "restaurant_id": "r-107", "restaurant_name": "Kadal Kozhi Mess", "item_id": HOSTILE_DISHES[name],
            "item_name": name}).get_json()
        assert review["orderable"] is False and review["fingerprint"] is None, name
    assert "update_food_cart" not in town["fake"].tool_calls()


def test_mis_marked_dish_is_never_carted_for_a_vegetarian(town):
    c = town["c"]
    uid = town["person"]("veg")
    review = c.post(f"/api/user/{uid}/swiggy/live-cart/preview", json={
        "restaurant_id": "r-107", "restaurant_name": "Kadal Kozhi Mess",
        "item_id": HOSTILE_DISHES["Murgh Biryani"], "item_name": "Murgh Biryani"}).get_json()
    assert review["orderable"] is False and "Murgh means chicken" in review["reason"]
    assert review["rule"]["id"] == "ingredients"
    ok = c.post(f"/api/user/{uid}/swiggy/live-cart/preview", json={
        "restaurant_id": "r-107", "restaurant_name": "Kadal Kozhi Mess",
        "item_id": HOSTILE_DISHES["Idli Sambar"], "item_name": "Idli Sambar"}).get_json()
    assert ok["orderable"] is True                                                 # the control still orders


@pytest.mark.parametrize("kind", ["veg", "vegan", "peanut", "diabetes"])
def test_a_week_planned_from_the_hostile_menu_is_safe(town, kind):
    c = town["c"]
    uid = town["person"](kind)
    r = c.post(f"/api/user/{uid}/swiggy/favourites", json={"restaurant_id": "r-107",
                                                           "restaurant_name": "Kadal Kozhi Mess"})
    assert r.status_code == 200, r.get_json()
    pid = c.get(f"/api/user/{uid}/plan").get_json()["plan"]["id"]
    view = c.post(f"/api/plan/{pid}/live-menus", json={}).get_json()
    assert view["source"]["kind"] == "live", view["source"]
    planned = {cell["item"] for d in view["grid"] for cell in d["meals"].values() if cell.get("kind") == "delivery"}
    assert not (planned & PROFILES[kind][1]), planned & PROFILES[kind][1]
    assert check_plan(pid) == []
    user = models.get_user(uid)
    stored = {row["name"] for row in models.menu_for_user(user)}
    assert stored & PROFILES[kind][1], "the hostile dishes were read into the catalogue"
    for row in models.menu_for_user(user):
        if row["name"] in PROFILES[kind][1]:
            assert allergens.violates(user, row), row["name"]                      # stored rows agree too
    sid = next(cell["session_id"] for d in view["grid"] for cell in d["meals"].values()
               if cell.get("session_id") and not cell.get("past"))
    sheet = c.get(f"/api/session/{sid}/options").get_json()
    offered = {d["name"] for g in sheet["usual"] for d in g["dishes"]} | {d["name"] for d in sheet["new"]}
    assert not (offered & PROFILES[kind][1])
    assert all(h["reason"] for h in sheet["hidden_dishes"])


def test_dish_library_gives_reasons_too(gt):
    user = {"diet": "veg", "allergens": [], "medical": []}
    res = dish_library.search(user, "kozhi")
    assert res["hidden"] >= 1 and res["hidden_dishes"]
    assert all(h["reason"] for h in res["hidden_dishes"])
    assert not any("kozhi" in d["title"].lower() for d in res["dishes"])


# --------------------------------------------------------------------------- #
# 4. Properties
# --------------------------------------------------------------------------- #
RISKY = [w for w, _ in NONVEG] + [w for w, _ in ALLERGEN] + SWEET + ["Idli", "Masala Dosa", "Veg Meals",
                                                                     "Chef Special", "Thattu", "Lemon Rice"]
people = st.fixed_dictionaries({
    "diet": st.sampled_from(["veg", "vegan", "nonveg"]),
    "allergens": st.lists(st.sampled_from(["peanut", "tree_nut", "dairy", "gluten", "fish", "shellfish",
                                           "egg", "sesame", "soy"]), max_size=2, unique=True),
    "medical": st.lists(st.sampled_from(["diabetes", "celiac", "hypertension"]), max_size=1, unique=True)})
dishes = st.tuples(st.lists(st.sampled_from(RISKY), min_size=1, max_size=2).map(" ".join),
                   st.sampled_from([True, False, None]))


def _facts(name, veg):
    est = live_catalog.estimate(name, veg) or live_catalog.unestimated(name, veg)
    return {**est, "name": name, "source": "live"}


@settings(max_examples=150)
@given(person=people, dish=dishes)
def test_property_nothing_unsafe_or_unknown_is_offered(person, dish):
    name, veg = dish
    facts = _facts(name, veg)
    verdict = allergens.check(person, facts)
    if verdict["status"] in ("unsafe", "unknown"):
        assert verdict["reason"] and verdict["code"]
        return
    # offered: the independent oracle must agree it breaks no rule
    assert item_violation(person, facts) is None, (person, name, veg, facts)
    if person["diet"] in ("veg", "vegan"):
        assert veg is True and not food_lexicon.read(name).nonveg
    if "diabetes" in person["medical"]:
        assert facts["nutrition_known"] == 1 and facts["sugar_g"] <= allergens.DIABETES_SUGAR_G
    if person["allergens"] or person["medical"] or person["diet"] == "vegan":
        assert verdict["status"] == "not_checked"                                  # never "safe"
    # the live-menu screen is the same checker
    shown, hidden, _ = swiggy_live._screen(person, [{"id": "x", "name": name, "veg": veg}])
    assert [i["name"] for i in shown] == [name] and not hidden


@settings(max_examples=100)
@given(person=people, dish=dishes, extra=st.sampled_from(RISKY))
def test_property_more_words_never_make_a_dish_safer(person, dish, extra):
    """Inference only adds restrictions: a description that mentions more food can hide
    a dish but never un-hide one."""
    name, veg = dish
    before = allergens.violates(person, _facts(name, veg))
    est = live_catalog.estimate(name, veg, extra) or live_catalog.unestimated(name, veg, extra)
    after = allergens.violates(person, {**est, "name": name, "description": extra, "source": "live"})
    if before:
        assert after, (person, name, extra)


@settings(max_examples=60)
@given(person=people, dish=dishes)
def test_property_screen_and_planner_agree(person, dish):
    """The menu screen and the stored-row check (what the planner sees) give one answer."""
    name, veg = dish
    shown, hidden, _ = swiggy_live._screen(person, [{"id": "x", "name": name, "veg": veg}])
    stored = {**_facts(name, veg), "sugar_g": _facts(name, veg)["sugar_g"] or 0}     # as the DB keeps it
    assert bool(hidden) == bool(allergens.violates(person, stored))


# --------------------------------------------------------------------------- #
# 5. Health-condition filters can be archived for the beta (#41)
# --------------------------------------------------------------------------- #
def test_medical_filters_off_refuses_new_conditions_but_keeps_saved_ones(gt, monkeypatch):
    from smartplate.domain import profile
    monkeypatch.setattr(config, "MEDICAL_FILTERS", False)
    with pytest.raises(ValueError, match="paused in this beta"):
        profile.validate_setup({"medical": ["diabetes"]})
    assert profile.validate_setup({"medical": []})["medical"] == []                 # removing one still works
    user = models.get_user(1)                                                        # saved: peanut + diabetes
    assert "diabetes" in user["medical"]
    rasmalai = {"name": "Rasmalai", "veg": 1, "sugar_g": 5}
    assert allergens.violates(user, rasmalai)                                        # still enforced
    monkeypatch.setattr(config, "MEDICAL_FILTERS", True)
    assert profile.validate_setup({"medical": ["diabetes"]})["medical"] == ["diabetes"]
