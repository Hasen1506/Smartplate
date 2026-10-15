"""Money actually spent — from real Swiggy bills only — and what it lets Ziggy say.

Three things read from it:
  • actual vs target for a week (critique 33: "weekly target", not "hard weekly cap"),
  • the Sunday recap: target, actual, ₹ kept, best/worst day (critique 24),
  • what the user really spends and, only when they ask, 10/15/20 % "cut" options in ₹
    (founder decision 14 Oct 2026: no automatic reduction targets).

What counts as spent (the ledger, `spend_ledger`):
  • an order in Swiggy's own order history (get_food_orders), not cancelled, with a
    total whose unit is known — `source = swiggy_order`, ref `swiggy:<orderId>`;
  • an order Ziggy placed through Swiggy's order tool — `ziggy_placed`, same ref when
    Swiggy returned an order id, so the history read never counts it twice.
A cart Ziggy checked and handed off to the Swiggy app is a real bill but not a known
payment: it is kept with `confirmed = 0` and never counted until Swiggy's order history
shows it. Plan estimates are never in the ledger, so they can never become "kept" money.

"Kept" is only claimed when Swiggy's order history was read after the period ended
(spend_sync): otherwise orders placed in the Swiggy app could be missing, and the recap
says so instead of inventing a number. Food bought outside Swiggy is never seen; the
recap says that too.
"""
import datetime as dt
import math
import re
import statistics

from .. import clock, db

WEEKS_FOR_SUGGESTION = 4
CANCELLED = re.compile(r"cancel|refund|fail|reject", re.IGNORECASE)
_MONEY_TEXT = re.compile(r"\s*(₹|rs\.?|inr)?\s*([\d,]+(?:\.\d+)?)\s*", re.IGNORECASE)
_ISO_DATE = re.compile(r"(\d{4}-\d{2}-\d{2})")
PAISE_AMBIGUOUS = 1000        # a bare whole number ≥ this could be paise or rupees: not counted


# --------------------------------------------------------------------------- #
# Units (critique 51): explicit where the provider says, never guessed silently
# --------------------------------------------------------------------------- #
def order_total(row: dict) -> tuple[float | None, str]:
    """(rupees, how the unit was known) for one Swiggy order-history row.

    "named"    — a field whose name carries the unit (…InPaise / …InRupees);
    "symbol"   — a display string with ₹ / Rs / INR;
    "display"  — a bare display string ("160"): Swiggy's order history shows rupees;
    "fraction" — a number with paise after the point is rupees;
    "small"    — a whole number under 1000 is rupees (a ₹10 order isn't real);
    "ambiguous"— a bare whole number ≥ 1000 could be either: (None, "ambiguous").
    """
    for key, unit in (("orderTotalInPaise", "paise"), ("order_total_in_paise", "paise"),
                      ("orderTotalInRupees", "rupees"), ("order_total_in_rupees", "rupees")):
        v = row.get(key)
        if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and v > 0:
            return (round(v / 100, 2) if unit == "paise" else round(float(v), 2)), "named"
    v = row.get("orderTotal", row.get("order_total"))
    if isinstance(v, bool) or v is None:
        return None, "missing"
    if isinstance(v, str):
        m = _MONEY_TEXT.fullmatch(v)
        if not m:
            return None, "unreadable"
        amount = float(m.group(2).replace(",", ""))
        if amount <= 0:
            return None, "unreadable"
        return round(amount, 2), ("symbol" if m.group(1) else "display")
    if isinstance(v, (int, float)) and math.isfinite(v) and v > 0:
        if abs(v - round(v)) > 1e-9:
            return round(float(v), 2), "fraction"
        if v < PAISE_AMBIGUOUS:
            return round(float(v), 2), "small"
        return None, "ambiguous"
    return None, "unreadable"


def order_date(row: dict) -> str | None:
    """The order's local date (YYYY-MM-DD) or None when Swiggy's time can't be read."""
    raw = row.get("orderedTime", row.get("ordered_time"))
    if isinstance(raw, (int, float)) and not isinstance(raw, bool) and raw > 1e9:
        stamp = raw / 1000 if raw > 1e12 else raw
        tz = clock._TZ or dt.timezone.utc
        return dt.datetime.fromtimestamp(stamp, dt.timezone.utc).astimezone(tz).date().isoformat()
    m = _ISO_DATE.search(str(raw or ""))
    if m:
        try:
            return dt.date.fromisoformat(m.group(1)).isoformat()
        except ValueError:
            return None
    from ..integrations import swiggy_live
    parsed = swiggy_live._order_time(raw)
    return parsed.date().isoformat() if parsed else None


