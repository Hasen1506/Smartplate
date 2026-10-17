"""The week's money as one envelope (budget model v2, founder decisions 16–17 Oct 2026).

The weekly number is an envelope, not a ration. A day's share is a guide line, never a wall:
the person's own choice (a pin, a pick, a day amount) always wins; Ziggy shows what it costs
and rebalances the days still ahead. Today and the Week read this one module, so the two can
never show different figures (the flat weekly ÷ 7 on Today against the Week's rolling share
was a bug, docs/CHANGELOG "Fix round 4").

Money states, per day (and per meal slot underneath):
  spent      a real bill (an order, a Swiggy bill, a receipt from another app). Immutable.
  committed  the person's own pick or pin (costed at its estimate until billed), a real cart bill.
  flexible   envelope - spent - committed (- the reserve of Off days, scope "all"): what Ziggy
             fills, spread over the flexible days only, weighted by the meals on each day.
  off        the person said they won't plan it: no money assumed. With scope "all" (the
             default: the budget is all my food spend) its share is kept aside as a reserve;
             with scope "planned" (only the meals Ziggy plans) the envelope spreads over the
             planned days alone.

A day amount the person set (day_budget) reserves that money for the day: soft (a target the
solver aims at) unless they said "never above" (mode hard). Unset follows the envelope.

`allocate` is pure (paise in, rupees out, largest-remainder rounding so the day chips add up
to the envelope). `week` reads one person's week from the database and calls it.
"""
from __future__ import annotations

import datetime as dt
import math

from .. import clock, db

SCOPES = ("all", "planned")
DEFAULT_SCOPE = "all"
MODES = ("soft", "hard")
STATES = ("plan", "off")
MEAL_DAY_CUTOFF_H = 4          # a meal eaten before 04:00 belongs to the day before (late dinner)
FAST_SUSPENDS = ("breakfast", "lunch")
OFF_NOTE = "Off: you're not planning this"
MEALS = ("breakfast", "lunch", "dinner")

SCHEMA = """
CREATE TABLE IF NOT EXISTS today_budget (         -- r3: a budget set for today (migrated to day_budget)
    user_id INTEGER NOT NULL,
    day TEXT NOT NULL,
    amount REAL NOT NULL,
    PRIMARY KEY (user_id, day)
);
CREATE TABLE IF NOT EXISTS day_budget (           -- a day's own amount (any day, today included)
    user_id INTEGER NOT NULL,
    day TEXT NOT NULL,
    amount REAL NOT NULL,
    mode TEXT NOT NULL DEFAULT 'soft',
    set_at TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (user_id, day)
);
CREATE TABLE IF NOT EXISTS week_override (        -- "Make this week ₹X": one week only
    user_id INTEGER NOT NULL,
    week_start TEXT NOT NULL,
    amount REAL NOT NULL,
    set_at TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (user_id, week_start)
);
CREATE TABLE IF NOT EXISTS day_plan (             -- Plan / Off for a day ('' meal) or one meal of it
    user_id INTEGER NOT NULL,
    day TEXT NOT NULL,
    meal TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL,
    eaters INTEGER,
    set_at TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (user_id, day, meal)
);
CREATE TABLE IF NOT EXISTS budget_settings (      -- what the weekly budget covers, asked once
    user_id INTEGER PRIMARY KEY,
    scope TEXT NOT NULL DEFAULT 'all',
    carry INTEGER NOT NULL DEFAULT 0,
    asked INTEGER NOT NULL DEFAULT 0
);
"""



def init_schema() -> None:
    """Create the tables and move r3's today_budget rows into day_budget (soft, once)."""
    with db.cursor() as cur:
        cur.executescript(SCHEMA)
        if cur.execute("SELECT 1 FROM today_budget LIMIT 1").fetchone():
            cur.execute("INSERT OR IGNORE INTO day_budget(user_id, day, amount, mode, set_at) "
                        "SELECT user_id, day, amount, 'soft', '' FROM today_budget")
            cur.execute("DELETE FROM today_budget")


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #
def monday(day: dt.date) -> dt.date:
    return day - dt.timedelta(days=day.weekday())


def meal_day(at: dt.datetime) -> dt.date:
    """The day a meal belongs to: a dinner after midnight is still yesterday's (edge case 8)."""
    return (at - dt.timedelta(days=1)).date() if at.hour < MEAL_DAY_CUTOFF_H else at.date()


def _date(value, what="date") -> dt.date:
    try:
        return dt.date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        raise ValueError(f"Give the {what} as YYYY-MM-DD") from None


def rupees(n: float) -> str:
    return f"₹{n:,.0f}"


def largest_remainder(values: list[float], total: int) -> list[int]:
    """Whole rupees that add up to `total`: floors first, then one more rupee to the largest
    remainders (earliest first on a tie). Negative inputs are treated as 0."""
    vals = [max(0.0, float(v)) for v in values]
    floors = [math.floor(v + 1e-9) for v in vals]
    short = total - sum(floors)
    order = sorted(range(len(vals)), key=lambda i: (-(round(vals[i] - floors[i], 6)), i))
    out = list(floors)
    if short > 0:
        k = 0
        while short > 0 and order:
            out[order[k % len(order)]] += 1
            short -= 1
            k += 1
    elif short < 0:                       # rounding pushed us over: take from the smallest remainders
        for i in reversed(order):
            if short == 0:
                break
            if out[i] > 0:
                out[i] -= 1
                short += 1
    return out


