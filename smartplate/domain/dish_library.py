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
from functools import lru_cache

from ..integrations.indian_recipes import DATA_FILE, SOURCE

_CACHE = {"mtime": None, "data": None, "index": None}
LIMIT = 20
HIDDEN_SHOWN = 30          # hidden dishes listed with their reason ("why is this hidden?")


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


def facts(d: dict) -> dict:
    """A library dish as the shared safety checker sees it (allergens.check): its stored
    marks plus whatever the food lexicon reads in its title and ingredient words
    (Tamil, Hindi and English), which can only add a restriction."""
    from . import food_lexicon
    words = food_lexicon.read_all(d.get("ingredients") or [], d.get("title") or "")
    veg = bool(d.get("veg")) and not words.nonveg
    return {"name": d.get("title") or "", "veg": 1 if veg else 0, "veg_known": 1,
            "vegan": bool(d.get("vegan")) and veg and "dairy" not in words.allergens,
            "allergens": sorted(set(d.get("allergens") or []) | words.allergens), "tags": [],
            "safety_basis": "ingredients", "sweet": sweet(d) or bool(words.sweet), "nutrition_known": 0,
            "sugar_g": None}


def blocked(user: dict, d: dict) -> str | None:
    """Why a dish is not for this person (or someone eating with them), else None: the
    same checker as a delivery dish (allergens.check), on the dish's ingredient words.
    For diabetes a sweet dish is out (the library has no sugar figures)."""
    from . import allergens
    return allergens.violates(user, facts(d))


def blocked_verdict(user: dict, d: dict) -> dict:
    from . import allergens
    return allergens.check(user, facts(d))


# --------------------------------------------------------------------------- #
# Reading a restaurant dish's name against the library (sp-grill #37–#39)
# --------------------------------------------------------------------------- #
# Words that say nothing about what's in a dish, dropped before matching names.
_STOP = set("""recipe recipes style homemade home made easy quick simple healthy special spl the a an and with of in
on for how to make restaurant hotel dhaba street south north indian india tamil nadu kerala andhra telangana
karnataka chettinad chettinadu hyderabadi mughlai punjabi bengali gujarati maharashtrian udupi mangalorean
malabar madurai kongu kongunadu traditional authentic classic delicious tasty spicy mild fresh hot new best
style combo pack portion plate full half small large regular family pcs pc piece pieces nos no ml gm g kg
serves single double""".split())


def _core(text: str) -> tuple[str, ...]:
    words = [w for w in _norm(text).split() if w not in _STOP and not w.isdigit()]
    return tuple(dict.fromkeys(words))


# One word that names a kind of dish, not a dish: never enough to match a recipe alone.
_GENERIC = set("""rice curry fry gravy masala soup meals meal thali bowl salad roll wrap sandwich starter
starters snack snacks sweet sweets dessert drink juice tea coffee chutney sambar rasam kuzhambu poriyal
kootu dry roast plain veg vegetable vegetables mix mixed""".split())


def _name_index() -> dict:
    """({core words of a recipe name: [dish keys]}, {word: {core}}), built once per data file."""
    key = ("names", _CACHE["mtime"])
    if _CACHE.get("names_for") != key:
        idx: dict[frozenset, list[str]] = {}
        for d in _index().values():
            for part in re.split(r"\s[-|]\s|\(|\)|/", d.get("title") or ""):
                core = frozenset(_core(part))
                if core:
                    idx.setdefault(core, []).append(d["key"])
        by_word: dict[str, set] = {}
        for core in idx:
            for w in core:
                by_word.setdefault(w, set()).add(core)
        _CACHE["names"], _CACHE["names_for"] = (idx, by_word), key
    return _CACHE["names"]


def infer(name: str, limit: int = 12) -> dict | None:
    """Cached `_infer` (menus repeat names; the data file never changes while running)."""
    return _infer_cached(name, limit, _CACHE["mtime"] if _load() else None)


@lru_cache(maxsize=4096)
def _infer_cached(name: str, limit: int, _mtime) -> dict | None:
    return _infer(name, limit)