# --------------------------------------------------------------------------- #
# Writing the ledger
# --------------------------------------------------------------------------- #
def _put(cur, user_id, ref, iso_date, amount, restaurant, source, confirmed):
    cur.execute("INSERT OR REPLACE INTO spend_ledger(user_id, ref, iso_date, amount, restaurant, source, confirmed) "
                "VALUES (?,?,?,?,?,?,?)", (user_id, ref, iso_date, round(float(amount), 2), restaurant or "",
                                           source, 1 if confirmed else 0))


def record_swiggy_orders(user_id: int, rows: list) -> dict:
    """Keep every readable, not-cancelled order from Swiggy's order history and note when
    the history was read. Returns what was kept and what was left out (and why)."""
    kept, skipped = 0, []
    dates = []
    with db.cursor() as cur:
        for r in rows or []:
            if not isinstance(r, dict) or not r.get("orderId"):
                continue
            ref = f"swiggy:{r['orderId']}"
            if CANCELLED.search(str(r.get("orderStatus") or "")):
                cur.execute("DELETE FROM spend_ledger WHERE user_id=? AND ref=?", (user_id, ref))
                skipped.append({"order_id": str(r["orderId"]), "why": "cancelled"})
                continue
            amount, how = order_total(r)
            day = order_date(r)
            if amount is None or day is None:
                skipped.append({"order_id": str(r["orderId"]), "why": "unit" if how == "ambiguous" else "unreadable"})
                continue
            _put(cur, user_id, ref, day, amount, str(r.get("restaurantName") or ""), "swiggy_order", True)
            dates.append(day)
            kept += 1
        # A handed-off cart is a real bill but not a known payment; once the history has been read,
        # a matching order (same total ±₹1, same day) confirms it and the hand-off row is dropped.
        for h in cur.execute("SELECT * FROM spend_ledger WHERE user_id=? AND source='ziggy_handoff'",
                             (user_id,)).fetchall():
            match = cur.execute("SELECT 1 FROM spend_ledger WHERE user_id=? AND source='swiggy_order' AND iso_date=? "
                                "AND ABS(amount - ?) <= 1", (user_id, h["iso_date"], h["amount"])).fetchone()
            if match:
                cur.execute("DELETE FROM spend_ledger WHERE user_id=? AND ref=?", (user_id, h["ref"]))
        now = clock.now().isoformat(timespec="seconds")
        prev = cur.execute("SELECT oldest_date FROM spend_sync WHERE user_id=?", (user_id,)).fetchone()
        oldest = min([d for d in dates] + ([prev["oldest_date"]] if prev and prev["oldest_date"] else []), default=None)
        cur.execute("INSERT OR REPLACE INTO spend_sync(user_id, synced_ts, oldest_date) VALUES (?,?,?)",
                    (user_id, now, oldest))
    return {"kept": kept, "skipped": skipped}


def record_placed(user_id: int, session_id: int, amount, order_id=None, restaurant: str = "") -> None:
    """Ziggy placed this order through Swiggy's order tool: real money."""
    if amount is None:
        return
    ref = f"swiggy:{order_id}" if order_id else f"queue:{session_id}"
    with db.cursor() as cur:
        _put(cur, user_id, ref, clock.today().isoformat(), amount, restaurant, "ziggy_placed", True)


def record_handoff(user_id: int, session_id: int, amount, restaurant: str = "") -> None:
    """A checked cart handed to the Swiggy app: its bill is real, its payment unknown."""
    if amount is None:
        return
    with db.cursor() as cur:
        _put(cur, user_id, f"queue:{session_id}", clock.today().isoformat(), amount, restaurant,
             "ziggy_handoff", False)


# --------------------------------------------------------------------------- #
# Reading it
# --------------------------------------------------------------------------- #
def entries(user_id: int, start: str, end: str, *, confirmed_only: bool = True) -> list[dict]:
    with db.cursor() as cur:
        rows = cur.execute("SELECT * FROM spend_ledger WHERE user_id=? AND iso_date>=? AND iso_date<=? "
                           + ("AND confirmed=1 " if confirmed_only else "") + "ORDER BY iso_date, ref",
                           (user_id, start, end)).fetchall()
    return [db.row_to_dict(r) for r in rows]


