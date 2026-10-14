"""Money actually spent — from real Swiggy bills only — and what it lets Ziggy say.

Three things read from it:
  • actual vs target for a week (critique 33: "weekly target", not "hard weekly cap"),
  • the Sunday recap: target, actual, ₹ kept, best/worst day (critique 24),
  • a budget suggestion from the user's own last-4-weeks spend with guard rails
    (critique 27 + Vijay's answer: never a dysfunctional target).

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
SUGGEST_CUT = 0.15            # "aim for 0.85 × what you spent"
MAX_CUT_PER_STEP = 0.25       # never ask for more than 25 % less than real spend in one step
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
    """The cheapest realistic delivered meal from the user's OWN places (live favourites /
    pools), with delivery, fees and GST — not a side, not a drink. None without any."""
    from . import fees, models
    try:
        menu = models.menu_for_user(user)
    except Exception:
        return None
    menu = [it for it in menu if "side" not in (it.get("tags") or []) and it.get("price")]
    if not menu:
        return None
    priced = [(round(it["price"] + it.get("delivery_fee", 0) + fees.charges(it, it["price"]), 2), it) for it in menu]
    cost, it = min(priced, key=lambda p: (p[0], p[1]["name"]))
    return {"cost": cost, "item": it["name"], "restaurant": it.get("restaurant_name", ""),
            "estimated": bool(it.get("delivery_fee_estimated") or (it.get("fees") or {}).get("fixed", {}).get("estimated"))}


def meals_per_week(user: dict) -> int:
    from . import profile
    return max(1, len(profile.meals_planned(user)) * 7)


def suggest(user: dict, *, cut: float | None = None, target: float | None = None) -> dict:
    """"You spent ₹X in the last 4 weeks; aim for ₹0.85X?" with guard rails.

    Guard rails, applied in this order:
      • real spend only (ledger); without it there is no suggestion, only what to do;
      • a single step never cuts more than 25 % below real weekly spend;
      • never below the floor: the cheapest realistic meal from the user's own places
        × the number of orders they actually make a week (from the same history).
    `cut` (0.2 = "20 % less") or `target` (₹) is the user's own ask; it is checked, not
    obeyed blindly. Cheaper alternatives are never suggested here (only on request).
    """
    today = clock.today()
    start = today - dt.timedelta(days=7 * WEEKS_FOR_SUGGESTION)
    rows = entries(user["id"], start.isoformat(), today.isoformat())
    s = synced(user["id"])
    if not rows:
        return {"available": False,
                "why": "No real Swiggy spend yet. Read your Swiggy order history (or place a first order) and "
                       "Ziggy will suggest a target from what you actually spend."}
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
    max_cut_target = round(weekly * (1 - MAX_CUT_PER_STEP), 2)

    asked = None
    if target is not None:
        asked = float(target)
    elif cut is not None:
        asked = round(weekly * (1 - float(cut)), 2)
    proposed = asked if asked is not None else round(weekly * (1 - SUGGEST_CUT), 2)
    reasons, clamped = [], False
    if proposed < max_cut_target:
        reasons.append(f"More than a 25% cut in one step usually doesn't stick, so this aims for "
                       f"₹{max_cut_target:,.0f} first; ask again once you've held it for a week.")
        proposed, clamped = max_cut_target, True
    if floor is not None and proposed < floor:
        reasons.append(f"Your cheapest meal from your own places is about ₹{cheap['cost']:,.0f} "
                       f"({cheap['item']}, incl. delivery, fees & GST); ×{max(1, round(orders_week))} orders a week "
                       f"is ₹{floor:,.0f}, so the target can't go below that without cooking or ordering less.")
        proposed, clamped = floor, True
    proposed = math.ceil(proposed / 10) * 10 if proposed >= 100 else round(proposed, 2)   # up: never under a floor
    saving = round(weekly - proposed, 2)
    trade = []
    if saving > 0 and cheap and avg_order > cheap["cost"]:
        swaps = math.ceil(saving / (avg_order - cheap["cost"]))
        trade.append(f"About {swaps} of your ~{orders_week:.0f} weekly orders at your cheapest favourite "
                     f"(₹{cheap['cost']:,.0f}) instead of your average ₹{avg_order:,.0f} order.")
    if saving > 0 and avg_order > 0:
        trade.append(f"Or cook {math.ceil(saving / avg_order)} of those meals instead of ordering.")
    if floor is not None and floor >= weekly:
        trade = ["You already spend about as little as your own places allow. Cooking some meals is the "
                 "only lever left."]
    return {"available": True, "spent_4w": spent, "weeks": round(covered_days / 7, 1), "weekly_spend": weekly,
            "orders_per_week": round(orders_week, 1), "avg_order": round(avg_order, 2),
            "suggested": proposed, "saving_per_week": max(0.0, saving), "clamped": clamped,
            "floor": floor, "cheapest_meal": cheap, "max_cut_target": max_cut_target, "reasons": reasons,
            "trade_offs": trade,
            "headline": (f"You spent ₹{spent:,.0f} in the last {_span(covered_days)} "
                         f"(₹{weekly:,.0f} a week). Aim for ₹{proposed:,.0f} a week?"),
            "complete": bool(s) and dt.date.fromisoformat(s["synced_ts"][:10]) >= today,
            "basis": "Real Swiggy bills and order history only."}


def _span(days: int) -> str:
    weeks = days / 7
    if days < 14 or abs(weeks - round(weeks)) > 0.15:
        return f"{days} days"
    return f"{round(weeks)} weeks"


def low_budget_check(user: dict, weekly_budget: float | None = None) -> dict | None:
    """A weekly budget that can't buy the meals it's meant to cover gets an explicit warning
    and a choice (usability finding 23: "₹300 covers about 3 delivered meals near you. Plan
    3 meals, or cook the rest?"). Uses the cheapest realistic meal from the user's own
    places; without any, the research default (cheapest plausible dish ₹60 + default fees)."""
    from . import fees
    budget = float(weekly_budget if weekly_budget is not None else user["weekly_budget"])
    meals = meals_per_week(user)
    cheap = cheapest_meal(user)
    if cheap:
        per, basis = cheap["cost"], f"your cheapest meal ({cheap['item']}, ~₹{cheap['cost']:,.0f} delivered)"
    else:
        per = round(DEFAULT_CHEAPEST_DISH + fees.DELIVERY + fees.DEFAULT_FIXED + fees.GST_FOOD_RATE * DEFAULT_CHEAPEST_DISH, 2)
        basis = f"a typical cheapest delivered meal (~₹{per:,.0f} incl. delivery, fees & GST, an estimate)"
    covers_n = int(budget // per) if per > 0 else 0
    if covers_n >= meals:
        return None
    return {"level": "warn", "kind": "low_budget", "budget": budget, "meals": meals, "per_meal": round(budget / meals, 2),
            "cheapest": per, "covers": covers_n,
            "title": f"₹{budget:,.0f} covers about {covers_n} delivered meal{'s' if covers_n != 1 else ''} a week",
            "body": (f"That's ₹{budget / meals:,.0f} a meal for {meals} meals; {basis}. "
                     f"Plan {covers_n} delivered meal{'s' if covers_n != 1 else ''} and cook the rest, "
                     f"or raise the target to about ₹{math.ceil(per * meals / 50) * 50:,.0f} to order every meal."),
            "actions": [{"act": "plan-delivered", "label": f"Plan {covers_n} delivered, cook the rest", "n": covers_n},
                        {"act": "raise-budget", "label": f"Raise to ₹{math.ceil(per * meals / 50) * 50:,.0f}",
                         "amount": math.ceil(per * meals / 50) * 50}]}


DEFAULT_CHEAPEST_DISH = 60.0   # ₹; a plain tiffin item is the cheapest realistic delivered meal (estimate)
