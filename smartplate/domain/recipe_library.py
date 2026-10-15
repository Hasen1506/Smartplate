"""The recipe library: home-cooking recipes imported from the Wikibooks Cookbook
(integrations/wikibooks_recipes.py, CC BY-SA 4.0), shown with their source and licence.

People browse them, read the ingredients and steps, and send ingredients to Instamart
search. They are not planned automatically: the Cookbook gives no prices or nutrition,
and Ziggy doesn't invent either. Recipes that break the person's diet or an allergy
(read from the ingredient words) are left out, and the count of those is shown.
"""
import json
import os

from ..integrations.wikibooks_recipes import DATA_FILE

_CACHE = {"mtime": None, "data": None}
LIMIT = 60


def snapshot() -> dict | None:
    try:
        mtime = os.path.getmtime(DATA_FILE)
    except OSError:
        return None
    if _CACHE["mtime"] != mtime:
        with open(DATA_FILE, encoding="utf-8") as f:
            _CACHE["data"], _CACHE["mtime"] = json.load(f), mtime
    return _CACHE["data"]


def _verdict(user: dict, r: dict) -> dict:
    """The shared safety checker (allergens.check) on the recipe's ingredient words, so
    the recipe library, the dish library and delivery dishes never disagree."""
    from . import allergens, dish_library
    return allergens.check(user, dish_library.facts(r))


def _blocked(user: dict, r: dict) -> str | None:
    from . import allergens
    v = _verdict(user, r)
    return allergens.reason_text(v) if v["status"] in ("unsafe", "unknown") else None


def for_user(user: dict, query: str = "") -> dict:
    snap = snapshot()
    if not snap:
        return {"available": False, "recipes": [], "hidden": 0}
    q = (query or "").strip().lower()
    shown, hidden, why = [], 0, []
    for r in snap.get("recipes", []):
        if q and q not in r["title"].lower() and not any(q in i.lower() for i in r["ingredients"]):
            continue
        reason = _blocked(user, r)
        if reason:
            hidden += 1
            if len(why) < 30:
                why.append({"title": r["title"], "reason": reason})
            continue
        shown.append(r)
    return {"available": True, "source": snap.get("source"), "license": snap.get("license"),
            "imported": snap.get("imported"), "note": snap.get("note"), "total": len(shown),
            "recipes": shown[:LIMIT], "hidden": hidden, "hidden_dishes": why}
