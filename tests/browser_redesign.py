"""Dark redesign (8 Oct 2026), in real Chromium at 375 px and 1280 px.

A connected, non-vegetarian profile with no allergies (so its cart can be filled) plans its
week from one live restaurant (a fake Swiggy MCP server; nothing real is touched). Checks:
three destinations (bottom tabs on a phone, a sidebar on a computer), the address pill,
one saffron primary per screen, the main flow in two taps ("Review & add to Swiggy cart" →
"Add to Swiggy cart" → Swiggy's real bill on Today), the full live menu with Swiggy's
categories, a one-tap filter, a photo that fails to load falling back to its dish icon,
one-tap Order/Cook, Pin and On/Off on the week, no horizontal scroll, and WCAG AA text contrast.
Run explicitly in CI after installing Playwright: `pytest tests/browser_redesign.py`.
"""
import json
import os
from pathlib import Path
from threading import Thread

import pytest
from werkzeug.serving import make_server

from smartplate import config
from smartplate.app import create_app
from smartplate.integrations import swiggy_connect
from test_followups import _connect
from test_swiggy_live import FakeLive

pw = pytest.importorskip("playwright.sync_api")
expect = pw.expect
FROZEN_JS = "2026-11-02T08:00:00+05:30"
PHOTO = "https://media-assets.swiggy.com/swiggy/image/upload/fl_lossy/ghee-roast.jpg"
CATEGORIES = {"Ghee Roast Dosa": ["Recommended", "Dosa"], "Masala Dosa": ["Dosa"],
              "Chicken Biryani": ["Biryani"], "Paneer Butter Masala": ["Curries"],
              "Veg Meals": ["Meals"], "Filter Coffee": ["Beverages"]}


class MenuFake(FakeLive):
    """The documented get_restaurant_menu shape with real-looking categories and bestsellers;
    search_menu adds one Swiggy-CDN photo (blocked in the test, so the icon fallback shows)."""

    def tool(self, name, args):
        reply = super().tool(name, args)
        data = reply.get("structuredContent", {}).get("data") if isinstance(reply, dict) else None
        if name == "get_restaurant_menu" and data:
            for item in data["items"]:
                item["categories"] = CATEGORIES.get(item["name"], ["Mains"])
                item["isBestseller"] = item["name"] == "Ghee Roast Dosa"
                item["isVeg"] = "Chicken" not in item["name"]
            data["categoryLabels"] = ["Recommended", "Dosa", "Biryani", "Curries", "Meals", "Beverages"]
        if name == "search_menu" and data:
            for item in data["items"]:
                if item["name"] == "Ghee Roast Dosa":
                    item["imageUrl"] = PHOTO
        return reply


def _launch(playwright):
    exe = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE")
    return playwright.chromium.launch(executable_path=exe) if exe else playwright.chromium.launch()


@pytest.fixture
def world(gt, monkeypatch):
    monkeypatch.setattr(config, "SWIGGY_PROVIDER", "live")
    fake = MenuFake()
    fake.dishes = {"Ghee Roast Dosa": 14000, "Masala Dosa": 12000, "Chicken Biryani": 26000,
                   "Paneer Butter Masala": 22000, "Veg Meals": 18000, "Filter Coffee": 4000}
    monkeypatch.setattr(swiggy_connect, "_http", fake)
    app = create_app()
    client = app.test_client()
    created = client.post("/api/profiles", json={"name": "Arun", "diet": "nonveg", "weekly_budget": 3000,
        "rhythm": {"breakfast": "skip", "lunch": "order", "dinner": "order"}, "cook": "sometimes",
        "allergens": [], "medical": [], "favourites": []}).get_json()
    uid = created["user"]["id"]
    _connect(client, fake, uid=uid)
    key = client.environ_base["HTTP_X_SMARTPLATE_KEY"]
    assert client.post(f"/api/user/{uid}/swiggy/address", json={"address_id": "addr-home"}).status_code == 200
    assert client.post(f"/api/user/{uid}/swiggy/favourites", json={
        "restaurant_id": "r-1", "restaurant_name": "Hotel Saravana Bhavan (Adyar)"}).status_code == 200
    plan = client.get(f"/api/user/{uid}/plan").get_json()
    live = client.post(f"/api/plan/{plan['plan']['id']}/live-menus", json={})
    assert live.status_code == 200, live.get_json()
    srv = make_server("127.0.0.1", 0, app, threaded=True)
    thread = Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield {"url": f"http://127.0.0.1:{srv.server_port}", "uid": uid, "key": key, "fake": fake, "client": client,
           "plan": plan["plan"]["id"]}
    srv.shutdown()
    thread.join(timeout=5)
    srv.server_close()


