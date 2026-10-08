"""Golden browser journey (Playwright, real Chromium): a new person's whole first week.

Onboarding (vegetarian, peanut allergy, ₹2,500, three meals) → week plan → pin a
dish from the options sheet → move a meal to another day → Swiggy restaurant search →
menu → dish review (recorded Swiggy replies; the allergy blocks the cart) → the
approval dialog for the week's simulated orders → after-meal rating → expenses and
insights → settings change and re-plan.

Deterministic: the server clock and the browser clock are both frozen at Mon 2 Nov
2026 08:00 IST, the network is blocked (fonts are fulfilled locally), Swiggy is a
replay, and orders use the simulated provider only after the approval click.
Run explicitly: `pytest tests/browser_journey.py` (CI does, after installing Chromium).
Set PLAYWRIGHT_CHROMIUM_EXECUTABLE to use a system Chromium.
"""
import json
import os
import re
from pathlib import Path
from threading import Thread

import pytest
from werkzeug.serving import make_server

from gt_support import assert_golden, check_plan, connect_swiggy, grid_snapshot
from smartplate.app import create_app

pw = pytest.importorskip("playwright.sync_api")
expect = pw.expect
FROZEN_JS = "2026-11-02T08:00:00+05:30"


@pytest.fixture
def server(gt, swiggy_replay):
    app = create_app()
    srv = make_server("127.0.0.1", 0, app, threaded=True)
    thread = Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield {"url": f"http://127.0.0.1:{srv.server_port}", "client": app.test_client(), "fake": swiggy_replay,
           "clock": gt}
    srv.shutdown()
    thread.join(timeout=5)
    srv.server_close()


def _launch(playwright):
    exe = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE")
    return playwright.chromium.launch(executable_path=exe) if exe else playwright.chromium.launch()


def _meal(page, day, meal):
    sid = page.evaluate("([d, m]) => S.view.grid.find(x => x.day === d).meals[m].session_id", [day, meal])
    return page.locator(f'.meal[data-meal="{sid}"]'), sid


