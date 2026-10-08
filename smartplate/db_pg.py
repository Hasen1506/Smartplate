"""Postgres backend (DATABASE_URL), e.g. Neon's free tier.

The app writes its SQL once, in SQLite's dialect (db.py). This module serves pooled
Postgres connections whose cursors accept that dialect and behave like sqlite3's:

* `?` placeholders, `INSERT OR IGNORE`, `INSERT OR REPLACE`, `LIKE` (case-insensitive
  in SQLite, so ILIKE here), `BEGIN IMMEDIATE` (one writer at a time: an advisory lock),
  `PRAGMA table_info(t)` and `executescript()` of the CREATE TABLE schema;
* types: INTEGER -> BIGINT, REAL -> DOUBLE PRECISION (Postgres REAL is only 4 bytes),
  `id INTEGER PRIMARY KEY` -> an identity column, and `cursor.lastrowid` after an INSERT;
* rows indexable by position or (case-insensitive) column name, like sqlite3.Row;
* integers are sent untyped, as SQLite would accept them, so `text_col = 5` works.

JSON is stored as TEXT on both databases (db.jd / db.jl), so JSON columns need no
translation. There are no BLOB columns.

Pooling suits Neon's free tier: connections are checked before use (Neon closes them
when its compute scales to zero) and a connection that fails its check is closed, never
put back; idle ones are closed after a minute and none is kept open for its own sake (so
Neon can scale to zero), TLS is required (sslmode=require unless the URL sets it), and
server-side prepared statements are off so Neon's pooled (-pooler) endpoint, which runs
PgBouncer, works too.

No database call can hang a request (Oct 2026 incident: every DB endpoint answered a
generic 500 after 30 s, a few minutes after each boot, with nothing in the logs but
`PoolTimeout`). Connects time out, TCP keepalives and tcp_user_timeout end any socket
whose peer vanished silently, a request waits at most PG_POOL_TIMEOUT for a connection,
and a pool that cannot hand one out is logged (stats + who holds connections) and
replaced once before the request gives up with OperationalError (503 in app.py).
"""
from __future__ import annotations

import decimal
import logging
import re
import threading
import time
from contextlib import suppress

import psycopg
from psycopg import adapt, pq
from psycopg_pool import ConnectionPool, PoolTimeout

from . import config

log = logging.getLogger("smartplate.db")
WRITE_LOCK_KEY = 0x53504C54          # "SPLT": BEGIN IMMEDIATE = one writer at a time, as in SQLite

# --------------------------------------------------------------------------- #
# Schema knowledge: primary keys (for INSERT OR REPLACE) and identity tables
# (for lastrowid). Filled from every CREATE TABLE this process translates and
# completed from the live catalogue when a pool opens.
# --------------------------------------------------------------------------- #
_PRIMARY_KEYS: dict[str, tuple[str, ...]] = {}
_IDENTITY_TABLES: set[str] = set()

_CREATE_RE = re.compile(r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)\s*\((.*)\)\s*$", re.I | re.S)


def _strip_comments(sql: str) -> str:
    out, i, n, quote = [], 0, len(sql), False
    while i < n:
        ch = sql[i]
        if quote:
            out.append(ch)
            if ch == "'":
                quote = False
        elif ch == "'":
            quote = True
            out.append(ch)
        elif sql.startswith("--", i):
            while i < n and sql[i] != "\n":
                i += 1
            continue
        else:
            out.append(ch)
        i += 1
    return "".join(out)


def _split_top_level(body: str) -> list[str]:
    parts, depth, cur, quote = [], 0, [], False
    for ch in body:
        if quote:
            quote = ch != "'"
        elif ch == "'":
            quote = True
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == "," and depth == 0:
            parts.append("".join(cur).strip())
            cur = []
            continue
        cur.append(ch)
    if "".join(cur).strip():
        parts.append("".join(cur).strip())
    return parts


