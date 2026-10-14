"""When the person's favourites don't fit the week's money: scenarios they choose from.

Founder decision (14 Oct 2026): favourites take priority whenever the budget allows, and
Ziggy never silently swaps a favourite for a cheaper dish or skips it. When a meal's own
favourites (its meal list) don't fit the money left, the planner leaves that meal open
with a reason that starts with `optimizer.BUILDOUT_PREFIX`, and this module offers a
"build-out" set of scenarios, each with its effect in ₹:

  • skip    another planned meal (the freed money lets the favourite in)
  • swap    a planned dish for a cheaper favourite from the same meal list
  • cook    the open meal at home
  • cheaper a cheaper dish from their own places for the open meal (the old silent
            fallback, now only when they pick it)
  • raise   the weekly target by the exact ₹ that makes every favourite fit

Nothing is applied until the person picks one (`apply`). Every scenario is re-derived at
apply time, so a stale or made-up id is refused.
"""
from __future__ import annotations

import datetime as dt
import math

from ..kernel import scheduler

from .. import db
from . import models

MAX_PER_KIND = 2
DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def _label(session_or_decision: dict) -> str:
    return f"{DAYS[int(session_or_decision['day']) % 7]} {session_or_decision['meal']}"


def _rupees(v: float) -> str:
    return f"₹{v:,.0f}"


def open_meals(decisions: list[dict], at: dt.datetime | None = None) -> list[dict]:
    """Meals the planner left open because their own favourites didn't fit the money."""
    from ..kernel import optimizer
    at = at or optimizer.now()
    return [d for d in decisions
            if d["chosen_kind"] == "skip" and d.get("session_status") == "active"
            and not scheduler.is_past(d, at)
            and (d.get("reasons") or [""])[0].startswith(optimizer.BUILDOUT_PREFIX)]


def summary(plan_id: int, decisions: list[dict] | None = None) -> dict:
    """Cheap: which meals are open for a build-out (for the plan view and heads-up)."""
    decisions = decisions if decisions is not None else models.decisions_for_plan(plan_id)
    rows = open_meals(decisions)
    return {"needed": bool(rows), "meals": [{"session_id": d["session_id"], "label": _label(d)} for d in rows],
            "count": len(rows)}