def _open(browser, w, viewport):
    context = browser.new_context(viewport=viewport, timezone_id="Asia/Kolkata", locale="en-IN")
    page = context.new_page()
    page.clock.set_fixed_time(FROZEN_JS)
    page.route("https://media-assets.swiggy.com/**", lambda r: r.fulfill(status=404, body=""))
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    bag = {str(w["uid"]): {"key": w["key"], "name": "Arun"}}
    page.add_init_script(f"""
        if (!localStorage.getItem('smartplate.keys')) {{
          localStorage.setItem('smartplate.keys', {json.dumps(json.dumps(bag))});
          localStorage.setItem('smartplate.user', {json.dumps(str(w['uid']))});
        }}
        globalThis.policyViolations = [];
        document.addEventListener('securitypolicyviolation', e => policyViolations.push(e.violatedDirective));
    """)
    page.goto(w["url"])
    expect(page.get_by_role("heading", name="Order from your area")).to_be_visible()
    return page, errors


def no_overflow(page):
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), "Page overflows the viewport"


# WCAG AA for every visible text node: 4.5:1 (3:1 for large text). Disabled or deliberately
# dimmed (opacity < 1) content is exempt, as WCAG allows for inactive components.
CONTRAST_JS = r"""() => {
  const parse = c => { const m = c.match(/rgba?\(([^)]+)\)/); if (!m) return null;
    const p = m[1].split(',').map(x => parseFloat(x)); return [p[0], p[1], p[2], p.length > 3 ? p[3] : 1]; };
  const lum = ([r, g, b]) => { const f = v => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b); };
  const over = (top, under) => { const a = top[3]; return [0,1,2].map(i => top[i] * a + under[i] * (1 - a)).concat(1); };
  const bgOf = el => { const chain = []; for (let n = el; n && n.nodeType === 1; n = n.parentElement) chain.push(n);
    let bg = [10, 10, 11, 1];
    for (const n of chain.reverse()) { const c = parse(getComputedStyle(n).backgroundColor); if (c && c[3] > 0) bg = over(c, bg); }
    return bg; };
  const dimmed = el => { for (let n = el; n && n.nodeType === 1; n = n.parentElement) {
      if (parseFloat(getComputedStyle(n).opacity) < 1 || n.disabled) return true; } return false; };
  const bad = [];
  const walk = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  while (walk.nextNode()) {
    const t = walk.currentNode, el = t.parentElement;
    if (!t.textContent.trim() || !el || !el.getClientRects().length || dimmed(el)) continue;
    const cs = getComputedStyle(el);
    if (cs.visibility === 'hidden' || cs.display === 'none') continue;
    const fg = parse(cs.color); if (!fg) continue;
    const bg = bgOf(el), col = over(fg, bg);
    const L1 = lum(col), L2 = lum(bg), ratio = (Math.max(L1, L2) + 0.05) / (Math.min(L1, L2) + 0.05);
    const size = parseFloat(cs.fontSize), bold = parseInt(cs.fontWeight) >= 700;
    const need = (size >= 24 || (size >= 18.66 && bold)) ? 3 : 4.5;
    if (ratio < need) bad.push(`${t.textContent.trim().slice(0, 40)} (${ratio.toFixed(2)})`);
  }
  return bad;
}"""


def primaries(page):
    """Saffron buttons visible on the screen itself (dialogs are their own screen)."""
    return page.evaluate("""() => [...document.querySelectorAll('main .primary, .topbar .primary, .nav .primary')]
        .filter(b => b.getClientRects().length && !b.closest('[role=dialog]')).map(b => b.textContent.trim())""")


