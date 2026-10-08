"""Regression tests for the Oct 2026 production incident.

Live on Render + Neon (pooled endpoint), every database endpoint answered Flask's
generic 500 after exactly 30 s a few minutes after each boot: `psycopg_pool.PoolTimeout`
in `db_pg.Connection.__init__`, with nothing else in the logs. /healthz (no database)
stayed 200 and the page sat on "Loading SmartPlate…". A pool must never get stuck like
that: a connection that fails its check is closed and replaced, a pool that cannot hand
out a connection is replaced once, every network wait is bounded, and when the database
really is unreachable the API says so at once (503, code=database_unavailable).
"""
import time

import pytest
from psycopg_pool import PoolTimeout

import pg_support
from smartplate import config, db, db_pg
from smartplate.app import create_app

needs_pg = pytest.mark.skipif(not pg_support.enabled(), reason="needs SMARTPLATE_TEST_DATABASE_URL (CI Postgres job)")


def test_database_url_loses_invisible_characters_pasted_from_a_web_page():
    # The URL first set on Render carried four U+202F inside the hostname: DNS failed.
    pasted = ("postgresql://user:secret@ep-x-pooler.c-4.ap\u202f\u202f\u202f\u202f-southeast-1.aws.neon.tech"
              "/neondb?sslmode=require&channel_binding=require\u00a0\u200b\n")
    assert config.clean_database_url(pasted) == (
        "postgresql://user:secret@ep-x-pooler.c-4.ap-southeast-1.aws.neon.tech"
        "/neondb?sslmode=require&channel_binding=require")
    assert config.clean_database_url("  postgresql://u@localhost/db  ") == "postgresql://u@localhost/db"


def test_every_network_wait_is_bounded():
    url, kwargs = db_pg.conninfo("postgresql://u:p@ep-x-pooler.example.neon.tech/neondb?sslmode=require")
    assert kwargs["connect_timeout"] <= 15
    assert kwargs["keepalives"] == 1 and kwargs["keepalives_idle"] <= 60
    assert 0 < kwargs["tcp_user_timeout"] <= 60000      # ms: a dead TCP flow fails, never hangs
    # A setting the URL names wins over the default.
    _, named = db_pg.conninfo("postgresql://u@db.example/x?sslmode=require&connect_timeout=3")
    assert "connect_timeout" not in named
    assert 0 < config.PG_POOL_TIMEOUT <= 15             # no more 30 s waits per request


def test_database_unavailable_answers_503_with_a_code_not_a_generic_500(seeded, monkeypatch):
    app = create_app()
    app.config["TESTING"] = True
    client = app.test_client()

    def unreachable():
        raise PoolTimeout("couldn't get a connection after 10.00 sec")
    monkeypatch.setattr(db, "connect", unreachable)
    for path in ("/api/meta", "/api/users"):
        response = client.get(path)
        assert response.status_code == 503, path
        body = response.get_json()
        assert body["code"] == "database_unavailable"
        assert "database" in body["error"]
        assert response.headers["Retry-After"]
    assert client.get("/healthz").status_code == 200    # liveness never touches the database


def _pooled():
    """The connections the current pool holds ready (pool internals, test only)."""
    return list(db_pg.pool()._pool)


@needs_pg
def test_a_connection_that_fails_its_check_is_closed_not_put_back(monkeypatch):
    """psycopg_pool puts a connection that fails its check but still looks IDLE back in
    the pool and checks it again, in a loop that ends in PoolTimeout (logged at INFO
    only). Ours closes it, so the pool opens a fresh one and the request goes on."""
    with db.cursor() as cur:                             # make sure the pool holds a connection
        cur.execute("SELECT 1")
    db_pg._last_used.clear()                             # it has been idle: the next checkout checks it
    poisoned = {id(c) for c in _pooled()}
    assert poisoned
    real_execute = db_pg.psycopg.Connection.execute

    def execute(self, query, *args, **kwargs):          # the pool's check runs execute("")
        if id(self) in poisoned and query == "":
            raise db_pg.psycopg.OperationalError("simulated: the check fails but the socket looks fine")
        return real_execute(self, query, *args, **kwargs)
    monkeypatch.setattr(db_pg.psycopg.Connection, "execute", execute)

    started = time.monotonic()
    with db.cursor() as cur:
        assert cur.execute("SELECT 41 + 1").fetchone()[0] == 42
    assert time.monotonic() - started < 5
    assert not poisoned & {id(c) for c in _pooled()}


@needs_pg
def test_a_pool_that_cannot_hand_out_a_connection_is_replaced_once(monkeypatch):
    stale = db_pg.pool()

    def stuck(timeout=None):
        raise PoolTimeout("couldn't get a connection after 10.00 sec")
    monkeypatch.setattr(stale, "getconn", stuck)

    with db.cursor() as cur:
        assert cur.execute("SELECT 1").fetchone()[0] == 1
    fresh = db_pg.pool()
    assert fresh is not stale
    with db.cursor() as cur:                             # the replacement keeps serving
        assert cur.execute("SELECT 2").fetchone()[0] == 2


