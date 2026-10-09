"""Ingredient swaps that are safe for everyone eating: cook mode, grocery, out of stock.

Safety comes from explicit labels, never from the embedding. Every ingredient SmartPlate
can suggest is in PANTRY below with its allergen families and diet class, written by
hand and deliberately conservative (coconut counts as a tree nut, hing as gluten, oats
as gluten, "vegetable oil" as soy). A keyword guard over the Epicure token name runs on
top of the labels, so a mislabelled row still cannot slip an allergen through.

Epicure (domain/epicure.py) only *ranks* the safe candidates: among the pantry items
that do the same job in a dish (the same role), the closest in flavour comes first.
"""
from __future__ import annotations

import re

from . import epicure

ALLERGENS = ("peanut", "dairy", "gluten", "egg", "soy", "shellfish", "fish", "sesame", "tree_nut")

# diet class of an ingredient: vegan (plant) < veg (dairy/honey) < egg < nonveg (meat/fish).
# Indian veg marks (FSSAI) count egg as non-vegetarian, as allergens.violates does.
DIET_RANK = {"vegan": 0, "veg": 1, "egg": 2, "nonveg": 3}
DIET_ALLOWS = {"vegan": {"vegan"}, "veg": {"vegan", "veg"}, "nonveg": {"vegan", "veg", "egg", "nonveg"}}

