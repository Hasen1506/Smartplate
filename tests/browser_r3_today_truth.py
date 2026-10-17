"""Fix round 3, "today-truth", in real Chromium at 390 px (usab audit 16 Oct 2026).

Frozen clock (Mon 2 Nov 2026 08:00 IST), stand-in Swiggy (tests/swiggy_town.py), a vegetarian who
orders lunch and dinner, with the product default week (only chosen dishes, #24):
  * the rules row says "Ziggy defaults" (#10); Today asks once when dinner is (#14) → 22:00;
  * a dinner dish greyed out by the delivery-time rule asks "Pick anyway?" and relaxes the rule
    in one tap (#27);
  * the early-cart button says "Cart opens 19:40 · remind me" (#21) and opens a reminder sheet
    for just that meal, with a calendar file (#22);
  * the Week shows the same dinner for today (#1), and the open meals as "Open" with the Week's
    own "Decide for me" (#24);
  * no horizontal scroll, no console errors.
Screenshots go to $V3_SHOTS when set."""
import os
import re
import tempfile

import pytest

from browser_v3_today import BOOT_MS, _page, _serve, no_overflow, shot
from smartplate import config, ratelimit
from smartplate.app import create_app
from smartplate.integrations import swiggy_connect
from swiggy_town import TownFake
from test_followups import _connect

pw = pytest.importorskip("playwright.sync_api")
expect = pw.expect
PHONE = {"width": 390, "height": 844}


@pytest.fixture
def town(frozen, monkeypatch):
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)
    for k, val in (("DB_PATH", path), ("DATABASE_URL", ""), ("FIXTURE_DATA", False), ("WEATHER_PROVIDER", "simulated"),
                   ("SWIGGY_PROVIDER", "live"), ("SWIGGY_REDIRECT_APPROVED", True), ("PUSH_ENABLED", False),
                   ("WEEK_FILL_DEFAULT", "chosen")):
        monkeypatch.setattr(config, k, val)
    ratelimit.reset()
    fake = TownFake()
    monkeypatch.setattr(swiggy_connect, "_http", fake)
    app = create_app()
    client = app.test_client()
    r = client.post("/api/profiles", json={
        "name": "Priya", "diet": "veg", "weekly_budget": 7000, "cook": "never",
        "rhythm": {"breakfast": "skip", "lunch": "order", "dinner": "order"}, "consent": {"account": True}})
    uid = r.get_json()["user"]["id"]
    _connect(client, fake, uid=uid)
    key = client.environ_base["HTTP_X_SMARTPLATE_KEY"]
    assert client.post(f"/api/user/{uid}/swiggy/address", json={"address_id": "addr-home"}).status_code == 200
    r = client.post(f"/api/user/{uid}/today/pool", json={"source": "swiggy", "meal": "dinner", "restaurant_id": "r-104",
                                                          "restaurant_name": "Punjab Da Dhaba", "item_id": "r-104-i1",
                                                          "rating": 4.1, "eta_min": 40})
    assert r.status_code == 200, r.get_json()
    assert client.post(f"/api/user/{uid}/today/rules", json={"max_eta": 30}).status_code == 200
    srv, thread = _serve(app)
    yield {"url": f"http://127.0.0.1:{srv.server_port}", "uid": uid, "key": key}
    srv.shutdown()
    thread.join(timeout=5)
    srv.server_close()
    if os.path.exists(path):
        os.unlink(path)


def test_today_truth_journey_at_390(town):
    with pw.sync_playwright() as p:
        from browser_redesign import _launch
        browser = _launch(p)
        page, errors = _page(browser, town["url"], PHONE, profile=town)
        today = page.locator(".v3-today")
        expect(today.get_by_text("Today's budget")).to_be_visible(timeout=BOOT_MS)
        # the rule chips: the person set 30 min, rating is still Ziggy's
        expect(today.get_by_label("Your rules")).to_contain_text("≤ 30 min")
        expect(today.get_by_label("Your rules")).to_contain_text("Ziggy defaults")
        # asked once: when is dinner?
        ask = page.get_by_role("group", name="When do you usually eat dinner?")
        expect(ask).to_be_visible()
        shot(page, "r3-01-ask-dinner-time-390")
        ask.get_by_role("button", name="22:00").click()
        expect(page.locator(".toast")).to_contain_text("Meal time saved")
        expect(page.get_by_role("group", name="When do you usually eat dinner?")).to_have_count(0)
        expect(page.locator('[data-v3-meal="dinner"] .v3-meal-name')).to_contain_text("22:00")
        # a dish greyed out by the 30-min rule: Pick anyway? / relax
        page.get_by_label("Dinner from your usual meals").select_option(value="sw:r-104:r-104-i1")
        note = page.locator(".v3-confirm")
        expect(note).to_contain_text("Pick anyway?")
        shot(page, "r3-02-pick-anyway-390")
        no_overflow(page)
        note.get_by_role("button", name="Relax to ≤45 min").click()
        expect(today.get_by_label("Your rules")).to_contain_text("≤ 45 min")
        expect(page.locator('[data-v3-meal="dinner"]')).to_contain_text("₹190")
        # the cart opens 90 min before order-by (22:00 − 35 − 10 = 21:15 → 19:45)
        # 22:00 − 40 min (the place's time) − 10 = 21:10, cart from 19:40 (15 min earlier on a rainy day)
        btn = page.locator(".v3-actions [data-v3-remind]")
        expect(btn).to_have_text(re.compile(r"^Cart opens (19:40|19:25) · remind me$"))
        opens = btn.inner_text().split()[2]
        btn.scroll_into_view_if_needed()
        page.locator(".v3-actions").screenshot(path=os.path.join(os.environ["V3_SHOTS"], "r3-03a-cart-opens-390.png")) if os.environ.get("V3_SHOTS") else None
        btn.click()
        sheet = page.get_by_role("dialog", name="Remind me: dinner")
        by = {"19:40": "21:10", "19:25": "20:55"}[opens]
        expect(sheet).to_contain_text(f"Ziggy nudges you at {opens}, when the Swiggy cart opens. Order by {by}.")
        expect(sheet.get_by_role("button", name="Add to my calendar")).to_be_visible()
        shot(page, "r3-03-remind-sheet-390")
        no_overflow(page)
        sheet.get_by_role("button", name="Close remind dinner").click()
        # the Week: same dinner today; lunch open, with Decide for me
        page.get_by_role("navigation").get_by_role("button", name="Week").click()
        expect(page.locator("main")).to_contain_text("Dal Makhani", timeout=BOOT_MS)
        expect(page.get_by_role("button", name="Decide for me")).to_be_visible()
        expect(page.locator(".mrow", has_text="Lunch").first).to_contain_text("Open")
        shot(page, "r3-04-week-one-truth-390")
        no_overflow(page)
        assert not errors, errors
        browser.close()
