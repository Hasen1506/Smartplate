"""Server-side secrets: generated keys and encryption of stored third-party tokens.

`seal()` encrypts a token with Fernet (AES-128-CBC + HMAC-SHA256) under a key derived from
SMARTPLATE_SECRET. In production that variable is required (app.create_app refuses to start
without a strong one, grill #64), so the key never sits in the database beside the tokens.
Off production, with no secret set, a random key is made once and kept in `app_secrets` so a
local trial needs zero setup.

Key rotation: move the current value to SMARTPLATE_SECRET_PREVIOUS and set a new
SMARTPLATE_SECRET. `unseal()` then opens values sealed under any listed key (and under the old
database-kept trial key, if one exists) and `reseal_all()` rewrites them under the new key.
"""
import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from . import config, db

PREFIX = "v1:"
LEGACY_NAME = "fernet"          # the trial key kept in app_secrets before SMARTPLATE_SECRET was required
# Every column that holds sealed values: (table, key column, sealed column).
SEALED_COLUMNS = (("swiggy_connections", "user_id", "access_token"),)


def stored(name: str, make) -> str:
    """A named generated secret (e.g. the Web Push VAPID key), created on first use and then kept.
    With SMARTPLATE_SECRET set it is kept sealed, so a database dump alone does not reveal it;
    a value kept in plain text by an older version is sealed the next time it is read."""
    with db.cursor() as cur:
        row = cur.execute("SELECT value FROM app_secrets WHERE name=?", (name,)).fetchone()
        if not row:
            value = make()
            cur.execute("INSERT OR IGNORE INTO app_secrets(name, value) VALUES (?,?)",
                        (name, seal(value) if config.SECRET and name != LEGACY_NAME else value))
            row = cur.execute("SELECT value FROM app_secrets WHERE name=?", (name,)).fetchone()
    value = row["value"]
    if value.startswith(PREFIX):
        opened = unseal(value)
        if opened is None:
            raise RuntimeError(f"The stored secret {name!r} was sealed under a key this server no longer has. "
                               "Put the previous SMARTPLATE_SECRET in SMARTPLATE_SECRET_PREVIOUS.")
        return opened
    if config.SECRET and name != LEGACY_NAME:
        with db.cursor() as cur:
            cur.execute("UPDATE app_secrets SET value=? WHERE name=? AND value=?", (seal(value), name, value))
    return value


def _derive(secret: str) -> bytes:
    return base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())


def _legacy_key() -> bytes | None:
    try:
        with db.cursor() as cur:
            row = cur.execute("SELECT value FROM app_secrets WHERE name=?", (LEGACY_NAME,)).fetchone()
    except Exception:                                       # noqa: BLE001 — table not there yet
        return None
    return row["value"].encode() if row else None


def _keys() -> list[bytes]:
    """The sealing key first, then every key that may still open older values."""
    if config.SECRET:
        keys = [_derive(config.SECRET)] + [_derive(old) for old in config.SECRET_PREVIOUS]
        legacy = _legacy_key()
        if legacy:
            keys.append(legacy)
        return keys
    return [stored(LEGACY_NAME, lambda: Fernet.generate_key().decode()).encode()]


def _fernet() -> MultiFernet:
    return MultiFernet([Fernet(k) for k in _keys()])


def seal(text: str) -> str:
    return PREFIX + _fernet().encrypt(text.encode()).decode()


def unseal(value: str | None) -> str | None:
    """Decrypt a sealed value. Tokens saved before encryption existed pass through;
    a value sealed under an unknown key returns None (treat as signed out)."""
    if not value:
        return None
    if not value.startswith(PREFIX):
        return value
    try:
        return _fernet().decrypt(value[len(PREFIX):].encode()).decode()
    except InvalidToken:
        return None


def is_current(value: str | None) -> bool:
    """Whether a sealed value already uses the current sealing key."""
    if not value or not value.startswith(PREFIX):
        return False
    try:
        Fernet(_keys()[0]).decrypt(value[len(PREFIX):].encode())
        return True
    except InvalidToken:
        return False


def reseal_all() -> dict:
    """Re-encrypt every stored token under the current key (run after a rotation).
    Values no listed key can open are left untouched and counted, never destroyed."""
    out = {"resealed": 0, "current": 0, "unreadable": 0}
    fernet = _fernet()
    with db.cursor() as cur:
        for table, key, column in SEALED_COLUMNS:
            try:
                rows = cur.execute(f"SELECT {key} AS k, {column} AS v FROM {table}").fetchall()
            except Exception:                               # noqa: BLE001 — table not created on this install
                continue
            for row in rows:
                value = row["v"]
                if not value:
                    continue
                if is_current(value):
                    out["current"] += 1
                    continue
                if value.startswith(PREFIX):
                    try:
                        fresh = PREFIX + fernet.rotate(value[len(PREFIX):].encode()).decode()
                    except InvalidToken:
                        out["unreadable"] += 1
                        continue
                else:                                       # plain text from before encryption existed
                    fresh = seal(value)
                cur.execute(f"UPDATE {table} SET {column}=? WHERE {key}=?", (fresh, row["k"]))
                out["resealed"] += 1
        secrets_rows = cur.execute("SELECT name, value FROM app_secrets WHERE name<>?", (LEGACY_NAME,)).fetchall()
    for row in secrets_rows:                               # generated keys (VAPID): re-sealed by reading them
        if row["value"].startswith(PREFIX) and not is_current(row["value"]):
            opened = unseal(row["value"])
            if opened is None:
                out["unreadable"] += 1
                continue
            with db.cursor() as cur:
                cur.execute("UPDATE app_secrets SET value=? WHERE name=?", (seal(opened), row["name"]))
            out["resealed"] += 1
        elif config.SECRET and not row["value"].startswith(PREFIX):
            stored(row["name"], lambda: "")
            out["resealed"] += 1
    if config.SECRET and not out["unreadable"]:
        # Everything now opens with the environment key: the trial key kept beside the data goes.
        with db.cursor() as cur:
            out["legacy_key_removed"] = bool(cur.execute("SELECT 1 FROM app_secrets WHERE name=?",
                                                         (LEGACY_NAME,)).fetchone())
            cur.execute("DELETE FROM app_secrets WHERE name=?", (LEGACY_NAME,))
    return out
