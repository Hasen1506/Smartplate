"""Ziggy, in real Chromium at 375 px and 1280 px.

A connected, non-vegetarian profile with no allergies (so its cart can be filled) plans its
week from one live restaurant (a fake Swiggy MCP server; nothing real is touched). Checks:
three tabs (bottom bar on a phone, a side rail on a computer), the address pill, one
primary per screen, the main flow in ONE tap ("Add to Swiggy cart" → Swiggy's real bill on
Today), the address sheet, the full live menu from Saved with Swiggy's categories, a
one-tap filter, a photo that fails to load falling back to its dish icon, the Change sheet
(cook instead, keep, skip with undo), Me, light and dark themes and the Zomato-to-Swiggy
colour mesh, no horizontal scroll, and WCAG AA text contrast.
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
    expect(page.get_by_role("navigation", name="Main")).to_be_visible()
    page.wait_for_load_state("networkidle")              # Today's other picks load after the first paint
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
    let bg = [255, 255, 255, 1];
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
    """Primary buttons visible on the screen itself (dialogs are their own screen)."""
    return page.evaluate("""() => [...document.querySelectorAll('main .primary, .topbar .primary, .nav .primary')]
        .filter(b => b.getClientRects().length && !b.closest('[role=dialog]')).map(b => b.textContent.trim())""")


@pytest.mark.parametrize("viewport", [{"width": 375, "height": 812}, {"width": 1280, "height": 900}], ids=["phone-375", "desktop-1280"])
def test_redesign_today_cart_menu_week_me(world, viewport):
    w = world
    with pw.sync_playwright() as playwright:
        browser = _launch(playwright)
        page, errors = _open(browser, w, viewport)
        shots = os.environ.get("SMARTPLATE_SHOTS")
        shot = (lambda name: page.screenshot(path=f"{shots}/{viewport['width']}-{name}.png", full_page=True)) if shots else (lambda name: None)
        try:
            # --- shell: three tabs, same on phone and web; address pill; avatar for Me ------ #
            nav = page.get_by_role("navigation", name="Main")
            assert [t.strip() for t in nav.get_by_role("button").all_inner_texts()] == ["Today", "Week", "Saved"]
            box = nav.bounding_box()
            if viewport["width"] >= 900:
                assert box["x"] == 0 and box["height"] >= 800 and box["width"] < 300      # side rail
            else:
                assert box["y"] > 700 and box["width"] == viewport["width"]               # bottom tabs
            pill = page.get_by_role("button", name="Delivering to Home", exact=False)
            expect(pill).to_be_visible()

            # --- Today: one pick, plain-word reasons, one primary --------------------------- #
            hero = page.locator('section[aria-label="Next meal"]')
            expect(hero).to_be_visible()
            assert "Fit" not in hero.inner_text()                                           # no opaque score
            expect(hero.get_by_role("list", name="Why this pick")).to_be_visible()
            expect(hero.locator(".dish-row .dicon")).to_be_visible()                        # dish identity
            assert primaries(page) == ["Add to Swiggy cart"], primaries(page)
            assert hero.locator(".est").count() >= 1                                         # the plan price is an estimate
            # other dishes for the same meal, one tap away (no sheet): the tap swaps the meal
            quick = hero.get_by_role("group", name="Or have instead")
            expect(quick.locator(".qpick").first).to_be_visible()
            assert 1 <= quick.locator(".qpick").count() <= 5
            first = quick.locator("[data-quick]").first
            other = first.get_attribute("data-quick-name")
            assert other != hero.locator("h2").inner_text()
            first.click()
            expect(page.locator(".toast")).to_contain_text(f"{other} it is")
            expect(page.locator('section[aria-label="Next meal"] h2')).to_have_text(other)
            hero = page.locator('section[aria-label="Next meal"]')
            # Hungry now: the week leaves breakfast out (rhythm), and it's breakfast time
            expect(page.get_by_role("region", name="Hungry now")).to_contain_text("Breakfast isn't in your week")
            # a computer shows the week's balance beside the meals; a phone doesn't
            aside = page.get_by_role("complementary", name="Your week at a glance")
            if viewport["width"] >= 1180:
                expect(aside).to_contain_text("left")
                expect(aside.locator(".nut")).to_have_count(4)
                expect(aside.locator("[data-glance-day]")).to_have_count(7)
            else:
                expect(aside).to_be_hidden()
            assert page.evaluate(CONTRAST_JS) == []
            no_overflow(page)
            shot("today")

            # --- the main flow: one tap to the real bill ------------------------------------- #
            hero.get_by_role("button", name="Add to Swiggy cart").click()
            hero = page.locator('section[aria-label="Next meal"]')
            bill = hero.locator(".bill-card")
            expect(bill).to_contain_text("Swiggy's bill")
            expect(bill).to_contain_text("To Pay")
            expect(hero).to_contain_text("Added to your Swiggy cart")
            checkout = hero.get_by_role("link", name="Pay in Swiggy")
            expect(checkout).to_have_attribute("href", "https://www.swiggy.com/checkout")
            expect(hero.locator(".cancel-note")).to_contain_text("cancellation policy applies")
            assert "update_food_cart" in w["fake"].tool_calls() and "place_food_order" not in w["fake"].tool_calls()
            expect(hero).to_contain_text("Plan estimate")
            # the next step is paying in Swiggy: it becomes the one primary; the cart isn't shown twice
            assert primaries(page) == ["Pay in Swiggy"], primaries(page)
            assert page.locator("main").get_by_text("Added to your Swiggy cart", exact=False).count() == 1
            assert page.get_by_text("In your Swiggy cart", exact=False).count() == 0
            expect(page.locator("main")).not_to_contain_text("is empty")
            assert page.evaluate(CONTRAST_JS) == []
            shot("bill")

            # --- address pill: change address in two taps --------------------------------------- #
            page.get_by_role("button", name="Delivering to Home", exact=False).click()
            sheet = page.get_by_role("dialog", name="Deliver to")
            expect(sheet.locator('[data-swaddr="addr-home"]')).to_have_attribute("aria-pressed", "true")
            expect(sheet).to_contain_text("Address not listed?")
            shot("address")
            sheet.get_by_role("button", name="Close").click()
            expect(page.get_by_role("dialog")).to_have_count(0)

            # --- Saved → the full live menu: categories, a filter, photo → icon fallback ---------- #
            nav.get_by_role("button", name="Saved").click()
            expect(page.get_by_role("heading", name="What you'd have for each meal")).to_be_visible()   # My meals first
            page.get_by_role("button", name="Places", exact=True).click()
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
            page.get_by_role("searchbox", name="Search this restaurant").fill("ghee")
            page.get_by_role("button", name="Find dishes", exact=True).click()
            row = page.locator("section.livemenu .lmrow").filter(has_text="Ghee Roast Dosa")
            expect(row.locator('.dicon[data-kind="dosa"]')).to_be_visible()                 # blocked photo → icon
            assert page.evaluate(CONTRAST_JS) == []
            no_overflow(page)
            shot("menu")
            page.get_by_role("button", name="Close menu").click()

            # --- Week: a day strip; one tap opens the Change sheet ------------------------------ #
            nav.get_by_role("button", name="Week").click()
            expect(page.get_by_role("heading", name="Week of 2 Nov")).to_be_visible()
            assert primaries(page) == [], primaries(page)
            grid = lambda: w["client"].get(f"/api/plan/{w['plan']}").get_json()["grid"]   # noqa: E731
            # the first day after today with a lunch planned (one saved place with four whole-meal
            # dishes, each at most twice a week, can't fill every lunch and dinner)
            day = next(i for i in range(1, 7) if grid()[i]["meals"]["lunch"]["kind"] == "delivery")
            tue = lambda: grid()[day]["meals"]   # noqa: E731
            page.locator(f'[data-day="{day}"]').click()
            if tue()["dinner"]["kind"] != "cook":
                page.locator(f'.mrow[data-meal="{tue()["dinner"]["session_id"]}"]').click()
                page.get_by_role("dialog").get_by_role("button", name="Cook instead", exact=False).click()
                expect(page.locator(".toast")).to_contain_text("Cook at home")
                assert tue()["dinner"]["kind"] == "cook"
            lunch = tue()["lunch"]["session_id"]
            page.locator(f'.mrow[data-meal="{lunch}"]').click()
            page.get_by_role("dialog").get_by_role("button", name="Keep it, don't re-plan").click()
            expect(page.locator(".toast")).to_contain_text("Kept")
            assert tue()["lunch"]["pinned"] is True
            page.locator(f'.mrow[data-meal="{lunch}"]').click()
            page.get_by_role("dialog").get_by_role("button", name="Skip this meal").click()
            expect(page.locator(".toast")).to_contain_text("skipped")
            assert tue()["lunch"]["status"] == "skipped"
            page.locator(".toast").get_by_role("button", name="Undo").click()
            expect(page.locator(".toast")).to_contain_text("back in the plan")
            assert tue()["lunch"]["status"] != "skipped"
            assert page.evaluate(CONTRAST_JS) == []
            no_overflow(page)
            shot("week")
            page.get_by_role("button", name="Cooking & groceries", exact=False).click()
            expect(page.get_by_role("heading", name="Cooking & groceries")).to_be_visible()
            shop = page.get_by_role("link", name="Find", exact=False).first                 # Instamart hand-off per line
            assert shop.get_attribute("href").startswith("https://www.swiggy.com/instamart/search?")
            no_overflow(page)

            # --- Me: everything else, grouped; theme and colour in one tap ------------------------ #
            page.get_by_role("button", name="Me: settings, money and account").click()
            for item in ("Food & budget", "Swiggy", "This week & spending", "Nutrition", "Household", "Account & devices"):
                expect(page.locator("button.set", has_text=item).first).to_be_visible()
            assert page.evaluate(CONTRAST_JS) == []
            no_overflow(page)
            shot("me")
            page.get_by_role("group", name="Appearance").get_by_role("button", name="Dark").click()
            assert page.evaluate("document.documentElement.dataset.theme") == "dark"
            page.wait_for_timeout(600)                                                      # let the colour fade finish
            assert page.evaluate(CONTRAST_JS.replace("[255, 255, 255, 1]", "[0, 0, 0, 1]")) == []
            page.get_by_role("group", name="Colour").get_by_role("button", name="Zomato × Swiggy").click()
            assert page.evaluate("document.documentElement.dataset.palette") == "mesh"
            shot("me-dark-mesh")
            page.reload()
            expect(page.get_by_role("navigation", name="Main")).to_be_visible()
            assert page.evaluate("[document.documentElement.dataset.theme, document.documentElement.dataset.palette]") == ["dark", "mesh"]
            page.get_by_role("button", name="Me: settings, money and account").click()
            page.get_by_role("group", name="Appearance").get_by_role("button", name="Light").click()
            page.locator('[data-go="more:settings"]').click()
            for legend in ("Food rules (always applied)", "Money", "What to plan"):
                expect(page.locator("legend", has_text=legend)).to_be_visible()
            no_overflow(page)
            shot("settings")

            # --- Hungry now: breakfast in two taps, even though the week leaves it out --------------- #
            nav.get_by_role("button", name="Today").click()
            page.get_by_role("region", name="Hungry now").get_by_role("button", name="Get breakfast").click()
            expect(page.locator(".toast")).to_contain_text("Breakfast is in")
            hero = page.locator('section[aria-label="Next meal"]')
            expect(hero.locator(".eyebrow").first).to_contain_text("Breakfast")
            assert w["client"].get(f"/api/plan/{w['plan']}").get_json()["grid"][0]["meals"]["breakfast"]["kind"] in ("delivery", "cook")
            expect(page.get_by_role("region", name="Hungry now")).to_have_count(0)

            assert not errors, errors
            assert page.evaluate("policyViolations") == []
            assert "place_food_order" not in w["fake"].tool_calls()
        except Exception:
            Path("test-artifacts").mkdir(exist_ok=True)
            page.screenshot(path=f"test-artifacts/redesign-{viewport['width']}.png", full_page=True)
            raise
        finally:
            browser.close()
