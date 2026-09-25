"""Server-side secrets: generated keys and encryption of stored third-party tokens.

`seal()` encrypts a token with Fernet (AES-128-CBC + HMAC-SHA256). The key comes from
SMARTPLATE_SECRET when set (render.yaml generates one); otherwise a random key is made
once and kept in `app_secrets`. That fallback keeps a trial working with zero setup,
but it stores the key beside the data, so production must set SMARTPLATE_SECRET.
"""
import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from . import config, db

PREFIX = "v1:"


def stored(name: str, make) -> str:
    """A named generated secret, created on first use and then kept."""
    with db.cursor() as cur:
        row = cur.execute("SELECT value FROM app_secrets WHERE name=?", (name,)).fetchone()
        if row:
            return row["value"]
        value = make()
        cur.execute("INSERT OR IGNORE INTO app_secrets(name, value) VALUES (?,?)", (name, value))
        return cur.execute("SELECT value FROM app_secrets WHERE name=?", (name,)).fetchone()["value"]


def _fernet() -> Fernet:
    if config.SECRET:
        key = base64.urlsafe_b64encode(hashlib.sha256(config.SECRET.encode()).digest())
    else:
        key = stored("fernet", lambda: Fernet.generate_key().decode()).encode()
    return Fernet(key)


def seal(text: str) -> str:
    return PREFIX + _fernet().encrypt(text.encode()).decode()


def unseal(value: str | None) -> str | None:
    """Decrypt a sealed value. Tokens saved before encryption existed pass through;
    a value sealed under a different key returns None (treat as signed out)."""
    if not value:
        return None
    if not value.startswith(PREFIX):
        return value
    try:
        return _fernet().decrypt(value[len(PREFIX):].encode()).decode()
    except InvalidToken:
        return None
