"""Import the dish library from the "6000+ Indian Food Recipes Dataset".

Source: Kanishka Jain, Mendeley Data V1 (2020), DOI 10.17632/xsphgmmh7b.1, listed as
CC BY 4.0, built from recipes published on Archana's Kitchen (archanaskitchen.com).
Also at https://github.com/kanishk307/IndianFoodDatasetGeneration (Dataset/IndianFoodDatasetCSV.csv).

What is kept per dish, by default: the dish name, its ingredient lines, prep/cook/total
time, servings, cuisine, course, diet and the link to the original recipe page. These
are facts about a dish. The method text (written by Archana's Kitchen's authors) is NOT
copied unless `--with-steps` is passed: the dataset's CC BY licence comes from the
person who scraped it, so whether the method text may be republished is the owner's
call. Without it, cooking along shows the ingredients, timers and a link to the method.

Rows whose ingredient translation failed (still in Hindi) are left out: the safety words
are read in English, and Instamart is searched in English.

Veg, egg, vegan and allergen marks are read from the ingredient words (domain/food_words)
together with the dataset's own Diet label, leaning safe: a dish is veg only when both
agree. Nothing is invented: no prices and no nutrition (the planner treats a library dish
as "groceries not priced, no nutrition estimate").

    python -m smartplate.integrations.indian_recipes path/to/IndianFoodDatasetCSV.csv

writes smartplate/data/indian_dishes.json.gz (the snapshot the app reads).
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import gzip
import json
import os
import re

from ..domain import food_words

DATA_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data",
                         "indian_dishes.json.gz")
SOURCE = {"name": "6000+ Indian Food Recipes Dataset", "author": "Kanishka Jain",
          "url": "https://data.mendeley.com/datasets/xsphgmmh7b/1", "doi": "10.17632/xsphgmmh7b.1",
          "license": "CC BY 4.0", "license_url": "https://creativecommons.org/licenses/by/4.0/",
          "origin": "Archana's Kitchen", "origin_url": "https://www.archanaskitchen.com/"}
DEVANAGARI = re.compile("[\u0900-\u097F]")
NONVEG_DIETS = {"non vegeterian", "non vegetarian", "high protein non vegetarian"}
VEG_DIETS = {"vegetarian", "high protein vegetarian", "no onion no garlic (sattvic)", "vegan"}
# The dataset's courses → which meals a dish is a whole meal for. Sides, snacks and
# desserts stay in the library (people cook them) without being offered as a meal.
COURSE_MEALS = {"south indian breakfast": ["breakfast"], "north indian breakfast": ["breakfast"],
                "indian breakfast": ["breakfast"], "world breakfast": ["breakfast"],
                "brunch": ["breakfast", "lunch"], "lunch": ["lunch", "dinner"], "dinner": ["lunch", "dinner"],
                "main course": ["lunch", "dinner"], "one pot dish": ["lunch", "dinner"]}


def split_ingredients(text: str) -> list[str]:
    """"1 cup Rice - soaked, 2 Onions (sliced, thin)" → lines; commas inside brackets stay."""
    out, depth, cur = [], 0, []
    for ch in text or "":
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth = max(0, depth - 1)
        if ch == "," and depth == 0:
            out.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    out.append("".join(cur))
    return [re.sub(r"\s+", " ", x).strip(" .") for x in out if x.strip(" .")]


def split_steps(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s*(?=[A-Z])", (text or "").replace("\xa0", " "))
    return [re.sub(r"\s+", " ", p).strip() for p in parts if len(p.strip()) > 2]


def clean_title(raw: str) -> tuple[str, str | None]:
    """"Pudina Khara Pongal Recipe (Rice and Lentils …)" → ("Pudina Khara Pongal", "Rice and Lentils …")."""
    raw = re.sub(r"\s+", " ", (raw or "").replace("﻿", "")).strip()
    about = None
    m = re.match(r"^(.*?)\s*\((.*)\)\s*$", raw)
    if m and m.group(1):
        raw, about = m.group(1), m.group(2).strip() or None
    title = re.sub(r"\s*-?\s*\brecipes?\b\s*$", "", raw, flags=re.I).strip(" -")
    title = re.sub(r"\s+\brecipe\b\s+", " ", title, flags=re.I)
    halves = re.split(r"\s+-\s+", title, maxsplit=1)
    if len(halves) == 2 and halves[0].strip().lower() == halves[1].strip().lower():
        title = halves[0]                                   # "Pudina Khara Pongal - Pudina Khara Pongal"
    return title or raw, about


def _int(v) -> int | None:
    try:
        n = int(float(v))
        return n if n > 0 else None
    except (TypeError, ValueError):
        return None


def _https(url: str) -> str | None:
    url = (url or "").strip()
    return ("https://" + url.split("://", 1)[1]) if url.startswith(("http://", "https://")) else None


def parse_row(row: dict, with_steps: bool = False) -> dict | None:
    row = {k.replace("﻿", "").strip(): v for k, v in row.items() if k}
    title, about = clean_title(row.get("TranslatedRecipeName") or row.get("RecipeName"))
    ingredients = split_ingredients(row.get("TranslatedIngredients") or row.get("Ingredients"))
    if not title or not ingredients or DEVANAGARI.search(title + " ".join(ingredients)):
        # Rows whose translation failed: the safety words are English, and so is Instamart search.
        return None
    if about and re.match(r"^recipe in \w+$", about, re.I):
        about = None
    diet = (row.get("Diet") or "").strip().lower()
    course = (row.get("Course") or "").strip()
    f = food_words.flags(ingredients + [title])
    veg = f["veg"] and not f["egg"] and diet not in NONVEG_DIETS and diet != "eggetarian"
    allergens = set(f["allergens"])
    if diet == "eggetarian":
        allergens.add("egg")
    r = {
        "key": f"ak:{_int(row.get('Srno')) or abs(hash(title)) % 10**8}",
        "title": title, "about": about,
        "ingredients": ingredients[:60],
        "prep_min": _int(row.get("PrepTimeInMins")), "cook_min": _int(row.get("CookTimeInMins")),
        "total_min": _int(row.get("TotalTimeInMins")), "servings": _int(row.get("Servings")),
        "cuisine": re.sub(r"\s*recipes?\s*$", "", (row.get("Cuisine") or "").replace("﻿", ""), flags=re.I).strip() or None,
        "course": course or None, "meals": COURSE_MEALS.get(course.lower(), []),
        "diet": row.get("Diet") or None,
        "veg": veg, "vegan": veg and (f["vegan"] or diet == "vegan"),
        "allergens": sorted(allergens),
        "url": _https(row.get("URL")),
    }
    if with_steps:
        r["steps"] = split_steps(row.get("TranslatedInstructions") or row.get("Instructions"))[:60]
    return r


def build(csv_path: str, with_steps: bool = False) -> dict:
    with open(csv_path, encoding="utf-8", errors="replace", newline="") as f:
        rows = [parse_row(r, with_steps) for r in csv.DictReader(f)]
    seen, dishes = set(), []
    for r in rows:
        if r and r["key"] not in seen:
            seen.add(r["key"])
            dishes.append(r)
    dishes.sort(key=lambda r: r["title"].lower())
    return {"source": SOURCE, "imported": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "steps_included": with_steps,
            "note": ("Dish names, ingredient lists, times and diet labels from the 6000+ Indian Food Recipes "
                     "Dataset (Kanishka Jain, CC BY 4.0), originally from Archana's Kitchen. Each dish links "
                     "to its original recipe page."),
            "dishes": dishes}


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("csv")
    ap.add_argument("--out", default=DATA_FILE)
    ap.add_argument("--with-steps", action="store_true",
                    help="also copy the method text (only if you have the right to republish it)")
    args = ap.parse_args(argv)
    snap = build(args.csv, args.with_steps)
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as f:
        f.write(json.dumps(snap, ensure_ascii=False, separators=(",", ":")).encode())
    print(f"{len(snap['dishes'])} dishes → {args.out}")


if __name__ == "__main__":
    main()
