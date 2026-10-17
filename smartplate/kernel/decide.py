""""Decide for me": plan today's remaining meals, or the week, by the explicit objective.

This runs only when the person taps "Decide for me" (Vijay, Oct 2026: the app never
auto-decides as the first step). It proposes; nothing is ordered. The order of
priority is in kernel/objective.py and docs/planner-objective.md:

    safe  >  within the money  >  favourites (what the mode allows)  >  no silent skips
          >  cheapest among equally liked  >  variety

Modes (all inside the person's rules: min rating, max price a meal, quantity, diet):
  favourites_only        their favourite dishes (meal lists) and every dish at their places
  favourites_plus_cook   … plus a home-cook for a meal, within their cook days
  favourites_plus_new    … plus places they haven't saved, rated at least their min rating
  surprise_me            anything inside their rules, picked for novelty (stable per day)

Small problems (today: up to 4 meals) are solved exactly by branch and bound (ms). A
week is solved exactly by a staged MILP (`solve_milp`, ~0.1–0.4 s), with a greedy
fill-then-upgrade pass as its floor; the benchmark that chose this is in
docs/testing/differential-w06.md.

The engine (`candidates_for_slot`, `solve`, `plan`) is pure: no database, no clock.
`gather` reads one person's inputs from the database for service.decide_today/week.
"""
from __future__ import annotations

import hashlib
import math
import time

from . import objective as obj

MODES = ("favourites_only", "favourites_plus_cook", "favourites_plus_new", "surprise_me")
DEFAULT_MODE = "favourites_only"
MODE_LABELS = {
    "favourites_only": "Favourites only",
    "favourites_plus_cook": "Favourites + cook",
    "favourites_plus_new": "Favourites + new places",
    "surprise_me": "Surprise me",
}
EXACT_MAX_SLOTS = 4          # up to this many meals: exact branch and bound
SURPRISE_JITTER = 0.30       # surprise_me: a stable per-day shuffle of this size
SURPRISE_NEW = 0.50          # surprise_me: bonus for a dish never ordered


def check_mode(mode: str | None) -> str:
    mode = mode or DEFAULT_MODE
    if mode not in MODES:
        raise ValueError(f"Choose one of: {', '.join(MODES)}")
    return mode


# --------------------------------------------------------------------------- #
# Candidates
# --------------------------------------------------------------------------- #
def _unsafe(person: dict, item: dict) -> str | None:
    from ..domain import allergens
    return allergens.violates({**person, "household_members": []}, item)


def _jitter(seed: str, item_id) -> float:
    h = hashlib.blake2b(f"{seed}|{item_id}".encode(), digest_size=4).digest()
    return int.from_bytes(h, "big") / 2**32 * SURPRISE_JITTER


def _diet_word(person: dict) -> str:
    word = {"egg": "eggetarian", "jain": "Jain"}
    bits = [word.get(person.get("diet"), person.get("diet") or "")] if person.get("diet") in ("veg", "vegan", "egg", "jain") else []
    bits += [f"no {a}" for a in person.get("allergens") or []]
    bits += list(person.get("medical") or [])
    return ", ".join(b for b in bits if b) or "their rules"


