"""W3 frontend fixes, in real Chromium (390 px phone): the Back button, an offline reload,
closed places, onboarding's budget step, focus in sheets, accessible names and Tamil.

Production settings with the stand-in neighbourhood (tests/swiggy_town.py, via the
town_world fixture of browser_pools.py), a frozen clock (Mon 2 Nov 2026 08:00 IST), no
network beyond the local test server.
Run explicitly: `pytest tests/browser_w03.py` (needs Playwright + Chromium).
"""
import json
import re
from pathlib import Path

import pytest

from browser_pools import town_world  # noqa: F401  (pytest fixture)
from browser_redesign import CONTRAST_JS, FROZEN_JS, _launch, no_overflow

pw = pytest.importorskip("playwright.sync_api")
expect = pw.expect
PHONE = {"width": 390, "height": 844}

# Every visible button, link and form field must have an accessible name (finding 53).
NAMELESS_JS = r"""() => [...document.querySelectorAll('button, a[href], input:not([type=hidden]), select, textarea')]
  .filter(el => el.getClientRects().length && getComputedStyle(el).visibility !== 'hidden')
  .filter(el => {
    const name = (el.getAttribute('aria-label') || el.getAttribute('title') || '').trim()
      || (el.labels && [...el.labels].map(l => l.textContent).join('').trim())
      || (el.getAttribute('aria-labelledby') && document.getElementById(el.getAttribute('aria-labelledby'))?.textContent.trim())
      || (el.placeholder || '').trim() || el.textContent.trim();
    return !name;
  }).map(el => el.outerHTML.slice(0, 120))"""


def _page(browser, w, *, signed_in=True):
    context = browser.new_context(viewport=PHONE, timezone_id="Asia/Kolkata", locale="en-IN")
    page = context.new_page()
    page.clock.set_fixed_time(FROZEN_JS)
    page.route("https://fonts.googleapis.com/**", lambda r: r.fulfill(status=200, content_type="text/css", body=""))
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    if signed_in:
        bag = {str(w["uid"]): {"key": w["key"], "name": "Priya"}}
        page.add_init_script(f"""if (location.protocol.startsWith('http') && !localStorage.getItem('smartplate.keys')) {{
            localStorage.setItem('smartplate.keys', {json.dumps(json.dumps(bag))});
            localStorage.setItem('smartplate.user', {json.dumps(str(w['uid']))}); }}""")
    return context, page, errors


def _shot(page, name):
    Path("test-artifacts").mkdir(exist_ok=True)
    page.screenshot(path=f"test-artifacts/w03-{name}.png", full_page=True)


def test_back_button_closes_sheet_then_tab_then_leaves(town_world):
    w = town_world
    with pw.sync_playwright() as playwright:
        browser = _launch(playwright)
        context, page, errors = _page(browser, w)
        try:
            page.goto("about:blank")                       # the "previous site"
            page.goto(w["url"])
            nav = page.get_by_role("navigation", name="Main")
            expect(nav).to_be_visible()
            nav.get_by_role("button", name="Week").click()
            expect(page.get_by_role("heading", name=re.compile("^Week of"))).to_be_visible()
            page.locator(".mrow", has_text="Dinner").first.click()
            sheet = page.get_by_role("dialog")
            expect(sheet).to_be_visible()
            # focus moved into the sheet when it opened
            assert page.evaluate("!!document.activeElement.closest('[role=dialog]')")

            page.go_back()                                  # 1: closes the sheet, stays on Week
            expect(page.get_by_role("dialog")).to_have_count(0)
            expect(page.get_by_role("heading", name=re.compile("^Week of"))).to_be_visible()
            assert page.url.startswith(w["url"])

            page.go_back()                                  # 2: back to Today, still in Ziggy
            expect(page.locator('section[aria-label="Next meal"]')).to_be_visible()
            assert page.url.startswith(w["url"])

            page.go_back()                                  # 3: only now does Back leave Ziggy
            page.wait_for_url("about:blank")

            # closing a sheet with its own button leaves no dead Back press behind
            page.go_forward()
            expect(page.get_by_role("navigation", name="Main")).to_be_visible()
            page.get_by_role("button", name="change budget").click()
            dlg = page.get_by_role("dialog")
            expect(dlg).to_be_visible()
            page.keyboard.press("Escape")
            expect(page.get_by_role("dialog")).to_have_count(0)
            # focus went back to the control that opened the sheet
            assert page.evaluate("document.activeElement.dataset.act") == "budget"
            page.wait_for_timeout(100)
            page.go_back()
            page.wait_for_url("about:blank")
            assert not errors, errors
        except Exception:
            _shot(page, "back")
            raise
        finally:
            browser.close()