# token: (display name, roles, allergen families, diet class)
PANTRY: dict[str, tuple[str, tuple[str, ...], tuple[str, ...], str]] = {
    # grains and staples
    "rice": ("Rice", ("grain",), (), "vegan"),
    "basmati_rice": ("Basmati rice", ("grain",), (), "vegan"),
    "brown_rice": ("Brown rice", ("grain",), (), "vegan"),
    "red_rice": ("Red rice", ("grain",), (), "vegan"),
    "black_rice": ("Black rice", ("grain",), (), "vegan"),
    "flattened_young_rice": ("Poha (flattened rice)", ("grain",), (), "vegan"),
    "millet": ("Millet (bajra)", ("grain", "flour"), (), "vegan"),
    "ragi": ("Ragi", ("grain", "flour"), (), "vegan"),
    "sorghum": ("Jowar (sorghum)", ("grain", "flour"), (), "vegan"),
    "amaranth": ("Amaranth (rajgira)", ("grain", "flour"), (), "vegan"),
    "quinoa": ("Quinoa", ("grain",), (), "vegan"),
    "buckwheat": ("Buckwheat (kuttu)", ("grain", "flour"), (), "vegan"),
    "sago": ("Sabudana (sago)", ("grain",), (), "vegan"),
    "cornmeal": ("Cornmeal (makki atta)", ("flour",), (), "vegan"),
    "oat": ("Oats", ("grain",), ("gluten",), "vegan"),          # Indian oats are rarely certified gluten-free
    "barley": ("Barley", ("grain",), ("gluten",), "vegan"),
    "bulgur": ("Bulgur wheat", ("grain",), ("gluten",), "vegan"),
    "couscous": ("Couscous", ("grain",), ("gluten",), "vegan"),
    "semolina": ("Rava (semolina)", ("grain", "flour"), ("gluten",), "vegan"),
    "vermicelli": ("Vermicelli", ("grain",), ("gluten",), "vegan"),
    "whole_wheat_flour": ("Atta (whole wheat flour)", ("flour",), ("gluten",), "vegan"),
    "noodle": ("Noodles", ("grain",), ("gluten", "egg"), "egg"),  # many packs contain egg
    "rice_noodle": ("Rice noodles", ("grain",), (), "vegan"),
    # pulses
    "toor_dal": ("Toor dal", ("pulse",), (), "vegan"),
    "chana_dal": ("Chana dal", ("pulse",), (), "vegan"),
    "masoor_dal": ("Masoor dal", ("pulse",), (), "vegan"),
    "urad_dal": ("Urad dal", ("pulse",), (), "vegan"),
    "mung_bean": ("Moong dal", ("pulse",), (), "vegan"),
    "horse_gram": ("Horse gram (kollu)", ("pulse",), (), "vegan"),
    "lentil": ("Lentils", ("pulse",), (), "vegan"),
    "chickpea": ("Chickpeas (chana)", ("pulse", "protein"), (), "vegan"),
    "kidney_bean": ("Rajma (kidney beans)", ("pulse", "protein"), (), "vegan"),
    "black_eyed_pea": ("Lobia (black-eyed peas)", ("pulse",), (), "vegan"),
    "pea": ("Green peas", ("pulse", "vegetable"), (), "vegan"),
    # protein pieces
    "paneer": ("Paneer", ("protein",), ("dairy",), "veg"),
    "tofu": ("Tofu", ("protein",), ("soy",), "vegan"),
    "tempeh": ("Tempeh", ("protein",), ("soy",), "vegan"),
    "textured_soy_protein": ("Soya chunks", ("protein",), ("soy",), "vegan"),
    "soybean": ("Soybeans", ("pulse", "protein"), ("soy",), "vegan"),
    "mushroom": ("Mushrooms", ("protein", "vegetable"), (), "vegan"),
    "jackfruit": ("Raw jackfruit", ("protein", "vegetable"), (), "vegan"),
    "egg": ("Eggs", ("protein",), ("egg",), "egg"),
    "chicken": ("Chicken", ("protein",), (), "nonveg"),
    "mutton": ("Mutton", ("protein",), (), "nonveg"),
    "lamb": ("Lamb", ("protein",), (), "nonveg"),
    "fish": ("Fish", ("protein",), ("fish",), "nonveg"),
    "sardine": ("Sardines (mathi)", ("protein",), ("fish",), "nonveg"),
    "mackerel": ("Mackerel (bangda)", ("protein",), ("fish",), "nonveg"),
    "pomfret": ("Pomfret", ("protein",), ("fish",), "nonveg"),
    "tuna": ("Tuna", ("protein",), ("fish",), "nonveg"),
    "shrimp": ("Prawns", ("protein",), ("shellfish",), "nonveg"),
    "crab": ("Crab", ("protein",), ("shellfish",), "nonveg"),
    "squid": ("Squid", ("protein",), ("shellfish",), "nonveg"),     # molluscs grouped with shellfish
    # dairy and its swaps
    "milk": ("Milk", ("milk",), ("dairy",), "veg"),
    "soy_milk": ("Soy milk", ("milk",), ("soy",), "vegan"),
    "almond_milk": ("Almond milk", ("milk",), ("tree_nut",), "vegan"),
    "oat_milk": ("Oat milk", ("milk",), ("gluten",), "vegan"),
    "rice_milk": ("Rice milk", ("milk",), (), "vegan"),
    "coconut_milk": ("Coconut milk", ("milk", "cream"), ("tree_nut",), "vegan"),
    "curd": ("Curd", ("curd",), ("dairy",), "veg"),
    "yogurt": ("Yogurt", ("curd",), ("dairy",), "veg"),
    "soy_yogurt": ("Soy yogurt", ("curd",), ("soy",), "vegan"),
    "buttermilk": ("Buttermilk", ("curd",), ("dairy",), "veg"),
    "cream": ("Fresh cream", ("cream",), ("dairy",), "veg"),
    "coconut_cream": ("Coconut cream", ("cream",), ("tree_nut",), "vegan"),
    "cashew": ("Cashews", ("cream", "nut_seed"), ("tree_nut",), "vegan"),
    "cheese": ("Cheese", ("cheese",), ("dairy",), "veg"),
    "nutritional_yeast": ("Nutritional yeast", ("cheese",), (), "vegan"),
    # fats
    "ghee": ("Ghee", ("fat",), ("dairy",), "veg"),
    "butter": ("Butter", ("fat",), ("dairy",), "veg"),
    "sunflower_oil": ("Sunflower oil", ("fat",), (), "vegan"),
    "mustard_oil": ("Mustard oil", ("fat",), (), "vegan"),
    "rice_bran_oil": ("Rice bran oil", ("fat",), (), "vegan"),
    "olive_oil": ("Olive oil", ("fat",), (), "vegan"),
    "canola_oil": ("Canola oil", ("fat",), (), "vegan"),
    "peanut_oil": ("Groundnut oil", ("fat",), ("peanut",), "vegan"),
    "coconut_oil": ("Coconut oil", ("fat",), ("tree_nut",), "vegan"),
    "sesame_oil": ("Gingelly (sesame) oil", ("fat",), ("sesame",), "vegan"),
    "vegetable_oil": ("Refined vegetable oil", ("fat",), ("soy",), "vegan"),   # often soybean oil
    # vegetables and greens
    "onion": ("Onion", ("vegetable",), (), "vegan"),
    "tomato": ("Tomato", ("vegetable",), (), "vegan"),
    "potato": ("Potato", ("vegetable",), (), "vegan"),
    "sweet_potato": ("Sweet potato", ("vegetable",), (), "vegan"),
    "carrot": ("Carrot", ("vegetable",), (), "vegan"),
    "cauliflower": ("Cauliflower", ("vegetable",), (), "vegan"),
    "cabbage": ("Cabbage", ("vegetable",), (), "vegan"),
    "okra": ("Bhindi (okra)", ("vegetable",), (), "vegan"),
    "eggplant": ("Brinjal", ("vegetable",), (), "vegan"),
    "bottle_gourd": ("Lauki (bottle gourd)", ("vegetable",), (), "vegan"),
    "bitter_melon": ("Karela (bitter gourd)", ("vegetable",), (), "vegan"),
    "snake_gourd": ("Snake gourd", ("vegetable",), (), "vegan"),
    "pumpkin": ("Pumpkin", ("vegetable",), (), "vegan"),
    "zucchini": ("Zucchini", ("vegetable",), (), "vegan"),
    "bell_pepper": ("Capsicum", ("vegetable",), (), "vegan"),
    "beet": ("Beetroot", ("vegetable",), (), "vegan"),
    "radish": ("Mooli (radish)", ("vegetable",), (), "vegan"),
    "green_bean": ("Beans", ("vegetable",), (), "vegan"),
    "mixed_vegetable": ("Mixed vegetables", ("vegetable",), (), "vegan"),
    "cucumber": ("Cucumber", ("vegetable",), (), "vegan"),
    "plantain": ("Raw banana", ("vegetable",), (), "vegan"),
    "yam": ("Yam (suran)", ("vegetable",), (), "vegan"),
    "taro": ("Arbi (taro)", ("vegetable",), (), "vegan"),
    "broccoli": ("Broccoli", ("vegetable",), (), "vegan"),
    "corn": ("Sweet corn", ("vegetable",), (), "vegan"),
    "spinach": ("Spinach (palak)", ("greens",), (), "vegan"),
    "mustard_green": ("Sarson (mustard greens)", ("greens",), (), "vegan"),
    "fenugreek_leaf": ("Methi leaves", ("greens",), (), "vegan"),
    "moringa": ("Drumstick leaves", ("greens",), (), "vegan"),
    "kale": ("Kale", ("greens",), (), "vegan"),
    # aromatics and spices
    "garlic": ("Garlic", ("aromatic",), (), "vegan"),
    "ginger": ("Ginger", ("aromatic",), (), "vegan"),
    "green_chili": ("Green chilli", ("aromatic",), (), "vegan"),
    "curry_leaf": ("Curry leaves", ("aromatic",), (), "vegan"),
    "coriander": ("Coriander", ("aromatic",), (), "vegan"),
    "mint": ("Mint", ("aromatic",), (), "vegan"),
    "shallot": ("Small onions (shallots)", ("aromatic", "vegetable"), (), "vegan"),
    "scallion": ("Spring onion", ("aromatic",), (), "vegan"),
    "turmeric": ("Turmeric", ("spice",), (), "vegan"),
    "cumin": ("Jeera (cumin)", ("spice",), (), "vegan"),
    "mustard_seed": ("Mustard seeds", ("spice",), (), "vegan"),
    "fenugreek_seed": ("Methi seeds", ("spice",), (), "vegan"),
    "black_pepper": ("Black pepper", ("spice",), (), "vegan"),
    "cardamom": ("Cardamom", ("spice",), (), "vegan"),
    "cinnamon": ("Cinnamon", ("spice",), (), "vegan"),
    "clove": ("Cloves", ("spice",), (), "vegan"),
    "fennel_seed": ("Saunf (fennel)", ("spice",), (), "vegan"),
    "ajwain": ("Ajwain", ("spice",), (), "vegan"),
    "nigella_seed": ("Kalonji (nigella)", ("spice",), (), "vegan"),
    "chili_powder": ("Chilli powder", ("spice",), (), "vegan"),
    "kashmiri_chili": ("Kashmiri chilli", ("spice",), (), "vegan"),
    "garam_masala": ("Garam masala", ("spice",), (), "vegan"),
    "asafoetida": ("Hing (asafoetida)", ("spice",), ("gluten",), "vegan"),      # compounded with wheat flour
    "sambar_powder": ("Sambar powder", ("spice",), ("gluten",), "vegan"),        # usually contains hing
    "chaat_masala": ("Chaat masala", ("spice",), ("gluten",), "vegan"),          # usually contains hing
    # sour and sweet
    "tamarind": ("Tamarind", ("sour",), (), "vegan"),
    "kokum": ("Kokum", ("sour",), (), "vegan"),
    "amchur": ("Amchur (dry mango)", ("sour",), (), "vegan"),
    "lemon": ("Lemon", ("sour",), (), "vegan"),
    "lime": ("Lime", ("sour",), (), "vegan"),
    "vinegar": ("Vinegar", ("sour",), (), "vegan"),
    "jaggery": ("Jaggery", ("sweet",), (), "vegan"),
    "sugar": ("Sugar", ("sweet",), (), "vegan"),
    "brown_sugar": ("Brown sugar", ("sweet",), (), "vegan"),
    "date": ("Dates", ("sweet",), (), "vegan"),
    "honey": ("Honey", ("sweet",), (), "veg"),
    "palm_sugar": ("Palm sugar (karupatti)", ("sweet",), (), "vegan"),
    # nuts and seeds
    "peanut": ("Peanuts", ("nut_seed",), ("peanut",), "vegan"),
    "almond": ("Almonds", ("nut_seed",), ("tree_nut",), "vegan"),
    "walnut": ("Walnuts", ("nut_seed",), ("tree_nut",), "vegan"),
    "pistachio": ("Pistachios", ("nut_seed",), ("tree_nut",), "vegan"),
    "coconut": ("Coconut", ("nut_seed",), ("tree_nut",), "vegan"),   # conservative: treated as a tree nut
    "sesame_seed": ("Sesame seeds", ("nut_seed",), ("sesame",), "vegan"),
    "sunflower_seed": ("Sunflower seeds", ("nut_seed",), (), "vegan"),
    "pumpkin_seed": ("Pumpkin seeds", ("nut_seed",), (), "vegan"),
    "flaxseed": ("Flax seeds", ("nut_seed",), (), "vegan"),
    "chia_seed": ("Chia seeds", ("nut_seed",), (), "vegan"),
    "melon_seed": ("Melon seeds (magaz)", ("nut_seed",), (), "vegan"),
    "poppy_seed": ("Poppy seeds (khus khus)", ("nut_seed",), (), "vegan"),
}

