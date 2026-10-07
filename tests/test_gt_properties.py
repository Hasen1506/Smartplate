"""Property-based ground truth (Hypothesis): invariants that must never break.

Runs are deterministic (derandomized, no example database — see conftest.py's "ci"
profile) and seeded from the frozen clock. `HYPOTHESIS_PROFILE=deep` explores ~400
examples per property for a local soak.

Invariants:
  1. allergen / medical / diet rules: nothing unsafe is planned, pinned or carted
  2. vegetarian and vegan profiles never get non-veg (eggs are non-veg, FSSAI)
  3. solver spend never exceeds the weekly room or the daily cap (all-skip is
     always feasible, so a feasible plan always exists)
  4. observed fast days keep breakfast and lunch clear
  5. every enabled meal slot exists once and holds exactly one decision
  6. pinned meals survive every kind of re-plan while they stay safe
  7. prices add up: item + fees (× surge) = cost; Σ items = checkout total;
     a live cart total is read exactly or not at all
  8. nothing is ordered without an explicit approval bound to the exact total
  9. fuzzed API input never produces a 500
"""
import datetime as dt
import json
import math

import pytest
from hypothesis import assume, example, given, note, settings
from hypothesis import strategies as st

from gt_support import (MONDAY_8AM, NAVRATRI_MONDAY, check_plan, connect_swiggy, item_violation,
                        safe_meal_items)
from smartplate import config, db, everyday, service
from smartplate.domain import models, profile
from smartplate.kernel import optimizer, scheduler

MEALS = list(profile.MEALS)
INSTANTS = [
    MONDAY_8AM,                                   # whole week ahead
    dt.datetime(2026, 11, 2, 14, 30),             # Monday afternoon: breakfast and lunch already past
    dt.datetime(2026, 11, 4, 12, 0),              # Wednesday: prorated week
    dt.datetime(2026, 11, 7, 21, 0),              # Saturday night: one day left
    dt.datetime(2026, 11, 8, 10, 0),              # Sunday: plans next week
    NAVRATRI_MONDAY,                              # a week of Navratri fast days
]


@st.composite
def profiles(draw):
    body = {
        "name": "Prop",
        "diet": draw(st.sampled_from(profile.DIETS)),
        "allergens": draw(st.lists(st.sampled_from(profile.ALLERGENS), unique=True, max_size=3)),
        "medical": draw(st.lists(st.sampled_from(profile.MEDICAL), unique=True, max_size=2)),
        "observances": draw(st.lists(st.sampled_from(profile.OBSERVANCES), unique=True, max_size=1)),
        "weekly_budget": draw(st.integers(100, 6000)),
        "meals": draw(st.lists(st.sampled_from(MEALS), unique=True, min_size=1)),
        "cook": draw(st.sampled_from(sorted(everyday.COOK_BY_ANSWER))),
        "rating_floor": draw(st.sampled_from([3.5, 3.8, 4.0, 4.2, 4.5])),
        "goal": draw(st.sampled_from(sorted(profile.GOALS))),
        "variety": draw(st.sampled_from(profile.VARIETY)),
        "favourites": draw(st.lists(st.integers(1, 12), unique=True, max_size=4)),
    }
    if draw(st.booleans()):
        lo = max(profile.DAILY_CAP_MIN, math.ceil(body["weekly_budget"] * 0.3 / 7))
        hi = min(profile.DAILY_CAP_MAX, body["weekly_budget"])
        if lo <= hi:
            body["daily_cap"] = draw(st.integers(lo, hi))
    return body


def _with_budget(body, weekly):
    """A different budget invalidates a drawn daily cap; drop it rather than half-fix it."""
    out = {**body, "weekly_budget": weekly}
    out.pop("daily_cap", None)
    return out


def _create(gt, body, at, mode=None):
    from smartplate import ratelimit
    ratelimit.reset()                     # one database serves every example of a test
    gt.set(at)
    view = everyday.create_profile(dict(body))
    pid = view["plan"]["id"]
    if mode and mode != view["plan"]["mode"]:
        service.reoptimize(pid, mode)
    return view["user"]["id"], pid


