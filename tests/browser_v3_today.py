"""v3 Today (the founder's sketch) in real Chromium at 375 px and 1280 px, light and dark.

Production settings (no fixture data), frozen clock (Mon 2 Nov 2026 08:00 IST), the stand-in
Swiggy neighbourhood (tests/swiggy_town.py). A vegetarian with breakfast + lunch:
  * Today shows "Today's budget ₹600 (B+L)", the weather card, rule chips, no usual meals yet;
  * "+ New item / restaurant" → search Swiggy → a place → a dish → in the usual breakfasts;
    a typed dish; a typed "Chicken Burrito" with no veg mark is greyed out with the reason;
  * a meal line picks from the usual meals (▾), quantity ±, budget presets; "₹X left after fees";
  * nothing is decided until "Decide for me" → a mode → Decide;
  * the price-compare row appears only once a Swiggy-priced pick exists (no fake numbers);
  * Send details hands the text to the share sheet; Place order → Swiggy cart review;
  * Swap is the one way to change a meal; the Week's "Change" sheet is reachable from it;
  * Cook tab: type any dish → ingredients with Instamart links;
  * Week: nutrition is off until turned on;
  * usual meals (founder, Oct 2026): desktop — three panels side by side; phone — no separate
    block, each meal line opens its own "Usual ▾" drawer (pick, add, remove);
  * no horizontal scroll, no console errors, WCAG AA text contrast, in light and dark;
  * first run: two questions (diet with nothing preselected, today's budget + meals) → Today;
    the account is offered only after a first pick;
  * ?ui=classic brings back the previous Today (nothing deleted).
Screenshots go to $V3_SHOTS when set. Run: `pytest tests/browser_v3_today.py`.
"""
import json
import os
import tempfile
from pathlib import Path
from threading import Thread

import pytest
from werkzeug.serving import make_server

from browser_redesign import CONTRAST_JS, FROZEN_JS, _launch, no_overflow
from smartplate import config, ratelimit
from smartplate.app import create_app
from smartplate.integrations import swiggy_connect
from swiggy_town import TownFake
from test_followups import _connect

pw = pytest.importorskip("playwright.sync_api")
expect = pw.expect
PHONE = {"width": 375, "height": 812}
DESKTOP = {"width": 1280, "height": 900}
SHOTS = os.environ.get("V3_SHOTS")
BOOT_MS = 20_000          # first paint / first plan on a loaded CI box; every later wait uses the 5 s default


def shot(page, name):
    if SHOTS:
        Path(SHOTS).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(SHOTS) / f"{name}.png"), full_page=True)


def _serve(app):
    srv = make_server("127.0.0.1", 0, app, threaded=True)
    thread = Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    return srv, thread


@pytest.fixture
def town(frozen, monkeypatch):
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
    ratelimit.reset()
    fake = TownFake()
    monkeypatch.setattr(swiggy_connect, "_http", fake)
    app = create_app()
    client = app.test_client()
    r = client.post("/api/profiles", json={
        "name": "Priya", "diet": "veg", "weekly_budget": 4200, "cook": "never",
        "rhythm": {"breakfast": "order", "lunch": "order", "dinner": "skip"}, "consent": {"account": True}})
    uid = r.get_json()["user"]["id"]
    _connect(client, fake, uid=uid)
    key = client.environ_base["HTTP_X_SMARTPLATE_KEY"]
    assert client.post(f"/api/user/{uid}/swiggy/address", json={"address_id": "addr-home"}).status_code == 200
    srv, thread = _serve(app)
    yield {"url": f"http://127.0.0.1:{srv.server_port}", "uid": uid, "key": key, "fake": fake, "client": client}
    srv.shutdown()
    thread.join(timeout=5)
    srv.server_close()
    if os.path.exists(path):
        os.unlink(path)


