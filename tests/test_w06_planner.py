"""W6 planner: the explicit objective (docs/planner-objective.md), "Decide for me" modes,
today-only planning, cheaper-not-skip, household safety, diet mix, whole favourite places
and speed. Pure engine tests use hand-made menus; the rest run on the seeded catalogue
with a frozen clock."""
import random
import time

import pytest

from smartplate.kernel import decide, explainability, objective, optimizer


# --------------------------------------------------------------------------- #
# A tiny hand-made neighbourhood for the pure engine
# --------------------------------------------------------------------------- #
def dish(i, name, price, rid, *, rating=4.3, veg=1, allergens=(), tags=("main",), fee=35):
    return {"id": i, "name": name, "price": price, "restaurant_id": rid, "restaurant_name": f"R{rid}",
            "restaurant_rating": rating, "item_rating": rating, "veg": veg, "allergens": list(allergens),
            "tags": list(tags), "delivery_fee": fee}


ME = {"id": 1, "name": "Priya", "diet": "veg", "allergens": [], "medical": []}
MENU = [
    dish(1, "South Indian Meals", 190, 1),          # Priya's lunch favourite (pool)
    dish(2, "Curd Rice", 70, 1, allergens=("dairy",)),
    dish(3, "Lemon Rice", 60, 1),
    dish(4, "Masala Dosa", 120, 1, tags=("breakfast",)),
    dish(5, "Veg Fried Rice", 150, 2),               # a place she saved
    dish(6, "Paneer Butter Masala", 210, 3, rating=4.6, allergens=("dairy",)),   # a new place
    dish(7, "Rajma Rice", 140, 4, rating=3.6),       # new place below her 4★ rule
    dish(8, "Chicken Biryani", 220, 2, veg=0),
]
PLACES = {1, 2}
POOLS = {"lunch": {1}, "dinner": {4}}


def slots(*meals, day=0, eaters=None):
    return [{"day": day, "meal": m, "session_id": n, "eaters": eaters or [ME]} for n, m in enumerate(meals)]


def run(sl, money, mode="favourites_only", people=None, menu=MENU, rules=None, **kw):
    return decide.plan(sl, menu, mode=mode, people=people or [ME], places=PLACES, pools=POOLS, money=money,
                       rules={"min_rating": 4.0, "max_repeat": 2, **(rules or {})}, **kw)


def picks(out):
    return [(p["meal"], p["kind"], p.get("name")) for p in out["picks"]]


# --------------------------------------------------------------------------- #
# Rule order: safe > money > favourites > no silent skips > cheapest
# --------------------------------------------------------------------------- #
def test_favourite_wins_when_the_money_allows():
    out = run(slots("lunch"), 500)
    assert picks(out) == [("lunch", "delivery", "South Indian Meals")]
    assert out["picks"][0]["why"].startswith("Your lunch favourite")


def test_a_cheaper_dish_from_your_places_beats_a_skip():
    """usab #10: at ₹1,500 a week every lunch was skipped. A ₹95 lemon rice from a place
    she uses beats skipping lunch, and the reason says so."""
    out = run(slots("lunch"), 100)
    assert picks(out) == [("lunch", "delivery", "Lemon Rice")]
    assert "isn't skipped" in out["picks"][0]["why"]


def test_skip_only_when_nothing_allowed_fits_and_it_says_how_much():
    out = run(slots("lunch"), 50)
    p = out["picks"][0]
    assert p["kind"] == "skip"
    assert p["why"] == "Skipped: ₹50 of this week's money is left; the cheapest allowed lunch is ₹95."


def test_filling_both_meals_outranks_one_favourite():
    """Rule 4 above the favourite score: two meals at ₹95 + ₹155 beat the ₹225 favourite
    and a skipped dinner."""
    out = run(slots("lunch", "dinner"), 260)
    assert [p["kind"] for p in out["picks"]] == ["delivery", "delivery"]
    assert out["spent"] <= 260


def test_cheapest_among_equally_liked():
    menu = [dish(1, "Veg Pulao", 150, 1), dish(2, "Veg Biryani", 149, 1)]
    out = decide.plan(slots("dinner"), menu, mode="favourites_only", people=[ME], places={1}, pools={},
                      money=1000, rules={"min_rating": 4.0})
    assert out["picks"][0]["name"] == "Veg Biryani"


