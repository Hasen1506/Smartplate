"""Roadmap E — households, the weekly recap, and a grocery list built from cook meals.

Real flows on a frozen week (Mon 2 Nov 2026, 08:00 IST):
  • a private profile creates a household and adds the people it cooks for; their
    allergies and diets combine, and the open meals are re-planned to meet all of them;
  • "split by consumption" divides each meal's cost among the people eating it (the
    usual eaters of that meal, or whoever is ticked in the meal's sheet) — on main this
    was a placeholder that split evenly;
  • the weekly recap reports spend and nutrition from what actually happened;
  • the grocery list covers the cook meals still ahead, scaled to who eats them,
    rounded up to whole packs, with "have it" ticks.
"""
import datetime as dt
import math

import pytest

from smartplate import db
from smartplate.app import create_app
from smartplate.domain import household, models, reverse_mode
from smartplate.kernel import scheduler


@pytest.fixture
def app_client(gt):
    app = create_app()
    app.config["TESTING"] = True
    return app.test_client()


def _profile(c, **over):
    body = {"name": "Kavya", "diet": "nonveg", "weekly_budget": 3500, "meals": ["lunch", "dinner"],
            "cook": "sometimes", **over}
    r = c.post("/api/profiles", json=body)
    assert r.status_code == 201, r.get_json()
    v = r.get_json()
    return v, {"X-SmartPlate-Key": v["access_key"]}


def _household(c, uid, h, *people):
    v = c.post(f"/api/user/{uid}/household", json={"name": "Home"}, headers=h).get_json()
    for p in people:
        r = c.post(f"/api/user/{uid}/household/members", json=p, headers=h)
        assert r.status_code == 200, r.get_json()
        v = r.get_json()
    return v


DEV = {"name": "Dev", "diet": "veg", "allergens": ["peanut"], "medical": [], "meals": ["dinner"]}
AMMA = {"name": "Amma", "diet": "nonveg", "allergens": ["shellfish"], "medical": ["diabetes"]}


def _open_cells(v):
    return [c for d in v["grid"] for c in d["meals"].values() if c["status"] == "active"]


# --------------------------------------------------------------------------- #
# Household set-up and people
# --------------------------------------------------------------------------- #
def test_create_household_add_edit_remove_people(app_client):
    c = app_client
    v, h = _profile(c)
    uid = v["user"]["id"]
    v = _household(c, uid, h, DEV, AMMA)
    hh = v["household"]
    assert hh["name"] == "Home" and [p["name"] for p in hh["people"]] == ["Kavya", "Dev", "Amma"]
    you, dev, amma = hh["people"]
    assert you["you"] and not you["managed"] and dev["managed"] and amma["managed"]
    assert dev["meals"] == ["dinner"] and amma["meals"] == ["lunch", "dinner"]       # default: the planned meals
    # edit and remove
    v = c.patch(f"/api/user/{uid}/household/members/{dev['id']}", json={"allergens": ["peanut", "sesame"]},
                headers=h).get_json()
    assert next(p for p in v["household"]["people"] if p["name"] == "Dev")["allergens"] == ["peanut", "sesame"]
    v = c.delete(f"/api/user/{uid}/household/members/{amma['id']}", json={}, headers=h).get_json()
    assert [p["name"] for p in v["household"]["people"]] == ["Kavya", "Dev"]
    assert models.get_user(amma["id"]) is None
    v = c.patch(f"/api/user/{uid}/household", json={"name": "Flat 2A"}, headers=h).get_json()
    assert v["household"]["name"] == "Flat 2A"


@pytest.mark.parametrize("body", [{"name": ""}, {"name": "x" * 41}, {"name": "A", "diet": "keto"},
                                  {"name": "A", "allergens": ["nuts"]}, {"name": "A", "meals": []},
                                  {"name": "A", "age": 3}])