# --------------------------------------------------------------------------- #
# The pure allocation
# --------------------------------------------------------------------------- #
def allocate(envelope: float, days: list[dict], *, scope: str = DEFAULT_SCOPE) -> dict:
    """Share an envelope over a week's days.

    Each day: {date, state: before|past|plan|off, spent, committed, set: amount|None,
    mode: soft|hard, weight (meals Ziggy can fill), guide_weight (meals the day normally has),
    off_meals (meals of a planned day the person turned off), meals}.

    Returns the envelope, totals and per-day {target, guide, reserve, flex, fixed, over_guide}.
    Targets are money for the day in all (spent + committed + what Ziggy may still fill).
    """
    env = max(0.0, float(envelope))
    covered = [d for d in days if d["state"] != "before"]
    # Guide: the fair share of each day, before anything is pinned or set.
    if scope == "planned":
        gw = {d["date"]: (d["guide_weight"] if d["state"] != "off" else 0.0) for d in covered}
    else:
        gw = {d["date"]: d["guide_weight"] for d in covered}
    gsum = sum(gw.values())
    guide = {k: (env * w / gsum if gsum else 0.0) for k, w in gw.items()}
    per_meal_guide = {d["date"]: (guide[d["date"]] / d["guide_weight"] if d["guide_weight"] else 0.0)
                      for d in covered}

    fixed = {d["date"]: float(d.get("spent") or 0) + float(d.get("committed") or 0) for d in covered}
    reserve, target = {}, {}
    flex_days = []
    for d in covered:
        k = d["date"]
        reserve[k] = 0.0
        if d["state"] == "off":
            target[k] = fixed[k]
            if scope == "all":
                reserve[k] = max(0.0, guide[k] - fixed[k])
        elif d["state"] == "past":
            target[k] = float(d.get("spent") or 0)
            fixed[k] = target[k]                  # a past day's open pick is unknown, not spent
        elif d.get("set") is not None:
            target[k] = max(float(d["set"]), fixed[k])
        else:
            if scope == "all" and d.get("off_meals"):
                reserve[k] = per_meal_guide[k] * d["off_meals"]
            flex_days.append(d)
    used = sum(target.values()) + sum(reserve.values())
    avail = env - used
    # Water-fill the flexible days: each gets max(its fixed money, level × its weight).
    fx = [(d["date"], fixed[d["date"]], float(d.get("weight") or 0)) for d in flex_days]
    level = _level(avail, fx)
    for k, f, w in fx:
        target[k] = max(f, level * w)
    total_fixed = sum(fixed.values())
    planned_total = sum(target.values()) + sum(reserve.values())
    unallocated = max(0.0, env - planned_total)
    over = max(0.0, planned_total - env)
    # Whole rupees: the chips (target + reserve per day) and the unallocated rest add up to the
    # envelope (or to what is planned, when the person's own picks take the week over).
    keys = [d["date"] for d in covered]
    total_r = int(round(env + over))
    parts = [target[k] for k in keys] + [reserve[k] for k in keys] + [unallocated]
    rounded = largest_remainder(parts, total_r)
    n = len(keys)
    g_round = largest_remainder([guide[k] for k in keys], int(round(env)) if gsum else 0) if keys else []
    out_days = []
    for i, d in enumerate(days):
        k = d["date"]
        if d["state"] == "before":
            out_days.append({"date": k, "state": "before", "target": 0, "guide": 0, "reserve": 0, "flex": 0,
                             "spent": 0.0, "committed": 0.0, "set": None, "mode": None, "over_guide": 0})
            continue
        j = keys.index(k)
        t = rounded[j]
        f = fixed[k]
        out_days.append({
            "date": k, "state": d["state"], "target": t, "target_exact": round(target[k], 2),
            "guide": g_round[j] if g_round else 0, "guide_exact": round(guide[k], 2),
            "reserve": rounded[n + j], "flex": round(max(0.0, target[k] - f), 2),
            "spent": round(float(d.get("spent") or 0), 2),
            "committed": round(float(d.get("committed") or 0) if d["state"] != "past" else 0.0, 2),
            "set": d.get("set"), "mode": d.get("mode") if d.get("set") is not None else None,
            "over_guide": round(max(0.0, f - guide[k]), 2) if d["state"] != "past" else 0.0,
        })
    spent = sum(float(d.get("spent") or 0) for d in covered)
    committed = sum(float(d.get("committed") or 0) for d in covered if d["state"] != "past")
    res = sum(reserve.values())
    return {
        "envelope": round(env, 2), "envelope_rupees": int(round(env)),
        "spent": round(spent, 2), "committed": round(committed, 2),
        "flexible": round(env - spent - committed - res, 2),
        "reserve": round(res, 2), "unallocated": rounded[-1] if rounded else 0,
        "over": round(over, 2), "is_over": over > 0.005, "fixed_total": round(total_fixed, 2),
        "level": round(level, 4), "days": out_days, "scope": scope,
    }


def _level(avail: float, fx: list[tuple]) -> float:
    """The water level L with sum(max(f, L*w)) == avail (0 when the fixed money already uses it)."""
    ws = [(f, w) for _, f, w in fx if w > 0]
    if not ws:
        return 0.0
    if avail <= sum(f for _, f, _ in fx) + 1e-9:
        return 0.0
    extra_fixed = sum(f for _, f, w in fx if w <= 0)     # a day with no meals to fill keeps its fixed money
    budget = avail - extra_fixed
    pts = sorted(ws, key=lambda p: p[0] / p[1])
    # days whose fixed money is above the level stay at fixed; the rest share the remainder
    for i in range(len(pts) + 1):
        above = pts[i:]
        below = pts[:i]
        rem = budget - sum(f for f, _ in above)
        wsum = sum(w for _, w in below)
        if wsum > 0:
            lvl = rem / wsum
            lo = pts[i - 1][0] / pts[i - 1][1]
            hi = pts[i][0] / pts[i][1] if i < len(pts) else math.inf
            if lo - 1e-9 <= lvl <= hi + 1e-9:
                return max(0.0, lvl)
    total_w = sum(w for _, w in pts)
    return max(0.0, (budget - 0) / total_w) if total_w else 0.0