# Defence in depth: whole words in a token name that put it in an allergen family even
# if its PANTRY row forgot. Matching is per underscore-separated word.
FAMILY_WORDS = {
    "dairy": {"milk", "cheese", "cream", "butter", "yogurt", "curd", "ghee", "paneer", "whey", "khoya",
              "buttermilk", "kefir", "custard", "lassi"},
    "gluten": {"wheat", "flour", "atta", "maida", "semolina", "rava", "barley", "rye", "bulgur", "couscous",
               "vermicelli", "noodle", "pasta", "bread", "roti", "oat", "malt", "seitan", "asafoetida",
               "sambar", "chaat"},
    "egg": {"egg", "mayonnaise", "noodle", "pasta"},
    "soy": {"soy", "soya", "soybean", "tofu", "tempeh", "edamame", "miso"},
    "shellfish": {"shrimp", "prawn", "crab", "lobster", "squid", "clam", "mussel", "oyster", "scallop"},
    "fish": {"fish", "sardine", "mackerel", "pomfret", "tuna", "salmon", "anchovy"},
    "sesame": {"sesame", "tahini", "gingelly"},
    "tree_nut": {"almond", "cashew", "walnut", "pistachio", "hazelnut", "pecan", "coconut", "nut"},
    "peanut": {"peanut", "groundnut"},
}
# Reviewed exceptions to the keyword guard: plant milks are not dairy, rice noodles have
# neither wheat nor egg. Each is still subject to its own explicit labels above.
GUARD_EXCEPTIONS = {("soy_milk", "dairy"), ("almond_milk", "dairy"), ("oat_milk", "dairy"), ("rice_milk", "dairy"),
                    ("coconut_milk", "dairy"), ("coconut_cream", "dairy"), ("soy_yogurt", "dairy"),
                    ("rice_noodle", "gluten"), ("rice_noodle", "egg")}