def test_unsafe_dish_is_never_planned_even_as_a_favourite():
    vegan = {**ME, "diet": "vegan"}
    out = run(slots("lunch", "dinner", day=0), 1000, people=[vegan], rules={}, )
    names = {p.get("name") for p in out["picks"]}
    assert "Curd Rice" not in names and "Paneer Butter Masala" not in names


def test_money_is_never_exceeded_and_daily_limit_holds():
    week = [{"day": d, "meal": m, "eaters": [ME]} for d in range(7) for m in ("lunch", "dinner")]
    out = run(week, 1500, rules={"daily_cap": 230})
    assert out["spent"] <= 1500
    by_day = {}
    for p in out["picks"]:
        by_day[p["day"]] = by_day.get(p["day"], 0) + p["cost"]
    assert max(by_day.values()) <= 230


# --------------------------------------------------------------------------- #
# Modes and rules
# --------------------------------------------------------------------------- #
def test_favourites_only_never_plans_a_new_place():
    out = run(slots("dinner"), 1000, mode="favourites_only")
    assert all(p.get("restaurant_id") in PLACES for p in out["picks"] if p["kind"] == "delivery")


def test_new_places_mode_adds_places_above_the_min_rating_only():
    menu = [dish(6, "Paneer Butter Masala", 210, 3, rating=4.6), dish(7, "Rajma Rice", 40, 4, rating=3.6)]
    out = decide.plan(slots("dinner"), menu, mode="favourites_plus_new", people=[ME], places=set(), pools={},
                      money=1000, rules={"min_rating": 4.0})
    assert out["picks"][0]["name"] == "Paneer Butter Masala"
    assert "meets your 4.0★ rule" in out["picks"][0]["why"]
    only = decide.plan(slots("dinner"), menu, mode="favourites_only", people=[ME], places=set(), pools={},
                       money=1000, rules={"min_rating": 4.0})
    assert only["picks"][0]["kind"] == "skip"


def test_cook_mode_offers_a_home_cook_inside_cook_days():
    recipe = {"key": "dal", "name": "Dal + rice", "cost": 60}
    out = run(slots("lunch"), 70, mode="favourites_plus_cook", cook_for=lambda meal: recipe)
    assert out["picks"][0]["kind"] == "cook"
    none = run(slots("lunch"), 70, mode="favourites_plus_cook", cook_for=lambda meal: recipe, rules={"cook_cap": 0})
    assert none["picks"][0]["kind"] == "skip"


def test_surprise_me_is_stable_for_a_day_and_changes_with_the_seed():
    week = [{"day": d, "meal": "dinner", "eaters": [ME]} for d in range(7)]
    a = run(week, 5000, mode="surprise_me", seed="u1|2026-11-02")
    b = run(week, 5000, mode="surprise_me", seed="u1|2026-11-02")
    assert picks(a) == picks(b)
    c = [picks(run(week, 5000, mode="surprise_me", seed=f"u1|{s}")) for s in range(6)]
    assert len({tuple(x) for x in c}) > 1


def test_max_price_a_meal_and_min_rating_hide_dishes_and_say_so():
    out = run(slots("dinner"), 1000, rules={"max_price_per_meal": 50})
    assert out["picks"][0]["kind"] == "skip"
    assert "over your price a meal" in out["picks"][0]["why"]


def test_unknown_mode_is_refused():
    with pytest.raises(ValueError):
        run(slots("lunch"), 100, mode="random")


# --------------------------------------------------------------------------- #
# Household: per-member safety, splits, truthful reasons (usab #16, W10 simulation)
# --------------------------------------------------------------------------- #
DEV = {"id": 2, "name": "Dev", "diet": "vegan", "allergens": [], "medical": []}
SURESH = {"id": 3, "name": "Suresh", "diet": "nonveg", "allergens": [], "medical": []}


def test_shared_meal_uses_a_dish_safe_for_everyone():
    out = run(slots("lunch", eaters=[ME, DEV]), 1000, people=[ME, DEV])
    p = out["picks"][0]
    assert p["kind"] == "delivery" and p["name"] != "Curd Rice"
    assert p["lines"][0]["qty"] == 2
    assert p["cost"] == p["lines"][0]["price"] * 2 + 35