def test_bad_people_are_refused(app_client, body):
    c = app_client
    v, h = _profile(c)
    _household(c, v["user"]["id"], h)
    assert c.post(f"/api/user/{v['user']['id']}/household/members", json=body, headers=h).status_code == 400


def test_people_without_a_profile_stay_private(app_client):
    c = app_client
    v, h = _profile(c)
    uid = v["user"]["id"]
    v = _household(c, uid, h, DEV)
    dev = v["household"]["people"][1]
    assert dev["name"] not in [u["name"] for u in c.get("/api/users").get_json()]      # never listed
    assert c.get(f"/api/user/{dev['id']}/plan").status_code == 401                     # never opened by id
    assert c.get(f"/api/user/{dev['id']}/plan", headers=h).status_code == 401          # not even with the owner's key
    other, h2 = _profile(c, name="Ravi")
    assert c.patch(f"/api/user/{uid}/household/members/{dev['id']}", json={"name": "X"}, headers=h2).status_code == 401


def test_sample_profiles_and_joined_profiles_cannot_be_edited_here(app_client):
    c = app_client
    assert c.post("/api/user/2/household", json={"name": "Mine"}).status_code == 400   # Meera is a shared sample
    v, h = _profile(c)
    uid = v["user"]["id"]
    _household(c, uid, h)
    with db.cursor() as cur:                                   # a real profile that joined the household
        cur.execute("UPDATE users SET household_id=(SELECT household_id FROM users WHERE id=?) WHERE id=3", (uid,))
    r = c.patch(f"/api/user/{uid}/household/members/3", json={"name": "X"}, headers=h)
    assert r.status_code == 400 and "own Ziggy profile" in r.get_json()["error"]
    assert c.post(f"/api/user/{uid}/household", json={"name": "Again"}, headers=h).status_code == 400


# --------------------------------------------------------------------------- #
# Combined allergies: one profile's household is planned for everyone's rules
# --------------------------------------------------------------------------- #
def test_combined_rules_are_listed_and_enforced_on_the_replan(app_client):
    c = app_client
    v, h = _profile(c, allergens=["dairy"])
    uid = v["user"]["id"]
    v = _household(c, uid, h, DEV, AMMA)
    rules = v["household"]["rules"]
    assert rules["allergens"] == [{"rule": "dairy", "who": ["Kavya"]}, {"rule": "peanut", "who": ["Dev"]},
                                  {"rule": "shellfish", "who": ["Amma"]}]
    assert rules["medical"] == [{"rule": "diabetes", "who": ["Amma"]}]
    assert rules["diet"] == {"rule": "veg", "who": ["Dev"]}
    menu = {it["id"]: it for it in models.menu_for_city("Chennai")}
    for cell in _open_cells(v):
        if cell["kind"] == "delivery":
            it = menu[cell["item_id"]]
            assert not ({"dairy", "peanut", "shellfish", "egg"} & set(it["allergens"])) and it["veg"], it["name"]
            assert it["sugar_g"] <= 20
        if cell["kind"] == "cook":
            r = reverse_mode.recipe(cell["recipe_key"])
            assert r["veg"] and not ({"dairy", "peanut", "shellfish", "egg"} & set(r["allergens"]))
    # the sheet hides what anyone can't eat, and a pick can't override it
    sid = next(cl["session_id"] for cl in _open_cells(v) if cl["kind"] == "delivery")
    peanut = next(it for it in menu.values() if "peanut" in it["allergens"] and it["veg"] and "dessert" not in it["tags"])
    r = c.post(f"/api/session/{sid}/choose", json={"item_id": peanut["id"]}, headers=h)
    assert r.status_code == 400 and "Dev" in r.get_json()["error"]


def test_stop_sharing_returns_to_your_own_rules(app_client):
    c = app_client
    v, h = _profile(c)
    uid = v["user"]["id"]
    v = _household(c, uid, h, DEV)
    hid = models.get_user(uid)["household_id"]
    dev_id = v["household"]["people"][1]["id"]
    v = c.post(f"/api/user/{uid}/household/leave", json={}, headers=h).get_json()
    assert "household" not in v and models.get_user(dev_id) is None and household.get(hid) is None
    assert models.get_user(uid)["household_members"] == []