def _column_type(decl: str) -> str:
    """SQLite column declaration -> Postgres. TEXT gets the C collation: byte order, as
    SQLite's BINARY, so ORDER BY on text sorts the same on both databases whatever the
    server's locale."""
    def fix(segment):
        segment = re.sub(r"\bINTEGER\b", "BIGINT", segment, flags=re.I)
        segment = re.sub(r"\bREAL\b", "DOUBLE PRECISION", segment, flags=re.I)
        segment = re.sub(r"\bBLOB\b", "BYTEA", segment, flags=re.I)
        return re.sub(r"\bTEXT\b", 'TEXT COLLATE "C"', segment, flags=re.I)
    return _rewrite_unquoted(decl, fix)


def translate_ddl(statement: str) -> str:
    """Translate one CREATE TABLE statement and record its keys."""
    text = _strip_comments(statement).strip()
    m = _CREATE_RE.match(text)
    if not m:
        return _column_type(text)
    table, body = m.group(1).lower(), m.group(2)
    columns = []
    for part in _split_top_level(body):
        pk = re.match(r"PRIMARY\s+KEY\s*\(([^)]*)\)", part, re.I)
        if pk:
            _PRIMARY_KEYS[table] = tuple(c.strip().lower() for c in pk.group(1).split(","))
            columns.append(part)
            continue
        name = part.split()[0].lower()
        if re.search(r"\bPRIMARY\s+KEY\b", part, re.I):
            _PRIMARY_KEYS[table] = (name,)
            if name == "id" and re.match(r"id\s+INTEGER\s+PRIMARY\s+KEY\s*$", part, re.I):
                _IDENTITY_TABLES.add(table)
                columns.append("id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY")
                continue
        columns.append(_column_type(part))
    return f"CREATE TABLE IF NOT EXISTS {table} (\n    " + ",\n    ".join(columns) + "\n)"


def register_schema(script: str) -> None:
    for statement in _strip_comments(script).split(";"):
        if statement.strip():
            translate_ddl(statement)


# --------------------------------------------------------------------------- #
# Statement translation (SQLite dialect -> Postgres)
# --------------------------------------------------------------------------- #
def _outside_quotes(sql: str):
    """Yield (segment, quoted) pieces of `sql`."""
    i, n = 0, len(sql)
    while i < n:
        if sql[i] == "'":
            j = i + 1
            while j < n:
                if sql[j] == "'" and (j + 1 >= n or sql[j + 1] != "'"):
                    break
                j += 2 if sql[j] == "'" else 1
            yield sql[i:j + 1], True
            i = j + 1
        else:
            j = sql.find("'", i)
            j = n if j < 0 else j
            yield sql[i:j], False
            i = j


def _rewrite_unquoted(sql: str, fn) -> str:
    return "".join(seg if quoted else fn(seg) for seg, quoted in _outside_quotes(sql))


_INSERT_RE = re.compile(r"^\s*INSERT\s+(?:OR\s+(IGNORE|REPLACE)\s+)?INTO\s+(\w+)\s*\(([^)]*)\)", re.I | re.S)


class Translated:
    __slots__ = ("sql", "returning_id", "explicit_id_table", "noop")

    def __init__(self, sql, returning_id=False, explicit_id_table=None, noop=False):
        self.sql, self.returning_id, self.explicit_id_table, self.noop = sql, returning_id, explicit_id_table, noop


_CACHE: dict[tuple[str, bool], Translated] = {}


def translate(sql: str, many: bool = False) -> Translated:
    key = (sql, many)
    hit = _CACHE.get(key)
    if hit is None:
        hit = _CACHE[key] = _translate(sql, many)
    return hit


