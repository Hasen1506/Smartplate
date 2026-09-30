"""End-to-end contracts, stale-state failures, isolation and durable delivery."""
import datetime as dt
import json

import pytest

from smartplate import clock, config, db, live_basket, live_core, push, reminder_queue
from smartplate.app import create_app
from smartplate.integrations import swiggy_connect
from test_followups import _connect
from test_push import _browser_subscription, FakeService
from live_stub import FakeBasket


@pytest.fixture
def core(seeded, monkeypatch):
    monkeypatch.setattr(config, 'LIVE_ORDERS', True)
    fake = FakeBasket()
    monkeypatch.setattr(swiggy_connect, '_http', fake)
    client = create_app().test_client()
    _connect(client, fake, uid=3)
    assert client.post('/api/user/3/swiggy/address', json={'address_id': 'addr-home'}).status_code == 200
    return client, fake


ROOT = '/api/user/3/swiggy'


def basket_body(modern=False):
    return {'restaurant_id': 'r-1', 'restaurant_name': 'Hotel Saravana Bhavan (Adyar)', 'items': [
        {'item_id': 'm0', 'item_name': 'Mini Tiffin', 'quantity': 1, 'variants': {}},
        {'item_id': 'm2' if modern else 'm1', 'item_name': 'Modern Meal' if modern else 'Veg Meals',
         'quantity': 2, 'variants': {'size': 'large'}, 'same_options': True}]}


def prepare(client, body=None):
    body = body or basket_body()
    response = client.post(ROOT + '/basket/preview', json=body)
    assert response.status_code == 200, response.get_json()
    review = response.get_json()
    response = client.post(ROOT + '/basket', json={**body, 'expected_fingerprint': review['fingerprint']})
    assert response.status_code == 200, response.get_json()
    return response.get_json()


def test_real_week_persists_and_respects_budget_and_location(core):
    client, fake = core
    assert client.post(ROOT + '/week', json={}).status_code == 400
    assert client.post(ROOT + '/favourites', json={'restaurant_id': 'r-1', 'restaurant_name': 'Hotel Saravana Bhavan'}).status_code == 200
    result = client.post(ROOT + '/week', json={'budget': 1500, 'fee_reserve': 40}).get_json()['plan']
    assert result['source'] == 'swiggy' and result['nutrition'] is None
    assert result['estimated_total'] <= 1500 and result['slots']
    assert all(s['item'] is None or s['item']['id'] in ('m0', 'm1', 'm2') for s in result['slots'])
    assert client.get(ROOT + '/week').get_json()['plan'] == result
    assert 'place_food_order' not in fake.tool_calls()
    stranger = create_app().test_client()
    assert stranger.get(ROOT + '/week').status_code == 401
    assert client.post(ROOT + '/address', json={'address_id': 'addr-work'}).status_code == 200
    assert client.get(ROOT + '/week').get_json()['plan'] is None


@pytest.mark.parametrize('modern', [False, True])
def test_multi_item_variants_addons_checkout_and_one_confirmation(core, modern):
    client, fake = core
    cart = prepare(client, basket_body(modern))
    assert cart['basket'] and cart['orderable'] and len(cart['items']) == 2
    second = fake.cart[1]
    assert ('variantsV2' in second) == modern
    review = client.get(ROOT + '/basket/addons').get_json()
    line_id = 'm2' if modern else 'm1'
    body = {'expected_fingerprint': review['fingerprint'], 'selections': {'m0': {}, line_id: {'extras': ['curd']}}}
    assert client.post(ROOT + '/basket/addons', json=body).status_code == 400
    assert client.post(ROOT + '/basket/addons', json={**body, 'same_options': True}).status_code == 200
    quote = client.get(ROOT + '/checkout/preview').get_json()
    assert quote['quantity'] == 3 and len(quote['items']) == 2
    assert quote['to_pay'] == (520 if modern else 580)
    result = client.post(ROOT + '/checkout', json={'expected_fingerprint': quote['fingerprint']})
    assert result.status_code == 200, result.get_json()
    assert 'place_food_order' in fake.tool_calls() and fake.tool_calls().count('place_food_order') == 1
    assert client.post(ROOT + '/checkout', json={'expected_fingerprint': quote['fingerprint']}).status_code != 200


@pytest.mark.parametrize('change', ['quantity', 'variant', 'foreign', 'stale_price', 'occupied'])
def test_basket_refuses_stale_or_unreviewed_state(core, change):
    client, fake = core
    body = basket_body()
    if change in ('stale_price', 'occupied'):
        quote = client.post(ROOT + '/basket/preview', json=body).get_json()
        if change == 'stale_price':
            fake.dishes['Mini Tiffin'] += 100
        else:
            fake.cart = [{'menu_item_id': 'm0', 'quantity': 1}]
        response = client.post(ROOT + '/basket', json={**body, 'expected_fingerprint': quote['fingerprint']})
        assert response.status_code == (409 if change == 'stale_price' else 502)
    else:
        prepare(client)
        if change == 'quantity':
            fake.cart[0]['quantity'] = 2
        elif change == 'variant':
            fake.cart[1]['variants'][0]['variation_id'] = 'small'
        else:
            fake.cart.append({'menu_item_id': 'm2', 'quantity': 1})
        assert client.get(ROOT + '/checkout/preview').status_code == 502
    assert 'place_food_order' not in fake.tool_calls()


