"""Sign-in by email (Oct 2026): new sign-ins name an email, validated, unique and
case-insensitive; accounts made with a free-form name before keep working.

Before this change the `email` field was ignored: a profile created with an email and a
password got no sign-in at all, so every test below that signs in by email failed."""
import pytest

from smartplate import ratelimit
from smartplate.app import create_app

NEW = {"name": "Priya", "diet": "veg", "weekly_budget": 2000, "rhythm": {"breakfast": "skip", "lunch": "skip", "dinner": "order"}}


@pytest.fixture
def client(seeded):
    ratelimit.reset()
    app = create_app()
    app.config["TESTING"] = True
    yield app.test_client()
    ratelimit.reset()


def _profile(client, **extra):
    return client.post("/api/profiles", json={**NEW, **extra})


def test_profile_created_with_an_email_signs_in_with_any_case(client):
    r = _profile(client, email="  Priya.K@Example.COM ", password="correct horse")
    assert r.status_code == 201, r.get_json()
    v = r.get_json()
    assert v["login"] == "priya.k@example.com"                     # stored lower-case
    uid = v["user"]["id"]
    got = client.post("/api/signin", json={"email": "PRIYA.k@example.com", "password": "correct horse"})
    assert got.status_code == 200 and got.get_json()["user_id"] == uid
    acct = client.get(f"/api/user/{uid}/account", headers={"X-SmartPlate-Key": v["access_key"]}).get_json()
    assert acct["email"] == "priya.k@example.com"


@pytest.mark.parametrize("bad", ["priya", "priya@", "@example.com", "priya@example", "pri ya@example.com",
                                 "priya@@example.com", "priya..k@example.com", "a" * 65 + "@example.com",
                                 "priya@-example.com", "", 42])
def test_an_invalid_email_is_refused_before_anything_is_written(client, bad):
    r = _profile(client, email=bad, password="correct horse")
    assert r.status_code == 400
    assert "valid email" in r.get_json()["error"]
    assert not [u for u in client.get("/api/users").get_json() if u["name"] == "Priya"]


def test_an_email_is_unique_whatever_its_case(client):
    assert _profile(client, email="priya@example.com", password="correct horse").status_code == 201
    r = _profile(client, email="Priya@Example.com", password="another horse")
    assert r.status_code == 400 and "already has a Ziggy account" in r.get_json()["error"]
    # and adding it later to another profile is refused the same way
    v = _profile(client).get_json()
    r = client.post(f"/api/user/{v['user']['id']}/account", json={"email": "PRIYA@example.COM", "password": "third horse!"},
                    headers={"X-SmartPlate-Key": v["access_key"]})
    assert r.status_code == 400 and "already has a Ziggy account" in r.get_json()["error"]


def test_email_added_after_onboarding_is_the_sign_in(client):
    v = _profile(client).get_json()                                   # v3: the account step comes last
    h = {"X-SmartPlate-Key": v["access_key"]}
    r = client.post(f"/api/user/{v['user']['id']}/account", json={"email": "Asha@Example.in", "password": "correct horse"}, headers=h)
    assert r.status_code == 200 and r.get_json()["email"] == "asha@example.in"
    assert client.post("/api/signin", json={"email": "asha@example.in", "password": "correct horse"}).status_code == 200
    bad = client.post("/api/signin", json={"email": "asha@example.in", "password": "wrong horse"})
    assert bad.status_code == 400 and bad.get_json()["error"] == "That email and password don't match."


def test_existing_name_accounts_keep_working(client):
    v = _profile(client, login="priya_k", password="correct horse").get_json()   # the legacy field
    assert v["login"] == "priya_k"
    r = client.post("/api/signin", json={"login": "Priya_K", "password": "correct horse"})
    assert r.status_code == 200 and r.get_json()["user_id"] == v["user"]["id"]
    acct = client.get(f"/api/user/{v['user']['id']}/account", headers={"X-SmartPlate-Key": v["access_key"]}).get_json()
    assert acct["login"] == "priya_k" and acct["email"] is None
    # a name account can move to an email; the old name then stops being a sign-in
    h = {"X-SmartPlate-Key": v["access_key"]}
    assert client.post(f"/api/user/{v['user']['id']}/account", json={"email": "priya@example.com", "password": "correct horse"},
                       headers=h).status_code == 200
    assert client.post("/api/signin", json={"email": "priya@example.com", "password": "correct horse"}).status_code == 200
    assert client.post("/api/signin", json={"login": "priya_k", "password": "correct horse"}).status_code == 400
