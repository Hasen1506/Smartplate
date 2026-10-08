"""Durable storage: Postgres through DATABASE_URL (e.g. Neon's free tier), SQLite otherwise.

* The data a person saved survives a restart: a new app process, built against the same
  database, serves exactly the same records (runs on both engines in CI).
* With DATABASE_URL set, the health checks and /api/meta say persistent=true, so the
  data-erasure banner (static/app.js, shown only for persistent === false) goes away.
* The SQL the app sends to Postgres is the translation of its SQLite dialect; the
  dialect checks below run on every run, with or without a Postgres server.
* scripts/sqlite_to_postgres.py copies a SQLite database into Postgres losslessly
  (needs the CI Postgres service: SMARTPLATE_TEST_DATABASE_URL).
"""
import ast
import json
import os
import pathlib
import sqlite3
import subprocess
import sys

import pytest

import pg_support
from smartplate import config, db, db_pg
from smartplate.app import create_app

REPO = pathlib.Path(__file__).resolve().parent.parent


@pytest.fixture
def client(seeded):
    app = create_app()
    app.config["TESTING"] = True
    return app.test_client()


def _private_profile(client, name="Kept across restarts"):
    made = client.post("/api/profiles", json={"name": name, "diet": "veg", "weekly_budget": 1800,
                                              "meals": ["lunch", "dinner"], "favourites": [6]}).get_json()
    uid, headers = made["user"]["id"], {"X-SmartPlate-Key": made["access_key"]}
    assert client.post(f"/api/user/{uid}/account", headers=headers,
                       json={"login": f"restart-{uid}", "password": "a-long-password"}).status_code == 200
    assert client.patch(f"/api/user/{uid}/setup", headers=headers, json={"allergens": ["peanut"]}).status_code == 200
    return uid, headers


RESTARTED_PROCESS = r"""
import json, sys
from smartplate.app import create_app
from smartplate import db
uid, key = int(sys.argv[1]), sys.argv[2]
client = create_app().test_client()               # a new process: nothing in memory carried over
r = client.get(f"/api/user/{uid}/data.json", headers={"X-SmartPlate-Key": key})
with db.cursor() as cur:
    users = cur.execute("SELECT COUNT(*) FROM users").fetchone()[0]
signin = client.post("/api/signin", json={"login": f"restart-{uid}", "password": "a-long-password"})
print(json.dumps({"status": r.status_code, "export": r.get_json(), "users": users,
                  "signin": signin.status_code, "engine": db.engine()}))
"""


