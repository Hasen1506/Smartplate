"""Menus that open at once (UX audit #3, #16; 16 Oct 2026).

A tap on a restaurant used to read its whole menu from Swiggy, page by page (about 3 s a
page live, up to 5 pages), every time. Now:
  • every menu Ziggy reads for the person (a tap, the catalogue refresh, a prefetch) is kept
    for MENU_TTL (6 h, as documented for Swiggy menus) in `swiggy_menus`, per delivery
    address, and dropped when the address changes (swiggy_live._forget_address_state);
  • a tap serves that copy straight away and, once it is older than REVALIDATE_AFTER,
    re-reads it in the background (`refreshing` tells the app to look again shortly);
  • with no copy, the person's live catalogue gives a first look at the dishes Ziggy
    already knows from that place, while the menu is read in the background;
  • with neither, page 1 is read and shown, and the other pages follow in the background;
  • opening the Add sheet prefetches the menus of the person's own places (their usual
    meals and the places in their imported order history), at most PREFETCH_MAX.
Only places the person uses or opens are read (Swiggy Builders Club: no bulk catalogue
reads, no aggregation). Every view is screened for the person when served
(swiggy_live.menu_view), so a change of diet or allergy applies to a cached menu too.
"""
import datetime as dt
import logging
import threading
from concurrent.futures import ThreadPoolExecutor

from .. import clock, db
from .swiggy_connect import SwiggyError

KEY = "id:"                                  # swiggy_menus.restaurant for menus kept by Swiggy id
REVALIDATE_AFTER = dt.timedelta(minutes=20)
PREFETCH_MAX = 6
PREFETCH_READERS = 3
log = logging.getLogger(__name__)

_lock = threading.Lock()
_inflight: set[tuple[int, str]] = set()
_threads: list[threading.Thread] = []


def _ttl() -> dt.timedelta:
    from . import swiggy_live
    return swiggy_live.MENU_TTL


def _address_id(user_id: int) -> str | None:
    from ..domain import live_catalog
    return live_catalog._address_id(user_id)


# --------------------------------------------------------------------------- storage
def store(user_id: int, raw: dict) -> None:
    """Keep a menu read for this person's current address (a partial one until the rest is in)."""
    if not raw or not raw.get("items") or raw.get("address_id") != _address_id(user_id):
        return                                # an empty reply is never kept: nothing to show from it
    rid = str(raw["restaurant"]["id"])
    if raw.get("partial"):
        kept, _ = load(user_id, rid)
        if kept is not None and not kept.get("partial"):
            return                            # one page (a cart review's place check) never replaces a whole menu
    with db.cursor() as cur:
        cur.execute("INSERT OR REPLACE INTO swiggy_menus(user_id, restaurant, payload, fetched_ts) VALUES (?,?,?,?)",
                    (user_id, KEY + rid, db.jd(raw), clock.now().isoformat()))


def load(user_id: int, restaurant_id: str) -> tuple[dict | None, dt.timedelta | None]:
    """(cached raw menu, its age) when one is kept for this address and younger than the TTL."""
    try:
        with db.cursor() as cur:
            row = cur.execute("SELECT payload, fetched_ts FROM swiggy_menus WHERE user_id=? AND restaurant=?",
                              (user_id, KEY + str(restaurant_id))).fetchone()
    except Exception:                         # the Swiggy tables don't exist until first connect
        return None, None
    if not row:
        return None, None
    age = clock.now() - dt.datetime.fromisoformat(row["fetched_ts"])
    raw = db.jl(row["payload"], {})
    if age > _ttl() or age < -dt.timedelta(minutes=5) or raw.get("address_id") != _address_id(user_id):
        return None, None
    return raw, age


def drop(user_id: int, restaurant_id: str) -> None:
    with db.cursor() as cur:
        cur.execute("DELETE FROM swiggy_menus WHERE user_id=? AND restaurant=?", (user_id, KEY + str(restaurant_id)))


# --------------------------------------------------------------------------- background
def _run(key: tuple[int, str], fn) -> bool:
    """Run fn in the background once per (user, restaurant); False when already running."""
    with _lock:
        if key in _inflight:
            return False
        _inflight.add(key)

    def go():
        try:
            fn()
        except SwiggyError as exc:
            log.info("Background menu read stopped: %s", exc)
        except Exception:                     # a background read never takes the app down
            log.exception("Background menu read failed")
        finally:
            with _lock:
                _inflight.discard(key)
    t = threading.Thread(target=go, daemon=True, name=f"menu-{key[1]}")
    with _lock:
        _threads[:] = [x for x in _threads if x.is_alive()] + [t]
    t.start()
    return True


def busy(user_id: int, restaurant_id: str) -> bool:
    with _lock:
        return (user_id, str(restaurant_id)) in _inflight


