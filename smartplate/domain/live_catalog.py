"""Plan from the user's real Swiggy restaurants instead of the sample catalogue.

When Swiggy is connected with a delivery address, `refresh(user_id)` reads the live
menus of the user's live favourites (or, with none, the best-rated results of a live
search) and stores them as the user's own catalogue (restaurants.city = "live:<id>",
source = "live"). The planner then plans only from those dishes, never mixing in the
sample ones. Prices come from Swiggy. Nutrition is ESTIMATED from the dish name
(`estimate`), and every live dish carries `nutrition_estimated = 1` so the UI and the
plan say so. The delivery fee is LIVE_DELIVERY_FEE_ESTIMATE until a real cart bill from
that restaurant has been seen; from then on the planner uses the fee Swiggy billed
(`record_fee`, `with_learned_fees`) and says so.

Without live menus the plan has no restaurant dishes at all: it offers home-cooked meals
and `source_for` says what is needed for real dishes. (The sample catalogue is test
fixture data only.)
"""
import re
from concurrent.futures import ThreadPoolExecutor

from .. import clock, config, db
from . import food_lexicon

# ₹; Swiggy's average fee + GST until a real cart bill shows the fee (domain/fees.DELIVERY)
LIVE_DELIVERY_FEE_ESTIMATE = 17.11
MAX_PLACES = 4                        # restaurants read per refresh (favourites first)
DEFAULT_QUERY = "meals"               # used only when the user has no live favourites

