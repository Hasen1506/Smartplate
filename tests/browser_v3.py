"""Ziggy v3 ("tonight's meal"), in real Chromium at 390 px and 1280 px, dark and light.

Production settings (no sample data) and a stand-in Swiggy neighbourhood (tests/v3_town.py).
The core flow, as a new person: welcome → one screen of rules → connect Swiggy (the real
OAuth redirect, Swiggy's page played by a route) → choose the address → Tonight's pick with
alternatives → swap in one tap → Add to Swiggy cart → Swiggy's real bill → "I've ordered it"
→ Week shows money left from the real bill → a past meal asks to be confirmed → More.
Checks one primary per screen, real vs est. money labels, no opaque score, no horizontal
scroll, WCAG AA contrast and no console errors. The classic app at / is untouched.
Set SMARTPLATE_SHOTS=<dir> to save a PNG of every step.
Run explicitly: `pytest tests/browser_v3.py` (CI does).
"""
import datetime as dt
import os
from pathlib import Path

import pytest

from browser_redesign import CONTRAST_JS, _launch, no_overflow
from v3_town import TONIGHT, start, swiggy_signin_page

pw = pytest.importorskip("playwright.sync_api")
expect = pw.expect


@pytest.fixture
def town(monkeypatch):
    w = start(monkeypatch.setattr)
    yield w
    w["stop"]()


def primaries(page):
    return page.evaluate("""() => [...document.querySelectorAll('.primary')]
        .filter(b => b.getClientRects().length && !b.closest('[role=dialog]')).map(b => b.textContent.trim())""")


def browser_time(at):
    return at.replace(tzinfo=dt.timezone(dt.timedelta(hours=5, minutes=30)))


@pytest.mark.parametrize("viewport,scheme", [({"width": 390, "height": 844}, "dark"),
                                             ({"width": 1280, "height": 900}, "dark"),
                                             ({"width": 1280, "height": 900}, "light")],
                         ids=["phone-390-dark", "desktop-1280-dark", "desktop-1280-light"])