def _assert_clean(pid):
    failures = check_plan(pid)
    assert not failures, "\n".join(failures)


# --------------------------------------------------------------------------- #
# 1–5: every plan obeys the hard rules
# --------------------------------------------------------------------------- #
@given(body=profiles(), at=st.sampled_from(INSTANTS), mode=st.sampled_from(sorted(config.MODE_LABELS)))
def test_every_plan_obeys_hard_invariants(gt, body, at, mode):
    uid, pid = _create(gt, body, at, mode)
    note(f"user {uid} plan {pid}")
    _assert_clean(pid)
    # the options sheet never offers an unsafe dish or recipe either
    user = models.get_user(uid)
    menu = {it["id"]: it for it in models.menu_for_city(user["city"])}
    open_s = [s for s in models.sessions_for_plan(pid) if s["status"] == "active"
              and not scheduler.is_past(s, optimizer.now())]
    if open_s:
        sheet = everyday.options(open_s[0]["id"])
        offered = [d["item_id"] for g in sheet["usual"] for d in g["dishes"]] + [d["item_id"] for d in sheet["new"]]
        for iid in offered:
            assert item_violation(user, menu[iid]) is None, (menu[iid]["name"], user["diet"], user["allergens"])


@given(body=profiles(), mode=st.sampled_from(["balanced", "comfort"]))
def test_no_needless_skips_when_money_and_safe_dishes_suffice(gt, body, mode):
    """'Every enabled meal slot is filled': when the budget clearly covers every meal
    and there are enough distinct safe dishes (each may repeat twice a week), the
    planner must not leave a meal empty and blame the budget."""
    body = {**_with_budget(body, 6000), "observances": []}
    uid, pid = _create(gt, body, MONDAY_8AM, mode)
    user = models.get_user(uid)
    open_s = [s for s in models.sessions_for_plan(pid) if s["status"] == "active"
              and not scheduler.is_past(s, optimizer.now())]
    safe = safe_meal_items(user)
    costs = sorted((it["price"] + it["delivery_fee"]) * 1.6 for it in safe for _ in range(2))
    per_day = {}
    for s in open_s:
        per_day[s["day"]] = per_day.get(s["day"], 0) + 1
    enough_dishes = len(safe) >= max(per_day.values(), default=0) and 2 * len(safe) >= len(open_s)
    assume(enough_dishes and sum(costs[:len(open_s)]) <= optimizer.week_cap(user, models.get_plan(pid)))
    skipped = [d for d in models.decisions_for_plan(pid)
               if d["chosen_kind"] == "skip" and d["session_status"] == "active"]
    assert not skipped, [(models.DAYS[d["day"]], d["meal"], d["reasons"]) for d in skipped]


# --------------------------------------------------------------------------- #
# 6: pins survive re-plans
# --------------------------------------------------------------------------- #
REPLANS = st.lists(st.sampled_from(["reoptimize", "mode", "favourite", "budget_down", "budget_up",
                                    "rate_other", "swap_others", "later_today", "add_meal"]),
                   min_size=1, max_size=4)


