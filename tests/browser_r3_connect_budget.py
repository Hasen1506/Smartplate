"""Fix round 3 "connect-budget" in real Chromium (375 px): onboarding with Day/Week and
"How many eat?", the recovery code on the first Today, Connect Swiggy with the notice ticked
inside the sheet (no native confirm) and its errors shown there, and the Week budget that
stores what was typed with a visible Undo. Run: `pytest tests/browser_r3_connect_budget.py`."""
import os
import tempfile

import pytest

from browser_redesign import FROZEN_JS, _launch, no_overflow
from browser_v3_today import BOOT_MS, PHONE, _serve, shot
from smartplate import config, ratelimit
from smartplate.app import create_app
from smartplate.integrations import swiggy_connect
from swiggy_town import TownFake

pw = pytest.importorskip("playwright.sync_api")
expect = pw.expect


@pytest.fixture
def server(frozen, monkeypatch):
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)
    monkeypatch.setattr(config, "DB_PATH", path)
    monkeypatch.setattr(config, "DATABASE_URL", "")
    monkeypatch.setattr(config, "FIXTURE_DATA", False)
    monkeypatch.setattr(config, "WEATHER_PROVIDER", "simulated")
    monkeypatch.setattr(config, "SWIGGY_PROVIDER", "live")
    monkeypatch.setattr(config, "SWIGGY_REDIRECT_APPROVED", True)
    monkeypatch.setattr(config, "PUSH_ENABLED", False)
    monkeypatch.setattr(swiggy_connect, "_http", TownFake())
    ratelimit.reset()
    srv, thread = _serve(create_app())
    yield {"url": f"http://127.0.0.1:{srv.server_port}", "monkeypatch": monkeypatch}
    srv.shutdown()
    thread.join(timeout=5)
    srv.server_close()
    if os.path.exists(path):
        os.unlink(path)


def _onboard(browser, url):
    context = browser.new_context(viewport=PHONE, timezone_id="Asia/Kolkata", locale="en-IN")
    page = context.new_page()
    page.clock.set_fixed_time(FROZEN_JS)
    dialogs = []
    page.on("dialog", lambda d: (dialogs.append(d.message), d.dismiss()))
    page.goto(url)
    page.get_by_role("button", name="Get started").click(timeout=BOOT_MS)
    page.locator('[data-v3-ob="diet:nonveg"]').click()
    page.get_by_role("button", name="Continue").click()
    expect(page.get_by_role("heading", name="Your food budget")).to_be_visible()
    page.get_by_role("group", name="How many eat").get_by_role("button", name="2").click()
    page.locator("#v3-ob-budget").fill("3000")
    page.get_by_role("button", name="Lunch").click()
    page.get_by_role("button", name="Dinner").click()
    page.locator("#v3-ob-consent").check()
    shot(page, "r3-onboard-budget")
    page.get_by_role("button", name="Show my day").click()
    today = page.locator(".v3-today")
    expect(today.locator(".v3-budget .v3-big")).to_have_text("₹3,000", timeout=BOOT_MS)
    return page, today, dialogs


def test_connect_sheet_consent_and_error_are_in_the_sheet(server):
    server["monkeypatch"].setattr(swiggy_connect, "redirect_whitelisted", lambda uri: False)
    with pw.sync_playwright() as p:
        browser = _launch(p)
        page, today, dialogs = _onboard(browser, server["url"])
        # #12: the recovery code, once, on the first Today
        card = today.locator(".v3-recovery")
        expect(card).to_contain_text("Save your recovery code")
        shot(page, "r3-recovery")
        card.get_by_role("button", name="I've saved it").click()
        expect(today.locator(".v3-recovery")).to_have_count(0)
        no_overflow(page)
        page.get_by_role("button", name="Connect Swiggy").first.click()
        sheet = page.get_by_role("dialog", name="Connect Swiggy")
        go = sheet.get_by_role("button", name="Continue with Swiggy")
        expect(go).to_be_disabled()
        sheet.locator("#connect-consent").check()
        expect(go).to_be_enabled()
        go.click()
        expect(sheet.get_by_role("alert")).to_contain_text("hasn't approved Ziggy's sign-in address yet")
        shot(page, "r3-connect-error")
        assert dialogs == []                                   # never a native confirm()
        browser.close()


def test_connect_goes_to_swiggy_after_the_tick(server):
    with pw.sync_playwright() as p:
        browser = _launch(p)
        page, today, dialogs = _onboard(browser, server["url"])
        page.route("https://mcp.swiggy.com/**", lambda r: r.fulfill(status=200, body="<title>Swiggy sign-in</title>",
                                                                   content_type="text/html"))
        page.get_by_role("button", name="Connect Swiggy").first.click()
        sheet = page.get_by_role("dialog", name="Connect Swiggy")
        sheet.locator("#connect-consent").check()
        sheet.get_by_role("button", name="Continue with Swiggy").click()
        page.wait_for_url("https://mcp.swiggy.com/**", timeout=BOOT_MS)
        assert dialogs == []
        browser.close()


def test_week_budget_keeps_what_was_typed_and_undo_stays_visible(server):
    with pw.sync_playwright() as p:
        browser = _launch(p)
        page, today, _ = _onboard(browser, server["url"])
        page.get_by_role("navigation", name="Main").get_by_role("button", name="Week").click()
        page.get_by_role("button", name="Change budget").click()
        sheet = page.get_by_role("dialog", name="Food budget")
        expect(sheet).to_contain_text("Now ₹3,000 a day · ₹21,000 a week")
        shot(page, "r3-budget-sheet")
        sheet.locator("#budget-in").fill("300")
        sheet.get_by_role("button", name="Re-plan with ₹300 a week").click()
        undo = page.locator(".v3-budget-undo")
        expect(undo).to_contain_text("Budget changed to ₹300 a week (was ₹21,000)")
        shot(page, "r3-week-undo")
        page.wait_for_timeout(6000)                            # the toast is gone; the Undo is not
        expect(undo).to_be_visible()
        undo.get_by_role("button", name="Undo").click()
        expect(page.locator(".v3-budget-undo")).to_have_count(0)
        page.get_by_role("button", name="Change budget").click()
        expect(page.get_by_role("dialog", name="Food budget")).to_contain_text("₹21,000 a week")
        browser.close()