# Dish-name templates → typical per-portion values (Indian restaurant portions; IFCT-style
# ballpark figures). First match wins, so specific names come before general ones.
# allergens are what the dish name implies; a name never proves a dish is allergen-free.
TEMPLATES = [
    (r"curd rice|thayir", dict(kcal=420, protein_g=11, carbs_g=68, fat_g=11, sugar_g=6, allergens=["dairy"])),
    (r"biryani", dict(kcal=750, protein_g=26, carbs_g=95, fat_g=28, sugar_g=4, allergens=[])),
    (r"fried rice|pulao|pulav", dict(kcal=620, protein_g=14, carbs_g=95, fat_g=18, sugar_g=4, allergens=["soy"])),
    (r"noodle|hakka|chowmein", dict(kcal=640, protein_g=15, carbs_g=92, fat_g=22, sugar_g=6,
                                    allergens=["gluten", "soy"])),
    (r"sambar rice|bisi bele|lemon rice|tamarind rice|puliyogare",
     dict(kcal=520, protein_g=12, carbs_g=88, fat_g=12, sugar_g=4, allergens=[])),
    (r"meals|thali", dict(kcal=820, protein_g=22, carbs_g=125, fat_g=24, sugar_g=8, allergens=["dairy"])),
    (r"mini tiffin|tiffin", dict(kcal=560, protein_g=14, carbs_g=88, fat_g=16, sugar_g=6, allergens=["dairy"])),
    (r"peanut", dict(kcal=480, protein_g=14, carbs_g=60, fat_g=20, sugar_g=4, allergens=["peanut"])),
    (r"pongal", dict(kcal=480, protein_g=12, carbs_g=62, fat_g=20, sugar_g=2, allergens=["dairy"])),
    (r"masala dosa|dosa|dosai|uttapam|uthappam",
     dict(kcal=420, protein_g=9, carbs_g=62, fat_g=14, sugar_g=3, allergens=[])),
    (r"idli|idly", dict(kcal=300, protein_g=9, carbs_g=58, fat_g=3, sugar_g=2, allergens=[])),
    (r"vada|vadai", dict(kcal=330, protein_g=10, carbs_g=34, fat_g=17, sugar_g=1, allergens=[])),
    (r"upma|poha", dict(kcal=350, protein_g=8, carbs_g=55, fat_g=11, sugar_g=3, allergens=["gluten"])),
    (r"parotta|paratha|roti|chapati|naan|kulcha|puri|poori",
     dict(kcal=560, protein_g=14, carbs_g=78, fat_g=21, sugar_g=4, allergens=["gluten"])),
    (r"paneer", dict(kcal=620, protein_g=24, carbs_g=40, fat_g=38, sugar_g=8, allergens=["dairy"])),
    (r"chicken", dict(kcal=640, protein_g=38, carbs_g=40, fat_g=34, sugar_g=5, allergens=[], veg=False)),
    (r"mutton|lamb", dict(kcal=700, protein_g=36, carbs_g=38, fat_g=42, sugar_g=4, allergens=[], veg=False)),
    (r"fish|meen", dict(kcal=520, protein_g=34, carbs_g=30, fat_g=26, sugar_g=3, allergens=["fish"], veg=False)),
    (r"prawn|shrimp|crab", dict(kcal=500, protein_g=30, carbs_g=32, fat_g=24, sugar_g=3,
                                allergens=["shellfish"], veg=False)),
    (r"egg|omelette|omelet", dict(kcal=420, protein_g=20, carbs_g=34, fat_g=22, sugar_g=3,
                                  allergens=["egg"], veg=False)),
    (r"chole|rajma|dal|daal|sambar", dict(kcal=480, protein_g=17, carbs_g=68, fat_g=14, sugar_g=5, allergens=[])),
    (r"sandwich|burger|pizza|wrap|roll|bun",
     dict(kcal=560, protein_g=18, carbs_g=62, fat_g=24, sugar_g=7, allergens=["gluten", "dairy"])),
    (r"salad|bowl", dict(kcal=380, protein_g=16, carbs_g=40, fat_g=16, sugar_g=8, allergens=[])),
    (r"coffee|tea|chai|juice|lassi|shake|ice cream|sweet|halwa|payasam|kheer|jamun|cake",
     dict(kcal=250, protein_g=4, carbs_g=40, fat_g=8, sugar_g=30, allergens=["dairy"], dessert=True)),
]
# Allergen, meat, egg, sugar and salt words now come from the one shared lexicon
# (domain/food_lexicon, sp-grill #60); these names stay for older imports.
ALLERGEN_WORDS = {w: fam for fam, words in food_lexicon.ALLERGEN_WORDS.items() for w in words}
NONVEG_WORDS = "|".join(sorted(food_lexicon.NONVEG_WORDS))
SEARCH_PAGES = 2                 # pages of the broad search read to pick places when none are saved
NEAR_KM = 3.0                    # places this close come first
MENU_READERS = 3                 # live menus read at the same time on a refresh
# Swiggy's own menu sections that hold sides, breads, drinks and sweets, not a whole meal.
# A dish filed there isn't planned as a meal by itself, unless the person pools it.
# Live finding (9 Oct 2026): real menus file curries, dals, soups, starters and snacks in
# their own sections too ("Indian Gravies", "Dal", "Soups", "Starters", "Chaat").
SIDE_SECTIONS = re.compile(r"\b(breads?|rotis?|sides?|accompaniments?|beverages?|drinks?|juices?|shakes?|desserts?|"
                           r"sweets?|extras?|add[- ]?ons?|raitas?|papads?|condiments?|dips?|ice creams?|soups?|"
                           r"starters?|appeti[sz]ers?|chaats?|snacks?|quick ?bites|pakoras?|gravies|gravy|curries|"
                           r"subzi|sabzi|dals?|evening|fries|curry|podis?|pickles?|retail|groceries|packaged|"
                           r"store|masalas? powders?)\b", re.I)
# …unless the section says it's a whole meal ("Curry Rice Combos", "Meals & Thalis").
MEAL_SECTIONS = re.compile(r"\b(meals?|combos?|thalis?|bowls?|platters?)\b", re.I)
# Dishes that are a side whatever section they sit in (Andhra Mess files Sambar under
# "Veg curry andhra style"; live finding, 9 Oct 2026).
# A pack sold by weight ("Idli Podi (150 Gm)") is shopping, not a meal. Volume isn't: a
# "Sambar Rice (500ml)" is a portion.
PACK_NAMES = re.compile(r"\b(podi|powder|pickle|masala mix|premix)\b|\b\d+\s*(gm|gms|g|grams?|kg)\b", re.I)
SIDE_NAMES = re.compile(r"^\s*(extra\s+.*|(plain\s+)?(sambar|rasam|raita|curd|papad|pickle|chutney|salna|"
                        r"kurma|gravy|dal|ghee|butter)(\s*\(.*\))?|(steamed|plain|jeera|white)\s+rice(\s*\(.*\))?|"
                        r"([\w-]+\s+){0,3}(roti|chapati|chapathi|phulka|naan|kulcha|pav)s?"
                        r"(\s*\(.*\))?)\s*$", re.I)
