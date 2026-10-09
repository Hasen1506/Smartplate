"""Swiggy's real get_restaurant_menu reply (live finding, 9 Oct 2026): dishes nested in
categories and subcategories, ten at most a section, five to eight sections a page, and
bare whole-rupee prices. The reply below is written for this test in that shape."""
import pytest

from smartplate.domain import live_catalog
from smartplate.integrations import swiggy_live as sl


def dish(i, name, price, veg=True, **kw):
    return {"id": str(i), "name": name, "price": price, "inStock": 1, "isVeg": veg, "isBestseller": False,
            "rating": "4.2", "hasVariants": False, "hasAddons": False, "description": "", "imageUrl": "", **kw}


PAGES = {
    1: {"restaurant": {"id": "777", "name": "Test Tiffin House", "areaName": "Vadapalani", "avgRating": 4.5,
                       "isOpen": True},
        "categories": [
            {"title": "Recommended", "totalItems": 12, "hasMoreItems": True,
             "items": [dish(1, "Ghee Pongal", 140), dish(5, "Dal Fry", 160)]},
            {"title": "Tiffin", "categoryId": "a", "totalItems": 2, "hasMoreItems": False,
             "items": [dish(1, "Ghee Pongal", 140), dish(2, "Mini Idli", 120)]},
            {"title": "Combos", "categoryId": "b", "totalSubcategories": 1,
             "subcategories": [{"title": "Meals For 1", "categoryId": "c", "totalItems": 1, "hasMoreItems": False,
                                "items": [dish(3, "Veg Full Meals", 210), dish(9, "Chicken Meals", 260, veg=False)]}]},
        ], "totalCategories": 4, "page": 1, "pageSize": 8, "hasMore": True},
    2: {"restaurant": {"id": "777", "name": "Test Tiffin House", "isOpen": True},
        "categories": [{"title": "Indian Gravies", "categoryId": "d", "totalItems": 1, "hasMoreItems": False,
                        "items": [dish(5, "Dal Fry", 160)]},
                       {"title": "Hot Beverages", "categoryId": "e", "totalItems": 1, "hasMoreItems": False,
                        "items": [dish(6, "Filter Coffee", 45)]}],
        "totalCategories": 4, "page": 2, "pageSize": 8, "hasMore": False},
}
SCHEMA = {"name": "get_restaurant_menu", "input_schema": {"properties": {
    "addressId": {"type": "string"}, "restaurantId": {"type": "string"},
    "page": {"type": "number"}, "pageSize": {"type": "number"}}}}


@pytest.fixture
def menu(monkeypatch):
    calls = []

    def call(user_id, name, args):
        calls.append(args)
        return {"success": True, "data": PAGES[args["page"]]}
    monkeypatch.setattr(sl, "_conn", lambda uid: {"address_label": "home"})
    monkeypatch.setattr(sl, "_address", lambda conn: "addr-1")
    monkeypatch.setattr(sl, "_tool", lambda conn, name: SCHEMA)
    monkeypatch.setattr(sl, "call", call)
    monkeypatch.setattr(sl, "remember_photos", lambda *a, **k: None)
    from smartplate.domain import models
    monkeypatch.setattr(models, "get_user", lambda uid: {"id": uid, "diet": "veg"})
    return calls


def test_every_page_and_section_is_read_once(menu):
    m = sl.live_menu(1, "777", "Test Tiffin House")
    assert [a["page"] for a in menu] == [1, 2] and all(a["pageSize"] == 8 for a in menu)
    names = [i["name"] for i in m["items"]]
    assert names == ["Ghee Pongal", "Dal Fry", "Mini Idli", "Veg Full Meals", "Filter Coffee"]   # each once
    assert m["hidden_nonveg"] == 1                                                          # Chicken Meals
    by = {i["name"]: i for i in m["items"]}
    assert by["Ghee Pongal"]["categories"] == ["Recommended", "Tiffin"]
    assert by["Dal Fry"]["categories"] == ["Recommended", "Indian Gravies"]
    assert by["Veg Full Meals"]["categories"] == ["Combos", "Meals For 1"]
    assert by["Ghee Pongal"]["price"] == 140 and by["Ghee Pongal"]["price_estimated"] is False   # whole rupees
    assert m["cut_sections"] == [{"section": "Recommended", "shown": 2, "total": 12}] and m["truncated"]


def test_sections_say_side_and_breakfast(menu):
    m = sl.live_menu(1, "777", "Test Tiffin House")
    tags = {i["name"]: live_catalog.section_tags([], i["categories"]) for i in m["items"]}
    assert tags["Ghee Pongal"] == ["breakfast"] and tags["Mini Idli"] == ["breakfast"]
    assert tags["Dal Fry"] == ["side"] and tags["Filter Coffee"] == ["side"]
    assert tags["Veg Full Meals"] == ["main"]


def test_a_bare_side_is_a_side_wherever_it_is_filed():
    assert live_catalog.section_tags([], ["Veg curry andhra style"], "Sambar") == ["side"]
    assert live_catalog.section_tags([], ["Curry Rice Combos"], "Rajma Chawal") == ["main"]
    assert live_catalog.section_tags([], ["Recommended"], "Extra Chutney") == ["side"]
    assert live_catalog.section_tags([], ["Roti & Rice"], "Tawa Roti (2 pcs)") == ["side"]
    assert live_catalog.section_tags([], ["Roti & Rice"], "Steamed Rice") == ["side"]
    assert live_catalog.section_tags([], ["Recommended"], "Whole Wheat Chapati (1 Pc)") == ["side"]
    assert live_catalog.section_tags([], ["Rice Bowls"], "Rajma Chawal Rice Bowl (Serves 1)") == ["main"]
    assert live_catalog.section_tags([], ["Recommended"], "Spicy Idli Podi (150 Gm)") == ["side"]
    assert live_catalog.section_tags([], ["Quick meal ( variety rice )"], "Sambar Rice ( 500ml)") == ["main"]
    assert live_catalog.section_tags([], ["North Indian Breakfast"], "Aloo Paratha") == ["breakfast"]


def test_meal_sections_are_a_preference_not_a_wall():
    """A place with two plates still fills a week of lunches: a dosa at lunch costs a
    little, never more than skipping (CI finding: the hard rule skipped Tuesday's lunch)."""
    from smartplate.kernel import optimizer
    dosa = {"tags": ["breakfast"]}
    meals = {"tags": ["main"]}
    curry_rice = {"tags": []}
    lunch = optimizer.meal_fit_pen([dosa, meals, curry_rice], "lunch")
    assert lunch(meals) == 0.0 < lunch(curry_rice) < lunch(dosa) < optimizer.SKIP_PENALTY["balanced"]
    no_plates = optimizer.meal_fit_pen([dosa, curry_rice], "lunch")
    assert no_plates(curry_rice) == 0.0                     # nothing to prefer: no charge
    breakfast = optimizer.meal_fit_pen([dosa, meals], "breakfast")
    assert breakfast(dosa) == 0.0 < breakfast(meals) < optimizer.SKIP_PENALTY["balanced"]
    assert optimizer.meal_fit_pen([meals], "breakfast")(meals) == 0.0
    assert optimizer.meal_fit_pen([dosa], "dinner")(dosa) == 0.0