# --------------------------------------------------------------------------- #
# Split by consumption
# --------------------------------------------------------------------------- #
def _expected_by_consumption(v, members_meals):
    """Recompute shares independently: each planned meal's cost over the people eating it."""
    shares = {n: 0.0 for n in members_meals}
    for d in v["grid"]:
        for meal, cell in d["meals"].items():
            if cell["status"] == "past" or cell["kind"] not in ("delivery", "cook"):
                continue
            eaters = [n for n, meals in members_meals.items() if meal in meals]
            for n in eaters:
                shares[n] += cell["cost"] / len(eaters)
    return shares


def test_split_by_consumption_follows_who_usually_eats_each_meal(app_client):
    c = app_client
    v, h = _profile(c)
    uid = v["user"]["id"]
    v = _household(c, uid, h, DEV)                                            # Dev eats dinner only
    even = {x["member"]: x["share"] for x in v["household"]["split"]}
    total = v["budget"]["spend"]
    assert even["Kavya"] == pytest.approx(total / 2, abs=0.01) and even["Dev"] == pytest.approx(total / 2, abs=0.01)
    v = c.patch(f"/api/user/{uid}/household", json={"split": "by_consumption"}, headers=h).get_json()
    got = {x["member"]: x["share"] for x in v["household"]["split"]}
    want = _expected_by_consumption(v, {"Kavya": ["lunch", "dinner"], "Dev": ["dinner"]})
    assert got["Dev"] == pytest.approx(want["Dev"], abs=0.02) and got["Kavya"] == pytest.approx(want["Kavya"], abs=0.02)
    assert got["Dev"] < got["Kavya"]                                          # Dev skips every lunch
    assert round(sum(got.values()), 2) == round(v["budget"]["spend"], 2)       # shares add up to the paisa
    assert c.patch(f"/api/user/{uid}/household", json={"split": "random"}, headers=h).status_code == 400


def test_ticking_who_eats_a_meal_changes_the_split_and_is_validated(app_client):
    c = app_client
    v, h = _profile(c)
    uid = v["user"]["id"]
    v = _household(c, uid, h, DEV)
    v = c.patch(f"/api/user/{uid}/household", json={"split": "by_consumption"}, headers=h).get_json()
    kavya, dev = [p["id"] for p in v["household"]["people"]]
    lunch = next(cl for d in v["grid"] for m, cl in d["meals"].items()
                 if m == "lunch" and cl["status"] == "active" and cl["kind"] in ("delivery", "cook"))
    assert lunch["eaters"] == [kavya]                                          # Dev doesn't usually eat lunch
    before = {x["member"]: x["share"] for x in v["household"]["split"]}
    sid = lunch["session_id"]
    for bad in ([], [999], [True], "all", [kavya, "x"]):
        assert c.post(f"/api/session/{sid}/eaters", json={"eaters": bad}, headers=h).status_code == 400
    v = c.post(f"/api/session/{sid}/eaters", json={"eaters": [kavya, dev]}, headers=h).get_json()
    cell = next(cl for d in v["grid"] for cl in d["meals"].values() if cl["session_id"] == sid)
    assert cell["eaters"] == [kavya, dev]
    after = {x["member"]: x["share"] for x in v["household"]["split"]}
    assert after["Dev"] == pytest.approx(before["Dev"] + cell["cost"] / 2, abs=0.02)
    v = c.post(f"/api/session/{sid}/eaters", json={"eaters": None}, headers=h).get_json()   # back to the usual
    assert next(cl for d in v["grid"] for cl in d["meals"].values() if cl["session_id"] == sid)["eaters"] == [kavya]
    # removing a person forgets their ticks
    c.post(f"/api/session/{sid}/eaters", json={"eaters": [dev]}, headers=h)
    c.delete(f"/api/user/{uid}/household/members/{dev}", json={}, headers=h)
    with db.cursor() as cur:
        assert cur.execute("SELECT eaters FROM sessions WHERE id=?", (sid,)).fetchone()["eaters"] is None


