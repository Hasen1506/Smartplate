"""Real Chromium desktop/mobile walkthroughs against an isolated fake provider.
Run explicitly in CI after installing Playwright. Production never imports this.
"""
import json
import time
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


@pytest.fixture
def pilot(seeded, monkeypatch):
    monkeypatch.setattr(config, "LIVE_ORDERS", True)
    monkeypatch.setattr(config, "SWIGGY_PROVIDER", "live")
    fake = FakeLive()
    fake.dishes = {"Mini Tiffin": 12500}
    monkeypatch.setattr(swiggy_connect, "_http", fake)
    app = create_app()
    client = app.test_client()
    created = client.post("/api/profiles", json={"name": "Browser test profile", "diet": "veg",
        "weekly_budget": 2000, "meals": ["dinner"], "favourites": []}).get_json()
    uid = created["user"]["id"]
    _connect(client, fake, uid=uid)
    key = client.environ_base["HTTP_X_SMARTPLATE_KEY"]
    assert client.post(f"/api/user/{uid}/swiggy/address", json={"address_id": "addr-home"}).status_code == 200
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield {"url": f"http://127.0.0.1:{server.server_port}", "uid": uid, "key": key, "fake": fake, "client": client}
    server.shutdown()
    thread.join(timeout=5)
    server.server_close()


def open_profile(browser, pilot, viewport):
    page = browser.new_page(viewport=viewport, accept_downloads=True)
    page.route("https://fonts.googleapis.com/**", lambda route: route.fulfill(status=200, content_type="text/css", body=""))
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    bag = {str(pilot["uid"]): {"key": pilot["key"], "name": "Browser test profile"}}
    page.add_init_script(f"""
        if (!localStorage.getItem('smartplate.keys')) {{
          localStorage.setItem('smartplate.keys', {json.dumps(json.dumps(bag))});
          localStorage.setItem('smartplate.user', {json.dumps(str(pilot['uid']))});
        }}
        globalThis.policyViolations = [];
        document.addEventListener('securitypolicyviolation', e => policyViolations.push(e.violatedDirective));
    """)
    page.goto(pilot["url"])
    pw.expect(page.get_by_role("heading", name="Order from your area")).to_be_visible()
    return page, errors


def no_overflow(page):
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), "Page overflows the viewport"


@pytest.mark.parametrize("viewport", [{"width": 1280, "height": 900}, {"width": 390, "height": 844}], ids=["desktop", "phone"])
def test_browser_live_menu_cart_review_reload_order_and_tracking(pilot, viewport):
    with pw.sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page, errors = open_profile(browser, pilot, viewport)
        try:
            # Force a slow response: Review must wait for search to finish rather
            # than silently dropping the click while the action guard is busy.
            def slow_dish_search(route):
                time.sleep(0.25)
                route.continue_()
            page.route("**/swiggy/dishes**", slow_dish_search)
            page.get_by_role("textbox", name="Restaurant or cuisine").fill("Hotel Saravana Bhavan")
            page.get_by_role("button", name="Search Swiggy", exact=True).click()
            page.locator('[data-live-fav="r-1"]').click()
            pw.expect(page.locator('[data-live-fav="r-1"]').first).to_have_attribute("aria-label", "Remove Hotel Saravana Bhavan (Adyar) from favourites")
            page.locator('[data-live-place="r-1"]').first.click()
            page.get_by_role("textbox", name="Search this restaurant").fill("tiffin")
            page.get_by_role("button", name="Find dishes", exact=True).click()
            page.locator('[data-live-item="m0"]').click()
            dialog = page.get_by_role("dialog")
            pw.expect(dialog.get_by_role("heading", name="Add this exact item?")).to_be_visible()
            dialog.get_by_role("button", name="Add to Swiggy cart", exact=True).click()
            pw.expect(page.get_by_text("Current total ₹160.", exact=False)).to_be_visible()
            page.reload()
            pw.expect(page.get_by_role("button", name="Review and place order", exact=True)).to_be_visible()
            no_overflow(page)
            page.get_by_role("button", name="Review and place order", exact=True).click()
            dialog = page.get_by_role("dialog")
            pw.expect(dialog).to_contain_text("12 Lake View Rd, Adyar")
            pw.expect(dialog).to_contain_text("Hotel Saravana Bhavan (Adyar)")
            dialog.get_by_role("button", name="Confirm and place order · ₹160", exact=True).click()
            pw.expect(page.get_by_text("Swiggy order confirmed:", exact=True)).to_be_visible()
            assert pilot["fake"].tool_calls().count("place_food_order") == 1
            page.reload()
            page.get_by_role("button", name="Check recent Swiggy orders", exact=True).click()
            page.locator('[data-live-track="real-order-17"]').click()
            pw.expect(page.get_by_role("heading", name="Food is being prepared")).to_be_visible()
            pw.expect(page.get_by_text("Arrives in 25 minutes", exact=True)).to_be_visible()
            assert not errors
            assert page.evaluate("policyViolations") == []
            no_overflow(page)
        except Exception:
            Path("test-artifacts").mkdir(exist_ok=True)
            page.screenshot(path=f"test-artifacts/ordering-{viewport['width']}.png", full_page=True)
            raise
        finally:
            browser.close()


@pytest.mark.parametrize("viewport", [{"width": 1280, "height": 900}, {"width": 390, "height": 844}], ids=["desktop", "phone"])
def test_browser_private_export_rotation_and_deletion(pilot, viewport, tmp_path):
    with pw.sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page, errors = open_profile(browser, pilot, viewport)
        try:
            page.get_by_role("button", name="Profile and settings", exact=True).click()
            pw.expect(page.get_by_role("heading", name="Your data", exact=True)).to_be_visible()
            no_overflow(page)
            with page.expect_download() as download_event:
                page.get_by_role("button", name="Download my data", exact=True).click()
            downloaded = tmp_path / "profile.json"
            download_event.value.save_as(downloaded)
            saved = json.loads(downloaded.read_text())
            assert saved["data"]["profile"]["id"] == pilot["uid"]
            assert pilot["key"] not in downloaded.read_text() and pilot["fake"].token not in downloaded.read_text()
            page.get_by_text("Replace a shared or lost code", exact=True).click()
            page.get_by_role("button", name="Generate a new recovery code", exact=True).click()
            pw.expect(page.get_by_text("New recovery code saved here. Copy it somewhere safe.", exact=True)).to_be_visible()
            fresh = page.evaluate(f"JSON.parse(localStorage.getItem('smartplate.keys'))[{json.dumps(str(pilot['uid']))}].key")
            assert fresh != pilot["key"]
            assert pilot["client"].get(f"/api/user/{pilot['uid']}/plan").status_code == 401
            page.get_by_text("Delete my SmartPlate profile", exact=True).click()
            page.locator("#delete-confirmation").fill("DELETE")
            page.get_by_role("button", name="Permanently delete this profile", exact=True).click()
            pw.expect(page.get_by_role("button", name="Create my private profile", exact=True)).to_be_visible()
            assert pilot["client"].get(f"/api/user/{pilot['uid']}/plan").status_code == 404
            assert "place_food_order" not in pilot["fake"].tool_calls()
            assert not errors
            assert page.evaluate("policyViolations") == []
            no_overflow(page)
        except Exception:
            Path("test-artifacts").mkdir(exist_ok=True)
            page.screenshot(path=f"test-artifacts/privacy-{viewport['width']}.png", full_page=True)
            raise
        finally:
            browser.close()
