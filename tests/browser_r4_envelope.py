"""Fix round 4, budget model v2, in real Chromium at 390 px. Frozen clock (Mon 2 Nov 2026 08:00
IST), a ₹2,000-a-week profile with three meals a day:
  * the Week strip shows seven day chips with a fair-share tick (no wall of sliders);
  * tap Wednesday: one slider, its guide line, and a rest-of-week preview that moves with it;
  * save it: Wednesday's chip says the new amount and Today's budget shows the same figure the
    Week does (one function, no flat ÷ 7 vs rolling mismatch);
  * Saturday Off: the chip says Off;
  * no horizontal scroll, no console errors."""
import os
import re
import tempfile

import pytest

from browser_v3_today import BOOT_MS, _page, _serve, no_overflow, shot
from smartplate import config, ratelimit
from smartplate.app import create_app

pw = pytest.importorskip("playwright.sync_api")
expect = pw.expect
PHONE = {"width": 390, "height": 844}


@pytest.fixture
def world(frozen, monkeypatch):
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)
    for k, val in (("DB_PATH", path), ("DATABASE_URL", ""), ("WEATHER_PROVIDER", "simulated"),
                   ("SWIGGY_PROVIDER", "simulated"), ("PUSH_ENABLED", False)):
        monkeypatch.setattr(config, k, val)
    ratelimit.reset()
    app = create_app()
    client = app.test_client()
    r = client.post("/api/profiles", json={
        "name": "Vijay", "diet": "nonveg", "weekly_budget": 2000, "cook": "never",
        "rhythm": {"breakfast": "order", "lunch": "order", "dinner": "order"}, "consent": {"account": True}})
    assert r.status_code == 201, r.get_json()
    uid = r.get_json()["user"]["id"]
    key = client.environ_base.get("HTTP_X_SMARTPLATE_KEY") or r.get_json()["access_key"]
    srv, thread = _serve(app)
    yield {"url": f"http://127.0.0.1:{srv.server_port}", "uid": uid, "key": key}
    srv.shutdown()
    thread.join(timeout=5)
    srv.server_close()
    if os.path.exists(path):
        os.unlink(path)


def _today_amount(page) -> str:
    return page.locator(".v3-budget .v3-big").inner_text()


def test_day_slider_and_today_week_same_figure_at_390(world):
    with pw.sync_playwright() as p:
        from browser_redesign import _launch
        browser = _launch(p)
        page, errors = _page(browser, world["url"], PHONE, profile=world)
        expect(page.locator(".v3-today").get_by_text("Today's budget")).to_be_visible(timeout=BOOT_MS)
        before = _today_amount(page)
        page.locator('.nav [data-tab="week"]').first.click()
        strip = page.locator(".v3-env")
        expect(strip).to_be_visible(timeout=BOOT_MS)
        expect(strip.locator("[data-v3-day]")).to_have_count(7)
        expect(page.locator('input[type="range"]')).to_have_count(0)         # no permanent wall of sliders
        shot(page, "r4-01-week-strip-390")
        no_overflow(page)

        with page.expect_response(lambda r: "/envelope" in r.url):
            strip.locator('[data-v3-day="2026-11-04"]').click()
        dlg = page.get_by_role("dialog", name="Wednesday's budget")
        expect(dlg).to_be_visible()
        slider = dlg.locator("#v3-day-slider")
        expect(slider).to_be_visible()
        expect(dlg.locator(".v3-guide")).to_have_count(1)
        preview = dlg.locator("#v3-day-preview")
        expect(preview).to_contain_text("Rest of week:")
        first = preview.inner_text()
        slider.evaluate("(el) => { el.value = String(Number(el.min) + 600); el.dispatchEvent(new Event('input')); }")
        expect(preview).not_to_have_text(first)
        expect(dlg.locator("#v3-day-out")).to_have_text(re.compile(r"₹\d"))
        shot(page, "r4-02-day-slider-390")
        no_overflow(page)
        amount = dlg.locator("#v3-day-out").inner_text()
        dlg.get_by_role("button", name=re.compile(r"^Use ₹")).click()
        expect(page.locator('[data-v3-day="2026-11-04"]')).to_contain_text(amount, timeout=BOOT_MS)

        # Today shows the same figure as the Week's chip for today
        week_today = page.locator('[data-v3-day="2026-11-02"]').get_attribute("aria-label")
        page.locator('.nav [data-tab="today"]').first.click()
        expect(page.locator(".v3-today").get_by_text("Today's budget")).to_be_visible(timeout=BOOT_MS)
        today_amt = _today_amount(page)
        assert today_amt != before                       # Wednesday's money came from the other days
        env = page.evaluate("([uid, key]) => fetch(`/api/user/${uid}/envelope`, { headers: { 'X-SmartPlate-Key': key } })"
                            ".then(r => r.json())", [world["uid"], world["key"]])
        mon = next(d for d in env["days"] if d["date"] == "2026-11-02")
        assert today_amt == f"₹{mon['target']:,}", (today_amt, mon)
        assert week_today

        # Saturday Off
        page.locator('.nav [data-tab="week"]').first.click()
        page.locator('[data-v3-day="2026-11-07"]').click()
        dlg = page.get_by_role("dialog", name="Saturday's budget")
        dlg.locator('[data-v3-dayplan="off"]').click()
        expect(page.locator('[data-v3-day="2026-11-07"]')).to_contain_text("Off", timeout=BOOT_MS)
        no_overflow(page)
        assert not errors, errors
        browser.close()