@pytest.fixture
def fresh(frozen, monkeypatch):
    """No profile yet, Swiggy sign-in not open (the live state for strangers)."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)
    monkeypatch.setattr(config, "DB_PATH", path)
    monkeypatch.setattr(config, "DATABASE_URL", "")
    monkeypatch.setattr(config, "FIXTURE_DATA", False)
    monkeypatch.setattr(config, "WEATHER_PROVIDER", "simulated")
    monkeypatch.setattr(config, "SWIGGY_PROVIDER", "live")
    monkeypatch.setattr(config, "SWIGGY_REDIRECT_APPROVED", False)
    monkeypatch.setattr(config, "PUSH_ENABLED", False)
    ratelimit.reset()
    srv, thread = _serve(create_app())
    yield {"url": f"http://127.0.0.1:{srv.server_port}"}
    srv.shutdown()
    thread.join(timeout=5)
    srv.server_close()
    if os.path.exists(path):
        os.unlink(path)


def _page(browser, url, viewport, theme="light", profile=None, query=""):
    context = browser.new_context(viewport=viewport, timezone_id="Asia/Kolkata", locale="en-IN")
    page = context.new_page()
    page.clock.set_fixed_time(FROZEN_JS)
    page.route("https://media-assets.swiggy.com/**", lambda r: r.fulfill(status=404, body=""))
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" and "404" not in m.text else None)
    init = f"localStorage.setItem('ziggy.theme', {json.dumps(theme)});"
    if profile:
        bag = {str(profile["uid"]): {"key": profile["key"], "name": "Priya"}}
        init += f"""if (!localStorage.getItem('smartplate.keys')) {{
          localStorage.setItem('smartplate.keys', {json.dumps(json.dumps(bag))});
          localStorage.setItem('smartplate.user', {json.dumps(str(profile['uid']))}); }}"""
    init += "globalThis.shared = []; navigator.share = async (d) => { globalThis.shared.push(d.text); };"
    page.add_init_script(init)
    page.goto(url + query)
    return page, errors


def contrast_ok(page):
    bad = page.evaluate(CONTRAST_JS)
    assert not bad, bad[:5]


def usual(page, viewport, meal):
    """The usual meals for one meal: a panel on a computer, the meal line's drawer on a phone."""
    if viewport["width"] >= 1024:
        return page.locator(".v3-pool", has=page.get_by_role("heading", name=f"Usual {meal}{'es' if meal == 'lunch' else 's'}", exact=True))
    btn = page.locator(f'[data-v3-usual="{meal}"]')
    if not btn.is_visible() and page.locator("[data-v3-other]").count():   # usab #32: other meals folded away
        page.locator("[data-v3-other]").click()
    if btn.get_attribute("aria-expanded") != "true":
        btn.click()
    expect(btn).to_have_attribute("aria-expanded", "true")
    return page.locator(f"#v3-usual-{meal}")


def no_old_name(page):
    """Users see Ziggy, never SmartPlate (founder, Oct 2026)."""
    text = page.evaluate("document.title + ' ' + document.body.innerText")
    assert "smartplate" not in text.lower().replace(" ", ""), text[:200]


def _add_from_swiggy(page, meal_btn, search, place, dish):
    if meal_btn:
        page.locator(meal_btn).click()
    sheet = page.get_by_role("dialog", name="New item / restaurant")
    expect(sheet).to_be_visible()
    sheet.get_by_role("searchbox", name="Search Swiggy").fill(search)
    sheet.get_by_role("button", name="Search", exact=True).click()
    sheet.get_by_role("button", name=place).click()
    sheet.get_by_role("button", name=dish).click()
    expect(page.locator(".toast")).to_contain_text(f"{dish} added")