def synced(user_id: int) -> dict | None:
    with db.cursor() as cur:
        row = cur.execute("SELECT * FROM spend_sync WHERE user_id=?", (user_id,)).fetchone()
    return db.row_to_dict(row) if row else None


def covers(user_id: int, start: str, end: str) -> bool:
    """True when Swiggy's order history was read after `end` (or, for a week still running,
    today) — so every Swiggy order of the period is in the ledger."""
    s = synced(user_id)
    if not s:
        return False
    upto = min(dt.date.fromisoformat(end), clock.today())
    return dt.date.fromisoformat(s["synced_ts"][:10]) >= upto


def week_start_of(day: dt.date) -> dt.date:
    return day - dt.timedelta(days=day.weekday())


def actual(user_id: int, start: str, end: str) -> dict:
    rows = entries(user_id, start, end)
    pending = [r for r in entries(user_id, start, end, confirmed_only=False) if not r["confirmed"]]
    return {"amount": round(sum(r["amount"] for r in rows), 2), "orders": len(rows),
            "complete": covers(user_id, start, end),
            "unconfirmed": {"amount": round(sum(r["amount"] for r in pending), 2), "orders": len(pending)},
            "source": "Swiggy bills and order history"}


# --------------------------------------------------------------------------- #
# Sunday recap (critique 24)
# --------------------------------------------------------------------------- #
TREATS = [   # what the kept money is "worth", Chennai prices, highest first
    (1500, "a weekend trip's bus fares"),
    (800, "a nice dinner out for two"),
    (300, "a movie"),
    (150, "a couple of filter coffees and a snack"),
    (50, "a filter coffee"),
]


def treat(amount: float) -> str | None:
    return next((what for floor, what in TREATS if amount >= floor), None)


def week_recap(user: dict, week_start: str | None = None) -> dict:
    """target, actual (real bills), kept, best/worst day and a shareable line for one Mon–Sun
    week (default: the current one). Kept money is never claimed from estimates."""
    today = clock.today()
    start = dt.date.fromisoformat(week_start) if week_start else week_start_of(today)
    start = week_start_of(start)
    end = start + dt.timedelta(days=6)
    target = round(float(user["weekly_budget"]), 2)
    act = actual(user["id"], start.isoformat(), end.isoformat())
    rows = entries(user["id"], start.isoformat(), end.isoformat())
    days_in = [start + dt.timedelta(days=i) for i in range(7) if start + dt.timedelta(days=i) <= today]
    share = target / 7
    by_day = []
    for d in days_in:
        spent = round(sum(r["amount"] for r in rows if r["iso_date"] == d.isoformat()), 2)
        by_day.append({"date": d.isoformat(), "day": d.strftime("%a"), "spent": spent,
                       "vs_share": round(share - spent, 2)})
    finished = end < today
    out = {"week_start": start.isoformat(), "week_end": end.isoformat(), "finished": finished,
           "target": target, "actual": act["amount"], "orders": act["orders"],
           "complete": act["complete"], "unconfirmed": act["unconfirmed"], "by_day": by_day,
           "best_day": None, "worst_day": None, "kept": None, "over": None, "share_text": None,
           "basis": "Real Swiggy bills only. Plan estimates are never counted as spent or kept. "
                    "Food bought outside Swiggy isn't seen."}
    # best / worst among days with real orders: a day with none may have been cooked at home or
    # bought outside Swiggy, which Ziggy can't see, so it is never called "best"
    spent_days = [d for d in by_day if d["spent"] > 0]
    out["no_order_days"] = sum(1 for d in by_day if d["spent"] == 0)
    if spent_days:
        out["best_day"] = max(spent_days, key=lambda d: (d["vs_share"], d["date"]))
        out["worst_day"] = min(spent_days, key=lambda d: (d["vs_share"], d["date"]))
    if not act["complete"]:
        out["note"] = ("Read your Swiggy order history to see what you kept: orders placed in the Swiggy app "
                       "only count once Ziggy has seen them.")
        return out
    gap = round(target - act["amount"], 2)
    if gap >= 0:
        out["kept"] = gap
        worth = treat(gap)
        label = "kept" if finished else "under so far"
        out["share_text"] = (f"₹{gap:,.0f} {label} this week on Ziggy" + (f" — that's {worth}." if worth else "."))
    else:
        out["over"] = -gap
        out["share_text"] = None
        out["note"] = f"₹{-gap:,.0f} over this week's target of ₹{target:,.0f}."
    return out


