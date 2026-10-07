"""Roadmap G — Epicure ingredient embeddings (Kaikaku/epicure-core, CC BY 4.0).

Real user flows on the offline fixture (tests/fixtures/epicure: the real file formats,
a subset of the real vectors):
  • cook mode: swap a recipe ingredient for one that is safe for everyone eating;
  • grocery: an item is out of stock → pick a safe replacement → the list shows it;
  • a recipe someone can't eat is offered "safe with a swap";
  • "more like this" on a planned dish → similar dishes now and in the re-plan;
  • a cuisine lean (slerp toward a cuisine pole) nudges the plan, with a reason.
Safety is a property: no suggestion ever belongs to anyone's allergen family or breaks
their diet, checked against an oracle written independently of the app's labels.
"""
import hashlib
import io
import json
import os
import shutil
import struct

import numpy as np
import pytest
from hypothesis import given
from hypothesis import strategies as st

from smartplate import config, db
from smartplate.app import create_app
from smartplate.domain import epicure, flavour, ingredients, models, reverse_mode
from smartplate.kernel import optimizer

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "epicure")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# --------------------------------------------------------------------------- #
# The independent allergen/diet oracle (deliberately broader than the app's guard)
# --------------------------------------------------------------------------- #
ORACLE_WORDS = {
    "dairy": {"milk", "cheese", "cream", "butter", "yogurt", "yoghurt", "curd", "ghee", "paneer", "whey", "khoya",
              "khoa", "buttermilk", "kefir", "custard", "lassi", "casein", "lactose", "malai", "raita", "labneh"},
    "gluten": {"wheat", "flour", "atta", "maida", "semolina", "rava", "suji", "barley", "rye", "spelt", "bulgur",
               "couscous", "vermicelli", "noodle", "pasta", "bread", "roti", "naan", "paratha", "oat", "malt",
               "seitan", "asafoetida", "hing", "sambar", "chaat", "soy_sauce", "farro", "freekeh", "bran"},
    "egg": {"egg", "mayonnaise", "meringue", "noodle", "pasta", "custard"},
    "soy": {"soy", "soya", "soybean", "tofu", "tempeh", "edamame", "miso", "natto", "tamari", "vegetable_oil"},
    "shellfish": {"shrimp", "prawn", "crab", "lobster", "squid", "clam", "mussel", "oyster", "scallop", "crayfish",
                  "octopus", "krill"},
    "fish": {"fish", "sardine", "mackerel", "pomfret", "tuna", "salmon", "anchovy", "cod", "hilsa", "bonito"},
    "sesame": {"sesame", "tahini", "gingelly", "til"},
    "tree_nut": {"almond", "cashew", "walnut", "pistachio", "hazelnut", "pecan", "macadamia", "coconut", "nut",
                 "chestnut", "praline", "marzipan"},
    "peanut": {"peanut", "groundnut"},
}
# deliberate decisions, asserted rather than hidden: plant milks are not dairy; rice
# noodles contain neither wheat nor egg
ORACLE_ALLOWED = {("soy_milk", "dairy"), ("almond_milk", "dairy"), ("oat_milk", "dairy"), ("rice_milk", "dairy"),
                  ("coconut_milk", "dairy"), ("coconut_cream", "dairy"), ("soy_yogurt", "dairy"),
                  ("rice_noodle", "gluten"), ("rice_noodle", "egg"), ("cocoa_butter", "dairy"),
                  ("rice_bran_oil", "gluten")}
ORACLE_NOT_VEG = {"chicken", "mutton", "lamb", "goat", "beef", "pork", "fish", "sardine", "mackerel", "pomfret",
                  "tuna", "salmon", "shrimp", "prawn", "crab", "squid", "egg", "meat", "gelatin", "bacon", "ham"}
ORACLE_NOT_VEGAN = ORACLE_NOT_VEG | ORACLE_WORDS["dairy"] | {"honey"}


def _oracle_words(token):
    parts = token.split("_")
    return set(parts) | {"_".join(parts[i:i + 2]) for i in range(len(parts) - 1)} | {token}


def oracle_families(token):
    w = _oracle_words(token)
    return {fam for fam, kw in ORACLE_WORDS.items() if w & kw and (token, fam) not in ORACLE_ALLOWED}