# A whole plate for lunch, from its section or its name ("Meals", "Thali", "Sambar Rice").
MAIN_WORDS = re.compile(r"\b(meals?|thalis?|rice|biryani|briyani|pulao|pulav|khichdi|combos?|bowls?|platters?)\b", re.I)
# Sections that say a dish is a breakfast ("Tiffin", "South Indian Breakfast", "Dosa").
BREAKFAST_SECTIONS = re.compile(r"\b(breakfast|tiffins?|idl[iy]s?|dosas?|uttap+ams?|pongal|upma|poha|parathas?|"
                                r"morning)\b", re.I)
TAG_WORDS = {"biryani": "heavy", "meals": "comfort", "thali": "comfort", "salad": "light", "idli": "light"}


_DESSERT = next(values for pattern, values in TEMPLATES if values.get("dessert"))


def _veg(veg, words: food_lexicon.Reading, template_veg=True, recipe_nonveg=False) -> tuple[int, int]:
    """(veg, veg_known). Fail-closed (sp-grill #38): a name that says meat or egg is
    non-veg whatever the mark says; with no mark from Swiggy the dish is NOT veg
    (stored 0) and marked unknown, so every veg check excludes it."""
    if words.nonveg or template_veg is False:
        return 0, 1
    if veg is None:
        return 0, 0 if not recipe_nonveg else 1
    return (1, 1) if bool(veg) else (0, 1)


def _provider(provider: dict | None) -> dict:
    """What the platform itself said (Swiggy's menus carry neither today: see
    docs/safety/fail-closed.md). Allergens it lists are trusted as given; its kcal wins."""
    provider = provider or {}
    out = {}
    if isinstance(provider.get("allergens"), list):
        out["allergens"] = {str(a) for a in provider["allergens"]}
    if isinstance(provider.get("kcal"), (int, float)) and not isinstance(provider.get("kcal"), bool):
        out["kcal"] = float(provider["kcal"])
    return out


def _recipe(name: str) -> dict | None:
    from . import dish_library
    try:
        return dish_library.infer(name)
    except Exception:                           # the library is an aid, never a reason to fail a refresh
        return None


def estimate(name: str, veg, description: str | None = None, provider: dict | None = None) -> dict | None:
    """Estimated nutrition, implied allergens and veg flag for a dish name; None when the
    name matches no template (the planner then doesn't use the dish).

    Sweet words are read first (usability finding 4: "Sakkarai Pongal" is sweet, not the
    savoury pongal template). Allergens are the template's, plus every lexicon word in the
    name and description, plus what matching recipes in the recipe library hold: each can
    only add one."""
    low = name.lower()
    words = food_lexicon.read_all(name, description or "")
    # The template comes from the NAME only: a description can add restrictions (an
    # allergen, meat, sugar) but never turn an unknown dish into an estimated one.
    values = (_DESSERT if food_lexicon.read(name).sweet
              else next((v for p, v in TEMPLATES if re.search(p, low)), None))
    if values is None:
        return None
    out = {k: v for k, v in values.items() if k not in ("allergens", "veg", "dessert")}
    recipe = _recipe(name)
    allergens_ = set(values["allergens"]) | words.allergens | set((recipe or {}).get("allergens") or [])
    given = _provider(provider)
    if "allergens" in given:
        allergens_ |= given["allergens"]
    if "kcal" in given:
        out["kcal"] = given["kcal"]
    veg_flag, veg_known = _veg(veg, words, values.get("veg", True), bool(recipe and recipe["nonveg"]))
    tags = {t for w, t in TAG_WORDS.items() if w in low}
    if values.get("dessert") or words.sweet or (recipe and recipe["sweet"]):
        tags.add("dessert")
        out["sugar_g"] = max(out.get("sugar_g", 0), _DESSERT["sugar_g"])
    if words.high_sodium:
        tags.add("high_sodium")
    return {**out, "allergens": sorted(allergens_), "veg": veg_flag, "veg_known": veg_known,
            "tags": sorted(tags), "nutrition_known": 1,
            "safety_basis": "provider" if "allergens" in given else "template"}