_OIL_SOURCES = {"plant_based_milk", "plant_based_cream", "plant_based_cheese"}     # unknown base: never suggested
NONVEG_WORDS = {"chicken", "mutton", "lamb", "goat", "beef", "pork", "fish", "sardine", "mackerel", "pomfret",
                "tuna", "shrimp", "prawn", "crab", "squid", "egg", "meat", "bacon", "ham", "sausage", "gelatin"}


def words(token: str) -> set[str]:
    return set(re.split(r"[_\s]+", token.lower()))


def families(token: str) -> set[str]:
    """Allergen families of an ingredient: its explicit labels plus the keyword guard."""
    out = set(PANTRY[token][2]) if token in PANTRY else set()
    w = words(token)
    for fam, kw in FAMILY_WORDS.items():
        if w & kw and (token, fam) not in GUARD_EXCEPTIONS:
            out.add(fam)
    return out


def diet_class(token: str) -> str:
    if token in PANTRY:
        cls = PANTRY[token][3]
    else:
        cls = "vegan"
    w = words(token)
    if w & NONVEG_WORDS and cls in ("vegan", "veg"):
        cls = "egg" if w & {"egg"} else "nonveg"
    if cls == "vegan" and families(token) & {"dairy"}:
        cls = "veg"
    return cls