def test_v3_tonight_flow(town, viewport, scheme):
    w = town
    shots = os.environ.get("SMARTPLATE_SHOTS")
    tag = f"{viewport['width']}-{scheme}"
    with pw.sync_playwright() as playwright:
        browser = _launch(playwright)
        context = browser.new_context(viewport=viewport, timezone_id="Asia/Kolkata", locale="en-IN",
                                      color_scheme=scheme)
        page = context.new_page()
        page.clock.set_fixed_time(browser_time(TONIGHT))
        errors, taps = [], []
        page.on("pageerror", lambda e: errors.append(str(e)))
        swiggy_signin_page(page, w["fake"])

        def shot(name):
            if shots:
                Path(shots).mkdir(parents=True, exist_ok=True)
                # The whole screen as one tall phone: no fixed bar or toast floating mid-page.
                page.evaluate("document.querySelectorAll('.toast').forEach(t => t.remove())")
                tall = page.evaluate("Math.max(document.documentElement.scrollHeight, document.body.scrollHeight)")
                page.set_viewport_size({"width": viewport["width"], "height": max(viewport["height"], tall)})
                page.wait_for_timeout(150)
                page.screenshot(path=f"{shots}/{tag}-{name}.png")
                page.set_viewport_size(viewport)

        def check(name, primary=None):
            page.wait_for_timeout(450)                  # let the entry animations settle
            no_overflow(page)
            assert page.evaluate(CONTRAST_JS) == [], name
            if primary is not None:
                assert primaries(page) == primary, (name, primaries(page))
            shot(name)

        def tap(locator):
            taps.append(1)
            locator.click()

        try:
            # --- welcome → one screen of rules ---------------------------------------- #
            page.goto(w["url"] + "/v3")
            expect(page.get_by_role("heading", name="Tonight's dinner, decided.")).to_be_visible()
            check("01-welcome", ["Get started"])
            tap(page.get_by_role("button", name="Get started"))
            expect(page.get_by_role("group", name="Diet", exact=True)).to_be_visible()
            tap(page.get_by_role("group", name="Diet", exact=True).get_by_role("button", name="Veg", exact=True))
            # dinner is preselected and ₹2,500 is prefilled; one more rule: no peanuts? not here
            expect(page.get_by_role("button", name="Dinner")).to_have_attribute("aria-pressed", "true")
            check("02-rules", ["Plan tonight"])
            tap(page.get_by_role("button", name="Plan tonight"))

            # --- connect Swiggy: the real redirect and callback, then the address ---------- #
            expect(page.get_by_role("heading", name="Connect Swiggy for real dishes and real bills")).to_be_visible()
            check("03-connect", ["Connect Swiggy"])
            tap(page.get_by_role("button", name="Connect Swiggy"))
            expect(page.get_by_role("heading", name="Where should dinner come to?")).to_be_visible()
            assert page.url.endswith("/v3"), page.url                  # came back to v3, not the classic app
            expect(page.get_by_role("button", name="Home", exact=False).first).to_be_visible()
            check("04-address")
            tap(page.locator('[data-addr="addr-home"]'))

            # --- Tonight: one pick, why in plain words, one primary, alternatives ---------- #
            hero = page.get_by_role("region", name="Tonight")
            expect(hero).to_be_visible(timeout=15000)
            expect(hero.get_by_role("button", name="Add to Swiggy cart")).to_be_visible()
            assert "score" not in hero.inner_text().lower()
            assert hero.locator(".est").count() >= 1                      # the plan price is an estimate
            expect(hero.get_by_role("list", name="Why this pick")).to_contain_text("Vegetarian")
            expect(hero).to_contain_text("Order by")
            alts = hero.get_by_role("group", name="Or instead", exact=False)
            expect(alts.locator(".v3-alt").first).to_be_visible()
            assert 1 <= alts.locator(".v3-alt").count() <= 3
            pill = page.get_by_role("button", name="left this week", exact=False)
            expect(pill).to_be_visible()
            check("05-tonight", ["Add to Swiggy cart"])

            # --- more choices (sheet), then a one-tap swap from the alternatives ------------ #
            tap(hero.get_by_role("button", name="More choices"))
            sheet = page.get_by_role("dialog")
            expect(sheet.get_by_role("heading", name="Change dinner")).to_be_visible()
            check("06-more-choices")
            tap(sheet.get_by_role("button", name="Close"))
            first = alts.locator(".v3-alt").first
            other = first.get_attribute("data-pick-name")
            tap(first)
            expect(page.locator(".toast")).to_contain_text(f"{other} it is")
            expect(hero.locator("h1")).to_have_text(other)
            check("07-swapped", ["Add to Swiggy cart"])

            # --- one tap: Swiggy's real bill, the money after it, Pay in Swiggy ------------- #
            tap(hero.get_by_role("button", name="Add to Swiggy cart"))
            hero = page.get_by_role("region", name="Tonight: in your Swiggy cart")
            expect(hero.locator(".bill")).to_contain_text("To Pay")
            assert hero.locator(".real").count() >= 2                     # Swiggy's own lines, exact
            expect(hero.get_by_role("link", name="Pay in Swiggy")).to_have_attribute("href", "https://www.swiggy.com/checkout")
            expect(hero).to_contain_text("left for the rest of the week")
            check("08-bill", ["Pay in Swiggy"])
            assert "place_food_order" not in w["fake"].tool_calls()       # Ziggy never orders or pays
            tap(hero.get_by_role("button", name="I've ordered it"))
            expect(page.get_by_role("region", name="Tonight: had")).to_be_visible()

            # --- the account comes LAST, by email, after the first meal --------------------- #
            step = page.get_by_role("region", name="Keep this plan on every device")
            expect(step).to_contain_text("Last step · your Ziggy account")
            expect(step).to_contain_text("Swiggy stays signed in separately")
            check("09-account-step", ["Save my Ziggy account"])
            step.get_by_label("Email").fill("priya.k@example")              # not an email: refused here
            step.get_by_label("Password").fill("dosa-every-night")
            tap(step.get_by_role("button", name="Save my Ziggy account"))
            expect(page.get_by_role("alert")).to_contain_text("valid email")
            step.get_by_label("Email").fill("Priya.K@Example.com")
            step.get_by_label("Password").fill("dosa-every-night")
            step.get_by_label("Your name (optional)").fill("Priya")
            tap(step.get_by_role("button", name="Save my Ziggy account"))
            expect(page.locator(".toast")).to_contain_text("priya.k@example.com")
            expect(page.get_by_role("region", name="Keep this plan on every device")).to_have_count(0)
            check("10-had", [])

            # --- Week: money left, from the real bill ---------------------------------------- #
            tap(page.get_by_role("navigation", name="Main").get_by_role("button", name="Week"))
            money = page.get_by_role("region", name="This week's food money")
            expect(money).to_contain_text("From Swiggy bills")
            assert money.locator(".real").count() == 1
            expect(money).not_to_contain_text("₹0From Swiggy")
            check("11-week", [])

            # --- two days later: a past dinner nobody confirmed asks to be settled ---------- #
            w["at"] = TONIGHT + dt.timedelta(days=1, hours=5)            # Tue 23:30, Tue dinner has passed
            page.clock.set_fixed_time(browser_time(w["at"]))
            page.reload()
            tap(page.get_by_role("navigation", name="Main").get_by_role("button", name="Week"))
            settle = page.get_by_role("region", name="Did you have these?")
            expect(settle).to_be_visible()
            expect(page.get_by_role("region", name="This week's food money")).to_contain_text("Not confirmed yet")
            check("12-week-settle", [])
            tap(settle.get_by_role("button", name="Yes").first)
            expect(page.get_by_role("region", name="Did you have these?")).to_have_count(0)

            # --- More: rules, Swiggy, reminders, account; the rest in the classic app ------- #
            tap(page.get_by_role("navigation", name="Main").get_by_role("button", name="More"))
            expect(page.get_by_role("link", name="Household", exact=False)).to_have_attribute("href", "/?tab=more")
            expect(page.get_by_role("region", name="Your Ziggy account")).to_contain_text("Signed in as priya.k@example.com")
            check("13-more", [])
            tap(page.get_by_role("button", name="Diet & budget", exact=False))
            rules = page.get_by_role("dialog")
            expect(rules.get_by_role("button", name="Veg", exact=True)).to_have_attribute("aria-pressed", "true")
            check("14-rules-sheet")
            rules.get_by_role("button", name="Close").click()

            # --- another device: sign in by email, any case ------------------------------- #
            page.get_by_role("button", name="Sign out of this device").click()
            expect(page.get_by_role("heading", name="Tonight's dinner, decided.")).to_be_visible()
            page.get_by_role("button", name="I already have a profile").click()
            page.get_by_label("Email").fill("PRIYA.K@example.com")
            page.get_by_label("Password").fill("dosa-every-night")
            check("15-sign-in", ["Sign in"])
            page.get_by_role("button", name="Sign in", exact=True).click()
            expect(page.get_by_role("button", name="left this week", exact=False)).to_be_visible()
            assert not errors, errors
            print(f"\nv3 core-flow taps ({tag}): {len(taps)}")
        except Exception:
            Path("test-artifacts").mkdir(exist_ok=True)
            page.screenshot(path=f"test-artifacts/v3-{tag}.png", full_page=True)
            raise
        finally:
            browser.close()


def test_classic_app_is_unchanged_and_v3_is_a_switch(town):
    """/ still serves the classic app; /v3 and /?ui=v3 serve the experiment."""
    client = town["app"].test_client()
    classic = client.get("/").get_data(as_text=True)
    assert '<script src="/static/app.js?v=' in classic and "v3" not in classic
    for path in ("/v3", "/?ui=v3"):
        html = client.get(path).get_data(as_text=True)
        assert '<script src="/static/v3/app.js?v=' in html and '<meta name="ziggy-ui" content="v3">' in html