def test_offline_reload_keeps_the_plan(town_world):
    w = town_world
    with pw.sync_playwright() as playwright:
        browser = _launch(playwright)
        context, page, errors = _page(browser, w)
        try:
            page.goto(w["url"])
            hero = page.locator('section[aria-label="Next meal"]')
            expect(hero).to_be_visible()
            page.evaluate("navigator.serviceWorker.ready.then(() => true)")
            page.reload()                                   # now the service worker controls the page
            expect(hero).to_be_visible()
            page.wait_for_function("!!navigator.serviceWorker.controller")
            page.wait_for_load_state("networkidle")
            dish = hero.locator("b").first.inner_text()
            page.wait_for_function("""async () => { const c = await caches.open('ziggy-api-v1');
                return !!(await c.match(location.origin + '/api/user/' + S.userId + '/plan')); }""")

            context.set_offline(True)
            page.reload()
            expect(page.get_by_role("navigation", name="Main")).to_be_visible()
            expect(page.get_by_role("status").filter(has_text="You're offline")).to_contain_text("Showing your plan as loaded at")
            expect(hero).to_contain_text(dish)
            expect(page.get_by_text("Ziggy needs a connection", exact=False)).to_have_count(0)

            # sign-out drops the copy (it belongs to whoever was signed in)
            context.set_offline(False)
            page.evaluate("clearCurrentProfile()")
            page.wait_for_function("async () => !(await caches.keys()).includes('ziggy-api-v1')")
            assert not errors, errors
        except Exception:
            _shot(page, "offline")
            raise
        finally:
            browser.close()


def test_closed_places_are_greyed_not_errors(town_world):
    w = town_world
    with pw.sync_playwright() as playwright:
        browser = _launch(playwright)
        context, page, errors = _page(browser, w)
        try:
            page.goto(w["url"])
            page.get_by_role("navigation", name="Main").get_by_role("button", name="Saved").click()
            page.get_by_role("button", name=re.compile("Or see every place Swiggy lists")).click()
            closed = page.locator(".mrow.closed", has_text="Night Owl Parotta")
            expect(closed).to_be_visible()
            expect(closed).to_contain_text("Closed now")
            expect(closed.get_by_role("button", name="Menu")).to_have_count(0)
            expect(page.locator(".mrow", has_text="Annapoorna Tiffin Room").get_by_role("button", name="Menu")).to_be_visible()
            # open places come first
            names = page.locator(".card.tight .mrow b").all_inner_texts()
            assert names.index(next(n for n in names if "Night Owl" in n)) == len(names) - 1, names
            expect(page.get_by_role("alert")).to_have_count(0)
            assert page.evaluate(CONTRAST_JS) == []
            assert page.evaluate(NAMELESS_JS) == []
            no_overflow(page)
            assert not errors, errors
        except Exception:
            _shot(page, "closed")
            raise
        finally:
            browser.close()


def test_onboarding_budget_and_diet_choice_and_tamil(town_world):
    w = town_world
    with pw.sync_playwright() as playwright:
        browser = _launch(playwright)
        context, page, errors = _page(browser, w, signed_in=False)
        try:
            page.goto(w["url"])
            page.get_by_role("button", name="Get started").click()
            cont = page.get_by_role("button", name="Continue")
            expect(cont).to_be_disabled()                   # no diet is chosen for you
            expect(page.locator('[data-ob="diet"][aria-pressed="true"]')).to_have_count(0)
            assert page.evaluate(NAMELESS_JS) == []
            page.locator('[data-ob="diet"][data-val="veg"]').click()
            expect(cont).to_be_enabled()
            cont.click()
            page.locator('[data-ob="rh_breakfast"][data-val="order"]').click()
            page.get_by_role("button", name="Continue").click()

            per = page.locator("#ob-per")
            expect(per).to_have_text("about ₹143 a meal for 21 meals")
            page.locator("#ob-budget").fill("300")          # typing, not the ± buttons
            expect(per).to_have_text("about ₹14 a meal for 21 meals")
            expect(page.locator('[data-ob="budgetpick"][data-val="3000"]')).to_have_attribute("aria-pressed", "false")
            warn = page.locator("#ob-floor [role=alert]")
            expect(warn).to_contain_text("₹300 is ₹14 a meal")
            warn.get_by_role("button", name="Use ₹1,500").click()
            expect(page.locator("#ob-budget")).to_have_value("1500")
            expect(page.locator("#ob-floor [role=alert]")).to_have_count(0)
            expect(page.locator('[data-ob="budgetpick"][data-val="1500"]')).to_have_attribute("aria-pressed", "true")

            # Back goes to the previous step, not out of Ziggy
            page.go_back()
            expect(page.get_by_role("heading", name="How do your days usually go?")).to_be_visible()
            page.go_back()
            expect(page.get_by_role("heading", name="What do you eat?")).to_be_visible()
            page.go_back()
            expect(page.get_by_role("button", name="Get started")).to_be_visible()

            # Tamil
            page.locator('[data-lang="ta"]').click()
            expect(page.get_by_role("button", name="தொடங்கு")).to_be_visible()
            assert page.evaluate("document.documentElement.lang") == "ta"
            page.get_by_role("button", name="தொடங்கு").click()
            expect(page.get_by_role("heading", name="நீங்கள் என்ன சாப்பிடுவீர்கள்?")).to_be_visible()
            no_overflow(page)
            assert page.evaluate(CONTRAST_JS) == []
            assert not errors, errors
        except Exception:
            _shot(page, "onboarding")
            raise
        finally:
            browser.close()
