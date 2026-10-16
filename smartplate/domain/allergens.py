"""§5.1.1 — Allergy, diet and medical-condition handling as HARD constraints.

This is the liability-critical feature. Items that violate a user's allergen, diet or
medical profile are removed from the candidate set *before* optimisation, so the
solver cannot select them under any objective — including Survival/Tight-Week mode.
Duty of care beats budget, always.

One checker for every path (usability finding 5): the planner, the shortlist, "have
this for dinner" from a live Swiggy menu, the dish library and the recipe library all
call `check(user, item)`, so two screens can never disagree about a dish.

It FAILS CLOSED (sp-grill #37–#39):
- unknown is not safe. A dish with no veg mark is not vegetarian; a dish with no sugar
  figure fails diabetes; a dish whose ingredients Ziggy can't guess fails an allergy;
- the food lexicon (domain/food_lexicon) is read over every dish's name on every check,
  whatever the dish's source, and can only add a restriction ("Kozhi means chicken");
- what passes for someone with an allergy, a medical condition or a vegan diet is
  "not checked", never "safe": Swiggy menus carry no allergen or nutrition data, so the
  person is told to ask the restaurant.

Verdicts: ok · not_checked (shown, with a caution) · unsafe · unknown (both hidden,
each with a reason code and plain words).
"""
from __future__ import annotations

import re
from functools import lru_cache

from . import food_lexicon

DIABETES_SUGAR_G = 20     # per item; more is "unsafe for diabetes"

# Where a dish's diet/allergen facts come from:
#   provider   the platform said so (not yet available from Swiggy: research note in
#              docs/safety/fail-closed.md)
#   catalogue  hand-curated rows (the sample catalogue used by tests)
#   template   a dish-name template matched (live_catalog.TEMPLATES)
#   recipe     the dish name matched recipes in the recipe library
#   ingredients a recipe's own ingredient list (dish library, home cooking)
#   name       only lexicon words in the name; nothing about the rest of the dish
#   none       nothing at all
KNOWN_BASES = ("provider", "catalogue", "template", "recipe", "ingredients")

# Kept for callers that read the rules by name; each is fail-closed (unknown → False).
MEDICAL_RULES = {
    "diabetes": lambda item: _sugar_verdict(item)[0] == "ok",
    "hypertension": lambda item: _sodium_verdict(item)[0] == "ok",
    "celiac": lambda item: "gluten" not in _allergens(item) and _basis(item) in KNOWN_BASES,
}

# The "instruct" half of exclude-and-instruct (docs/optimization-and-ux.md §1.3,
# design-audit §4.3): hard exclusion is the floor, but many dishes are safe *with a
# request*, so we attach order-time cart instructions to keep adjustable dishes in
# the candidate set instead of pruning the whole menu down to pre-safe items.
MEDICAL_INSTRUCTIONS = {
    "diabetes": "no added sugar / less sweet",
    "hypertension": "less salt — no extra salt",
}

# Jain / no onion-garlic diet (usab audit 16 Oct #11): onion, garlic and the root vegetables a
# Jain diet leaves out, in English and the Hindi, Tamil, Telugu, Kannada and Malayalam names a
# Chennai menu uses. Read over the dish name like the food lexicon; a hit rules the dish out.
JAIN_WORDS = (
    (r"onions?|spring onions?|shallots?|scallions?|leeks?", "onion"),
    (r"pyaa?z|pyaaz|kanda|kaanda|vengayam|vengaya|vengaaya|chinna vengayam|ulli|eerulli|erulli|"
     r"neerulli|ullipaya|ullipaaya|savala", "onion"),
    (r"garlic|garlicky", "garlic"),
    (r"lehsun|lahsun|lasun|lehsuni|lasooni|lasuni|poondu|vellulli|velluli|veluthulli|belluli|"
     r"bellulli", "garlic"),
    (r"potato(es)?|aloo|alu|batata|urulai|urulaikizhangu|urulai kizhangu|bangaladumpa|aalugadde",
     "potato (a root vegetable, not Jain)"),
    (r"carrots?|gajar|radish|mooli|mullangi|beet ?root|beets?|sweet potato|shakarkandi|sakkaravalli|"
     r"yam|suran|senai|kizhangu|arbi|seppankizhangu|ginger|adrak|inji|turnip|shalgam",
     "a root vegetable (not Jain)"),
)
_JAIN_RE = [(re.compile(r"\b(" + rx + r")\b"), gloss) for rx, gloss in JAIN_WORDS]
# Phrases that say the dish is made without them.
_JAIN_OK = re.compile(r"\b(jain|no onion|without onion|no garlic|without garlic|"
                                    r"satvik|sattvic|swaminarayan)\b")
