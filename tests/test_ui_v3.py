"""Ziggy v3 behind a switch: /v3 and /?ui=v3 serve the experiment, / stays the classic app,
and a Swiggy sign-in started from v3 comes back to v3 (it used to land on the classic app's
Me tab, so v3 lost its place mid-onboarding)."""
import re

import pytest

from smartplate.app import create_app
from test_followups import APP, _allow_app_host, _secure_profile, swiggy  # noqa: F401  (fixture)


@pytest.fixture
def client(seeded):
    app = create_app()
    app.config["TESTING"] = True
    return app.test_client()


def test_v3_is_served_beside_the_classic_app(client):
    classic = client.get("/").get_data(as_text=True)
    assert re.search(r'<script src="/static/app\.js\?v=[0-9a-f]{12}">', classic) and "/static/v3/" not in classic
    for path in ("/v3", "/?ui=v3"):
        r = client.get(path)
        html = r.get_data(as_text=True)
        assert r.status_code == 200 and r.headers["Cache-Control"] == "no-cache"
        js = re.search(r'src="(/static/v3/app\.js\?v=[0-9a-f]{12})"', html).group(1)
        css = re.search(r'href="(/static/v3/v3\.css\?v=[0-9a-f]{12})"', html).group(1)
        assert '<script>' not in html and "onclick" not in html                 # CSP: script-src 'self'
        assert client.get(js).headers["Cache-Control"].endswith("immutable")
        assert client.get(css).status_code == 200
    assert "onclick=" not in client.get("/static/v3/app.js").get_data(as_text=True)


def _connect(client, swiggy, body):
    _allow_app_host()
    _secure_profile(client)
    url = client.post("/api/user/1/swiggy/connect", json=body, base_url=APP).get_json()["authorize_url"]
    q = swiggy.approve(url)
    return client.get(f"/swiggy/callback?state={q['state']}&code=code-1", base_url=APP)


def test_swiggy_sign_in_started_from_v3_returns_to_v3(client, swiggy):  # noqa: F811
    r = _connect(client, swiggy, {"ui": "v3"})
    assert r.status_code == 302 and r.headers["Location"] == "/v3?swiggy=connected"
    assert "sp_swiggy_ui=;" in " ".join(r.headers.getlist("Set-Cookie"))          # used once


def test_swiggy_sign_in_from_the_classic_app_is_unchanged(client, swiggy):  # noqa: F811
    r = _connect(client, swiggy, {})
    assert r.headers["Location"] == "/?tab=more&swiggy=connected"


def test_v3_error_codes_stay_fixed(client, swiggy):  # noqa: F811
    _allow_app_host()
    _secure_profile(client)
    client.post("/api/user/1/swiggy/connect", json={"ui": "v3"}, base_url=APP)
    denied = client.get("/swiggy/callback?error=access_denied&error_description=Hacked", base_url=APP)
    assert denied.headers["Location"] == "/v3?swiggy_error=denied"