@pytest.mark.parametrize("viewport", [{"width": 375, "height": 812}, {"width": 1280, "height": 900}], ids=["phone-375", "desktop-1280"])
def test_redesign_today_cart_menu_week_you(world, viewport):
    w = world
    with pw.sync_playwright() as playwright:
        browser = _launch(playwright)
        page, errors = _open(browser, w, viewport)
        shots = os.environ.get("SMARTPLATE_SHOTS")
        shot = (lambda name: page.screenshot(path=f"{shots}/{viewport['width']}-{name}.png", full_page=True)) if shots else (lambda name: None)
        try:
            # --- shell: three destinations, same on phone and web; address pill ------------- #
            nav = page.get_by_role("navigation", name="Main")
            assert nav.get_by_role("button").all_inner_texts() == ["Today", "Plan", "You"]
            box = nav.bounding_box()
            if viewport["width"] >= 1024:
                assert box["x"] == 0 and box["height"] >= 800 and box["width"] < 300      # sidebar
            else:
                assert box["y"] > 700 and box["width"] == viewport["width"]               # bottom tabs
            pill = page.get_by_role("button", name="Delivering to Home", exact=False)
            expect(pill).to_be_visible()

            # --- Today: one pick, plain-word reasons, one primary, real reasons ---------------- #
            hero = page.locator('section[aria-label="Next meal"]')
            expect(hero).to_be_visible()
            assert "Fit" not in hero.inner_text()                                           # no opaque score
            expect(hero.get_by_role("list", name="Why this pick")).to_be_visible()
            expect(hero.locator(".dicon")).to_be_visible()                                  # dish identity
            assert primaries(page) == ["Review & add to Swiggy cart"], primaries(page)
            assert hero.locator(".est").count() >= 1                                         # the plan price is an estimate
            assert page.evaluate(CONTRAST_JS) == []
            no_overflow(page)
            shot("today")

            # --- the main flow: two taps to the real bill -------------------------------------- #
            hero.get_by_role("button", name="Review & add to Swiggy cart").click()          # tap 1
            dialog = page.get_by_role("dialog")
            expect(dialog.get_by_role("heading", name="Add to your Swiggy cart?")).to_be_visible()
            expect(dialog.locator(".est")).to_have_count(1)                                 # a menu price is an estimate
            dialog.get_by_role("button", name="Add to Swiggy cart", exact=True).click()     # tap 2
            hero = page.locator('section[aria-label="Next meal"]')
            bill = hero.locator(".billcard")
            expect(bill).to_contain_text("Swiggy's bill")
            expect(bill).to_contain_text("To Pay")
            expect(hero).to_contain_text("Added to your Swiggy cart")
            checkout = hero.get_by_role("link", name="Open Swiggy checkout ↗")
            expect(checkout).to_have_attribute("href", "https://www.swiggy.com/checkout")
            expect(hero.locator(".cancel-note")).to_contain_text("cancellation policy applies")
            assert "update_food_cart" in w["fake"].tool_calls() and "place_food_order" not in w["fake"].tool_calls()
            expect(hero).to_contain_text("Plan estimate")
            # the next step is paying in Swiggy: it becomes the one primary; the cart isn't shown twice
            assert primaries(page) == ["Open Swiggy checkout ↗"], primaries(page)
            assert page.get_by_text("Added to your Swiggy cart", exact=False).count() == 1
            assert page.get_by_text("In your Swiggy cart:", exact=False).count() == 0     # the cart is shown once
            expect(page.locator("main")).not_to_contain_text("cart for Home · 12 Lake View Rd, Adyar is empty")
            assert page.evaluate(CONTRAST_JS) == []
            shot("bill")

            # --- address pill: change address in two taps --------------------------------------- #
            page.get_by_role("button", name="Delivering to Home", exact=False).click()
            sheet = page.get_by_role("dialog", name="Deliver to")
            expect(sheet.locator('[data-swaddr="addr-home"]')).to_have_attribute("aria-pressed", "true")
            expect(sheet).to_contain_text("Address not listed?")
            shot("address")
            sheet.get_by_role("button", name="Close").click()

            # --- the full live menu: categories, a filter, photo → icon fallback ------------------ #
            page.locator('[data-live-place="r-1"]').first.click()
            menu = page.locator("section.livemenu")
            expect(menu).to_contain_text("6 current dishes")
            cats = menu.get_by_role("group", name="Menu categories")
            assert cats.get_by_role("button").all_inner_texts() == ["All", "Bestsellers", "Dosa", "Biryani", "Curries", "Meals", "Beverages"]
            assert [t.upper() for t in menu.locator(".lmcat").all_inner_texts()[:2]] == ["DOSA", "BIRYANI"]
            expect(menu.locator(".vegmark.nonveg")).to_have_count(1)
            expect(menu.locator(".lmrow").filter(has_text="Ghee Roast Dosa")).to_contain_text("Bestseller")
            assert len(set(menu.locator(".dicon").evaluate_all("els => els.map(e => e.dataset.kind)"))) >= 5
            cats.get_by_role("button", name="Beverages").click()
            expect(page.locator("section.livemenu .lmrow")).to_have_count(1)
            page.locator("section.livemenu").get_by_role("group", name="Menu categories").get_by_role("button", name="All").click()
            page.locator("section.livemenu").get_by_role("button", name="Veg only").click()     # one tap
            expect(page.locator("section.livemenu .lmrow")).to_have_count(5)
            expect(page.locator("section.livemenu")).to_contain_text("1 hidden by your filters")
            page.locator("section.livemenu").get_by_role("button", name="Veg only").click()
            page.get_by_role("textbox", name="Search this restaurant").fill("ghee")
            page.get_by_role("button", name="Find dishes", exact=True).click()
            row = page.locator("section.livemenu .lmrow").filter(has_text="Ghee Roast Dosa")
            expect(row.locator('.dicon[data-kind="dosa"]')).to_be_visible()                 # blocked photo → icon
            assert page.evaluate(CONTRAST_JS) == []
            no_overflow(page)
            shot("menu")

            # --- Plan: one-tap On/Off, Order/Cook, Pin -------------------------------------------- #
            page.get_by_role("navigation", name="Main").get_by_role("button", name="Plan").click()
            expect(page.get_by_role("heading", name="Week of 2 Nov")).to_be_visible()
            assert primaries(page) == ["↻ Re-plan"], primaries(page)
            tue = lambda: w["client"].get(f"/api/plan/{w['plan']}").get_json()["grid"][1]["meals"]   # noqa: E731
            day = page.locator('section.day[aria-label="Tue"]')
            if tue()["dinner"]["kind"] != "cook":
                day.get_by_role("group", name="Dinner quick changes").get_by_role("button", name="Cook", exact=True).click()
                expect(page.locator(".toast")).to_contain_text("Cook:")
                assert tue()["dinner"]["kind"] == "cook"
            day = page.locator('section.day[aria-label="Tue"]')
            day.get_by_role("group", name="Lunch quick changes").get_by_role("button", name="Pin this meal so re-plans keep it").click()
            expect(page.locator(".toast")).to_contain_text("Pinned")
            assert tue()["lunch"]["pinned"] is True
            day = page.locator('section.day[aria-label="Tue"]')
            day.get_by_role("group", name="Lunch quick changes").get_by_role("button", name="Lunch on. Tap to skip it").click()
            expect(page.locator(".toast")).to_contain_text("Skipped")
            assert tue()["lunch"]["status"] == "skipped"
            day = page.locator('section.day[aria-label="Tue"]')
            day.get_by_role("group", name="Lunch quick changes").get_by_role("button", name="Lunch off. Tap to plan it again").click()
            expect(page.locator(".toast")).to_contain_text("Back in the plan")
            assert page.evaluate(CONTRAST_JS) == []
            no_overflow(page)
            shot("plan")
            page.get_by_role("navigation", name="Plan").get_by_role("button", name="Cook & groceries").click()
            expect(page.get_by_role("heading", name="Cooking & groceries")).to_be_visible()
            no_overflow(page)

            # --- You: grouped, insights and expenses inside ---------------------------------------- #
            page.get_by_role("navigation", name="Main").get_by_role("button", name="You").click()
            expect(page.get_by_role("heading", name="You", exact=True)).to_be_visible()
            for item in ("This week", "Expenses", "Nutrition & insights", "Settings", "Swiggy connection"):
                expect(page.locator(".mitem b", has_text=item).first).to_be_visible()
            assert page.evaluate(CONTRAST_JS) == []
            no_overflow(page)
            shot("you")
            page.locator('[data-go="more:settings"]').click()
            for legend in ("Food rules (always applied)", "Money", "What to plan"):
                expect(page.locator("legend", has_text=legend)).to_be_visible()
            no_overflow(page)
            shot("settings")

            assert not errors, errors
            assert page.evaluate("policyViolations") == []
            assert "place_food_order" not in w["fake"].tool_calls()
        except Exception:
            Path("test-artifacts").mkdir(exist_ok=True)
            page.screenshot(path=f"test-artifacts/redesign-{viewport['width']}.png", full_page=True)
            raise
        finally:
            browser.close()