JAIN_NOTE = ("Restaurants often cook with onion and garlic even when the name doesn't say so: ask for "
             "Jain preparation (no onion, garlic or root vegetables).")
JAIN_INSTRUCTION = "Jain: no onion, no garlic, no root vegetables"


def jain_hit(name: str) -> str | None:
    """Plain words for the first onion / garlic / root-vegetable word in a dish name, else None
    ("Aloo means potato (a root vegetable, not Jain)"). "Jain …" / "no onion" dishes pass."""
    n = food_lexicon.normalise(name)
    if not n or _JAIN_OK.search(n):
        return None
    for rx, gloss in _JAIN_RE:
        m = rx.search(n)
        if m:
            w = m.group(1)
            return f"The name says “{w}”" if w.rstrip("s") in (gloss, gloss.split(" ")[0]) else f"{w.capitalize()} means {gloss}"
    return None


NOT_CHECKED = ("Not checked: Ziggy reads dish names, not recipes, and Swiggy doesn't list allergens or "
               "nutrition. Ask the restaurant before you order.")
MEDICAL_NOTE = "Health filters are a convenience, not medical advice."


def order_instructions(user: dict, item: dict | None = None) -> list[str]:
    """Cart special-instructions implied by the user's medical profile.

    These ride along on a *safe* item's order so the kitchen adjusts it (e.g. a
    diabetic's drink comes unsweetened) — the soft refinement above the hard
    exclusion in `violates`. `item` is optional: when given, an instruction is only
    added if it's actually relevant to that dish (e.g. don't say "no sugar" on a
    plain dal), keeping the note set tight.
    """
    notes: list[str] = []
    if user.get("diet") == "jain":                    # the kitchen is asked, every order (#11)
        notes.append(JAIN_INSTRUCTION)
    for condition in user.get("medical", []):
        msg = MEDICAL_INSTRUCTIONS.get(condition)
        if not msg:
            continue
        if item is not None and not _instruction_relevant(condition, item):
            continue
        if msg not in notes:
            notes.append(msg)
    return notes


def _instruction_relevant(condition: str, item: dict) -> bool:
    """Whether a medical instruction actually applies to this dish."""
    if condition == "diabetes":
        # relevant to anything with some sugar (drinks, desserts, sweet gravies)
        return (item.get("sugar_g") or 0) > 0 or "sweet" in (item.get("tags") or [])
    if condition == "hypertension":
        return True   # salt is near-universal in restaurant food
    return True


# --------------------------------------------------------------------------- #
# Facts about a dish
# --------------------------------------------------------------------------- #
def _name(item: dict) -> str:
    return str(item.get("name") or item.get("title") or item.get("item_name") or "")


def reading(item: dict) -> food_lexicon.Reading:
    """What the dish's own words say (name, plus a menu description when there is one)."""
    return _reading(_name(item), item.get("description") or "")


@lru_cache(maxsize=20000)
def _reading(name: str, description: str) -> food_lexicon.Reading:
    return food_lexicon.read_all(name, description)


def _basis(item: dict) -> str:
    if item.get("safety_basis"):
        return item["safety_basis"]
    # Rows written before the basis was recorded: curated sample rows are "catalogue";
    # a live row read before the fix can't be trusted for allergies (its facts came
    # from a 14-word list), so it counts as "name" until the next menu refresh.
    if item.get("source") == "live":
        return "name"
    return "catalogue"