def impact(alloc_before: dict, alloc_after: dict, date: str, today: str | None = None) -> dict:
    """What a pick or pin on `date` costs the week: "₹65 above Monday's share. Rest of week
    ₹1,650 over 6 days ≈ ₹275/day", with the options the person can take. Never a block."""
    day = next(d for d in alloc_after["days"] if d["date"] == date)
    name = dt.date.fromisoformat(date).strftime("%A")
    fixed = day["spent"] + day["committed"]
    over_share = round(max(0.0, fixed - day["guide_exact"]), 2)
    rest = [d for d in alloc_after["days"] if d["date"] != date and d["state"] == "plan" and d["set"] is None
            and (today is None or d["date"] >= today)]
    rest_money = sum(d["target_exact"] for d in rest)
    rest_days = len(rest)
    per_day = rest_money / rest_days if rest_days else 0.0
    env = alloc_after["envelope"]
    out = {"date": date, "day": name, "over_share": over_share, "rest": round(rest_money, 2),
           "rest_days": rest_days, "rest_per_day": round(per_day, 2), "over_week": alloc_after["over"],
           "envelope": env}
    if alloc_after["is_over"]:
        need = alloc_after["over"]
        out["text"] = (f"{rupees(fixed)} on {name} takes the week {rupees(need)} over your {rupees(env)}. "
                       "Your pick stays.")
        out["options"] = [
            {"act": "raise_week", "label": f"Make this week {rupees(math.ceil((env + need) / 50.0) * 50)}",
             "amount": math.ceil((env + need) / 50.0) * 50},
            {"act": "suggest", "label": "Let Ziggy suggest meals to cook or skip", "need": round(need, 2)},
            {"act": "keep_over", "label": "Keep it and run over"},
        ]
        out["default"] = "keep_over"
    elif over_share > 0.5:
        out["text"] = (f"{rupees(over_share)} above {name}'s share. Rest of week {rupees(rest_money)} over "
                       f"{rest_days} day{'s' if rest_days != 1 else ''} ≈ {rupees(per_day)}/day.")
        out["options"] = [
            {"act": "take_from_week", "label": "Take it from the week"},
            {"act": "raise_week", "label": f"Make this week {rupees(env + over_share)}",
             "amount": int(math.ceil(env + over_share))},
        ]
        out["default"] = "take_from_week"
    else:
        out["text"] = None
        out["options"] = []
        out["default"] = None
    return out


# --------------------------------------------------------------------------- #
# Settings and the person's own amounts
# --------------------------------------------------------------------------- #
def _now_iso() -> str:
    return clock.now().isoformat(timespec="seconds")


def settings(user_id: int) -> dict:
    init_schema()
    with db.cursor() as cur:
        row = cur.execute("SELECT scope, carry, asked FROM budget_settings WHERE user_id=?", (user_id,)).fetchone()
    if not row:
        return {"scope": DEFAULT_SCOPE, "carry": False, "asked": False}
    return {"scope": row["scope"] if row["scope"] in SCOPES else DEFAULT_SCOPE, "carry": bool(row["carry"]),
            "asked": bool(row["asked"])}


def set_settings(user_id: int, body: dict) -> dict:
    cur_s = settings(user_id)
    scope = body.get("scope", cur_s["scope"])
    if scope not in SCOPES:
        raise ValueError("The weekly budget covers all your food spend, or only the meals Ziggy plans")
    carry = body.get("carry", cur_s["carry"])
    if not isinstance(carry, bool):
        raise ValueError("Carry over is on or off")
    with db.cursor() as cur:
        cur.execute("INSERT OR REPLACE INTO budget_settings(user_id, scope, carry, asked) VALUES (?,?,?,1)",
                    (user_id, scope, 1 if carry else 0))
    return settings(user_id)


def _check_set_at(value) -> str:
    if value in (None, ""):
        return _now_iso()
    try:
        dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("set_at must be an ISO time") from None
    return str(value)


def _newer(existing: str | None, incoming: str) -> bool:
    """Last write wins by set_at (edge case 15): an older write from another device is ignored."""
    if not existing:
        return True
    try:
        a = dt.datetime.fromisoformat(existing.replace("Z", "+00:00"))
        b = dt.datetime.fromisoformat(incoming.replace("Z", "+00:00"))
        if (a.tzinfo is None) != (b.tzinfo is None):
            a, b = a.replace(tzinfo=None), b.replace(tzinfo=None)
        return b >= a
    except ValueError:
        return True


def _forward(day: dt.date, what: str) -> None:
    if day < clock.today():
        raise ValueError(f"{what} can only be set for today or a day ahead")
    if day > clock.today() + dt.timedelta(days=13):
        raise ValueError(f"{what} can be set up to two weeks ahead")


