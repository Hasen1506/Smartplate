"""Sign-in for private profiles, device tokens, rate limits and token encryption."""
import datetime as dt

import pytest

from smartplate import db, ratelimit, vault
from smartplate.app import create_app

NEW = {"name": "Priya", "diet": "veg", "weekly_budget": 2000, "meals": ["dinner"], "favourites": [6]}


@pytest.fixture
def client(seeded):
    ratelimit.reset()
    app = create_app()
    app.config["TESTING"] = True
    yield app.test_client()
    ratelimit.reset()


def _private(client):
    v = client.post("/api/profiles", json=dict(NEW)).get_json()
    return v["user"]["id"], {"X-SmartPlate-Key": v["access_key"]}


def _with_login(client, login="priya", password="correct horse"):
    uid, h = _private(client)
    r = client.post(f"/api/user/{uid}/account", json={"login": login, "password": password}, headers=h)
    assert r.status_code == 200, r.get_json()
    return uid, h


def test_sign_in_opens_the_profile_on_another_device(client):
    uid, _ = _with_login(client, "Priya@Example.com ".strip())
    r = client.post("/api/signin", json={"login": "PRIYA@example.com", "password": "correct horse", "device": "Phone"})
    assert r.status_code == 200
    got = r.get_json()
    assert got["user_id"] == uid and got["name"] == "Priya" and len(got["key"]) >= 24
    phone = {"X-SmartPlate-Key": got["key"]}
    assert client.get(f"/api/user/{uid}/plan", headers=phone).status_code == 200
    acct = client.get(f"/api/user/{uid}/account", headers=phone).get_json()
    assert acct["login"] == "priya@example.com"
    assert [d["label"] for d in acct["devices"]] == ["Phone"] and acct["devices"][0]["this_device"]
    with db.cursor() as cur:                                            # only hashes are stored
        stored = [r[0] for r in cur.execute("SELECT token_hash FROM devices")] + \
                 [r[0] for r in cur.execute("SELECT pw_hash FROM logins")]
    assert got["key"] not in "".join(stored) and "correct horse" not in "".join(stored)


def test_wrong_password_and_unknown_name_look_the_same(client):
    _with_login(client)
    a = client.post("/api/signin", json={"login": "priya", "password": "wrong password"})
    b = client.post("/api/signin", json={"login": "nobody", "password": "correct horse"})
    assert a.status_code == b.status_code == 400
    assert a.get_json() == b.get_json() == {"error": "That name and password don't match."}


def test_repeated_failures_are_rate_limited(client):
    _with_login(client)
    for _ in range(5):
        assert client.post("/api/signin", json={"login": "priya", "password": "nope-nope"}).status_code == 400
    r = client.post("/api/signin", json={"login": "priya", "password": "correct horse"})
    assert r.status_code == 429 and int(r.headers["Retry-After"]) > 0


def test_sign_out_and_remove_device_revoke_access(client):
    uid, owner = _with_login(client)
    k1 = client.post("/api/signin", json={"login": "priya", "password": "correct horse", "device": "Phone"}).get_json()["key"]
    k2 = client.post("/api/signin", json={"login": "priya", "password": "correct horse", "device": "Laptop"}).get_json()["key"]
    assert client.post(f"/api/user/{uid}/signout", json={}, headers={"X-SmartPlate-Key": k1}).status_code == 200
    assert client.get(f"/api/user/{uid}/plan", headers={"X-SmartPlate-Key": k1}).status_code == 401
    laptop = next(d for d in client.get(f"/api/user/{uid}/account", headers=owner).get_json()["devices"])
    assert laptop["label"] == "Laptop"
    client.post(f"/api/user/{uid}/devices/{laptop['id']}/remove", json={}, headers=owner)
    assert client.get(f"/api/user/{uid}/plan", headers={"X-SmartPlate-Key": k2}).status_code == 401
    assert client.get(f"/api/user/{uid}/plan", headers=owner).status_code == 200    # the profile key still works


def test_a_device_token_only_opens_its_own_profile(client):
    uid, _ = _with_login(client)
    other, _ = _private(client)
    key = client.post("/api/signin", json={"login": "priya", "password": "correct horse"}).get_json()["key"]
    assert client.get(f"/api/user/{other}/plan", headers={"X-SmartPlate-Key": key}).status_code == 401


def test_account_rules(client):
    uid, h = _private(client)
    other, h2 = _private(client)
    bad = [({"login": "ab", "password": "long enough"}, "3–64"), ({"login": "priya", "password": "short"}, "8 characters"),
           ({"login": "has space", "password": "long enough"}, "3–64")]
    for body, msg in bad:
        r = client.post(f"/api/user/{uid}/account", json=body, headers=h)
        assert r.status_code == 400 and msg in r.get_json()["error"]
    assert client.post(f"/api/user/{uid}/account", json={"login": "priya", "password": "long enough"}, headers=h).status_code == 200
    r = client.post(f"/api/user/{other}/account", json={"login": "priya", "password": "long enough"}, headers=h2)
    assert r.status_code == 400 and "taken" in r.get_json()["error"]
    # nobody else can set a sign-in on a private profile
    assert client.post(f"/api/user/{uid}/account", json={"login": "x-priya", "password": "long enough"}).status_code == 401
    # shared sample profiles can't have one
    r = client.post("/api/user/1/account", json={"login": "sample", "password": "long enough"})
    assert r.status_code == 400 and "Sample profiles" in r.get_json()["error"]


