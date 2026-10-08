"""Production mode in real Chromium at 375 px and 1280 px (Oct 8, 2026 live cart test).

No fixture data (config.FIXTURE_DATA off, as on Render): a fresh profile with no Swiggy
sees an honest empty state, never a sample dish. Connected with an address, Today shows a
skeleton, reads real dishes from Swiggy by itself and picks one; Review → Add is two taps
to the cart, which shows Swiggy's bill to the paisa with Swiggy's labels and the real
checkout link. A fake Swiggy MCP server stands in for Swiggy; nothing real is touched.
"""
import json
import os
import tempfile
import time
from pathlib import Path
from threading import Thread

import pytest
from werkzeug.serving import make_server

from browser_redesign import CONTRAST_JS, FROZEN_JS, MenuFake, _launch, no_overflow, primaries
from smartplate import config, ratelimit
from smartplate.app import create_app
from smartplate.integrations import swiggy_connect
from test_followups import _connect

pw = pytest.importorskip("playwright.sync_api")
expect = pw.expect


class BillFake(MenuFake):
    """Swiggy's documented cart pricing: taxes_and_charges in paise precision, to_pay rounded."""

    def tool(self, name, args):
        reply = super().tool(name, args)
        data = reply.get("structuredContent", {}).get("data") if isinstance(reply, dict) else None
        if name == "get_food_cart" and data and isinstance(data.get("data"), dict) and data["data"].get("items"):
            pricing = data["data"]["pricing"]
            item_total = pricing["item_total"]
            pricing.update({"delivery_charge": 6, "taxes_and_charges": 21.58,
                            "to_pay": round(item_total + 6 + 21.58)})
        return reply


@pytest.fixture
def prod_world(frozen, monkeypatch):
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
    fake = BillFake()
    fake.dishes = {"Ghee Roast Dosa": 14000, "Masala Dosa": 12000, "Paneer Butter Masala": 22000,
                   "Veg Meals": 18000}
    monkeypatch.setattr(swiggy_connect, "_http", fake)
    app = create_app()
    client = app.test_client()
    created = client.post("/api/profiles", json={
        "name": "Arun", "diet": "veg", "weekly_budget": 3000, "cook": "never",
        "rhythm": {"breakfast": "skip", "lunch": "order", "dinner": "order"},
        "login": "arun@example.com", "password": "arun-long-password"}).get_json()
    uid = created["user"]["id"]
    key = created["access_key"]
    client.environ_base["HTTP_X_SMARTPLATE_KEY"] = key
    srv = make_server("127.0.0.1", 0, app, threaded=True)
    thread = Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield {"url": f"http://127.0.0.1:{srv.server_port}", "uid": uid, "key": key, "fake": fake, "client": client}
    srv.shutdown()
    thread.join(timeout=5)
    srv.server_close()
    if os.path.exists(path):
        os.unlink(path)


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
        }}""")
    return page, errors


@pytest.mark.parametrize("viewport", [{"width": 375, "height": 812}, {"width": 1280, "height": 900}],
                         ids=["phone-375", "desktop-1280"])
def test_live_only_today_two_taps_to_the_real_bill(prod_world, viewport):
    w = prod_world
    shots = os.environ.get("SMARTPLATE_SHOTS")
    with pw.sync_playwright() as playwright:
        browser = _launch(playwright)
        page, errors = _open(browser, w, viewport)
        shot = (lambda n: page.screenshot(path=f"{shots}/live-{viewport['width']}-{n}.png", full_page=True)) \
            if shots else (lambda n: None)
        try:
            # 1. No Swiggy yet: an honest empty state; nothing sample anywhere
            page.goto(w["url"])
            expect(page.get_by_role("heading", name="Connect Swiggy and pick an address to see real dishes")).to_be_visible()
            assert "sample" not in page.locator("main").inner_text().lower()
            assert page.locator(".meal.delivery, section.hero-card.delivery").count() == 0
            assert page.evaluate(CONTRAST_JS) == []
            no_overflow(page)
            shot("empty")

            # 2. Connected with an address: the app reads real dishes by itself (skeleton first)
            client, uid = w["client"], w["uid"]
            _connect(client, w["fake"], uid=uid)
            assert client.post(f"/api/user/{uid}/swiggy/address", json={"address_id": "addr-home"}).status_code == 200
            client.post(f"/api/user/{uid}/swiggy/favourites",
                        json={"restaurant_id": "r-1", "restaurant_name": "Hotel Saravana Bhavan (Adyar)"})
            # the browser's stored key must match the profile key _connect rotated
            page.evaluate("k => { const b = JSON.parse(localStorage.getItem('smartplate.keys')); "
                          "for (const id in b) b[id].key = k; localStorage.setItem('smartplate.keys', JSON.stringify(b)); }",
                          client.environ_base["HTTP_X_SMARTPLATE_KEY"])

            def slow_sync(route):
                time.sleep(0.4)
                route.continue_()
            page.route("**/live-menus", slow_sync)
            started = time.monotonic()
            page.reload()
            expect(page.get_by_text("Finding real dishes near you", exact=False)).to_be_visible()
            hero = page.locator('section[aria-label="Next meal"]')
            expect(hero).to_contain_text("Hotel Saravana Bhavan (Adyar)")
            interactive_s = time.monotonic() - started
            assert "sample" not in page.locator("main").inner_text().lower()
            expect(hero.locator(".est")).to_have_count(1)                     # the plan price is ≈ est.
            assert primaries(page) == ["Review & add to Swiggy cart"], primaries(page)
            no_overflow(page)
            shot("today")

            # 3. Two taps to the cart: Review, then Add
            hero.get_by_role("button", name="Review & add to Swiggy cart").click()          # tap 1
            dialog = page.get_by_role("dialog")
            expect(dialog.get_by_role("heading", name="Add to your Swiggy cart?")).to_be_visible()
            shot("review")
            dialog.get_by_role("button", name="Add to Swiggy cart", exact=True).click()     # tap 2
            hero = page.locator('section[aria-label="Next meal"]')
            expect(hero).to_contain_text("Added to your Swiggy cart")
            bill = hero.locator(".billcard")
            expect(bill).to_contain_text("GST & Other Charges")
            expect(bill).to_contain_text("₹21.58")                            # exact paise, Swiggy's label
            expect(bill).not_to_contain_text("₹22")
            expect(bill).to_contain_text("Swiggy rounds the total to the rupee")
            expect(hero.get_by_role("link", name="Open Swiggy checkout ↗")).to_have_attribute(
                "href", "https://www.swiggy.com/checkout")
            expect(hero.locator(".cancel-note")).to_contain_text("cancellation policy applies")
            assert "place_food_order" not in w["fake"].tool_calls()
            assert page.evaluate(CONTRAST_JS) == []
            no_overflow(page)
            shot("added")
            assert not errors, errors
            print(f"\nviewport {viewport['width']}: Today interactive with a live pick in {interactive_s:.2f} s "
                  "(incl. a 0.4 s simulated Swiggy delay)")
        except Exception:
            Path("test-artifacts").mkdir(exist_ok=True)
            page.screenshot(path=f"test-artifacts/live-only-{viewport['width']}.png", full_page=True)
            raise
        finally:
            browser.close()
