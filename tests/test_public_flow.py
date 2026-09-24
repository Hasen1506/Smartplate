"""Public account isolation and the read-only provider-to-planner path."""
from smartplate import config, db, service
from smartplate.app import create_app
from smartplate.domain import allergens, models
from smartplate.integrations import live_catalog, swiggy_oauth


class Reply:
    ok = True

    def __init__(self, body):
        self.body = body

    def json(self):
        return self.body


def test_invited_account_cannot_read_a_different_plan(seeded, monkeypatch):
    monkeypatch.setattr(config, 'SESSION_SECRET', 'test-signing-secret')
    app = create_app()
    app.config['TESTING'] = True
    monkeypatch.setattr(config, 'APP_MODE', 'production')
    monkeypatch.setattr(config, 'INVITED_EMAILS', {'new@example.com'})
    monkeypatch.setattr(config, 'SUPABASE_URL', 'https://auth.example')
    monkeypatch.setattr(config, 'SUPABASE_PUBLISHABLE_KEY', 'publishable')
    monkeypatch.setattr('smartplate.auth.requests.post', lambda *a, **k:
                        Reply({'access_token': 'verified', 'user': {
                            'id': 'supabase-user-1', 'email': 'new@example.com'}}))
    client = app.test_client()
    assert client.get('/api/plan/1').status_code == 401
    assert client.post('/api/auth/request-code', json={'email': 'stranger@example.com'}).status_code == 400
    assert client.post('/api/auth/request-code', json={'email': 'new@example.com'}).status_code == 200
    signed_in = client.post('/api/auth/verify-code', json={'email': 'new@example.com', 'code': '123456'})
    assert signed_in.status_code == 200
    own_id = signed_in.get_json()['id']
    assert own_id != 1
    assert [u['id'] for u in client.get('/api/users').get_json()] == [own_id]
    assert client.get('/api/plan/1').status_code == 404
    assert client.get('/api/user/1/plan').status_code == 404
    assert client.post('/api/plan', json={'user_id': 1}).status_code == 404
    assert client.post('/api/auth/logout', json={}).status_code == 200
    assert client.get('/api/users').status_code == 401


def test_swiggy_selected_menu_is_the_only_public_restaurant_source(seeded, monkeypatch):
    monkeypatch.setattr(config, 'SESSION_SECRET', 'test-signing-secret')
    monkeypatch.setattr(config, 'APP_MODE', 'production')
    user_id = 3
    ciphertext = swiggy_oauth._cipher().encrypt(b'user-access-token').decode()
    with db.cursor() as cur:
        cur.execute("INSERT INTO swiggy_connections(user_id,token_ciphertext,expires_ts) "
                    "VALUES (?,?,?)", (user_id, ciphertext, '2099-01-01T00:00:00+00:00'))

    def provider(_user_id, name, arguments):
        assert _user_id == user_id
        if name == 'get_addresses':
            return {'addresses': [{'id': 'address-7', 'addressLine': 'Near Station',
                                   'addressCategory': 'Home'}], 'pagination': {'hasMore': False}}
        assert arguments['addressId'] == 'address-7'
        if name == 'search_restaurants':
            return {'restaurants': [
                {'id': 'rest-open', 'name': 'Real Kitchen', 'avgRating': 4.7,
                 'availabilityStatus': 'OPEN'},
                {'id': 'rest-closed', 'name': 'Closed Kitchen', 'avgRating': 4.8,
                 'availabilityStatus': 'CLOSED'}], 'hasMore': False}
        if name == 'get_restaurant_menu':
            assert arguments['restaurantId'] == 'rest-open'
            return {'restaurant': {'id': 'rest-open'}, 'items': [
                {'id': 'dish-1', 'name': 'Provider Paneer', 'price': 210, 'isVeg': True,
                 'inStock': 1},
                {'id': 'dish-2', 'name': 'Out of stock', 'price': 90, 'inStock': 0}]}
        raise AssertionError(name)

    monkeypatch.setattr(live_catalog, '_call', provider)
    assert live_catalog.select_address(user_id, 'address-7')['address_id'] == 'address-7'
    found = live_catalog.search(user_id, 'paneer')
    assert [r['id'] for r in found['restaurants']] == ['rest-open']
    assert [r['id'] for r in live_catalog.browse_menu(user_id, 'rest-open')['items']] == ['dish-1']
    live_catalog.select_restaurant(user_id, 'rest-open', True)
    user = models.get_user(user_id)
    menu = models.menu_for_user(user)
    assert [i['name'] for i in menu] == ['Provider Paneer']
    assert menu[0]['provider_id'] == 'dish-1'
    assert menu[0]['item_rating'] is None  # An absent dish rating is never invented.
    assert allergens.violates({'allergens': ['peanut']}, menu[0])
    assert allergens.violates({'medical': ['hypertension']}, menu[0])
    assert models.menu_for_city(user['city'])  # demo data still exists but is never read in public mode
    plan_id = service.create_plan(user_id, schedule=[{'day': 0, 'meal': 'lunch', 'kind': 'delivery'}])
    assert models.get_plan(plan_id)['address_id'] == 'address-7'
    deliveries = [d for d in models.decisions_for_plan(plan_id) if d['chosen_kind'] == 'delivery']
    assert deliveries and {d['restaurant_name'] for d in deliveries} == {'Real Kitchen'}