def test_changing_the_password_signs_out_other_devices(client):
    """L-03: a leaked password must not keep working through device tokens it issued."""
    uid, owner = _with_login(client)
    key = client.post("/api/signin", json={"login": "priya", "password": "correct horse"}).get_json()["key"]
    here = client.post("/api/signin", json={"login": "priya", "password": "correct horse"}).get_json()["key"]
    client.post(f"/api/user/{uid}/account", json={"login": "priya", "password": "a new password"},
                headers={"X-SmartPlate-Key": here})
    assert client.post("/api/signin", json={"login": "priya", "password": "correct horse"}).status_code == 400
    assert client.post("/api/signin", json={"login": "priya", "password": "a new password"}).status_code == 200
    assert client.get(f"/api/user/{uid}/plan", headers={"X-SmartPlate-Key": key}).status_code == 401
    assert client.get(f"/api/user/{uid}/plan", headers={"X-SmartPlate-Key": here}).status_code == 200   # the browser that changed it
    assert client.get(f"/api/user/{uid}/plan", headers=owner).status_code == 200                         # recovery key unaffected
    # re-saving the same password (e.g. renaming the login) signs nobody out
    again = client.post("/api/signin", json={"login": "priya", "password": "a new password"}).get_json()["key"]
    client.post(f"/api/user/{uid}/account", json={"login": "priya2", "password": "a new password"}, headers=owner)
    assert client.get(f"/api/user/{uid}/plan", headers={"X-SmartPlate-Key": again}).status_code == 200


def test_idle_device_tokens_expire(client, monkeypatch):
    uid, owner = _with_login(client)
    key = client.post("/api/signin", json={"login": "priya", "password": "correct horse"}).get_json()["key"]
    from smartplate import access, clock
    later = clock.now() + access.DEVICE_IDLE_TTL + dt.timedelta(days=1)
    monkeypatch.setattr(clock, "now", lambda: later)
    assert client.get(f"/api/user/{uid}/plan", headers={"X-SmartPlate-Key": key}).status_code == 401


def test_third_party_failures_do_not_lock_out_the_owner(client):
    """L-04: failures from another address don't lock the owner's sign-in name."""
    _with_login(client)
    for _ in range(6):
        client.post("/api/signin", json={"login": "priya", "password": "wrong guess"},
                    environ_base={"REMOTE_ADDR": "203.0.113.9"})
    assert client.post("/api/signin", json={"login": "priya", "password": "x" * 9},
                       environ_base={"REMOTE_ADDR": "203.0.113.9"}).status_code == 429       # the guesser is limited
    assert client.post("/api/signin", json={"login": "priya", "password": "correct horse"}).status_code == 200


def test_new_profiles_are_rate_limited_per_address(client):
    for _ in range(20):
        assert client.post("/api/profiles", json=dict(NEW)).status_code == 201
    assert client.post("/api/profiles", json=dict(NEW)).status_code == 429


def test_vault_seals_and_rejects_foreign_keys(seeded, monkeypatch):
    from smartplate import config
    monkeypatch.setattr(config, "SECRET", "")
    sealed = vault.seal("swiggy-token")
    assert sealed.startswith("v1:") and "swiggy-token" not in sealed
    assert vault.unseal(sealed) == "swiggy-token"
    assert vault.unseal("legacy-plaintext") == "legacy-plaintext"
    monkeypatch.setattr(config, "SECRET", "a different server secret")
    assert vault.unseal(sealed) is None
    assert vault.unseal(vault.seal("t2")) == "t2"



def test_recovery_rotation_revokes_old_codes_devices_and_approvals(client):
    uid, old = _with_login(client, login="rotate-me")
    phone = client.post("/api/signin", json={"login": "rotate-me", "password": "correct horse", "device": "Phone"}).get_json()
    with db.cursor() as cur:
        cur.execute("INSERT INTO swiggy_pending(state, user_id, verifier, redirect_uri, created_ts) VALUES (?,?,?,?,?)", ("old-state", uid, "verifier", "https://example.com/callback", "now"))
        cur.execute("INSERT INTO swiggy_checkout_quotes VALUES (?,?,?,?)", ("old-quote", uid, "cart", "now"))
    route = f"/api/user/{uid}/account/rotate-key"
    assert client.post(route, json={"confirmation": "ROTATE"}).status_code == 401
    assert client.post(route, json={}, headers=old).status_code == 400
    response = client.post(route, json={"confirmation": "ROTATE"}, headers=old)
    assert response.status_code == 200 and response.headers["Cache-Control"] == "no-store"
    fresh = {"X-SmartPlate-Key": response.get_json()["key"]}
    assert fresh != old
    assert client.get(f"/api/user/{uid}/plan", headers=old).status_code == 401
    assert client.get(f"/api/user/{uid}/plan", headers={"X-SmartPlate-Key": phone["key"]}).status_code == 401
    assert client.get(f"/api/user/{uid}/plan", headers=fresh).status_code == 200
    with db.cursor() as cur:
        for table in ("devices", "swiggy_pending", "swiggy_checkout_quotes"):
            assert not cur.execute(f"SELECT 1 FROM {table} WHERE user_id=?", (uid,)).fetchone()
    assert client.post("/api/signin", json={"login": "rotate-me", "password": "correct horse"}).status_code == 200