def test_one_dish_for_everyone_beats_a_split():
    """A dish safe for all three (Dal Khichdi) is one shared order, even though the others'
    favourite (Paneer Thali) would need a split."""
    m = [dish(1, "Paneer Thali", 180, 1, allergens=("dairy",)), dish(2, "Dal Khichdi", 120, 1)]
    anu = {**ME, "id": 4, "name": "Anu"}
    out = decide.plan(slots("dinner", eaters=[ME, DEV, anu]), m, mode="favourites_only", people=[ME, DEV, anu],
                      places={1}, pools={"dinner": {1}}, money=1000, rules={})
    p = out["picks"][0]
    assert [(ln["name"], ln["qty"]) for ln in p["lines"]] == [("Dal Khichdi", 3)]
    assert "isn't safe for Dev" in p["why"]


def test_split_lines_cover_each_eater_exactly_once():
    m = [dish(1, "Paneer Thali", 180, 1, allergens=("dairy",)), dish(2, "Chicken Meals", 200, 1, veg=0),
         dish(3, "Aloo Paratha", 90, 1, allergens=("dairy", "gluten"))]
    gluten_free = {"id": 5, "name": "Kavya", "diet": "nonveg", "allergens": ["gluten", "dairy"], "medical": []}
    out = decide.plan(slots("dinner", eaters=[ME, gluten_free]), m, mode="favourites_only",
                      people=[ME, gluten_free], places={1}, pools={}, money=1000, rules={})
    p = out["picks"][0]
    assert p["kind"] == "delivery" and len(p["lines"]) == 2
    covered = [who for ln in p["lines"] for who in ln["for"]]
    assert sorted(covered) == ["Kavya", "Priya"]
    assert next(ln for ln in p["lines"] if "Kavya" in ln["for"])["name"] == "Chicken Meals"


def test_household_skip_names_the_member_not_the_budget():
    dairy_only = [dish(1, "Veg Thali", 200, 1, allergens=("dairy",)),
                  dish(2, "Paneer Biryani", 220, 1, allergens=("dairy",))]
    out = decide.plan(slots("lunch", eaters=[ME, DEV]), dairy_only, mode="favourites_only", people=[ME, DEV],
                      places={1}, pools={"lunch": {1}}, money=6000, rules={})
    why = out["picks"][0]["why"]
    assert out["picks"][0]["kind"] == "skip"
    assert "Dev" in why and "vegan" in why and "budget" not in why.lower() and "money" not in why.lower()


def test_usual_dish_unsafe_for_a_member_says_so_on_the_stand_in():
    m = [dish(1, "Veg Thali", 200, 1, allergens=("dairy",)), dish(2, "Lemon Rice", 60, 1)]
    out = decide.plan(slots("lunch", eaters=[ME, DEV]), m, mode="favourites_only", people=[ME, DEV],
                      places={1}, pools={"lunch": {1}}, money=6000, rules={})
    assert out["picks"][0]["name"] == "Lemon Rice"
    assert "isn't safe for Dev" in out["picks"][0]["why"]


# --------------------------------------------------------------------------- #
# Diet mix (usab #11) and whole places (grill #5)
# --------------------------------------------------------------------------- #
def test_non_veg_eater_gets_the_non_veg_dish_at_equal_price():
    ravi = {"id": 9, "name": "Ravi", "diet": "nonveg", "allergens": [], "medical": []}
    m = [dish(1, "Dal Khichdi", 200, 1), dish(2, "Chicken Curry Meals", 205, 1, veg=0)]
    out = decide.plan(slots("dinner", eaters=[ravi]), m, mode="favourites_only", people=[ravi], places={1},
                      pools={}, money=1000, rules={})
    assert out["picks"][0]["name"] == "Chicken Curry Meals"
    off = decide.plan(slots("dinner", eaters=[ravi]), m, mode="favourites_only", people=[ravi], places={1},
                      pools={}, money=1000, rules={"nonveg_pref": False})
    assert off["picks"][0]["name"] == "Dal Khichdi"


def test_every_dish_at_a_favourite_place_is_a_candidate():
    """No '3 dishes per place' cap: a place with 30 dishes offers all 30."""
    m = [dish(i, f"Plate {i}", 100 + i, 1) for i in range(1, 31)]
    cands, _ = decide.candidates_for_slot({"day": 0, "meal": "dinner"}, m, mode="favourites_only", people=[ME],
                                          places={1}, pool_ids=set(), rules={})
    assert len(cands) == 30


