"""One food lexicon for every safety check: what a dish name, a menu description or a
recipe's ingredient words say about meat, egg, allergens, sugar and salt.

Before this module three lists disagreed (live_catalog.NONVEG_WORDS, ingredients.NONVEG_WORDS,
food_words.NONVEG), so the same dish could be veg on one screen and non-veg on another
(sp-grill #60), and none of them knew Chennai menu words such as kozhi, eral or nandu
(#38) or verkadalai, kaju or mor (#39). Every reader now goes through `read()`.

Rules of the lexicon
- It leans safe. A word that often means meat, an allergen or sugar counts as one; a
  wrong hit only hides a dish, a missed one can hurt someone. Kaju Kari is hidden from a
  cashew allergy *and* from a vegetarian ("kari" is Tamil for meat) until a person checks.
- It can only ever ADD a restriction. Not finding a word proves nothing: a dish with no
  hit is "not checked", never "safe" (allergens.check says which).
- Every hit carries plain words for the person ("Kozhi (Tamil) means chicken"), which is
  the "why is this hidden" answer.
- Phrases that only look like a hit ("eggless", "eggplant", "peanut butter" for dairy,
  "coconut milk", "kari patta" = curry leaves, "veg keema") are taken out first.

Word lists are by meaning, with the language in the gloss. Fragments are regular
expressions matched against lower-case text with letters on neither side, so
"[a-z]*kozhi" also finds "Nattukozhi".
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache

# kind -> [(regex fragment, gloss)]. Glosses read "<Word> means <gloss>".
NONVEG: list[tuple[str, str]] = [
    # English
    (r"chicken|chiken|chikken", "chicken"), (r"mutton|lamb|goat|meat|meats", "meat"),
    (r"beef|pork|bacon|ham|salami|sausages?|pepperoni|ribs|steak", "meat"),
    (r"turkey|duck|quail|kaadai", "poultry (quail, duck or turkey)"),
    (r"fish|fishes|fillet", "fish"), (r"prawns?|shrimps?|crabs?|lobsters?|squids?|calamari|octopus|"
                                      r"mussels?|clams?|oysters?|scallops?", "seafood"),
    (r"keema|kheema|qeema|kheema", "minced meat"), (r"liver|brain|trotters?|bone ?marrow", "meat (offal)"),
    (r"shawarma|shawarmas|seekh|galouti|haleem|nihari|paya|kaleji|bheja|boti", "a meat dish"),
    (r"gelatin|gelatine", "gelatin (from animals)"),
    # Tamil
    (r"[a-z]*kozhi[a-z]*|koli|kozi", "chicken (Tamil/Malayalam)"),
    (r"eral|era|iraal|yeral", "prawn (Tamil)"), (r"nandu|njandu|nhandu", "crab (Tamil/Malayalam)"),
    (r"meen|[a-z]+meen|karimeen|meenu", "fish (Tamil/Malayalam)"),
    (r"aatu|aattu|aadu|aattukari", "goat/mutton (Tamil)"),
    (r"kari", "meat (Tamil)"),
    (r"nethili|nethli|vanjaram|vaval|sura|kanava|kanavai|mathi|chaala|ayala|kilanga|paarai|koduva|"
     r"karuvadu|karuvaadu|nalli", "fish or seafood (Tamil)"),
    (r"chemmeen|chemmen|konju", "prawn (Malayalam)"), (r"erachi|irachi|erachi[a-z]*|kakka", "meat or shellfish (Malayalam)"),
    # Hindi / Urdu
    (r"gosht|ghosht|murgh|murg|machli|machhli|machchi|jhinga|jheenga|bakra|tangdi",
     "meat, chicken or fish (Hindi/Urdu)"),
    # Telugu / Kannada
    (r"kodi|koodi", "chicken (Telugu)"), (r"mamsam|mamsa|mamsaharam", "meat (Telugu)"),
    (r"royyalu|royya|chepa|chepala|peethala", "prawn, fish or crab (Telugu)"),
    (r"kori", "chicken (Kannada/Tulu)"), (r"mamsa", "meat (Kannada)"),
]
EGG: list[tuple[str, str]] = [
    (r"eggs?|omelettes?|omelets?|omlet|frittata|scrambled eggs?|bhurji egg|egg bhurji", "egg"),
    (r"muttai|mutta|anda|anday|ande|guddu|motte|mottai", "egg (Tamil/Malayalam/Hindi/Telugu)"),
]
ALLERGENS: dict[str, list[tuple[str, str]]] = {
    "peanut": [(r"peanuts?|groundnuts?|ground nuts?|satay|chikki", "peanut"),
               (r"verkadalai|verkadala|ver kadalai|[a-z]*kadalai|kadala|nilakadalai|kappalandi",
                "peanut or gram (Tamil/Malayalam; read as peanut to be safe)"),
               (r"moongphali|moongfali|mungfali|singdana|shengdana|palli|pallilu|kadlekai",
                "peanut (Hindi/Marathi/Telugu/Kannada)")],
    "tree_nut": [(r"cashews?|almonds?|walnuts?|pistachios?|hazelnuts?|pecans?|nuts|dry ?fruits?|praline",
                  "tree nuts"),
                 (r"kaju|badam|pista|akhrot|munthiri|mundhiri|[a-z]*mundiri|parippu ?mundiri|godambi",
                  "cashew, almond or other nuts (Hindi/Tamil/Telugu)"),
                 (r"korma|kurma|navratan|navratna|shahi|malai kofta",
                  "a gravy usually thickened with cashew paste")],
    "gluten": [(r"wheat|maida|atta|flour|bread|breads|buns?|pav|toast|pasta|spaghetti|macaroni|penne|"
                r"noodles?|maggi|ramen|burgers?|pizzas?|sandwich(es)?|wraps?|pita|croissants?|cakes?|"
                r"cookies?|biscuits?|rusk|brownies?|muffins?|pastry|pastries|puffs?|samosas?|kachori|"
                r"momos?|dumplings?|spring rolls?|manchurian|barley|oats|oat|dalia|daliya|seitan|semolina",
                "wheat"),
               (r"naan|naans|parotta|porotta|barotta|paratha|parantha|roti|rotis|chapati|chapathi|chappathi|"
                r"phulka|kulcha|puri|poori|bhatura|bhature|kothu|kottu|rumali|tandoori roti",
                "a wheat bread"),
               (r"rava|rawa|sooji|suji|semiya|seviyan|vermicelli|upma|kesari|sheera",
                "semolina or vermicelli (wheat)")],
    "dairy": [(r"milk|milky|cheese|cheesy|butter|buttery|cream|creamy|yogh?urt|curd|ghee|paneer|khoya|khoa|"
               r"kova|mawa|malai|rabri|rabdi|kulfi|lassi|raita|kheer|basundi|shrikhand|kalakand|peda|"
               r"rasmalai|ras ?malai|rasgulla|rasagulla|sandesh|makhani|makhana? ?kheer|makkhan|"
               r"ice ?creams?|milkshakes?|shakes?|latte|cappuccino|mocha|frappe|custard|gulab ?jamun|"
               r"jamun|payasam|cheesecake|tiramisu|mascarpone|mozzarella|cheddar|whey", "milk or a milk product"),
              (r"thayir|thair|thayiru|dahi|chaas|chaach|mor|moru|majjiga|paal|paalu|neyyi|nei|vennai|benne|"
               r"perugu|paneer", "milk, curd, buttermilk or ghee (Tamil/Hindi/Telugu/Kannada)")],
    "egg": [(r"eggs?|omelettes?|omelets?|muttai|mutta|anda|anday|guddu|motte|meringue", "egg")],
    "fish": [(r"fish|fishes|fillet|tuna|salmon|sardines?|mackerel|pomfret|surmai|bangda|rohu|katla|hilsa|"
              r"basa|anchov[a-z]*|seer", "fish"),
             (r"meen|[a-z]+meen|karimeen|nethili|nethli|vanjaram|vaval|sura|mathi|chaala|ayala|paarai|"
              r"karuvadu|karuvaadu|machli|machhli|machchi|chepa|chepala", "fish (Tamil/Malayalam/Hindi/Telugu)")],
    "shellfish": [(r"prawns?|shrimps?|crabs?|lobsters?|squids?|calamari|octopus|mussels?|clams?|oysters?|"
                   r"scallops?|seafood", "shellfish"),
                  (r"eral|era|iraal|yeral|nandu|njandu|chemmeen|chemmen|konju|jhinga|jheenga|royyalu|royya|"
                   r"kanava|kanavai|kakka|kallumakkai|kekda", "prawn, crab or squid (Tamil/Malayalam/Hindi/Telugu)")],
    "soy": [(r"soy|soya|soyabean|soybeans?|tofu|tempeh|edamame|miso|manchurian|schezwan|szechuan|"
             r"hakka|chop ?suey|chowmein|chow mein", "soy (soya, tofu or soy sauce)")],
    "sesame": [(r"sesame|til|gingelly|tahini|ellu|nuvvulu|nuvvula|hummus|ellu ?urundai", "sesame (til/ellu)")],
}
SWEET: list[tuple[str, str]] = [
    (r"sweets?|dessert|desserts|mithai|meetha|meethi|jaggery|gur|honey|syrup|caramel|chocolate|choco|candy|"
     r"toffee|fudge", "a sweet"),
    (r"sakkarai|chakkarai|sakarai|sarkarai|vellam|chakkera|bellada", "sugar or jaggery (Tamil/Telugu/Kannada)"),
    (r"kesari|halwa|halva|alwa|payasam|payasa|kheer|phirni|jalebi|jilebi|jangiri|imarti|gulab ?jamun|jamun|"
     r"rasmalai|ras ?malai|rasgulla|rasagulla|rasogolla|sandesh|laddu|laddoo|ladoo|ladu|barfi|burfi|peda|"
     r"kalakand|mysore ?pak|mysorepak|soan ?papdi|kaju katli|katli|chikki|adhirasam|athirasam|ariselu|"
     r"kozhukattai|modak|modakam|poli|obbattu|holige|puran ?poli|basundi|rabri|rabdi|shrikhand|kulfi|"
     r"falooda|faluda|double ka meetha|qubani|shahi tukda|malpua|ghevar|petha|boondi|unniyappam|"
     r"pradhaman|ada pradhaman|sheera|sheer khurma|kalkandu|pongali",
     "an Indian sweet"),
    (r"ice ?creams?|sundae|cakes?|pastry|pastries|brownies?|muffins?|donuts?|doughnuts?|cookies?|"
     r"cheesecake|tiramisu|mousse|pudding|custard|waffles?|pancakes?|nutella|milkshakes?|shakes?|"
     r"smoothies?|juices?|lassi|frappe|cold coffee|mojito|rose milk|badam milk|badam pal",
     "a sweet dish or sweet drink"),
]
HIGH_SODIUM: list[tuple[str, str]] = [
    (r"pickles?|oorugai|urugai|achar|achaar|avakaya|papads?|appalam|pappadam|papadum|karuvadu|karuvaadu|"
     r"dry fish|salted|chips|namkeen|mixture|instant noodles?|maggi", "a salty food (pickle, papad, dried fish)"),
]

# Phrases taken out before matching, by what they would otherwise wrongly trigger:
# (words that must appear for the phrase to be possible, pattern). The trigger keeps a
# long ingredient list from paying for patterns that cannot match it.
NOT_WHAT_IT_SAYS = [
    (("egg",), r"egg ?less|egg[- ]free|without eggs?|no eggs?|eggplants?|egg ?plant"),
    (("mutta",), r"muttai ?(kose|kosu|gose|cabbage)|mutta ?kose"),               # muttaikose = cabbage
    (("kari", "curry"), r"kari ?(patta|pata|leaves|leaf|veppilai|bevu|vepaku)|kariveppilai|karivepaku|"
                        r"curry ?leaves"),
    (("butter",), r"\b(peanut|almond|cashew|nut)\s+butter"),                     # still a nut, not dairy
    (("milk", "cream", "curd", "yog"), r"\b(coconut|almond|soy|soya|oat|cashew|rice|peanut)\s+"
                                         r"(milk|cream|curd|yogh?urt)"),
    (("masala",), r"\b(meat|chicken|mutton|fish|egg|kebab|biryani)\s+(curry\s+)?masala(\s+powder)?\b"),
    (("butter", "buckwheat", "tartar", "cracker", "sweet", "sugar", "lassi", "chaas"),
     r"butter\s*nut|buckwheat|cream of tartar|cream ?cracker|sweet ?corn|sweet ?potato|"
     r"sweet\s+(and|&|n)\s+sour|sweet ?lime|sugar ?free|no sugar|without sugar|salt lassi|masala chaas"),
    (("chicken", "keema", "kheema", "mutton", "fish", "shawarma", "seekh", "galouti", "kebab", "haleem", "65",
      "nugget", "cutlet"),
     r"\b(veg|vegetable|vegan|soya?|mock|plant[- ]based|jackfruit|kathal|mushroom|paneer|gobi|tofu|"
     r"haryali|hariyali|hara bhara|rajma|peas|green peas|chana|dal|corn|beetroot|banana|raw banana)"
     r"\s+(chicken|keema|kheema|mutton|fish|shawarma|seekh|galouti|kebab|haleem|65|nuggets?|cutlet)"),
    (("omel",), r"\b(eggless|vegan|chickpea|besan|tomato|moong|chilla|veg) ?(bread )?omel+et+e?s?"),
    (("turkey", "koli"), r"turkey ?berr(y|ies)|koli ?masala"),
    (("kozhukattai",), r"kara ?kozhukattai|kaara ?kozhukattai|uppu ?kozhukattai|ulundu ?kozhukattai|"
                       r"spicy ?kozhukattai"),
    (("boondi",), r"(kara|khara|masala) ?boondi|boondi ?raita"),
    (("flour",), r"\b(rice|ragi|corn|gram|besan|chickpea|millet|jowar|bajra|tapioca|arrowroot|potato|coconut|"
                 r"almond|urad|moong|bengal ?gram|kuttu|singhara|rajgira|sorghum|quinoa) ?flour"),
]
# What a removed phrase still says ("peanut butter" is still peanut, "soya chunks" still soy).
_KEEP = ("peanut", "almond", "cashew", "soy", "soya", "veg", "vegetable", "vegan", "mock",
         "jackfruit", "kathal", "mushroom", "paneer", "gobi", "tofu", "oat")

_NEG = [(keys, re.compile(p)) for keys, p in NOT_WHAT_IT_SAYS]


def _expand(frag: str) -> list[str]:
    """Every literal form of a fragment written in the small regex subset used above
    (alternation, optional pieces, one-letter classes). "[a-z]*" / "[a-z]+" become the
    wildcards "*" / "+", allowed only at the start or end of a one-word form."""
    from re import _parser as sre                     # stdlib; finite patterns only

    def seq(items) -> list[str]:
        out = [""]
        for op, av in items:
            out = [a + b for a in out for b in node(op, av)]
        return out

    def node(op, av) -> list[str]:
        if op is sre.LITERAL:
            return [chr(av)]
        if op is sre.BRANCH:
            return [x for alt in av[1] for x in seq(alt)]
        if op is sre.SUBPATTERN:
            return seq(av[-1])
        if op is sre.IN:
            chars = []
            for cop, cav in av:
                if cop is sre.LITERAL:
                    chars.append(chr(cav))
                elif cop is sre.RANGE and cav == (97, 122):
                    return ["\0"]                   # [a-z]: only used as a wildcard
                else:
                    raise ValueError(frag)
            return chars
        if op is sre.MAX_REPEAT:
            lo, hi, body = av
            inner = seq(body)
            if inner == ["\0"]:
                return ["*" if lo == 0 else "+"]
            if (lo, hi) == (0, 1):
                return [""] + inner
            raise ValueError(frag)
        if op is sre.AT:                              # \b: words are matched whole anyway
            return [""]
        raise ValueError(f"{frag}: {op}")

    return sorted(set(seq(sre.parse(frag))))


class _Matcher:
    """Whole-word phrase lookup plus a few compound-word rules (…kozhi…, …meen)."""

    def __init__(self, table):
        self.phrases: dict[tuple[str, ...], list[int]] = {}
        self.affix: list[tuple[str, str, int]] = []   # (mode, stem, entry): mode in pre/suf/in
        for i, (_, _, frag, _) in enumerate(table):
            for form in _expand(frag):
                lead, trail = form[:1] in "*+", form[-1:] in "*+"
                stem = form.strip("*+")
                if lead or trail:
                    if " " in stem:
                        raise ValueError(f"wildcard phrase: {form}")
                    mode = "in" if lead and trail else "suf" if lead else "pre"
                    min_extra = 1 if "+" in form else 0
                    self.affix.append((mode, stem, i) if not min_extra else (mode + "+", stem, i))
                else:
                    self.phrases.setdefault(tuple(stem.split()), []).append(i)
        self.heads: dict[str, list[int]] = {}
        for k in self.phrases:
            if len(k) > 1:
                self.heads.setdefault(k[0], []).append(len(k))
        stems = sorted({stem for _, stem, _ in self.affix}, key=len, reverse=True)
        self.affix_rx = re.compile("|".join(re.escape(x) for x in stems)) if stems else None

    def find(self, tokens: list[str]):
        """(entry index, matched words) for every hit, in text order."""
        n, phrases, heads = len(tokens), self.phrases, self.heads
        for a, tok in enumerate(tokens):
            for i in phrases.get((tok,), ()):
                yield i, tok
            for k in heads.get(tok, ()):
                if a + k <= n:
                    for i in phrases.get(tuple(tokens[a:a + k]), ()):
                        yield i, " ".join(tokens[a:a + k])
            if self.affix_rx is None or not self.affix_rx.search(tok):
                continue
            for mode, stem, i in self.affix:
                if (mode == "in" and stem in tok) or (mode == "suf" and tok.endswith(stem)) \
                        or (mode == "suf+" and tok.endswith(stem) and len(tok) > len(stem)) \
                        or (mode == "pre" and tok.startswith(stem)):
                    yield i, tok


_TABLE: list[tuple[str, str, str, str]] = (
    [("nonveg", "", f, g) for f, g in NONVEG]
    + [("egg", "egg", f, g) for f, g in EGG]
    + [("allergen", fam, f, g) for fam, rows in ALLERGENS.items() for f, g in rows]
    + [("sweet", "", f, g) for f, g in SWEET]
    + [("high_sodium", "", f, g) for f, g in HIGH_SODIUM]
)
_MATCH = _Matcher(_TABLE)


@dataclass(frozen=True)
class Hit:
    kind: str          # nonveg | egg | allergen | sweet | high_sodium
    family: str        # allergen family ("" when not an allergen)
    word: str          # the word as found
    gloss: str

    @property
    def words(self) -> str:
        """Plain words for the person: "Kozhi (Tamil/Malayalam) means chicken",
        "Kesari is an Indian sweet", or "The name says “chicken”"."""
        w = self.word.strip()
        base = self.gloss.split(" (")[0]
        if w.rstrip("s") in base.replace(",", " ").split() or w in base.replace(",", " ").split():
            return f"The name says “{w}”"
        if base.startswith(("a ", "an ")):
            return f"{w.capitalize()} is {self.gloss}"
        return f"{w.capitalize()} means {self.gloss}"


@dataclass(frozen=True)
class Reading:
    hits: tuple[Hit, ...] = field(default_factory=tuple)

    @property
    def nonveg(self) -> list[Hit]:
        return [h for h in self.hits if h.kind in ("nonveg", "egg")]

    @property
    def egg(self) -> bool:
        return any(h.kind == "egg" for h in self.hits)

    @property
    def allergens(self) -> set[str]:
        out = {h.family for h in self.hits if h.kind == "allergen"}
        if self.egg:
            out.add("egg")
        return out

    def allergen_hits(self, family: str) -> list[Hit]:
        return [h for h in self.hits if (h.kind == "allergen" and h.family == family)
                or (family == "egg" and h.kind == "egg")]

    @property
    def sweet(self) -> list[Hit]:
        return [h for h in self.hits if h.kind == "sweet"]

    @property
    def high_sodium(self) -> list[Hit]:
        return [h for h in self.hits if h.kind == "high_sodium"]

    @property
    def any(self) -> bool:
        return bool(self.hits)


def normalise(text: str) -> str:
    t = (text or "").lower().replace("&", " and ")
    return re.sub(r"[^a-z0-9]+", " ", t).strip()


def _strip_negations(t: str) -> str:
    def keep(m: re.Match) -> str:
        first = m.group(0).split()[0] if m.group(0).split() else ""
        return f" {first} " if first in _KEEP else " "
    for keys, rx in _NEG:
        if any(k in t for k in keys):
            t = rx.sub(keep, t)
    return t


@lru_cache(maxsize=20000)
def read(text: str) -> Reading:
    """Everything the words in `text` say. Cached: menus repeat the same names."""
    tokens = _strip_negations(normalise(text)).split()
    hits, seen = [], set()
    for i, words in _MATCH.find(tokens):
        kind, fam, _, gloss = _TABLE[i]
        key = (kind, fam, words)
        if key not in seen:
            seen.add(key)
            hits.append(Hit(kind, fam, words, gloss))
    return Reading(tuple(hits))


def read_all(*texts) -> Reading:
    hits: list[Hit] = []
    for t in texts:
        if isinstance(t, (list, tuple)):
            t = " ; ".join(str(x) for x in t)
        if t:
            hits += [h for h in read(str(t)).hits if h not in hits]
    return Reading(tuple(hits))


# Single words, for readers that work word by word (ingredients.py pantry tokens).
def _single_words(rows) -> set[str]:
    return {w for frag, _ in rows for w in _expand(frag) if re.fullmatch(r"[a-z]+", w)}


NONVEG_WORDS = _single_words(NONVEG) | _single_words(EGG)
ALLERGEN_WORDS = {fam: _single_words(rows) for fam, rows in ALLERGENS.items()}
