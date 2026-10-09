"""The dish library (6000+ Indian Food Recipes Dataset, facts only) and typed dish
search, offline: rows written for these tests in the dataset's columns."""
import gzip
import json
import os
from fractions import Fraction

import pytest

from smartplate.domain import dish_library, food_words, reverse_mode
from smartplate.integrations import indian_recipes as ir

CSV = os.path.join(os.path.dirname(__file__), "fixtures", "indian", "sample.csv")


def test_rows_become_dishes_without_method_text_by_default():
    snap = ir.build(CSV)
    titles = [d["title"] for d in snap["dishes"]]
    assert titles == ["Coconut Rice", "Egg Roast", "Rava Kesari", "Ven Pongal"]      # Hindi row left out
    pongal = next(d for d in snap["dishes"] if d["title"] == "Ven Pongal")
    assert pongal["about"] == "Rice and Lentils" and pongal["meals"] == ["breakfast"]
    assert pongal["ingredients"][1] == "1/2 cup Moong Dal (Split, yellow)"            # comma inside brackets kept
    assert pongal["url"] == "https://www.example.com/dish-1" and "steps" not in pongal
    assert snap["source"]["license"] == "CC BY 4.0" and snap["steps_included"] is False
    assert ir.build(CSV, with_steps=True)["dishes"][0]["steps"] == ["Cook it.", "Serve it."]


def test_diet_and_allergens_lean_safe():
    snap = {d["title"]: d for d in ir.build(CSV)["dishes"]}
    assert snap["Egg Roast"]["veg"] is False and "egg" in snap["Egg Roast"]["allergens"]
    assert snap["Ven Pongal"]["veg"] and snap["Ven Pongal"]["allergens"] == ["dairy"]
    coco = snap["Coconut Rice"]                       # coconut milk is not dairy; cashews are tree nuts
    assert coco["vegan"] and coco["allergens"] == ["tree_nut"]
    f = food_words.flags(["2 Brinjal (eggplant)", "1 tsp chicken masala powder", "2 tbsp peanut butter"])
    assert f["veg"] and not f["egg"] and f["allergens"] == ["peanut"]


@pytest.fixture
def library(tmp_path, monkeypatch):
    path = tmp_path / "indian_dishes.json.gz"
    with gzip.open(path, "wt", encoding="utf-8") as f:
        json.dump(ir.build(CSV), f)
    monkeypatch.setattr(dish_library, "DATA_FILE", str(path))
    monkeypatch.setattr(dish_library, "_wikibooks", lambda: [])
    dish_library._CACHE.update(mtime=None, data=None, index=None)
    yield
    dish_library._CACHE.update(mtime=None, data=None, index=None)


def user(**kw):
    return {"diet": "nonveg", "allergens": [], "medical": [], "household_members": [], **kw}


def test_search_by_name_and_by_ingredients(library):
    r = dish_library.search(user(), "pon")
    assert [d["title"] for d in r["dishes"]] == ["Ven Pongal"] and r["credits"][0]["license"] == "CC BY 4.0"
    assert [d["title"] for d in dish_library.search(user(), "rice")["dishes"]][0] == "Coconut Rice"
    assert {d["title"] for d in dish_library.search(user(), "cashew")["dishes"]} == {"Coconut Rice"}
    assert [d["title"] for d in dish_library.search(user(), "", meal="breakfast")["dishes"]] == ["Ven Pongal"]


def test_rules_hide_dishes_and_count_them(library):
    veg = dish_library.search(user(diet="veg"), "egg")
    assert veg["dishes"] == [] and veg["hidden"] == 1
    assert dish_library.search(user(allergens=["dairy"]), "pongal")["hidden"] == 1
    assert dish_library.search(user(medical=["diabetes"]), "kesari")["hidden"] == 1      # sweet, no sugar figures
    member = user(household_members=[{"name": "Asha", "diet": "veg", "allergens": [], "medical": []}])
    assert dish_library.search(member, "egg")["hidden"] == 1
    key = next(k for k, d in dish_library._index().items() if d["title"] == "Egg Roast")
    with pytest.raises(ValueError):
        dish_library.get(user(diet="veg"), key)


def test_ingredients_scale_to_how_many_eat(library):
    key = next(k for k, d in dish_library._index().items() if d["title"] == "Coconut Rice")   # serves 3
    d = dish_library.get(user(), key, people=6)
    assert d["scaled"] and [i["line"] for i in d["ingredients"]] == ["3 cups Rice", "2 cup Coconut milk", "20 Cashew nuts"]
    assert d["ingredients"][0]["name"] == "Rice" and "CC BY 4.0" in d["credit"]
    assert dish_library.scale_line("Salt - to taste", Fraction(2)) == "Salt - to taste"
    assert dish_library.scale_line("1/2 cup dal", Fraction(3)) == "1 1/2 cup dal"


def test_a_library_dish_is_a_home_cook_recipe_without_made_up_numbers(library):
    key = next(k for k, d in dish_library._index().items() if d["title"] == "Ven Pongal")
    r = reverse_mode.recipe(key)
    assert r["name"] == "Ven Pongal" and r["cost"] == 0 and r["cost_unknown"] and r["nutrition_unknown"]
    assert reverse_mode.unsafe_reason(user(allergens=["dairy"]), r)
    assert reverse_mode.unsafe_reason(user(), r) is None
