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


def _blocked(user: dict, r: dict) -> str | None:
    if user.get("diet") == "vegan" and not r.get("vegan"):
        return "diet"
    if user.get("diet") == "veg" and not r.get("veg"):
        return "diet"
    if set(user.get("allergens") or []) & set(r.get("allergens") or []):
        return "allergen"
    return None


def for_user(user: dict, query: str = "") -> dict:
    snap = snapshot()
    if not snap:
        return {"available": False, "recipes": [], "hidden": 0}
    q = (query or "").strip().lower()
    shown, hidden = [], 0
    for r in snap.get("recipes", []):
        if q and q not in r["title"].lower() and not any(q in i.lower() for i in r["ingredients"]):
            continue
        if _blocked(user, r):
            hidden += 1
            continue
        shown.append(r)
    return {"available": True, "source": snap.get("source"), "license": snap.get("license"),
            "imported": snap.get("imported"), "note": snap.get("note"), "total": len(shown),
            "recipes": shown[:LIMIT], "hidden": hidden}
