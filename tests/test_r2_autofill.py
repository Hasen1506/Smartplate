"""Plan-ahead carts: opt-in "auto-fill my cart" (founder decision, 14 Oct 2026).

When a planned meal's cart window opens, /cron/tick (push.tick) puts the reviewed dish into
the Swiggy cart and pushes "Your lunch cart is ready — pay in Swiggy". It never pays, never
overwrites a cart that already holds something, and never adds a dish that changed since
the person reviewed it. Fakes only: the recorded Swiggy contract fake and a fake push service.
"""
import datetime as dt

import pytest

from gt_support import MONDAY_8AM
from smartplate import config, push
from smartplate.domain import autofill
from test_push import FakeService, _browser_subscription
from test_real_bill_corrects_plan import _live_week, client, swiggy  # noqa: F401

pytestmark = pytest.mark.cart_window


@pytest.fixture
def pushing(monkeypatch):
    monkeypatch.setattr(config, "PUSH_ENABLED", True)
    push.invalidate()
    yield FakeService()
    push.invalidate()


def _tomorrow_meal(view):
    tomorrow = (MONDAY_8AM.date() + dt.timedelta(days=1)).isoformat()
    return next({**c, "meal_name": m, "day_no": i} for i, d in enumerate(view["grid"]) for m, c in d["meals"].items()
                if c["kind"] == "delivery" and c["status"] == "active"
                and c["order"]["order_by_at"].startswith(tomorrow))


def _arm(client, sid):
    fp = client.get(f"/api/session/{sid}/swiggy-cart/preview").get_json()["fingerprint"]
    r = client.post(f"/api/session/{sid}/auto-cart", json={"on": True, "expected_fingerprint": fp})
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def _open_window(gt, cell):
    gt.set(dt.datetime.fromisoformat(cell["order"]["cart_opens_at"]).replace(tzinfo=None) + dt.timedelta(minutes=1))


def test_opt_in_needs_the_reviewed_fingerprint_and_never_pays(client, swiggy, gt):  # noqa: F811
    pid, view = _live_week(client, swiggy)
    sid = _tomorrow_meal(view)["session_id"]
    r = client.post(f"/api/session/{sid}/auto-cart", json={"on": True})
    assert r.status_code == 409 and r.get_json()["error"] == "cart_changed"
    r = client.post(f"/api/session/{sid}/auto-cart", json={"on": True, "expected_fingerprint": "stale"})
    assert r.status_code == 409
    st = _arm(client, sid)
    assert st["on"] is True and st["state"] == "armed" and st["pays"] is False and "never pays" in st["how"]
    assert client.get(f"/api/session/{sid}/auto-cart").get_json()["on"] is True
    assert [m["session_id"] for m in client.get(f"/api/plan/{pid}/auto-cart").get_json()["meals"]] == [sid]
    assert swiggy.cart is None                                  # opting in adds nothing yet
    off = client.post(f"/api/session/{sid}/auto-cart", json={"on": False}).get_json()
    assert off["on"] is False and off["state"] == "off"


def test_tick_fills_the_cart_when_the_window_opens_and_pushes_pay_in_swiggy(client, swiggy, gt, pushing):  # noqa: F811
    pid, view = _live_week(client, swiggy)
    cell = _tomorrow_meal(view)
    sid = cell["session_id"]
    client.post("/api/user/3/push/subscribe", json={"subscription": _browser_subscription()})
    _arm(client, sid)
    out = push.tick(sender=pushing)                             # window not open yet: nothing happens
    assert swiggy.cart is None and "auto_cart" not in out
    assert dt.datetime.fromisoformat(out["next_check"]) <= dt.datetime.fromisoformat(
        cell["order"]["cart_opens_at"]).replace(tzinfo=None)    # the tick wakes for the window
    _open_window(gt, cell)
    out = push.tick(sender=pushing, force=True)
    assert out["auto_cart"]["filled"] == 1 and swiggy.cart is not None
    assert "place_food_order" not in swiggy.tool_calls()        # never pays or places
    meal = cell["meal_name"]
    note = [p for _, p in pushing.calls if p["tag"] == f"smartplate-autofill-{sid}"]
    assert len(note) == 1 and note[0]["title"] == f"Your {meal} cart is ready — pay in Swiggy"
    assert note[0]["url"] == "https://www.swiggy.com/checkout" and "doesn't pay" in note[0]["body"]
    st = client.get(f"/api/session/{sid}/auto-cart").get_json()
    assert st["state"] == "filled" and "Pay in Swiggy" in st["note"]
    # the real bill is kept like the meal's own "Order" button
    assert client.get(f"/api/plan/{pid}").get_json()["budget"]["spend"] > 0
    push.tick(sender=pushing, force=True)                       # filled once, never twice
    assert len([p for _, p in pushing.calls if p["tag"] == f"smartplate-autofill-{sid}"]) == 1