def _infer(name: str, limit: int = 12) -> dict | None:
    """What the recipe library says a restaurant dish called `name` is likely made of
    (its dish category, sp-grill #37 answer): the union of every matching recipe's
    allergens, and whether any is non-veg or sweet. A recipe matches when all its name
    words are in the dish name (two words or more, or the whole name); failing that,
    the closest recipes whose names contain every word of the dish name (not for a
    lone generic word like "rice"). None when nothing matches. Only ever used to ADD
    restrictions, never to call a dish safe."""
    if not name or not _index():
        return None
    words = _core(name)[:8]
    if not words:
        return None
    idx, by_word = _name_index()
    keys: list[str] = []
    from itertools import combinations
    for k in range(len(words), 0, -1):
        if k == 1 and len(words) > 1:
            break
        for combo in combinations(words, k):
            keys += idx.get(frozenset(combo), [])
        if len(keys) >= limit:
            break
    strong = bool(keys)
    if not keys and not (len(words) == 1 and words[0] in _GENERIC):
        cores = set.intersection(*(by_word.get(w, set()) for w in words))
        for core in sorted(cores, key=lambda c: (len(c), sorted(c)))[:limit]:
            if len(core) <= len(words) + 1:           # at most one extra word: "Semiya Kesari"
                keys += idx[core]
    if not keys or (not strong and len(set(keys)) < 2):    # one look-alike variant is no evidence
        return None
    from . import food_lexicon
    dishes = [_index()[k] for k in dict.fromkeys(keys)][:limit]
    # "sweet" here means the dish IS a sweet (a dessert, or a sweet word in its name),
    # not that its batter has a pinch of sugar: naan dough does, naan isn't a dessert.
    fs = [{**facts(d), "sweet": (d.get("course") or "").lower() == "dessert"
           or bool(food_lexicon.read(d.get("title") or "").sweet)} for d in dishes]
    if strong:                                         # the dish itself: everything it may hold
        allergens_ = set().union(*(f["allergens"] for f in fs))
        nonveg, sweet_ = any(not f["veg"] for f in fs), any(f["sweet"] for f in fs)
    else:                                              # its variants: what most of them (over half) hold
        half = len(fs) / 2
        allergens_ = {a for a in set().union(*(f["allergens"] for f in fs))
                      if sum(a in f["allergens"] for f in fs) > half}
        nonveg = sum(not f["veg"] for f in fs) > half
        sweet_ = sum(f["sweet"] for f in fs) > half
    return {"allergens": sorted(allergens_), "nonveg": nonveg, "sweet": sweet_, "strong": strong,
            "matched": [d["title"] for d in dishes]}


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
    hits, hidden, why = [], 0, []
    for d in idx.values():
        if q:
            s = _score(d, q, words)
            if s is None:
                continue
        else:
            if not meal or meal not in (d.get("meals") or []):
                continue
            s = 4
        verdict = blocked_verdict(user, d)
        if verdict["status"] in ("unsafe", "unknown"):
            hidden += 1
            if len(why) < HIDDEN_SHOWN:
                why.append({"key": d["key"], "title": d["title"], "code": verdict["code"],
                            "reason": _reason(verdict)})
            continue
        fits_meal = bool(meal and meal in (d.get("meals") or []))
        hits.append((s, 0 if fits_meal else 1, d.get("total_min") or 999, d["title"].lower(), d))
    hits.sort(key=lambda h: h[:4])
    from . import allergens
    return {"available": True, "total": len(hits), "hidden": hidden, "hidden_dishes": why,
            "dishes": [{**card(h[4]), "safety": allergens.safety_label(user, facts(h[4]))} for h in hits[:limit]],
            "credits": credits()}


def _reason(verdict: dict) -> str:
    from . import allergens
    return allergens.reason_text(verdict)


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
    """Kitchen amounts: halves to eighths, never "0" (a tiny amount reads "a little under 1/8")."""
    if 0 < x < Fraction(1, 8):
        return "a little under 1/8"
    x = x.limit_denominator(8) if x.denominator > 8 else x
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
        raise ValueError(f"This dish isn't for you or someone you cook for ({why}), so Ziggy doesn't show it.")
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