def unestimated(name: str, veg, description: str | None = None, provider: dict | None = None) -> dict:
    """A dish the person put in a meal pool whose name matches no template: planned with
    NO nutrition figures (nutrition_known = 0, sugar unknown so it fails diabetes), never
    made-up ones. Allergens, meat and egg are read from the name (shared lexicon) and the
    recipe library, and the veg mark from Swiggy; a missing mark means "unknown"."""
    words = food_lexicon.read_all(name, description or "")
    recipe = _recipe(name)
    given = _provider(provider)
    allergens_ = words.allergens | set((recipe or {}).get("allergens") or []) | given.get("allergens", set())
    veg_flag, veg_known = _veg(veg, words, True, bool(recipe and recipe["nonveg"]))
    tags = set()
    if words.sweet or (recipe and recipe["sweet"]):
        tags.add("dessert")
    if words.high_sodium:
        tags.add("high_sodium")
    basis = "provider" if "allergens" in given else "recipe" if recipe else "name" if words.any else "none"
    return {"kcal": given.get("kcal"), "protein_g": None, "carbs_g": None, "fat_g": None, "sugar_g": None,
            "nutrition_known": 0, "allergens": sorted(allergens_), "veg": veg_flag, "veg_known": veg_known,
            "tags": sorted(tags), "safety_basis": basis}


# --------------------------------------------------------------------------- #
# The user's live catalogue
# --------------------------------------------------------------------------- #
def city_key(user_id: int) -> str:
    return f"live:{user_id}"


def has_live(user_id: int) -> bool:
    """True only when the live catalogue was read for the user's chosen delivery address."""
    with db.cursor() as cur:
        if cur.execute("SELECT 1 FROM restaurants WHERE city=? LIMIT 1", (city_key(user_id),)).fetchone() is None:
            return False
        # A catalogue read before the fail-closed checks (no safety_basis) is read again:
        # its veg marks defaulted to veg when Swiggy gave none (sp-grill #38).
        if cur.execute("SELECT 1 FROM menu_items m JOIN restaurants r ON r.id=m.restaurant_id "
                       "WHERE r.city=? AND m.safety_basis IS NULL LIMIT 1", (city_key(user_id),)).fetchone():
            return False
        state = cur.execute("SELECT address_id FROM live_catalog_state WHERE user_id=?", (user_id,)).fetchone()
    return bool(state) and _for_current_address(user_id, state["address_id"])


def _for_current_address(user_id: int, read_for: str | None) -> bool:
    current = _address_id(user_id)
    return bool(current) and str(read_for or "") == str(current)


def clear(user_id: int) -> None:
    with db.cursor() as cur:
        ids = [r["id"] for r in cur.execute("SELECT id FROM restaurants WHERE city=?", (city_key(user_id),))]
        if ids:
            marks = ",".join("?" * len(ids))
            cur.execute(f"DELETE FROM menu_items WHERE restaurant_id IN ({marks})", ids)
            cur.execute(f"DELETE FROM restaurants WHERE id IN ({marks})", ids)
        cur.execute("DELETE FROM live_catalog_state WHERE user_id=?", (user_id,))


def _places(user_id: int) -> list[dict]:
    """Where the plan's dishes come from: every restaurant in a meal pool, then the saved
    places (or, with neither, the best-rated results of a live search)."""
    from ..integrations import swiggy_live
    from . import meal_pools
    pooled = meal_pools.restaurants(user_id)
    favs = swiggy_live.live_favourites(user_id)
    if pooled or favs:
        out, seen = [], set()
        for p in pooled + favs[:MAX_PLACES]:
            if p["id"] not in seen and len(out) < MAX_PLACES + meal_pools.MAX_PLACES:
                seen.add(p["id"])
                out.append(p)
        return out
    # Nothing saved yet: the best nearby results of a broad search, two pages of it. Ads and
    # closed places are left out, and close places come first (live finding, 9 Oct 2026:
    # the first page is mostly sponsored).
    found, offset = [], 0
    for _ in range(SEARCH_PAGES):
        page = swiggy_live.search_live_restaurants(user_id, DEFAULT_QUERY, offset)
        found += page["restaurants"]
        if not page.get("has_more"):
            break
        offset = page["next_offset"]
    open_ = [r for r in found if str(r.get("availability") or "OPEN").upper() != "CLOSED"]
    organic = [r for r in open_ if not r.get("sponsored")] or open_

    def near(r):
        try:
            return float(r.get("distance")) > NEAR_KM
        except (TypeError, ValueError):
            return False
    organic.sort(key=lambda r: (near(r), -float(r.get("rating") or 0)))
    return [{"id": r["id"], "name": r["name"]} for r in organic[:MAX_PLACES]]