def set_day_budget(user_id: int, date, amount, mode: str = "soft", set_at=None) -> dict:
    """A day's own amount (null: follow the envelope again). Returns {"applied": bool}."""
    init_schema()
    day = _date(date)
    _forward(day, "A day's budget")
    mode = mode or "soft"
    if mode not in MODES:
        raise ValueError("A day's budget is a target (soft) or a limit (hard)")
    stamp = _check_set_at(set_at)
    with db.cursor() as cur:
        row = cur.execute("SELECT set_at FROM day_budget WHERE user_id=? AND day=?",
                          (user_id, day.isoformat())).fetchone()
        if row and not _newer(row["set_at"], stamp):
            return {"applied": False}
        if amount is None:
            cur.execute("DELETE FROM day_budget WHERE user_id=? AND day=?", (user_id, day.isoformat()))
            return {"applied": True}
        try:
            a = float(amount)
        except (TypeError, ValueError):
            raise ValueError("A day's budget must be a number") from None
        if not math.isfinite(a) or not 0 <= a <= 20000:
            raise ValueError("A day's budget must be between ₹0 and ₹20,000")
        cur.execute("INSERT OR REPLACE INTO day_budget(user_id, day, amount, mode, set_at) VALUES (?,?,?,?,?)",
                    (user_id, day.isoformat(), round(a), mode, stamp))
    return {"applied": True}


def set_week_override(user_id: int, week_start, amount, set_at=None) -> dict:
    init_schema()
    ws = _date(week_start, "week start")
    if ws.weekday() != 0:
        raise ValueError("A week starts on Monday")
    if ws < monday(clock.today()):
        raise ValueError("Only this week or a week ahead can be changed")
    stamp = _check_set_at(set_at)
    with db.cursor() as cur:
        row = cur.execute("SELECT set_at FROM week_override WHERE user_id=? AND week_start=?",
                          (user_id, ws.isoformat())).fetchone()
        if row and not _newer(row["set_at"], stamp):
            return {"applied": False}
        if amount is None:
            cur.execute("DELETE FROM week_override WHERE user_id=? AND week_start=?", (user_id, ws.isoformat()))
            return {"applied": True}
        try:
            a = float(amount)
        except (TypeError, ValueError):
            raise ValueError("This week's budget must be a number") from None
        from . import profile
        if not math.isfinite(a) or not profile.BUDGET_MIN <= a <= profile.BUDGET_MAX:
            raise ValueError(f"This week's budget must be between ₹{profile.BUDGET_MIN:,.0f} and "
                             f"₹{profile.BUDGET_MAX:,.0f}")
        cur.execute("INSERT OR REPLACE INTO week_override(user_id, week_start, amount, set_at) VALUES (?,?,?,?)",
                    (user_id, ws.isoformat(), round(a), stamp))
    return {"applied": True}


def set_day_plan(user_id: int, date, state: str, meal: str | None = None, eaters=None, set_at=None) -> dict:
    init_schema()
    day = _date(date)
    _forward(day, "Plan or Off")
    if state not in STATES:
        raise ValueError("A day is Plan or Off")
    meal = meal or ""
    if meal and meal not in MEALS:
        raise ValueError("Choose breakfast, lunch or dinner")
    if eaters is not None:
        if not isinstance(eaters, int) or not 1 <= eaters <= 20:
            raise ValueError("How many eat: 1 to 20")
    stamp = _check_set_at(set_at)
    with db.cursor() as cur:
        row = cur.execute("SELECT set_at FROM day_plan WHERE user_id=? AND day=? AND meal=?",
                          (user_id, day.isoformat(), meal)).fetchone()
        if row and not _newer(row["set_at"], stamp):
            return {"applied": False}
        if state == "plan" and eaters is None:
            cur.execute("DELETE FROM day_plan WHERE user_id=? AND day=? AND meal=?", (user_id, day.isoformat(), meal))
            if not meal:          # the whole day planned again: its meals too
                cur.execute("DELETE FROM day_plan WHERE user_id=? AND day=? AND state='off'",
                            (user_id, day.isoformat()))
        else:
            cur.execute("INSERT OR REPLACE INTO day_plan(user_id, day, meal, state, eaters, set_at) "
                        "VALUES (?,?,?,?,?,?)", (user_id, day.isoformat(), meal, state, eaters, stamp))
    return {"applied": True}


def _rows(user_id: int, start: dt.date, end: dt.date) -> tuple[dict, dict, dict]:
    init_schema()
    a, b = start.isoformat(), end.isoformat()
    with db.cursor() as cur:
        sets = {r["day"]: {"amount": float(r["amount"]), "mode": r["mode"], "set_at": r["set_at"]}
                for r in cur.execute("SELECT * FROM day_budget WHERE user_id=? AND day>=? AND day<=?",
                                     (user_id, a, b)).fetchall()}
        plans: dict = {}
        for r in cur.execute("SELECT * FROM day_plan WHERE user_id=? AND day>=? AND day<=?",
                             (user_id, a, b)).fetchall():
            plans.setdefault(r["day"], {})[r["meal"]] = {"state": r["state"], "eaters": r["eaters"]}
        ov = cur.execute("SELECT amount FROM week_override WHERE user_id=? AND week_start=?",
                         (user_id, a)).fetchone()
    return sets, plans, ({"amount": float(ov["amount"])} if ov else {})


def off_meals(user_id: int, date: str, meals) -> set[str]:
    """Which of `meals` on `date` the person turned Off (the whole day, or one meal)."""
    d = _date(date)
    _, plans, _ = _rows(user_id, d, d)
    p = plans.get(date) or {}
    if (p.get("") or {}).get("state") == "off":
        return set(meals)
    return {m for m in meals if (p.get(m) or {}).get("state") == "off"}


