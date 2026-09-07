"""Regressions found while making the app usable as a private trial."""
import math
from concurrent.futures import ThreadPoolExecutor

import pytest

from smartplate import config, db, service
from smartplate.app import create_app
from smartplate.domain import checkout, models
from smartplate.integrations import swiggy_mcp


@pytest.fixture
def client(seeded):
    app = create_app()
    app.config['TESTING'] = True
    return app.test_client()


def reviewed_execute(client, pid=1):
    review = client.get(f'/api/plan/{pid}/execute/preview').get_json()
    response = client.post(f'/api/plan/{pid}/execute', json={
        'expected_fingerprint': review['fingerprint'], 'max_total': review['total']})
    assert response.status_code == 200
    return review, response.get_json()


def test_factory_seeds_existing_empty_database(monkeypatch, tmp_path):
    path = tmp_path / 'empty.db'
    path.touch()
    monkeypatch.setattr(config, 'DB_PATH', str(path))
    client = create_app().test_client()
    assert len(client.get('/api/users').get_json()) == 3
    assert len(client.get('/api/plan/1').get_json()['grid']) == 7
    with db.cursor() as cur:
        cur.execute("UPDATE users SET name='My saved profile' WHERE id=1")
    create_app()
    assert models.get_user(1)['name'] == 'My saved profile'


def test_latest_plan_resumes_without_duplicates(client):
    first = client.get('/api/user/2/plan').get_json()['plan']['id']
    assert client.get('/api/user/2/plan').get_json()['plan']['id'] == first
    new = client.post('/api/plan', json={'user_id': 2}).get_json()['plan']['id']
    assert new != first
    assert client.get('/api/user/2/plan').get_json()['plan']['id'] == new


def test_preferences_persist_and_replan(client):
    response = client.patch('/api/user/1', json={
        'name': 'Trial', 'weekly_budget': 1000, 'diet': 'vegan',
        'allergens': ['peanut', 'gluten'], 'medical': [], 'max_cook_per_week': 3,
        'rating_floor': 4.2, 'kcal': 1900, 'protein_g': 75})
    assert response.status_code == 200
    view = response.get_json()
    assert view['budget']['spend'] <= 1000
    assert view['nutrition']['daily_target']['protein_g'] == 75
    user = models.get_user(1)
    assert user['medical'] == [] and user['diet'] == 'vegan'
    assert user['health_targets']['max_cook_per_week'] == 3
    assert create_app().test_client().get('/api/user/1/plan').get_json()['user']['name'] == 'Trial'


@pytest.mark.parametrize('bad', [float('nan'), float('inf'), -1, True, '100'])
def test_invalid_preferences_do_not_partially_save(client, bad):
    old = models.get_user(1)['name']
    response = client.patch('/api/user/1', json={'name': 'Wrong', 'weekly_budget': bad})
    assert response.status_code == 400
    assert models.get_user(1)['name'] == old


def test_api_validation_and_live_mode_fail_closed(client, monkeypatch):
    assert client.post('/api/plan', json={'user_id': 9999}).status_code == 400
    assert client.post('/api/plan/1/optimize', json={'mode': 'missing'}).status_code == 400
    assert client.post('/api/plan/9999/optimize', json={}).status_code == 404
    assert client.post('/api/plan/1/execute', json={}).status_code == 409
    assert client.post('/api/plan/1/execute', data='{}', content_type='text/plain').status_code == 400
    monkeypatch.setattr(config, 'SWIGGY_PROVIDER', 'live')
    assert client.post('/api/plan/1/execute', json={}).status_code == 503
    with db.cursor() as cur:
        assert cur.execute('SELECT count(*) FROM orders').fetchone()[0] == 0


@pytest.mark.parametrize('bad', [float('nan'), float('inf'), -1])
def test_checkout_rejects_nonfinite_ceiling(seeded, bad):
    with pytest.raises(checkout.CheckoutConflict):
        checkout.validate(service.execution_preview(1), max_total=bad)