def section_tags(tags, sections, name: str = "") -> list[str]:
    """A dish's tags plus what its Swiggy menu sections (and a bare side's name) say:
    "side" (not a meal by itself) and "breakfast"."""
    out, sections = set(tags or []), sections or []
    if any(SIDE_SECTIONS.search(c) and not MEAL_SECTIONS.search(c) for c in sections) or SIDE_NAMES.match(name or "") \
            or PACK_NAMES.search(name or ""):
        out.add("side")
    if any(BREAKFAST_SECTIONS.search(c) for c in sections):
        out.add("breakfast")
    if "side" not in out and (MAIN_WORDS.search(name or "") or any(MAIN_WORDS.search(c) for c in sections)):
        out.add("main")
    return sorted(out)


def refresh(user_id: int) -> dict:
    """Read live menus and replace the user's live catalogue. Raises SwiggyError
    (409 when not connected) and keeps the previous catalogue when Swiggy fails."""
    from ..integrations import swiggy_live
    from ..integrations.swiggy_connect import SwiggyError
    from . import meal_pools
    address_id = _address_id(user_id)
    places = _places(user_id)
    pooled = meal_pools.keys(user_id)
    menus, skipped = [], []

    def read(place):
        try:
            return swiggy_live.live_menu(user_id, str(place["id"]), place["name"], screen=False), None
        except SwiggyError as exc:
            return None, exc

    # A real menu is several pages (live finding, 9 Oct 2026: ~3 s a page), so read a few
    # places at once rather than one after another.
    with ThreadPoolExecutor(max_workers=MENU_READERS) as pool:
        results = list(pool.map(read, places))
    for place, (menu, exc) in zip(places, results):
        if exc is None:
            menus.append(menu)
        elif exc.code in ("swiggy_not_connected", "swiggy_auth_expired"):
            raise exc
        else:
            skipped.append({"name": place["name"], "why": str(exc)})
    rows = []
    for m in menus:
        r = m["restaurant"]
        dishes = []
        for it in m["items"]:
            if it.get("in_stock") is False or not it.get("price"):
                continue
            est = estimate(it["name"], it.get("veg"), it.get("description"), it.get("provider_facts"))
            if not est and (str(r["id"]), str(it["id"])) in pooled:
                # their pick: planned, nutrition unknown
                est = unestimated(it["name"], it.get("veg"), it.get("description"), it.get("provider_facts"))
            if not est:
                continue
            est = {**est, "tags": section_tags(est["tags"], it.get("categories"), it["name"])}
            dishes.append({**est, "name": it["name"], "price": float(it["price"]), "provider_item_id": it["id"]})
        if dishes:
            rows.append((r, dishes))
    if not rows:
        raise SwiggyError("Swiggy's menus had no dishes Ziggy can plan with yet. "
                          "Add a favourite restaurant from live search and try again.")
    from . import fees
    fees.reprice_guard(_current_prices(user_id), {(str(r["id"]), str(d["provider_item_id"])): d["price"]
                                                  for r, dishes in rows for d in dishes})
    clear(user_id)
    n_dishes = 0
    with db.cursor() as cur:
        for r, dishes in rows:
            rid, rating = _insert_restaurant(cur, user_id, r)
            for d in dishes:
                _insert_dish(cur, rid, rating, d)
                n_dishes += 1
        cur.execute("INSERT INTO live_catalog_state(user_id, fetched_ts, restaurants, dishes, address_id) "
                    "VALUES (?,?,?,?,?)",
                    (user_id, clock.now().isoformat(timespec="minutes"), len(rows), n_dishes, address_id))
    return {**source_for(user_id), "skipped": skipped}


def _current_prices(user_id: int) -> dict:
    """{(Swiggy restaurant id, Swiggy item id): rupees} of the live catalogue being replaced."""
    with db.cursor() as cur:
        rows = cur.execute("SELECT r.provider_id, m.provider_item_id, m.price FROM menu_items m "
                           "JOIN restaurants r ON r.id=m.restaurant_id WHERE r.city=?", (city_key(user_id),)).fetchall()
    return {(str(r["provider_id"]), str(r["provider_item_id"])): r["price"] for r in rows}