# --------------------------------------------------------------------------- #
# Reading one person's week
# --------------------------------------------------------------------------- #
def _plan_for_week(user_id: int, ws: dt.date) -> dict | None:
    with db.cursor() as cur:
        row = cur.execute("SELECT * FROM plans WHERE user_id=? AND week_start=? ORDER BY id DESC LIMIT 1",
                          (user_id, ws.isoformat())).fetchone()
    return db.row_to_dict(row) if row else None


def _covered_from(plan: dict | None, ws: dt.date) -> dt.date:
    """The first day the envelope covers: the plan's (a plan made mid-week covers the rest of
    its week, its budget pro-rated), or today when this week has no plan yet."""
    if plan:
        from ..kernel import optimizer
        return ws + dt.timedelta(days=7 - optimizer.days_covered(plan))
    today = clock.today()
    return today if ws <= today <= ws + dt.timedelta(days=6) else ws


def base_envelope(user: dict, ws: dt.date, covered_days: int, override: dict | None = None,
                  carry: float = 0.0) -> float:
    """This week's envelope: its own override, else the weekly budget pro-rated to the days the
    plan covers (a Friday start covers Fri–Sun = 3/7), plus a carry-over when turned on."""
    if override:
        return round(float(override["amount"]), 2)
    return round(float(user.get("weekly_budget") or 0) * covered_days / 7 + carry, 2)


def _ledger_by_day(user_id: int, start: dt.date, end: dt.date) -> dict:
    try:
        with db.cursor() as cur:
            rows = cur.execute("SELECT iso_date, amount FROM spend_ledger WHERE user_id=? AND confirmed=1 "
                               "AND iso_date>=? AND iso_date<=?", (user_id, start.isoformat(),
                                                                   end.isoformat())).fetchall()
    except Exception:
        return {}
    out: dict = {}
    for r in rows:
        out[r["iso_date"]] = out.get(r["iso_date"], 0.0) + float(r["amount"])
    return out


def _meals_for(user: dict) -> list[str]:
    from . import profile
    return list(profile.meals_planned(user))


def _fasts(user: dict, ws: dt.date) -> dict:
    try:
        from . import festivals
        rows = festivals.for_week_all(ws.isoformat())
        return {(ws + dt.timedelta(days=i)).isoformat(): f for i, r in rows.items()
                for f in [festivals.fast_for(r, user)] if f}
    except Exception:
        return {}


def plan_money(plan: dict, user: dict, *, today_picks: dict | None = None) -> dict:
    """{date: {"spent", "committed", "planned"}} from the plan's own rows: ordered/confirmed
    meals are spent; pins and checked carts (at the real bill) are committed; the rest Ziggy
    planned is "planned" (flexible money in use). Today's picks replace today's cells."""
    from ..kernel import scheduler
    from . import models, week_orders
    at = clock.now()
    out: dict = {}
    real = week_orders.real_totals(plan["id"])
    today = clock.today().isoformat()
    picked_meals = set((today_picks or {}).keys())
    for d in models.decisions_for_plan(plan["id"]):
        date = models.session_date(plan, d["day"])
        row = out.setdefault(date, {"spent": 0.0, "committed": 0.0, "planned": 0.0})
        if d["chosen_kind"] not in ("delivery", "cook"):
            continue
        cost = float(d.get("cost") or 0)
        r = real.get(d["session_id"])
        if r and d["chosen_kind"] == "delivery":
            cost = float(r["to_pay"])
        if d["session_status"] in ("ordered", "confirmed"):
            row["spent"] += cost
            continue
        if d["session_status"] != "active" or scheduler.is_past(d, at):
            continue
        if date == today and d["meal"] in picked_meals:
            continue                                  # Today's pick is this meal (one truth, usab #1)
        if d.get("pinned") or r:
            row["committed"] += cost
        else:
            row["planned"] += cost
    return out


def week(user_id: int, week_start=None, **kw) -> dict:
    """The envelope of one week for one person (see _week)."""
    return _week(user_id, week_start, **kw)[0]


