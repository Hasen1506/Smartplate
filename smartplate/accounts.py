"""Sign-in for private profiles: a name and password that open the profile anywhere.

Private profiles start with a browser-held key (access.py). Adding a sign-in lets the
person open the same profile on a phone and a laptop without copying a recovery code.
Each successful sign-in creates a *device token*: a random key for that browser, stored
only as a SHA-256. The access check accepts the profile key or any of its device tokens,
and "Sign out" deletes one.

Deliberately simple for a trial: new sign-ins use an email address as the identifier
(validated, unique, case-insensitive), but nothing is verified and nothing is ever sent.
Older accounts with a free-form name keep signing in with it. Forgotten password → open the profile with its recovery code (or any signed-in
device) and set a new one. Sample profiles are shared, so they can't have a sign-in.
"""
import re
import secrets

from werkzeug.security import check_password_hash, generate_password_hash

from . import access, clock, db, ratelimit

LOGIN_RE = re.compile(r"^[a-z0-9._@+-]{3,64}$")         # legacy free-form names: still sign in
# New sign-ins are an email (Oct 2026): one identifier people recognise, so nobody wonders
# which account they're on. Stored lower-case in the same unique `login` column, so it is
# unique and case-insensitive, and old name-only accounts keep working unchanged.
EMAIL_RE = re.compile(r"^[a-z0-9.!#$%&'*+/=?^_`{|}~-]{1,64}@[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
                      r"(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*\.[a-z]{2,63}$")
MAX_EMAIL = 254
MIN_PASSWORD = 8
MAX_PASSWORD = 200
FAIL_LIMIT, FAIL_WINDOW_S = 5, 15 * 60                     # per sign-in name from one address
NAME_LIMIT, NAME_WINDOW_S = 60, 60 * 60                    # per sign-in name from all addresses
IP_LIMIT, IP_WINDOW_S = 30, 15 * 60                        # per address, any name
_DUMMY = generate_password_hash("not-a-real-password")    # equal work for unknown names


def _norm(login) -> str:
    login = (login or "").strip().lower() if isinstance(login, str) else ""
    if not LOGIN_RE.match(login):
        raise ValueError("Use 3–64 letters, numbers or . _ @ + - for your sign-in name")
    return login


def _email(email) -> str:
    email = (email or "").strip().lower() if isinstance(email, str) else ""
    if len(email) > MAX_EMAIL or ".." in email or not EMAIL_RE.match(email):
        raise ValueError("Enter a valid email address, like name@example.com")
    return email


def _identifier(body: dict) -> tuple[str, bool]:
    """The sign-in a request chooses: `email` (current) or the legacy free-form `login`."""
    if body.get("email") is not None:
        return _email(body.get("email")), True
    return _norm(body.get("login")), False


def _taken(is_email: bool) -> str:
    return ("That email already has a Ziggy account. Sign in with it instead." if is_email
            else "That sign-in name is taken. Try another.")


def _password(pw) -> str:
    if not isinstance(pw, str) or not (MIN_PASSWORD <= len(pw) <= MAX_PASSWORD):
        raise ValueError(f"Use a password of at least {MIN_PASSWORD} characters")
    return pw


def _now() -> str:
    return clock.now().isoformat(timespec="seconds")


def check_new_login(login, password, email=None) -> tuple[str, str]:
    """Validate a sign-in chosen while creating a profile, before anything is written.
    `email` wins when given; `login` is the legacy free-form name."""
    login, is_email = _identifier({"email": email, "login": login})
    pw = _password(password)
    with db.cursor() as cur:
        if cur.execute("SELECT 1 FROM logins WHERE login=?", (login,)).fetchone():
            raise ValueError(_taken(is_email))
    return login, pw


def insert_login(cur, user_id: int, login: str, password: str) -> None:
    now = _now()
    try:
        cur.execute("INSERT INTO logins(user_id, login, pw_hash, created_ts, updated_ts) VALUES (?,?,?,?,?)",
                    (user_id, login, generate_password_hash(password), now, now))
    except db.IntegrityError as exc:                      # taken between the check and the insert
        raise ValueError("That sign-in name is taken. Try another.") from exc


def summary(user_id: int, presented: str | None = None) -> dict:
    here = access.digest(presented) if presented else None
    with db.cursor() as cur:
        user = cur.execute("SELECT access_hash FROM users WHERE id=?", (user_id,)).fetchone()
        row = cur.execute("SELECT login FROM logins WHERE user_id=?", (user_id,)).fetchone()
        devices = cur.execute("SELECT id, label, created_ts, last_seen_ts, token_hash FROM devices "
                              "WHERE user_id=? ORDER BY last_seen_ts DESC", (user_id,)).fetchall()
    login = row["login"] if row else None
    return {"private": bool(user and user["access_hash"]), "login": login,
            "email": login if login and EMAIL_RE.match(login) else None,
            "devices": [{"id": d["id"], "label": d["label"] or "Browser", "since": d["created_ts"],
                         "last_seen": d["last_seen_ts"], "this_device": d["token_hash"] == here} for d in devices]}