@given(body=profiles(), ops=REPLANS, pick=st.integers(0, 10_000))
def test_pinned_meals_survive_replans(gt, body, ops, pick):
    body = {**_with_budget(body, max(body["weekly_budget"], 1200)), "observances": []}
    uid, pid = _create(gt, body, MONDAY_8AM)
    user = models.get_user(uid)
    safe = safe_meal_items(user)
    sessions = [s for s in models.sessions_for_plan(pid) if s["day"] >= 1]     # tomorrow on: never goes past
    assume(safe and sessions)
    target = sessions[pick % len(sessions)]
    item = safe[pick % len(safe)]
    everyday.choose(target["id"], {"item_id": item["id"]})
    for op in ops:
        others = [s for s in models.sessions_for_plan(pid) if s["id"] != target["id"]
                  and s["status"] == "active" and not scheduler.is_past(s, optimizer.now())]
        if op == "reoptimize":
            service.reoptimize(pid)
        elif op == "mode":
            service.reoptimize(pid, "survival" if models.get_plan(pid)["mode"] != "survival" else "comfort")
        elif op == "favourite":
            everyday.toggle_favourite(uid, 1 + pick % 12)
        elif op == "budget_down":
            everyday.update_setup(uid, {"weekly_budget": 100})
        elif op == "budget_up":
            everyday.update_setup(uid, {"weekly_budget": 9000})
        elif op == "rate_other":
            rated = next((s for s in others if any(d["session_id"] == s["id"] and d["chosen_kind"] == "delivery"
                                                   for d in models.decisions_for_plan(pid))), None)
            if rated:
                everyday.rate(rated["id"], -1)
        elif op == "swap_others" and len(others) >= 2:
            try:
                everyday.swap(pid, others[0]["id"], others[1]["id"])
            except ValueError:
                pass                                     # e.g. a leftover can't move — not this invariant
        elif op == "later_today":
            gt.advance(hours=6)
        elif op == "add_meal":
            everyday.update_setup(uid, {"meals": MEALS})
        s = models.get_session(target["id"])
        cell = next(d for d in models.decisions_for_plan(pid) if d["session_id"] == target["id"])
        assert s["pinned"] and json.loads(s["pinned"]) == {"kind": "delivery", "item_id": item["id"]}, op
        assert cell["chosen_kind"] == "delivery" and cell["item_id"] == item["id"], (op, cell["item_name"])
        _assert_clean(pid)


@given(body=profiles())
def test_a_pin_that_becomes_unsafe_is_dropped_never_kept(gt, body):
    body = {**body, "observances": [], "allergens": [], "medical": [], "diet": "nonveg"}
    uid, pid = _create(gt, body, MONDAY_8AM)
    egg = next(it for it in models.menu_for_city("Chennai") if "egg" in it["allergens"]
               and optimizer.meal_suitable(it, "dinner"))
    target = next(s for s in models.sessions_for_plan(pid) if s["day"] >= 1)
    everyday.choose(target["id"], {"item_id": egg["id"]})
    everyday.update_setup(uid, {"allergens": ["egg"]})
    cell = next(d for d in models.decisions_for_plan(pid) if d["session_id"] == target["id"])
    assert cell.get("item_id") != egg["id"] and not models.get_session(target["id"])["pinned"]
    _assert_clean(pid)


# --------------------------------------------------------------------------- #
# 7: money adds up
# --------------------------------------------------------------------------- #
@given(body=profiles(), at=st.sampled_from(INSTANTS))
def test_checkout_preview_and_budget_add_up(gt, body, at):
    uid, pid = _create(gt, body, at)
    view = service.plan_view(pid)
    preview = service.execution_preview(pid)
    assert preview["total"] == round(sum(i["amount"] for i in preview["items"]), 2)
    decisions = {d["id"]: d for d in models.decisions_for_plan(pid)}
    for i in preview["items"]:
        d = decisions[i["decision_id"]]
        assert d["chosen_kind"] == "delivery" and d["session_status"] == "active"
        assert not scheduler.is_past(d, optimizer.now())
        assert i["amount"] == round(d["cost"], 2)
    planned = [d for d in decisions.values() if d["chosen_kind"] in ("delivery", "cook")
               and not (d["session_status"] == "active" and scheduler.is_past(d, optimizer.now()))]
    assert abs(view["budget"]["spend"] - round(sum(d["cost"] for d in planned), 2)) <= 0.011
    grid_cost = sum(c["cost"] for day in view["grid"] for c in day["meals"].values()
                    if c["kind"] in ("delivery", "cook") and c.get("status") != "past")
    assert abs(view["budget"]["spend"] - grid_cost) <= 0.011 * max(1, len(planned))
    # approve exactly the reviewed total → receipts equal what was placed
    result = service.execute(pid, expected_fingerprint=preview["fingerprint"], max_total=preview["total"])
    placed = [r for r in result["results"] if r["placed"]]
    assert round(sum(r["cost"] for r in placed), 2) <= preview["total"] + 0.011
    receipts = service.receipts_view(uid)
    assert receipts["total"] == round(sum(r["amount"] for r in receipts["rows"]), 2)