def candidates_for_slot(slot: dict, menu: list[dict], *, mode: str, people: list[dict],
                        places: set, pool_ids: set, history: dict | None = None, liked: set = frozenset(),
                        disliked: set = frozenset(), rules: dict | None = None, cook: dict | None = None,
                        seed: str = "") -> tuple[list[dict], str | None]:
    """(candidates, reason there are none). Every candidate is safe for every eater,
    allowed by the mode, and inside the person's rules. A shared meal with no dish safe
    for everyone gets a split: one dish per group, from one restaurant (one cart)."""
    from .optimizer import meal_suitable
    from ..domain import timing
    rules = rules or {}
    meal = slot["meal"]
    eaters = slot.get("eaters") or people
    n_people = len(eaters)
    qty_each = max(1, int(rules.get("qty") or 1)) if n_people == 1 else 1
    min_rating = float(rules.get("min_rating") or 0)
    max_price = rules.get("max_price_per_meal")
    max_eta = rules.get("max_eta")                 # Today's "max delivery time" rule (0/None = no limit)
    hidden = {"rating": 0, "price": 0, "meal": 0, "disliked": 0, "eta": 0}
    eligible_any = False
    by_place: dict = {}
    for it in menu:
        pooled = it["id"] in pool_ids
        if pooled:
            tier = obj.POOL
        elif it["restaurant_id"] in places:
            tier = obj.PLACE
        elif mode in ("favourites_plus_new", "surprise_me"):
            tier = obj.NEW
        else:
            continue
        if it["id"] in disliked:
            hidden["disliked"] += 1
            continue
        if not meal_suitable(it, meal, chosen=pooled) or not timing.slot_suitability(
                it, meal, favourited_for_slot=pooled):
            hidden["meal"] += 1
            continue
        rating = it.get("restaurant_rating")
        # A dish the person put in their own list with no known rating (typed in) is theirs:
        # unknown stays unknown, it isn't hidden as "below the rating". Everything else is.
        if (rating is None and not pooled) or (rating is not None and rating < min_rating):
            if min_rating:
                hidden["rating"] += 1
                continue
        if max_eta and it.get("eta_min") is not None and it["eta_min"] > float(max_eta):
            hidden["eta"] += 1
            continue
        if max_price is not None and it["price"] > float(max_price):
            hidden["price"] += 1
            continue
        eligible_any = True
        safe_for = [i for i, p in enumerate(eaters) if _unsafe(p, it) is None]
        if not safe_for:
            continue
        nonveg_eater = bool(rules.get("nonveg_pref", True)) and all(
            (p.get("diet") or "nonveg") == "nonveg" for p in eaters)
        score, parts = obj.favourite_score(it, meal, tier=tier, history=history, liked=it["id"] in liked,
                                           nonveg_eater=nonveg_eater)
        if mode == "surprise_me":
            h = (history or {}).get(it["id"]) or {}
            score = round(parts["rating"] + (SURPRISE_NEW if not h.get("count") else 0.0)
                          + _jitter(f"{seed}|{slot.get('day')}|{meal}", it["id"]), 4)
        by_place.setdefault(it["restaurant_id"], []).append(
            {"item": it, "tier": tier, "score": score, "parts": parts, "safe_for": set(safe_for)})

    cands = []
    everyone = set(range(n_people))
    pool_dishes = [d for ds in by_place.values() for d in ds if d["tier"] == obj.POOL]
    pool_cheapest = min((d["item"]["price"] for d in pool_dishes if d["safe_for"] == everyone), default=None)
    pool_unsafe_for = None
    if pool_ids and pool_cheapest is None:
        # their own dishes for this meal exist but none is safe for everyone eating: say for whom
        blocked = [eaters[i].get("name") or "you" for i in range(n_people)
                   if any(it["id"] in pool_ids and _unsafe(eaters[i], it) for it in menu)]
        pool_unsafe_for = ", ".join(dict.fromkeys(blocked)) or None
    for rid, dishes in by_place.items():
        fee = float(dishes[0]["item"].get("delivery_fee") or 0)
        shared = [d for d in dishes if d["safe_for"] == everyone]
        for d in shared:
            q = (int(d["item"].get("qty") or qty_each)) if n_people == 1 else n_people * qty_each
            cands.append(_delivery(slot, [(d, q, everyone)], fee, eaters, min_rating, history,
                                   pool_cheapest, pool_unsafe_for))
        if not shared and n_people > 1 and set().union(*(d["safe_for"] for d in dishes)) == everyone:
            # Greedy cover: the dish safe for most of those not yet covered (then best liked, cheapest).
            lines, left = [], set(everyone)
            while left:
                d = max(dishes, key=lambda d: (len(d["safe_for"] & left), obj.quantize(d["score"]),
                                               -d["item"]["price"]))
                got = d["safe_for"] & left
                lines.append((d, len(got) * qty_each, got))
                left -= got
            cands.append(_delivery(slot, lines, fee, eaters, min_rating, history, pool_cheapest, pool_unsafe_for))
    if cook is not None:
        cands.append({"kind": "cook", "tier": obj.COOK, "name": cook["name"], "recipe_key": cook.get("key"),
                      "restaurant": "Home kitchen", "restaurant_id": None, "item_ids": (),
                      "lines": [], "cost": round(float(cook.get("cost") or 0) * n_people, 2),
                      "score": 0.55, "parts": {}, "rating": None})
    if cands:
        return cands, None
    # Why there is nothing: who can't eat safely, or which rule hid what.
    if eligible_any:
        covered = set().union(*(d["safe_for"] for ds in by_place.values() for d in ds))
        stuck = [eaters[i] for i in range(n_people) if i not in covered]
        if stuck:
            who = stuck[0]
            name = who.get("name") or "you"
            return [], f"Nothing at your places is safe for {name} ({_diet_word(who)}) for {meal}."
        return [], f"No single place has a safe {meal} for everyone eating."
    hid = [f"{n} {w}" for w, n in (("below your min rating", hidden["rating"]), ("over your price a meal",
                                    hidden["price"]), ("over your delivery time", hidden["eta"]),
                                   ("not a whole " + meal, hidden["meal"]),
                                   ("you said not again", hidden["disliked"])) if n]
    where = "your places" if mode in ("favourites_only", "favourites_plus_cook") else "your places or new ones"
    return [], (f"Nothing from {where} fits {meal}" + (f" ({'; '.join(hid)})" if hid else "") + ".")


