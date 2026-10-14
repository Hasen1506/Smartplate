"""Browser sessions in an HttpOnly cookie, so no profile key sits in localStorage (grill #62).

Before: each private profile's key (or device token) lived in the browser's localStorage and
went out in the X-SmartPlate-Key header. Any script running on the page (an XSS bug, a bad
browser extension) could read it and open the profile from anywhere, for ever.

Now: the browser holds one opaque, signed session cookie that page scripts cannot read
(HttpOnly), that is only sent over HTTPS in production (Secure) and only on Ziggy's own
requests (SameSite=Lax). The server keeps which profiles each session may open
(`web_session_profiles`). The cookie value is a random token, signed with SMARTPLATE_SECRET
and stored only as a SHA-256, and it is replaced with a fresh one every day of use
(`ROTATE_AFTER`), so a copied cookie stops working soon after. A session unused for
`IDLE_TTL` ends.

Because a cookie is sent automatically, every state-changing request that relies on it must
also carry the session's CSRF token in the X-CSRF-Token header (double submit: the token is in
a readable `zg_csrf` cookie and in GET /api/session). Another site can make the browser send
the cookie, but cannot read the token.

Back-compatibility: the header key still works for browsers and scripts that have not moved
yet, and POST /api/session/claim moves a key into the session (the app does this once on
load, then forgets the key). A session only starts when the client asks for it
(`X-Ziggy-Session: cookie`), so API scripts keep their header-only behaviour.
"""
import datetime as dt
import hashlib
import hmac
import secrets

from itsdangerous import BadSignature, URLSafeSerializer

from . import clock, config, db

COOKIE = "zg_session"
CSRF_COOKIE = "zg_csrf"
CSRF_HEADER = "X-CSRF-Token"
OPT_IN_HEADER = "X-Ziggy-Session"
IDLE_TTL = dt.timedelta(days=60)
ROTATE_AFTER = dt.timedelta(days=1)
GRACE = dt.timedelta(minutes=2)          # the replaced token still works for requests already in flight

SCHEMA = """
CREATE TABLE IF NOT EXISTS web_sessions (        -- one per browser that opted into cookie sessions
    id INTEGER PRIMARY KEY,
    token_hash TEXT NOT NULL UNIQUE,               -- sha256 of the current cookie token
    prev_hash TEXT,                                -- the token it replaced, valid until prev_until
    prev_until TEXT,
    csrf TEXT NOT NULL,                            -- double-submit token for state-changing requests
    created_ts TEXT NOT NULL,
    rotated_ts TEXT NOT NULL,
    last_seen_ts TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS web_session_profiles ( -- which private profiles a browser session may open
    session_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    device_id INTEGER,                             -- set when opened by password sign-in (accounts.py)
    created_ts TEXT NOT NULL,
    PRIMARY KEY (session_id, user_id)
);
"""
_ready: set = set()


def init_schema() -> None:
    where = (config.DATABASE_URL, config.PG_SCHEMA, config.DB_PATH)
    if where in _ready:
        return
    with db.cursor() as cur:
        cur.executescript(SCHEMA)
    if config.DATABASE_URL:
        from . import db_pg
        db_pg.register_schema(SCHEMA)
    _ready.add(where)


def _signer() -> URLSafeSerializer:
    if config.SECRET:
        key = config.SECRET
    else:                                               # local trial: a generated key (production refuses this)
        from . import vault
        key = vault.stored("session_signing", lambda: secrets.token_urlsafe(32))
    return URLSafeSerializer(key, salt="ziggy-session-v1")


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _ts(value: dt.datetime) -> str:
    return value.isoformat(timespec="seconds")


def cookie_value(token: str) -> str:
    return _signer().dumps(token)


def _token_from(cookie: str | None) -> str | None:
    if not cookie or len(cookie) > 512:
        return None
    try:
        token = _signer().loads(cookie)
    except BadSignature:
        return None
    return token if isinstance(token, str) else None


def load(cookie: str | None) -> dict | None:
    """The live session a cookie names, or None. Marks it `rotate` when due for a new token."""
    token = _token_from(cookie)
    if not token:
        return None
    init_schema()
    hashed, now = _hash(token), clock.now()
    with db.cursor() as cur:
        row = cur.execute("SELECT * FROM web_sessions WHERE token_hash=?", (hashed,)).fetchone()
        if not row:
            row = cur.execute("SELECT * FROM web_sessions WHERE prev_hash=? AND prev_until>=?",
                              (hashed, _ts(now))).fetchone()
            if not row:
                return None
            current = False
        else:
            current = True
        if dt.datetime.fromisoformat(row["last_seen_ts"]) < now - IDLE_TTL:
            cur.execute("DELETE FROM web_session_profiles WHERE session_id=?", (row["id"],))
            cur.execute("DELETE FROM web_sessions WHERE id=?", (row["id"],))
            return None
        if dt.datetime.fromisoformat(row["last_seen_ts"]) < now - dt.timedelta(minutes=10):
            cur.execute("UPDATE web_sessions SET last_seen_ts=? WHERE id=?", (_ts(now), row["id"]))
    return {"id": row["id"], "csrf": row["csrf"], "token": token if current else None,
            "rotate": current and dt.datetime.fromisoformat(row["rotated_ts"]) < now - ROTATE_AFTER}