def test_eaters_need_a_household(app_client):
    c = app_client
    v, h = _profile(c)
    sid = _open_cells(v)[0]["session_id"]
    assert c.post(f"/api/session/{sid}/eaters", json={"eaters": None}, headers=h).status_code == 400


def test_split_cost_unit_rounding_always_adds_up():
    hh = {"split": "by_consumption"}
    members = [{"id": 1, "name": "A", "prefs": "{}"}, {"id": 2, "name": "B", "prefs": "{}"},
               {"id": 3, "name": "C", "prefs": "{}"}]
    owner = {"prefs": {"meals": ["lunch"]}}
    meals = [{"meal": "lunch", "cost": 100.0, "eaters": "[1, 2, 3]"}, {"meal": "lunch", "cost": 10.0, "eaters": "[2]"}]
    shares = household.split_cost(hh, members, 110.0, meals, owner)
    assert [s["share"] for s in shares] == [33.33, 43.34, 33.33]
    assert round(sum(s["share"] for s in shares), 2) == 110.0


# --------------------------------------------------------------------------- #
# Weekly recap
# --------------------------------------------------------------------------- #
def test_weekly_recap_from_what_happened(app_client, gt):
    c = app_client
    v, h = _profile(c)
    uid, pid = v["user"]["id"], v["plan"]["id"]
    v = _household(c, uid, h, DEV)
    c.patch(f"/api/user/{uid}/household", json={"split": "by_consumption"}, headers=h)
    cells = sorted(_open_cells(v), key=lambda cl: cl["session_id"])
    first, second = cells[0], cells[1]                    # Monday lunch and dinner
    gt.set(dt.datetime(2026, 11, 2, 14, 30))              # after lunch
    v = c.post(f"/api/session/{first['session_id']}/confirm", json={}, headers=h).get_json()
    had_cost = next(cl for d in v["grid"] for cl in d["meals"].values() if cl["session_id"] == first["session_id"])["cost"]
    gt.set(dt.datetime(2026, 11, 3, 9, 0))                # Monday dinner is now past, never marked
    r = c.get(f"/api/plan/{pid}/recap", headers=h).get_json()
    assert r["week_start"] == "2026-11-02"
    assert r["spend"]["spent"] == round(had_cost, 2)
    assert r["meals"]["had"] == 1 and r["meals"]["unmarked"] == 1
    planned_now = sum(cl["cost"] for cl in _open_cells(c.get(f"/api/plan/{pid}", headers=h).get_json())
                      if cl["kind"] in ("delivery", "cook"))
    assert r["spend"]["planned"] == pytest.approx(planned_now, abs=0.01)
    assert r["spend"]["left"] == pytest.approx(r["spend"]["budget"] - r["spend"]["spent"] - r["spend"]["planned"], abs=0.01)
    # nutrition from the intake log the confirm wrote
    assert r["nutrition"]["days_logged"] == 1 and r["nutrition"]["avg"]["kcal"] > 0
    # Monday lunch was Kavya's alone (Dev eats dinner only): she owes all of it
    split = {x["member"]: x["share"] for x in r["household"]["split"]}
    assert split == {"Kavya": round(had_cost, 2), "Dev": 0.0}
    assert r["spend"]["last_week"] is None


# --------------------------------------------------------------------------- #
# Grocery list from cook meals
# --------------------------------------------------------------------------- #
def _cook(c, h, sid, key="dal_rice"):
    return c.post(f"/api/session/{sid}/choose", json={"recipe_key": key}, headers=h).get_json()


