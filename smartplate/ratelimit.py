"""Rate limits kept in the database, so they hold across restarts, redeploys, Render's
sleep and more than one worker (grill #63). Works the same on SQLite and Postgres.

Each key is a token bucket: `limit` tokens that refill evenly over `window_s` seconds
(so 5 per 15 minutes = one new try every 3 minutes, never a burst of 5 after a reset).
A request spends one token; with none left it is refused with the wait until the next.

One UPDATE both refills and spends, guarded by "enough tokens", so two requests racing
for the last token cannot both win (row lock on Postgres, database lock on SQLite).
"""
import secrets
import time

from . import config, db

SCHEMA = """
CREATE TABLE IF NOT EXISTS rate_buckets (        -- token buckets for sign-in, profile creation, Swiggy quotas
    key TEXT PRIMARY KEY,
    tokens REAL NOT NULL,
    updated REAL NOT NULL                          -- unix seconds of the last refill
);
"""
STALE_S = 2 * 86400            # a bucket untouched this long is full again and can go
_now = time.time               # tests replace this
_ready: set = set()
_namespace = ""                # reset() starts a fresh namespace without touching any database


class TooMany(Exception):
    def __init__(self, wait_s: int):
        super().__init__(f"Too many tries. Wait {max(1, -(-wait_s // 60))} minute(s) and try again.")
        self.wait_s = wait_s


def _ensure() -> None:
    where = (config.DATABASE_URL, config.PG_SCHEMA, config.DB_PATH)
    if where in _ready:
        return
    with db.cursor() as cur:
        cur.executescript(SCHEMA)
    _ready.add(where)


def init_schema() -> None:
    _ensure()


def _key(key: str) -> str:
    return f"{_namespace}{key}"[:400]


def _refilled(tokens: float, updated: float, now: float, limit: int, window_s: float) -> float:
    return min(float(limit), tokens + max(0.0, now - updated) * limit / window_s)


def _wait(tokens: float, limit: int, window_s: float) -> int:
    return int((1 - tokens) * window_s / limit) + 1


def check(key: str, limit: int, window_s: int, *, record: bool = True) -> None:
    """Raise TooMany if `key` has no token left; else spend one (unless record=False)."""
    _ensure()
    k, now, rate = _key(key), _now(), limit / window_s
    with db.cursor() as cur:
        row = cur.execute("SELECT tokens, updated FROM rate_buckets WHERE key=?", (k,)).fetchone()
        if row is None:
            cur.execute("DELETE FROM rate_buckets WHERE updated < ?", (now - STALE_S,))
            cur.execute("INSERT OR IGNORE INTO rate_buckets(key, tokens, updated) VALUES (?,?,?)",
                        (k, float(limit), now))
            row = cur.execute("SELECT tokens, updated FROM rate_buckets WHERE key=?", (k,)).fetchone()
        if not record:
            tokens = _refilled(row["tokens"], row["updated"], now, limit, window_s)
            if tokens < 1:
                raise TooMany(_wait(tokens, limit, window_s))
            return
        # Refill and spend in one guarded statement (CASE, not min(): same SQL on both databases).
        refill = "(tokens + (CASE WHEN ? > updated THEN ? - updated ELSE 0 END) * ?)"
        capped = f"(CASE WHEN {refill} > ? THEN ? ELSE {refill} END)"
        cur.execute(f"UPDATE rate_buckets SET tokens = {capped} - 1, updated = ? WHERE key = ? AND {capped} >= 1",
                    (now, now, rate, float(limit), float(limit), now, now, rate,
                     now, k,
                     now, now, rate, float(limit), float(limit), now, now, rate))
        if cur.rowcount == 1:
            return
        row = cur.execute("SELECT tokens, updated FROM rate_buckets WHERE key=?", (k,)).fetchone()
    raise TooMany(_wait(_refilled(row["tokens"], row["updated"], now, limit, window_s), limit, window_s))


def hit(key: str, limit: int, window_s: int) -> None:
    """Spend one token without checking (a failed sign-in counted after the fact). Never below zero."""
    _ensure()
    k, now = _key(key), _now()
    with db.cursor() as cur:
        cur.execute("INSERT OR IGNORE INTO rate_buckets(key, tokens, updated) VALUES (?,?,?)", (k, float(limit), now))
        row = cur.execute("SELECT tokens, updated FROM rate_buckets WHERE key=?", (k,)).fetchone()
        tokens = _refilled(row["tokens"], row["updated"], now, limit, window_s)
        cur.execute("UPDATE rate_buckets SET tokens=?, updated=? WHERE key=?",
                    (max(0.0, tokens - 1), now, k))


def reset() -> None:
    """Forget every bucket for this process (tests). Starts a new key namespace instead of
    deleting rows, so it never opens a database the caller has not configured yet."""
    global _namespace
    _namespace = secrets.token_hex(4) + ":"
