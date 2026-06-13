"""§5.1.3 — idempotency keys & the order saga (§6)."""
from smartplate.integrations import swiggy_mcp


def _args(**over):
    base = dict(user_id=1, plan_id=2, session_id=7, trigger_ts="2026-06-15T13:00:00")
    base.update(over)
    return base


def test_key_is_stable_and_derived(seeded):
    a = swiggy_mcp.idempotency_key(**_args())
    b = swiggy_mcp.idempotency_key(**_args())
    assert a == b
    assert swiggy_mcp.idempotency_key(**_args(session_id=8)) != a


def test_retry_does_not_double_order(seeded):
    prov = swiggy_mcp.SimulatedSwiggyProvider(seed=3)
    decision = {"id": 1, "cost": 250.0}
    rest = {"id": 1, "name": "Reliable Diner", "flaky": 0}
    item = {"id": 1, "name": "Thali"}
    r1 = swiggy_mcp.place_order(decision, rest, item, provider=prov, **_args())
    r2 = swiggy_mcp.place_order(decision, rest, item, provider=prov, **_args())
    assert r1.ok and r2.ok
    assert r1.provider_order_id == r2.provider_order_id   # same order, not a new one
    assert r2.deduped is True
    assert r1.deduped is False


def test_saga_runs_all_steps(seeded):
    prov = swiggy_mcp.SimulatedSwiggyProvider(seed=4, flaky_fail_rate=0.0)
    rest = {"id": 2, "name": "X", "flaky": 0}
    res = swiggy_mcp.place_order({"id": 2, "cost": 99}, rest, {"id": 2, "name": "Y"},
                                 provider=prov, **_args(session_id=99))
    assert res.state == "placed"
    joined = " ".join(res.log)
    assert "menu loaded" in joined and "cart" in joined and "paid" in joined and "placed" in joined


def test_flaky_menu_load_fails_cleanly(seeded):
    prov = swiggy_mcp.SimulatedSwiggyProvider(flaky_fail_rate=1.0)
    rest = {"id": 3, "name": "Flaky Chain", "flaky": 1}
    res = swiggy_mcp.place_order({"id": 3, "cost": 120}, rest, {"id": 3, "name": "Z"},
                                 provider=prov, **_args(session_id=101))
    assert res.ok is False
    assert res.error == "menu_load"
    assert res.state == "failed"