def _week(user_id: int, week_start=None, *, user: dict | None = None, plan: dict | None = None,
         fixed_override: dict | None = None, include_picks: bool = True, light: bool = False) -> dict:
    """The envelope of one week for one person: totals, each day's state and money, settings.

    `fixed_override` ({date: {"spent", "committed"}}) lets the planner pass the money it has
    just fixed (fresh pins) instead of the stored plan rows."""
    from . import models, profile
    user = user or models.get_user(user_id)
    if not user:
        raise KeyError("Profile not found")
    init_schema()
    today = clock.today()
    ws = monday(_date(week_start, "week") if week_start else today)
    end = ws + dt.timedelta(days=6)
    plan = plan if plan is not None else _plan_for_week(user_id, ws)
    start = _covered_from(plan, ws)
    covered_days = (end - start).days + 1
    sets, plans, override = _rows(user_id, ws, end)
    st = settings(user_id)
    carry = carried_in(user, ws) if st["carry"] and not override else 0.0
    env = base_envelope(user, ws, covered_days, override, carry)
    # money from the plan, today's picks, real bills
    picks_total, picks = 0.0, {}
    if include_picks and ws <= today <= end:
        from .. import today as today_mod
        picks, picks_total = today_mod.committed_today(user)
    pm = plan_money(plan, user, today_picks=picks) if plan else {}
    ledger = _ledger_by_day(user_id, ws, end)
    meals = _meals_for(user)
    base_eaters = profile.eaters(user)
    fasts = _fasts(user, ws)
    days = []
    for i in range(7):
        day = ws + dt.timedelta(days=i)
        k = day.isoformat()
        p = pm.get(k, {"spent": 0.0, "committed": 0.0, "planned": 0.0})
        if fixed_override is not None:
            fo = fixed_override.get(k) or {}
            p = {**p, "spent": float(fo.get("spent", p["spent"])), "committed": float(fo.get("committed", 0.0))}
        committed = p["committed"] + (picks_total if k == today.isoformat() else 0.0)
        plan_spent = p["spent"]
        bills = ledger.get(k, 0.0)
        spent = max(plan_spent, bills)
        # real bills beyond what the plan ordered first pay for that day's own picks (a pick
        # ordered through Swiggy shows up as a bill; never count it twice)
        committed = max(0.0, committed - max(0.0, bills - plan_spent))
        dp = plans.get(k) or {}
        whole_off = (dp.get("") or {}).get("state") == "off"
        meal_off = [m for m in meals if (dp.get(m) or {}).get("state") == "off"]
        eaters = (dp.get("") or {}).get("eaters")
        factor = (eaters / base_eaters) if eaters else 1.0
        fast = fasts.get(k)
        day_meals = [m for m in meals if not (fast and m in FAST_SUSPENDS)] if fast else list(meals)
        gweight = len(day_meals) * factor
        weight = len([m for m in day_meals if m not in meal_off]) * factor
        if day < start:
            state = "before"
        elif whole_off:
            state = "off"
        elif day < today:
            state = "past"
        else:
            state = "plan"
        s = sets.get(k)
        flags = []
        if eaters and eaters != base_eaters:
            flags.append({"kind": "eaters", "text": f"{eaters} eating (usually {base_eaters})"})
        if fast:
            flags.append({"kind": "fast", "text": f"{fast['name']}: a lighter day"})
        days.append({"date": k, "state": state, "spent": spent, "committed": committed,
                     "set": s["amount"] if s and state == "plan" else None,
                     "mode": s["mode"] if s else None, "weight": weight, "guide_weight": gweight,
                     "off_meals": len(meal_off) if state == "plan" else 0, "planned": p["planned"],
                     "flags": flags, "off_meal_names": meal_off, "eaters": eaters})
    alloc = allocate(env, days, scope=st["scope"])
    cheapest = None if light else _cheapest(user)
    for d_in, d in zip(days, alloc["days"]):
        d["planned"] = round(d_in["spent"] + d_in["committed"] + d_in["planned"], 2) if d["state"] in (
            "plan", "off") else round(d_in["spent"], 2)
        d["weekday"] = dt.date.fromisoformat(d["date"]).strftime("%a")
        d["flags"] = list(d_in["flags"])
        d["off_meals"] = d_in["off_meal_names"]
        d["eaters"] = d_in["eaters"]
        d["is_today"] = d["date"] == today.isoformat()
        d["min"] = int(math.ceil(d["spent"] + d["committed"])) if d["state"] == "plan" else 0
        if (cheapest and d["state"] == "plan" and d_in["weight"] and d["set"] is None
                and d["flex"] / max(1.0, d_in["weight"]) < cheapest["amount"] - 0.5 and d["flex"] > 0):
            d["flags"].append({"kind": "below_cheapest",
                               "text": f"{rupees(d['flex'])} left for the day is below your cheapest usual meal "
                                       f"({rupees(cheapest['amount'])})"})
    alloc.update({
        "week_start": ws.isoformat(), "week_end": end.isoformat(), "covers_from": start.isoformat(),
        "covered_days": covered_days, "weekly_budget": float(user.get("weekly_budget") or 0),
        "override": override.get("amount"), "carry_in": round(carry, 2),
        "settings": st, "plan_id": plan["id"] if plan else None,
    })
    alloc["kept"] = kept(alloc, today)
    alloc["low"] = _low(alloc, cheapest, days)
    return alloc, env, days, st["scope"]


def _cheapest(user: dict) -> dict | None:
    try:
        from . import spend
        c = spend.cheapest_meal(user)
        return {"amount": float(c["amount"]), "item": c.get("item")} if c and c.get("amount") else None
    except Exception:
        return None