def _rating(r: dict) -> float:
    try:
        return float(r.get("rating") or 4.0)
    except (TypeError, ValueError):
        return 4.0


def _insert_restaurant(cur, user_id: int, r: dict) -> tuple[int, float]:
    rating = _rating(r)
    cur.execute("INSERT INTO restaurants(name, city, rating, cuisines, delivery_fee, eta_min, is_open, flaky, "
                "source, provider_id) VALUES (?,?,?,?,?,?,1,0,'live',?)",
                (r["name"], city_key(user_id), rating, "[]", LIVE_DELIVERY_FEE_ESTIMATE, 35, str(r["id"])))
    return cur.lastrowid, rating


def _insert_dish(cur, rid: int, rating: float, d: dict) -> int:
    # The columns are NOT NULL: an unknown figure is stored as 0 *with* nutrition_known = 0,
    # which the safety checker reads as unknown (allergens._sugar_verdict), never as 0 g.
    n = {k: (d.get(k) if d.get(k) is not None else 0) for k in ("kcal", "protein_g", "carbs_g", "fat_g", "sugar_g")}
    cur.execute("INSERT INTO menu_items(restaurant_id, name, price, cuisine, kcal, protein_g, carbs_g, "
                "fat_g, sugar_g, veg, allergens, tags, carbon_kg, item_rating, popularity, reviews, "
                "source, provider_item_id, nutrition_estimated, nutrition_known, veg_known, safety_basis) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'[]','live',?,1,?,?,?)",
                (rid, d["name"], d["price"], "mixed", n["kcal"], n["protein_g"], n["carbs_g"],
                 n["fat_g"], n["sugar_g"], d["veg"], db.jd(d["allergens"]), db.jd(d["tags"]),
                 1.0, rating, 0.5, d["provider_item_id"], d.get("nutrition_known", 1),
                 d.get("veg_known", 1), d.get("safety_basis") or "none"))
    return cur.lastrowid


def add_dish(user_id: int, restaurant: dict, item: dict) -> int:
    """Put one dish the person picked from a live menu into their live catalogue (for the
    current address) and return its local id. Reads the whole catalogue first when there
    is none yet. The dish keeps Swiggy's price; nutrition is estimated from its name, or
    left unknown when no template fits."""
    from ..integrations.swiggy_connect import SwiggyError
    if not has_live(user_id):
        refresh(user_id)
    if not item.get("price"):
        raise SwiggyError("Swiggy didn't show a price for this dish. Pick another or order it in Swiggy.")
    city = city_key(user_id)
    with db.cursor() as cur:
        row = cur.execute("SELECT m.id FROM menu_items m JOIN restaurants r ON r.id=m.restaurant_id "
                          "WHERE r.city=? AND r.provider_id=? AND m.provider_item_id=?",
                          (city, str(restaurant["id"]), str(item["id"]))).fetchone()
        if row:
            return row["id"]
        place = cur.execute("SELECT id, rating FROM restaurants WHERE city=? AND provider_id=?",
                            (city, str(restaurant["id"]))).fetchone()
        if place:
            rid, rating = place["id"], place["rating"]
        else:
            rid, rating = _insert_restaurant(cur, user_id, restaurant)
        est = (estimate(item["name"], item.get("veg"), item.get("description"), item.get("provider_facts"))
               or unestimated(item["name"], item.get("veg"), item.get("description"), item.get("provider_facts")))
        est = {**est, "tags": section_tags(est["tags"], item.get("categories"), item["name"])}
        local = _insert_dish(cur, rid, rating, {**est, "name": item["name"], "price": float(item["price"]),
                                                "provider_item_id": str(item["id"])})
        cur.execute("UPDATE live_catalog_state SET dishes = dishes + 1 WHERE user_id=?", (user_id,))
    return local


