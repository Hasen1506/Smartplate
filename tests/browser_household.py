"""Real Chromium walkthrough of roadmap E: household, split by consumption, grocery list, recap.

A private profile sets up a household, adds Dev (vegetarian, peanut allergy, eats dinner
only), switches to "split by who eats each meal", ticks Dev in for one lunch, then checks
the grocery list for a shared dinner cook and the weekly recap. Desktop and phone, no
console errors, no CSP violations, no horizontal overflow.
Run explicitly: `pytest tests/browser_household.py` (CI does, after installing Chromium).
"""
import json
import os
from pathlib import Path
from threading import Thread

import pytest
from werkzeug.serving import make_server

from smartplate.app import create_app
from smartplate.domain import models

pw = pytest.importorskip("playwright.sync_api")


@pytest.fixture
def home(gt):
    app = create_app()
    client = app.test_client()
    created = client.post("/api/profiles", json={"name": "Kavya", "diet": "nonveg", "weekly_budget": 3500,
                                                 "meals": ["lunch", "dinner"], "cook": "never"}).get_json()
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield {"url": f"http://127.0.0.1:{server.server_port}", "uid": created["user"]["id"], "key": created["access_key"],
           "client": client, "h": {"X-SmartPlate-Key": created["access_key"]}, "plan": created["plan"]["id"]}
    server.shutdown()
    thread.join(timeout=5)
    server.server_close()


def _launch(playwright):
    exe = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE")
    return playwright.chromium.launch(executable_path=exe) if exe else playwright.chromium.launch()


def _no_overflow(page):
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), "Page overflows the viewport"


@pytest.mark.parametrize("viewport", [{"width": 1280, "height": 900}, {"width": 390, "height": 844}], ids=["desktop", "phone"])
def test_browser_household_split_grocery_and_recap(home, viewport):
    k = home
    with pw.sync_playwright() as playwright:
        browser = _launch(playwright)
        context = browser.new_context(viewport=viewport, timezone_id="Asia/Kolkata", locale="en-IN")
        page = context.new_page()
        page.route("https://fonts.googleapis.com/**", lambda route: route.fulfill(status=200, content_type="text/css", body=""))
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        bag = {str(k["uid"]): {"key": k["key"], "name": "Kavya"}}
        page.add_init_script(f"""
            if (!localStorage.getItem('smartplate.keys')) {{
              localStorage.setItem('smartplate.keys', {json.dumps(json.dumps(bag))});
              localStorage.setItem('smartplate.user', {json.dumps(str(k['uid']))});
            }}
            globalThis.policyViolations = [];
            document.addEventListener('securitypolicyviolation', e => policyViolations.push(e.violatedDirective));
        """)
        try:
            page.goto(k["url"])
            pw.expect(page.get_by_role("navigation", name="Main")).to_be_visible()
            # --- set up the household and add Dev ------------------------------- #
            page.get_by_role("navigation", name="Main").get_by_role("button", name="You", exact=True).click()
            page.get_by_role("button", name="Household People you cook and order for").click()
            page.get_by_role("button", name="Create household", exact=True).click()
            pw.expect(page.get_by_text("Household created. Add the people you cook for.")).to_be_visible()
            page.get_by_text("Add someone you cook or order for").click()
            form = page.locator("#hh-add")
            form.get_by_label("Name").fill("Dev")
            form.get_by_label("Diet").select_option("veg")
            form.get_by_label("Peanut").check()
            form.get_by_label("Lunch").uncheck()
            _no_overflow(page)
            form.get_by_role("button", name="Add to household").click()
            pw.expect(page.get_by_text("Dev added. Upcoming meals re-planned for everyone's rules.")).to_be_visible()
            pw.expect(page.get_by_text("No peanut · Dev")).to_be_visible()
            pw.expect(page.get_by_text("Vegetarian · Dev")).to_be_visible()
            view = k["client"].get(f"/api/plan/{k['plan']}", headers=k["h"]).get_json()
            menu = {it["id"]: it for it in models.menu_for_city("Chennai")}
            for d in view["grid"]:
                for cell in d["meals"].values():
                    if cell["status"] == "active" and cell["kind"] == "delivery":
                        it = menu[cell["item_id"]]
                        assert it["veg"] and "peanut" not in it["allergens"] and "egg" not in it["allergens"], it["name"]
            # --- split by who eats each meal ----------------------------------- #
            page.locator("#hh-split").select_option("by_consumption")
            pw.expect(page.get_by_text("Split updated.")).to_be_visible()
            pw.expect(page.get_by_text("Each meal's cost is divided among the people eating it", exact=False)).to_be_visible()
            split = {x["member"]: x["share"] for x in
                     k["client"].get(f"/api/plan/{k['plan']}", headers=k["h"]).get_json()["household"]["split"]}
            assert split["Dev"] < split["Kavya"]
            # --- tick Dev in for one lunch ------------------------------------- #
            view = k["client"].get(f"/api/plan/{k['plan']}", headers=k["h"]).get_json()
            lunch = next(cl for d in view["grid"] for m, cl in d["meals"].items()
                         if m == "lunch" and cl["status"] == "active" and cl["kind"] == "delivery")
            page.evaluate(f"openSheet({lunch['session_id']})")
            sheet = page.get_by_role("dialog")
            dev_chip = sheet.get_by_role("button", name="Dev", exact=True)
            pw.expect(dev_chip).to_have_attribute("aria-pressed", "false")
            dev_chip.click()
            pw.expect(sheet.get_by_role("button", name="Dev", exact=True)).to_have_attribute("aria-pressed", "true")
            after = {x["member"]: x["share"] for x in
                     k["client"].get(f"/api/plan/{k['plan']}", headers=k["h"]).get_json()["household"]["split"]}
            assert after["Dev"] > split["Dev"]
            sheet.get_by_role("button", name="Close").click()
            # --- a shared dinner cook: the grocery list scales to two ------------- #
            dinner = next(cl for d in view["grid"] for m, cl in d["meals"].items() if m == "dinner" and cl["status"] == "active")
            k["client"].post(f"/api/session/{dinner['session_id']}/choose", json={"recipe_key": "dal_rice"}, headers=k["h"])
            page.reload()
            pw.expect(page.get_by_role("navigation", name="Main")).to_be_visible()
            page.get_by_role("navigation", name="Main").get_by_role("button", name="Plan", exact=True).click()
            page.get_by_role("navigation", name="Plan").get_by_role("button", name="Cook & groceries").click()
            pw.expect(page.get_by_text("needs 120 g for 2 servings").first).to_be_visible()
            page.locator('[data-have="Rice 1kg"]').check()
            pw.expect(page.get_by_role("heading", name="Grocery list · ₹130")).to_be_visible()      # 90 dal + 40 onion
            _no_overflow(page)
            # --- the weekly recap ------------------------------------------------ #
            page.get_by_role("navigation", name="Main").get_by_role("button", name="You", exact=True).click()
            page.get_by_role("button", name="This week What you spent and ate, against your plan").click()
            pw.expect(page.get_by_role("heading", name="This week")).to_be_visible()
            pw.expect(page.get_by_text("From what actually happened", exact=False)).to_be_visible()
            pw.expect(page.get_by_text("Home · who owes what so far")).to_be_visible()
            _no_overflow(page)
            assert not errors
            assert page.evaluate("policyViolations") == []
        except Exception:
            Path("test-artifacts").mkdir(exist_ok=True)
            page.screenshot(path=f"test-artifacts/household-{viewport['width']}.png", full_page=True)
            raise
        finally:
            browser.close()