def _allergens(item: dict) -> set[str]:
    return set(item.get("allergens") or []) | reading(item).allergens


def _veg_state(item: dict) -> tuple[str, str]:
    """("veg" | "nonveg" | "unknown", plain-words reason) for this dish."""
    hits = reading(item).nonveg
    if hits:
        return "nonveg", hits[0].words
    if "egg" in set(item.get("allergens") or []):
        return "nonveg", "Contains egg (egg counts as non-veg)"
    if "nonveg" in (item.get("tags") or []):
        return "nonveg", "Marked non-veg"
    veg = item.get("veg", None)
    known = item.get("veg_known", 1 if veg is not None else 0)
    if item.get("source") == "live" and not item.get("safety_basis"):
        known = 0                               # read before missing marks meant "unknown"
    if veg is None or not known:
        return "unknown", "Swiggy doesn't mark it veg or non-veg, so Ziggy can't tell"
    if veg in (0, False, "0"):
        return "nonveg", "Marked non-veg"
    return "veg", ""


def _sugar_verdict(item: dict) -> tuple[str, str]:
    sweet = reading(item).sweet
    if sweet:
        return "unsafe", sweet[0].words
    if "dessert" in (item.get("tags") or []):
        return "unsafe", "It's a dessert"
    if item.get("sweet") is True:
        return "unsafe", "Its recipe has sugar, jaggery or honey"
    sugar = item.get("sugar_g")
    unknown = item.get("nutrition_known", 1) == 0 or sugar is None
    if not unknown:
        try:
            if float(sugar) > DIABETES_SUGAR_G:
                return "unsafe", f"About {float(sugar):.0f} g sugar (more than {DIABETES_SUGAR_G} g)"
            return "ok", ""
        except (TypeError, ValueError):
            pass
    if item.get("sweet") is False:              # a full ingredient list with no sweetener
        return "ok", ""
    return "unknown", "No sugar figure for this dish, so Ziggy can't tell if it fits diabetes"


def _sodium_verdict(item: dict) -> tuple[str, str]:
    salty = reading(item).high_sodium
    if salty:
        return "unsafe", salty[0].words
    if "high_sodium" in (item.get("tags") or []):
        return "unsafe", "Marked high in salt"
    if item.get("nutrition_known", 1) == 0 and item.get("sweet") is None:
        return "unknown", "No nutrition figures for this dish, so Ziggy can't check salt"
    return "ok", ""


def _allergen_words(item: dict, fam: str) -> str:
    hits = reading(item).allergen_hits(fam)
    if hits:
        return hits[0].words
    label = fam.replace("_", " ")
    if item.get("safety_basis") in ("recipe", "template") and _name(item):
        from . import dish_library
        guess = dish_library.infer(_name(item))
        if guess and fam in guess["allergens"]:
            return f"Recipes for {_name(item)} usually include {label}"
    return f"Contains {label}"


# --------------------------------------------------------------------------- #
# The checker
# --------------------------------------------------------------------------- #
def _verdict(status: str, code: str, reason: str, who: str | None = None) -> dict:
    return {"status": status, "code": code, "reason": reason, "who": who}


