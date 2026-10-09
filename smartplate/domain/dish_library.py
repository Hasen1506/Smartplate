"""Type a dish, get its ingredients: the cooking side of Ziggy.

Two sources, both credited on every dish:
- the 6000+ Indian Food Recipes Dataset (integrations/indian_recipes.py): dish names,
  ingredient lists, times, servings and a link to the original method;
- the Wikibooks Cookbook (integrations/wikibooks_recipes.py, CC BY-SA 4.0), when its
  snapshot exists: these carry their steps too.

Dishes that break the person's diet or an allergy (read from the ingredient words) are
never shown; the count of those is. A dish chosen for a meal is planned as a home-cooked
meal with its groceries not priced and no nutrition estimate: neither source gives them,
and Ziggy doesn't make them up.
"""
from __future__ import annotations

import gzip
import json
import os
import re
from fractions import Fraction

from ..integrations.indian_recipes import DATA_FILE, SOURCE

_CACHE = {"mtime": None, "data": None, "index": None}
LIMIT = 20


def _load() -> dict | None:
    try:
        mtime = os.path.getmtime(DATA_FILE)
    except OSError:
        return None
    if _CACHE["mtime"] != mtime:
        with gzip.open(DATA_FILE, "rt", encoding="utf-8") as f:
            _CACHE["data"], _CACHE["mtime"] = json.load(f), mtime
        _CACHE["index"] = None
    return _CACHE["data"]


def _wikibooks() -> list[dict]:
    from . import recipe_library
    snap = recipe_library.snapshot()
    out = []
    for r in (snap or {}).get("recipes", []):
        out.append({**r, "source": "wikibooks", "meals": [], "about": None,
                    "credit": f"Wikibooks contributors, {r.get('license') or 'CC BY-SA 4.0'}"})
    return out


def _all() -> list[dict]:
    snap = _load()
    dishes = [{**d, "source": "indian6000"} for d in (snap or {}).get("dishes", [])]
    return dishes + _wikibooks()


def _index() -> dict:
    if _CACHE["index"] is None or _CACHE["index"][0] != _CACHE["mtime"]:
        _CACHE["index"] = (_CACHE["mtime"], {d["key"]: d for d in _all()})
    return _CACHE["index"][1]


def available() -> bool:
    return bool(_index())


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", (s or "").lower()).strip()


SWEET = re.compile(r"\b(sugar|jaggery|gur|condensed milk|honey|syrup|khoa|khoya|mawa)\b", re.I)


def sweet(d: dict) -> bool:
    return (d.get("course") or "").lower() == "dessert" or bool(SWEET.search(" ".join(d.get("ingredients") or [])))


def blocked(user: dict, d: dict) -> str | None:
    """Why a dish is not for this person (or someone eating with them), else None: the
    same hard rules as a delivery dish (allergens.violates), plus vegan read from the
    words and, for diabetes, no sweet dish (the library has no sugar figures)."""
    from . import allergens
    item = {"veg": 1 if d.get("veg") else 0, "allergens": d.get("allergens") or [], "tags": []}
    reason = allergens.violates(user, item)
    if reason:
        return reason
    people = [user] + list(user.get("household_members") or [])
    if any(p.get("diet") == "vegan" for p in people) and not d.get("vegan"):
        return "not vegan"
    if any("diabetes" in (p.get("medical") or []) for p in people) and sweet(d):
        return "sweet dish (diabetes)"
    return None


def _score(d: dict, q: str, words: list[str]) -> int | None:
    t = _norm(d["title"])
    if t == q:
        return 0
    if t.startswith(q):
        return 1
    tw = t.split()
    if all(any(w2.startswith(w) for w2 in tw) for w in words):
        return 2
    if q in t:
        return 3
    if len(q) >= 4 and all(any(w in _norm(i) for i in d["ingredients"]) for w in words):
        return 5                                        # "paneer peas": dishes made with both
    return None


def card(d: dict) -> dict:
    """What a search result shows (no ingredient list yet)."""
    return {"key": d["key"], "title": d["title"], "about": d.get("about"), "cuisine": d.get("cuisine"),
            "course": d.get("course"), "total_min": d.get("total_min"), "servings": d.get("servings"),
            "veg": d.get("veg"), "has_steps": bool(d.get("steps")), "source": d.get("source"),
            "ingredient_count": len(d.get("ingredients") or [])}


def search(user: dict, query: str, meal: str | None = None, limit: int = LIMIT) -> dict:
    idx = _index()
    if not idx:
        return {"available": False, "dishes": [], "hidden": 0, "total": 0}
    q = _norm(query)
    words = q.split()
    hits, hidden = [], 0
    for d in idx.values():
        if q:
            s = _score(d, q, words)
            if s is None:
                continue
        else:
            if not meal or meal not in (d.get("meals") or []):
                continue
            s = 4
        if blocked(user, d):
            hidden += 1
            continue
        fits_meal = bool(meal and meal in (d.get("meals") or []))
        hits.append((s, 0 if fits_meal else 1, d.get("total_min") or 999, d["title"].lower(), d))
    hits.sort(key=lambda h: h[:4])
    return {"available": True, "total": len(hits), "hidden": hidden,
            "dishes": [card(h[4]) for h in hits[:limit]], "credits": credits()}