def oracle_diet_ok(token, diet):
    w = _oracle_words(token) - ({token} if (token, "dairy") in ORACLE_ALLOWED else set())
    if diet == "vegan":
        bad = ORACLE_NOT_VEGAN - (ORACLE_WORDS["dairy"] if (token, "dairy") in ORACLE_ALLOWED else set())
        return not (w & bad)
    if diet == "veg":
        return not (w & ORACLE_NOT_VEG)
    return True


def _person(allergens=(), diet="nonveg", medical=(), name="Me"):
    return {"name": name, "allergens": list(allergens), "diet": diet, "medical": list(medical)}


people_st = st.lists(
    st.builds(lambda a, d, c, n: _person(sorted(a), d, ["celiac"] if c else [], n),
              st.sets(st.sampled_from(ingredients.ALLERGENS)), st.sampled_from(["vegan", "veg", "nonveg"]),
              st.booleans(), st.sampled_from(["Asha", "Ravi", "Meera"])),
    min_size=1, max_size=3)


def _assert_safe(token, people):
    for p in people:
        banned = set(p["allergens"]) | ({"gluten"} if "celiac" in p["medical"] else set())
        assert not (oracle_families(token) & banned), f"{token} suggested to someone avoiding {banned}"
        assert oracle_diet_ok(token, p["diet"]), f"{token} suggested to a {p['diet']} eater"


# --------------------------------------------------------------------------- #
# Loading: numpy-only safetensors, pinned checksums, fixture integrity
# --------------------------------------------------------------------------- #
def _safetensors(header, body=b""):
    h = json.dumps(header).encode()
    return struct.pack("<Q", len(h)) + h + body


def test_safetensors_reader_parses_header_by_hand():
    arr = np.arange(6, dtype="<f4").reshape(2, 3)
    blob = _safetensors({"__metadata__": {"x": "y"},
                         "embeddings": {"dtype": "F32", "shape": [2, 3], "data_offsets": [0, 24]}}, arr.tobytes())
    out = epicure.read_safetensors(blob)
    assert out.shape == (2, 3) and out.dtype == np.float32 and out[1, 2] == 5.0


@pytest.mark.parametrize("blob, msg", [
    (b"\x01\x02", "too short"),
    (struct.pack("<Q", 10 ** 9) + b"{}", "out of range"),
    (struct.pack("<Q", 3) + b"{x}", "not JSON"),
    (_safetensors({"other": {"dtype": "F32", "shape": [1, 1], "data_offsets": [0, 4]}}, b"\0" * 4), "no tensor"),
    (_safetensors({"embeddings": {"dtype": "F16", "shape": [1, 2], "data_offsets": [0, 4]}}, b"\0" * 4), "F32"),
    (_safetensors({"embeddings": {"dtype": "F32", "shape": [2, 2], "data_offsets": [0, 8]}}, b"\0" * 8), "byte length"),
    (_safetensors({"embeddings": {"dtype": "F32", "shape": [1, 2], "data_offsets": [0, 8]}}, b"\0" * 4), "outside"),
])
def test_safetensors_reader_rejects_malformed_files(blob, msg):
    with pytest.raises(epicure.EpicureError, match=msg):
        epicure.read_safetensors(blob)


def test_fixture_loads_with_real_formats_and_cuisine_poles():
    m = epicure.get()
    assert m is not None, epicure.status()
    assert m.E.shape[1] == 300 and np.allclose(np.linalg.norm(m.E, axis=1), 1, atol=1e-5)
    assert m.factor_poles.shape == (87, 300) and len(m.factor_ids) == 87
    assert {"South_Asian", "East_Asian", "Mediterranean", "Latin_American"} <= set(m.cuisine_poles)
    # every ingredient SmartPlate can suggest, and every token a dish name implies, has a vector
    assert all(m.has(t) for t in ingredients.PANTRY)
    assert all(m.has(t) for ts in flavour.DISH_WORDS.values() for t in ts)
    # the model card's slerp example holds on the real vectors: rice → South Asian spices
    near = [t for t, _ in m.rank(m.slerp(m.vec("rice"), m.cuisine_poles["South_Asian"], 30), m.itos) if t != "rice"]
    assert {"turmeric", "mustard_seed", "cumin"} <= set(near[:8])