@needs_pg
def test_a_connection_used_moments_ago_skips_the_check_round_trip(monkeypatch):
    with db.cursor() as cur:
        cur.execute("SELECT 1")
    checks = []
    real_execute = db_pg.psycopg.Connection.execute

    def execute(self, query, *args, **kwargs):
        if query == "":
            checks.append(1)
        return real_execute(self, query, *args, **kwargs)
    monkeypatch.setattr(db_pg.psycopg.Connection, "execute", execute)
    for _ in range(5):
        with db.cursor() as cur:
            cur.execute("SELECT 1")
    assert checks == []


@needs_pg
def test_the_server_closing_the_connection_does_not_break_the_next_request():
    with db.cursor() as cur:
        pid = cur.execute("SELECT pg_backend_pid()").fetchone()[0]
    pg_support._admin("SELECT pg_terminate_backend(%s)", pid)     # only this test's own connection
    started = time.monotonic()
    with db.cursor() as cur:
        assert cur.execute("SELECT 3").fetchone()[0] == 3
    assert time.monotonic() - started < 5


# --- Oct 8 2026, second production finding: gunicorn --preload --------------------------- #
# Render runs gunicorn with --preload (its default GUNICORN_CMD_ARGS also adds the access
# log). create_app() - the pool, the start-up migration's connection and the keep-alive -
# ran in the master, which then forked the worker. The master's keep-alive pinged a TLS
# connection the worker was also using: "SSL error: decryption failed or bad record mac"
# 45 s after boot, then a pool that could never open a replacement (its threads don't
# survive a fork) and a 503 after PG_POOL_TIMEOUT. Reproduced locally with an SSL Postgres
# and `GUNICORN_CMD_ARGS="--preload"`, byte for byte the production log lines.

def test_the_keepalive_starts_in_the_serving_process_never_at_start_up(monkeypatch):
    started = []
    monkeypatch.setattr(db_pg, "_keep_alive_loop", lambda stop: started.append(1))
    monkeypatch.setattr(db_pg, "_keeper", [])
    monkeypatch.setattr(db_pg, "_keepalive_wanted", [False])
    db_pg.ensure_keepalive()
    assert db_pg._keeper == []                           # not switched on: nothing runs
    db_pg.start_keepalive()                              # start-up (maybe a --preload master)
    assert db_pg._keeper == []                           # ... only switches it on
    db_pg.ensure_keepalive()                             # the first request this process serves
    db_pg.ensure_keepalive()
    assert len(db_pg._keeper) == 1
    db_pg._keeper[0][0].join(timeout=5)
    assert started == [1]


def test_a_forked_child_never_touches_its_parents_connections(monkeypatch):
    parent_pool = object()
    monkeypatch.setattr(db_pg, "_pools", {("postgresql://x", ""): parent_pool})
    monkeypatch.setattr(db_pg, "_keeper", [("parent-thread", None)])
    monkeypatch.setattr(db_pg, "_inherited", [])
    monkeypatch.setattr(db_pg, "_held", {1: ("t", 0.0)})
    monkeypatch.setattr(db_pg, "_last_used", {1: 0.0})
    monkeypatch.setattr(db_pg, "_pools_lock", db_pg._pools_lock)
    monkeypatch.setattr(db_pg, "_keeper_lock", db_pg._keeper_lock)
    db_pg._after_fork_in_child()
    assert db_pg._pools == {} and db_pg._held == {} and db_pg._last_used == {}
    assert db_pg._keeper == []                           # the child runs its own keep-alive
    assert db_pg._inherited == [parent_pool]             # kept, never used or closed


@needs_pg
def test_a_worker_forked_after_start_up_gets_its_own_connections():
    import os
    with db.cursor() as cur:                             # start-up in the parent opened a connection
        assert cur.execute("SELECT 1").fetchone()[0] == 1
    parent_pool = db_pg.pool()
    parent_conns = {id(c) for c in _pooled()}
    assert parent_conns
    read, write = os.pipe()
    pid = os.fork()
    if pid == 0:                                         # the "worker"
        os.close(read)
        status = b"ok"
        try:
            fresh = db_pg.pool()
            if fresh is parent_pool:
                status = b"inherited pool"
            with db.cursor() as cur:
                if cur.execute("SELECT 6 * 7").fetchone()[0] != 42:
                    status = b"bad answer"
            if parent_conns & {id(c) for c in fresh._pool}:
                status = b"used a parent connection"
        except BaseException as exc:                     # report, never let pytest run on in the child
            status = repr(exc).encode()[:300]
        os.write(write, status)
        os._exit(0)
    os.close(write)
    _, code = os.waitpid(pid, 0)
    with os.fdopen(read, "rb") as f:
        assert f.read() == b"ok"
    assert code == 0
    assert db_pg.pool() is parent_pool                   # the parent's pool is untouched ...
    with db.cursor() as cur:                             # ... and still serves
        assert cur.execute("SELECT 2").fetchone()[0] == 2