def _delivery(slot, lines, fee, eaters, min_rating, history, pool_cheapest, pool_unsafe_for=None) -> dict:
    cost = round(sum(d["item"]["price"] * q for d, q, _ in lines) + fee, 2)
    total_q = sum(q for _, q, _ in lines) or 1
    score = round(sum(d["score"] * q for d, q, _ in lines) / total_q, 4)
    main = max(lines, key=lambda ln: ln[1])[0]
    it = main["item"]
    h = (history or {}).get(it["id"]) or {}
    tier = min((d["tier"] for d, _, _ in lines), key=[obj.POOL, obj.PLACE, obj.NEW].index)
    names = [p.get("name") or "you" for p in eaters]
    return {
        "kind": "delivery", "tier": tier, "name": it["name"] if len(lines) == 1 else " + ".join(
            d["item"]["name"] for d, _, _ in lines),
        "restaurant": it["restaurant_name"], "restaurant_id": it["restaurant_id"], "item_id": it["id"],
        "item_ids": tuple(d["item"]["id"] for d, _, _ in lines),
        "lines": [{"item_id": d["item"]["id"], "name": d["item"]["name"], "qty": q, "price": d["item"]["price"],
                   "for": [names[i] for i in sorted(who)]} for d, q, who in lines],
        "cost": cost, "delivery_fee": fee, "score": score, "parts": main["parts"],
        "rating": it.get("restaurant_rating"), "times_ordered": h.get("count", 0), "min_rating": min_rating,
        "veg": bool(it.get("veg", 1)),
        "cheaper_than_pool": tier == obj.PLACE and pool_cheapest is not None and it["price"] < pool_cheapest,
        "pool_unsafe_for": pool_unsafe_for if tier != obj.POOL else None,
    }


# --------------------------------------------------------------------------- #
# Solving
# --------------------------------------------------------------------------- #
def _caps(slots, cand_lists, pool_ids_by_meal, max_repeat):
    """Per-dish weekly cap: the variety setting, or more for a small meal list that has to
    cover all of that meal's slots (one breakfast dish → every breakfast)."""
    caps = {}
    for meal, ids in (pool_ids_by_meal or {}).items():
        n = sum(1 for s in slots if s["meal"] == meal)
        if ids and n:
            for i in ids:
                caps[i] = max(caps.get(i, max_repeat), math.ceil(n / len(ids)))
    return caps


class _State:
    def __init__(self, slots, money, rules, caps):
        self.money, self.rules, self.caps = money, rules, caps
        self.max_repeat = int(rules.get("max_repeat") or 2)
        self.cook_cap = int(rules.get("cook_cap", 99))
        self.daily_cap = rules.get("daily_cap")                    # hard only: the person's "never above"
        self.day_money = dict(rules.get("day_money") or {})       # day -> money already fixed that day
        self.day_caps = dict(rules.get("day_caps") or {})         # day -> open money of a hard day amount
        self.targets = dict(rules.get("day_targets") or {})       # day -> soft target of open money
        self.spent = 0.0
        self.item_n: dict = {}
        self.item_day: dict = {}
        self.day_spent: dict = {}
        self.cooks = 0

    def fits(self, slot, c, old=None) -> bool:
        if c["kind"] == "skip":
            return True
        d_old = old["cost"] if old else 0.0
        if self.spent - d_old + c["cost"] > self.money + 1e-6:
            return False
        if self.daily_cap is not None:
            day = slot["day"]
            if (self.day_spent.get(day, 0.0) - (d_old if old else 0.0) + c["cost"]
                    + self.day_money.get(day, 0.0) > float(self.daily_cap) + 1e-6):
                return False
        cap = self.day_caps.get(slot["day"])
        if cap is not None and (self.day_spent.get(slot["day"], 0.0) - d_old + c["cost"] > float(cap) + 1e-6):
            return False
        if c["kind"] == "cook" and (old is None or old["kind"] != "cook") and self.cooks >= self.cook_cap:
            return False
        old_ids = set(old.get("item_ids") or ()) if old else set()
        for i in set(c.get("item_ids") or ()):
            if i in old_ids:
                continue
            if self.item_n.get(i, 0) >= self.caps.get(i, self.max_repeat):
                return False
            if self.item_day.get((i, slot["day"]), 0) >= 1:
                return False
        return True

    def over(self) -> float:
        """Money above the days' soft targets (only days that have one)."""
        return sum(max(0.0, self.day_spent.get(d, 0.0) - float(t)) for d, t in self.targets.items())

    def add(self, slot, c, sign=1):
        if c["kind"] == "skip":
            return
        self.spent += sign * c["cost"]
        self.day_spent[slot["day"]] = self.day_spent.get(slot["day"], 0.0) + sign * c["cost"]
        if c["kind"] == "cook":
            self.cooks += sign
        for i in set(c.get("item_ids") or ()):
            self.item_n[i] = self.item_n.get(i, 0) + sign
            self.item_day[(i, slot["day"])] = self.item_day.get((i, slot["day"]), 0) + sign


