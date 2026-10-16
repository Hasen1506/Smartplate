"""Fix round 3 "history-favs" in real Chromium at 390 px (ux-audit 16 Oct 2026 #5, #7, #8, #29).

After connecting Swiggy, the Add sheet turns past orders into favourites in one or two taps:
the consent is one line + Details, the checklist rows are readable at 390 px (no 28 px badge
over squashed text), a whole past order is one tick, and a place from the orders opens its
menu in one tap. Screenshots go to $V3_SHOTS when set.
Run: `pytest tests/browser_r3_history.py`.
"""
import pytest

from browser_redesign import _launch, no_overflow
from browser_v3_today import BOOT_MS, _page, shot, town  # noqa: F401  (fixture)
from test_r3_history_favs import ORDERS
from test_swiggy_live import ok

pw = pytest.importorskip("playwright.sync_api")
expect = pw.expect
PHONE = {"width": 390, "height": 844}


def _with_orders(fake):
    real = fake.tool

    def tool(name, args):
        return ok({"orders": ORDERS}) if name == "get_food_orders" else real(name, args)
    fake.tool = tool


def test_history_to_favourites_at_390(town):  # noqa: F811
    _with_orders(town["fake"])
    with pw.sync_playwright() as p:
        browser = _launch(p)
        page, errors = _page(browser, town["url"], PHONE, "light", profile=town)
        today = page.locator(".v3-today")
        expect(today.get_by_text("Today's budget")).to_be_visible(timeout=BOOT_MS)
        page.locator('[data-v3-add="lunch"]').first.click()
        sheet = page.get_by_role("dialog", name="New item / restaurant")
        expect(sheet).to_be_visible()
        sugg = sheet.get_by_label("Suggestions from your Swiggy orders")
        # #29: one line + Details, the long note folded away
        expect(sugg.locator("details.v3-consent-more")).to_have_count(1)
        assert not sugg.locator("details.v3-consent-more p").is_visible()
        sugg.get_by_role("button", name="Suggest from my Swiggy orders").click()
        expect(sugg.get_by_text("Veg Biryani + 2 Raita")).to_be_visible()
        # #5: rows are readable: a 20 px checkbox beside full-width text, not a 28 px badge
        rows = sugg.locator(".v3-sugg-row")
        assert rows.count() >= 2
        for n in range(rows.count()):
            box = rows.nth(n).locator("input").bounding_box()
            text = rows.nth(n).locator(".t").bounding_box()
            assert box["width"] <= 22 and box["height"] <= 22, box
            assert text["width"] > 200, text                          # not squashed
        assert sugg.locator(".tick").count() == 0
        # #7: Raita comes inside its order, never alone
        expect(sugg.locator(".v3-sugg-row", has_text="Raita")).to_have_count(1)
        no_overflow(page)
        shot(page, "r3-01-suggestions-390")
        # one tick for the whole order
        sugg.locator(".v3-sugg-row", has_text="Veg Biryani + 2 Raita").locator("input").check()
        sugg.get_by_role("button", name="Add ticked to your usual lunches").click()
        expect(page.locator(".toast")).to_contain_text("2 added to your usual lunches")
        # #8: a place from the orders opens its menu in one tap
        sheet.get_by_role("button", name="Search Swiggy").click()
        sheet.locator(".chips").get_by_role("button", name="Wok Street").click()
        # r3 menus-offers: with a place open the sheet is titled "Add to your usual lunches"
        menu = page.get_by_role("dialog", name="Add to your usual lunches")
        expect(menu.get_by_text("Veg Hakka Noodles")).to_be_visible()
        expect(menu.get_by_role("button", name="All places")).to_be_visible()
        shot(page, "r3-02-menu-from-chip-390")
        assert not [e for e in errors if "favicon" not in e], errors
        browser.close()