def scenarios(plan_id: int) -> dict:
    """{"needed", "gap", "remaining", "open": [...], "scenarios": [...]} for this week."""
    from ..kernel import optimizer
    from . import allergens, reverse_mode
    plan = models.get_plan(plan_id)
    if not plan:
        raise KeyError("Plan not found")
    user = models.get_user(plan["user_id"])
    decisions = models.decisions_for_plan(plan_id)
    at = optimizer.now()
    unfit = open_meals(decisions, at)
    if not unfit:
        return {"needed": False, "open": [], "scenarios": [], "gap": 0.0,
                "note": "Your favourites fit this week's money."}
    sessions = {s["id"]: s for s in models.sessions_for_plan(plan_id)}
    ctx = optimizer.build_context(user, plan)
    ctx["allow_cheaper"] = True                 # to SEE the cheaper dishes; nothing is applied here
    planned = [d for d in decisions if d["chosen_kind"] in ("delivery", "cook")
               and not scheduler.is_past(d, at)]
    cap = optimizer.week_cap(user, plan)
    committed = optimizer.committed_spend(plan_id)["total"]
    remaining = round(cap - committed - sum(float(d["cost"] or 0) for d in planned
                                            if d.get("session_status") == "active"), 2)

    open_rows, need = [], 0.0
    for d in unfit:
        s = sessions[d["session_id"]]
        cands = optimizer.build_candidates(user, plan, s, ctx)
        favs = sorted((c for c in cands if c["kind"] == "delivery" and not c.get("pool_fallback")),
                      key=lambda c: (c["cost"], c["item_id"]))
        cheaper = sorted((c for c in cands if c["kind"] == "delivery" and c.get("pool_fallback")),
                         key=lambda c: (c["cost"], c["item_id"]))
        if not favs:
            continue
        need += favs[0]["cost"]
        open_rows.append({"session_id": s["id"], "label": _label(s), "meal": s["meal"], "day": s["day"],
                          "favourite": {"item_id": favs[0]["item_id"], "name": favs[0]["item_name"],
                                        "cost": favs[0]["cost"]},
                          "_cheaper": cheaper, "_session": s})
    gap = round(max(0.0, need - max(0.0, remaining)), 2)
    # What the whole week costs with every pooled meal on a favourite, the way the planner
    # would plan it: cheapest first, each dish at most as often as the variety rule lets a
    # meal list repeat (optimizer: max(your repeat setting, meals ÷ list size)).
    full_gap = _full_gap(user, plan, ctx, decisions, sessions, unfit, cap, committed, at)
    gap = max(gap, full_gap) if gap > 0 else gap
    out = []

    # 1. skip another planned meal (not a pin: that is their own pick)
    if gap > 0:
        frees = sorted((d for d in planned if d["chosen_kind"] == "delivery" and not d.get("pinned")
                        and d.get("session_status") == "active" and float(d["cost"] or 0) > 0),
                       key=lambda d: (float(d["cost"]) < gap, abs(float(d["cost"]) - gap), d["session_id"]))
        for d in frees[:MAX_PER_KIND]:
            fixes = "every favourite fits" if float(d["cost"]) >= gap else f"{_rupees(gap - float(d['cost']))} still short"
            out.append({"id": f"skip:{d['session_id']}", "kind": "skip", "session_id": d["session_id"],
                        "title": f"Skip {_label(d)} ({d['item_name']}, {_rupees(float(d['cost']))})",
                        "detail": f"Frees {_rupees(float(d['cost']))}: {fixes}.",
                        "saves": round(float(d["cost"]), 2), "closes_gap": float(d["cost"]) >= gap})

    # 2. swap a planned dish for a cheaper favourite from the same meal list
    pools = ctx.get("pools") or {}
    if gap > 0 and pools:
        menu = {it["id"]: it for it in ctx["menu"]}
        swaps = []
        for d in planned:
            pool = pools.get(d["meal"])
            if d["chosen_kind"] != "delivery" or not pool or d.get("pinned") or d.get("session_status") != "active":
                continue
            s = sessions.get(d["session_id"])
            for c in optimizer.build_candidates(user, plan, s, ctx) if s else []:
                if (c["kind"] == "delivery" and not c.get("pool_fallback") and c["item_id"] in pool["ids"]
                        and c["item_id"] != d["item_id"] and c["cost"] < float(d["cost"]) and c["item_id"] in menu
                        and not allergens.violates(user, menu[c["item_id"]])):
                    swaps.append((d, c, round(float(d["cost"]) - c["cost"], 2)))
        swaps.sort(key=lambda t: (t[2] < gap, abs(t[2] - gap), t[0]["session_id"], t[1]["item_id"]))
        seen = set()
        for d, c, saving in swaps:
            if d["session_id"] in seen or len(seen) >= MAX_PER_KIND:
                continue
            seen.add(d["session_id"])
            out.append({"id": f"swap:{d['session_id']}:{c['item_id']}", "kind": "swap", "session_id": d["session_id"],
                        "item_id": c["item_id"],
                        "title": f"Swap {d['item_name']} on {_label(d)} for your cheaper favourite {c['item_name']}",
                        "detail": f"{_rupees(saving)} less ({_rupees(float(d['cost']))} → {_rupees(c['cost'])}).",
                        "saves": saving, "closes_gap": saving >= gap})

    for row in open_rows:
        s = row["_session"]
        # 3. cook the open meal at home
        r = reverse_mode.cook_candidate(user, s["meal"])
        if r and not reverse_mode.unsafe_reason(user, r):
            cost = round(float(r.get("cost") or 0) * optimizer.portions(user, s, ctx), 2)
            out.append({"id": f"cook:{s['id']}:{r['key']}", "kind": "cook", "session_id": s["id"],
                        "recipe_key": r["key"],
                        "title": f"Cook {row['label']} at home ({r['name']})",
                        "detail": ("Groceries aren't priced for this dish." if r.get("cost_unknown") else
                                   f"About {_rupees(cost)} of groceries instead of "
                                   f"{_rupees(row['favourite']['cost'])} delivered."),
                        "costs": cost, "closes_gap": None})
        # 4. a cheaper dish from their own places, only because they pick it
        for c in [c for c in row["_cheaper"] if c["cost"] <= max(0.0, remaining)][:1]:
            out.append({"id": f"cheaper:{s['id']}:{c['item_id']}", "kind": "cheaper", "session_id": s["id"],
                        "item_id": c["item_id"],
                        "title": f"Have {c['item_name']} from {c['restaurant_name']} for {row['label']}",
                        "detail": (f"{_rupees(c['cost'])}: not one of your {s['meal']} favourites "
                                   f"({row['favourite']['name']} is {_rupees(row['favourite']['cost'])}), "
                                   "but it fits the money left."),
                        "costs": c["cost"], "closes_gap": None})

    # 5. raise the weekly target by exactly what makes every favourite fit
    if gap > 0:
        covered = max(1e-9, cap / max(1e-9, float(user["weekly_budget"]))) if user["weekly_budget"] else 1.0
        raise_by = int(math.ceil(max(gap, full_gap) / covered / 10.0) * 10)
        new = float(user["weekly_budget"]) + raise_by
        out.append({"id": f"raise:{raise_by}", "kind": "raise", "amount": raise_by,
                    "title": f"Raise the weekly target by {_rupees(raise_by)} ({_rupees(float(user['weekly_budget']))} "
                             f"→ {_rupees(new)})",
                    "detail": "Every favourite fits this week.", "costs": float(raise_by), "closes_gap": True})

    opened = [{k: v for k, v in row.items() if not k.startswith("_")} for row in open_rows]
    names = ", ".join(r["label"] for r in opened)
    return {"needed": True, "open": opened, "gap": gap, "remaining": max(0.0, remaining),
            "scenarios": out,
            "note": (f"Your favourites for {names} don't fit this week's money "
                     f"({_rupees(gap)} short). Ziggy didn't swap them for something cheaper or skip "
                     "anything: pick how to build out the week." if gap > 0 else
                     f"Your favourites for {names} were left open: pick how to fill "
                     f"{'it' if len(opened) == 1 else 'them'}.")}


