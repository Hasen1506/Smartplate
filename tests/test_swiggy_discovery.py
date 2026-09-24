import asyncio

import pytest

from smartplate.integrations.swiggy_discovery import (
    DiscoveryError, SwiggyFoodClient, addresses, menu, restaurants,
)


def test_discovery_returns_only_open_identified_restaurants():
    result = restaurants({"restaurants": [
        {"id": "swg-1", "name": "Open", "availabilityStatus": "OPEN"},
        {"id": "swg-2", "name": "Closed", "availabilityStatus": "CLOSED"},
        {"name": "Missing ID", "availabilityStatus": "OPEN"},
        {"id": "swg-3", "name": "Unknown status"},
    ], "hasMore": True, "nextOffset": 20})
    assert [r["id"] for r in result["restaurants"]] == ["swg-1"]
    assert result["next_offset"] == 20


def test_menu_keeps_missing_safety_and_nutrition_unknown():
    result = menu({"restaurant": {"id": "swg-1"}, "items": [
        {"id": "dish-1", "name": "Dish", "price": 250, "isVeg": True},
        {"id": "dish-2", "name": "Gone", "inStock": 0},
    ]}, "swg-1")
    assert result["items"] == [{"id": "dish-1", "restaurant_id": "swg-1", "name": "Dish",
                                 "price": 250, "veg": True, "rating": None, "categories": [],
                                 "nutrition": None, "allergens": None}]
    with pytest.raises(DiscoveryError):
        menu({"restaurant": {"id": "another"}, "items": []}, "swg-1")


def test_discovery_never_exposes_write_tools():
    assert addresses({"addresses": [{"id": "addr-1", "addressLine": "Example"}]})["addresses"][0]["id"] == "addr-1"
    with pytest.raises(ValueError):
        asyncio.run(SwiggyFoodClient("token").call("place_food_order", {}))


def test_malformed_optional_fields_stay_unknown():
    found = restaurants({'restaurants': [{'id': 'r', 'name': 'Place', 'availabilityStatus': 'OPEN',
                                         'avgRating': 'N/A', 'cuisines': 'spicy'}]})['restaurants'][0]
    assert found['rating'] is None and found['cuisines'] == []
    dish = menu({'restaurant': {'id': 'r'}, 'items': [{'id': 'i', 'name': 'Meal',
                'price': 'not a price', 'isVeg': 'false', 'rating': 9}]}, 'r')['items'][0]
    assert dish['price'] is None and dish['veg'] is None and dish['rating'] is None