@given(price=st.integers(1000, 90000), fee=st.integers(0, 9900), unit=st.sampled_from(["paise", "rupees", "bare"]))
def test_live_cart_total_is_item_plus_fees_or_nothing(price, fee, unit):
    """Swiggy's payable total is read exactly (item + fees) or refused — never guessed."""
    from smartplate.integrations import swiggy_live
    item, fees = price / 100, fee / 100
    total = round(item + fees, 2)
    if unit == "paise":
        cart = {"pricing": {"item_total": item, "delivery_charge": fees, "toPayInPaise": price + fee}}
    elif unit == "rupees":
        cart = {"pricing": {"item_total": item, "delivery_charge": fees, "toPayInRupees": total}}
    else:
        cart = {"pricing": {"item_total": item, "delivery_charge": fees, "to_pay": total}}
    got = swiggy_live.cart_total(cart, anchor=item)
    assert got is None or got == total
    if unit != "bare":
        assert got == total


# --------------------------------------------------------------------------- #
# 8: no order without explicit approval
# --------------------------------------------------------------------------- #
APPROVALS = st.fixed_dictionaries({}, optional={
    "expected_fingerprint": st.one_of(st.none(), st.text(max_size=30), st.just("VALID"), st.integers(),
                                      st.just("VALID_STALE")),
    "max_total": st.one_of(st.none(), st.just("TOTAL"), st.just("TOTAL-1"), st.floats(allow_nan=True),
                           st.text(max_size=8), st.integers(-10, 10**6), st.booleans()),
})


def _orders_count():
    with db.cursor() as cur:
        return cur.execute("SELECT COUNT(*) FROM orders").fetchone()[0]


@given(body=profiles(), approval=APPROVALS)
def test_nothing_is_ordered_without_explicit_approval(gt, body, approval):
    from smartplate.app import create_app
    uid, pid = _create(gt, _with_budget(body, max(body["weekly_budget"], 2000)), MONDAY_8AM)
    client = create_app().test_client()
    preview = service.execution_preview(pid)
    sent = dict(approval)
    if sent.get("expected_fingerprint") == "VALID":
        sent["expected_fingerprint"] = preview["fingerprint"]
    if sent.get("expected_fingerprint") == "VALID_STALE":
        sent["expected_fingerprint"] = preview["fingerprint"]
        later = next((s for s in models.sessions_for_plan(pid) if s["day"] >= 1 and s["status"] == "active"), None)
        if later:                                              # the plan changes after the review
            service.set_session_status(later["id"], "skipped")
            service.reoptimize(pid)
    if sent.get("max_total") == "TOTAL":
        sent["max_total"] = preview["total"]
    elif sent.get("max_total") == "TOTAL-1":
        sent["max_total"] = preview["total"] - 1
    before = _orders_count()
    fresh = service.execution_preview(pid)
    headers = {"X-SmartPlate-Key": _key_for(uid)}
    r = client.post(f"/api/plan/{pid}/execute", json=sent, headers=headers)
    assert r.status_code != 500
    fp, mt = sent.get("expected_fingerprint"), sent.get("max_total")
    approved = (isinstance(fp, str) and fp == fresh["fingerprint"] and mt is not None
                and not isinstance(mt, (bool, str)) and math.isfinite(float(mt)) and float(mt) >= fresh["total"])
    if not approved or not fresh["order_count"]:
        assert _orders_count() == before, (sent, r.status_code, r.get_json())
        assert not any(s["status"] == "ordered" for s in models.sessions_for_plan(pid))
    else:
        assert r.status_code == 200, r.get_json()


def _key_for(uid):
    """create_profile returns the key once; re-issue one for API calls in this test."""
    from smartplate import access
    key, hashed = access.new_key()
    with db.cursor() as cur:
        cur.execute("UPDATE users SET access_hash=? WHERE id=?", (hashed, uid))
    return key