SKIP = {"kind": "skip", "cost": 0.0, "score": 0.0, "item_ids": ()}


def _over(chosen, slots, rules) -> float:
    targets = (rules or {}).get("day_targets") or {}
    if not targets or slots is None:
        return 0.0
    by_day: dict = {}
    for s, c in zip(slots, chosen):
        by_day[s["day"]] = by_day.get(s["day"], 0.0) + c["cost"]
    return sum(max(0.0, by_day.get(d, 0.0) - float(t)) for d, t in targets.items())


def _key(chosen, slots=None, rules=None) -> tuple:
    filled = sum(1 for c in chosen if c["kind"] != "skip")
    q = sum(obj.quantize(c["score"]) for c in chosen if c["kind"] != "skip")
    cost = sum(c["cost"] for c in chosen)
    return obj.plan_key(filled, q, cost, over=_over(chosen, slots, rules))


def _undominated(cands, n, keep_max: int | None = None):
    """Drop a candidate dominated (liked no more, costs no less) by n others: with at most
    n meals, one of those n is always free to take its place, so the optimum is kept.
    O(K log K). `keep_max` additionally bounds the list (best-liked, then cheapest)."""
    import bisect
    cs = sorted(cands, key=lambda c: (-obj.quantize(c["score"]), c["cost"]))
    keep, costs, seen = [], [], {}
    for c in cs:
        k = (obj.quantize(c["score"]), c["cost"])
        # earlier = liked at least as much; dominated by those of them costing no more
        # (an identical twin is not a dominator)
        dom = bisect.bisect_right(costs, c["cost"]) - seen.get(k, 0)
        if dom < n:
            keep.append(c)
        bisect.insort(costs, c["cost"])
        seen[k] = seen.get(k, 0) + 1
    if keep_max is not None and len(keep) > keep_max:
        cheapest = sorted(keep, key=lambda c: c["cost"])[: keep_max // 3]
        ids = {id(c) for c in cheapest}
        keep = [c for c in keep if id(c) not in ids][: keep_max - len(cheapest)] + cheapest
    return keep


def _exact(slots, cand_lists, money, rules, caps):
    """Branch and bound over the plan key. Exact for the problem as stated."""
    n = len(slots)
    lists = []
    for cands in cand_lists:
        lists.append(_undominated(cands, n) + [SKIP])
    best_q = [max((obj.quantize(c["score"]) for c in cs if c["kind"] != "skip"), default=0) for cs in lists]
    has = [any(c["kind"] != "skip" for c in cs) for cs in lists]
    st = _State(slots, money, rules, caps)
    best = {"key": None, "pick": None}
    chosen = []

    def dfs(i, filled, q, cost):
        if i == n:
            k = obj.plan_key(filled, q, cost, over=st.over())
            if best["key"] is None or k > best["key"]:
                best["key"], best["pick"] = k, list(chosen)
            return
        if best["key"] is not None:
            bound = obj.plan_key(filled + sum(has[i:]), q + sum(best_q[i:]), cost, over=st.over())
            if bound <= best["key"]:
                return
        for c in lists[i]:
            if not st.fits(slots[i], c):
                continue
            st.add(slots[i], c)
            chosen.append(c)
            dfs(i + 1, filled + (c["kind"] != "skip"), q + (obj.quantize(c["score"]) if c["kind"] != "skip" else 0),
                cost + c["cost"])
            chosen.pop()
            st.add(slots[i], c, -1)

    dfs(0, 0, 0, 0.0)
    return best["pick"] or [SKIP] * n


def _greedy(slots, cand_lists, money, rules, caps):
    """Fill the most meals first (cheapest allowed dish, cheapest meals first), then spend
    what's left on the best-liked upgrades per rupee, then take the cheaper of equals."""
    n = len(slots)
    st = _State(slots, money, rules, caps)
    chosen = [SKIP] * n
    order = sorted(range(n), key=lambda i: (min((c["cost"] for c in cand_lists[i]), default=math.inf), i))
    for i in order:
        for c in sorted(cand_lists[i], key=lambda c: (c["cost"], -c["score"])):
            if st.fits(slots[i], c):
                st.add(slots[i], c)
                chosen[i] = c
                break
    _upgrade(slots, cand_lists, chosen, st)
    _pair_exchange(slots, cand_lists, chosen, st)
    for i in range(n):                                  # rule 5: cheaper among equals
        cur = chosen[i]
        if cur["kind"] == "skip":
            continue
        for c in sorted(cand_lists[i], key=lambda c: c["cost"]):
            if c["cost"] < cur["cost"] and obj.quantize(c["score"]) == obj.quantize(cur["score"]) \
                    and st.fits(slots[i], c, old=cur):
                st.add(slots[i], cur, -1)
                st.add(slots[i], c)
                chosen[i] = c
                break
    _less_over(slots, cand_lists, chosen, st)
    # a meal left empty early may fit now (an upgrade elsewhere never frees money, but
    # rule 5 may have): try once more, cheapest first
    for i in order:
        if chosen[i]["kind"] == "skip":
            for c in sorted(cand_lists[i], key=lambda c: c["cost"]):
                if st.fits(slots[i], c):
                    st.add(slots[i], c)
                    chosen[i] = c
                    break
    return chosen


def _less_over(slots, cand_lists, chosen, st):
    """Greedy mirror of the MILP's third stage: among equally liked dishes, take the one that
    puts the week least over the days' soft targets (then the cheaper)."""
    if not st.targets:
        return
    for i in range(len(slots)):
        cur = chosen[i]
        if cur["kind"] == "skip":
            continue
        base = st.over()
        best = None
        for c in cand_lists[i]:
            if c is cur or obj.quantize(c["score"]) != obj.quantize(cur["score"]) or not st.fits(slots[i], c, old=cur):
                continue
            st.add(slots[i], cur, -1)
            st.add(slots[i], c)
            o = st.over()
            st.add(slots[i], c, -1)
            st.add(slots[i], cur)
            if o < base - 1e-6 and (best is None or (o, c["cost"]) < best[0]):
                best = ((o, c["cost"]), c)
        if best:
            st.add(slots[i], cur, -1)
            st.add(slots[i], best[1])
            chosen[i] = best[1]


def _pair_exchange(slots, cand_lists, chosen, st, max_rounds: int = 60):
    """Local search: upgrade one meal and pay for it by stepping another meal down, when
    the pair raises the total favourite score. (The greedy alone was up to 13% below the
    exact MILP on random weeks; see docs/testing/differential-w06.md.)"""
    n = len(slots)
    q = obj.quantize
    for _ in range(max_rounds):
        left = st.money - st.spent
        best = None
        for i in range(n):
            ci = chosen[i]
            if ci["kind"] == "skip":
                continue
            for c in cand_lists[i]:
                dqi = q(c["score"]) - q(ci["score"])
                if dqi <= 0:
                    continue
                need = c["cost"] - ci["cost"] - left      # > 0: money-blocked; else cap-blocked
                for j in range(n):
                    cj = chosen[j]
                    if j == i or cj["kind"] == "skip":
                        continue
                    for d in cand_lists[j]:
                        if d is cj or d["cost"] > cj["cost"] - need + 1e-9:
                            continue
                        gain = dqi + q(d["score"]) - q(cj["score"])
                        if gain <= 0 or (best is not None and (gain, -(c["cost"] + d["cost"])) <= best[0]):
                            continue
                        st.add(slots[j], cj, -1)
                        ok = st.fits(slots[j], d)
                        if ok:
                            st.add(slots[j], d)
                            ok = st.fits(slots[i], c, old=ci)
                            st.add(slots[j], d, -1)
                        st.add(slots[j], cj)
                        if ok:
                            best = ((gain, -(c["cost"] + d["cost"])), i, c, j, d)
        if best is None:
            return
        _, i, c, j, d = best
        st.add(slots[j], chosen[j], -1)
        st.add(slots[j], d)
        chosen[j] = d
        st.add(slots[i], chosen[i], -1)
        st.add(slots[i], c)
        chosen[i] = c
        # money freed by the pair may buy plain upgrades again
        _upgrade(slots, cand_lists, chosen, st)


def _upgrade(slots, cand_lists, chosen, st):
    n = len(slots)
    while True:
        best = None
        for i in range(n):
            cur = chosen[i]
            if cur["kind"] == "skip":
                continue
            cq = obj.quantize(cur["score"])
            for c in cand_lists[i]:
                dq = obj.quantize(c["score"]) - cq
                if dq <= 0:
                    continue
                dc = c["cost"] - cur["cost"]
                if not st.fits(slots[i], c, old=cur):
                    continue
                rank = (1, dq, -dc) if dc <= 0 else (0, dq / dc, dq)
                if best is None or rank > best[0]:
                    best = (rank, i, c)
        if best is None:
            return
        _, i, c = best
        st.add(slots[i], chosen[i], -1)
        st.add(slots[i], c)
        chosen[i] = c


MILP_MAX_NODES = 5000
MILP_PER_SLOT = 24
MILP_TIME_LIMIT_S = 30
MILP_LAST = {"proven": None}


def solve_milp(slots, cand_lists, money, rules, caps=None):
    """Exact reference for the same plan key, by staged MILP (PuLP/CBC): most meals,
    then highest favourite score, then cheapest. Used to measure the greedy."""
    import pulp
    caps = caps or {}
    max_repeat = int(rules.get("max_repeat") or 2)
    prob = pulp.LpProblem("decide", pulp.LpMaximize)
    x = {(i, j): pulp.LpVariable(f"x_{i}_{j}", cat="Binary")
         for i, cs in enumerate(cand_lists) for j in range(len(cs))}
    for i, cs in enumerate(cand_lists):
        prob += pulp.lpSum(x[(i, j)] for j in range(len(cs))) <= 1
    prob += pulp.lpSum(c["cost"] * x[(i, j)] for i, cs in enumerate(cand_lists) for j, c in enumerate(cs)) <= money
    for day, cap in (rules.get("day_caps") or {}).items():
        terms = [c["cost"] * x[(i, j)] for i, cs in enumerate(cand_lists) if slots[i]["day"] == day
                 for j, c in enumerate(cs)]
        if terms:
            prob += pulp.lpSum(terms) <= float(cap)
    overs = []
    for day, target in (rules.get("day_targets") or {}).items():
        terms = [c["cost"] * x[(i, j)] for i, cs in enumerate(cand_lists) if slots[i]["day"] == day
                 for j, c in enumerate(cs)]
        if terms:
            o = pulp.LpVariable(f"over_{day}", lowBound=0)
            prob += pulp.lpSum(terms) - o <= float(target)
            overs.append(o)
    if rules.get("daily_cap") is not None:
        for day in {s["day"] for s in slots}:
            prob += pulp.lpSum(c["cost"] * x[(i, j)] for i, cs in enumerate(cand_lists) if slots[i]["day"] == day
                               for j, c in enumerate(cs)) <= float(rules["daily_cap"]) - \
                (rules.get("day_money") or {}).get(day, 0.0)
    cooks = [x[(i, j)] for i, cs in enumerate(cand_lists) for j, c in enumerate(cs) if c["kind"] == "cook"]
    if cooks:
        prob += pulp.lpSum(cooks) <= int(rules.get("cook_cap", 99))
    uses: dict = {}
    for i, cs in enumerate(cand_lists):
        for j, c in enumerate(cs):
            for it in set(c.get("item_ids") or ()):
                uses.setdefault(it, []).append((i, j))
    for it, ij in uses.items():
        prob += pulp.lpSum(x[k] for k in ij) <= caps.get(it, max_repeat)
        for day in {slots[i]["day"] for i, _ in ij}:
            prob += pulp.lpSum(x[(i, j)] for i, j in ij if slots[i]["day"] == day) <= 1
    filled = pulp.lpSum(x.values())
    score = pulp.lpSum(obj.quantize(c["score"]) * x[(i, j)] for i, cs in enumerate(cand_lists)
                       for j, c in enumerate(cs))
    cost = pulp.lpSum(c["cost"] * x[(i, j)] for i, cs in enumerate(cand_lists) for j, c in enumerate(cs))
    # A node cap (deterministic, like the week planner's) so a hard instance can't hang;
    # MILP_LAST says whether every stage was proven optimal.
    solver = pulp.PULP_CBC_CMD(msg=False, maxNodes=MILP_MAX_NODES, timeLimit=MILP_TIME_LIMIT_S)
    MILP_LAST["proven"] = True
    for stage in (filled, score):
        prob.setObjective(stage)
        prob.solve(solver)
        MILP_LAST["proven"] &= prob.status == pulp.LpStatusOptimal and prob.sol_status == pulp.LpSolutionOptimal
        prob += stage >= round(pulp.value(stage) or 0)
    if overs:                              # third stage: least money over the days' soft targets
        over_sum = pulp.lpSum(overs)
        prob.setObjective(-over_sum)
        prob.solve(solver)
        MILP_LAST["proven"] &= prob.sol_status == pulp.LpSolutionOptimal
        prob += over_sum <= (pulp.value(over_sum) or 0) + 0.01
    prob.setObjective(-cost)
    prob.solve(solver)
    MILP_LAST["proven"] &= prob.sol_status == pulp.LpSolutionOptimal
    out = []
    for i, cs in enumerate(cand_lists):
        j = next((j for j in range(len(cs)) if (x[(i, j)].value() or 0) > 0.5), None)
        out.append(cs[j] if j is not None else SKIP)
    return out


def solve(slots, cand_lists, money, rules, *, caps=None, method="auto"):
    caps = caps or {}
    if method == "milp":
        return solve_milp(slots, cand_lists, money, rules, caps), "milp"
    if method == "exact" or (method == "auto" and len(slots) <= EXACT_MAX_SLOTS):
        return _exact(slots, cand_lists, money, rules, caps), "exact"
    greedy = _greedy(slots, cand_lists, money, rules, caps)
    if method == "greedy":
        return greedy, "greedy"
    # A week: the greedy matches the exact MILP on meals filled but was 0–13% below it on
    # favourite score in the benchmark (docs/testing/differential-w06.md), so the week is
    # solved exactly (≈0.1–0.4 s) and the greedy stays as the floor if CBC hits its cap.
    try:
        # every slot keeps its undominated dishes, at most MILP_PER_SLOT (best-liked and the
        # cheapest): a bound for CBC on big menus, never a taste rule
        bounded = [_undominated(cs, len(slots), MILP_PER_SLOT) for cs in cand_lists]
        milp = solve_milp(slots, bounded, money, rules, caps)
    except Exception:                                    # no CBC binary: the greedy answer
        return greedy, "greedy"
    if _key(milp, slots, rules) >= _key(greedy, slots, rules):
        return milp, "milp" if MILP_LAST["proven"] else "milp-capped"
    return greedy, "greedy"


def plan(slots: list[dict], menu: list[dict], *, mode: str, people: list[dict], places: set,
         pools: dict, money: float, rules: dict | None = None, history: dict | None = None,
         liked: set = frozenset(), disliked: set = frozenset(), cook_for=None, seed: str = "",
         money_word: str = "this week's", method: str = "auto") -> dict:
    """Pure: pick one answer (or an honest skip) for each slot. `pools` is {meal: set of
    dish ids}; `cook_for(meal)` returns a safe recipe or None."""
    t0 = time.perf_counter()
    mode = check_mode(mode)
    rules = dict(rules or {})
    rules["_mode"] = mode
    cand_lists, none_why = [], []
    for s in slots:
        cook = cook_for(s["meal"]) if (cook_for and mode == "favourites_plus_cook") else None
        cands, why = candidates_for_slot(s, menu, mode=mode, people=people, places=places,
                                         pool_ids=set((pools or {}).get(s["meal"]) or ()), history=history,
                                         liked=liked, disliked=disliked, rules=rules, cook=cook, seed=seed)
        cand_lists.append(cands)
        none_why.append(why)
    caps = _caps(slots, cand_lists, pools, int(rules.get("max_repeat") or 2))
    chosen, method_used = solve(slots, cand_lists, money, rules, caps=caps, method=method)
    spent = round(sum(c["cost"] for c in chosen), 2)
    left = round(money - spent, 2)
    picks = []
    for s, c, cands, why in zip(slots, chosen, cand_lists, none_why):
        base = {"day": s["day"], "meal": s["meal"], "session_id": s.get("session_id"),
                "eaters": [p.get("name") for p in (s.get("eaters") or people)]}
        if c["kind"] == "skip":
            picks.append({**base, "kind": "skip", "cost": 0.0,
                          "reason": why or _money_reason(cands, left, money_word, rules, s, chosen, slots)})
        else:
            pick = {**base, **{k: v for k, v in c.items() if k not in ("item_ids",)}, "meal": s["meal"]}
            pick["why"] = obj.why_pick(pick, money_word=money_word)
            picks.append(pick)
    for p in picks:
        if p["kind"] == "skip":
            p["why"] = p["reason"]
    return {"mode": mode, "mode_label": MODE_LABELS[mode], "money": round(money, 2), "spent": spent,
            "left": left, "picks": picks, "method": method_used,
            "filled": sum(1 for p in picks if p["kind"] != "skip"), "meals": len(picks),
            "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1)}


def _money_reason(cands, left, money_word, rules, slot, chosen, slots) -> str:
    cooks_used = sum(1 for c in chosen if c["kind"] == "cook")
    open_cands = [c for c in cands if c["kind"] != "cook" or cooks_used < int(rules.get("cook_cap", 99))]
    if not open_cands:
        return "Skipped: your cook days for the week are already planned and nothing else fits."
    cheapest = min(c["cost"] for c in open_cands)
    if rules.get("daily_cap") is not None:
        day_spent = sum(c["cost"] for s, c in zip(slots, chosen) if s["day"] == slot["day"]) + \
            (rules.get("day_money") or {}).get(slot["day"], 0.0)
        day_left = max(0.0, float(rules["daily_cap"]) - day_spent)
        if day_left < cheapest <= left:
            return (f"Skipped: ₹{day_left:.0f} of your daily limit is left; the cheapest allowed "
                    f"{slot['meal']} is ₹{cheapest:.0f}.")
    if cheapest > left:
        return (f"Skipped: ₹{max(0.0, left):.0f} of {money_word} money is left; the cheapest allowed "
                f"{slot['meal']} is ₹{cheapest:.0f}.")
    hint = {"favourites_only": " Add a dish or a place, or allow cooking or new places, to fill it.",
            "favourites_plus_cook": " Add a dish or a place, or allow new places, to fill it."}.get(
        rules.get("_mode"), "")
    return ("Skipped: the dishes that fit the money left are already planned as often as your variety "
            "setting allows." + hint)


# --------------------------------------------------------------------------- #
# Inputs from the database
# --------------------------------------------------------------------------- #
def rules_for(user: dict) -> dict:
    """The person's own rules (Settings / the filter chips): min rating, max price a meal,
    quantity, how often a dish may repeat, cook days, daily limit."""
    from ..domain import profile
    from . import optimizer
    prefs = user.get("prefs") or {}
    ht = user.get("health_targets") or {}

    def num(v, cast=float):
        try:
            return cast(v) if v not in (None, "") else None
        except (TypeError, ValueError):
            return None
    return {"min_rating": float(user.get("rating_floor") or 0),
            "max_price_per_meal": num(prefs.get("max_price_per_meal") or ht.get("max_price_per_meal")),
            "qty": num(prefs.get("qty"), int) or 1,
            "max_repeat": optimizer.item_repeat(user),
            "cook_cap": optimizer._cook_cap(user),
            # the Settings daily limit binds only as the person's hard "never above" (v2)
            "daily_cap": profile.daily_cap(user) if profile.daily_cap_mode(user) == "hard" else None,
            # a non-veg eater's non-veg dishes score a little higher, unless they said otherwise
            "nonveg_pref": prefs.get("nonveg_pref", True) is not False}


def gather(user: dict) -> dict:
    """menu, people, places, pools, history, liked, disliked for one person."""
    from ..domain import meal_pools, models, taste
    menu = models.menu_for_user(user)
    sig = taste.signals(user["id"])
    live = any(it.get("source") == "live" for it in menu)
    pools = {m: set(p["ids"]) for m, p in (meal_pools.for_planner(user["id"]) if live else {}).items()}
    places = set(sig["favourites"])
    if live:
        # The live catalogue holds only the person's places (saved + pooled restaurants).
        places |= {it["restaurant_id"] for it in menu}
    places |= {it["restaurant_id"] for it in menu if any(it["id"] in ids for ids in pools.values())}
    me = {k: user.get(k) for k in ("id", "name", "diet", "allergens", "medical")}
    others = list(user.get("household_members") or [])
    return {"menu": menu, "me": me, "others": others, "places": places, "pools": pools,
            "history": sig["history"], "liked": sig["liked"], "disliked": sig["disliked"]}