def test_browser_golden_first_week(server):
    fake, client = server["fake"], server["client"]
    with pw.sync_playwright() as playwright:
        browser = _launch(playwright)
        context = browser.new_context(viewport={"width": 1280, "height": 900}, timezone_id="Asia/Kolkata",
                                      locale="en-IN")
        page = context.new_page()
        page.clock.set_fixed_time(FROZEN_JS)
        page.route("https://fonts.googleapis.com/**", lambda r: r.fulfill(status=200, content_type="text/css", body=""))
        page.route("https://fonts.gstatic.com/**", lambda r: r.fulfill(status=204, body=""))
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.add_init_script("globalThis.policyViolations = [];"
                             "document.addEventListener('securitypolicyviolation', e => policyViolations.push(e.violatedDirective));")
        transcript = {}
        try:
            # 1. onboarding ------------------------------------------------------------
            page.goto(server["url"])
            page.get_by_role("button", name="Create my private profile").click()
            page.locator('[data-ob="diet"][data-val="veg"]').click()
            page.locator('[data-ob="allergens"][data-val="peanut"]').click()
            expect(page.locator('[data-ob="allergens"][data-val="peanut"]')).to_have_attribute("aria-pressed", "true")
            page.get_by_role("button", name="Continue").click()
            page.locator('[data-ob="rh_breakfast"][data-val="order"]').click()      # rhythm: order all three
            page.get_by_role("button", name="Continue").click()
            page.locator("#ob-budget").fill("2500")
            page.get_by_role("button", name="Continue").click()
            page.locator('[data-ob="goal"][data-val="protein"]').click()
            page.locator("#ob-name").fill("Asha")
            page.get_by_role("button", name="Create profile →").click()
            expect(page.get_by_role("heading", name="Swiggy connection")).to_be_visible()
            uid = page.evaluate("S.userId")
            key = page.evaluate(f"JSON.parse(localStorage.getItem('smartplate.keys'))['{uid}'].key")
            h = {"X-SmartPlate-Key": key}
            pid = page.evaluate("S.planId")

            # 2. the week ---------------------------------------------------------------
            page.locator('[data-tab="week"]').click()
            expect(page.get_by_role("heading", name=re.compile("^Week of 2 Nov"))).to_be_visible()
            expect(page.locator("main")).not_to_contain_text("Peanut")
            api_view = client.get(f"/api/plan/{pid}", headers=h).get_json()
            ui_items = page.locator(".meal .mname").all_inner_texts()
            assert len(ui_items) == 21
            assert [t.split("\n")[0].strip() for t in ui_items][:3] == \
                [api_view["grid"][0]["meals"][m]["item"] for m in ("breakfast", "lunch", "dinner")]
            transcript["week"] = grid_snapshot(api_view)

            # 3. pin a dish from the options sheet ---------------------------------------
            tue_lunch, tue_sid = _meal(page, "Tue", "lunch")
            tue_lunch.click()
            sheet = page.get_by_role("dialog")
            expect(sheet).to_contain_text("not safe for your allergies or diet")
            sheet.locator('[data-pick="14"]').first.click()                         # Veg Meals
            expect(page.get_by_text("Veg Meals it is.", exact=False)).to_be_visible()
            expect(page.locator(f'.meal[data-meal="{tue_sid}"] .pin[title="Your pick"]')).to_be_attached()

            # 4. move Wednesday dinner onto Thursday dinner ---------------------------------
            wed, wed_sid = _meal(page, "Wed", "dinner")
            thu, thu_sid = _meal(page, "Thu", "dinner")
            wed_item = page.evaluate(f"S.view.grid[2].meals.dinner.item")
            thu_item = page.evaluate(f"S.view.grid[3].meals.dinner.item")
            wed.click()
            page.get_by_role("dialog").get_by_role("button", name="Move to another day…").click()
            expect(page.locator(".moving[role=status]")).to_contain_text("Moving a meal")
            page.locator(f'.meal[data-meal="{thu_sid}"]').click()
            expect(page.locator(f'.meal[data-meal="{wed_sid}"] .mname')).to_contain_text(thu_item)
            expect(page.locator(f'.meal[data-meal="{thu_sid}"] .mname')).to_contain_text(wed_item)
            assert not check_plan(pid)

            # 5. Swiggy: restaurant search → menu → dish review (allergy: no cart) -------------
            connect_swiggy(client, fake, uid, key)
            page.reload()
            expect(page.get_by_role("heading", name="Order from your area")).to_be_visible()
            page.get_by_role("textbox", name="Restaurant or cuisine").fill("Hotel Saravana Bhavan")
            page.get_by_role("button", name="Search Swiggy", exact=True).click()
            page.locator('[data-live-place="r-1"]').first.click()
            page.get_by_role("textbox", name="Search this restaurant").fill("tiffin")
            page.get_by_role("button", name="Find dishes", exact=True).click()
            page.locator('[data-live-item="m0"]').click()
            dialog = page.get_by_role("dialog")
            expect(dialog.get_by_role("heading", name="Review this dish")).to_be_visible()
            expect(dialog).to_contain_text("won't add this to your cart")
            expect(dialog.get_by_role("link", name="Open in Swiggy ↗")).to_be_visible()
            assert dialog.get_by_role("button", name="Add to Swiggy cart").count() == 0
            dialog.get_by_role("button", name="Close", exact=True).click()
            assert "update_food_cart" not in fake.tool_calls()

            # 6. approval: the dialog shows the exact total; nothing ordered until confirmed ----
            page.locator('[data-tab="week"]').click()
            page.get_by_role("button", name="Simulate auto-ordering…").click()
            review = page.get_by_role("dialog")
            expect(review.get_by_role("heading", name="Approve before anything is ordered")).to_be_visible()
            preview = client.get(f"/api/plan/{pid}/execute/preview", headers=h).get_json()
            amounts = [float(re.sub(r"[^\d.]", "", t)) for t in review.locator(".checkout-row b").all_inner_texts()]
            assert round(sum(amounts), 2) == preview["total"] and len(amounts) == preview["order_count"]
            review.get_by_role("button", name="Keep editing").click()
            assert client.get(f"/api/plan/{pid}/orders", headers=h).get_json()["attempted"] == 0
            page.get_by_role("button", name="Simulate auto-ordering…").click()
            page.get_by_role("dialog").get_by_role("button", name=re.compile(r"^Simulate \d+ orders")).click()
            expect(page.get_by_role("heading", name="Auto-ordering (simulation)")).to_be_visible()
            orders = client.get(f"/api/plan/{pid}/orders", headers=h).get_json()
            transcript["approval"] = {"order_count": preview["order_count"], "total": preview["total"],
                                      "placed": orders["placed"], "substituted": orders["substituted"]}

            # 7. after-meal check-in: rate Monday breakfast ------------------------------------
            server["clock"].set(server["clock"].at.replace(hour=10))
            page.clock.set_fixed_time("2026-11-02T10:00:00+05:30")
            page.locator('[data-tab="today"]').click()
            # the plan's meal is on Today itself now (no "sample weekly planner" disclosure to open)
            good = page.get_by_role("button", name="👍 Good").first
            rated_sid = int(good.get_attribute("data-rate").split(":")[0])
            with page.expect_response(lambda r: r.url.endswith(f"/api/session/{rated_sid}/rate")) as rated:
                good.click()
            assert rated.value.ok
            cells = [c for d in client.get(f"/api/plan/{pid}", headers=h).get_json()["grid"] for c in d["meals"].values()]
            assert next(c for c in cells if c["session_id"] == rated_sid)["rating_given"] == 1
            transcript["rated"] = next(c["item"] for c in cells if c["session_id"] == rated_sid)

            # 8. expenses and insights ------------------------------------------------------------
            page.get_by_role("button", name="Profile and settings").click()
            page.locator('[data-go="more:"]').click()
            page.locator('[data-go="more:receipts"]').click()
            page.get_by_role("button", name="Update from this week").click()
            receipts = client.get(f"/api/receipts/{uid}", headers=h).get_json()
            total_text = page.locator(".stat .val").first.inner_text()
            assert float(re.sub(r"[^\d.]", "", total_text)) == receipts["total"]
            page.locator('[data-go="more:"]').click()
            page.locator('[data-go="more:insights"]').click()
            expect(page.locator("main")).to_contain_text("protein")
            transcript["expenses"] = {"total": receipts["total"], "rows": len(receipts["rows"])}

            # 9. settings: budget, daily limit and a new allergy → re-plan ---------------------------
            page.locator('[data-go="more:"]').click()
            page.locator('[data-go="more:settings"]').click()
            form = page.locator("#preferences")
            form.locator('input[name="weekly_budget"]').fill("1800")
            form.locator('input[name="daily_cap"]').fill("450")
            form.locator('input[name="allergens"][value="dairy"]').check()
            form.get_by_role("button", name="Save & re-plan").click()
            expect(page.get_by_text("Saved. Upcoming meals re-planned.")).to_be_visible()
            after = client.get(f"/api/plan/{pid}", headers=h).get_json()
            assert after["user"]["allergens"] == ["dairy", "peanut"] and after["budget"]["daily_cap"] == 450
            assert not check_plan(pid)
            transcript["after_settings"] = grid_snapshot(after)

            assert "place_food_order" not in fake.tool_calls() and "update_food_cart" not in fake.tool_calls()
            assert not errors, errors
            assert page.evaluate("policyViolations") == []
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
            assert_golden("browser_journey_asha", transcript)
        except Exception:
            Path("test-artifacts").mkdir(exist_ok=True)
            page.screenshot(path="test-artifacts/browser-journey.png", full_page=True)
            raise
        finally:
            browser.close()