def credits() -> list[dict]:
    out = []
    if _load():
        out.append({"name": SOURCE["name"], "by": SOURCE["author"], "license": SOURCE["license"],
                    "url": SOURCE["url"], "license_url": SOURCE["license_url"],
                    "origin": SOURCE["origin"], "origin_url": SOURCE["origin_url"]})
    if _wikibooks():
        out.append({"name": "Wikibooks Cookbook", "by": "Wikibooks contributors", "license": "CC BY-SA 4.0",
                    "url": "https://en.wikibooks.org/wiki/Cookbook:Table_of_Contents",
                    "license_url": "https://creativecommons.org/licenses/by-sa/4.0/"})
    return out


# --------------------------------------------------------------------------- #
# Scaling an ingredient line to how many people eat
# --------------------------------------------------------------------------- #
QTY = re.compile(r"^\s*(\d+\s*-\s*\d+/\d+|\d+\s+\d+/\d+|\d+/\d+|\d+(?:\.\d+)?)(?=\s|[a-zA-Z]|$)")


def _parse(q: str) -> Fraction:
    q = q.replace(" ", "")
    if "-" in q:                                    # "1-1/2" is one and a half in this dataset
        whole, frac = q.split("-", 1)
        return Fraction(int(whole)) + Fraction(frac)
    return Fraction(q) if "/" in q or "." not in q else Fraction(q).limit_denominator(8)


def _show(x: Fraction) -> str:
    x = x.limit_denominator(4) if x.denominator > 4 else x
    whole, rest = divmod(x.numerator, x.denominator)
    if rest == 0:
        return str(whole)
    frac = f"{rest}/{x.denominator}"
    return f"{whole} {frac}" if whole else frac


def scale_line(line: str, factor: Fraction) -> str:
    """"1 1/2 cups rice" × 2 → "3 cups rice". Lines without a leading amount ("Salt - to taste") stay."""
    m = QTY.match(line)
    if not m or factor == 1:
        return line
    try:
        amount = _parse(m.group(1)) * factor
    except (ValueError, ZeroDivisionError):
        return line
    return _show(amount) + line[m.end(1):]


def ingredient_name(line: str) -> str:
    """The shoppable part of a line: "1 cup Rice - soaked for 20 minutes" → "Rice"."""
    s = QTY.sub("", line).strip()
    s = re.sub(r"\s+-\s+.*$", "", s)                       # " - soaked …", " - to taste"
    s = re.sub(r"\([^)]*\)", "", s)
    s = re.sub(r"^(cups?|tablespoons?|teaspoons?|tbsp|tsp|grams?|g|kg|ml|litres?|liters?|pinch|sprigs?|"
               r"inch|inches|cloves?|pieces?|small|medium|large|big|handful|bunch|of)\b\.?\s*", "", s, flags=re.I)
    s = re.sub(r"^(cups?|tablespoons?|teaspoons?|of)\b\s*", "", s, flags=re.I)
    return re.sub(r"\s+", " ", s).strip(" ,.-") or line


def get(user: dict, key: str, people: int | None = None) -> dict | None:
    d = _index().get(key)
    if not d:
        return None
    why = blocked(user, d)
    if why:
        raise ValueError(f"This dish isn't safe for you or someone you cook for ({why}), so Ziggy doesn't show it.")
    serves = d.get("servings") or None
    people = max(1, min(20, int(people or serves or 1)))
    factor = Fraction(people, serves) if serves else Fraction(1)
    return {**card(d), "url": d.get("url"), "diet": d.get("diet"), "allergens": d.get("allergens") or [],
            "prep_min": d.get("prep_min"), "cook_min": d.get("cook_min"), "meals": d.get("meals") or [],
            "people": people, "scaled": bool(serves) and factor != 1,
            "ingredients": [{"line": scale_line(i, factor), "name": ingredient_name(i)} for i in d["ingredients"]],
            "steps": d.get("steps") or [],
            "credit": d.get("credit") or (f"{SOURCE['name']} ({SOURCE['author']}, {SOURCE['license']}), "
                                          f"from {SOURCE['origin']}")}


def raw(key: str) -> dict | None:
    return _index().get(key)


def as_recipe(key: str) -> dict | None:
    """A library dish in the planner's recipe shape: a home-cooked meal with groceries not
    priced (cost 0, cost_unknown) and no nutrition estimate."""
    d = _index().get(key)
    if not d:
        return None
    return {"key": d["key"], "name": d["title"], "cost": 0, "cost_unknown": True, "nutrition_unknown": True,
            "veg": 1 if d.get("veg") else 0, "vegan": bool(d.get("vegan")), "allergens": d.get("allergens") or [],
            "tags": ["library"], "carbon_kg": 0.0, "basket": [],
            "ingredients": [], "library_ingredients": [ingredient_name(i) for i in d["ingredients"]],
            "steps": d.get("steps") or [], "url": d.get("url"), "source": d.get("source")}
