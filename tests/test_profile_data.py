"""Private data controls: ownership, credential exclusion and atomic removal."""
import json
import sqlite3

import pytest

from smartplate import db, profile_data, push
from smartplate.app import create_app


@pytest.fixture
def client(seeded):
    app = create_app()
    app.config["TESTING"] = True
    return app.test_client()


def private(client, name):
    result = client.post("/api/profiles", json={"name": name, "diet": "veg", "weekly_budget": 2000,
                                               "meals": ["dinner"], "favourites": [6]}).get_json()
    return result["user"]["id"], {"X-SmartPlate-Key": result["access_key"]}, result["plan"]["id"]


def test_export_is_owned_and_excludes_all_credentials(client):
    uid, headers, pid = private(client, "My private profile")
    other, _, other_pid = private(client, "Another person's private profile")
    assert client.get(f"/api/user/{uid}/data.json").status_code == 401
    client.post(f"/api/user/{uid}/account", headers=headers, json={"login": "export-owner", "password": "private-password"})
    with db.cursor() as cur:
        cur.execute("INSERT INTO swiggy_connections(user_id,access_token,connected_ts,address_label) VALUES (?,?,?,?)",
                    (uid, "secret-provider-token", "2026-09-30T00:00:00", "My real home"))
        cur.execute("INSERT INTO push_subscriptions(user_id,endpoint,p256dh,auth,created_ts) VALUES (?,?,?,?,?)",
                    (uid, "https://fcm.googleapis.com/my-device", "secret-encryption-key", "secret-auth", "now"))
        cur.execute("INSERT INTO swiggy_pending(state, user_id, verifier, redirect_uri, created_ts) VALUES (?,?,?,?,?)", ("secret-state", uid, "secret-verifier", "https://example.com/callback", "now"))
    client.post(f"/api/plan/{pid}/save-template", json={"title": "My published week"}, headers=headers)
    result = client.get(f"/api/user/{uid}/data.json", headers=headers)
    assert result.status_code == 200 and result.headers["Cache-Control"] == "no-store"
    data = result.get_json()["data"]
    assert data["profile"]["id"] == uid
    assert data["plans"][0]["id"] == pid and all(r["plan_id"] == pid for r in data["sessions"])
    assert data["community_templates"][0]["title"] == "My published week"
    text = json.dumps(data)
    for forbidden in ["secret-provider-token", "secret-encryption-key", "secret-auth", "secret-verifier",
                      "private-password", "pw_hash", "access_hash", "token_hash", "Another person's"]:
        assert forbidden not in text
    assert not any(r["id"] == other_pid for r in data["plans"])
    assert client.get(f"/api/user/{other}/data.json", headers=headers).status_code == 401
    assert "author_user_id" not in client.get("/api/community").get_json()[0]


def test_delete_removes_dependents_and_preserves_other_users(client):
    uid, headers, pid = private(client, "Delete me")
    other, other_headers, other_pid = private(client, "Keep me")
    client.post(f"/api/user/{uid}/account", headers=headers, json={"login": "delete-me", "password": "private-password"})
    client.post(f"/api/plan/{pid}/save-template", headers=headers, json={"title": "Owned published week"})
    with db.cursor() as cur:
        cur.execute("INSERT INTO push_subscriptions(user_id,endpoint,p256dh,auth,created_ts) VALUES (?,?,?,?,?)",
                    (uid, "https://fcm.googleapis.com/delete-device", "key", "auth", "now"))
        sub = dict(cur.execute("SELECT * FROM push_subscriptions WHERE user_id=?", (uid,)).fetchone())
        session = cur.execute("SELECT id FROM sessions WHERE plan_id=? LIMIT 1", (pid,)).fetchone()[0]
        cur.execute("INSERT INTO push_sent VALUES (?,?,?,?)", (sub["id"], session, "due", "now"))
    result = client.delete(f"/api/user/{uid}", headers=headers, json={"confirmation": "DELETE"})
    assert result.status_code == 200 and result.get_json()["deleted"]
    with db.cursor() as cur:
        for table in profile_data.USER_TABLES:
            assert not cur.execute(f"SELECT 1 FROM {table} WHERE user_id=?", (uid,)).fetchone(), table
        for table in ("sessions", "decisions", "grocery_baskets"):
            assert not cur.execute(f"SELECT 1 FROM {table} WHERE plan_id=?", (pid,)).fetchone(), table
        assert not cur.execute("SELECT 1 FROM plans WHERE id=?", (pid,)).fetchone()
        assert not cur.execute("SELECT 1 FROM push_sent WHERE subscription_id=?", (sub["id"],)).fetchone()
        assert not cur.execute("SELECT 1 FROM community_templates WHERE author_user_id=?", (uid,)).fetchone()
    assert client.get(f"/api/user/{other}/plan", headers=other_headers).get_json()["plan"]["id"] == other_pid
    assert client.get(f"/api/user/{uid}/plan", headers=headers).status_code == 404
    assert client.post("/api/signin", json={"login": "delete-me", "password": "private-password"}).status_code == 400
    sent = []
    assert push.send_due(pairs=[(sub, {})], sender=lambda *a, **kw: sent.append(a)) == 0
    assert not sent


def test_delete_requires_private_ownership_and_exact_confirmation(client):
    uid, headers, _ = private(client, "Owner")
    assert client.delete(f"/api/user/{uid}", json={"confirmation": "DELETE"}).status_code == 401
    assert client.delete(f"/api/user/{uid}", headers=headers, json={"confirmation": "delete"}).status_code == 400
    assert client.delete("/api/user/1", json={"confirmation": "DELETE"}).status_code == 400
    assert client.get("/api/user/1/data.json").status_code == 400
    assert client.get(f"/api/user/{uid}/plan", headers=headers).status_code == 200


def test_delete_rolls_back_every_record_on_database_failure(client):
    uid, headers, pid = private(client, "Still here")
    with db.cursor() as cur:
        before = cur.execute("SELECT COUNT(*) FROM sessions WHERE plan_id=?", (pid,)).fetchone()[0]
        cur.execute(f"CREATE TRIGGER prevent_delete BEFORE DELETE ON plans WHEN OLD.id={int(pid)} "
                    "BEGIN SELECT RAISE(ABORT, 'test interruption'); END")
    with pytest.raises(sqlite3.IntegrityError):
        profile_data.delete(uid, "DELETE")
    with db.cursor() as cur:
        assert cur.execute("SELECT COUNT(*) FROM sessions WHERE plan_id=?", (pid,)).fetchone()[0] == before
        assert cur.execute("SELECT 1 FROM users WHERE id=?", (uid,)).fetchone()
    assert client.get(f"/api/user/{uid}/data.json", headers=headers).status_code == 200