# --------------------------------------------------------------------------- #
# Delivery fees learned from real cart bills
# --------------------------------------------------------------------------- #
def record_fee(user_id: int, address_id: str, provider_id: str, restaurant_name: str,
               fee: float | None) -> None:
    """Remember the delivery fee Swiggy billed for this restaurant and address. A bill
    without a delivery line (fee None) teaches nothing."""
    if fee is None or not provider_id or not address_id:
        return
    with db.cursor() as cur:
        cur.execute("INSERT OR REPLACE INTO swiggy_delivery_fees(user_id, address_id, provider_id, restaurant_name, "
                    "delivery_fee, seen_ts) VALUES (?,?,?,?,?,?)",
                    (user_id, str(address_id), str(provider_id), restaurant_name or "", round(float(fee), 2),
                     clock.now().isoformat(timespec="minutes")))


def _address_id(user_id: int) -> str | None:
    with db.cursor() as cur:
        try:
            row = cur.execute("SELECT address_id FROM swiggy_connections WHERE user_id=?", (user_id,)).fetchone()
        except Exception:                      # the Swiggy tables don't exist until first connect
            return None
    return row["address_id"] if row else None


def learned_fees(user_id: int) -> dict:
    """{Swiggy restaurant id: {"fee", "seen"}} for the user's current delivery address."""
    address_id = _address_id(user_id)
    if not address_id:
        return {}
    with db.cursor() as cur:
        rows = cur.execute("SELECT provider_id, delivery_fee, seen_ts FROM swiggy_delivery_fees "
                           "WHERE user_id=? AND address_id=?", (user_id, address_id)).fetchall()
    return {r["provider_id"]: {"fee": r["delivery_fee"], "seen": r["seen_ts"]} for r in rows}


def fee_view(provider_id, fallback: float, fees: dict) -> dict:
    """What the plan says about one live restaurant's delivery fee."""
    known = fees.get(str(provider_id)) if provider_id else None
    if known:
        return {"amount": known["fee"], "estimated": False, "seen": known["seen"]}
    return {"amount": fallback, "estimated": True}


def with_learned_fees(user_id: int, items: list[dict]) -> list[dict]:
    """Live menu rows priced with the fees from the user's real Swiggy bills (delivery,
    platform fee, packaging, GST), or labelled estimates until a bill is seen (domain/fees)."""
    from . import fees
    return fees.annotate(user_id, items)


def source_for(user_id: int, connected: bool | None = None) -> dict:
    """What the plan's restaurant dishes come from, in words the user can act on.

    kind "live": real Swiggy menus read for the chosen address. Otherwise kind "none"
    (no restaurant dishes; only home-cooked meals) with `needs` saying the next step:
    "connect" Swiggy, choose an "address", or "sync" (connected with an address: the app
    reads the live menus itself). Test fixture runs report kind "sample" instead of "none"."""
    from ..integrations import swiggy_connect
    with db.cursor() as cur:
        row = cur.execute("SELECT * FROM live_catalog_state WHERE user_id=?", (user_id,)).fetchone()
    if row and _for_current_address(user_id, row["address_id"]):
        return {"kind": "live", "fetched": row["fetched_ts"], "restaurants": row["restaurants"],
                "dishes": row["dishes"],
                "label": f"Live Swiggy menus · {row['restaurants']} restaurant{'s' if row['restaurants'] != 1 else ''}, "
                         f"{row['dishes']} dishes",
                "note": "Prices from Swiggy. Nutrition is estimated from dish names; delivery fees are estimates "
                        "until the cart is checked."}
    status = swiggy_connect.status(user_id) if connected is None else {"connected": connected}
    connected = bool(status.get("connected"))
    has_address = bool(_address_id(user_id))
    needs = "sync" if connected and has_address else "address" if connected else "connect"
    kind = "sample" if config.FIXTURE_DATA else "none"
    note = {"sync": ("Your live menus were read for another delivery address. Reading real dishes for the "
                     "address you chose." if row else "Reading real dishes from Swiggy for your address."),
            "address": "Pick a delivery address to see real dishes.",
            "connect": ("Connect Swiggy and pick an address to see real dishes." if config.SWIGGY_REDIRECT_APPROVED
                        else "Connecting Swiggy from Ziggy is waiting for Swiggy's approval. "
                             "Until then, plans show home-cooked meals only.")}[needs]
    out = {"kind": kind, "connected": connected, "needs": needs, "stale_address": bool(row),
           "label": "Sample dishes (test data)" if kind == "sample" else "No restaurant dishes yet",
           "note": note}
    return out