def name(token: str) -> str:
    return PANTRY[token][0] if token in PANTRY else token.replace("_", " ").capitalize()


def roles(token: str) -> tuple[str, ...]:
    return PANTRY[token][1] if token in PANTRY else ()


def people_of(user: dict) -> list[dict]:
    """Everyone a shared kitchen cooks for: the user and their household members."""
    return [user, *(user.get("household_members") or [])]


def unsafe_for(people: list[dict], token: str) -> str | None:
    """Why `token` cannot be suggested to this group, or None if it is safe for all."""
    if token in _OIL_SOURCES or token not in PANTRY:
        return "not a known pantry ingredient"
    fam = families(token)
    cls = diet_class(token)
    for p in people:
        who = p.get("name") or "someone"
        clash = fam & set(p.get("allergens") or [])
        if "celiac" in (p.get("medical") or []) and "gluten" in fam:
            clash.add("gluten")
        if clash:
            return f"{who}: {', '.join(sorted(clash))}"
        diet = p.get("diet", "nonveg")
        if cls not in DIET_ALLOWS.get(diet, DIET_ALLOWS["nonveg"]):
            return f"{who}: not {diet}"
    return None


def substitutes(token: str, people: list[dict], k: int = 3, exclude=()) -> dict:
    """Up to `k` pantry swaps for `token` that do the same job and are safe for everyone.

    Ranked by Epicure similarity; without the model, nothing is suggested (the caller
    hides the feature) rather than an unranked guess."""
    model = epicure.get()
    if token not in PANTRY:
        return {"available": model is not None, "ingredient": token, "name": name(token), "options": [],
                "hidden_unsafe": 0, "reason": "Ziggy has no swaps for this item yet"}
    if model is None:
        return {"available": False, "ingredient": token, "name": name(token), "options": [], "hidden_unsafe": 0}
    want = set(roles(token))
    # a swap never turns a vegetarian dish non-vegetarian (paneer → tofu, never → mutton);
    # dairy may replace a plant ingredient (oil → ghee) when everyone eating has dairy
    ceiling = max(DIET_RANK[diet_class(token)], DIET_RANK["veg"])
    pool = [t for t, row in PANTRY.items() if t != token and t not in exclude and want & set(row[1])
            and DIET_RANK[diet_class(t)] <= ceiling]
    safe = [t for t in pool if unsafe_for(people, t) is None]
    ranked = model.rank(model.vec(token), safe) if model.has(token) else []
    return {"available": True, "ingredient": token, "name": name(token),
            "options": [{"token": t, "name": name(t), "similarity": round(s, 3)} for t, s in ranked[:k]],
            "hidden_unsafe": len(pool) - len(safe)}


def make_safe(recipe: dict, people: list[dict]) -> dict | None:
    """A recipe that breaks someone's allergy or diet, made safe by swapping only the
    offending ingredients — or None if any of them has no safe swap."""
    swaps = []
    used = {i["token"] for i in recipe.get("ingredients", [])}
    for ing in recipe.get("ingredients", []):
        if unsafe_for(people, ing["token"]) is None:
            continue
        opts = substitutes(ing["token"], people, k=1, exclude=used)["options"]
        if not opts:
            return None
        used.add(opts[0]["token"])
        swaps.append({"from": ing["token"], "from_name": ing["name"], "to": opts[0]["token"], "to_name": opts[0]["name"],
                      "why": unsafe_for(people, ing["token"])})
    return {"key": recipe["key"], "name": recipe["name"], "swaps": swaps} if swaps else None