LIVE_BODIES = st.fixed_dictionaries({}, optional={
    "expected_fingerprint": st.one_of(st.none(), st.text(max_size=40), st.integers(), st.just("QUOTE"),
                                      st.just("CART_FP")),
    "restaurant_id": st.sampled_from(["r-1", "r-2", "", "x"]),
    "restaurant_name": st.sampled_from(["Hotel Saravana Bhavan (Adyar)", "", "Other"]),
    "item_id": st.sampled_from(["m0", "m1", "m9", ""]),
    "item_name": st.sampled_from(["Mini Tiffin", "Ghee Pongal", "", "Nope"]),
})


_VALID = {"restaurant_id": "r-1", "restaurant_name": "Hotel Saravana Bhavan (Adyar)", "item_id": "m0",
          "item_name": "Mini Tiffin"}


@settings(max_examples=40)
@given(steps=st.lists(st.tuples(st.sampled_from(["preview", "fill", "checkout_preview", "checkout"]), LIVE_BODIES),
                      min_size=1, max_size=6))
@example(steps=[("preview", _VALID), ("fill", {**_VALID, "expected_fingerprint": "CART_FP"}),
                ("checkout_preview", {}), ("checkout", {"expected_fingerprint": "QUOTE"}),
                ("checkout", {"expected_fingerprint": "QUOTE"})])          # the approved path, and no replay of it
@example(steps=[("preview", _VALID), ("fill", {**_VALID, "expected_fingerprint": "CART_FP"}),
                ("checkout", {"expected_fingerprint": "CART_FP"}), ("checkout", {})])   # cart ≠ approval
@example(steps=[("fill", {**_VALID, "expected_fingerprint": "guess"}), ("checkout_preview", {}),
                ("checkout", {"expected_fingerprint": "QUOTE"})])
def test_live_swiggy_never_carts_or_orders_without_approval(gt, monkeypatch, swiggy_replay, steps):
    """A real Swiggy cart change needs the exact reviewed item's fingerprint, and a real
    order needs a fresh checkout approval token for the exact cart. Random sequences
    of requests never get past either gate."""
    from smartplate import ratelimit
    from smartplate.app import create_app
    monkeypatch.setattr(config, "LIVE_ORDERS", True)
    ratelimit.reset()
    fake = swiggy_replay
    fake.reset()
    fake.strict = False               # random requests: unrecorded ones get Swiggy's refusal
    app = create_app()
    client = app.test_client()
    created = client.post("/api/profiles", json={"name": "Live", "diet": "veg", "weekly_budget": 2000,
                                                 "meals": ["dinner"]}).get_json()
    uid, key = created["user"]["id"], created["access_key"]
    h = {"X-SmartPlate-Key": key}
    connect_swiggy(client, fake, uid, key)
    reviewed, quote, placed = {}, None, []
    for step, body in steps:
        body = dict(body)
        if body.get("expected_fingerprint") == "CART_FP":
            body["expected_fingerprint"] = reviewed.get("fingerprint")
        if body.get("expected_fingerprint") == "QUOTE":
            body["expected_fingerprint"] = quote
        carts_before = fake.tool_calls().count("update_food_cart")
        orders_before = fake.tool_calls().count("place_food_order")
        if step == "preview":
            r = client.post(f"/api/user/{uid}/swiggy/live-cart/preview", json=body, headers=h)
            if r.status_code == 200:
                reviewed = {**r.get_json(), "_body": body}
        elif step == "fill":
            r = client.post(f"/api/user/{uid}/swiggy/live-cart", json=body, headers=h)
            ok = (body.get("expected_fingerprint") and reviewed.get("fingerprint") == body.get("expected_fingerprint")
                  and all(str(body.get(k) or "") == str(reviewed["_body"].get(k) or "")
                          for k in ("restaurant_id", "item_id", "item_name")))
            if fake.tool_calls().count("update_food_cart") > carts_before:
                assert ok, (body, reviewed)
        elif step == "checkout_preview":
            r = client.get(f"/api/user/{uid}/swiggy/checkout/preview", headers=h)
            if r.status_code == 200:
                quote = r.get_json()["fingerprint"]
        else:
            r = client.post(f"/api/user/{uid}/swiggy/checkout", json=body, headers=h)
            if fake.tool_calls().count("place_food_order") > orders_before:
                assert quote and body.get("expected_fingerprint") == quote, body
                assert fake.tool_calls().count("place_food_order") == orders_before + 1
                placed.append(r.get_json())
            quote = None if body.get("expected_fingerprint") == quote else quote
        assert r.status_code != 500, (step, body, r.get_json())
    if steps and steps[-2:] == [("checkout", {"expected_fingerprint": "QUOTE"})] * 2 and steps[0][0] == "preview":
        assert len(placed) == 1 and placed[0]["order_id"] == "real-order-17"     # approved once, never replayed


