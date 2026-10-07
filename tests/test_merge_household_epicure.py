"""Regression from merging Roadmap G (Epicure ingredient swaps) with Roadmap E (grocery list
scaled to who eats, with "have it" ticks). E rebuilt the grocery list as one line per shop
pack (basket_for_meals) and dropped the Epicure token each line carried, so an
out-of-stock swap could no longer find its grocery line: the swap vanished from the list and
the line lost its "Out of stock?" button, in exactly the household weeks E scales."""
import math

import pytest

from smartplate.app import create_app
from smartplate.domain import ingredients

DEV = {"name": "Dev", "diet": "veg", "allergens": ["peanut"], "medical": [], "meals": ["dinner"]}


@pytest.fixture
def c(gt):
    app = create_app()
    app.config["TESTING"] = True
    return app.test_client()


def test_household_grocery_line_keeps_its_swap_and_its_scaling(c):
    r = c.post("/api/profiles", json={"name": "Kavya", "diet": "veg", "weekly_budget": 3500,
                                      "meals": ["lunch", "dinner"], "cook": "never"})
    v = r.get_json(); h = {"X-SmartPlate-Key": v["access_key"]}
    uid, pid = v["user"]["id"], v["plan"]["id"]
    c.post(f"/api/user/{uid}/household", json={"name": "Home"}, headers=h)
    v = c.post(f"/api/user/{uid}/household/members", json=DEV, headers=h).get_json()
    dinners = [cl["session_id"] for d in v["grid"] for m, cl in d["meals"].items()
               if m == "dinner" and cl["status"] == "active"][:3]
    for sid in dinners:                                        # Kavya + Dev eat dinner: 2 servings each
        v = c.post(f"/api/session/{sid}/choose", json={"recipe_key": "dal_rice"}, headers=h).get_json()
    dal = next(b for b in v["coach"]["basket"]["items"] if b["name"] == "Toor dal 500g")
    assert dal["token"] == "toor_dal" and dal["swappable"] and dal["swap"] is None
    assert dal["servings"] == 6 and dal["qty"] == math.ceil(60 * 6 / 500)

    pick = c.get(f"/api/plan/{pid}/swaps?token=toor_dal", headers=h).get_json()["options"][0]["token"]
    v = c.post(f"/api/plan/{pid}/grocery-swap", json={"token": "toor_dal", "swap_token": pick,
                                                      "reason": "out_of_stock"}, headers=h).get_json()
    v = c.post(f"/api/plan/{pid}/grocery-have", json={"item": "Rice 1kg", "have": True}, headers=h).get_json()
    items = {b["name"]: b for b in v["coach"]["basket"]["items"]}
    dal = items["Toor dal 500g"]
    assert dal["swap"] == {"token": pick, "name": ingredients.name(pick), "reason": "out_of_stock"}
    assert dal["servings"] == 6 and dal["qty"] == math.ceil(60 * 6 / 500)    # still scaled to who eats
    assert items["Rice 1kg"]["have"] and items["Rice 1kg"]["swap"] is None
    assert v["coach"]["basket"]["total"] == sum(b["price"] for b in items.values() if not b["have"])
