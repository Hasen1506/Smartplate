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
from live_stub import FakeBasket

pw = pytest.importorskip("playwright.sync_api")
pw.expect.set_options(timeout=15000)


@pytest.fixture
def pilot(seeded, monkeypatch):
    monkeypatch.setattr(config, "LIVE_ORDERS", True)
    monkeypatch.setattr(config, "SWIGGY_PROVIDER", "live")
    fake = FakeBasket()
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
    try:
        pw.expect(page.get_by_role("heading", name="Order from your area")).to_be_visible(timeout=15000)
    except AssertionError:
        print('Browser script errors:', errors)
        raise
    return page, errors


def no_overflow(page):
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), "Page overflows the viewport"


@pytest.mark.parametrize('viewport', [{'width': 1280, 'height': 900}, {'width': 390, 'height': 844}], ids=['desktop', 'phone'])
def test_browser_real_week_and_customized_basket(pilot, viewport):
    pilot['fake'].dishes = {'Mini Tiffin': 12500, 'Veg Meals': 18000}
    with pw.sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page, errors = open_profile(browser, pilot, viewport)
        try:
            page.get_by_role('textbox', name='Restaurant or cuisine').fill('Hotel Saravana Bhavan')
            page.get_by_role('button', name='Search Swiggy', exact=True).click()
            page.locator('[data-live-fav="r-1"]').click()
            page.locator('[data-tab="week"]').click()
            page.get_by_role('button', name='Plan from live favourites', exact=True).click()
            pw.expect(page.locator('[data-live-week-slot]')).to_have_count(7)
            no_overflow(page)
            page.reload()
            page.locator('[data-tab="week"]').click()
            pw.expect(page.locator('[data-live-week-slot]')).to_have_count(7)
            page.locator('[data-live-week-slot="0"]').click()
            dialog = page.get_by_role('dialog')
            dialog.get_by_role('button', name='Save to basket draft', exact=True).click()
            page.locator('[data-item-options="m1"]').click()
            dialog = page.get_by_role('dialog')
            dialog.get_by_label('Quantity', exact=True).fill('2')
            dialog.locator('select').select_option('large')
            dialog.get_by_role('checkbox').check()
            dialog.get_by_role('button', name='Save to basket draft', exact=True).click()
            page.get_by_role('button', name='Review whole basket', exact=True).click()
            dialog = page.get_by_role('dialog')
            pw.expect(dialog).to_contain_text('Large')
            dialog.get_by_role('button', name='Confirm and prepare basket', exact=True).click()
            page.get_by_role('button', name='Review available add-ons', exact=True).click()
            dialog = page.get_by_role('dialog')
            dialog.get_by_label('Curd', exact=True).check()
            dialog.get_by_label('Apply these same add-ons to all portions of each dish.', exact=True).check()
            dialog.get_by_role('button', name='Confirm selected add-ons', exact=True).click()
            page.reload()
            page.get_by_role('button', name='Review and place order', exact=True).click()
            dialog = page.get_by_role('dialog')
            pw.expect(dialog).to_contain_text('Quantity 3')
            pw.expect(dialog).to_contain_text('Curd')
            pw.expect(dialog).to_contain_text('Large')
            no_overflow(page)
            dialog.get_by_role('button', name='Confirm and place order · ₹580', exact=True).click()
            pw.expect(page.get_by_text('Swiggy order confirmed:', exact=True)).to_be_visible()
            assert pilot['fake'].tool_calls().count('place_food_order') == 1
            assert not errors and page.evaluate('policyViolations') == []
        except Exception:
            Path('test-artifacts').mkdir(exist_ok=True)
            page.screenshot(path=f"test-artifacts/basket-{viewport['width']}.png", full_page=True)
            raise
        finally:
            browser.close()


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


@pytest.mark.parametrize('viewport', [{'width':1280,'height':900},{'width':390,'height':844}],ids=['desktop','phone'])
def test_browser_agent_connection_memory_and_human_approval(pilot,viewport):
    from urllib.parse import urlsplit
    client=pilot['client']; uid=pilot['uid']; root=f'/api/user/{uid}/swiggy'
    assert client.post(root+'/favourites',json={'restaurant_id':'r-1','restaurant_name':'Hotel Saravana Bhavan'}).status_code==200
    with pw.sync_playwright() as playwright:
        browser=playwright.chromium.launch()
        page,errors=open_profile(browser,pilot,viewport)
        try:
            page.locator('[data-tab="more"]').click()
            page.locator('[data-go="more:agents"]').click()
            page.get_by_label('Connection name',exact=True).fill('My personal agent')
            page.get_by_label('Plan',exact=True).check()
            page.get_by_label('Memory',exact=True).check()
            page.get_by_label('Prepare cart and request human checkout review',exact=True).check()
            page.get_by_role('button',name='Create scoped connection',exact=True).click()
            token=page.get_by_label('Agent access token',exact=True).input_value()
            assert token and token not in page.url
            headers={'Authorization':'Bearer '+token,'Accept':'application/json, text/event-stream','MCP-Protocol-Version':'2025-11-25'}
            def call(name,args=None):
                response=client.post('/mcp',json={'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':name,'arguments':args or {}}},headers=headers)
                body=response.get_json()['result'];assert not body.get('isError'),body
                return json.loads(body['content'][0]['text'])
            call('plan_meals',{'budget':1500,'meals':['dinner'],'start_date':__import__('smartplate.clock',fromlist=['now']).now().date().isoformat()})
            page.locator('[data-tab="week"]').click()
            page.locator('[data-memory-slot]').first.click()
            page.get_by_label('Preference',exact=True).select_option('like')
            page.get_by_label('Dinner',exact=True).check()
            page.get_by_label('Note',exact=True).fill('My reliable dinner')
            page.get_by_role('button',name='Save preference',exact=True).click()
            pw.expect(page.get_by_text('My reliable dinner',exact=False)).to_be_visible()
            call('revise_plan',{'expected_version':call('get_week_status')['plan']['version']})
            basket={'restaurant_id':'r-1','restaurant_name':'Hotel Saravana Bhavan','items':[{'item_id':'m0','item_name':'Mini Tiffin','quantity':1,'variants':{}}]}
            quote=call('review_basket',basket)
            call('prepare_order',{**basket,'expected_fingerprint':quote['fingerprint']})
            review=call('request_order_review')
            assert 'place_food_order' not in pilot['fake'].tool_calls()
            path=urlsplit(review['approval_url']);page.goto(pilot['url']+path.path+'?'+path.query)
            pw.expect(page.get_by_role('heading',name='Review your agent’s order',exact=True)).to_be_visible()
            pw.expect(page.get_by_text('Pay ₹160',exact=False)).to_be_visible()
            no_overflow(page)
            page.get_by_role('button',name='Confirm and place this order',exact=True).click()
            pw.expect(page.get_by_text('Swiggy order confirmed:',exact=True)).to_be_visible()
            assert call('confirm_order',{'review_id':review['review_id']})['status']=='confirmed'
            assert pilot['fake'].tool_calls().count('place_food_order')==1
            assert not errors and page.evaluate('policyViolations')==[]
        except Exception:
            Path('test-artifacts').mkdir(exist_ok=True)
            page.screenshot(path=f'test-artifacts/agent-{viewport["width"]}.png',full_page=True)
            raise
        finally:
            browser.close()