@pytest.mark.parametrize('bad', ['sold-out', 'made-up'])
def test_unavailable_variant_never_changes_cart(core, bad):
    client, fake = core
    body = basket_body()
    body['items'][1]['variants']['size'] = bad
    assert client.post(ROOT + '/basket/preview', json=body).status_code == 400
    assert 'update_food_cart' not in fake.tool_calls()


def test_required_addons_rechecked_before_checkout(core):
    client, fake = core
    prepare(client)
    fake.required_addons = 1
    assert client.get(ROOT + '/checkout/preview').status_code == 502
    review = client.get(ROOT + '/basket/addons').get_json()
    body = {'expected_fingerprint': review['fingerprint'], 'same_options': True,
            'selections': {'m0': {}, 'm1': {'extras': ['curd']}}}
    assert client.post(ROOT + '/basket/addons', json=body).status_code == 200
    quote = client.get(ROOT + '/checkout/preview').get_json()
    fake.addon_stock = False
    assert client.post(ROOT + '/checkout', json={'expected_fingerprint': quote['fingerprint']}).status_code == 502
    assert 'place_food_order' not in fake.tool_calls()


def queued(client, at):
    push.subscribe(3, _browser_subscription())
    with db.cursor() as cur:
        sub = dict(cur.execute('SELECT * FROM push_subscriptions WHERE user_id=3').fetchone())
    reminder = {'session_id': 'live:test:dinner', 'at': at.isoformat(), 'title': 'Review dinner', 'body': 'Real dish', 'link': '/?tab=week'}
    reminder_queue.enqueue([(sub, reminder)], at)
    return sub, reminder


def test_reminder_retry_survives_restart_and_lease_blocks_concurrent_sender(core):
    client, _ = core
    at = clock.now()
    queued(client, at)
    sender = FakeService(503)
    assert reminder_queue.drain(at, sender) == 0
    create_app()  # restart initialization does not erase jobs
    assert reminder_queue.drain(at + dt.timedelta(seconds=30), sender) == 0
    def nested(*args, **kwargs):
        assert reminder_queue.drain(at + dt.timedelta(minutes=1), FakeService()) == 0
        return 201
    assert reminder_queue.drain(at + dt.timedelta(minutes=1), nested) == 1
    assert reminder_queue.drain(at + dt.timedelta(minutes=2), FakeService()) == 0
    with db.cursor() as cur:
        job = cur.execute('SELECT * FROM reminder_jobs').fetchone()
    assert job['state'] == 'sent' and job['attempts'] == 2


def test_old_reminders_expire_and_cancelled_schedule_is_not_delivered(core):
    client, _ = core
    at = clock.now()
    queued(client, at - dt.timedelta(minutes=21))
    sender = FakeService()
    assert reminder_queue.drain(at, sender) == 0 and not sender.calls
    queued(client, at)
    reminder_queue.enqueue([], at, reconcile=True)
    assert reminder_queue.drain(at, sender) == 0 and not sender.calls


def test_operations_auth_redacted_logs_and_sqlite_restart(core, monkeypatch, caplog):
    client, fake = core
    monkeypatch.setenv('SMARTPLATE_OPS_TOKEN', 'test-ops-token')
    assert client.get('/ops/status').status_code == 401
    response = client.get('/ops/status', headers={'Authorization': 'Bearer test-ops-token'})
    assert response.status_code in (200, 503)
    assert 'reminder_jobs' in response.get_json()
    client.get(ROOT + '/week?private=test-secret')
    assert 'test-secret' not in caplog.text and fake.token not in response.get_data(as_text=True)
    assert response.headers['X-Request-ID']
    db.init_db()
    with db.cursor() as cur:
        assert cur.execute('PRAGMA journal_mode').fetchone()[0] == 'wal'
        assert cur.execute('PRAGMA synchronous').fetchone()[0] == 2
        assert cur.execute('SELECT count(*) FROM schema_migrations').fetchone()[0] == len(db.MIGRATIONS)


def test_private_export_and_deletion_cover_new_real_data(core):
    client, _ = core
    prepare(client)
    queued(client, clock.now())
    response = client.get('/api/user/3/data.json')
    assert response.status_code == 200
    data = response.get_json()['data']
    assert data['swiggy_cart_lines'] and data['reminder_jobs']
    assert client.delete('/api/user/3', json={'confirmation': 'DELETE'}).status_code == 200
    with db.cursor() as cur:
        for table in ('swiggy_cart_lines', 'live_weeks', 'reminder_jobs'):
            assert cur.execute(f'SELECT count(*) FROM {table} WHERE user_id=3').fetchone()[0] == 0


def test_retry_budget_is_bounded_and_private_profiles_never_get_sample_reminders(core):
    client, _ = core
    at = clock.now()
    queued(client, at)
    sender = FakeService(503)
    for delay in (0, 60, 180, 420, 900, 1500):
        reminder_queue.drain(at + dt.timedelta(seconds=delay), sender)
    # The late cutoff can expire a job before six attempts; neither failed nor
    # expired jobs are automatically retried.
    assert len(sender.calls) <= 6
    reminder_queue.drain(at + dt.timedelta(hours=1), sender)
    with db.cursor() as cur:
        assert cur.execute('SELECT state FROM reminder_jobs').fetchone()[0] in ('failed', 'expired')
    assert client.get('/api/user/3/reminders').get_json() == []