def test_ordered_meals_are_fixed_and_remaining_budget_respected(client):
    review, result = reviewed_execute(client)
    amounts = {item['session_id']: item['amount'] for item in review['items']}
    placed = [r for r in result['results'] if r['placed']]
    assert placed
    assert all(r['cost'] <= amounts[r['session_id']] for r in placed)
    assert client.get('/api/plan/1/execute/preview').get_json()['order_count'] == 0
    before = service.order_history(1)
    client.post('/api/plan/1/optimize', json={})
    assert service.plan_view(1)['budget']['spend'] <= models.get_user(1)['weekly_budget'] + .01
    assert service.order_history(1)['placed'] == before['placed']
    sid = placed[0]['session_id']
    assert client.post(f'/api/session/{sid}/status', json={'status': 'active'}).status_code == 400
    assert any(m['status'] == 'ordered' for d in service.plan_view(1)['grid'] for m in d['meals'].values())


def test_skip_then_checkout_without_replan_does_not_order_skipped_meal(client):
    original = service.execution_preview(1)
    sid = original['items'][0]['session_id']
    assert client.post(f'/api/session/{sid}/status', json={'status':'skipped'}).status_code == 200
    updated = service.execution_preview(1)
    assert sid not in [i['session_id'] for i in updated['items']]
    assert updated['fingerprint'] != original['fingerprint']
    stale = client.post('/api/plan/1/execute', json={'expected_fingerprint':original['fingerprint'], 'max_total':original['total']})
    assert stale.status_code == 409
    _, result = reviewed_execute(client)
    assert sid not in [r['session_id'] for r in result['results']]


def test_receipts_only_completed_orders_and_no_duplicate_totals(client):
    assert client.post('/api/plan/1/receipts', json={}).get_json()['recorded'] == 0
    _, result = reviewed_execute(client)
    before = client.get('/api/receipts/1').get_json()
    assert len(before['rows']) == result['placed']
    client.post('/api/plan/1/receipts', json={})
    client.post('/api/plan/1/receipts', json={})
    assert client.get('/api/receipts/1').get_json() == before


def test_restore_skipped_meal(client):
    sid = models.sessions_for_plan(1)[0]['id']
    client.post(f'/api/session/{sid}/status', json={'status':'skipped'})
    client.post(f'/api/session/{sid}/status', json={'status':'active'})
    client.post('/api/plan/1/optimize', json={})
    assert models.sessions_for_plan(1)[0]['status'] == 'active'


def test_concurrent_duplicate_orders_place_once(seeded):
    class Counting(swiggy_mcp.SimulatedSwiggyProvider):
        calls = 0
        def place(self, cart_id, txn_id):
            self.calls += 1
            return super().place(cart_id, txn_id)
    provider = Counting(flaky_fail_rate=0)
    def attempt(_):
        return swiggy_mcp.place_order({'id':1,'cost':100}, {'id':1,'name':'Diner'},
            {'id':1,'name':'Meal'}, user_id=1, plan_id=1, session_id=1,
            trigger_ts='2026-09-07T12:00:00', provider=provider)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, range(2)))
    assert provider.calls == 1
    assert results[0].provider_order_id == results[1].provider_order_id


def test_payment_confirmation_without_order_id_is_not_success(seeded):
    class Timeout(swiggy_mcp.SimulatedSwiggyProvider):
        calls = 0
        def place(self, cart_id, txn_id):
            self.calls += 1
            raise TimeoutError('unknown provider outcome')
    provider = Timeout(flaky_fail_rate=0)
    kwargs = dict(user_id=1, plan_id=1, session_id=500,
                  trigger_ts='2026-09-07T12:00:00', provider=provider)
    args = ({'id':1,'cost':100}, {'id':1,'name':'Diner'}, {'id':1,'name':'Meal'})
    first = swiggy_mcp.place_order(*args, **kwargs)
    second = swiggy_mcp.place_order(*args, **kwargs)
    assert not first.ok and not second.ok
    assert first.state == second.state == 'unknown'
    assert provider.calls == 1
