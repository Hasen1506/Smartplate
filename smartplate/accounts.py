"""Sign-in for private profiles: a name and password that open the profile anywhere.

Private profiles start with a browser-held key (access.py). Adding a sign-in lets the
person open the same profile on a phone and a laptop without copying a recovery code.
Each successful sign-in creates a *device token*: a random key for that browser, stored
only as a SHA-256. The access check accepts the profile key or any of its device tokens,
and "Sign out" deletes one.

Deliberately simple for a trial: no email, so nothing to verify and nothing is ever
sent. Forgotten password → open the profile with its recovery code (or any signed-in
device) and set a new one. Sample profiles are shared, so they can't have a sign-in.
"""
import re
import secrets

from werkzeug.security import check_password_hash, generate_password_hash

from . import access, clock, db, ratelimit

LOGIN_RE = re.compile(r"^[a-z0-9._@+-]{3,64}$")
MIN_PASSWORD = 8
MAX_PASSWORD = 200
FAIL_LIMIT, FAIL_WINDOW_S = 5, 15 * 60                     # per sign-in name
IP_LIMIT, IP_WINDOW_S = 30, 15 * 60                        # per address, any name
_DUMMY = generate_password_hash("not-a-real-password")    # equal work for unknown names


def _norm(login) -> str:
    login = (login or "").strip().lower() if isinstance(login, str) else ""
    if not LOGIN_RE.match(login):
        raise ValueError("Use 3–64 letters, numbers or . _ @ + - for your sign-in name")
    return login


def _password(pw) -> str:
    if not isinstance(pw, str) or not (MIN_PASSWORD <= len(pw) <= MAX_PASSWORD):
        raise ValueError(f"Use a password of at least {MIN_PASSWORD} characters")
    return pw


def _now() -> str:
    return clock.now().isoformat(timespec="seconds")


def summary(user_id: int, presented: str | None = None) -> dict:
    here = access.digest(presented) if presented else None
    with db.cursor() as cur:
        user = cur.execute("SELECT access_hash FROM users WHERE id=?", (user_id,)).fetchone()
        row = cur.execute("SELECT login FROM logins WHERE user_id=?", (user_id,)).fetchone()
        devices = cur.execute("SELECT id, label, created_ts, last_seen_ts, token_hash FROM devices "
                              "WHERE user_id=? ORDER BY last_seen_ts DESC", (user_id,)).fetchall()
    return {"private": bool(user and user["access_hash"]), "login": row["login"] if row else None,
            "devices": [{"id": d["id"], "label": d["label"] or "Browser", "since": d["created_ts"],
                         "last_seen": d["last_seen_ts"], "this_device": d["token_hash"] == here} for d in devices]}


def set_login(user_id: int, body: dict, presented: str | None = None) -> dict:
    """Create or change the sign-in. The caller already proved access to the profile."""
    with db.cursor() as cur:
        user = cur.execute("SELECT access_hash FROM users WHERE id=?", (user_id,)).fetchone()
    if not user or not user["access_hash"]:
        raise ValueError("Sample profiles are shared, so they can't have a sign-in. Set up your own profile first.")
    login, pw = _norm(body.get("login")), _password(body.get("password"))
    with db.cursor() as cur:
        taken = cur.execute("SELECT user_id FROM logins WHERE login=?", (login,)).fetchone()
        if taken and taken["user_id"] != user_id:
            raise ValueError("That sign-in name is taken. Try another.")
        now = _now()
        cur.execute("INSERT INTO logins(user_id, login, pw_hash, created_ts, updated_ts) VALUES (?,?,?,?,?) "
                    "ON CONFLICT(user_id) DO UPDATE SET login=excluded.login, pw_hash=excluded.pw_hash, "
                    "updated_ts=excluded.updated_ts", (user_id, login, generate_password_hash(pw), now, now))
    return summary(user_id, presented)


def sign_in(body: dict, ip: str = "") -> dict:
    """Name + password → a new device token for this browser."""
    ratelimit.check(f"signin-ip:{ip}", IP_LIMIT, IP_WINDOW_S)
    raw = body.get("login")
    login = raw.strip().lower() if isinstance(raw, str) else ""
    pw = body.get("password") if isinstance(body.get("password"), str) else ""
    ratelimit.check(f"signin:{login}", FAIL_LIMIT, FAIL_WINDOW_S, record=False)
    with db.cursor() as cur:
        row = cur.execute("SELECT l.user_id, l.pw_hash, u.name FROM logins l JOIN users u ON u.id=l.user_id "
                          "WHERE l.login=?", (login,)).fetchone()
    if not check_password_hash(row["pw_hash"] if row else _DUMMY, pw[:MAX_PASSWORD]) or not row:
        ratelimit.hit(f"signin:{login}")
        raise ValueError("That name and password don't match.")
    token = secrets.token_urlsafe(24)
    label = body.get("device") if isinstance(body.get("device"), str) else ""
    now = _now()
    with db.cursor() as cur:
        cur.execute("INSERT INTO devices(user_id, token_hash, label, created_ts, last_seen_ts) VALUES (?,?,?,?,?)",
                    (row["user_id"], access.digest(token), label.strip()[:60], now, now))
    return {"user_id": row["user_id"], "name": row["name"], "key": token}


def remove_device(user_id: int, device_id: int) -> dict:
    with db.cursor() as cur:
        cur.execute("DELETE FROM devices WHERE id=? AND user_id=?", (device_id, user_id))
    return summary(user_id)


def sign_out(user_id: int, presented: str | None) -> dict:
    """Forget this browser's device token (the profile key itself can't be revoked)."""
    if presented:
        with db.cursor() as cur:
            cur.execute("DELETE FROM devices WHERE user_id=? AND token_hash=?", (user_id, access.digest(presented)))
    return {"ok": True}
