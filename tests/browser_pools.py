"""Meal pools and picking any dish nearby, in real Chromium at 390 px and 1280 px.

Production settings (no fixture data). A vegetarian connects Swiggy (a stand-in
neighbourhood of six restaurants, tests/swiggy_town.py), opens Saved → My meals, taps a
dish into breakfast, drags another onto lunch (the pinned drop bar on a phone, the column
on a computer), and Today plans breakfast from the pool. Then "Any place near you" on a
meal opens every place Swiggy lists, a cuisine filter, a full menu, and "Have for
dinner". No console errors, no horizontal scroll, readable contrast.
Run explicitly: `pytest tests/browser_pools.py` (CI does).
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


@pytest.fixture
def town_world(frozen, monkeypatch):
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
    uid = client.post("/api/profiles", json={
        "name": "Priya", "diet": "veg", "weekly_budget": 3500, "cook": "never",
        "rhythm": {"breakfast": "order", "lunch": "order", "dinner": "order"},
        "login": "priya@example.com", "password": "priya-long-password"}).get_json()["user"]["id"]
    _connect(client, fake, uid=uid)
    assert client.post(f"/api/user/{uid}/swiggy/address", json={"address_id": "addr-home"}).status_code == 200
    key = client.environ_base["HTTP_X_SMARTPLATE_KEY"]
    srv = make_server("127.0.0.1", 0, app, threaded=True)
    thread = Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield {"url": f"http://127.0.0.1:{srv.server_port}", "uid": uid, "key": key, "fake": fake}
    srv.shutdown()
    thread.join(timeout=5)
    srv.server_close()
    if os.path.exists(path):
        os.unlink(path)


def _drag(page, grip, target):
    """Hold a dish by its grip and move it onto a drop target (mouse pointer events)."""
    g = grip.bounding_box()
    page.mouse.move(g["x"] + 10, g["y"] + 20)
    page.mouse.down()
    page.mouse.move(g["x"] + 40, g["y"], steps=4)
    t = page.locator(target).first.bounding_box()
    page.mouse.move(t["x"] + 30, t["y"] + 30, steps=10)
    page.mouse.up()


@pytest.mark.parametrize("viewport", [{"width": 390, "height": 844}, {"width": 1280, "height": 900}],
                         ids=["phone-390", "desktop-1280"])
def test_meal_pools_drive_the_plan_and_any_nearby_dish_can_be_picked(town_world, viewport):
    w = town_world
    with pw.sync_playwright() as playwright:
        browser = _launch(playwright)
        context = browser.new_context(viewport=viewport, timezone_id="Asia/Kolkata", locale="en-IN")
        page = context.new_page()
        page.clock.set_fixed_time(FROZEN_JS)
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        bag = {str(w["uid"]): {"key": w["key"], "name": "Priya"}}
        page.add_init_script(f"""localStorage.setItem('smartplate.keys', {json.dumps(json.dumps(bag))});
            localStorage.setItem('smartplate.user', {json.dumps(str(w['uid']))});""")
        try:
            page.goto(w["url"])
            hero = page.locator('section[aria-label="Next meal"]')
            expect(hero).to_be_visible()
            expect(page.get_by_role("heading", name="Tell Ziggy what you eat")).to_be_visible()
            page.get_by_role("button", name="Set up my meals").click()

            # Saved → My meals: the plan's places are the palette, with their real menus
            expect(page.get_by_role("heading", name="What you'd have for each meal")).to_be_visible()
            page.get_by_role("button", name="Annapoorna Tiffin Room").click()
            pongal = page.locator(".pdish", has_text="Ven Pongal")
            pongal.get_by_role("button", name="Add to breakfast: Ven Pongal").click()
            expect(page.locator('section[data-drop="breakfast"]')).to_contain_text("Ven Pongal")
            expect(pongal.get_by_role("button", name="In breakfast: Ven Pongal")).to_have_attribute("aria-pressed", "true")
            meals = page.locator(".pdish", has_text="South Indian Meals")
            meals.scroll_into_view_if_needed()
            _drag(page, meals.locator(".grip"), ".dropbar [data-drop=lunch]")       # the bar pinned to the bottom
            expect(page.locator('section[data-drop="lunch"]')).to_contain_text("South Indian Meals")
            # and a pooled dish dragged from one meal's column to another's
            page.locator('section[data-drop="lunch"]').scroll_into_view_if_needed()
            _drag(page, page.locator('section[data-drop="lunch"] .pchip .grip').first, "section[data-drop=dinner]"
                  if viewport["width"] >= 700 else ".dropbar [data-drop=dinner]")
            expect(page.locator('section[data-drop="dinner"]')).to_contain_text("South Indian Meals")
            page.locator('section[data-drop="dinner"]').get_by_role("button", name="Move South Indian Meals to lunch").click()
            expect(page.locator('section[data-drop="lunch"]')).to_contain_text("South Indian Meals")
            assert page.locator(".dropbar").count() == 0 and page.locator(".drag-ghost").count() == 0
            no_overflow(page)
            assert page.evaluate(CONTRAST_JS) == []

            # Today: breakfast comes from the pool
            page.get_by_role("navigation", name="Main").get_by_role("button", name="Today").click()
            hero = page.locator('section[aria-label="Next meal"]')
            expect(hero).to_contain_text("Ven Pongal")
            expect(hero).to_contain_text("From your breakfast pool")
            expect(page.get_by_role("heading", name="Tell Ziggy what you eat")).to_have_count(0)

            # Change dinner → any place Swiggy lists → filter → full menu → Have for dinner
            page.get_by_role("navigation", name="Main").get_by_role("button", name="Week").click()
            dinner = page.locator(".mrow", has_text="Dinner").first
            dinner.click()
            sheet = page.get_by_role("dialog")
            sheet.get_by_role("button", name="Any place near you").click()
            expect(page.get_by_text("Choosing Mon dinner.", exact=False)).to_be_visible()
            expect(page.get_by_text("Places Swiggy lists near Home", exact=False)).to_be_visible()
            page.get_by_role("button", name="North Indian").click()
            expect(page.locator(".mrow", has_text="Biryani Junction")).to_have_count(0)
            page.locator(".mrow", has_text="Punjab Da Dhaba").get_by_role("button", name="Menu").click()
            menu = page.get_by_role("dialog")
            menu.locator(".lmrow", has_text="Dal Makhani").get_by_role("button", name="Have for dinner").click()
            expect(page.get_by_text("Dal Makhani from Punjab Da Dhaba is your Mon dinner", exact=False)).to_be_visible()
            page.get_by_role("navigation", name="Main").get_by_role("button", name="Week").click()
            expect(page.locator(".mrow", has_text="Dinner").first).to_contain_text("Dal Makhani")
            assert "place_food_order" not in w["fake"].tool_calls()
            no_overflow(page)
            assert not errors, errors
        except Exception:
            Path("test-artifacts").mkdir(exist_ok=True)
            page.screenshot(path=f"test-artifacts/pools-{viewport['width']}.png", full_page=True)
            raise
        finally:
            browser.close()
