"""Grill #62: identity in an HttpOnly, signed, rotating session cookie with CSRF protection,
while header keys (and every profile made before) keep working."""
import datetime as dt

import pytest

from smartplate import clock, db, websession
from smartplate.app import create_app

NEW = {"name": "Priya", "diet": "veg", "weekly_budget": 2000, "meals": ["dinner"], "favourites": [6]}
OPT_IN = {"X-Ziggy-Session": "cookie"}


@pytest.fixture
def app(seeded):
    app = create_app()
    app.config["TESTING"] = True
    return app


def _cookie(client, name):
    c = client.get_cookie(name)
    return c.value if c else None


def _csrf(client):
    return {"X-CSRF-Token": _cookie(client, websession.CSRF_COOKIE)}


def _create(client, headers=OPT_IN):
    r = client.post("/api/profiles", json=dict(NEW), headers=headers)
    assert r.status_code == 201, r.get_json()
    return r.get_json()


def test_new_profile_opens_with_cookie_alone(app):
    browser = app.test_client()
    v = _create(browser)
    uid = v["user"]["id"]
    set_cookie = [h for h in browser.post("/api/session/claim", json={"user_id": uid},
                                          headers={"X-SmartPlate-Key": v["access_key"], **_csrf(browser)}
                                          ).headers.getlist("Set-Cookie") if h.startswith("zg_session=")][0]
    assert "HttpOnly" in set_cookie and "SameSite=Lax" in set_cookie
    assert browser.get(f"/api/user/{uid}/plan").status_code == 200          # no header key
    assert browser.get("/api/session").get_json()["profiles"] == [{"id": uid, "name": "Priya"}]
    stranger = app.test_client()
    assert stranger.get(f"/api/user/{uid}/plan").status_code == 401


def test_secure_flag_when_served_over_https(app):
    browser = app.test_client()
    r = browser.post("/api/profiles", json=dict(NEW), headers=OPT_IN, base_url="https://ziggy.example")
    cookie = [h for h in r.headers.getlist("Set-Cookie") if h.startswith("zg_session=")][0]
    assert "Secure" in cookie and "HttpOnly" in cookie


def test_state_changes_need_the_csrf_token(app):
    browser = app.test_client()
    uid = _create(browser)["user"]["id"]
    body = {"login": "priya-csrf", "password": "correct horse"}
    r = browser.post(f"/api/user/{uid}/account", json=body)
    assert r.status_code == 403 and r.get_json()["code"] == "csrf_failed"
    r = browser.post(f"/api/user/{uid}/account", json=body, headers={"X-CSRF-Token": "guess"})
    assert r.status_code == 403
    assert browser.post(f"/api/user/{uid}/account", json=body, headers=_csrf(browser)).status_code == 200
    assert browser.get(f"/api/user/{uid}/account").status_code == 200       # reads need no token


def test_forged_or_tampered_cookie_is_ignored(app):
    browser = app.test_client()
    uid = _create(browser)["user"]["id"]
    value = _cookie(browser, websession.COOKIE)
    other = app.test_client()
    other.set_cookie(websession.COOKIE, value[:-2] + ("AA" if value[-2:] != "AA" else "BB"))
    assert other.get(f"/api/user/{uid}/plan").status_code == 401
    other.set_cookie(websession.COOKIE, "not-signed")
    assert other.get(f"/api/user/{uid}/plan").status_code == 401


def test_token_rotates_daily_and_old_token_dies_after_grace(app, monkeypatch):
    browser = app.test_client()
    uid = _create(browser)["user"]["id"]
    first = _cookie(browser, websession.COOKIE)
    later = clock.now() + dt.timedelta(days=2)
    monkeypatch.setattr(clock, "now", lambda: later)
    assert browser.get(f"/api/user/{uid}/plan").status_code == 200
    second = _cookie(browser, websession.COOKIE)
    assert second and second != first
    copied = app.test_client()
    copied.set_cookie(websession.COOKIE, first)
    assert copied.get(f"/api/user/{uid}/plan").status_code == 200           # in-flight grace
    monkeypatch.setattr(clock, "now", lambda: later + dt.timedelta(minutes=5))
    assert copied.get(f"/api/user/{uid}/plan").status_code == 401           # a stolen old cookie is dead
    assert browser.get(f"/api/user/{uid}/plan").status_code == 200


def test_idle_session_expires(app, monkeypatch):
    browser = app.test_client()
    uid = _create(browser)["user"]["id"]
    later = clock.now() + websession.IDLE_TTL + dt.timedelta(days=1)
    monkeypatch.setattr(clock, "now", lambda: later)
    assert browser.get(f"/api/user/{uid}/plan").status_code == 401


