"""What a recipe's ingredient words say about diet and allergens.

Shared by the recipe importers (Wikibooks Cookbook, the 6000+ Indian recipes dataset).
It reads only the words written in the ingredient list, never guesses beyond them, and
leans safe: a word that may mean an allergen counts as one. Phrases that only look like
one ("coconut milk", "meat masala", "eggplant") are taken out first.
"""
import re

NONVEG = re.compile(r"\b(chicken|mutton|lamb|goat|beef|pork|bacon|ham|fish|prawns?|shrimps?|crabs?|lobster|"
                    r"squid|eggs?|meat|keema|anchov\w*|sardines?|tuna|salmon|mackerel|pomfret|surmai|bangda|"
                    r"gelatin)\b", re.I)
ANIMAL = re.compile(r"\b(milk|curd|yogh?urt|dahi|ghee|butter|buttermilk|paneer|cream|cheese|honey|khoa|khoya|"
                    r"mawa|malai|condensed)\b", re.I)
ALLERGEN_WORDS = {"peanut": "peanut", "groundnut": "peanut", "cashew": "tree_nut", "almond": "tree_nut",
                  "pistachio": "tree_nut", "walnut": "tree_nut", "badam": "tree_nut", "kaju": "tree_nut",
                  "milk": "dairy", "curd": "dairy", "dahi": "dairy", "yogurt": "dairy", "yoghurt": "dairy",
                  "ghee": "dairy", "butter": "dairy", "paneer": "dairy", "cream": "dairy", "cheese": "dairy",
                  "khoa": "dairy", "khoya": "dairy", "mawa": "dairy", "malai": "dairy",
                  "egg": "egg", "wheat": "gluten", "maida": "gluten", "atta": "gluten", "semolina": "gluten",
                  "rava": "gluten", "sooji": "gluten", "suji": "gluten", "bread": "gluten", "pasta": "gluten",
                  "soy": "soy", "soya": "soy", "tofu": "soy", "sesame": "sesame", "til": "sesame",
                  "fish": "fish", "prawn": "shellfish", "shrimp": "shellfish", "crab": "shellfish",
                  "lobster": "shellfish"}
# Phrases whose words would mislead: plant milks, spice blends named after meat, and so on.
NOT_WHAT_IT_SAYS = re.compile(
    r"\b(coconut|almond|soy|soya|oat|cashew|rice)\s+(milk|cream|yogh?urt|curd)\b|"
    r"\b(peanut|cocoa|almond|cashew)\s+butter\b|"
    r"\b(meat|chicken|mutton|fish|egg|kebab|biryani)\s+(curry\s+)?masala(\s+powder)?\b|"
    r"\beggplant\b|\bbutter\s*nut\b|\bbuckwheat\b|\bcream\s+of\s+tartar\b|\beggless\b",
    re.I)


def _clean(words: str) -> str:
    return NOT_WHAT_IT_SAYS.sub(lambda m: _keep(m.group(0)), words)


def _keep(phrase: str) -> str:
    """The part of a misleading phrase that still counts ("peanut butter" is still peanut)."""
    p = phrase.lower()
    for nut in ("peanut", "almond", "cashew", "soy", "soya"):
        if p.startswith(nut):
            return nut
    return " "


def flags(ingredients: list[str]) -> dict:
    """{"veg", "vegan", "egg", "allergens"} read from the ingredient lines."""
    words = _clean(" ".join(ingredients).lower())
    veg = not NONVEG.search(words)
    allergens = sorted({a for w, a in ALLERGEN_WORDS.items() if re.search(rf"\b{w}(s|es)?\b", words)})
    return {"veg": veg, "vegan": veg and not ANIMAL.search(words),
            "egg": bool(re.search(r"\beggs?\b", words)), "allergens": allergens}