def _check_one(person: dict, item: dict) -> dict | None:
    """The first hard rule this person's profile and the dish break, else None."""
    allergens_ = _allergens(item)
    basis = _basis(item)
    mine = set(person.get("allergens") or [])
    medical = set(person.get("medical") or [])
    clash = mine & allergens_
    if clash:
        fam = sorted(clash)[0]
        return _verdict("unsafe", f"allergen:{fam}", f"{_allergen_words(item, fam)} ({fam.replace('_', ' ')} "
                                                      f"allergy)")
    if "celiac" in medical and "gluten" in allergens_:
        return _verdict("unsafe", "celiac", f"{_allergen_words(item, 'gluten')} (celiac)")

    diet = person.get("diet") or "nonveg"
    if diet in ("veg", "vegan", "jain"):
        state, why = _veg_state(item)
        word = "Jain" if diet == "jain" else diet
        if state == "nonveg":
            return _verdict("unsafe", "not_veg", f"{why} (not {word})")
        if state == "unknown":
            return _verdict("unknown", "veg_unknown", why)
    if diet == "jain":
        hit = jain_hit(_name(item)) or (jain_hit(item.get("description") or "") if item.get("description") else None)
        if hit:
            return _verdict("unsafe", "not_jain", f"{hit} (Jain / no onion-garlic)")
    if diet == "egg":
        meat = [h for h in reading(item).hits if h.kind == "nonveg"]
        if meat:
            return _verdict("unsafe", "not_eggetarian", f"{meat[0].words} (eggetarian: no meat or fish)")
        state, why = _veg_state(item)
        if state == "nonveg" and not (reading(item).egg or "egg" in set(item.get("allergens") or [])):
            return _verdict("unknown", "egg_unknown",
                            "Swiggy marks it non-veg and its name doesn't say egg, so Ziggy can't tell it has no "
                            "meat or fish")
        if state == "unknown":
            return _verdict("unknown", "veg_unknown", why)
    if diet == "vegan":
        if "dairy" in allergens_:
            return _verdict("unsafe", "not_vegan", f"{_allergen_words(item, 'dairy')} (not vegan)")
        if item.get("vegan") is False:
            return _verdict("unsafe", "not_vegan", "Its recipe has milk, ghee, curd or honey (not vegan)")
        if basis not in KNOWN_BASES:
            return _verdict("unknown", "vegan_unknown",
                            "Ziggy can't tell what this dish is made with, so it can't say it's vegan")

    if (mine or "celiac" in medical) and basis not in KNOWN_BASES:
        return _verdict("unknown", "allergens_unknown",
                        "Ziggy can't tell what's in this dish (its name matches nothing Ziggy knows): "
                        "ask the restaurant")
    if "diabetes" in medical:
        state, why = _sugar_verdict(item)
        if state != "ok":
            return _verdict(state, "diabetes" if state == "unsafe" else "sugar_unknown", f"{why} (diabetes)"
                            if state == "unsafe" else why)
    if "hypertension" in medical:
        state, why = _sodium_verdict(item)
        if state != "ok":
            return _verdict(state, "hypertension" if state == "unsafe" else "salt_unknown",
                            f"{why} (blood pressure)" if state == "unsafe" else why)
    return None


def check(user: dict, item: dict) -> dict:
    """{"status", "code", "reason", "who"} for one dish and everyone it must suit.

    status: "unsafe" / "unknown" (never offered, planned or carted), "not_checked"
    (shown with a caution: an allergy, medical condition or vegan diet and no data
    from the restaurant), or "ok". A profile in a household also carries its other
    members (`household_members`); a shared plan must suit every one of them (§5.2.7)."""
    people = [user] + list(user.get("household_members") or [])
    for i, person in enumerate(people):
        v = _check_one(person, item)
        if v:
            if i:
                v["who"] = person.get("name") or "a household member"
            return v
    cautious = any(p.get("allergens") or p.get("medical") or p.get("diet") == "vegan" for p in people)
    if cautious and _basis(item) != "provider":
        return _verdict("not_checked", "not_checked", NOT_CHECKED)
    if any(p.get("diet") == "jain" for p in people) and _basis(item) != "provider":
        return _verdict("not_checked", "jain_not_checked", JAIN_NOTE)
    return _verdict("ok", "ok", "")


def reason_text(verdict: dict) -> str:
    """One line for the person: "Kozhi means chicken (Tamil/Malayalam) (not veg)"."""
    if verdict.get("who"):
        return f"{verdict['reason']} — {verdict['who']}"
    return verdict["reason"]


def violates(user: dict, item: dict) -> str | None:
    """A human reason string if the item must not be offered to the user, else None."""
    v = check(user, item)
    if v["status"] in ("unsafe", "unknown"):
        return reason_text(v)
    return None