def test_schedule_kind_changes_solver_options(seeded):
    session = models.sessions_for_plan(seeded['plan_id'])[0]
    view = service.set_session_kind(session['id'], 'skip')
    assert next(s for s in view['schedule'] if s['id'] == session['id'])['desired_kind'] == 'skip'
    decision = next(d for d in models.decisions_for_plan(seeded['plan_id'])
                    if d['session_id'] == session['id'])
    assert decision['chosen_kind'] == 'skip'


def test_public_api_discovery_to_plan(seeded, monkeypatch):
    monkeypatch.setattr(config, 'SESSION_SECRET', 'test-signing-secret')
    app = create_app()
    app.config['TESTING'] = True
    monkeypatch.setattr(config, 'APP_MODE', 'production')
    monkeypatch.setattr(config, 'INVITED_EMAILS', {'third@example.com'})
    user_id = 3
    with db.cursor() as cur:
        cur.execute('UPDATE users SET supabase_uid=? WHERE id=?', ('uid-three', user_id))
        cur.execute('INSERT INTO swiggy_connections(user_id,token_ciphertext,expires_ts) '
                    'VALUES (?,?,?)', (user_id,
                    swiggy_oauth._cipher().encrypt(b'test-access').decode(),
                    '2099-01-01T00:00:00+00:00'))

    def provider(_user_id, name, arguments):
        assert _user_id == user_id
        if name == 'get_addresses':
            return {'addresses': [{'id': 'addr-real', 'addressLine': 'Saved address'}],
                    'pagination': {'hasMore': False}}
        assert arguments['addressId'] == 'addr-real'
        if name == 'search_restaurants':
            return {'restaurants': [{'id': 'restaurant-real', 'name': 'Provider Kitchen',
                                     'avgRating': 4.8, 'availabilityStatus': 'OPEN'}]}
        if name == 'get_restaurant_menu':
            return {'restaurant': {'id': 'restaurant-real', 'isOpen': True},
                    'items': [{'id': 'item-real', 'name': 'Provider Lunch',
                               'price': 160, 'isVeg': True, 'inStock': 1}]}
        raise AssertionError(name)

    monkeypatch.setattr(live_catalog, '_call', provider)
    client = app.test_client()
    with client.session_transaction() as sess:
        sess['uid'], sess['user_id'], sess['email'] = 'uid-three', user_id, 'third@example.com'
    assert client.get('/api/swiggy/status').get_json()['connected'] is True
    assert client.get('/api/swiggy/addresses').get_json()['addresses'][0]['id'] == 'addr-real'
    assert client.post('/api/swiggy/address', json={'address_id': 'addr-real'}).status_code == 200
    assert client.get('/api/swiggy/restaurants?query=lunch').get_json()['restaurants'][0]['id'] == 'restaurant-real'
    assert client.get('/api/swiggy/restaurant/restaurant-real/menu').status_code == 200
    assert client.post('/api/swiggy/restaurant/restaurant-real/select', json={'selected': True}).status_code == 200
    assert client.get(f'/api/user/{user_id}/plan').get_json() is None
    assert client.post('/api/plan', json={'user_id': user_id}).status_code == 400
    setup = client.patch(f'/api/user/{user_id}', json={'diet':'veg', 'weekly_budget':500,
                         'allergens':['peanut']})
    assert setup.status_code == 200 and setup.get_json()['diet'] == 'veg'
    response = client.post('/api/plan', json={'user_id': user_id,
                           'schedule':[{'day': 0, 'meal':'lunch', 'kind':'delivery'}]})
    assert response.status_code == 200
    view = response.get_json()
    assert view['data_source'] == 'swiggy'
    assert view['plan']['address_id'] == 'addr-real'
    assert len(view['schedule']) == 21
    assert sum(s['desired_kind'] != 'skip' for s in view['schedule']) == 1
    assert view['carbon'] is None and view['surge_saved'] is None
    assert client.post(f"/api/plan/{view['plan']['id']}/execute", json={}).status_code == 503
    assert client.post(f"/api/plan/{view['plan']['id']}/receipts", json={}).status_code == 503
    assert client.get('/api/community').status_code == 404
    assert client.post(f"/api/plan/{view['plan']['id']}/save-template", json={'title':'Private'}).status_code == 404