def test_a_cart_with_other_items_is_never_overwritten(client, swiggy, gt, pushing):  # noqa: F811
    pid, view = _live_week(client, swiggy)
    cell = _tomorrow_meal(view)
    sid = cell["session_id"]
    client.post("/api/user/3/push/subscribe", json={"subscription": _browser_subscription()})
    _arm(client, sid)
    swiggy.cart, swiggy.qty = "m0", 2                           # the person filled Swiggy's cart themselves
    _open_window(gt, cell)
    out = push.tick(sender=pushing, force=True)
    assert out["auto_cart"]["blocked"] == 1
    assert (swiggy.cart, swiggy.qty) == ("m0", 2)               # untouched
    assert "update_food_cart" not in swiggy.tool_calls()
    note = [p for _, p in pushing.calls if p["tag"] == f"smartplate-autofill-{sid}"]
    assert len(note) == 1 and "isn't empty" in note[0]["title"]
    assert client.get(f"/api/session/{sid}/auto-cart").get_json()["state"] == "blocked"


def test_a_changed_dish_is_not_added_and_the_person_is_asked_to_review(client, swiggy, gt, pushing):  # noqa: F811
    pid, view = _live_week(client, swiggy)
    cell = _tomorrow_meal(view)
    sid = cell["session_id"]
    client.post("/api/user/3/push/subscribe", json={"subscription": _browser_subscription()})
    _arm(client, sid)
    swiggy.dishes = {n: p + 1000 for n, p in swiggy.dishes.items()}   # every price went up ₹10
    from smartplate.domain import live_catalog
    live_catalog.clear(3)                                       # the next preview reads the new menu
    _open_window(gt, cell)
    out = push.tick(sender=pushing, force=True)
    assert swiggy.cart is None
    st = client.get(f"/api/session/{sid}/auto-cart").get_json()
    assert out["auto_cart"]["changed"] == 1 and st["state"] == "changed", (out, st)
    note = [p for _, p in pushing.calls if p["tag"] == f"smartplate-autofill-{sid}"]
    assert len(note) == 1 and note[0]["title"].startswith("Review your")


def test_a_missed_window_is_reported_not_filled(client, swiggy, gt, pushing):  # noqa: F811
    pid, view = _live_week(client, swiggy)
    cell = _tomorrow_meal(view)
    sid = cell["session_id"]
    _arm(client, sid)
    gt.set(dt.datetime.fromisoformat(cell["order"]["order_by_at"]).replace(tzinfo=None) + dt.timedelta(hours=6))
    out = push.tick(sender=pushing, force=True)
    assert out["auto_cart"]["missed"] == 1 and swiggy.cart is None
    assert client.get(f"/api/session/{sid}/auto-cart").get_json()["state"] == "missed"


def test_a_whole_day_opts_in_with_each_meals_review(client, swiggy, gt):  # noqa: F811
    pid, view = _live_week(client, swiggy)
    cell = _tomorrow_meal(view)
    day = cell["day_no"]
    meals = [c for c in view["grid"][day]["meals"].values() if c["kind"] == "delivery" and c["status"] == "active"]
    fps = {str(c["session_id"]): client.get(f"/api/session/{c['session_id']}/swiggy-cart/preview").get_json()["fingerprint"]
           for c in meals[:1]}
    r = client.post(f"/api/plan/{pid}/auto-cart/day", json={"day": day, "fingerprints": fps}).get_json()
    assert r["armed"] == [meals[0]["session_id"]]
    assert all(s["why"] == "Not reviewed" for s in r["skipped"]) and len(r["skipped"]) == len(meals) - 1
    assert client.post(f"/api/plan/{pid}/auto-cart/day", json={"day": 9, "fingerprints": {}}).status_code == 400
    off = client.post(f"/api/plan/{pid}/auto-cart/day", json={"day": day, "on": False}).get_json()
    assert all(m["on"] is False for m in off["meals"])


def test_off_unless_the_live_cart_is_allowed(client, swiggy, gt, monkeypatch):  # noqa: F811
    pid, view = _live_week(client, swiggy)
    sid = _tomorrow_meal(view)["session_id"]
    fp = client.get(f"/api/session/{sid}/swiggy-cart/preview").get_json()["fingerprint"]
    monkeypatch.setattr(config, "SWIGGY_MODE", "live_read")
    r = client.post(f"/api/session/{sid}/auto-cart", json={"on": True, "expected_fingerprint": fp})
    assert r.status_code == 400 and "off on this server" in r.get_json()["error"]
    assert autofill.run_due() == {"filled": 0, "blocked": 0, "changed": 0, "missed": 0, "failed": 0}


def test_auto_fill_choices_are_private_profile_data():
    from smartplate import profile_data
    assert {"auto_cart", "today_cart_bills"} <= set(profile_data.USER_TABLES)