# --------------------------------------------------------------------------- #
# 9: fuzzed API input never 500s
# --------------------------------------------------------------------------- #
JSONISH = st.recursive(
    st.one_of(st.none(), st.booleans(), st.integers(-10**12, 10**12), st.floats(allow_nan=True, allow_infinity=True),
              st.text(max_size=12)),
    lambda inner: st.one_of(st.lists(inner, max_size=3), st.dictionaries(st.text(max_size=8), inner, max_size=3)),
    max_leaves=6)
KNOWN_KEYS = ["user_id", "mode", "text", "status", "note", "date", "meal", "source", "expected_fingerprint",
              "max_total", "title", "show_name", "plan_id", "ics", "reviews", "name", "diet", "allergens",
              "medical", "observances", "weekly_budget", "daily_cap", "meals", "goal", "body", "favourites",
              "variety", "rating_floor", "cook", "item_id", "recipe_key", "action", "score", "a", "b",
              "confirmation", "login", "password", "address_id", "restaurant_id", "restaurant_name",
              "item_name", "kcal", "protein_g", "max_cook_per_week", "subscription", "endpoint"]
BODIES = st.dictionaries(st.sampled_from(KNOWN_KEYS), JSONISH, max_size=5)
QUERY = st.dictionaries(st.sampled_from(["user_id", "city", "diet", "allergens", "medical", "restaurant",
                                         "query", "offset", "restaurant_id", "restaurant_name", "days", "fresh"]),
                        st.text(max_size=10), max_size=3)
IDS = st.one_of(st.integers(0, 6), st.integers(-3, 10**9))


def _routes():
    from smartplate.app import create_app
    app = create_app()
    out = []
    for rule in app.url_map.iter_rules():
        if rule.endpoint == "static" or not str(rule).startswith(("/api", "/swiggy", "/auth")):
            continue
        for method in sorted(rule.methods - {"HEAD", "OPTIONS"}):
            out.append((str(rule), method, sorted(rule.arguments)))
    return sorted(out)


@settings(max_examples=150)
@given(data=st.data())
def test_fuzzed_api_inputs_never_500(gt, monkeypatch, data):
    from smartplate import ratelimit
    from smartplate.app import create_app
    from smartplate.integrations import swiggy_connect
    ratelimit.reset()
    monkeypatch.setattr(swiggy_connect, "_http", lambda *a: (_ for _ in ()).throw(OSError("offline")))
    app = create_app()
    client = app.test_client()
    rule, method, args = data.draw(st.sampled_from(_routes()), label="route")
    path = rule
    for a in args:
        path = path.replace(f"<int:{a}>", str(data.draw(IDS, label=a))).replace(f"<{a}>", data.draw(
            st.text(alphabet="abc-_19", min_size=1, max_size=6), label=a))
    q = data.draw(QUERY, label="query")
    kwargs = {"query_string": q}
    if method in ("POST", "PATCH", "PUT", "DELETE"):
        kwargs["json"] = data.draw(st.one_of(BODIES, JSONISH), label="body")
    r = client.open(path, method=method, **kwargs)
    note(f"{method} {path} {kwargs} -> {r.status_code} {r.get_data(as_text=True)[:200]}")
    assert r.status_code != 500, (method, path, kwargs, r.get_data(as_text=True)[:300])
    if r.status_code >= 500:                # only deliberate upstream statuses: Swiggy (502/429) or live checkout off (503)
        assert r.status_code in (502, 503), r.status_code
