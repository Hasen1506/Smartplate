"""§1.2 / §3.1 — substitution never drops below the rating floor; retries are idempotent."""
from smartplate.domain import models
from smartplate import service
from smartplate.integrations import swiggy_mcp
from smartplate.kernel import optimizer, variance


def test_flaky_pick_triggers_substitution_above_floor(seeded):
    pid = seeded["plan_id"]
    optimizer.optimize(pid)
    prov = swiggy_mcp.SimulatedSwiggyProvider(flaky_fail_rate=1.0)
    report = variance.execute_plan(pid, provider=prov)
    assert report["placed"] == report["attempted"]   # everything ends up placed
    assert report["substituted"] >= 1                # A2B is flaky and gets picked

    user = models.get_user(1)
    for r in report["results"]:
        if r["substituted"]:
            # the substitute that was actually placed must clear the rating floor
            dec = next(d for d in models.decisions_for_plan(pid) if d["session_id"] == r["session_id"])
            assert dec["rating"] >= user["rating_floor"]


def test_execution_is_idempotent(seeded):
    pid = seeded["plan_id"]
    optimizer.optimize(pid)
    prov = swiggy_mcp.SimulatedSwiggyProvider(flaky_fail_rate=1.0)
    first = variance.execute_plan(pid, provider=prov)
    before = service.order_history(pid)
    second = variance.execute_plan(pid, provider=prov)
    assert first['placed'] > 0
    assert second['attempted'] == second['placed'] == 0
    assert service.order_history(pid) == before  # no new order, charge or history row
