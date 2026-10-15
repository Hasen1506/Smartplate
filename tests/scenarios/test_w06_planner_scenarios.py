"""W6 planner fixes, checked on W10's persona weeks (stand-in Swiggy town, frozen clock).

The first test is W10's strict-xfail gap `test_household_skips_are_not_blamed_on_the_budget`
as a plain regression test; when W10's branch merges, its xfail marker for that gap
must be removed (it now passes)."""
from harness import PERSONAS, PLACES, town, run_week, unsafe_in_week  # noqa: F401  (fixtures)

NONVEG_DISHES = {row[0] for p in PLACES.values() for row in p[6] if row[2] is False}


def _planned(run_week, key, budget=None):
    w = run_week(PERSONAS[key])
    w.open_app()
    if budget:
        w.set_budget(budget)
    w.pick_favourites()
    w.plan_week()
    return w


def test_household_skips_are_not_blamed_on_the_budget(run_week):
    """W10 simulation: a household of 4 with one vegan and ₹6,000 had 12 of 14 meals
    skipped 'to stay within budget / rating floor' with ₹5,640 unspent."""
    w = _planned(run_week, "household_mixed")
    v = w.view()
    b = v["budget"]
    if b["remaining"] > 0.5 * b["budget"]:
        blamed = [r for r in v["summary_reasons"] if "budget" in r.lower() and "skip" in r.lower()]
        assert not blamed, blamed
    assert v["counts"]["delivery"] + v["counts"]["cook"] >= 7, v["counts"]
    assert unsafe_in_week(w) == []
    # every skip names its real cause; none claims money while most of it is unspent
    for _, _, c in w.cells(v, kind="skip"):
        reason = " ".join(c.get("reasons") or [])
        if b["remaining"] > 0.5 * b["budget"]:
            assert "is left; the cheapest" not in reason, reason


def test_household_decide_for_me_is_safe_for_the_vegan_member(run_week):
    w = _planned(run_week, "household_mixed")
    r = w.c.post(f"/api/plan/{w.plan}/decide", json={"mode": "favourites_only"})
    assert r.status_code == 200
    body = r.get_json()
    assert body["filled"] >= 7, [p["why"] for p in body["picks"]]
    from harness import unsafe_for
    people = {p["name"]: p for p in PERSONAS["household_mixed"].people()}
    for p in body["picks"]:
        for line in p.get("lines") or []:
            for who in line["for"]:
                assert unsafe_for(people[who], line["name"]) is None, (who, line["name"])


def test_low_weekly_money_goes_cheaper_instead_of_skipping_lunches(run_week):
    """usab #10: Priya at ₹1,500 had every lunch skipped while ₹125 dinners stayed."""
    # W4 prices platform fee + packaging + GST + delivery into every order (cheapest dish ≈ ₹108
    # delivered), so ₹1,500 for 14 meals is below the shared low-budget floor; ₹1,900 keeps the
    # original question (cheaper dishes, not skipped lunches) on the fee-inclusive prices.
    w = _planned(run_week, "student_veg", budget=1900)
    v = w.view()
    lunches = [d["meals"]["lunch"] for d in v["grid"] if d["meals"].get("lunch")]
    assert sum(1 for c in lunches if c["kind"] == "delivery") >= 4, [c["item"] for c in lunches]
    assert v["budget"]["spend"] <= v["budget"]["budget"] + 0.01
    for c in lunches:
        if c["kind"] == "skip":
            assert "didn't fit the budget" not in " ".join(c.get("reasons") or [])


def test_today_decide_spreads_the_money_and_explains_each_pick(run_week):
    w = _planned(run_week, "student_veg")
    r = w.c.post(f"/api/user/{w.uid}/decide/today", json={"mode": "favourites_only"})
    body = r.get_json()
    assert r.status_code == 200 and body["horizon"] == "today" and body["method"] == "exact"
    assert body["money"] == round(1500 / 7, 2)
    assert body["spent"] <= body["money"]
    assert all(p["why"] for p in body["picks"])
    assert body["elapsed_ms"] < 1000


def test_non_veg_eater_with_money_gets_a_non_veg_mix(run_week):
    """usab #11: with money for it, a non-veg eater's week is at least half non-veg."""
    w = _planned(run_week, "salaried_nonveg", budget=5000)
    v = w.view()
    delivered = [c["item"] for _, _, c in w.cells(v, kind="delivery")]
    nonveg = [d for d in delivered if d in NONVEG_DISHES]
    assert len(nonveg) * 2 >= len(delivered), delivered
