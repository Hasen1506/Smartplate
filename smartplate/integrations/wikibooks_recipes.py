"""Import home-cooking recipes from the Wikibooks Cookbook (CC BY-SA 4.0).

Why this source: RecipeNLG and Recipe1M are licensed for non-commercial research only,
and most "Indian recipe" datasets are scraped from commercial sites with no licence.
The Wikibooks Cookbook is written by volunteers and licensed CC BY-SA 4.0
(https://en.wikibooks.org/wiki/Wikibooks:COPY): it may be reused, including in an app,
with attribution (a link to each recipe page and its licence) and share-alike (the
recipe text we redistribute stays CC BY-SA 4.0).

What is kept per recipe: the title, ingredient lines and steps as written, servings and
time when the page states them, the page URL and revision id, and the licence. Veg and
allergen hints are read from the ingredient words (never guessed beyond them). No cost
or nutrition is invented: a recipe from here has neither until real grocery prices
exist, so the planner doesn't plan it; people browse it and send its ingredients to
Instamart search.

Run where the network allows Wikimedia (the server, or the "Import recipes" workflow):

    python -m smartplate.integrations.wikibooks_recipes --category "Category:Indian recipes"

It writes smartplate/data/wikibooks_recipes.json (a snapshot the app reads).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import time
import urllib.parse
import urllib.request

API = "https://en.wikibooks.org/w/api.php"
SITE = "https://en.wikibooks.org/wiki/"
LICENSE = {"name": "CC BY-SA 4.0", "url": "https://creativecommons.org/licenses/by-sa/4.0/",
           "terms": "https://en.wikibooks.org/wiki/Wikibooks:COPY"}
USER_AGENT = "Ziggy-meal-planner/1.0 (https://github.com/Hasen1506/Smartplate; recipe import)"
COOKBOOK_NS = 102                      # the "Cookbook:" namespace on Wikibooks
DATA_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data",
                         "wikibooks_recipes.json")

INGREDIENT_HEADS = re.compile(r"^ingredients?\b", re.I)
STEP_HEADS = re.compile(r"^(procedure|directions?|method|preparation|instructions?|steps?)\b", re.I)
NONVEG = re.compile(r"\b(chicken|mutton|lamb|goat|beef|pork|fish|prawns?|shrimps?|crab|eggs?|meat|keema|anchov\w*)\b", re.I)
ANIMAL = re.compile(r"\b(milk|curd|yogh?urt|ghee|butter|paneer|cream|cheese|honey|khoa|khoya)\b", re.I)
ALLERGEN_WORDS = {"peanut": "peanut", "groundnut": "peanut", "cashew": "tree_nut", "almond": "tree_nut",
                  "pistachio": "tree_nut", "walnut": "tree_nut", "milk": "dairy", "curd": "dairy", "yogurt": "dairy",
                  "yoghurt": "dairy", "ghee": "dairy", "butter": "dairy", "paneer": "dairy", "cream": "dairy",
                  "cheese": "dairy", "egg": "egg", "wheat": "gluten", "maida": "gluten", "atta": "gluten",
                  "semolina": "gluten", "rava": "gluten", "sooji": "gluten", "soy": "soy", "sesame": "sesame",
                  "til": "sesame", "fish": "fish", "prawn": "shellfish", "shrimp": "shellfish", "crab": "shellfish"}


# --------------------------------------------------------------------------- #
# Wikitext → plain text
# --------------------------------------------------------------------------- #
def plain(text: str) -> str:
    """Wiki markup to readable text: links keep their label, templates and refs go."""
    t = re.sub(r"<ref[^>]*/>|<ref[^>]*>.*?</ref>", "", text, flags=re.S | re.I)
    t = re.sub(r"<!--.*?-->", "", t, flags=re.S)
    for _ in range(3):                                        # nested templates
        t = re.sub(r"\{\{[^{}]*\}\}", "", t)
    t = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]*)\]\]", r"\1", t)   # [[target|label]] → label
    t = re.sub(r"\[https?://\S+\s+([^\]]+)\]", r"\1", t)      # [url label] → label
    t = re.sub(r"\[https?://\S+\]", "", t)
    t = re.sub(r"'{2,}", "", t)                               # bold / italics
    t = re.sub(r"<[^>]+>", "", t)
    return re.sub(r"\s+", " ", t).strip()


def _summary(wikitext: str) -> dict:
    m = re.search(r"\{\{\s*recipe\s*summary(.*?)\}\}", wikitext, flags=re.S | re.I)
    out = {}
    if not m:
        return out
    for part in m.group(1).split("|"):
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.strip().lower()] = plain(v)
    return out


def _sections(wikitext: str) -> list[tuple[str, list[str]]]:
    out, head, lines = [], "", []
    for line in wikitext.splitlines():
        m = re.match(r"^(=+)\s*(.*?)\s*\1\s*$", line)
        if m:
            out.append((head, lines))
            head, lines = plain(m.group(2)), []
        else:
            lines.append(line)
    out.append((head, lines))
    return out


def parse(title: str, wikitext: str, revision: int | None = None) -> dict | None:
    """One Cookbook page → a recipe, or None when it has no ingredient list and steps."""
    ingredients, steps = [], []
    for head, lines in _sections(wikitext):
        if INGREDIENT_HEADS.match(head):
            ingredients += [plain(l.lstrip("*#:; ")) for l in lines if l.strip().startswith(("*", "#"))]
        elif STEP_HEADS.match(head):
            steps += [plain(l.lstrip("*#:; ")) for l in lines if l.strip().startswith(("#", "*"))]
    ingredients = [i for i in ingredients if i]
    steps = [s for s in steps if s]
    if not ingredients or not steps:
        return None
    meta = _summary(wikitext)
    words = " ".join(ingredients).lower()
    name = title.split(":", 1)[-1]
    veg = not NONVEG.search(words)
    servings = re.search(r"\d+", meta.get("servings", "") or meta.get("yield", ""))
    return {
        "key": "wb:" + re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_"),
        "title": name, "url": SITE + urllib.parse.quote(title.replace(" ", "_")), "revision": revision,
        "servings": int(servings.group(0)) if servings else None,
        "time": meta.get("time") or None,
        "ingredients": ingredients[:60], "steps": steps[:60],
        "veg": veg, "vegan": veg and not ANIMAL.search(words),
        "allergens": sorted({a for w, a in ALLERGEN_WORDS.items() if re.search(rf"\b{w}", words)}),
        "license": LICENSE["name"], "credit": "Wikibooks contributors",
    }


# --------------------------------------------------------------------------- #
# MediaWiki API (only when importing; the app never calls Wikibooks itself)
# --------------------------------------------------------------------------- #
def _get(params: dict, opener=None) -> dict:
    url = API + "?" + urllib.parse.urlencode({**params, "format": "json", "formatversion": 2, "maxlag": 5})
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with (opener or urllib.request.urlopen)(req, timeout=30) as r:
        return json.loads(r.read().decode())


def titles(category: str, opener=None, limit: int = 1000) -> list[str]:
    out, cont = [], {}
    while len(out) < limit:
        data = _get({"action": "query", "list": "categorymembers", "cmtitle": category, "cmlimit": 500,
                     "cmnamespace": COOKBOOK_NS, **cont}, opener)
        out += [m["title"] for m in data.get("query", {}).get("categorymembers", [])]
        if "continue" not in data:
            break
        cont = {"cmcontinue": data["continue"]["cmcontinue"]}
    return out[:limit]


def pages(names: list[str], opener=None, pause: float = 1.0):
    """(title, wikitext, revision id) in batches of 50, politely spaced."""
    for i in range(0, len(names), 50):
        data = _get({"action": "query", "prop": "revisions", "rvprop": "content|ids", "rvslots": "main",
                     "titles": "|".join(names[i:i + 50])}, opener)
        for p in data.get("query", {}).get("pages", []):
            rev = (p.get("revisions") or [{}])[0]
            text = ((rev.get("slots") or {}).get("main") or {}).get("content")
            if text:
                yield p["title"], text, rev.get("revid")
        if pause and i + 50 < len(names):
            time.sleep(pause)


def import_category(category: str, opener=None, limit: int = 1000, pause: float = 1.0) -> dict:
    recipes = []
    for title, text, rev in pages(titles(category, opener, limit), opener, pause):
        r = parse(title, text, rev)
        if r:
            recipes.append(r)
    recipes.sort(key=lambda r: r["title"].lower())
    return {"source": "Wikibooks Cookbook", "category": category, "license": LICENSE,
            "imported": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "note": "Recipe text by Wikibooks contributors, CC BY-SA 4.0. Each recipe links to its page and history.",
            "recipes": recipes}


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--category", default="Category:Indian recipes")
    ap.add_argument("--limit", type=int, default=1000)
    ap.add_argument("--out", default=DATA_FILE)
    args = ap.parse_args(argv)
    snap = import_category(args.category, limit=args.limit)
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(snap, f, ensure_ascii=False, indent=1)
    print(f"{len(snap['recipes'])} recipes from {args.category} → {args.out}")


if __name__ == "__main__":
    main()