def start() -> tuple[dict, str]:
    """A new session; returns it and the token for the cookie."""
    init_schema()
    token, now = secrets.token_urlsafe(32), _ts(clock.now())
    with db.cursor() as cur:
        cur.execute("INSERT INTO web_sessions(token_hash, csrf, created_ts, rotated_ts, last_seen_ts) "
                    "VALUES (?,?,?,?,?)", (_hash(token), secrets.token_urlsafe(24), now, now, now))
        row = cur.execute("SELECT id, csrf FROM web_sessions WHERE token_hash=?", (_hash(token),)).fetchone()
    return {"id": row["id"], "csrf": row["csrf"], "token": token, "rotate": False}, token


def rotate(session: dict) -> str:
    """Give the session a fresh token; the old one keeps working for GRACE (in-flight requests)."""
    token, now = secrets.token_urlsafe(32), clock.now()
    with db.cursor() as cur:
        cur.execute("UPDATE web_sessions SET prev_hash=token_hash, prev_until=?, token_hash=?, rotated_ts=? "
                    "WHERE id=?", (_ts(now + GRACE), _hash(token), _ts(now), session["id"]))
    session.update(token=token, rotate=False)
    return token


def renew(session: dict) -> str:
    """A fresh token with no grace for the old one: after sign-in or a privilege change, so a
    token planted before the change (session fixation) is worthless afterwards."""
    token, now = secrets.token_urlsafe(32), clock.now()
    with db.cursor() as cur:
        cur.execute("UPDATE web_sessions SET prev_hash=NULL, prev_until=NULL, token_hash=?, rotated_ts=?, "
                    "csrf=? WHERE id=?", (_hash(token), _ts(now), secrets.token_urlsafe(24), session["id"]))
        session["csrf"] = cur.execute("SELECT csrf FROM web_sessions WHERE id=?", (session["id"],)).fetchone()["csrf"]
    session.update(token=token, rotate=False)
    return token


def bind(session: dict, user_id: int, device_id: int | None = None) -> None:
    with db.cursor() as cur:
        cur.execute("DELETE FROM web_session_profiles WHERE session_id=? AND user_id=?", (session["id"], user_id))
        cur.execute("INSERT INTO web_session_profiles(session_id, user_id, device_id, created_ts) VALUES (?,?,?,?)",
                    (session["id"], user_id, device_id, _ts(clock.now())))


def binding(session: dict | None, user_id: int) -> dict | None:
    """This session's hold on a profile, if it still stands. A hold that came from a password
    sign-in ends when that device is signed out or the password changes."""
    if not session:
        return None
    with db.cursor() as cur:
        row = cur.execute("SELECT device_id FROM web_session_profiles WHERE session_id=? AND user_id=?",
                          (session["id"], user_id)).fetchone()
        if not row:
            return None
        if row["device_id"] is not None and not cur.execute(
                "SELECT 1 FROM devices WHERE id=? AND user_id=?", (row["device_id"], user_id)).fetchone():
            cur.execute("DELETE FROM web_session_profiles WHERE session_id=? AND user_id=?", (session["id"], user_id))
            return None
    return {"device_id": row["device_id"]}


def grants(session: dict | None, user_id: int) -> bool:
    return binding(session, user_id) is not None


def profiles(session: dict | None) -> list[dict]:
    """The private profiles this browser may open, with their names (for the profile switcher)."""
    if not session:
        return []
    with db.cursor() as cur:
        rows = cur.execute("SELECT p.user_id, u.name FROM web_session_profiles p JOIN users u ON u.id=p.user_id "
                           "WHERE p.session_id=? ORDER BY p.created_ts, p.user_id", (session["id"],)).fetchall()
    return [{"id": r["user_id"], "name": r["name"]} for r in rows if grants(session, r["user_id"])]


def unbind(session: dict | None, user_id: int) -> int | None:
    """Forget a profile on this browser; returns the sign-in device it was opened with, if any."""
    if not session:
        return None
    with db.cursor() as cur:
        row = cur.execute("SELECT device_id FROM web_session_profiles WHERE session_id=? AND user_id=?",
                          (session["id"], user_id)).fetchone()
        cur.execute("DELETE FROM web_session_profiles WHERE session_id=? AND user_id=?", (session["id"], user_id))
    return row["device_id"] if row else None


def revoke_user(user_id: int, keep_session: int | None = None) -> None:
    """Every browser except `keep_session` loses this profile (key rotation, deletion)."""
    init_schema()
    with db.cursor() as cur:
        cur.execute("DELETE FROM web_session_profiles WHERE user_id=? AND session_id<>?", (user_id, keep_session or -1))


def csrf_ok(session: dict, presented: str | None) -> bool:
    return bool(presented) and hmac.compare_digest(session["csrf"], presented)


def purge_idle() -> int:
    """Delete sessions idle longer than IDLE_TTL (housekeeping)."""
    init_schema()
    cutoff = _ts(clock.now() - IDLE_TTL)
    with db.cursor() as cur:
        old = [r["id"] for r in cur.execute("SELECT id FROM web_sessions WHERE last_seen_ts<?", (cutoff,)).fetchall()]
        for sid in old:
            cur.execute("DELETE FROM web_session_profiles WHERE session_id=?", (sid,))
            cur.execute("DELETE FROM web_sessions WHERE id=?", (sid,))
    return len(old)
