"""Dish flavour from Epicure: "more like this" and a cuisine tilt.

A dish's flavour vector is the unit mean of the Epicure vectors of the ingredients its
name implies ("Chicken Chettinad + Rice" → chicken, black pepper, fennel, curry leaf,
coconut, Kashmiri chilli, rice). Dishes whose names say nothing we recognise get no
vector and therefore no flavour signal at all, rather than a guess.

Both signals are small taste bonuses inside the planner's objective. They never touch
the hard rules (allergens, diet, budget, rating floor), and with no "more like this"
taps and no tilt set they are exactly zero, so plans are unchanged.
"""
from __future__ import annotations

import re

import numpy as np

from . import epicure

# Words in a dish name → the ingredients they imply (Epicure tokens).
DISH_WORDS: dict[str, tuple[str, ...]] = {
    "biryani": ("basmati_rice", "biryani_masala", "onion", "yogurt", "mint"),
    "pulao": ("basmati_rice", "garam_masala"), "rice": ("rice",), "chawal": ("rice",),
    "meals": ("rice", "toor_dal", "sambar_powder", "curd"), "thali": ("rice", "toor_dal", "roti", "curd"),
    "tiffin": ("idli", "urad_dal", "chutney"), "curd": ("curd",), "khichdi": ("rice", "mung_bean", "ghee"),
    "dosa": ("rice", "urad_dal"), "idli": ("idli", "urad_dal"), "sambar": ("toor_dal", "sambar_powder", "tamarind"),
    "vada": ("urad_dal", "curry_leaf", "black_pepper"), "pongal": ("rice", "mung_bean", "black_pepper", "cumin"),
    "podi": ("chana_dal", "urad_dal", "kashmiri_chili"), "pesarattu": ("mung_bean", "ginger", "green_chili"),
    "chutney": ("chutney",), "upma": ("semolina", "curry_leaf", "mustard_seed"), "poha": ("flattened_young_rice",),
    "kesari": ("semolina", "sugar", "ghee", "saffron"), "chikki": ("peanut", "jaggery"),
    "parotta": ("flatbread", "flour"), "paratha": ("paratha",), "roti": ("roti",), "naan": ("naan",),
    "kurma": ("coconut", "cashew", "mixed_vegetable", "garam_masala"), "dal": ("toor_dal", "cumin"),
    "rajma": ("kidney_bean", "tomato", "onion"), "chana": ("chickpea", "garam_masala"), "chole": ("chickpea",),
    "paneer": ("paneer",), "tikka": ("tikka_masala", "yogurt"), "tandoori": ("tandoori_masala", "yogurt"),
    "butter": ("butter",), "ghee": ("ghee",), "masala": ("garam_masala",), "curry": ("onion", "tomato", "turmeric"),
    "chettinad": ("black_pepper", "fennel_seed", "curry_leaf", "coconut", "kashmiri_chili"),
    "chicken": ("chicken",), "mutton": ("mutton",), "egg": ("egg",), "fish": ("fish",), "prawn": ("shrimp",),
    "veg": ("mixed_vegetable",), "salad": ("lettuce", "cucumber", "tomato", "olive_oil"), "grilled": ("black_pepper",),
    "quinoa": ("quinoa",), "buddha": ("chickpea", "avocado"), "roll": ("flatbread", "onion"), "kathi": ("flatbread",),
    "wrap": ("tortilla",), "sandwich": ("bread", "cheese", "cucumber", "tomato"), "toast": ("bread",),
    "peanut": ("peanut_butter",), "bun": ("bread",), "puff": ("puff_pastry",), "muffin": ("muffin",),
    "coffee": ("coffee",), "cappuccino": ("coffee", "milk"), "brew": ("coffee",),
    "chai": ("black_tea", "milk", "cardamom", "ginger"),
    "noodles": ("noodle", "soy_sauce"), "hakka": ("noodle", "soy_sauce", "cabbage"),
    "fried": ("vegetable_oil",), "manchurian": ("cabbage", "soy_sauce", "cornstarch", "ginger"),
    "schezwan": ("sichuan_peppercorn", "chili_paste", "garlic"), "momo": ("dumpling", "cabbage", "ginger"),
    "momos": ("dumpling", "cabbage", "ginger"), "pizza": ("pizza_crust", "mozzarella_cheese", "tomato", "oregano"),
    "pasta": ("pasta", "tomato", "garlic"), "burger": ("bread", "onion", "tomato"),
    "shawarma": ("chicken", "garlic", "flatbread"), "kebab": ("lamb", "garam_masala"), "falafel": ("falafel",),
    "hummus": ("hummus",), "sushi": ("rice", "nori", "rice_vinegar"), "ramen": ("ramen_noodle",),
    "thai": ("lemongrass", "galangal", "coconut_milk"), "laksa": ("laksa_paste", "coconut_milk"),
    "taco": ("corn_tortilla", "salsa"), "burrito": ("flour_tortilla", "black_bean", "rice"),
}
CUISINES = {   # the cuisine poles offered as a tilt, with the words people use for them
    "South_Asian": "Indian", "East_Asian": "East Asian (Chinese, Japanese, Korean)",
    "Mediterranean": "Mediterranean", "Latin_American": "Latin American",
}   # Southeast_Asian is left out: its pole is a single coconut-dessert mode, too thin to steer meals
TILT_DEGREES = {"light": 20.0, "strong": 40.0}
TILT_W = 0.5          # bonus per unit of cosine gained toward the tilted taste
SIMILAR_W = 0.12      # at most the size of a 👍 (taste.LIKE_BONUS)
SIMILAR_FLOOR = 0.6   # below this cosine a dish is not "like" the one asked for
MORE_LIKE_MAX = 5