def test_snacks_are_not_a_lunch_or_dinner_on_their_own():
    vada = dish(1, "Medu Vada", 55, 1, tags=())
    assert optimizer.meal_suitable(vada, "breakfast")
    assert not optimizer.meal_suitable(vada, "dinner")
    assert optimizer.meal_suitable(vada, "dinner", chosen=True)
    assert optimizer.meal_suitable(dish(2, "South Indian Meals", 120, 1), "lunch")


# --------------------------------------------------------------------------- #
# Exact vs greedy vs MILP, and speed
# --------------------------------------------------------------------------- #
def _random_problem(rng, n_slots, n_dishes=24, n_places=4):
    menu = [dish(i, f"D{i}", rng.randrange(60, 320, 5), rng.randrange(1, n_places + 1),
                 rating=round(rng.uniform(3.9, 4.8), 1), veg=rng.random() < 0.7,
                 tags=rng.choice([("main",), ("breakfast",), ("main",)])) for i in range(1, n_dishes + 1)]
    meals = ("breakfast", "lunch", "dinner")
    sl = [{"day": k // 3 if n_slots > 3 else 0, "meal": meals[k % 3], "eaters": [ME]} for k in range(n_slots)]
    pools = {m: {d["id"] for d in rng.sample(menu, 3)} for m in meals}
    hist = {d["id"]: {"count": rng.randrange(0, 6), "days_since": rng.randrange(0, 40)}
            for d in rng.sample(menu, 8)}
    money = rng.randrange(80, 230) * n_slots
    return sl, menu, pools, hist, money


def _lists(sl, menu, pools, hist):
    return [decide.candidates_for_slot(s, menu, mode="favourites_only", people=[ME], places={1, 2, 3},
                                       pool_ids=pools.get(s["meal"], set()), history=hist, rules={})[0]
            for s in sl]


def _key(chosen):
    return decide._key(chosen)


@pytest.mark.parametrize("seed", range(12))
def test_exact_today_matches_the_milp(seed):
    rng = random.Random(seed)
    sl, menu, pools, hist, money = _random_problem(rng, 3)
    lists = _lists(sl, menu, pools, hist)
    rules = {"max_repeat": 2}
    caps = decide._caps(sl, lists, pools, 2)
    exact = decide._exact(sl, lists, money, rules, caps)
    milp = decide.solve_milp(sl, lists, money, rules, caps)
    assert _key(exact)[:3] == _key(milp)[:3]


def test_week_benchmark_greedy_vs_exact_milp():
    """grill #53: benchmark the greedy against the exact MILP on random weeks. The greedy
    fills exactly as many meals (rule 4) but trails on favourite score, so the week uses the
    MILP with the greedy as its floor: `solve` is never worse than either."""
    gaps = []
    for seed in range(6):
        rng = random.Random(1000 + seed)
        sl, menu, pools, hist, money = _random_problem(rng, 14, n_dishes=30)
        lists = _lists(sl, menu, pools, hist)
        rules = {"max_repeat": 2}
        caps = decide._caps(sl, lists, pools, 2)
        g = _key(decide._greedy(sl, lists, money, rules, caps))
        m = _key(decide.solve_milp(sl, lists, money, rules, caps))
        chosen, method = decide.solve(sl, lists, money, rules, caps=caps)
        assert g[0] == m[0]                                  # same meals filled
        assert _key(chosen) >= max(g, m) and method.startswith("milp")
        gaps.append(0.0 if m[1] == 0 else (m[1] - g[1]) / m[1])
    assert max(gaps) < 0.2, gaps


def test_decide_today_is_fast_on_a_big_menu():
    rng = random.Random(7)
    menu = [dish(i, f"D{i}", rng.randrange(60, 400, 5), rng.randrange(1, 13),
                 rating=round(rng.uniform(3.9, 4.8), 1)) for i in range(1, 801)]
    sl = slots("breakfast", "lunch", "dinner")
    t0 = time.perf_counter()
    out = decide.plan(sl, menu, mode="favourites_plus_new", people=[ME], places=set(range(1, 7)),
                      pools={"lunch": {1, 2, 3}}, money=600, rules={"min_rating": 4.0})
    assert time.perf_counter() - t0 < 1.0
    assert out["method"] == "exact" and out["filled"] == 3


def test_week_decide_is_fast():
    rng = random.Random(8)
    sl, menu, pools, hist, money = _random_problem(rng, 21, n_dishes=200, n_places=10)
    t0 = time.perf_counter()
    out = decide.plan(sl, menu, mode="favourites_only", people=[ME], places=set(range(1, 11)), pools=pools,
                      money=money, history=hist, rules={})
    assert time.perf_counter() - t0 < 5.0 and out["method"] in ("milp", "milp-capped", "greedy")
    assert out["spent"] <= money


# --------------------------------------------------------------------------- #
# Explanations
# --------------------------------------------------------------------------- #
def test_skip_summary_counts_real_causes_not_budget():
    reasons = ["Nothing at your places is safe for Dev (vegan) for lunch.",
               "Your lunch dishes aren't safe for everyone eating (not vegan — Dev), and nothing else at your places is.",
               "Budget skip: ₹40 of this week's budget is left; the cheapest safe option here is ₹95."]
    lines = explainability.skip_summary(3, reasons)
    assert lines == ["2 meal(s) skipped: nothing at your places is safe for everyone eating.",
                     "1 meal(s) skipped: the money left doesn't cover them."]
    assert explainability.skip_summary(2, None) == ["2 meal(s) not planned."]


def test_the_objective_has_only_the_named_weights():
    s, parts = objective.favourite_score(dish(1, "South Indian Meals", 190, 1), "lunch", tier=objective.POOL,
                                         history={1: {"count": 2, "days_since": 0}}, liked=True)
    assert set(parts) == {"rating", "times", "slot", "recency", "pool", "place", "liked", "diet"}
    assert s == pytest.approx(0.35 * 4.3 / 5 + 0.30 * 0.5 + 0.20 + 0.15 + 0.5 + 0.2 + 0.2)


# --------------------------------------------------------------------------- #
# Service + API on the seeded catalogue (frozen clock)
# --------------------------------------------------------------------------- #
def _profile(body):
    from smartplate import everyday
    return everyday.create_profile(dict(body))


def test_decide_today_plans_only_todays_open_meals_inside_todays_money(gt):
    from smartplate import service
    v = _profile({"name": "Asha", "diet": "veg", "weekly_budget": 2100, "meals": ["breakfast", "lunch", "dinner"],
                  "cook": "never"})
    out = service.decide_today(v["user"]["id"], "favourites_only")
    assert out["horizon"] == "today" and out["method"] == "exact"
    assert {p["day"] for p in out["picks"]} == {0}
    assert out["money"] == pytest.approx(300.0)                # ₹2,100 over 7 days, Monday 08:00
    assert out["spent"] <= out["money"]
    for p in out["picks"]:
        assert p["why"]
    capped = service.decide_today(v["user"]["id"], "favourites_only", money=120)
    assert capped["money"] == 120 and capped["spent"] <= 120


def test_decide_today_after_lunch_has_only_dinner_left(gt):
    from smartplate import service
    import datetime as dt
    v = _profile({"name": "Asha", "diet": "veg", "weekly_budget": 2100, "meals": ["breakfast", "lunch", "dinner"],
                  "cook": "never"})
    gt.set(dt.datetime(2026, 11, 2, 16, 0))
    out = service.decide_today(v["user"]["id"])
    assert [p["meal"] for p in out["picks"]] == ["dinner"]


def test_decide_week_apply_pins_picks_and_stays_safe_and_in_budget(gt):
    from gt_support import check_plan
    from smartplate import service
    v = _profile({"name": "Ravi", "diet": "nonveg", "allergens": ["peanut"], "weekly_budget": 2500,
                  "meals": ["lunch", "dinner"], "cook": "sometimes", "favourites": [1, 2, 3, 4]})
    pid = v["plan"]["id"]
    out = service.decide_week(pid, "favourites_plus_cook", apply=True)
    assert out["spent"] <= out["money"] + 0.01
    assert not check_plan(pid)
    view = out["view"]
    planned = {c["session_id"]: c for d in view["grid"] for c in d["meals"].values()}
    for p in out["picks"]:
        if p["kind"] == "delivery" and len(p["lines"]) == 1:
            assert planned[p["session_id"]]["item"] == p["name"]
            assert planned[p["session_id"]]["pinned"]
        if p["kind"] == "skip":
            assert planned[p["session_id"]]["status"] == "skipped"


def test_decide_api_routes(gt):
    from smartplate.app import create_app
    c = create_app().test_client()
    v = c.post("/api/profiles", json={"name": "Asha", "diet": "veg", "weekly_budget": 2100,
                                      "meals": ["lunch", "dinner"]}).get_json()
    c.environ_base["HTTP_X_SMARTPLATE_KEY"] = v["access_key"]
    r = c.post(f"/api/user/{v['user']['id']}/decide/today", json={"mode": "favourites_plus_new"})
    assert r.status_code == 200 and r.get_json()["mode"] == "favourites_plus_new"
    r = c.post(f"/api/plan/{v['plan']['id']}/decide", json={"mode": "surprise_me"})
    assert r.status_code == 200 and r.get_json()["horizon"] == "week"
    assert c.post(f"/api/plan/{v['plan']['id']}/decide", json={"mode": "everything"}).status_code == 400
    assert c.post(f"/api/user/{v['user']['id']}/decide/today", json={"money": "lots"}).status_code == 400
    del c.environ_base["HTTP_X_SMARTPLATE_KEY"]
    assert c.post(f"/api/plan/{v['plan']['id']}/decide", json={}).status_code == 401


def test_balanced_week_fills_meals_before_skipping(gt):
    """usab #10 on the week planner (every mode now solves fewest skips first): a ₹1,500
    two-meal veg week leaves no meal empty that the money left could cover."""
    from gt_support import check_plan
    from smartplate import service
    v = _profile({"name": "Priya", "diet": "veg", "weekly_budget": 1500, "meals": ["lunch", "dinner"],
                  "cook": "never"})
    pid = v["plan"]["id"]
    view = service.plan_view(pid)
    assert not check_plan(pid)
    cells = [c for d in view["grid"] for c in d["meals"].values()]
    left = view["budget"]["budget"] - view["budget"]["spend"]
    for c in cells:
        if c["kind"] == "skip":
            reason = " ".join(c.get("reasons") or [])
            assert "variety" in reason or "₹" in reason, reason
    lunches = [c for d in view["grid"] for m, c in d["meals"].items() if m == "lunch"]
    assert sum(1 for c in lunches if c["kind"] == "delivery") >= 5, [c["item"] for c in lunches]
    assert left >= 0


def test_non_veg_week_is_not_all_veg(gt):
    """usab #11: Ravi (non-veg, no favourites) got two non-veg meals in 14."""
    from smartplate import service
    from smartplate.domain import models
    v = _profile({"name": "Ravi", "diet": "nonveg", "weekly_budget": 3500, "meals": ["lunch", "dinner"],
                  "cook": "never"})
    view = service.plan_view(v["plan"]["id"])
    veg = {it["name"]: it["veg"] for it in models.menu_for_city("Chennai")}
    delivered = [c["item"] for d in view["grid"] for c in d["meals"].values() if c["kind"] == "delivery"]
    nonveg = [n for n in delivered if not veg.get(n, 1)]
    assert len(nonveg) >= len(delivered) // 2, delivered


@pytest.mark.parametrize("body", [
    {"name": "B", "diet": "nonveg", "weekly_budget": 3500, "meals": ["breakfast", "lunch", "dinner"],
     "cook": "sometimes", "favourites": [1, 2, 3, 4, 5, 6]},
    {"name": "D", "diet": "nonveg", "weekly_budget": 1400, "daily_cap": 250,
     "meals": ["breakfast", "lunch", "dinner"], "cook": "sometimes"},
])
def test_week_replan_proves_optimal_fast_with_production_settings(gt, monkeypatch, body):
    """grill #55: with production solver settings (0.1% gap, 20,000 nodes, 10 s backstop) a
    fresh week solve is proven optimal well inside the time limit. A week-wide non-veg
    share row made the first profile hit the 10 s limit, and the second one did before W6.
    Deterministic part: proven optimal; the time bound is generous (measured ≈0.2–0.5 s)."""
    from smartplate import config, everyday
    monkeypatch.setattr(config, "SOLVER_GAP", 0.001)
    monkeypatch.setattr(config, "SOLVER_MAX_NODES", 20000)
    monkeypatch.setattr(config, "SOLVER_TIME_LIMIT_S", 10)
    pid = everyday.create_profile(dict(body))["plan"]["id"]
    t0 = time.perf_counter()
    optimizer.optimize(pid, stable=False)
    assert optimizer.LAST_SOLVE["proven_optimal"]
    assert time.perf_counter() - t0 < 6.0