@pytest.mark.parametrize("viewport,theme", [(PHONE, "light"), (DESKTOP, "dark")], ids=["phone-375-light", "desktop-1280-dark"])
def test_today_favourites_first_journey(town, viewport, theme):
    w = town
    tag = f"{viewport['width']}-{theme}"
    with pw.sync_playwright() as p:
        browser = _launch(p)
        page, errors = _page(browser, w["url"], viewport, theme, profile=w)
        expect(page.get_by_role("navigation", name="Main")).to_be_visible(timeout=BOOT_MS)
        nav = page.get_by_role("navigation", name="Main")
        for label in ("Today", "Week", "Cook", "Saved"):
            expect(nav.get_by_role("button", name=label)).to_be_visible()
        today = page.locator(".v3-today")
        expect(today.get_by_text("Today's budget")).to_be_visible(timeout=BOOT_MS)
        expect(today.locator(".v3-budget .v3-big")).to_have_text("₹600")
        expect(today.locator(".v3-budget")).to_contain_text("(B+L)")
        expect(today.get_by_label("Weather today")).to_have_count(0)    # ux #38: no tile without a live forecast
        rules = today.get_by_label("Your rules")
        for chip in ("Vegetarian", "Any rating", "≤ 45 min", "Ziggy defaults"):     # usab #10
            expect(rules).to_contain_text(chip)
        expect(page.locator(".v3-compare")).to_have_count(0)            # no data → no price row
        assert page.locator("[data-v3-pick]").count() == 0
        # nothing decided for you: the meal lines are empty
        expect(today.get_by_label("Today's meals")).to_contain_text("Add a usual breakfast")
        if viewport["width"] < 1024:
            expect(page.locator(".v3-pools")).to_have_count(0)          # no separate block on a phone
        no_old_name(page)
        page.wait_for_load_state("networkidle")
        shot(page, f"01-today-empty-{tag}")
        no_overflow(page)

        # + New item / restaurant → Swiggy search → place → dish
        usual(page, viewport, "breakfast").locator('[data-v3-add="breakfast"]').click()
        _add_from_swiggy(page, None, "pongal", "Annapoorna Tiffin Room", "Ven Pongal")
        page.get_by_role("button", name="Close add a dish").click()
        # typed dishes: one fine, one greyed out (no veg mark for a vegetarian)
        usual(page, viewport, "lunch").locator('[data-v3-add="lunch"]').click()
        sheet = page.get_by_role("dialog", name="New item / restaurant")
        sheet.get_by_role("button", name="Type it in").click()
        sheet.get_by_role("textbox", name="Dish").fill("Chicken Burrito")
        sheet.get_by_label("Place (optional)").fill("Burrito Co")
        sheet.get_by_label("Price you pay (₹)").fill("220")
        sheet.get_by_role("button", name="Add to your usual lunches").click()
        expect(page.locator(".toast")).to_contain_text("Chicken Burrito added")
        lunch_pool = usual(page, viewport, "lunch")
        expect(lunch_pool.locator(".v3-pool-item.off")).to_contain_text("The name says “chicken” (not veg)")   # W1 reason
        usual(page, viewport, "lunch").locator('[data-v3-add="lunch"]').click()
        sheet = page.get_by_role("dialog", name="New item / restaurant")
        sheet.get_by_role("button", name="Type it in").click()
        sheet.get_by_role("textbox", name="Dish").fill("Veg Thali")
        sheet.get_by_label("Price you pay (₹)").fill("650")
        sheet.locator("label.chip", has_text="Yes").click()
        sheet.get_by_role("button", name="Add to your usual lunches").click()
        expect(page.locator(".toast")).to_contain_text("Veg Thali added")

        # pick breakfast from the usual meals (▾), two of them
        page.get_by_label("Breakfast from your usual meals").select_option(label="Ven Pongal · ₹90 · Annapoorna Tiffin Room")
        expect(page.locator('[data-v3-meal="breakfast"]')).to_contain_text("₹90")
        page.get_by_role("button", name="One more Ven Pongal").first.click()
        expect(page.locator('[data-v3-meal="breakfast"] .v3-qty b')).to_have_text("2×")
        # what's left is net of Swiggy fees, and says it is an estimate until a real bill
        expect(page.locator(".v3-budget .v3-left")).to_contain_text("left after fees")
        expect(page.locator(".v3-budget .v3-left-tag")).to_have_text("estimate")
        # price compare row appears now that a Swiggy price exists
        expect(page.locator(".v3-compare")).to_contain_text("Swiggy ₹180")
        expect(page.locator(".v3-compare")).to_contain_text("Other apps · coming")
        # the greyed-out dish can't be picked
        option = page.locator("#v3-pick-lunch option", has_text="Chicken Burrito")
        assert option.get_attribute("disabled") is not None

        # budget presets sit around the person's own ₹600 a day (ux #15): ₹450 leaves no room
        # for the ₹650 thali → Decide says so
        page.locator(".v3-budget").get_by_role("button", name="₹450").click()
        expect(today.locator(".v3-budget .v3-big")).to_have_text("₹450")
        page.get_by_role("button", name="Decide for me").click()
        dlg = page.get_by_role("dialog", name="Decide for me")
        expect(dlg.get_by_role("button", name="Decide", exact=True)).to_be_disabled()
        dlg.get_by_role("radio", name="From my favourites only").click()
        dlg.get_by_role("button", name="Decide", exact=True).click()
        expect(page.locator(".toast")).to_contain_text("Couldn't fill lunch")
        page.locator(".v3-budget").get_by_role("button", name="Other…").click()
        page.locator("#v3-budget-in").fill("1000")
        page.get_by_role("button", name="Use this budget").click()
        expect(today.locator(".v3-budget .v3-big")).to_have_text("₹1,000")
        page.get_by_role("button", name="Decide for me").click()
        dlg = page.get_by_role("dialog", name="Decide for me")
        dlg.get_by_role("radio", name="From my favourites only").click()
        dlg.get_by_role("button", name="Decide", exact=True).click()
        expect(page.locator('[data-v3-meal="lunch"]')).to_contain_text("Ziggy picked")
        shot(page, f"02-today-picked-{tag}")
        no_overflow(page)
        contrast_ok(page)

        # usual meals: desktop — the three panels side by side; phone — a drawer under each meal line
        if viewport["width"] >= 1024:
            boxes = [b.bounding_box() for b in page.locator(".v3-pool").all()]
            assert len(boxes) == 3
            assert len({round(b["y"]) for b in boxes}) == 1 and boxes[0]["x"] < boxes[1]["x"] < boxes[2]["x"]
            assert page.locator("[data-v3-usual]").count() == 0
        else:
            expect(page.locator(".v3-pools")).to_have_count(0)
            for meal in ("breakfast", "lunch"):
                drawer = usual(page, viewport, meal)
                expect(page.locator(f'[data-v3-meal="{meal}"] #v3-usual-{meal}')).to_have_count(1)   # under its own line
            expect(usual(page, viewport, "dinner")).to_contain_text("No dishes yet")   # not today, still reachable
            # remove from the drawer: the typed burrito goes, the thali stays
            usual(page, viewport, "lunch").get_by_role("button", name="Remove Chicken Burrito from your usual lunches").click()
            expect(page.locator(".toast")).to_contain_text("Removed from your usual lunches")
            expect(usual(page, viewport, "lunch")).not_to_contain_text("Chicken Burrito")
            expect(usual(page, viewport, "lunch")).to_contain_text("Veg Thali")
            shot(page, f"02b-usual-drawers-{tag}")
            no_overflow(page)
            contrast_ok(page)
            page.locator('[data-v3-usual="lunch"]').click()           # close again
            expect(page.locator("#v3-usual-lunch")).to_have_count(0)

        # Send details → the share sheet gets today's text
        page.get_by_role("button", name="Send details").click()
        page.wait_for_function("globalThis.shared.length > 0")
        text = page.evaluate("globalThis.shared[0]")
        assert "Breakfast: 2× Ven Pongal from Annapoorna Tiffin Room · ₹180" in text and "Lunch: 1× Veg Thali" in text

        # Swap: the one way to change a meal; the classic Change sheet is behind it
        page.locator('[data-v3-swap="breakfast"]').click()
        swap = page.get_by_role("dialog", name="Swap breakfast")
        expect(swap.get_by_text("From your usual breakfasts")).to_be_visible()
        expect(swap.get_by_role("button", name="New item / restaurant")).to_be_visible()
        shot(page, f"03-swap-{tag}")
        swap.get_by_role("button", name="Close swap breakfast").click()

        # Place order → Swiggy cart review (Swiggy menu dish only)
        page.get_by_role("button", name="Place order").click()
        order = page.get_by_role("dialog", name="Place order")
        expect(order).to_contain_text("Typed by you")
        order.get_by_role("button", name="Add to Swiggy cart").click()
        review = page.get_by_role("dialog", name="Add this to your cart?")
        expect(review).to_contain_text("Ven Pongal")
        review.get_by_role("button", name="Add to Swiggy cart").click()
        expect(page.locator(".toast")).to_contain_text("Added to your Swiggy cart")
        assert w["fake"].cart_items == [("r-101-i5", 2)]
        cart = page.locator(".v3-cart section", has_text="In your Swiggy cart")
        expect(cart).to_contain_text("Ven Pongal")                      # Swiggy's real bill, on Today

        # Cook tab: type any dish → ingredients with Instamart links
        nav.get_by_role("button", name="Cook").click()
        expect(page.get_by_role("heading", name="Cook", exact=True)).to_be_visible()
        page.get_by_label("Type a dish to cook").fill("pongal")
        page.locator(".dishrow").first.click()
        dish = page.get_by_role("dialog")
        expect(dish.get_by_text("Ingredients · tick what you have")).to_be_visible()
        assert dish.locator('a[href*="swiggy.com/instamart/search"]').count() > 0
        shot(page, f"04-cook-{tag}")
        dish.get_by_role("button", name="Close").first.click()

        # Week: nutrition is opt-in
        nav.get_by_role("button", name="Week").click()
        toggle = page.get_by_label("Show nutrition")
        expect(toggle).not_to_be_checked()
        expect(page.locator(".v3-nut")).to_have_count(0)
        if viewport["width"] >= 1180:
            expect(page.locator(".aside")).not_to_contain_text("Nutrition")
        toggle.check()
        expect(page.get_by_label("Show nutrition")).to_be_checked()
        shot(page, f"05-week-{tag}")
        no_overflow(page)
        # Saved → My meals: no drag-and-drop board; it points to the usual meals on Today
        nav.get_by_role("button", name="Saved").click()
        expect(page.get_by_text("Your usual meals are on Today")).to_be_visible()
        no_old_name(page)
        assert page.locator("[data-drop]").count() == 0
        assert not errors, errors
        browser.close()