def _low(alloc: dict, cheapest: dict | None, days_in: list[dict]) -> dict | None:
    """Edge case 7: the money left can't buy the cheapest usual meal for every meal ahead —
    say so and suggest Off days or cooking; never invent cheap food."""
    if not cheapest:
        return None
    meals_ahead = sum(d["weight"] for d in days_in if d["state"] == "plan" and d["set"] is None)
    flex = sum(d["flex"] for d in alloc["days"] if d["state"] == "plan" and d["set"] is None)
    if meals_ahead <= 0:
        return None
    need = cheapest["amount"] * meals_ahead
    if flex + 0.5 >= need:
        return None
    fits = int(flex // cheapest["amount"]) if cheapest["amount"] else 0
    return {"need": round(need, 2), "flex": round(flex, 2), "meals_ahead": meals_ahead, "fits": fits,
            "text": (f"{rupees(flex)} left covers about {fits} of {int(meals_ahead)} meals at your cheapest usual "
                     f"({rupees(cheapest['amount'])}). Turn a day Off, cook a few, or raise this week.")}


def kept(alloc: dict, today: dt.date) -> dict | None:
    """At the end of a week (Sunday or after): "kept ₹X". No automatic carry-over (edge 11)."""
    end = dt.date.fromisoformat(alloc["days"][-1]["date"])
    if today < end:
        return None
    left = alloc["envelope"] - alloc["spent"]
    return {"amount": round(max(0.0, left), 2), "final": today > end,
            "text": f"Kept {rupees(max(0.0, left))}" if left > 0 else None}


def carried_in(user: dict, ws: dt.date) -> float:
    """With carry-over on (off by default): last week's kept money, real bills only."""
    prev = ws - dt.timedelta(days=7)
    try:
        from . import spend
        spent = spend.actual(user["id"], prev.isoformat(), (prev + dt.timedelta(days=6)).isoformat())["amount"]
    except Exception:
        return 0.0
    _, _, ov = _rows(user["id"], prev, prev + dt.timedelta(days=6))
    base = float(ov["amount"]) if ov else float(user.get("weekly_budget") or 0)
    return round(max(0.0, base - spent), 2)


def day_view(user: dict, date: dt.date | None = None) -> dict:
    """One day of the envelope (Today reads this): target, guide, spent, committed, flags."""
    date = date or clock.today()
    w = week(user["id"], date.isoformat(), user=user)
    d = next(x for x in w["days"] if x["date"] == date.isoformat())
    rest = [x for x in w["days"] if x["date"] > date.isoformat() and x["state"] == "plan"]
    return {"day": d, "week": {k: w[k] for k in ("envelope", "spent", "committed", "flexible", "reserve", "over",
                                                 "is_over", "week_start", "covered_days", "weekly_budget",
                                                 "override", "settings", "kept", "low", "unallocated")},
            "rest": {"days": len(rest), "money": sum(x["target"] for x in rest)}}


def solver_targets(user: dict, plan: dict, fixed_by_day: dict, spent_by_day: dict | None = None) -> dict:
    """What the planners need, by plan day index: soft targets of open money per day (target -
    fixed), the person's set days (soft/hard), Off sessions, and the money the plan may use."""
    ws = dt.date.fromisoformat(plan["week_start"])
    fo = {}
    for i in range(7):
        k = (ws + dt.timedelta(days=i)).isoformat()
        fo[k] = {"spent": float((spent_by_day or {}).get(i, 0.0)),
                 "committed": float(fixed_by_day.get(i, 0.0)) - float((spent_by_day or {}).get(i, 0.0))}
    # the planner plans today's picked meals itself (as before), so picks aren't added twice here
    w = week(user["id"], ws.isoformat(), user=user, plan=plan, fixed_override=fo, include_picks=False,
             light=True)
    targets, sets = {}, {}
    for i, d in enumerate(w["days"]):
        if d["state"] != "plan":
            continue
        open_money = max(0.0, d["target_exact"] - d["spent"] - d["committed"])
        targets[i] = round(open_money, 2)
        if d["set"] is not None:
            sets[i] = {"open": round(open_money, 2), "mode": d["mode"] or "soft"}
    # money the plan may still spend: the envelope less everything fixed, reserved, or spent elsewhere
    room = w["envelope"] - w["reserve"] - sum(d["spent"] + d["committed"] for d in w["days"]
                                               if d["state"] in ("plan", "off", "past"))
    return {"targets": targets, "sets": sets, "room": round(max(0.0, room), 2), "envelope": w["envelope"],
            "week": w}


def budget_changed(user_id: int) -> None:
    """The weekly budget changed: it applies to what's left of this week (spent stays spent).
    A one-week override of this week gives way to the new number."""
    init_schema()
    with db.cursor() as cur:
        cur.execute("DELETE FROM week_override WHERE user_id=? AND week_start=?",
                    (user_id, monday(clock.today()).isoformat()))


def replan(user_id: int, date=None) -> list[int]:
    """Rebalance forward: re-plan the open meals of the plan covering `date` (default today)
    and of a week planned ahead. Past and fixed meals never move (optimizer keeps them)."""
    from ..kernel import optimizer
    day = _date(date) if date else clock.today()
    ws = monday(day)
    ids = []
    with db.cursor() as cur:
        rows = cur.execute("SELECT id, week_start FROM plans WHERE user_id=? AND week_start>=? "
                           "ORDER BY week_start, id DESC", (user_id, ws.isoformat())).fetchall()
    seen = set()
    for r in rows:
        if r["week_start"] in seen:
            continue
        seen.add(r["week_start"])
        try:
            optimizer.optimize(r["id"])
            ids.append(r["id"])
        except Exception:                    # a plan that can't be re-solved keeps its picks
            import logging
            logging.getLogger("smartplate.envelope").exception("re-plan of plan %s failed", r["id"])
    return ids


def plan_envelope(user: dict, plan: dict) -> float:
    """The money a plan's week has (optimizer.week_cap): the week's own override, else the
    weekly budget pro-rated to the days the plan covers, plus carry-over when turned on."""
    from ..kernel import optimizer
    ws = dt.date.fromisoformat(plan["week_start"])
    try:
        _, _, ov = _rows(user["id"], ws, ws + dt.timedelta(days=6))
        st = settings(user["id"])
    except Exception:
        ov, st = {}, {"carry": False}
    carry = carried_in(user, ws) if st.get("carry") and not ov else 0.0
    return base_envelope(user, ws, optimizer.days_covered(plan), ov, carry)


def sync_off(user: dict, plan: dict) -> None:
    """Off days / meals become skipped sessions with OFF_NOTE (no money, no reminders); a day
    turned back to Plan re-opens only the sessions Off closed (never a meal the person skipped)."""
    from ..kernel import scheduler
    from . import models
    ws = dt.date.fromisoformat(plan["week_start"])
    _, plans, _ = _rows(user["id"], ws, ws + dt.timedelta(days=6))
    at = clock.now()
    with db.cursor() as cur:
        for s in models.sessions_for_plan(plan["id"]):
            date = models.session_date(plan, s["day"])
            p = plans.get(date) or {}
            off = (p.get("") or {}).get("state") == "off" or (p.get(s["meal"]) or {}).get("state") == "off"
            if off and s["status"] == "active" and not scheduler.is_past(s, at):
                cur.execute("UPDATE sessions SET status='skipped', pinned=NULL, note=? WHERE id=?", (OFF_NOTE, s["id"]))
            elif not off and s["status"] == "skipped" and (s.get("note") or "") == OFF_NOTE:
                cur.execute("UPDATE sessions SET status='active', note='' WHERE id=?", (s["id"],))


def impact_for(user_id: int, date: str, cost: float, *, user: dict | None = None) -> dict:
    """The impact of the person's own pick/pin of `cost` on `date`, already saved: the week as
    it is now against the same week without it. Shown, never enforced."""
    after, env, days, scope = _week(user_id, date, user=user)
    before_days = [dict(d, committed=max(0.0, d["committed"] - cost)) if d["date"] == date else d for d in days]
    before = allocate(env, before_days, scope=scope)
    out = impact(before, after, date, today=clock.today().isoformat())
    if after["is_over"]:
        out["suggest"] = suggest_cuts(user_id, after, date)
    return out


def suggest_cuts(user_id: int, alloc: dict, keep_date: str) -> list[dict]:
    """Pin over the whole week: which of Ziggy's own (not pinned) meals to cook or skip to
    make room, dearest first, until the overshoot is covered. Proposals only."""
    from ..kernel import scheduler
    from . import models
    plan_id = alloc.get("plan_id")
    if not plan_id:
        return []
    plan = models.get_plan(plan_id)
    need = alloc["over"]
    at = clock.now()
    rows = [d for d in models.decisions_for_plan(plan_id)
            if d["session_status"] == "active" and not d.get("pinned") and d["chosen_kind"] == "delivery"
            and not scheduler.is_past(d, at)]
    rows.sort(key=lambda d: (-float(d.get("cost") or 0), d["day"]))
    out, got = [], 0.0
    for d in rows:
        if got >= need - 0.5:
            break
        out.append({"session_id": d["session_id"], "date": models.session_date(plan, d["day"]), "meal": d["meal"],
                    "name": d.get("item_name"), "cost": round(float(d["cost"]), 2), "act": "cook_or_skip"})
        got += float(d["cost"])
    return out


APPS = ("zomato", "toing", "swiggy", "restaurant", "other")


def record_outside(user_id: int, body: dict) -> dict:
    """Keep a bill from outside Ziggy (scope "all": it eats the same envelope). With `at` (when
    it was eaten), a meal after midnight belongs to the day before. With `meal`, that slot of the
    plan is marked eaten (skipped with the receipt as its note) so it isn't planned again."""
    import uuid
    from . import models
    try:
        amount = float(body.get("amount"))
    except (TypeError, ValueError):
        raise ValueError("Enter what the bill came to") from None
    if not math.isfinite(amount) or not 1 <= amount <= 50000:
        raise ValueError("A bill must be between ₹1 and ₹50,000")
    app = str(body.get("app") or "other").lower()
    if app not in APPS:
        raise ValueError("Zomato, Toing, Swiggy, a restaurant, or other")
    if body.get("at"):
        try:
            day = meal_day(dt.datetime.fromisoformat(str(body["at"])))
        except ValueError:
            raise ValueError("`at` must be an ISO time") from None
    elif body.get("date"):
        day = _date(body["date"])
    else:
        day = meal_day(clock.now())
    if day > clock.today():
        raise ValueError("A bill can't be from a day ahead")
    meal = body.get("meal")
    if meal is not None and meal not in MEALS + ("snack",):
        raise ValueError("Choose breakfast, lunch, dinner or snack")
    ref = f"outside:{uuid.uuid4().hex[:12]}"
    with db.cursor() as cur:
        cur.execute("INSERT INTO spend_ledger(user_id, ref, iso_date, amount, restaurant, source, confirmed) "
                    "VALUES (?,?,?,?,?,?,1)", (user_id, ref, day.isoformat(), round(amount, 2),
                                               str(body.get("restaurant") or app)[:80], "outside"))
    marked = None
    if meal in MEALS:
        plan = _plan_for_week(user_id, monday(day))
        if plan:
            idx = (day - dt.date.fromisoformat(plan["week_start"])).days
            s = next((x for x in models.sessions_for_plan(plan["id"]) if x["day"] == idx and x["meal"] == meal), None)
            if s and s["status"] in ("active", "skipped"):
                with db.cursor() as cur:
                    cur.execute("UPDATE sessions SET status='skipped', pinned=NULL, note=? WHERE id=?",
                                (f"Eaten: {app.title()} bill {rupees(amount)}", s["id"]))
                marked = s["id"]
        if day == clock.today():
            from .. import today as today_mod
            today_mod.clear_pick(user_id, meal)
    return {"ref": ref, "date": day.isoformat(), "amount": round(amount, 2), "marked_session": marked}


def remove_outside(user_id: int, ref: str) -> str | None:
    if not ref.startswith("outside:"):
        raise ValueError("Only a bill you added can be removed here")
    with db.cursor() as cur:
        row = cur.execute("SELECT iso_date FROM spend_ledger WHERE user_id=? AND ref=?", (user_id, ref)).fetchone()
        if not row:
            raise ValueError("That bill isn't there")
        cur.execute("DELETE FROM spend_ledger WHERE user_id=? AND ref=?", (user_id, ref))
    return row["iso_date"]
