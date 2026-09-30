"""Explicit localhost-only contract walkthrough; no real Swiggy account or orders."""
import json
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]


def build_app():
    if os.environ.get('RENDER'):
        raise RuntimeError('The local stub cannot run on Render')
    # Always use a new database and this process's in-memory provider. Never read
    # production credentials or reuse the configured application database.
    directory = tempfile.TemporaryDirectory(prefix='smartplate-local-stub-')
    from smartplate import config
    config.DB_PATH = str(Path(directory.name) / 'stub.db')
    config.SWIGGY_PROVIDER = 'live'
    config.LIVE_ORDERS = True
    config.WEATHER_PROVIDER = 'simulated'
    config.PUSH_ENABLED = False
    config.PUBLIC_URL = 'http://localhost:5000'
    config.BEHIND_PROXY = False
    config.SECRET = ''
    from smartplate.app import create_app
    from smartplate.integrations import swiggy_connect
    from live_stub import FakeBasket
    fake = FakeBasket()
    swiggy_connect._http = fake
    app = create_app()
    app.config['LOCAL_STUB'] = True
    from flask import Response, request

    @app.before_request
    def local_hosts_only():
        if request.host.split(':')[0] not in ('localhost', '127.0.0.1'):
            return Response('Local walkthrough accepts localhost only', status=403)
        if request.path.startswith('/__local') and request.headers.get('Sec-Fetch-Site') == 'cross-site':
            return Response('Open the walkthrough directly on localhost', status=403)

    @app.get('/__local')
    def setup():
        return Response('<!doctype html><title>Local SmartPlate walkthrough</title>'
                        '<script src="/__local/bootstrap.js"></script>', mimetype='text/html')

    @app.get('/__local/bootstrap.js')
    def bootstrap():
        bag = json.dumps({str(uid): {'key': key, 'name': 'Local walkthrough'}})
        body = f"localStorage.setItem('smartplate.keys', {json.dumps(bag)});localStorage.setItem('smartplate.user', {json.dumps(str(uid))});location.replace('/');"
        return Response(body, mimetype='application/javascript', headers={'Cache-Control': 'no-store'})

    client = app.test_client()
    profile = client.post('/api/profiles', json={'name': 'Local walkthrough', 'diet': 'veg',
        'weekly_budget': 2000, 'meals': ['dinner'], 'favourites': []}).get_json()
    uid, key = profile['user']['id'], profile['access_key']
    client.environ_base['HTTP_X_SMARTPLATE_KEY'] = key
    auth = client.post(f'/api/user/{uid}/swiggy/connect', json={}).get_json()
    if 'authorize_url' not in auth:
        raise RuntimeError(auth.get('error', 'Local authentication failed'))
    approved = fake.approve(auth['authorize_url'])
    response = client.get(f"/swiggy/callback?state={approved['state']}&code=code-1")
    if response.status_code != 302:
        raise RuntimeError('Local callback failed')
    if client.post(f'/api/user/{uid}/swiggy/address', json={'address_id': 'addr-home'}).status_code != 200:
        raise RuntimeError('Local address setup failed')
    return app, directory


if __name__ == '__main__':
    app, directory = build_app()
    print('Open http://localhost:5000/__local — example provider data, no real purchases.')
    try:
        app.run(host='127.0.0.1', port=5000, debug=False, use_reloader=False)
    finally:
        directory.cleanup()