def test_pinned_checksums_cover_every_file_and_reject_tampering(tmp_path, monkeypatch):
    assert set(epicure.PINNED) == set(epicure.FILES) and all(len(v) == 64 for v in epicure.PINNED.values())
    for f in epicure.FILES:
        shutil.copy(os.path.join(FIXTURE, f), tmp_path / f)
    sums = epicure._load_checksums(os.path.join(FIXTURE, "SHA256SUMS"))
    epicure.load(str(tmp_path), sums)                                 # intact: loads
    with open(tmp_path / "vocab.json", "a") as fh:
        fh.write(" ")                                                  # one byte changed
    with pytest.raises(epicure.EpicureError, match="vocab.json"):
        epicure.load(str(tmp_path), sums)
    # production pins: the fixture is not the real file set, so get() refuses it and the
    # features hide instead of running on unverified data
    monkeypatch.setattr(config, "EPICURE_CHECKSUMS", "pinned")
    assert epicure.get() is None and "does not match" in epicure.status()["error"]


def test_missing_files_hide_the_features(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "EPICURE_DIR", str(tmp_path))
    assert epicure.get() is None
    r = ingredients.substitutes("toor_dal", [_person()])
    assert r == {"available": False, "ingredient": "toor_dal", "name": "Toor dal", "options": [], "hidden_unsafe": 0}


def test_vocab_and_itos_must_agree(tmp_path):
    for f in epicure.FILES:
        shutil.copy(os.path.join(FIXTURE, f), tmp_path / f)
    itos = json.load(open(tmp_path / "itos.json"))
    itos["0"], itos["1"] = itos["1"], itos["0"]
    json.dump(itos, open(tmp_path / "itos.json", "w"))
    with pytest.raises(epicure.EpicureError, match="disagree"):
        epicure.load(str(tmp_path), None)