@pytest.mark.parametrize("viewport,theme", [(PHONE, "dark"), (DESKTOP, "light")], ids=["phone-375-dark", "desktop-1280-light"])
def test_first_run_two_questions_then_today_and_account_after_value(fresh, viewport, theme):
    tag = f"{viewport['width']}-{theme}"
    with pw.sync_playwright() as p:
        browser = _launch(p)
        page, errors = _page(browser, fresh["url"], viewport, theme)
        page.get_by_role("button", name="Get started").click(timeout=BOOT_MS)
        expect(page.get_by_role("heading", name="What do you eat?")).to_be_visible()
        for k in ("veg", "nonveg", "vegan"):                          # nothing preselected
            assert page.locator(f'[data-v3-ob="diet:{k}"]').get_attribute("aria-checked") == "false"
        expect(page.get_by_role("button", name="Continue")).to_be_disabled()
        shot(page, f"10-onboard-diet-{tag}")
        no_overflow(page)
        page.locator('[data-v3-ob="diet:veg"]').click()
        page.get_by_role("button", name="Peanut").click()
        page.get_by_role("button", name="Continue").click()
        expect(page.get_by_role("heading", name="Your food budget")).to_be_visible()
        expect(page.get_by_role("group", name="How many eat")).to_be_visible()
        expect(page.get_by_role("button", name="Show my day")).to_be_disabled()
        page.get_by_role("button", name="₹600").click()
        page.get_by_role("button", name="Breakfast").click()
        page.get_by_role("button", name="Lunch").click()
        expect(page.get_by_role("button", name="Show my day")).to_be_disabled()       # W8: notice not agreed yet
        page.locator("#v3-ob-consent").check()
        shot(page, f"11-onboard-budget-{tag}")
        no_overflow(page)
        contrast_ok(page)
        page.get_by_role("button", name="Show my day").click()
        today = page.locator(".v3-today")
        expect(today.locator(".v3-budget .v3-big")).to_have_text("₹600", timeout=BOOT_MS)   # the profile plans first
        expect(today.get_by_role("heading", name="Get set up")).to_be_visible()
        expect(today.get_by_text("Swiggy sign-in is opening soon")).to_be_visible()
        no_old_name(page)
        expect(today.get_by_label("Your rules")).to_contain_text("No peanut")
        expect(page.locator(".v3-save")).to_have_count(0)              # no account before value
        page.wait_for_load_state("networkidle")
        shot(page, f"12-first-today-{tag}")
        no_overflow(page)
        contrast_ok(page)
        # first value: a typed favourite, picked → then the account is offered
        page.get_by_role("button", name="Add your favourite dishes").click()
        sheet = page.get_by_role("dialog", name="New item / restaurant")
        sheet.get_by_role("button", name="Breakfast", exact=True).click()
        sheet.get_by_role("textbox", name="Dish").fill("Cappuccino")
        sheet.get_by_label("Place (optional)").fill("Corner Cafe")
        sheet.get_by_label("Price you pay (₹)").fill("140")
        sheet.locator("label.chip", has_text="Yes").click()
        shot(page, f"13-add-typed-{tag}")
        sheet.get_by_role("button", name="Add to your usual breakfasts").click()
        page.get_by_label("Breakfast from your usual meals").select_option(label="Cappuccino · ₹140 · Corner Cafe")
        expect(page.locator(".v3-save")).to_be_visible()
        page.get_by_label("Sign-in name").fill("priya-v3")
        page.get_by_label("Password").fill("a-long-password")
        page.locator(".v3-save").get_by_role("button", name="Save").click()
        expect(page.locator(".toast")).to_contain_text("Saved")
        expect(page.locator(".v3-save")).to_have_count(0)
        shot(page, f"14-after-value-{tag}")
        assert not errors, errors
        browser.close()


def test_classic_today_is_kept_behind_a_flag(town):
    with pw.sync_playwright() as p:
        browser = _launch(p)
        page, errors = _page(browser, town["url"], PHONE, profile=town, query="/?ui=classic")
        nav = page.get_by_role("navigation", name="Main")
        expect(nav.get_by_role("button", name="Saved")).to_be_visible(timeout=BOOT_MS)
        expect(nav.get_by_role("button", name="Cook")).to_have_count(0)
        expect(page.locator(".v3-today")).to_have_count(0)
        page.goto(town["url"] + "/?ui=v3")
        expect(page.locator(".v3-today")).to_be_visible(timeout=BOOT_MS)
        assert not errors, errors
        browser.close()
