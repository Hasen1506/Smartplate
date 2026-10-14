"""Cold start (grill #66) and observability (grill #67): a warm boot skips the schema
migration, the keep-alive lets Neon sleep when nobody uses the app, request ids, JSON
logs, latency lines and an optional, no-op-by-default Sentry."""
import io
import json
import logging
import threading

import pytest

from smartplate import config, db, db_pg, observability, runtime
from smartplate.app import create_app


@pytest.fixture
def client(seeded):
    app = create_app()
    app.config["TESTING"] = True
    return app.test_client()


# ---------------------------------------------------------------- cold start
def _count_statements(monkeypatch):
    seen = []
    real = db.connect

    def counting():
        conn = real()

        class Cur:
            def __init__(self, cur):
                self._cur = cur

            def execute(self, *a):
                seen.append(a[0])
                return self._cur.execute(*a)

            def executescript(self, script):
                seen.extend(["DDL"] * script.count("CREATE "))
                return self._cur.executescript(script)

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
    monkeypatch.setattr(db, "connect", counting)
    return seen


def test_a_warm_boot_skips_the_schema_migration(seeded, monkeypatch):
    runtime.initialize()                                   # first boot: migrates and marks
    seen = _count_statements(monkeypatch)
    runtime.initialize()                                   # every later boot
    assert not any(s == "DDL" or "PRAGMA table_info" in s for s in seen)
    assert len(seen) < 30, len(seen)


def test_a_changed_schema_runs_the_migration_again(seeded, monkeypatch):
    runtime.initialize()
    monkeypatch.setattr(db, "MIGRATIONS", db.MIGRATIONS + [("users", "w09_probe", "TEXT")])
    runtime.initialize()
    with db.cursor() as cur:
        assert "w09_probe" in {r["name"] for r in cur.execute("PRAGMA table_info(users)")}


def test_a_missing_or_unreadable_mark_means_migrate(seeded, monkeypatch):
    assert db.schema_is_current("not-the-fingerprint") is False
    monkeypatch.setattr(db, "connect", lambda: (_ for _ in ()).throw(RuntimeError("no db")))
    assert db.schema_is_current("anything") is False


def test_healthz_never_touches_the_database(client, monkeypatch):
    def no_db():
        raise AssertionError("healthz must not open a database connection")
    monkeypatch.setattr(db, "connect", no_db)
    assert client.get("/healthz").status_code == 200


def test_keepalive_lets_the_database_sleep_when_nobody_uses_the_app(monkeypatch):
    pinged, stop = [], threading.Event()

    class Pool:
        def getconn(self, timeout=None):
            pinged.append(1)
            raise RuntimeError("not a real pool")
    monkeypatch.setattr(db_pg, "pool", lambda: Pool())
    monkeypatch.setattr(db_pg, "KEEPALIVE_S", 0.01)
    monkeypatch.setattr(db_pg, "KEEPALIVE_IDLE_S", 600)
    clock = [1000.0]
    monkeypatch.setattr(db_pg.time, "monotonic", lambda: clock[0])
    db_pg.note_activity()
    clock[0] += 601                                        # 10 min with no person using the app
    t = threading.Thread(target=db_pg._keep_alive_loop, args=(stop,), daemon=True)
    t.start()
    threading.Event().wait(0.1)
    assert pinged == []
    db_pg.note_activity()                                  # someone opens the app
    for _ in range(200):
        if pinged:
            break
        threading.Event().wait(0.01)
    stop.set()
    t.join(2)
    assert pinged


def test_cron_and_health_checks_are_not_activity(client, monkeypatch):
    from smartplate import ops
    assert all(p.startswith(ops.NOT_ACTIVITY) for p in ("/cron/tick", "/healthz", "/admin/metrics"))
    assert not "/api/user/1/plan".startswith(ops.NOT_ACTIVITY)


# ---------------------------------------------------------------- observability
def test_every_response_has_a_request_id_and_honours_a_safe_incoming_one(client):
    r = client.get("/healthz")
    assert len(r.headers["X-Request-ID"]) >= 16
    assert client.get("/healthz", headers={"X-Request-ID": "abc-123-def"}).headers["X-Request-ID"] == "abc-123-def"
    hostile = "<script>alert(1)</script> " + "x" * 100
    assert client.get("/healthz", headers={"X-Request-ID": hostile}).headers["X-Request-ID"] != hostile


def test_a_5xx_answer_carries_its_request_id(client, monkeypatch):
    import psycopg
    from smartplate import service

    def down(*a, **kw):
        raise psycopg.OperationalError("db down")
    monkeypatch.setattr(service, "current_plan", down)
    monkeypatch.setattr(db, "on_stale_connection", lambda: None)
    r = client.get("/api/user/1/plan", headers={"X-Request-ID": "trace-me-42"})
    assert r.status_code == 503 and r.get_json()["request_id"] == "trace-me-42"


def test_json_logs_carry_request_id_route_status_and_latency(client, monkeypatch):
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.addFilter(observability.RequestIdFilter())
    handler.setFormatter(observability.JsonFormatter())
    logging.getLogger("smartplate").addHandler(handler)
    try:
        client.get("/api/user/1/plan", headers={"X-Request-ID": "log-me-0001"})
    finally:
        logging.getLogger("smartplate").removeHandler(handler)
    lines = [json.loads(line) for line in stream.getvalue().splitlines()]
    req = [entry for entry in lines if entry.get("event") == "request"]
    assert req and req[-1]["request_id"] == "log-me-0001"
    assert req[-1]["route"] == "/api/user/<int:user_id>/plan" and req[-1]["status"] == 200
    assert isinstance(req[-1]["ms"], float) and req[-1]["level"] == "INFO"


def test_slow_requests_are_warnings(client, monkeypatch, caplog):
    monkeypatch.setattr(config, "SLOW_REQUEST_MS", 0)
    with caplog.at_level(logging.INFO, logger="smartplate.http"):
        client.get("/api/users")
    assert any(r.levelno == logging.WARNING and "/api/users" in r.getMessage() for r in caplog.records)


def test_timed_logs_outbound_calls(caplog):
    with caplog.at_level(logging.INFO, logger="smartplate.swiggy"):
        with observability.timed("swiggy", tool="search_restaurants", session="s1") as info:
            info["status"] = 200
        with pytest.raises(TimeoutError):
            with observability.timed("swiggy", tool="get_menu"):
                raise TimeoutError
    fields = [r.fields for r in caplog.records if hasattr(r, "fields")]
    assert fields[0]["tool"] == "search_restaurants" and fields[0]["status"] == 200 and "ms" in fields[0]
    assert fields[1]["status"] == "TimeoutError"


def test_sentry_is_a_no_op_without_a_dsn(monkeypatch):
    monkeypatch.setattr(config, "SENTRY_DSN", "")
    assert observability.init_sentry() is False


def test_sentry_dsn_without_the_package_warns_and_carries_on(monkeypatch, caplog):
    import builtins
    real_import = builtins.__import__

    def no_sentry(name, *a, **kw):
        if name.startswith("sentry_sdk"):
            raise ImportError(name)
        return real_import(name, *a, **kw)
    monkeypatch.setattr(builtins, "__import__", no_sentry)
    assert observability.init_sentry("https://key@o0.ingest.sentry.io/0") is False