# --------------------------------------------------------------------------- #
# The build-time download
# --------------------------------------------------------------------------- #
def _fetch_module():
    import importlib.util
    spec = importlib.util.spec_from_file_location("fetch_epicure", os.path.join(ROOT, "scripts", "fetch_epicure.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_fetch_verifies_sha256_and_never_leaves_a_bad_file(tmp_path, monkeypatch):
    fetch = _fetch_module()
    good = b'{"a": 0}'
    monkeypatch.setitem(epicure.PINNED, "vocab.json", hashlib.sha256(good).hexdigest())
    urls = []

    def opener(url, timeout):
        urls.append(url)
        return _Resp(served)

    served = b'{"a": 1}'                                               # tampered in transit
    with pytest.raises(ValueError, match="does not match"):
        fetch.fetch("vocab.json", str(tmp_path), opener=opener)
    assert os.listdir(tmp_path) == []                                  # nothing half-written
    assert urls[0] == f"https://huggingface.co/Kaikaku/epicure-core/resolve/{epicure.REVISION}/vocab.json"
    served = good
    assert fetch.fetch("vocab.json", str(tmp_path), opener=opener) == "downloaded"
    assert (tmp_path / "vocab.json").read_bytes() == good
    assert fetch.fetch("vocab.json", str(tmp_path), opener=lambda *a, **k: 1 / 0) == "kept"


def test_build_and_ci_fetch_before_serving():
    for f in ("render.yaml", "render.production.yaml"):
        assert "python scripts/fetch_epicure.py" in open(os.path.join(ROOT, f)).read()
    assert "numpy==" in open(os.path.join(ROOT, "requirements.txt")).read()
    req = open(os.path.join(ROOT, "requirements.txt")).read().lower()
    assert not any(x in req for x in ("torch", "safetensors", "gensim"))


# --------------------------------------------------------------------------- #
# Safety properties (the oracle, never the app's own labels)
# --------------------------------------------------------------------------- #
@given(token=st.sampled_from(sorted(ingredients.PANTRY)), people=people_st)
def test_property_substitutes_never_suggest_an_allergen_family_or_break_a_diet(token, people):
    r = ingredients.substitutes(token, people, k=len(ingredients.PANTRY))
    assert r["available"]
    for o in r["options"]:
        assert o["token"] != token
        _assert_safe(o["token"], people)
        assert set(ingredients.roles(o["token"])) & set(ingredients.roles(token))     # same job in the dish
        # a vegetarian ingredient is never swapped for egg, meat or fish
        assert ingredients.DIET_RANK[ingredients.diet_class(o["token"])] <= \
            max(ingredients.DIET_RANK[ingredients.diet_class(token)], ingredients.DIET_RANK["veg"])


@given(people=people_st)
def test_property_recipes_made_safe_contain_nothing_anyone_avoids(people):
    for r in reverse_mode.RECIPES:
        fix = ingredients.make_safe(r, people)
        if not fix:
            continue
        swapped = {s["from"]: s["to"] for s in fix["swaps"]}
        final = [swapped.get(i["token"], i["token"]) for i in r["ingredients"]]
        for t in final:
            _assert_safe(t, people)


def test_every_pantry_label_is_at_least_as_strict_as_the_oracle():
    for t in ingredients.PANTRY:
        assert oracle_families(t) <= ingredients.families(t), t
        for diet in ("vegan", "veg"):
            if not oracle_diet_ok(t, diet):
                assert ingredients.diet_class(t) not in ingredients.DIET_ALLOWS[diet], (t, diet)


def test_recipe_labels_match_their_ingredients():
    """A recipe's allergens and veg mark are exactly what its ingredients imply, so the
    planner's recipe-level rule and the swap engine never disagree."""
    for r in reverse_mode.RECIPES:
        fams = set().union(*(ingredients.families(i["token"]) for i in r["ingredients"]))
        assert fams == set(r["allergens"]), r["key"]
        veg = all(ingredients.diet_class(i["token"]) in ("vegan", "veg") for i in r["ingredients"])
        assert veg == bool(r["veg"]), r["key"]
        for b in r["basket"]:
            assert b["token"] is None or b["token"] in {i["token"] for i in r["ingredients"]}


def test_swaps_are_sensible_on_real_vectors():
    me = [_person()]
    assert {o["token"] for o in ingredients.substitutes("toor_dal", me)["options"]} <= \
        {"chana_dal", "urad_dal", "masoor_dal", "mung_bean", "horse_gram", "lentil"}
    milk = ingredients.substitutes("milk", [_person(["dairy", "soy", "tree_nut"])])
    assert {o["token"] for o in milk["options"]} == {"oat_milk", "rice_milk"} and milk["hidden_unsafe"] == 3
    celiac = ingredients.substitutes("whole_wheat_flour", [_person(medical=["celiac"])])
    assert celiac["options"] and all("gluten" not in ingredients.families(o["token"]) for o in celiac["options"])
    # paneer for a dairy-allergic non-vegetarian is still a vegetarian swap, never meat
    assert all(ingredients.diet_class(o["token"]) in ("vegan", "veg")
               for o in ingredients.substitutes("paneer", [_person(["dairy"])])["options"])


# --------------------------------------------------------------------------- #
# Flows through the API
# --------------------------------------------------------------------------- #
@pytest.fixture
def client(seeded):
    app = create_app()
    app.config["TESTING"] = True
    return app.test_client()


def _profile(client, **over):
    body = {"name": "Kavya", "diet": "veg", "allergens": [], "weekly_budget": 2500,
            "meals": ["lunch", "dinner"], "cook": "sometimes", **over}
    r = client.post("/api/profiles", json=body)
    assert r.status_code == 201, r.get_json()
    v = r.get_json()
    return v, {"X-SmartPlate-Key": v["access_key"]}


def _open_session(view, meal="lunch"):
    return next(c["session_id"] for d in view["grid"] for m, c in d["meals"].items()
                if m == meal and c["status"] == "active")


def test_cook_day_ingredient_swap_and_out_of_stock_grocery_flow(client):
    v, h = _profile(client)
    sid = _open_session(v)
    v = client.post(f"/api/session/{sid}/choose", json={"recipe_key": "dal_rice"}, headers=h).get_json()
    pid = v["plan"]["id"]
    coach = v["coach"]
    assert coach["swaps_available"]
    dal = next(r for r in coach["recipes"] if r["key"] == "dal_rice")
    assert [i["token"] for i in dal["ingredients"]][:2] == ["toor_dal", "rice"] and dal["ingredients"][0]["swappable"]
    line = next(b for b in coach["basket"]["items"] if b["token"] == "toor_dal")
    assert line["swappable"] and line["swap"] is None
    # "Out of stock?" on the toor dal line
    opts = client.get(f"/api/plan/{pid}/swaps?token=toor_dal", headers=h).get_json()
    assert opts["available"] and opts["options"] and opts["options"][0]["token"] in {"chana_dal", "urad_dal", "masoor_dal"}
    pick = opts["options"][0]["token"]
    v = client.post(f"/api/plan/{pid}/grocery-swap", json={"token": "toor_dal", "swap_token": pick,
                                                           "reason": "out_of_stock"}, headers=h).get_json()
    line = next(b for b in v["coach"]["basket"]["items"] if b["token"] == "toor_dal")
    assert line["swap"] == {"token": pick, "name": ingredients.name(pick), "reason": "out_of_stock"}
    assert v["coach"]["basket"]["total"] == coach["basket"]["total"]          # estimate stays the original price
    dal = next(r for r in v["coach"]["recipes"] if r["key"] == "dal_rice")
    assert dal["ingredients"][0]["swap"]["token"] == pick
    # undo
    v = client.post(f"/api/plan/{pid}/grocery-swap", json={"token": "toor_dal", "swap_token": None}, headers=h).get_json()
    assert next(b for b in v["coach"]["basket"]["items"] if b["token"] == "toor_dal")["swap"] is None


def test_unsafe_or_unrelated_swaps_are_refused(client):
    v, h = _profile(client, allergens=["dairy"])
    sid = _open_session(v)
    v = client.post(f"/api/session/{sid}/choose", json={"recipe_key": "veg_pulao"}, headers=h).get_json()
    pid = v["plan"]["id"]
    bad = [{"token": "sunflower_oil", "swap_token": "ghee"},          # dairy for a dairy allergy
           {"token": "sunflower_oil", "swap_token": "toor_dal"},      # not the same job
           {"token": "chicken", "swap_token": "paneer"},              # not in this week's cooking
           {"token": "sunflower_oil", "swap_token": "sunflower_oil", "reason": "because"}]
    for body in bad:
        r = client.post(f"/api/plan/{pid}/grocery-swap", json=body, headers=h)
        assert r.status_code == 400, body
    assert client.get(f"/api/plan/{pid}/swaps?token=chicken", headers=h).status_code == 400
    with db.cursor() as cur:
        assert cur.execute("SELECT COUNT(*) n FROM grocery_swaps").fetchone()["n"] == 0


def test_stored_swap_disappears_when_it_becomes_unsafe(client):
    v, h = _profile(client)
    sid = _open_session(v)
    v = client.post(f"/api/session/{sid}/choose", json={"recipe_key": "veg_pulao"}, headers=h).get_json()
    pid, uid = v["plan"]["id"], v["user"]["id"]
    v = client.post(f"/api/plan/{pid}/grocery-swap", json={"token": "sunflower_oil", "swap_token": "ghee"},
                    headers=h).get_json()
    assert next(r for r in v["coach"]["recipes"] if r["key"] == "veg_pulao")["ingredients"][-1]["swap"]["token"] == "ghee"
    r = client.patch(f"/api/user/{uid}/setup", json={"allergens": ["dairy"]}, headers=h)
    assert r.status_code == 200, r.get_json()
    v = client.get(f"/api/plan/{pid}", headers=h).get_json()
    pulao = [r for r in v["coach"]["recipes"] if r["key"] == "veg_pulao"]
    assert all(i["swap"] is None for r in pulao for i in r["ingredients"])


def test_recipes_someone_cannot_eat_are_offered_safe_with_a_swap(client):
    v, h = _profile(client, diet="veg", allergens=["gluten"])
    fixes = {f["key"]: f for f in v["coach"]["safe_with_swap"]}
    assert set(fixes) == {"egg_curry", "oats_bowl"}
    egg = {s["from"]: s for s in fixes["egg_curry"]["swaps"]}
    assert set(egg) == {"egg", "whole_wheat_flour"}
    assert ingredients.diet_class(egg["egg"]["to"]) in ("vegan", "veg")
    assert "gluten" not in ingredients.families(egg["whole_wheat_flour"]["to"])
    oats = fixes["oats_bowl"]["swaps"]
    assert [s["from"] for s in oats] == ["oat"] and "gluten" not in ingredients.families(oats[0]["to"])
    # everyone else eats everything as written: nothing to fix
    v2, _ = _profile(client, diet="nonveg", allergens=[], name="Ravi")
    assert v2["coach"]["safe_with_swap"] == []


def test_household_allergies_limit_swaps(client):
    v, h = _profile(client, diet="nonveg")
    uid = v["user"]["id"]
    with db.cursor() as cur:
        cur.execute("INSERT INTO households(name, split) VALUES ('Home', 'even')")
        hid = cur.lastrowid
        cur.execute("UPDATE users SET household_id=? WHERE id=?", (hid, uid))
        cur.execute("INSERT INTO users(name, city, diet, allergens, household_id) VALUES ('Dev', 'Chennai', 'vegan', ?, ?)",
                    (json.dumps(["soy"]), hid))
    user = models.get_user(uid)
    opts = ingredients.substitutes("milk", ingredients.people_of(user))["options"]
    assert opts and all(ingredients.diet_class(o["token"]) == "vegan" and "soy" not in ingredients.families(o["token"])
                        for o in opts)


def _delivery_session(view):
    return next(c["session_id"] for d in view["grid"] for c in d["meals"].values()
                if c["status"] == "active" and c["kind"] == "delivery")


def test_more_like_this_records_replans_and_offers_similar_safe_dishes(client):
    v, h = _profile(client, diet="nonveg", allergens=["dairy"])
    sid = _delivery_session(v)
    dish = next(c["item"] for d in v["grid"] for c in d["meals"].values() if c["session_id"] == sid)
    r = client.post(f"/api/session/{sid}/more-like", json={}, headers=h)
    assert r.status_code == 200, r.get_json()
    out = r.get_json()
    assert out["recorded"] == dish
    assert out["similar"], dish                                        # the menu has dishes alike
    assert models.get_user(v["user"]["id"])["prefs"]["more_like"][-1]["name"] == dish
    menu = {it["id"]: it for it in models.menu_for_city("Chennai")}
    for s in out["similar"]:
        assert s["name"] != dish and "dairy" not in menu[s["item_id"]]["allergens"]
        assert s["similarity"] >= flavour.SIMILAR_FLOOR
    # the re-plan nudges every similar dish, and any planned one says why
    user = models.get_user(v["user"]["id"])
    fctx = flavour.context(user, models.menu_for_city("Chennai"))
    for s in out["similar"]:
        bonus, why = flavour.bonus(fctx, s["name"])
        assert bonus > 0 and why == [f"Similar to {dish}, which you asked for more of."]
    names = {s["name"] for s in out["similar"]}
    for d in out["plan"]["grid"]:
        for c in d["meals"].values():
            if c["status"] == "active" and c["item"] in names:
                assert f"Similar to {dish}, which you asked for more of." in c["reasons"]
    # forget it again
    v = client.post(f"/api/user/{v['user']['id']}/more-like/forget", json={"name": dish}, headers=h).get_json()
    assert v["user"]["prefs"]["more_like"] == []
    assert client.post(f"/api/user/{v['user']['id']}/more-like/forget", json={"name": dish}, headers=h).status_code == 400


def test_similarity_nudge_and_tilt_are_exactly_zero_when_unset(seeded):
    user = models.get_user(1)
    assert flavour.context(user, models.menu_for_city(user["city"])) == {}
    assert flavour.bonus({}, "Chicken Biryani") == (0.0, [])


def test_more_like_this_ranks_similar_dishes_on_real_vectors(seeded):
    menu = models.menu_for_city("Chennai")
    near = [x["name"] for x in flavour.similar_items("Chicken Biryani", menu, k=2)]
    assert set(near) == {"Mutton Biryani", "Veg Biryani"}
    dosa = [x["name"] for x in flavour.similar_items("Masala Dosa", menu, k=1)]
    assert dosa == ["Ghee Podi Dosa"]


def test_cuisine_tilt_validates_and_nudges_with_an_honest_reason(client):
    v, h = _profile(client, diet="nonveg")
    uid = v["user"]["id"]
    for bad in ({"cuisine": "Martian"}, {"cuisine": "Mediterranean", "strength": "max"}, "Mediterranean",
                {"cuisine": "Southeast_Asian"}):
        assert client.patch(f"/api/user/{uid}/setup", json={"cuisine_tilt": bad}, headers=h).status_code == 400
    v = client.patch(f"/api/user/{uid}/setup", json={"cuisine_tilt": {"cuisine": "Mediterranean", "strength": "strong"}},
                     headers=h).get_json()
    assert v["user"]["prefs"]["cuisine_tilt"] == {"cuisine": "Mediterranean", "strength": "strong"}
    user = models.get_user(uid)
    fctx = flavour.context(user, models.menu_for_city("Chennai"))
    nudged = {n for n in fctx["vec"] if flavour.bonus(fctx, n)[0] > 0}
    assert nudged and nudged <= {"Grilled Chicken Salad", "Cold Brew + Sandwich", "Quinoa Buddha Bowl"}
    for n in nudged:
        assert flavour.bonus(fctx, n)[1] == ["Leans Mediterranean, as you asked."]
    # every reason the plan shows is backed by a nudged dish
    for d in v["grid"]:
        for c in d["meals"].values():
            if any("Leans Mediterranean" in r for r in c["reasons"]):
                assert c["item"] in nudged
    v = client.patch(f"/api/user/{uid}/setup", json={"cuisine_tilt": None}, headers=h).get_json()
    assert v["user"]["prefs"]["cuisine_tilt"] is None
    assert flavour.context(models.get_user(uid), models.menu_for_city("Chennai")) == {}


def test_planner_unchanged_without_epicure_signals(seeded, monkeypatch, tmp_path):
    """Installing Epicure must not move a single meal for someone who never used it."""
    from smartplate import service
    plan = service.create_plan(1)
    with_model = [(d["session_id"], d["chosen_kind"], d.get("item_id"), d.get("recipe_key"))
                  for d in models.decisions_for_plan(plan)]
    monkeypatch.setattr(config, "EPICURE_DIR", str(tmp_path))
    optimizer.optimize(plan)
    without = [(d["session_id"], d["chosen_kind"], d.get("item_id"), d.get("recipe_key"))
               for d in models.decisions_for_plan(plan)]
    assert with_model == without


def test_credits_page_and_meta(client):
    page = client.get("/credits")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    for must in ("Epicure-Core", "Jakub Radzikowski and Josef Chen", "CC BY 4.0",
                 "https://creativecommons.org/licenses/by/4.0/", epicure.REVISION, "arXiv:2605.22391",
                 "Changes made"):
        assert must in html
    meta = client.get("/api/meta").get_json()["epicure"]
    assert meta["available"] and "Mediterranean" in meta["cuisines"] and "Southeast_Asian" not in meta["cuisines"]


def test_profile_export_and_delete_include_grocery_swaps(client):
    v, h = _profile(client)
    sid = _open_session(v)
    v = client.post(f"/api/session/{sid}/choose", json={"recipe_key": "dal_rice"}, headers=h).get_json()
    pid, uid = v["plan"]["id"], v["user"]["id"]
    client.post(f"/api/plan/{pid}/grocery-swap", json={"token": "toor_dal", "swap_token": "masoor_dal"}, headers=h)
    data = client.get(f"/api/user/{uid}/data.json", headers=h).get_json()
    assert data["data"]["grocery_swaps"][0]["swap_token"] == "masoor_dal"
    assert client.delete(f"/api/user/{uid}", json={"confirmation": "DELETE"}, headers=h).status_code == 200
    with db.cursor() as cur:
        assert cur.execute("SELECT COUNT(*) n FROM grocery_swaps WHERE plan_id=?", (pid,)).fetchone()["n"] == 0


# --------------------------------------------------------------------------- #
# Found by the property suite while building G (PYTHONHASHSEED unset): a checkout
# approval sent as a huge number crashed with a 500 instead of asking for a review.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("bad", [9_223_372_036_854_775_808, 12, ["x"], {"a": 1}, True])
def test_checkout_with_a_non_string_approval_asks_for_review_not_500(gt, swiggy_replay, bad):
    from gt_support import connect_swiggy
    app = create_app()
    c = app.test_client()
    created = c.post("/api/profiles", json={"name": "Live", "diet": "veg", "weekly_budget": 2000,
                                            "meals": ["dinner"]}).get_json()
    uid, key = created["user"]["id"], created["access_key"]
    connect_swiggy(c, swiggy_replay, uid, key)
    r = c.post(f"/api/user/{uid}/swiggy/checkout", json={"expected_fingerprint": bad},
               headers={"X-SmartPlate-Key": key})
    assert r.status_code == 409 and "Review" in r.get_json()["message"]
    assert "place_food_order" not in swiggy_replay.tool_calls()