def wait_idle(timeout: float = 20.0) -> None:
    """Tests and shutdown: wait for background reads to finish."""
    import time
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        with _lock:
            alive = [t for t in _threads if t.is_alive()]
        if not alive:
            return
        alive[0].join(max(0.01, end - time.monotonic()))


def _read_full(user_id: int, restaurant_id: str, name: str) -> None:
    from . import swiggy_live
    swiggy_live.live_menu(user_id, restaurant_id, name, screen=False)          # stores the menu


def _read_rest(user_id: int, restaurant_id: str, first: dict) -> None:
    """Pages 2.. of a menu whose page 1 is already shown, merged into one menu."""
    from . import swiggy_live
    conn = swiggy_live._conn(user_id)
    body, rows, notes = swiggy_live._read_menu(user_id, conn, restaurant_id, swiggy_live.MENU_PAGES,
                                               start=int(first.get("next_page") or 2))
    rest = swiggy_live.menu_raw(user_id, conn, restaurant_id, body, rows, notes)
    store(user_id, merge(first, rest))


def merge(first: dict, rest: dict) -> dict:
    """One menu from its first page and the pages after it (each dish once)."""
    items = [dict(i, categories=list(i["categories"])) for i in first["items"]]
    by_id = {i["id"]: i for i in items}
    for it in rest["items"]:
        known = by_id.get(it["id"])
        if known:
            known["categories"] += [c for c in it["categories"] if c not in known["categories"]]
            continue
        by_id[it["id"]] = it
        items.append(it)
    cats = list(first["categories"]) + [c for c in rest["categories"] if c not in first["categories"]]
    return {**first, "items": items, "categories": cats, "partial": rest["partial"], "next_page": rest.get("next_page"),
            "cut_sections": first["cut_sections"] + rest["cut_sections"], "more_sections": rest["more_sections"],
            "truncated": first["truncated"] or rest["truncated"],
            "total_items": first.get("total_items") or rest.get("total_items"), "fetched": rest["fetched"]}


# --------------------------------------------------------------------------- the catalogue's first look
def _from_catalogue(user_id: int, restaurant_id: str) -> dict | None:
    """The dishes of this place already in the person's live catalogue (read for this address
    by the last refresh): enough to show at once while the full menu is read."""
    from ..domain import live_catalog
    if not live_catalog.has_live(user_id):
        return None
    with db.cursor() as cur:
        place = cur.execute("SELECT id, name, rating FROM restaurants WHERE city=? AND provider_id=?",
                            (live_catalog.city_key(user_id), str(restaurant_id))).fetchone()
        if not place:
            return None
        rows = cur.execute("SELECT name, price, veg, veg_known, provider_item_id FROM menu_items "
                           "WHERE restaurant_id=? ORDER BY name", (place["id"],)).fetchall()
        state = cur.execute("SELECT fetched_ts FROM live_catalog_state WHERE user_id=?", (user_id,)).fetchone()
    if not rows:
        return None
    from . import swiggy_live
    items = [{"id": str(r["provider_item_id"]), "name": r["name"], "price": r["price"], "list_price": None,
              "price_estimated": False, "veg": (bool(r["veg"]) if r["veg_known"] else None), "in_stock": None,
              "has_options": False, "categories": [], "bestseller": False, "image": None,
              "name_allergens": swiggy_live._name_allergens(r["name"], bool(r["veg"]) if r["veg_known"] else None, None),
              "description": None, "provider_facts": {}} for r in rows]
    return {"restaurant": {"id": str(restaurant_id), "name": place["name"], "area": None, "rating": place["rating"],
                           "offer": None},
            "address_id": _address_id(user_id), "items": items, "categories": [], "total_items": None,
            "truncated": False, "cut_sections": [], "more_sections": False, "partial": True, "next_page": 1,
            "verified_prices": False, "fetched": state["fetched_ts"] if state else None, "source": "catalogue"}