def _restart_and_read(uid, key):
    env = {**os.environ, "SMARTPLATE_DB": config.DB_PATH, "SMARTPLATE_WEATHER": "simulated",
           "SMARTPLATE_PUSH": "off", "PYTHONPATH": str(REPO)}
    env.pop("RENDER", None)
    if config.DATABASE_URL:
        env.update(DATABASE_URL=config.DATABASE_URL, SMARTPLATE_PG_SCHEMA=config.PG_SCHEMA)
    else:
        env.pop("DATABASE_URL", None)
    done = subprocess.run([sys.executable, "-c", RESTARTED_PROCESS, str(uid), key], cwd=REPO, env=env,
                          capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr[-2000:]
    return json.loads(done.stdout.strip().splitlines()[-1])


def test_saved_profile_survives_an_app_restart(client):
    uid, headers = _private_profile(client)
    before = client.get(f"/api/user/{uid}/data.json", headers=headers).get_json()
    with db.cursor() as cur:
        users = cur.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    if db.engine() == "postgres":
        db_pg.close_pools()                              # this process lets go of every connection

    after = _restart_and_read(uid, headers["X-SmartPlate-Key"])

    assert after["engine"] == db.engine()
    assert after["status"] == 200
    assert after["export"]["data"] == before["data"]     # every saved record, unchanged
    assert after["users"] == users                       # nothing re-seeded or duplicated
    assert after["signin"] == 200                        # the password sign-in survived too
    assert before["data"]["profile"]["name"] == "Kept across restarts"
    assert json.loads(before["data"]["profile"]["allergens"]) == ["peanut"]
    # a second restart also keeps what the first restarted process wrote: its sign-in device
    again = _restart_and_read(uid, headers["X-SmartPlate-Key"])
    devices = again["export"]["data"].pop("devices")
    assert len(devices) == 1 and devices[0]["user_id"] == uid
    assert again["export"]["data"] == {k: v for k, v in before["data"].items() if k != "devices"}
    assert again["users"] == users


def test_database_url_reports_persistent_storage(monkeypatch, client):
    test_db, test_url = config.DB_PATH, config.DATABASE_URL
    monkeypatch.setenv("RENDER", "true")                 # the free Render instance, temporary disk
    monkeypatch.setattr(config, "DB_PATH", "smartplate.db")
    monkeypatch.setattr(config, "DB_PERSISTENT", "")
    monkeypatch.setattr(config, "DATABASE_URL", "")
    assert config.storage_status()["persistent"] is False
    neon = "postgresql://user@ep-example-pooler.ap-southeast-1.aws.neon.tech/neondb?sslmode=require"
    monkeypatch.setattr(config, "DATABASE_URL", neon)
    status = config.storage_status()
    assert status["persistent"] is True and status["engine"] == "postgres"
    # the endpoints read the same status; their own queries go to this test's database
    monkeypatch.setattr(config, "DB_PATH", test_db)
    if test_url:
        monkeypatch.setattr(config, "DATABASE_URL", test_url)
    else:
        real_connect = db.connect
        monkeypatch.setattr(db_pg, "connect", lambda: _sqlite(real_connect, monkeypatch))
    assert client.get("/healthz").get_json()["database"]["persistent"] is True
    meta = client.get("/api/meta").get_json()["storage"]
    assert meta["persistent"] is True                    # app.js hides the banner unless persistent is false


def _sqlite(real_connect, monkeypatch):
    with monkeypatch.context() as m:
        m.setattr(config, "DATABASE_URL", "")
        return real_connect()


def test_readyz_needs_no_local_file_on_postgres(monkeypatch, client):
    if db.engine() != "postgres":
        monkeypatch.setattr(config, "DB_PATH", "/nonexistent/dir/smartplate.db")
        assert client.get("/readyz").status_code == 503      # SQLite: the file must be writable
        return
    monkeypatch.setattr(config, "DB_PATH", "/nonexistent/dir/smartplate.db")
    r = client.get("/readyz")
    assert r.status_code == 200 and r.get_json()["database"]["engine"] == "postgres"


def test_render_blueprint_declares_database_url_unsynced():
    text = (REPO / "render.yaml").read_text()
    block = text[text.index("- key: DATABASE_URL"):].split("- key:")[1]
    assert "sync: false" in block and "value:" not in block   # the secret is set in the dashboard, never committed


# --------------------------------------------------------------------------- #
# Postgres dialect checks (no server needed)
# --------------------------------------------------------------------------- #
def test_schema_translates_types_keys_and_collation():
    db_pg.register_schema(db.SCHEMA)
    users = db_pg.translate("CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, name TEXT NOT NULL, "
                            "weekly_budget REAL NOT NULL DEFAULT 2000, household_id INTEGER)").sql
    assert "id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY" in users
    assert 'name TEXT COLLATE "C" NOT NULL' in users            # byte order, as SQLite's BINARY
    assert "weekly_budget DOUBLE PRECISION" in users             # Postgres REAL would be 4-byte
    assert "household_id BIGINT" in users
    alter = db_pg.translate("ALTER TABLE users ADD COLUMN prefs TEXT NOT NULL DEFAULT '{}'").sql
    assert alter == "ALTER TABLE users ADD COLUMN IF NOT EXISTS prefs TEXT COLLATE \"C\" NOT NULL DEFAULT '{}'"


def test_statements_translate_to_postgres():
    from smartplate.integrations import swiggy_connect
    db_pg.register_schema(db.SCHEMA)
    db_pg.register_schema(swiggy_connect.SCHEMA)
    t = db_pg.translate
    assert t("INSERT INTO users(name, city) VALUES (?, ?)").sql == \
        "INSERT INTO users(name, city) VALUES (%s, %s) RETURNING id"
    assert t("INSERT INTO users(name, city) VALUES (?, ?)").returning_id
    assert t("INSERT OR IGNORE INTO grocery_have(plan_id, item) VALUES (?,?)").sql.endswith("ON CONFLICT DO NOTHING")
    upsert = t("INSERT OR REPLACE INTO swiggy_menus(user_id, restaurant, payload, fetched_ts) VALUES (?,?,?,?)").sql
    assert upsert.endswith("ON CONFLICT (user_id, restaurant) DO UPDATE SET payload=EXCLUDED.payload, "
                           "fetched_ts=EXCLUDED.fetched_ts")
    assert t("SELECT * FROM x WHERE a LIKE '%?%' AND b=?").sql == "SELECT * FROM x WHERE a ILIKE '%%?%%' AND b=%s"
    assert t("BEGIN IMMEDIATE").sql.startswith("SELECT pg_advisory_xact_lock(")
    assert "information_schema.columns" in t("PRAGMA table_info(users)").sql
    assert t("PRAGMA foreign_keys = ON").noop
    assert t("INSERT INTO orders(id, decision_id) VALUES (?,?)").explicit_id_table == "orders"


def test_rows_behave_like_sqlite_rows():
    cls = type("Row", (db_pg.Row,), {"_names": ("id", "Name"), "_index": {"id": 0, "name": 1}})
    row = tuple.__new__(cls, (7, "Asha"))
    assert row[0] == 7 and row["name"] == "Asha" and row["NAME"] == "Asha"
    assert dict(zip(row.keys(), row)) == {"id": 7, "Name": "Asha"} and db.row_to_dict(row) == {"id": 7, "Name": "Asha"}
    assert db_pg._NumericLoader(0).load(b"12") == 12 and db_pg._NumericLoader(0).load(b"12.5") == 12.5


UNPORTABLE = ("json_extract", "json_each", "group_concat", "ifnull(", "datetime(", "julianday(",
              "strftime(", "last_insert_rowid", "sqlite_master", "autoincrement", " glob ")


def _app_sql():
    for path in sorted((REPO / "smartplate").rglob("*.py")):
        if path.name == "db_pg.py":
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Call) and getattr(node.func, "attr", "") in ("execute", "executemany") \
                    and node.args:
                yield path.relative_to(REPO), node.lineno, ast.unparse(node.args[0])