def set_login(user_id: int, body: dict, presented: str | None = None) -> dict:
    """Create or change the sign-in. The caller already proved access to the profile."""
    with db.cursor() as cur:
        user = cur.execute("SELECT access_hash FROM users WHERE id=?", (user_id,)).fetchone()
    if not user or not user["access_hash"]:
        raise ValueError("Sample profiles are shared, so they can't have a sign-in. Set up your own profile first.")
    login, is_email = _identifier(body)
    pw = _password(body.get("password"))
    with db.cursor() as cur:
        taken = cur.execute("SELECT user_id FROM logins WHERE login=?", (login,)).fetchone()
        if taken and taken["user_id"] != user_id:
            raise ValueError(_taken(is_email))
        existing = cur.execute("SELECT pw_hash FROM logins WHERE user_id=?", (user_id,)).fetchone()
        now = _now()
        cur.execute("INSERT INTO logins(user_id, login, pw_hash, created_ts, updated_ts) VALUES (?,?,?,?,?) "
                    "ON CONFLICT(user_id) DO UPDATE SET login=excluded.login, pw_hash=excluded.pw_hash, "
                    "updated_ts=excluded.updated_ts", (user_id, login, generate_password_hash(pw), now, now))
        if existing and not check_password_hash(existing["pw_hash"], pw):
            # A new password signs out every other browser (L-03): a leaked password
            # must not keep working through the device tokens it already issued.
            keep = access.digest(presented) if presented else ""
            cur.execute("DELETE FROM devices WHERE user_id=? AND token_hash<>?", (user_id, keep))
    return summary(user_id, presented)


def sign_in(body: dict, ip: str = "") -> dict:
    """Name + password → a new device token for this browser."""
    ratelimit.check(f"signin-ip:{ip}", IP_LIMIT, IP_WINDOW_S)
    by_email = isinstance(body.get("email"), str)
    raw = body.get("email") if by_email else body.get("login")
    login = raw.strip().lower() if isinstance(raw, str) else ""        # case-insensitive, both kinds
    pw = body.get("password") if isinstance(body.get("password"), str) else ""
    # Failures count per (name, address), so a stranger elsewhere cannot lock the owner
    # out (L-04); a much higher per-name ceiling still slows distributed guessing.
    ratelimit.check(f"signin:{login}:{ip}", FAIL_LIMIT, FAIL_WINDOW_S, record=False)
    ratelimit.check(f"signin-name:{login}", NAME_LIMIT, NAME_WINDOW_S, record=False)
    with db.cursor() as cur:
        row = cur.execute("SELECT l.user_id, l.pw_hash, u.name FROM logins l JOIN users u ON u.id=l.user_id "
                          "WHERE l.login=?", (login,)).fetchone()
    if not check_password_hash(row["pw_hash"] if row else _DUMMY, pw[:MAX_PASSWORD]) or not row:
        ratelimit.hit(f"signin:{login}:{ip}")
        ratelimit.hit(f"signin-name:{login}")
        raise ValueError("That email and password don't match." if by_email
                         else "That name and password don't match.")
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
    """Forget this browser's device token; root codes rotate through rotate_key."""
    if presented:
        with db.cursor() as cur:
            cur.execute("DELETE FROM devices WHERE user_id=? AND token_hash=?", (user_id, access.digest(presented)))
    return {"ok": True}


def rotate_key(user_id: int, confirmation):
    if confirmation != "ROTATE":
        raise ValueError("Confirm recovery code rotation")
    key, hashed = access.new_key()
    with db.cursor() as cur:
        cur.execute("BEGIN IMMEDIATE")
        user = cur.execute("SELECT name, access_hash FROM users WHERE id=?", (user_id,)).fetchone()
        if not user or not user["access_hash"]:
            raise ValueError("Recovery codes are only available for private profiles")
        cur.execute("UPDATE users SET access_hash=? WHERE id=?", (hashed, user_id))
        cur.execute("DELETE FROM devices WHERE user_id=?", (user_id,))
        cur.execute("DELETE FROM swiggy_pending WHERE user_id=?", (user_id,))
        cur.execute("DELETE FROM swiggy_checkout_quotes WHERE user_id=?", (user_id,))
    return {"user_id": user_id, "key": key, "name": user["name"]}
