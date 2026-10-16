"""Fix round 3 integration: the workstreams meet.

  • history-favs' optional snack pick and today-truth's one source of truth: a snack picked on
    Today costs money in the Week's budget too (the snack is never a Week cell), and it is
    unaffected by per-user meal times (no order-by for a snack; a late dinner still works);
  • menus-offers' 6 h menu cache and history-favs' name matching: importing past orders
    serves a kept whole menu without a second Swiggy read.
"""
from __future__ import annotations

import test_r3_history_favs as hf
from test_r3_history_favs import _import, orders  # noqa: F401  (fixture)
from test_w07_platform import _open, town  # noqa: F401  (fixture)


def _week(uid):
    from smartplate import service
    cur = service.current_plan(uid)
    pid = cur.get("today_plan_id") or cur["plan"]["id"]
    return service.plan_view(pid)


def test_snack_pick_counts_in_week_money_and_ignores_meal_times(town, orders):  # noqa: F811
    c, uid, _ = _open(town)
    _import(c, uid)
    t = c.post(f"/api/user/{uid}/today/pool/suggested",
               json={"items": [{"restaurant_id": "r-101", "item_name": "Kesari"}]}).get_json()
    snack = t["pools"]["snack"]
    assert snack, t["suggested_added"]
    assert c.post(f"/api/user/{uid}/today/meal-times", json={"dinner": "23:00"}).status_code == 200
    before = _week(uid)["budget"]
    assert before["snack_today"] is None
    t = c.post(f"/api/user/{uid}/today/pick", json={"meal": "snack", "ref": snack[0]["ref"]}).get_json()
    pick = t["picks"]["snack"]
    assert "when" not in pick                       # a snack has no order-by / cart window / reminder
    assert t["meal_times"].get("dinner", {}).get("at") in (None, "23:00")
    after = _week(uid)["budget"]
    assert after["snack_today"] and after["snack_today"] == round(pick["total"], 2)
    assert round(after["spend"] - before["spend"], 2) == after["snack_today"]
    assert not any(m == "snack" for d in _week(uid)["grid"] for m in d["meals"])   # never a Week cell
    c.post(f"/api/user/{uid}/today/pick", json={"meal": "snack", "ref": ""})
    assert _week(uid)["budget"]["snack_today"] in (None, after["snack_today"])


def test_history_import_uses_a_kept_menu_without_rereading(town, orders):  # noqa: F811
    from smartplate.domain import live_catalog
    from smartplate.integrations import swiggy_live
    c, uid, _ = _open(town)
    _import(c, uid)
    live_catalog.refresh(uid)              # the one-off catalogue read a first pooled dish triggers
    swiggy_live.live_menu(uid, "r-101", "Annapoorna Tiffin Room")          # kept for 6 h
    before = town["fake"].tool_calls().count("get_restaurant_menu")
    t = c.post(f"/api/user/{uid}/today/pool/suggested",
               json={"items": [{"restaurant_id": "r-101", "item_name": "Idli", "meal": "breakfast"}]}).get_json()
    assert t["suggested_added"]["swiggy"] == ["Idli"]
    assert town["fake"].tool_calls().count("get_restaurant_menu") == before


assert hf  # keep the module import (fixtures live there)