def test_app_sql_uses_only_the_portable_dialect():
    """Everything the app sends must be SQLite that db_pg can translate. These SQLite-only
    functions have no Postgres twin, so they must not appear."""
    from smartplate.integrations import swiggy_connect
    db_pg.register_schema(db.SCHEMA)
    db_pg.register_schema(swiggy_connect.SCHEMA)
    found = []
    for path, line, sql in _app_sql():
        low = sql.lower()
        found += [f"{path}:{line} {bad}" for bad in UNPORTABLE if bad in low]
        if "insert or replace into" in low:
            table = low.split("insert or replace into")[1].split("(")[0].strip()
            if table not in db_pg._PRIMARY_KEYS:
                found.append(f"{path}:{line} INSERT OR REPLACE into {table} without a primary key")
    assert found == []


# --------------------------------------------------------------------------- #
# The one-off copy (needs the Postgres service)
# --------------------------------------------------------------------------- #
needs_pg = pytest.mark.skipif(not pg_support.enabled(), reason="needs SMARTPLATE_TEST_DATABASE_URL (CI Postgres job)")


@needs_pg
def test_sqlite_to_postgres_copies_every_record(tmp_path, monkeypatch):
    sys.path.insert(0, str(REPO / "scripts"))
    import sqlite_to_postgres
    from smartplate import seed

    # a SQLite database with the sample profiles and one private profile
    source = tmp_path / "smartplate.db"
    monkeypatch.setattr(config, "DATABASE_URL", "")
    monkeypatch.setattr(config, "DB_PATH", str(source))
    monkeypatch.setattr(config, "WEATHER_PROVIDER", "simulated")
    db.init_db()
    seed.seed_all()
    app = create_app()
    client = app.test_client()
    uid, headers = _private_profile(client, "Moved to Neon")
    sqlite_export = client.get(f"/api/user/{uid}/data.json", headers=headers).get_json()
    with closing_sqlite(source) as conn:
        expected = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in
                    [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' "
                                                "AND name NOT LIKE 'sqlite_%'")]}
    source_bytes = source.read_bytes()

    # an empty Postgres schema as the target
    schema = pg_support.fresh_schema()
    pg_support.reset(schema)
    monkeypatch.setattr(config, "DATABASE_URL", pg_support.URL)
    monkeypatch.setattr(config, "PG_SCHEMA", schema)
    try:
        lines = []
        counts = sqlite_to_postgres.copy(source, dry_run=True, out=lines.append)
        assert counts == expected and lines[-1].startswith("Dry run")
        with db.cursor() as cur:
            assert cur.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0   # rolled back

        assert sqlite_to_postgres.copy(source, out=lines.append) == expected
        assert source.read_bytes() == source_bytes                               # source untouched

        pg_client = create_app().test_client()
        pg_export = pg_client.get(f"/api/user/{uid}/data.json", headers=headers).get_json()
        assert pg_export["data"] == sqlite_export["data"]
        assert pg_client.post("/api/signin", json={"login": f"restart-{uid}",
                                                   "password": "a-long-password"}).status_code == 200
        # new rows continue after the copied ids
        made = pg_client.post("/api/profiles", json={"name": "After the move", "diet": "veg",
                                                     "weekly_budget": 1500, "meals": ["dinner"]}).get_json()
        assert made["user"]["id"] > uid

        with pytest.raises(SystemExit, match="already holds SmartPlate data"):
            sqlite_to_postgres.copy(source, out=lines.append)
        assert sqlite_to_postgres.copy(source, replace=True, out=lines.append) == expected
        with db.cursor() as cur:
            assert cur.execute("SELECT COUNT(*) FROM users").fetchone()[0] == expected["users"]
    finally:
        pg_support.drop(schema)


def closing_sqlite(path):
    from contextlib import closing
    return closing(sqlite3.connect(path))
