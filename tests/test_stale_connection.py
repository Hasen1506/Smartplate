"""Grill #66 / founder: "after 5 min of no use, typing to save something - no storage
occurs?" Reproduced with a database whose connections all go stale after an idle spell
(as Neon's do when it suspends after 5 minutes), then fixed by replaying a request that
committed nothing on a fresh pool (smartplate/resilience.py)."""
import json
import sqlite3

import psycopg
import pytest

from smartplate import db, resilience
from smartplate.app import create_app


class NeonLikeDatabase:
    """db.connect() whose connections fail with the error psycopg raises for a connection
    the server closed, from the moment `idle()` is called until the pool is replaced."""

    def __init__(self, real_connect):
        self.real_connect, self.stale, self.replaced, self.failures = real_connect, False, 0, 0

    def idle(self, minutes=6):
        self.stale = minutes >= 5

    def replace_pool(self):
        self.replaced += 1
        self.stale = False

    def connect(self):
        conn = self.real_connect()
        outer = self

        class Cur:
            def __init__(self, cur):
                self._cur = cur

            def execute(self, *a):
                if outer.stale:
                    outer.failures += 1
                    raise psycopg.OperationalError("SSL connection has been closed unexpectedly")
                self._cur.execute(*a)
                return self

            def __getattr__(self, name):
                return getattr(self._cur, name)

            def __iter__(self):
                return iter(self._cur)

        class Conn:
            def cursor(self):
                return Cur(conn.cursor())

            def __getattr__(self, name):
                return getattr(conn, name)
        return Conn()


@pytest.fixture
def neon(seeded, monkeypatch):
    fake = NeonLikeDatabase(db.connect)
    monkeypatch.setattr(db, "connect", fake.connect)
    monkeypatch.setattr(db, "_changes", lambda c: c.total_changes)
    monkeypatch.setattr(db, "on_stale_connection", fake.replace_pool)
    return fake


@pytest.fixture
def app(neon):
    application = create_app()
    application.config["TESTING"] = True
    return application


def _budget(user_id=1):
    with db.cursor() as cur:
        return cur.execute("SELECT weekly_budget FROM users WHERE id=?", (user_id,)).fetchone()["weekly_budget"]


def test_reproduced_without_the_fix_a_save_after_idle_is_lost(app, neon):
    app.wsgi_app = app.wsgi_app.app                     # the app as it was: no replay
    neon.idle(minutes=6)
    r = app.test_client().patch("/api/user/1", json={"weekly_budget": 2345})
    assert r.status_code == 503 and r.get_json()["code"] == "database_unavailable"
    neon.replace_pool()
    assert _budget() != 2345                            # nothing was stored


def test_a_save_after_idle_is_stored(app, neon):
    neon.idle(minutes=6)
    r = app.test_client().patch("/api/user/1", json={"weekly_budget": 2345})
    assert r.status_code == 200, r.get_json()
    assert neon.replaced == 1 and neon.failures >= 1
    assert _budget() == 2345


def test_a_short_pause_needs_no_replay(app, neon):
    neon.idle(minutes=2)
    assert app.test_client().patch("/api/user/1", json={"weekly_budget": 2222}).status_code == 200
    assert neon.replaced == 0


def test_a_database_that_stays_down_still_answers_503_after_one_replay(app, neon, monkeypatch):
    monkeypatch.setattr(neon, "replace_pool", lambda: setattr(neon, "replaced", neon.replaced + 1))
    monkeypatch.setattr(db, "on_stale_connection", neon.replace_pool)
    neon.idle()
    r = app.test_client().patch("/api/user/1", json={"weekly_budget": 2222})
    assert r.status_code == 503 and neon.replaced == 1           # one retry, never a loop


def _wsgi(status, body, *, write=False, calls=None):
    def app(environ, start_response):
        if calls is not None:
            calls.append(environ["wsgi.input"].read())
        if write:
            db._writes.set(db._writes.get() + 1)
        start_response(status, [("Content-Type", "application/json")])
        return [json.dumps(body).encode()]
    return app


def _call(wsgi, path="/api/user/1", body=b'{"name":"n"}'):
    import io
    out = {}
    environ = {"REQUEST_METHOD": "PATCH", "PATH_INFO": path, "CONTENT_LENGTH": str(len(body)),
               "wsgi.input": io.BytesIO(body)}
    chunks = wsgi(environ, lambda s, h, e=None: out.setdefault("status", s))
    return out["status"], b"".join(chunks)


def test_replay_resends_the_same_body_and_never_after_a_committed_write():
    calls, replaced = [], []
    down = _wsgi("503 SERVICE UNAVAILABLE", {"code": "database_unavailable"}, calls=calls)
    status, _ = _call(resilience.RetryStaleDatabase(down, on_stale=lambda: replaced.append(1)))
    assert status.startswith("503") and calls == [b'{"name":"n"}', b'{"name":"n"}'] and replaced == [1]

    calls.clear()
    wrote = _wsgi("503 SERVICE UNAVAILABLE", {"code": "database_unavailable"}, write=True, calls=calls)
    _call(resilience.RetryStaleDatabase(wrote, on_stale=lambda: None))
    assert len(calls) == 1                                       # something was committed: no replay

    calls.clear()
    _call(resilience.RetryStaleDatabase(down, on_stale=lambda: None), path="/api/user/1/swiggy/checkout")
    assert len(calls) == 1                                       # talks to Swiggy: never replayed

    calls.clear()
    other = _wsgi("503 SERVICE UNAVAILABLE", {"code": "swiggy_down"}, calls=calls)
    _call(resilience.RetryStaleDatabase(other, on_stale=lambda: None))
    assert len(calls) == 1                                       # not a database failure


def test_write_counter_counts_commits_with_changes_only(seeded):
    db.reset_writes()
    with db.cursor() as cur:
        cur.execute("SELECT 1 FROM users").fetchone()
    assert db.writes_committed() == 0
    with db.cursor() as cur:
        cur.execute("UPDATE users SET name=name WHERE id=1")
    assert db.writes_committed() == 1
    with pytest.raises(RuntimeError):
        with db.cursor() as cur:
            cur.execute("UPDATE users SET name='rolled back' WHERE id=1")
            raise RuntimeError
    with db.cursor() as cur:
        assert cur.execute("SELECT name FROM users WHERE id=1").fetchone()["name"] != "rolled back"
    assert db.writes_committed() == 1


def test_postgres_cursor_marks_writes_but_not_reads():
    from smartplate import db_pg
    assert db_pg._READ_ONLY.match("SELECT * FROM users")
    assert not db_pg._READ_ONLY.match("INSERT INTO users(name) VALUES ($1)")
    assert not db_pg._READ_ONLY.match("UPDATE users SET name=$1")
    assert not db_pg._READ_ONLY.match("DELETE FROM users")
