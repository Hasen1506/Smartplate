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

from .. import clock, config, db

LIVE_DELIVERY_FEE_ESTIMATE = 35.0     # ₹; the real fee comes from the cart bill
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
# Words that always add an allergen, whatever template matched.
ALLERGEN_WORDS = {"peanut": "peanut", "groundnut": "peanut", "cashew": "tree_nut", "badam": "tree_nut",
                  "almond": "tree_nut", "paneer": "dairy", "cheese": "dairy", "butter": "dairy",
                  "ghee": "dairy", "curd": "dairy", "cream": "dairy", "egg": "egg", "prawn": "shellfish",
                  "fish": "fish", "soya": "soy", "wheat": "gluten", "maida": "gluten", "sesame": "sesame"}
NONVEG_WORDS = r"chicken|mutton|lamb|fish|meen|prawn|shrimp|crab|egg|omelet|keema|kheema|beef|pork"
TAG_WORDS = {"biryani": "heavy", "meals": "comfort", "thali": "comfort", "salad": "light", "idli": "light"}


def estimate(name: str, veg) -> dict | None:
    """Estimated nutrition, implied allergens and veg flag for a dish name; None when the
    name matches no template (the planner then doesn't use the dish)."""
    low = name.lower()
    for pattern, values in TEMPLATES:
        if re.search(pattern, low):
            out = {k: v for k, v in values.items() if k not in ("allergens", "veg", "dessert")}
            allergens_ = set(values["allergens"])
            allergens_ |= {a for w, a in ALLERGEN_WORDS.items() if w in low}
            veg_flag = values.get("veg", True) and not re.search(NONVEG_WORDS, low)
            if veg is not None:                      # Swiggy's own flag wins over the name
                veg_flag = bool(veg)
            tags = sorted({t for w, t in TAG_WORDS.items() if w in low} | ({"dessert"} if values.get("dessert") else set()))
            return {**out, "allergens": sorted(allergens_), "veg": 1 if veg_flag else 0, "tags": tags}
    return None


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
    from ..integrations import swiggy_live
    favs = swiggy_live.live_favourites(user_id)
    if favs:
        return favs[:MAX_PLACES]
    found = swiggy_live.search_live_restaurants(user_id, DEFAULT_QUERY)["restaurants"]
    found = [r for r in found if str(r.get("availability") or "OPEN").upper() != "CLOSED"]
    found.sort(key=lambda r: -float(r.get("rating") or 0))
    return [{"id": r["id"], "name": r["name"]} for r in found[:MAX_PLACES]]


def refresh(user_id: int) -> dict:
    """Read live menus and replace the user's live catalogue. Raises SwiggyError
    (409 when not connected) and keeps the previous catalogue when Swiggy fails."""
    from ..integrations import swiggy_live
    from ..integrations.swiggy_connect import SwiggyError
    address_id = _address_id(user_id)
    places = _places(user_id)
    menus, skipped = [], []
    for place in places:
        try:
            menus.append(swiggy_live.live_menu(user_id, str(place["id"]), place["name"]))
        except SwiggyError as exc:
            if exc.code in ("swiggy_not_connected", "swiggy_auth_expired"):
                raise
            skipped.append({"name": place["name"], "why": str(exc)})
    rows = []
    for m in menus:
        r = m["restaurant"]
        dishes = []
        for it in m["items"]:
            if it.get("in_stock") is False or not it.get("price"):
                continue
            est = estimate(it["name"], it.get("veg"))
            if not est:
                continue
            dishes.append({**est, "name": it["name"], "price": float(it["price"]), "provider_item_id": it["id"]})
        if dishes:
            rows.append((r, dishes))
    if not rows:
        raise SwiggyError("Swiggy's menus had no dishes SmartPlate can plan with yet. "
                          "Add a favourite restaurant from live search and try again.")
    clear(user_id)
    n_dishes = 0
    with db.cursor() as cur:
        for r, dishes in rows:
            try:
                rating = float(r.get("rating") or 4.0)
            except (TypeError, ValueError):
                rating = 4.0
            cur.execute("INSERT INTO restaurants(name, city, rating, cuisines, delivery_fee, eta_min, is_open, flaky, "
                        "source, provider_id) VALUES (?,?,?,?,?,?,1,0,'live',?)",
                        (r["name"], city_key(user_id), rating, "[]", LIVE_DELIVERY_FEE_ESTIMATE, 35, str(r["id"])))
            rid = cur.lastrowid
            for d in dishes:
                cur.execute("INSERT INTO menu_items(restaurant_id, name, price, cuisine, kcal, protein_g, carbs_g, "
                            "fat_g, sugar_g, veg, allergens, tags, carbon_kg, item_rating, popularity, reviews, "
                            "source, provider_item_id, nutrition_estimated) "
                            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'[]','live',?,1)",
                            (rid, d["name"], d["price"], "mixed", d["kcal"], d["protein_g"], d["carbs_g"],
                             d["fat_g"], d["sugar_g"], d["veg"], db.jd(d["allergens"]), db.jd(d["tags"]),
                             1.0, rating, 0.5, d["provider_item_id"]))
                n_dishes += 1
        cur.execute("INSERT INTO live_catalog_state(user_id, fetched_ts, restaurants, dishes, address_id) "
                    "VALUES (?,?,?,?,?)",
                    (user_id, clock.now().isoformat(timespec="minutes"), len(rows), n_dishes, address_id))
    return {**source_for(user_id), "skipped": skipped}


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
    """Live menu rows with each restaurant's billed delivery fee in place of the estimate."""
    fees = learned_fees(user_id)
    out = []
    for it in items:
        view = fee_view(it.get("provider_id") or it.get("restaurant_provider_id"), it["delivery_fee"], fees)
        out.append({**it, "delivery_fee": view["amount"], "delivery_fee_estimated": view["estimated"]})
    return out


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
                        else "Connecting Swiggy from SmartPlate is waiting for Swiggy's approval. "
                             "Until then, plans show home-cooked meals only.")}[needs]
    out = {"kind": kind, "connected": connected, "needs": needs, "stale_address": bool(row),
           "label": "Sample dishes (test data)" if kind == "sample" else "No restaurant dishes yet",
           "note": note}
    return out