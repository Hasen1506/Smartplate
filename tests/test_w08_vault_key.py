"""Grill #64: the token-encryption key comes from the environment, production refuses to
start without a strong one, and keys can be rotated without signing anybody out."""
import pytest

from smartplate import config, db, vault

STRONG_A = "k7Qw2Lr9Zx4Vb8Nm1Pc6Td3Hs5Jf0Gy-AAAA"
STRONG_B = "Z9y8X7w6V5u4T3s2R1q0P9o8N7m6L5k4-BBBB"


@pytest.mark.parametrize("value,ok", [
    ("", False), ("dev", False), ("changeme", False), ("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", False),
    ("short-but-random-9x2", False), (STRONG_A, True)])
def test_secret_problem(value, ok):
    assert (config.secret_problem(value) is None) is ok


def test_production_refuses_to_start_without_a_strong_secret(seeded, monkeypatch):
    from smartplate.app import create_app
    monkeypatch.setattr(config, "PRODUCTION", True)
    for bad in ("", "changeme", "too-short-123"):
        monkeypatch.setattr(config, "SECRET", bad)
        with pytest.raises(RuntimeError, match="SMARTPLATE_SECRET"):
            create_app()
    monkeypatch.setattr(config, "SECRET", STRONG_A)
    monkeypatch.setattr(config, "FIXTURE_DATA", True)          # keep the seeded world for this check
    assert create_app() is not None


def test_development_without_secret_still_works(seeded, monkeypatch):
    monkeypatch.setattr(config, "PRODUCTION", False)
    monkeypatch.setattr(config, "SECRET", "")
    assert vault.unseal(vault.seal("tok")) == "tok"


def _connection(user_id, sealed):
    from smartplate.integrations import swiggy_connect
    swiggy_connect.init_schema()
    with db.cursor() as cur:
        cur.execute("DELETE FROM swiggy_connections WHERE user_id=?", (user_id,))
        cur.execute("INSERT INTO swiggy_connections(user_id, access_token, expires_ts, connected_ts) "
                    "VALUES (?,?,?,?)", (user_id, sealed, "2099-01-01T00:00:00", "2026-10-14T10:00:00"))


def _token(user_id):
    with db.cursor() as cur:
        return cur.execute("SELECT access_token FROM swiggy_connections WHERE user_id=?", (user_id,)).fetchone()[0]


def test_rotation_keeps_tokens_and_reseals_them(seeded, monkeypatch):
    monkeypatch.setattr(config, "SECRET", STRONG_A)
    monkeypatch.setattr(config, "SECRET_PREVIOUS", ())
    _connection(1, vault.seal("swiggy-token-1"))
    # Rotate: the old key moves to SMARTPLATE_SECRET_PREVIOUS, a new key becomes current.
    monkeypatch.setattr(config, "SECRET", STRONG_B)
    monkeypatch.setattr(config, "SECRET_PREVIOUS", (STRONG_A,))
    old = _token(1)
    assert vault.unseal(old) == "swiggy-token-1" and not vault.is_current(old)
    result = vault.reseal_all()
    assert result["resealed"] >= 1 and result["unreadable"] == 0
    fresh = _token(1)
    assert vault.is_current(fresh)
    # After re-sealing, the previous key can be dropped entirely.
    monkeypatch.setattr(config, "SECRET_PREVIOUS", ())
    assert vault.unseal(fresh) == "swiggy-token-1"


def test_trial_key_in_database_is_migrated_then_removed(seeded, monkeypatch):
    monkeypatch.setattr(config, "SECRET", "")
    _connection(1, vault.seal("trial-token"))          # sealed with the key kept in app_secrets
    with db.cursor() as cur:
        assert cur.execute("SELECT 1 FROM app_secrets WHERE name='fernet'").fetchone()
    monkeypatch.setattr(config, "SECRET", STRONG_A)
    assert vault.unseal(_token(1)) == "trial-token"    # still opens: nobody is signed out
    result = vault.reseal_all()
    assert result["unreadable"] == 0 and result.get("legacy_key_removed") is True
    with db.cursor() as cur:
        assert not cur.execute("SELECT 1 FROM app_secrets WHERE name='fernet'").fetchone()
    assert vault.unseal(_token(1)) == "trial-token" and vault.is_current(_token(1))


def test_generated_keys_are_sealed_at_rest_when_a_secret_is_set(seeded, monkeypatch):
    monkeypatch.setattr(config, "SECRET", "")
    plain = vault.stored("vapid_private", lambda: "private-key-material")
    monkeypatch.setattr(config, "SECRET", STRONG_A)
    assert vault.stored("vapid_private", lambda: "other") == plain
    with db.cursor() as cur:
        raw = cur.execute("SELECT value FROM app_secrets WHERE name='vapid_private'").fetchone()[0]
    assert raw.startswith("v1:") and "private-key-material" not in raw
    assert vault.stored("vapid_private", lambda: "other") == plain
