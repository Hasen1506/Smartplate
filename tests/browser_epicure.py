"""Real Chromium walkthrough of the Epicure features (roadmap G), on the offline fixture.

A private profile with a dairy allergy and a cook day:
  Cooking & groceries → toor dal is out of stock → pick a replacement → the grocery
  list and the recipe both show it → undo; the "safe with a swap" card;
  a planned restaurant dish → "More like …" → similar picks in the sheet;
  More → Credits & licences → the CC BY 4.0 attribution.
Desktop and phone, no console errors, no CSP violations, no horizontal overflow.
Run explicitly: `pytest tests/browser_epicure.py` (CI does, after installing Chromium).
"""
import json
import os
from pathlib import Path
from threading import Thread

import pytest
from werkzeug.serving import make_server

from smartplate.app import create_app

pw = pytest.importorskip("playwright.sync_api")


@pytest.fixture
def kitchen(seeded):
    app = create_app()
    client = app.test_client()
    created = client.post("/api/profiles", json={"name": "Kavya", "diet": "veg", "allergens": ["dairy"],
                                                 "weekly_budget": 2500, "meals": ["lunch", "dinner"],
                                                 "cook": "sometimes"}).get_json()
    uid, key = created["user"]["id"], created["access_key"]
    h = {"X-SmartPlate-Key": key}
    cells = [c for d in created["grid"] for c in d["meals"].values() if c["status"] == "active"]
    cook_sid = cells[0]["session_id"]
    view = client.post(f"/api/session/{cook_sid}/choose", json={"recipe_key": "dal_rice"}, headers=h).get_json()
    delivery = next(c for d in view["grid"] for c in d["meals"].values()
                    if c["status"] == "active" and c["kind"] == "delivery")
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield {"url": f"http://127.0.0.1:{server.server_port}", "uid": uid, "key": key, "client": client, "h": h,
           "plan": view["plan"]["id"], "delivery": delivery}
    server.shutdown()
    thread.join(timeout=5)
    server.server_close()


def _launch(playwright):
    exe = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE")
    return playwright.chromium.launch(executable_path=exe) if exe else playwright.chromium.launch()


def _open(browser, k, viewport):
    page = browser.new_page(viewport=viewport)
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
    page.goto(k["url"])
    pw.expect(page.get_by_role("navigation", name="Main")).to_be_visible()
    return page, errors


def _no_overflow(page):
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), "Page overflows the viewport"


@pytest.mark.parametrize("viewport", [{"width": 1280, "height": 900}, {"width": 390, "height": 844}], ids=["desktop", "phone"])
def test_browser_out_of_stock_swap_more_like_this_and_credits(kitchen, viewport):
    k = kitchen
    with pw.sync_playwright() as playwright:
        browser = _launch(playwright)
        page, errors = _open(browser, k, viewport)
        try:
            # --- grocery: toor dal is out of stock -------------------------------- #
            page.get_by_role("button", name="More", exact=True).click()
            page.get_by_role("button", name="Cooking & groceries Recipes and one grocery list for cook days").click()
            pw.expect(page.get_by_role("heading", name="Cooking & groceries")).to_be_visible()
            page.locator('[data-oos="toor_dal"]').click()
            picker = page.get_by_role("group", name="Swap Toor dal")
            pw.expect(picker).to_contain_text("Toor dal is out of stock. Use instead:")
            first = picker.locator("[data-swap-to]").first
            replacement = first.inner_text()
            assert replacement in ("Chana dal", "Urad dal", "Masoor dal", "Moong dal")
            _no_overflow(page)
            first.click()
            pw.expect(page.get_by_text("Swap saved. Your recipe and grocery list show it.")).to_be_visible()
            pw.expect(page.locator("table")).to_contain_text(f"Toor dal 500g → {replacement} (out of stock)")
            pw.expect(page.locator(".tag.swapped").first).to_contain_text(f"{replacement} for Toor dal")
            saved = k["client"].get(f"/api/plan/{k['plan']}", headers=k["h"]).get_json()
            line = next(b for b in saved["coach"]["basket"]["items"] if b["token"] == "toor_dal")
            assert line["swap"]["name"] == replacement and line["swap"]["reason"] == "out_of_stock"
            # a dairy-allergic vegetarian: egg curry is offered with safe swaps, never with dairy
            safe = page.locator(".card", has_text="Also safe with a swap")
            pw.expect(safe).to_contain_text("Egg curry + roti")
            assert "Paneer" not in safe.inner_text()
            page.get_by_role("button", name="Undo", exact=True).click()
            pw.expect(page.get_by_text("Back to the original ingredient.")).to_be_visible()
            pw.expect(page.locator('[data-oos="toor_dal"]')).to_be_visible()

            # --- "more like this" on a planned restaurant dish ------------------- #
            dish = k["delivery"]["item"]
            page.evaluate(f"openSheet({k['delivery']['session_id']})")
            sheet = page.get_by_role("dialog")
            more = sheet.get_by_role("button", name=f"More like {dish}", exact=True)
            pw.expect(more).to_be_visible()
            more.click()
            pw.expect(page.get_by_text(f"Got it: more like {dish}. Your open meals were re-planned.")).to_be_visible()
            pw.expect(sheet.get_by_role("heading", name=f"Like {dish}")).to_be_visible()
            prefs = k["client"].get(f"/api/user/{k['uid']}/plan", headers=k["h"]).get_json()["user"]["prefs"]
            assert prefs["more_like"][-1]["name"] == dish
            sheet.get_by_role("button", name="Close").click()

            # --- credits ---------------------------------------------------------- #
            page.get_by_role("button", name="More", exact=True).click()
            back = page.get_by_role("button", name="← More")
            if back.count():
                back.click()
            page.get_by_role("link", name="Credits & licences Data and models SmartPlate uses").click()
            pw.expect(page.get_by_role("heading", name="Epicure ingredient embeddings")).to_be_visible()
            pw.expect(page.get_by_role("link", name="Creative Commons Attribution 4.0 International (CC BY 4.0)")).to_be_visible()
            _no_overflow(page)
            assert not errors
            assert page.evaluate("policyViolations") == []
        except Exception:
            Path("test-artifacts").mkdir(exist_ok=True)
            page.screenshot(path=f"test-artifacts/epicure-{viewport['width']}.png", full_page=True)
            raise
        finally:
            browser.close()