# --------------------------------------------------------------------------- #
# Budget from real spend (critique 27)
# --------------------------------------------------------------------------- #
def cheapest_meal(user: dict) -> dict | None:
    """The user's own cheapest FAVOURITE, delivered (delivery, fees and GST included) — a
    dish in one of their meal lists or at one of their saved places; not a side, not a
    drink. None without favourites: then there is no floor at all (founder decision,
    14 Oct 2026: no made-up "₹60 a meal" floor)."""
    from . import fees, models, taste
    from .meal_pools import for_planner
    try:
        menu = models.menu_for_user(user)
    except Exception:
        return None
    try:
        pooled = set().union(*(p["ids"] for p in for_planner(user["id"]).values())) if user.get("id") else set()
    except Exception:
        pooled = set()
    places = set(user.get("favourite_places") or ()) | (taste.favourites(user["id"]) if user.get("id") else set())
    menu = [it for it in menu if (it["id"] in pooled or it["restaurant_id"] in places)
            and "side" not in (it.get("tags") or []) and it.get("price")]
    if not menu:
        return None
    priced = [(round(it["price"] + it.get("delivery_fee", 0) + fees.charges(it, it["price"]), 2), it) for it in menu]
    cost, it = min(priced, key=lambda p: (p[0], p[1]["name"]))
    return {"cost": cost, "item": it["name"], "restaurant": it.get("restaurant_name", ""),
            "estimated": bool(it.get("delivery_fee_estimated") or (it.get("fees") or {}).get("fixed", {}).get("estimated"))}


def meals_per_week(user: dict) -> int:
    from . import profile
    return max(1, len(profile.meals_planned(user)) * 7)


CUT_OPTIONS = (0.10, 0.15, 0.20)     # "cut my spending": the three offers, each shown in ₹
DEFAULT_CUT = 0.15                   # highlighted, never applied by itself


def _cut_option(pct: float, weekly: float, orders_week: float, avg_order: float, cheap: dict | None,
                floor: float | None) -> dict:
    target = round(weekly * (1 - pct), 2)
    target = math.ceil(target / 10) * 10 if target >= 100 else round(target, 2)
    saving = round(weekly - target, 2)
    changes = []
    if cheap and avg_order > cheap["cost"] and saving > 0:
        swaps = math.ceil(saving / (avg_order - cheap["cost"]))
        changes.append(f"About {swaps} of your ~{orders_week:.0f} weekly orders at your cheapest favourite "
                       f"({cheap['item']}, ₹{cheap['cost']:,.0f}) instead of your average ₹{avg_order:,.0f} order")
    if avg_order > 0 and saving > 0:
        n = math.ceil(saving / avg_order)
        changes.append(f"Or cook {n} meal{'s' if n != 1 else ''} a week instead of ordering")
    below = floor is not None and target < floor
    if below:
        changes = [f"Below what your favourites cost (₹{floor:,.0f} a week for your ~{orders_week:.0f} orders): "
                   "only cooking more or ordering less gets there"]
    return {"pct": round(pct * 100), "target": target, "saving_week": saving,
            "saving_month": round(saving * 52 / 12), "default": abs(pct - DEFAULT_CUT) < 1e-9,
            "below_floor": below, "label": f"{round(pct * 100)}% less: ₹{target:,.0f} a week (₹{saving:,.0f} less)",
            "what_changes": changes}


