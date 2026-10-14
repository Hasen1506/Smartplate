"""Founder decision 14 Oct 2026: keep people with allergies in, and say how likely a
restaurant dish holds each allergen from the recipe library ("usually contains cashew —
7 of 9 recipes"), with the caveat that restaurants cook differently. Information only:
the checker stays fail-closed and nothing certifies a dish safe."""
import pytest

from smartplate.app import create_app
from smartplate.domain import allergens, dish_library, models


@pytest.fixture()
def client(seeded):
    app = create_app()
    app.config.update(TESTING=True)
    return app.test_client()


def test_likelihood_counts_recipes_per_allergen_and_names_the_ingredient():
    lk = dish_library.likelihood("Paneer Butter Masala")
    assert lk and lk["recipes"] >= 2 and lk["caveat"].startswith("From home recipes")
    rows = {r["allergen"]: r for r in lk["allergens"]}
    nut = rows["tree_nut"]
    assert 0 < nut["recipes"] <= nut["of"] == lk["recipes"]
    assert nut["share"] == round(nut["recipes"] / nut["of"], 2)
    assert "cashew" in nut["words"] and f"{nut['recipes']} of {nut['of']} recipes" in nut["text"]
    assert nut["text"].startswith(("Usually contains", "Often contains", "Sometimes contains"))
    shares = [r["share"] for r in lk["allergens"]]
    assert shares == sorted(shares, reverse=True)


def test_unknown_dish_has_no_likelihood_and_stays_fail_closed():
    assert dish_library.likelihood("Xyzzy Special") is None
    meera = {"diet": "nonveg", "allergens": ["tree_nut"], "medical": []}
    out = allergens.allergen_likelihood(meera, "Xyzzy Special")
    assert out["known"] is False and out["never_safe"] and "Ask the restaurant" in out["text"]
    v = allergens.check(meera, {"name": "Xyzzy Special", "source": "live"})
    assert v["status"] in ("unknown", "unsafe")                        # still hidden for an allergy


def test_likelihood_never_turns_a_verdict_into_safe():
    who = {"diet": "nonveg", "allergens": ["tree_nut"], "medical": []}
    for name in ("Paneer Butter Masala", "Pongal", "Chicken Biryani", "Masala Dosa"):
        v = allergens.check(who, {"name": name})
        assert v["status"] != "ok"                                     # at best "not checked"
        label = allergens.safety_label(who, {"name": name})
        assert "allergen_likelihood" in label and label["allergen_likelihood"]["never_safe"]
        assert "safe" not in label["note"].lower().replace("unsafe", "")


def test_yours_lists_only_the_persons_allergens_and_zero_is_not_free_of():
    who = {"diet": "nonveg", "allergens": ["shellfish"], "medical": []}
    out = allergens.allergen_likelihood(who, "Paneer Butter Masala")
    assert out["known"] and out["yours"] == []
    assert "not a promise" in out["text"] and "ask the restaurant" in out["text"]
    nuts = allergens.allergen_likelihood({"allergens": ["tree_nut"]}, "Paneer Butter Masala")
    assert [r["allergen"] for r in nuts["yours"]] == ["tree_nut"] and "cashew" in nuts["text"]


def test_household_allergies_count():
    user = {"diet": "nonveg", "allergens": [], "medical": [],
            "household_members": [{"name": "Dev", "allergens": ["tree_nut"], "diet": "veg"}]}
    out = allergens.allergen_likelihood(user, "Pongal")
    assert [r["allergen"] for r in out["yours"]] == ["tree_nut"]
    assert "allergen_likelihood" in allergens.safety_label(user, {"name": "Pongal"})


def test_no_allergy_no_likelihood_noise_on_labels():
    label = allergens.safety_label({"diet": "nonveg", "allergens": [], "medical": []}, {"name": "Pongal"})
    assert "allergen_likelihood" not in label


def test_api_allergen_likelihood(client):
    # user 2 (Meera) is vegan with a dairy allergy
    r = client.get("/api/user/2/allergen-likelihood?name=Paneer Butter Masala")
    assert r.status_code == 200
    body = r.get_json()
    lk = body["allergen_likelihood"]
    assert lk["known"] and [x["allergen"] for x in lk["yours"]] == ["dairy"]
    assert lk["never_safe"] is True and "restaurants cook differently" in lk["caveat"]
    assert body["safety"]["status"] in ("unsafe", "unknown")
    assert client.get("/api/user/2/allergen-likelihood").status_code == 400
    assert client.get("/api/user/999/allergen-likelihood?name=Pongal").status_code == 404
    assert models.get_user(2)["allergens"] == ["dairy"]