def _full_gap(user, plan, ctx, decisions, sessions, unfit, cap, committed, at) -> float:
    from ..kernel import optimizer
    pools = ctx.get("pools") or {}
    unfit_ids = {d["session_id"] for d in unfit}
    fixed, slots = 0.0, {}
    for d in decisions:
        if d.get("session_status") != "active" or scheduler.is_past(d, at):
            continue
        pooled = d["meal"] in pools and not d.get("pinned") and (
            d["session_id"] in unfit_ids or d["chosen_kind"] == "delivery")
        if pooled:
            slots.setdefault(d["meal"], []).append(d["session_id"])
        elif d["chosen_kind"] in ("delivery", "cook"):
            fixed += float(d["cost"] or 0)
    repeat = optimizer.item_repeat(user)
    total = fixed
    for meal, sids in slots.items():
        s = sessions.get(sids[0])
        if not s:
            continue
        costs = sorted({c["item_id"]: c["cost"] for c in optimizer.build_candidates(user, plan, s, ctx)
                        if c["kind"] == "delivery" and not c.get("pool_fallback")}.items(), key=lambda kv: kv[1])
        if not costs:
            continue
        per = max(repeat, math.ceil(len(sids) / max(1, len(pools[meal]["ids"]) or 1)))
        left = len(sids)
        for _, cost in costs:
            take = min(per, left)
            total += take * cost
            left -= take
            if not left:
                break
        total += left * costs[-1][1]
    return round(max(0.0, total - (cap - committed)), 2)


def apply(plan_id: int, scenario_id: str) -> dict:
    """Apply one scenario the person picked; returns the new plan view with a fresh build-out."""
    from .. import everyday, service
    from ..kernel import optimizer
    current = scenarios(plan_id)
    pick = next((s for s in current["scenarios"] if s["id"] == scenario_id), None)
    if not pick:
        raise ValueError("That choice isn't on offer any more. Here are the current ones.")
    kind = pick["kind"]
    if kind == "skip":
        view = everyday.choose(pick["session_id"], {"action": "skip"})
    elif kind in ("swap", "cheaper"):
        view = everyday.choose(pick["session_id"], {"item_id": pick["item_id"]})
    elif kind == "cook":
        view = everyday.choose(pick["session_id"], {"recipe_key": pick["recipe_key"]})
    elif kind == "raise":
        plan = models.get_plan(plan_id)
        with db.cursor() as cur:
            cur.execute("UPDATE users SET weekly_budget = weekly_budget + ? WHERE id=?",
                        (float(pick["amount"]), plan["user_id"]))
        optimizer.optimize(plan_id)
        view = service.plan_view(plan_id)
    else:                                               # pragma: no cover - kinds are closed
        raise ValueError("Unknown choice")
    view["buildout_applied"] = {"id": scenario_id, "title": pick["title"]}
    view["buildout"] = scenarios(plan_id)
    return view