def suggest(user: dict, *, cut: float | None = None, target: float | None = None,
            asked: bool = False) -> dict:
    """What the user really spends (Swiggy bills only) and, ONLY when they ask to cut it,
    10 / 15 / 20 % options, each in ₹ with what would change; 15 % is highlighted.

    Founder decision (14 Oct 2026): no automatic reduction target. Without `asked` (or
    their own `cut` / `target`) this returns the facts and no suggested number. A `cut`
    (0.2 = "20 % less") or `target` (₹) of their own is checked against the floor — their
    own cheapest favourite × the orders they actually make a week (none without
    favourites) — and never applied here.
    """
    today = clock.today()
    start = today - dt.timedelta(days=7 * WEEKS_FOR_SUGGESTION)
    rows = entries(user["id"], start.isoformat(), today.isoformat())
    s = synced(user["id"])
    if not rows:
        return {"available": False, "asked": bool(asked or cut is not None or target is not None),
                "why": "No real Swiggy spend yet. Read your Swiggy order history (or place a first order) and "
                       "Ziggy can show what you actually spend, and options to cut it if you ask."}
    first = min(r["iso_date"] for r in rows)
    oldest = (s or {}).get("oldest_date") or first
    covered_days = max(7, (today - dt.date.fromisoformat(min(first, oldest))).days + 1)
    covered_days = min(covered_days, 7 * WEEKS_FOR_SUGGESTION)
    spent = round(sum(r["amount"] for r in rows), 2)
    weekly = round(spent * 7 / covered_days, 2)
    orders_week = len(rows) * 7 / covered_days
    avg_order = spent / len(rows)
    cheap = cheapest_meal(user)
    floor = round(cheap["cost"] * max(1, round(orders_week)), 2) if cheap else None
    out = {"available": True, "spent_4w": spent, "weeks": round(covered_days / 7, 1), "weekly_spend": weekly,
           "orders_per_week": round(orders_week, 1), "avg_order": round(avg_order, 2),
           "floor": floor, "cheapest_meal": cheap,
           "headline": f"You spent ₹{spent:,.0f} in the last {_span(covered_days)} (₹{weekly:,.0f} a week).",
           "complete": bool(s) and dt.date.fromisoformat(s["synced_ts"][:10]) >= today,
           "basis": "Real Swiggy bills and order history only.",
           "asked": bool(asked or cut is not None or target is not None), "options": [],
           "suggested": None, "reasons": [], "clamped": False}
    if not out["asked"]:
        out["offer"] = "Want to spend less? Ask and Ziggy shows 10%, 15% and 20% options in rupees."
        return out
    out["options"] = [_cut_option(p, weekly, orders_week, avg_order, cheap, floor) for p in CUT_OPTIONS]
    out["default_pct"] = round(DEFAULT_CUT * 100)
    mine = None
    if target is not None:
        mine = float(target)
    elif cut is not None:
        mine = round(weekly * (1 - float(cut)), 2)
    if mine is not None:
        out["suggested"] = mine
        out["saving_per_week"] = max(0.0, round(weekly - mine, 2))
        if floor is not None and mine < floor:
            out["reasons"].append(
                f"Your cheapest favourite is about ₹{cheap['cost']:,.0f} ({cheap['item']}, incl. delivery, fees & "
                f"GST); ×{max(1, round(orders_week))} orders a week is ₹{floor:,.0f}, so ₹{mine:,.0f} means "
                "cooking more or ordering less.")
            out["clamped"] = True
    else:
        d = next(o for o in out["options"] if o["default"])
        out["suggested"], out["saving_per_week"] = d["target"], d["saving_week"]
    return out


def _span(days: int) -> str:
    weeks = days / 7
    if days < 14 or abs(weeks - round(weeks)) > 0.15:
        return f"{days} days"
    return f"{round(weeks)} weeks"


def low_budget_check(user: dict, weekly_budget: float | None = None) -> dict | None:
    """A weekly budget that can't buy the meals it's meant to cover gets an explicit warning
    and a choice (usability finding 23). The only floor is the user's own cheapest
    favourite with its fees (founder decision, 14 Oct 2026); with no favourites there is no
    floor and no warning (no made-up "typical cheapest meal")."""
    budget = float(weekly_budget if weekly_budget is not None else user["weekly_budget"])
    meals = meals_per_week(user)
    cheap = cheapest_meal(user)
    if not cheap:
        return None
    per = cheap["cost"]
    basis = f"your cheapest favourite ({cheap['item']}, ~₹{cheap['cost']:,.0f} delivered)"
    covers_n = int(budget // per) if per > 0 else 0
    if covers_n >= meals:
        return None
    raise_to = math.ceil(per * meals / 50) * 50
    return {"level": "warn", "kind": "low_budget", "budget": budget, "meals": meals, "per_meal": round(budget / meals, 2),
            "cheapest": per, "covers": covers_n,
            "title": f"₹{budget:,.0f} covers about {covers_n} of your favourite meal{'s' if covers_n != 1 else ''} a week",
            "body": (f"That's ₹{budget / meals:,.0f} a meal for {meals} meals; {basis}. "
                     f"Plan {covers_n} delivered meal{'s' if covers_n != 1 else ''} and cook the rest, "
                     f"or raise the target to about ₹{raise_to:,.0f} to order every meal."),
            "actions": [{"act": "plan-delivered", "label": f"Plan {covers_n} delivered, cook the rest", "n": covers_n},
                        {"act": "raise-budget", "label": f"Raise to ₹{raise_to:,.0f}", "amount": raise_to}]}
