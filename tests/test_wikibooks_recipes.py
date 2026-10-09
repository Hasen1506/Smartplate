"""The Wikibooks Cookbook importer (CC BY-SA 4.0) and the recipe library, offline: a fake
MediaWiki API and test pages written for these tests. Nothing is fetched."""
import io
import json
import os

import pytest

from smartplate.domain import recipe_library
from smartplate.integrations import wikibooks_recipes as wb

HERE = os.path.dirname(__file__)
DAL = open(os.path.join(HERE, "fixtures", "wikibooks", "dal.wikitext"), encoding="utf-8").read()
CHICKEN = """== Ingredients ==
* 500 g chicken
* 2 onions
== Directions ==
# Brown the onions.
# Add the chicken and cook through.
"""
NO_STEPS = "== Ingredients ==\n* rice\n"


def test_a_cookbook_page_becomes_a_credited_recipe():
    r = wb.parse("Cookbook:Tadka Dal", DAL, revision=123)
    assert r["title"] == "Tadka Dal" and r["key"] == "wb:tadka_dal"
    assert r["url"] == "https://en.wikibooks.org/wiki/Cookbook%3ATadka_Dal" and r["revision"] == 123
    assert r["license"] == "CC BY-SA 4.0" and r["credit"] == "Wikibooks contributors"
    assert r["servings"] == 4 and r["time"] == "40 minutes"
    assert r["ingredients"][0] == "1 cup toor dal" and "2 green chillies, slit" in r["ingredients"]
    assert r["steps"][0] == "Rinse the dal and cook it in the water until soft." and len(r["steps"]) == 3
    assert r["veg"] and not r["vegan"] and r["allergens"] == ["dairy"]          # ghee


def test_meat_pages_are_not_veg_and_pages_without_steps_are_skipped():
    r = wb.parse("Cookbook:Chicken Curry", CHICKEN)
    assert r["veg"] is False and r["steps"] == ["Brown the onions.", "Add the chicken and cook through."]
    assert wb.parse("Cookbook:Plain Rice", NO_STEPS) is None


def test_markup_is_reduced_to_plain_text():
    assert wb.plain("[[Cookbook:Salt|salt]] and [[pepper]] {{nowrap|x}} '''bold''' [https://a.example b]") == "salt and pepper bold b"


class FakeAPI:
    """MediaWiki's categorymembers (paged with continue) and revisions replies."""

    def __init__(self):
        self.requests = []

    def __call__(self, req, timeout=None):
        assert req.get_header("User-agent").startswith("Ziggy-meal-planner/")
        from urllib.parse import parse_qs, urlparse
        q = {k: v[0] for k, v in parse_qs(urlparse(req.full_url).query).items()}
        self.requests.append(q)
        if q.get("list") == "categorymembers":
            assert q["cmnamespace"] == "102" and q["cmtitle"] == "Category:Indian recipes"
            if "cmcontinue" not in q:
                body = {"query": {"categorymembers": [{"title": "Cookbook:Tadka Dal"}]},
                        "continue": {"cmcontinue": "page|2", "continue": "-||"}}
            else:
                body = {"query": {"categorymembers": [{"title": "Cookbook:Chicken Curry"}, {"title": "Cookbook:Plain Rice"}]}}
        else:
            texts = {"Cookbook:Tadka Dal": DAL, "Cookbook:Chicken Curry": CHICKEN, "Cookbook:Plain Rice": NO_STEPS}
            body = {"query": {"pages": [{"title": t, "revisions": [{"revid": i, "slots": {"main": {"content": texts[t]}}}]}
                                        for i, t in enumerate(q["titles"].split("|"))]}}
        return io.BytesIO(json.dumps(body).encode())


def test_import_follows_paging_and_keeps_licence(tmp_path, monkeypatch):
    api = FakeAPI()
    snap = wb.import_category("Category:Indian recipes", opener=api, pause=0)
    assert [r["title"] for r in snap["recipes"]] == ["Chicken Curry", "Tadka Dal"]       # Plain Rice: no steps
    assert snap["license"]["name"] == "CC BY-SA 4.0" and "Wikibooks contributors" in snap["note"]
    assert len(api.requests) == 3 and all(r["maxlag"] == "5" for r in api.requests)


@pytest.fixture
def library(tmp_path, monkeypatch):
    path = tmp_path / "wikibooks_recipes.json"
    snap = wb.import_category("Category:Indian recipes", opener=FakeAPI(), pause=0)
    path.write_text(json.dumps(snap))
    monkeypatch.setattr(recipe_library, "DATA_FILE", str(path))
    recipe_library._CACHE.update(mtime=None, data=None)
    return path


def test_library_hides_what_breaks_diet_or_allergies(library):
    veg = recipe_library.for_user({"diet": "veg", "allergens": []})
    assert [r["title"] for r in veg["recipes"]] == ["Tadka Dal"] and veg["hidden"] == 1
    dairy_free = recipe_library.for_user({"diet": "nonveg", "allergens": ["dairy"]})
    assert [r["title"] for r in dairy_free["recipes"]] == ["Chicken Curry"]
    assert recipe_library.for_user({"diet": "nonveg", "allergens": []}, "toor")["total"] == 1


def test_no_snapshot_means_no_library(monkeypatch, tmp_path):
    monkeypatch.setattr(recipe_library, "DATA_FILE", str(tmp_path / "missing.json"))
    recipe_library._CACHE.update(mtime=None, data=None)
    assert recipe_library.for_user({"diet": "veg"}) == {"available": False, "recipes": [], "hidden": 0}


def test_library_route_is_private_and_filtered(library, seeded):
    from smartplate.app import create_app
    from test_followups import _secure_profile
    c = create_app().test_client()
    _secure_profile(c, 2)                                   # Meera: vegan, dairy allergy
    body = c.get("/api/user/2/recipes").get_json()
    assert body["available"] and body["recipes"] == [] and body["hidden"] == 2
    assert create_app().test_client().get("/api/user/2/recipes").status_code in (401, 403)