def test_grocery_list_scales_to_who_eats_and_rounds_to_packs(app_client):
    c = app_client
    v, h = _profile(c, cook="never")
    uid = v["user"]["id"]
    v = _household(c, uid, h, DEV, AMMA)
    dinners = [cl["session_id"] for d in v["grid"] for m, cl in d["meals"].items() if m == "dinner" and cl["status"] == "active"]
    lunch = next(cl["session_id"] for d in v["grid"] for m, cl in d["meals"].items() if m == "lunch" and cl["status"] == "active")
    for sid in dinners[:2]:
        v = _cook(c, h, sid)                               # dinner: Kavya, Dev, Amma → 3 servings each
    v = _cook(c, h, lunch)                                 # lunch: Kavya, Amma → 2 servings
    servings = 3 + 3 + 2
    items = {b["name"]: b for b in v["coach"]["basket"]["items"]}
    dal = items["Toor dal 500g"]
    assert dal["servings"] == servings and dal["need"] == 60 * servings
    assert dal["qty"] == math.ceil(60 * servings / 500) and dal["price"] == 90 * dal["qty"]
    rice = items["Rice 1kg"]
    assert rice["need"] == 90 * servings and rice["qty"] == 1
    assert v["coach"]["basket"]["total"] == sum(b["price"] for b in items.values())
    assert v["coach"]["headline"].startswith("3 cook meals still to cook")
    assert c.get(f"/api/plan/{v['plan']['id']}/basket", headers=h).get_json() == v["coach"]["basket"]


def test_have_it_ticks_and_past_cook_meals_drop_off(app_client, gt):
    c = app_client
    v, h = _profile(c, cook="never")
    pid = v["plan"]["id"]
    cells = sorted(_open_cells(v), key=lambda cl: cl["session_id"])
    v = _cook(c, h, cells[0]["session_id"])                # Monday lunch
    v = _cook(c, h, cells[2]["session_id"], "veg_pulao")   # Tuesday lunch
    total = v["coach"]["basket"]["total"]
    v = c.post(f"/api/plan/{pid}/grocery-have", json={"item": "Rice 1kg", "have": True}, headers=h).get_json()
    rice = next(b for b in v["coach"]["basket"]["items"] if b["name"] == "Rice 1kg")
    assert rice["have"] and v["coach"]["basket"]["total"] == total - rice["price"]
    for bad in ({"item": "Caviar", "have": True}, {"item": "Rice 1kg", "have": "yes"}):
        assert c.post(f"/api/plan/{pid}/grocery-have", json=bad, headers=h).status_code == 400
    v = c.post(f"/api/plan/{pid}/grocery-have", json={"item": "Rice 1kg", "have": False}, headers=h).get_json()
    assert v["coach"]["basket"]["total"] == total
    gt.set(dt.datetime(2026, 11, 2, 15, 0))                # Monday lunch is past
    v = c.get(f"/api/plan/{pid}", headers=h).get_json()
    names = {b["name"] for b in v["coach"]["basket"]["items"]}
    assert "Toor dal 500g" not in names and "Basmati rice 1kg" in names
    assert v["coach"]["headline"].startswith("1 cook meal still to cook")


# --------------------------------------------------------------------------- #
# Your data
# --------------------------------------------------------------------------- #
def test_export_and_delete_cover_the_household(app_client):
    c = app_client
    v, h = _profile(c)
    uid = v["user"]["id"]
    v = _household(c, uid, h, DEV)
    hid = models.get_user(uid)["household_id"]
    dev_id = v["household"]["people"][1]["id"]
    data = c.get(f"/api/user/{uid}/data.json", headers=h).get_json()["data"]
    assert data["household"][0]["name"] == "Home"
    assert [p["name"] for p in data["household_people"]] == ["Dev"]
    assert "access_hash" not in data["household_people"][0]
    assert c.delete(f"/api/user/{uid}", json={"confirmation": "DELETE"}, headers=h).status_code == 200
    assert models.get_user(dev_id) is None and household.get(hid) is None