def dish_tokens(dish_name: str) -> list[str]:
    out = []
    for w in re.findall(r"[a-z]+", (dish_name or "").lower()):
        for t in DISH_WORDS.get(w, ()):
            if t not in out:
                out.append(t)
    return out


def dish_vector(dish_name: str, model=None) -> np.ndarray | None:
    model = model or epicure.get()
    if model is None:
        return None
    return model.mean(dish_tokens(dish_name))


def similarity(a: str, b: str) -> float | None:
    model = epicure.get()
    va, vb = dish_vector(a, model), dish_vector(b, model)
    if va is None or vb is None:
        return None
    return model.cos(va, vb)


def context(user: dict, menu: list[dict]) -> dict:
    """Everything the planner needs, computed once per solve. Empty when off."""
    prefs = user.get("prefs") or {}
    more_like = prefs.get("more_like") or []
    tilt = prefs.get("cuisine_tilt") or None
    model = epicure.get() if (more_like or tilt) else None
    if model is None:
        return {}
    out = {"model": model, "vec": {}}
    for it in menu:
        v = dish_vector(it["name"], model)
        if v is not None:
            out["vec"][it["name"]] = v
    likes = []
    for m in more_like:
        v = dish_vector(m["name"], model)
        if v is not None:
            likes.append((m["name"], v))
    out["likes"] = likes
    pole = model.cuisine_poles.get(tilt["cuisine"]) if tilt and tilt.get("cuisine") in CUISINES else None
    if pole is not None and out["vec"]:
        stack = np.stack(list(out["vec"].values()))
        base = stack.mean(axis=0)
        base = base / max(float(np.linalg.norm(base)), 1e-9)
        q = model.slerp(base, pole, TILT_DEGREES[tilt.get("strength", "light")])
        # a dish's pull toward the tilted taste, relative to the menu as a whole: rotating
        # the menu's centre toward the pole moves away from most dishes, so what counts is
        # losing less than the average dish does.
        gains = {n: model.cos(v, q) - model.cos(v, base) for n, v in out["vec"].items()}
        mean_gain = float(np.mean(list(gains.values())))
        # only dishes in the top quarter by closeness to the pole are nudged, so every
        # nudged dish can honestly say it leans that way
        # and whose nearest offered cuisine is this one (a dosa is not "East Asian" just
        # because an East Asian tilt ranks it highest on a Chennai menu)
        near = {n: model.cos(v, pole) for n, v in out["vec"].items()}
        cut = float(np.quantile(list(near.values()), 0.75))
        poles = {c: model.cuisine_poles[c] for c in CUISINES if c in model.cuisine_poles}
        own = {n for n, v in out["vec"].items()
               if max(poles, key=lambda c: model.cos(v, poles[c])) == tilt["cuisine"]}
        out["tilt"] = {"cuisine": tilt["cuisine"], "label": CUISINES.get(tilt["cuisine"], tilt["cuisine"]),
                       "gain": {n: g - mean_gain for n, g in gains.items() if near[n] >= cut and n in own}}
    return out


def bonus(fctx: dict, dish_name: str) -> tuple[float, list[str]]:
    """(taste bonus, reason lines) for one delivery dish."""
    if not fctx:
        return 0.0, []
    v = fctx["vec"].get(dish_name)
    if v is None:
        return 0.0, []
    model, total, why = fctx["model"], 0.0, []
    best = max(((model.cos(v, lv), n) for n, lv in fctx.get("likes", []) if n != dish_name), default=None)
    if best and best[0] >= SIMILAR_FLOOR:
        total += SIMILAR_W * (best[0] - SIMILAR_FLOOR) / (1 - SIMILAR_FLOOR)
        why.append(f"Similar to {best[1]}, which you asked for more of.")
    t = fctx.get("tilt")
    if t:
        gain = t["gain"].get(dish_name, 0.0)
        if gain > 0:
            total += TILT_W * gain
            why.append(f"Leans {t['label']}, as you asked.")
    return round(total, 4), why


def similar_items(dish_name: str, items: list[dict], k: int = 3, exclude_ids=()) -> list[dict]:
    """The `k` menu items closest in flavour to `dish_name` (only those clearly alike)."""
    model = epicure.get()
    q = dish_vector(dish_name, model) if model else None
    if q is None:
        return []
    scored = []
    for it in items:
        if it["id"] in exclude_ids or it["name"] == dish_name:
            continue
        v = dish_vector(it["name"], model)
        if v is not None:
            s = model.cos(q, v)
            if s >= SIMILAR_FLOOR:
                scored.append((s, it))
    scored.sort(key=lambda x: (-x[0], x[1]["id"]))
    return [{**it, "similarity": round(s, 3)} for s, it in scored[:k]]
