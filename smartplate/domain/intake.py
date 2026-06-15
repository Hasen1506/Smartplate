"""Free-text "I made X" intake lookup (docs/optimization-and-ux.md §7, SPEC §1.7/P4).

Closes the planned≠eaten gap: cook days currently reuse the menu picker, but when a
user cooks something off-plan they should be able to just type it — "dal + 2 rotis" —
and get a nutrition estimate that can land in the ledger.

Honest by design (cold-start): we match against a small library of common home
dishes, **flag what we couldn't match**, and return an *estimate to confirm* with a
confidence — never silent precision. Swap in a real food DB (IFCT / USDA / Nutritionix)
to widen coverage; the interface stays the same.
"""
import re

MACROS = ("kcal", "protein_g", "carbs_g", "fat_g", "sugar_g")

# Per-serving estimates for common (India-leaning) home dishes. Coarse on purpose.
HOME_DISHES = {
    "dal":         {"kcal": 150, "protein_g": 9,  "carbs_g": 20, "fat_g": 4,  "sugar_g": 2},
    "rice":        {"kcal": 200, "protein_g": 4,  "carbs_g": 44, "fat_g": 1,  "sugar_g": 0},
    "roti":        {"kcal": 120, "protein_g": 3,  "carbs_g": 18, "fat_g": 4,  "sugar_g": 1},
    "paratha":     {"kcal": 260, "protein_g": 6,  "carbs_g": 36, "fat_g": 10, "sugar_g": 2},
    "curd rice":   {"kcal": 250, "protein_g": 7,  "carbs_g": 40, "fat_g": 6,  "sugar_g": 4},
    "poha":        {"kcal": 270, "protein_g": 6,  "carbs_g": 45, "fat_g": 7,  "sugar_g": 3},
    "upma":        {"kcal": 250, "protein_g": 6,  "carbs_g": 40, "fat_g": 8,  "sugar_g": 2},
    "oats":        {"kcal": 220, "protein_g": 8,  "carbs_g": 33, "fat_g": 5,  "sugar_g": 4},
    "idli":        {"kcal": 58,  "protein_g": 2,  "carbs_g": 12, "fat_g": 0,  "sugar_g": 0},
    "dosa":        {"kcal": 170, "protein_g": 4,  "carbs_g": 28, "fat_g": 5,  "sugar_g": 1},
    "pongal":      {"kcal": 300, "protein_g": 9,  "carbs_g": 45, "fat_g": 9,  "sugar_g": 2},
    "khichdi":     {"kcal": 350, "protein_g": 12, "carbs_g": 55, "fat_g": 8,  "sugar_g": 3},
    "sambar":      {"kcal": 110, "protein_g": 5,  "carbs_g": 15, "fat_g": 3,  "sugar_g": 3},
    "rasam":       {"kcal": 60,  "protein_g": 2,  "carbs_g": 8,  "fat_g": 2,  "sugar_g": 2},
    "sabzi":       {"kcal": 130, "protein_g": 4,  "carbs_g": 12, "fat_g": 8,  "sugar_g": 4},
    "paneer":      {"kcal": 280, "protein_g": 14, "carbs_g": 8,  "fat_g": 22, "sugar_g": 4},
    "egg":         {"kcal": 78,  "protein_g": 6,  "carbs_g": 1,  "fat_g": 5,  "sugar_g": 0},
    "egg curry":   {"kcal": 250, "protein_g": 14, "carbs_g": 8,  "fat_g": 18, "sugar_g": 3},
    "chicken curry": {"kcal": 240, "protein_g": 24, "carbs_g": 6, "fat_g": 13, "sugar_g": 3},
}

# Map what people type → a library key (plurals, synonyms).
ALIASES = {
    "rotis": "roti", "chapati": "roti", "chapatis": "roti", "phulka": "roti",
    "eggs": "egg", "omelette": "egg curry", "omelet": "egg curry",
    "veg": "sabzi", "vegetable": "sabzi", "vegetables": "sabzi", "curry": "sabzi",
    "idlis": "idli", "dosas": "dosa", "chapathi": "roti",
    "paneer curry": "paneer", "dal tadka": "dal", "toor dal": "dal",
}

_WORD_NUM = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
             "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}
_QTY_RE = re.compile(r"^\s*(\d+|a|an|one|two|three|four|five|six|seven|eight|nine|ten)\s+", re.I)


def _match_dish(phrase: str) -> str | None:
    p = phrase.strip().lower()
    if not p:
        return None
    if p in HOME_DISHES:
        return p
    if p in ALIASES:
        return ALIASES[p]
    # try a trailing-'s' singular, then a substring match against known keys/aliases
    singular = p[:-1] if p.endswith("s") else p
    if singular in HOME_DISHES:
        return singular
    if singular in ALIASES:
        return ALIASES[singular]
    for key in list(HOME_DISHES) + list(ALIASES):
        if key in p:
            return ALIASES.get(key, key)
    return None


def parse(text: str) -> dict:
    """Estimate the nutrition of a free-text meal. Returns matched items, the macro
    totals, anything we couldn't match, and a confidence (matched / all chunks)."""
    chunks = [c.strip() for c in re.split(r"[,;+]|\band\b|\bwith\b", text or "", flags=re.I) if c.strip()]
    totals = {k: 0.0 for k in MACROS}
    items, unmatched = [], []
    for chunk in chunks:
        qty, rest = 1, chunk
        m = _QTY_RE.match(chunk)
        if m:
            tok = m.group(1).lower()
            qty = int(tok) if tok.isdigit() else _WORD_NUM.get(tok, 1)
            rest = chunk[m.end():]
        key = _match_dish(rest)
        if not key:
            unmatched.append(chunk)
            continue
        per = HOME_DISHES[key]
        for k in MACROS:
            totals[k] += per[k] * qty
        items.append({"dish": key, "qty": qty, "kcal": per["kcal"] * qty})
    matched = len(items)
    confidence = round(matched / (matched + len(unmatched)), 2) if (matched or unmatched) else 0.0
    note = "Estimate — confirm before logging."
    if unmatched:
        note += " Couldn't match: " + ", ".join(unmatched) + "."
    return {
        "items": items,
        "nutrition": {k: round(v) for k, v in totals.items()},
        "unmatched": unmatched,
        "confidence": confidence,
        "note": note,
    }