# --------------------------------------------------------------------------- what the app calls
def view(user_id: int, restaurant_id: str, restaurant_name: str, *, fresh: bool = False) -> dict:
    """The menu for a tap, as fast as it can be shown truthfully, with `source` ("cache",
    "catalogue" or "swiggy"), `partial` (more dishes are coming) and `refreshing` (a
    background read is running: ask again shortly)."""
    from . import swiggy_live
    if not restaurant_id or not restaurant_name:
        raise ValueError("Choose a restaurant from live Swiggy search")
    rid, key = str(restaurant_id), (user_id, str(restaurant_id))
    raw, age = (None, None) if fresh else load(user_id, rid)
    started = False
    source = "cache"
    if raw is not None:
        if raw.get("partial") and not busy(user_id, rid):
            started = _run(key, lambda: _read_rest(user_id, rid, raw))
        elif age is not None and age > REVALIDATE_AFTER:
            started = _run(key, lambda: _read_full(user_id, rid, restaurant_name))
    else:
        raw = None if fresh else _from_catalogue(user_id, rid)
        if raw is not None:
            source = "catalogue"
            started = _run(key, lambda: _read_full(user_id, rid, restaurant_name))
        else:
            source = "swiggy"
            first = swiggy_live.live_menu(user_id, rid, restaurant_name, screen=False, pages=1)   # stores it
            raw, _ = load(user_id, rid)
            raw = raw or {**first, "address_id": _address_id(user_id)}
            if first.get("partial"):
                kept = raw
                started = _run(key, lambda: _read_rest(user_id, rid, kept))
    out = swiggy_live.menu_view(user_id, raw)
    return annotate(user_id, {**out, "source": source, "cached": source != "swiggy",
                              "age_min": int(age.total_seconds() // 60) if age is not None else 0,
                              "refreshing": started or busy(user_id, rid)})


def place(user_id: int, restaurant_id: str, restaurant_name: str) -> dict:
    """The restaurant as Swiggy last verified it for this address (cached menu), else one page."""
    from . import swiggy_live
    raw, _ = load(user_id, str(restaurant_id))
    if raw and raw.get("restaurant", {}).get("name"):
        return raw["restaurant"]
    return swiggy_live.verify_place(user_id, str(restaurant_id), restaurant_name)


def item(user_id: int, restaurant_id: str, restaurant_name: str, item_id: str) -> tuple[dict, dict | None, dict | None]:
    """(screened menu, the dish, its hidden entry) from the cached menu, re-read once when the
    dish isn't in it (a new dish since the copy was kept)."""
    from . import swiggy_live
    raw, _ = load(user_id, str(restaurant_id))
    if raw is None or raw.get("partial") or not any(i["id"] == str(item_id) for i in raw["items"]):
        menu = swiggy_live.live_menu(user_id, str(restaurant_id), restaurant_name)
    else:
        menu = swiggy_live.menu_view(user_id, raw)
    dish = next((i for i in menu["items"] if i["id"] == str(item_id)), None)
    hidden = next((i for i in menu.get("hidden_items") or [] if i["id"] == str(item_id)), None)
    return menu, dish, hidden


def prefetch(user_id: int, extra: list | None = None) -> dict:
    """Read, in the background, the menus of the person's own places that aren't kept yet:
    their usual meals' restaurants, then the places in their imported Swiggy history, then
    any the app names (`extra`, [{id, name}]). At most PREFETCH_MAX; nothing else."""
    from ..domain import meal_pools
    wanted, seen = [], set()
    try:
        wanted += meal_pools.restaurants(user_id)
    except Exception:
        pass
    try:
        from . import swiggy_history
        wanted += [{"id": i["restaurant_id"], "name": i["restaurant_name"]}
                   for i in swiggy_history.view(user_id).get("items") or [] if i.get("restaurant_id")]
    except Exception:
        pass
    wanted += [p for p in (extra or []) if isinstance(p, dict)]
    todo, kept = [], []
    for p in wanted:
        rid, name = str(p.get("id") or ""), str(p.get("name") or "")
        if not rid or not name or rid in seen:
            continue
        seen.add(rid)
        if len(seen) > PREFETCH_MAX:
            break
        raw, _ = load(user_id, rid)
        if raw is not None and not raw.get("partial"):
            kept.append(rid)
        elif not busy(user_id, rid):
            todo.append((rid, name))
    if todo:
        keys = [(user_id, rid) for rid, _ in todo]
        with _lock:
            todo = [t for t, k in zip(todo, keys) if k not in _inflight]
            _inflight.update((user_id, rid) for rid, _ in todo)

        def go():
            def one(t):
                try:
                    _read_full(user_id, *t)
                except SwiggyError as exc:
                    log.info("Prefetch skipped a menu: %s", exc)
                except Exception:
                    log.exception("Prefetch failed for one menu")
                finally:
                    with _lock:
                        _inflight.discard((user_id, t[0]))
            with ThreadPoolExecutor(max_workers=PREFETCH_READERS) as pool:
                list(pool.map(one, todo))
        t = threading.Thread(target=go, daemon=True, name="menu-prefetch")
        with _lock:
            _threads[:] = [x for x in _threads if x.is_alive()] + [t]
        t.start()
    return {"queued": [rid for rid, _ in todo], "kept": kept}


def per_meal_budget(user_id: int) -> float | None:
    """Today's budget shared over today's meals (the person's own numbers)."""
    from .. import today
    try:
        user = today._user(user_id)
        amount = today.budget_for(user)["amount"]
        meals = today.meals_today(user)
    except Exception:
        return None
    return round(amount / max(1, len(meals)), 2) if amount else None


def annotate(user_id: int, menu: dict) -> dict:
    """Best value within the per-meal budget; the restaurant's offer is already on the place."""
    from . import swiggy_offers
    menu["value"] = swiggy_offers.mark_value(menu["items"], per_meal_budget(user_id))
    return menu