def test_legacy_key_migrates_into_the_session(app):
    old_browser = app.test_client()
    v = _create(old_browser, headers={})                      # made the old way: no cookie
    uid, key = v["user"]["id"], v["access_key"]
    assert _cookie(old_browser, websession.COOKIE) is None
    assert old_browser.get(f"/api/user/{uid}/plan", headers={"X-SmartPlate-Key": key}).status_code == 200
    r = old_browser.post("/api/session/claim", json={"user_id": uid}, headers={"X-SmartPlate-Key": key})
    assert r.status_code == 200 and r.get_json()["name"] == "Priya"
    assert old_browser.get(f"/api/user/{uid}/plan").status_code == 200       # cookie alone now
    bad = app.test_client()
    assert bad.post("/api/session/claim", json={"user_id": uid},
                    headers={"X-SmartPlate-Key": "wrong-key-wrong-key"}).status_code == 401
    # sample (shared) profiles have no key and cannot be claimed
    assert bad.post("/api/session/claim", json={"user_id": 1}, headers={"X-SmartPlate-Key": "x" * 20}).status_code == 401


def test_rotating_the_recovery_code_removes_every_other_browser(app):
    a = app.test_client()
    v = _create(a)
    uid, key = v["user"]["id"], v["access_key"]
    b = app.test_client()
    assert b.post("/api/session/claim", json={"user_id": uid}, headers={"X-SmartPlate-Key": key}).status_code == 200
    assert b.get(f"/api/user/{uid}/plan").status_code == 200
    r = a.post(f"/api/user/{uid}/account/rotate-key", json={"confirmation": "ROTATE"}, headers=_csrf(a))
    assert r.status_code == 200
    assert b.get(f"/api/user/{uid}/plan").status_code == 401
    assert a.get(f"/api/user/{uid}/plan").status_code == 200


def test_password_sign_in_session_and_sign_out(app):
    owner = app.test_client()
    uid = _create(owner)["user"]["id"]
    assert owner.post(f"/api/user/{uid}/account", json={"login": "priya-s", "password": "correct horse"},
                      headers=_csrf(owner)).status_code == 200
    phone = app.test_client()
    planted = _cookie(owner, websession.COOKIE)
    r = phone.post("/api/signin", json={"login": "priya-s", "password": "correct horse", "device": "Phone"},
                   headers=OPT_IN)
    assert r.status_code == 200
    assert phone.get(f"/api/user/{uid}/plan").status_code == 200
    account = phone.get(f"/api/user/{uid}/account").get_json()
    assert [d["label"] for d in account["devices"] if d["this_device"]] == ["Phone"]
    # The owner signs the phone out from the laptop: the phone's session loses the profile.
    device = account["devices"][0]["id"]
    assert owner.post(f"/api/user/{uid}/devices/{device}/remove", json={}, headers=_csrf(owner)).status_code == 200
    assert phone.get(f"/api/user/{uid}/plan").status_code == 401
    assert planted != _cookie(phone, websession.COOKIE)


def test_sign_in_renews_token_against_session_fixation(app):
    owner = app.test_client()
    uid = _create(owner)["user"]["id"]
    owner.post(f"/api/user/{uid}/account", json={"login": "priya-f", "password": "correct horse"},
               headers=_csrf(owner))
    victim = app.test_client()
    victim.get("/api/session")
    _create(victim)                                        # victim has a session (could be planted)
    before = _cookie(victim, websession.COOKIE)
    victim.post("/api/signin", json={"login": "priya-f", "password": "correct horse"},
                headers={**OPT_IN, **_csrf(victim)})
    attacker = app.test_client()
    attacker.set_cookie(websession.COOKIE, before)
    assert attacker.get(f"/api/user/{uid}/plan").status_code == 401


def test_sign_out_forgets_profile_on_this_browser(app):
    browser = app.test_client()
    uid = _create(browser)["user"]["id"]
    assert browser.post(f"/api/user/{uid}/signout", json={}, headers=_csrf(browser)).status_code == 200
    assert browser.get(f"/api/user/{uid}/plan").status_code == 401
    assert browser.get("/api/session").get_json()["profiles"] == []


def test_deleting_a_profile_removes_session_holds(app):
    browser = app.test_client()
    uid = _create(browser)["user"]["id"]
    browser.delete(f"/api/user/{uid}", json={"confirmation": "DELETE"}, headers=_csrf(browser))
    with db.cursor() as cur:
        assert not cur.execute("SELECT 1 FROM web_session_profiles WHERE user_id=?", (uid,)).fetchone()


def test_scripts_without_opt_in_get_no_cookie(app):
    script = app.test_client()
    _create(script, headers={})
    assert _cookie(script, websession.COOKIE) is None