def safe_items(user: dict, items: list[dict]) -> list[dict]:
    """Filter a candidate list down to only items safe for the user."""
    return [it for it in items if violates(user, it) is None]


def hidden_report(user: dict, items: list[dict], limit: int = 30) -> dict:
    """Why dishes were hidden: counts by reason code and the first `limit` dishes with
    their reason, for the "N hidden" notes (usability finding 6)."""
    by_code: dict[str, int] = {}
    rows = []
    for it in items:
        v = check(user, it)
        if v["status"] not in ("unsafe", "unknown"):
            continue
        by_code[v["code"]] = by_code.get(v["code"], 0) + 1
        if len(rows) < limit:
            rows.append({"name": _name(it), "id": it.get("id") or it.get("key"), "code": v["code"],
                         "status": v["status"], "reason": reason_text(v)})
    return {"count": sum(by_code.values()), "by_code": by_code, "dishes": rows}


def safety_label(user: dict, item: dict) -> dict:
    """What a shown dish says about safety: {"status", "note"}; never the word "safe".
    For someone with an allergy (them or anyone eating with them) it also carries
    `allergen_likelihood`: how often home recipes for this dish hold each of THEIR
    allergens (see allergen_likelihood)."""
    v = check(user, item)
    note = v["reason"] if v["status"] != "ok" else ""
    if v["status"] == "not_checked" and any(p.get("medical") for p in [user, *(user.get("household_members") or [])]):
        note = f"{note} {MEDICAL_NOTE}"
    out = {"status": v["status"], "code": v["code"], "note": note}
    if any(p.get("allergens") for p in [user, *(user.get("household_members") or [])]):
        out["allergen_likelihood"] = allergen_likelihood(user, item)
    return out


def allergen_likelihood(user: dict | None, item: dict | str) -> dict:
    """Founder decision (14 Oct 2026): keep people with allergies in, and tell them how
    likely a restaurant dish holds each allergen from the recipe library ("usually contains
    cashew — 7 of 9 recipes"), with the caveat that restaurants cook differently.

    Informational only. It never changes a verdict: `check` stays fail-closed (a dish whose
    name matches no recipe is still "unknown" for an allergy) and nothing here certifies a
    dish safe. An allergen no recipe holds is simply not listed: "0 of 9 recipes" is not
    "free of". `yours` lists the rows for the allergies of the person and the people
    eating with them (all rows when no profile is given)."""
    from . import dish_library
    name = item if isinstance(item, str) else _name(item)
    lk = dish_library.likelihood(name)
    mine = set()
    for p in [user or {}, *((user or {}).get("household_members") or [])]:
        mine |= set(p.get("allergens") or [])
    if not lk:
        return {"dish": name, "known": False, "allergens": [], "yours": [],
                "text": (f"Ziggy's recipe library has no recipe for {name or 'this dish'}, so it can't say how "
                         "likely it is to hold an allergen. Ask the restaurant."),
                "caveat": dish_library.LIKELIHOOD_CAVEAT, "never_safe": True}
    yours = [r for r in lk["allergens"] if r["allergen"] in mine] if mine else list(lk["allergens"])
    return {"dish": name, "known": True, "recipes": lk["recipes"], "matched": lk["matched"],
            "allergens": lk["allergens"], "yours": yours,
            "text": "; ".join(r["text"] for r in yours) if yours else
                    (f"None of {lk['recipes']} home recipe{'s' if lk['recipes'] != 1 else ''} for this dish "
                     f"list{'s' if lk['recipes'] == 1 else ''} your allergens. That is not a promise: ask the restaurant."
                     if mine else ""),
            "caveat": dish_library.LIKELIHOOD_CAVEAT, "never_safe": True}


def household_safe(members: list[dict], item: dict) -> str | None:
    """Group mode (§5.2.7): an item must be safe for EVERY member. The union of
    all members' allergens/medical rules is the hard constraint."""
    for m in members:
        v = _check_one(m, item)
        if v:
            return f"{m['name']}: {v['reason']}"
    return None