def _translate(sql: str, many: bool) -> Translated:
    stripped = sql.strip().rstrip(";").strip()
    upper = stripped.upper()
    if re.fullmatch(r"BEGIN(\s+(DEFERRED|TRANSACTION))?", upper):
        # a read-only snapshot (profile export): one consistent view, as in SQLite
        return Translated("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
    if re.fullmatch(r"BEGIN\s+(IMMEDIATE|EXCLUSIVE)(\s+TRANSACTION)?", upper):
        # SQLite takes the write lock here; serialise writers the same way
        return Translated(f"SELECT pg_advisory_xact_lock({WRITE_LOCK_KEY})")
    m = re.fullmatch(r"PRAGMA\s+table_info\s*\(\s*(\w+)\s*\)", stripped, re.I)
    if m:
        return Translated("SELECT column_name AS name, data_type AS type FROM information_schema.columns "
                          f"WHERE table_schema = current_schema() AND table_name = '{m.group(1).lower()}' "
                          "ORDER BY ordinal_position")
    if upper.startswith("PRAGMA"):
        return Translated("", noop=True)
    if upper.startswith("CREATE TABLE"):
        return Translated(translate_ddl(stripped))
    m = re.fullmatch(r"ALTER\s+TABLE\s+(\w+)\s+ADD\s+COLUMN\s+(.*)", stripped, re.I | re.S)
    if m:
        return Translated(f"ALTER TABLE {m.group(1)} ADD COLUMN IF NOT EXISTS {_column_type(m.group(2))}")

    out = stripped
    returning_id, explicit_id_table = False, None
    ins = _INSERT_RE.match(out)
    if ins:
        kind = (ins.group(1) or "").upper()
        table = ins.group(2).lower()
        cols = [c.strip().lower() for c in ins.group(3).split(",")]
        out = out[:ins.start()] + f"INSERT INTO {ins.group(2)}({ins.group(3)})" + out[ins.end():]
        if kind == "IGNORE":
            out += " ON CONFLICT DO NOTHING"
        elif kind == "REPLACE":
            keys = _PRIMARY_KEYS.get(table)
            if not keys:
                raise psycopg.ProgrammingError(f"INSERT OR REPLACE into {table}: primary key unknown")
            updates = [c for c in cols if c not in keys]
            if updates:
                out += (f" ON CONFLICT ({', '.join(keys)}) DO UPDATE SET "
                        + ", ".join(f"{c}=EXCLUDED.{c}" for c in updates))
            else:
                out += " ON CONFLICT DO NOTHING"
        if table in _IDENTITY_TABLES:
            if "id" in cols:
                explicit_id_table = table
            elif not many and not re.search(r"\bRETURNING\b", out, re.I):
                out += " RETURNING id"
                returning_id = True

    def fix(segment: str) -> str:
        segment = re.sub(r"\bNOT\s+LIKE\b", "NOT ILIKE", segment, flags=re.I)
        segment = re.sub(r"(?<!NOT )\bLIKE\b", "ILIKE", segment, flags=re.I)
        segment = re.sub(r"\bAS\s+REAL\b", "AS DOUBLE PRECISION", segment, flags=re.I)
        segment = re.sub(r"\bAS\s+INTEGER\b", "AS BIGINT", segment, flags=re.I)
        return segment.replace("%", "%%").replace("?", "%s")

    # quoted literals keep their text, but a literal % must still be escaped for psycopg
    out = "".join(seg.replace("%", "%%") if quoted else fix(seg) for seg, quoted in _outside_quotes(out))
    return Translated(out, returning_id, explicit_id_table)


# --------------------------------------------------------------------------- #
# Rows and value adaptation
# --------------------------------------------------------------------------- #
class Row(tuple):
    """A result row like sqlite3.Row: row[0], row["name"] (any case), keys(), dict(row)."""

    _names: tuple = ()
    _index: dict = {}

    def __getitem__(self, key):
        if isinstance(key, str):
            try:
                return tuple.__getitem__(self, self._index[key.lower()])
            except KeyError:
                raise IndexError("No item with that key") from None
        return tuple.__getitem__(self, key)

    def keys(self):
        return list(self._names)


def _row_factory(cursor):
    desc = cursor.description
    if desc is None:
        return tuple
    names = tuple(c.name for c in desc)
    cls = type("Row", (Row,), {"_names": names, "_index": {n.lower(): i for i, n in reversed(list(enumerate(names)))}})
    return lambda values: tuple.__new__(cls, values)


class _UntypedIntDumper(adapt.Dumper):
    """Send ints as untyped literals: Postgres then reads them as whatever the column
    is (SQLite-style leniency), instead of rejecting `text_col = 5`."""
    oid = 0
    format = pq.Format.TEXT

    def dump(self, obj):
        return str(int(obj)).encode()


class _NumericLoader(adapt.Loader):
    """SUM()/AVG() of BIGINT are NUMERIC in Postgres; SQLite returns int or float."""
    format = pq.Format.TEXT

    def load(self, data):
        value = decimal.Decimal(bytes(data).decode())
        return int(value) if value == value.to_integral_value() else float(value)


class _BytesLoader(adapt.Loader):
    format = pq.Format.TEXT

    def load(self, data):
        return bytes.fromhex(bytes(data)[2:].decode()) if bytes(data).startswith(b"\\x") else bytes(data)


def _configure(conn: psycopg.Connection) -> None:
    conn.adapters.register_dumper(int, _UntypedIntDumper)
    conn.adapters.register_dumper(bool, _UntypedIntDumper)
    conn.adapters.register_loader("numeric", _NumericLoader)
    conn.adapters.register_loader("bytea", _BytesLoader)


# --------------------------------------------------------------------------- #
# Connection pool
# --------------------------------------------------------------------------- #
# libpq settings that bound every network wait. Neon (and the NAT in front of a free
# Render instance) can drop an idle TCP flow without telling either end; without these a
# read on such a socket blocks until the kernel gives up, minutes later.
NETWORK_KWARGS = {"connect_timeout": 10, "keepalives": 1, "keepalives_idle": 30,
                  "keepalives_interval": 10, "keepalives_count": 3, "tcp_user_timeout": 30000}


def conninfo(url: str, schema: str = "") -> tuple[str, dict]:
    """The libpq URL and keyword arguments for one pool. Settings the URL names win."""
    kwargs: dict = {"application_name": "smartplate"}
    for key, value in NETWORK_KWARGS.items():
        if f"{key}=" not in url:
            kwargs[key] = value
    if "sslmode=" not in url and not re.search(r"@(localhost|127\.0\.0\.1|\[::1\])[:/]", url):
        kwargs["sslmode"] = "require"
    if schema:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", schema):
            raise ValueError("SMARTPLATE_PG_SCHEMA must be a plain identifier")
        kwargs["options"] = f"-c search_path={schema}"
    return url, kwargs


CHECK_AFTER_IDLE_S = 5.0
_last_used: dict[int, float] = {}            # id(connection) -> monotonic time it was returned healthy


def check_connection(conn: psycopg.Connection) -> None:
    """The pool's check before it hands out a connection. One that fails is CLOSED, so the
    pool discards it and opens a fresh one. (psycopg_pool's own check only raises: a
    connection whose socket still looks fine is put straight back and checked again, in a
    loop that ends in PoolTimeout with nothing but INFO lines. Fresh connections work.)

    A connection returned healthy within the last CHECK_AFTER_IDLE_S seconds is handed out
    without the extra round-trip: one Today load checks out ~30 connections in a row, and a
    check each time doubled its database round-trips. Idle drops (the Neon pooler's) happen
    after much longer idles, and the keep-alive finds them off the request path."""
    used = _last_used.get(id(conn))
    if used is not None and time.monotonic() - used < CHECK_AFTER_IDLE_S and not conn.closed \
            and conn.info.transaction_status == pq.TransactionStatus.IDLE:
        return
    try:
        ConnectionPool.check_connection(conn)
    except Exception as exc:
        log.warning("database connection failed its check (%s: %s); closing it",
                    type(exc).__name__, str(exc).strip()[:200])
        with suppress(Exception):
            conn.close()
        raise


_pools: dict[tuple[str, str], ConnectionPool] = {}
_pools_lock = threading.Lock()
_held: dict[int, tuple[str, float]] = {}          # id(connection) -> (thread name, since)
_logged_url = [False]


def _new_pool(key: tuple[str, str]) -> ConnectionPool:
    url, kwargs = conninfo(*key)
    return ConnectionPool(
        url, min_size=0, max_size=max(1, config.PG_POOL_MAX), max_idle=60, max_lifetime=600,
        timeout=config.PG_POOL_TIMEOUT, reconnect_timeout=60, check=check_connection, configure=_configure,
        kwargs={**kwargs, "autocommit": False, "prepare_threshold": None, "row_factory": _row_factory},
        name="smartplate", open=True)


def pool() -> ConnectionPool:
    key = (config.DATABASE_URL, config.PG_SCHEMA)
    with _pools_lock:
        found = _pools.get(key)
        if found is None:
            for old_key in [k for k in _pools if k[0] == key[0] and k != key]:
                _pools.pop(old_key).close()          # tests switch schemas; drop the old pool
            if getattr(config, "DATABASE_URL_CLEANED", False) and not _logged_url[0]:
                _logged_url[0] = True
                log.warning("DATABASE_URL contained spaces or invisible characters; they were removed. "
                            "Re-paste it in the host's settings to tidy it.")
            from . import db
            register_schema(db.SCHEMA)
            from .integrations import swiggy_connect
            register_schema(swiggy_connect.SCHEMA)
            found = _new_pool(key)
            _pools[key] = found
        return found


def replace_pool(stale: ConnectionPool) -> ConnectionPool:
    """Swap a pool that could not hand out a connection for a new one (the state right
    after boot, which works). Connections still out return to the old pool, which closes
    them. Closing runs in the background so this request does not wait for it."""
    key = (config.DATABASE_URL, config.PG_SCHEMA)
    with _pools_lock:
        fresh = _pools.get(key)
        if fresh is None or fresh is stale:      # another thread may have replaced it already
            fresh = _pools[key] = _new_pool(key)
    if fresh is not stale:
        threading.Thread(target=lambda: stale.close(timeout=5), name="smartplate-pool-close",
                         daemon=True).start()
    return fresh


def _report_timeout(pool_: ConnectionPool) -> None:
    now = time.monotonic()
    holders = sorted(((name, round(now - since, 1)) for name, since in list(_held.values())),
                     key=lambda h: -h[1])
    log.warning("no database connection within %.0f s; pool stats %s; held by %s",
                pool_.timeout, pool_.get_stats(), holders[:8] or "nobody")


KEEPALIVE_S = 45.0
_keeper: list = []


def warm() -> float:
    """Open and check one pooled connection now (boot), so the first request doesn't pay
    for the TLS handshake. Returns the seconds it took."""
    t0 = time.monotonic()
    conn = Connection(pool())
    try:
        conn.execute("SELECT 1")
        conn.commit()
    finally:
        conn.close()
    return time.monotonic() - t0


def _keep_alive_loop(stop: threading.Event) -> None:
    while not stop.wait(KEEPALIVE_S):
        current = pool()
        try:
            conn = current.getconn(timeout=3)      # the check hook closes a dropped connection
            try:
                conn.execute("SELECT 1")
                conn.rollback()
            finally:
                current.putconn(conn)
        except PoolTimeout:
            log.warning("keep-alive: no database connection within 3 s; replacing the pool")
            replace_pool(current)
        except Exception as exc:                 # never let the keeper die
            log.warning("keep-alive ping failed (%s: %s)", type(exc).__name__, str(exc).strip()[:200])


def start_keepalive() -> None:
    """Ping the pool every KEEPALIVE_S seconds in the background. The Neon pooler drops idle
    TLS connections from its side (Oct 2026 logs: SSL EOF / bad record mac); before this, the
    first request after such a drop waited the whole pool timeout while the pool was
    replaced. The ping keeps one connection in use and finds a dead pool off the request
    path. It runs only while the web instance is awake (Render's free plan sleeps it)."""
    if _keeper:
        return
    stop = threading.Event()
    t = threading.Thread(target=_keep_alive_loop, args=(stop,), name="smartplate-db-keepalive", daemon=True)
    _keeper.append((t, stop))
    t.start()


def close_pools() -> None:
    with _pools_lock:
        while _pools:
            _pools.popitem()[1].close()


class Connection:
    """A pooled connection with sqlite3.Connection's small surface."""

    def __init__(self, pool_: ConnectionPool):
        try:
            conn = pool_.getconn()
        except PoolTimeout:
            _report_timeout(pool_)
            pool_ = replace_pool(pool_)
            conn = pool_.getconn()           # one bounded try on a fresh pool, then PoolTimeout -> 503
        self._pool = pool_
        self._conn = conn
        self._first = True                   # nothing sent yet: a dead connection can be swapped
        _held[id(conn)] = (threading.current_thread().name, time.monotonic())

    def cursor(self) -> "Cursor":
        return Cursor(self._conn, owner=self)

    def _swap_dead(self) -> psycopg.Connection | None:
        """Before anything was sent on this connection: if it turns out dead (it was handed
        out without a check because it was used moments ago), discard it and take a
        checked one. Returns the new connection, or None when the old one is alive (a real
        SQL error that must surface)."""
        old = self._conn
        if not (old.closed or old.info.status == pq.ConnStatus.BAD):
            return None
        _held.pop(id(old), None)
        _last_used.pop(id(old), None)
        with suppress(Exception):
            self._pool.putconn(old)
        self._conn = self._pool.getconn()
        _held[id(self._conn)] = (threading.current_thread().name, time.monotonic())
        return self._conn

    def execute(self, sql, params=()):
        return self.cursor().execute(sql, params)

    def commit(self):
        if self._conn.info.transaction_status == pq.TransactionStatus.INERROR:
            self._conn.rollback()           # a statement failed and was handled: nothing to keep
        else:
            self._conn.commit()

    def rollback(self):
        if not self._conn.closed:
            self._conn.rollback()

    def close(self):
        if self._conn is not None:
            conn, self._conn = self._conn, None
            _held.pop(id(conn), None)
            try:
                if not conn.closed and conn.info.transaction_status != pq.TransactionStatus.IDLE:
                    conn.rollback()
            except Exception:                # a dead connection: the pool discards it
                with suppress(Exception):
                    conn.close()
            finally:
                if not conn.closed and conn.info.transaction_status == pq.TransactionStatus.IDLE:
                    _last_used[id(conn)] = time.monotonic()
                else:
                    _last_used.pop(id(conn), None)
                self._pool.putconn(conn)


def connect() -> Connection:
    return Connection(pool())


class Cursor:
    """sqlite3.Cursor's surface over a psycopg cursor."""

    def __init__(self, conn: psycopg.Connection, owner: "Connection | None" = None):
        self._conn = conn
        self._owner = owner
        self._cur = conn.cursor()
        self.lastrowid = None
        self._pending = None          # the row consumed to read RETURNING id

    def _run(self, fn):
        owner = self._owner
        if owner is None or not owner._first:
            return fn()
        try:
            out = fn()
        except psycopg.OperationalError:
            fresh = owner._swap_dead()
            if fresh is None:
                raise
            self._conn, self._cur = fresh, fresh.cursor()
            out = fn()
        owner._first = False
        return out

    @property
    def rowcount(self):
        return self._cur.rowcount

    @property
    def description(self):
        return self._cur.description

    def execute(self, sql, params=()):
        t = translate(sql)
        self._pending = None
        if t.noop:
            return self
        params = tuple(params or ())
        self._run(lambda: self._cur.execute(t.sql, params))
        if t.returning_id:
            row = self._cur.fetchone()
            self.lastrowid = row[0] if row else self.lastrowid
        if t.explicit_id_table:
            self._sync_identity(t.explicit_id_table)
        return self

    def executemany(self, sql, seq):
        t = translate(sql, many=True)
        seq = [tuple(p) for p in seq]
        if t.noop or not seq:
            return self
        self._run(lambda: self._cur.executemany(t.sql, seq))
        if t.explicit_id_table:
            self._sync_identity(t.explicit_id_table)
        return self

    def executescript(self, script):
        register_schema(script)
        for statement in _strip_comments(script).split(";"):
            if statement.strip():
                self.execute(statement)
        return self

    def _sync_identity(self, table: str) -> None:
        # rows inserted with an explicit id must not collide with the next generated one
        with self._conn.cursor(row_factory=psycopg.rows.tuple_row) as c:
            c.execute(f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
                      f"COALESCE((SELECT MAX(id) FROM {table}), 0) + 1, false)")

    def fetchone(self):
        if self._cur.description is None or self._cur.pgresult is None:
            return None
        return self._cur.fetchone()

    def fetchall(self):
        if self._cur.description is None:
            return []
        return self._cur.fetchall()

    def fetchmany(self, size=1):
        return self._cur.fetchmany(size) if self._cur.description is not None else []

    def __iter__(self):
        if self._cur.description is None:
            return iter(())
        return iter(self._cur)

    def close(self):
        self._cur.close()


IntegrityError = psycopg.IntegrityError
