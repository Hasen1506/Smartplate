"""Run the suite against Postgres: set SMARTPLATE_TEST_DATABASE_URL to a throwaway database.

Each test gets its own Postgres schema, cloned server-side from a seeded template
schema (one per worker and fixture kind), the way the SQLite suite gets a byte copy of
a seeded file. Nothing is shared between tests, so runs are deterministic, and the
database is local (a CI service container), so the suite stays offline.
"""
import os
import re

URL = os.environ.get("SMARTPLATE_TEST_DATABASE_URL", "").strip()
WORKER = re.sub(r"\W", "_", os.environ.get("PYTEST_XDIST_WORKER", "main"))

CLONE_FN = """
CREATE OR REPLACE FUNCTION {schema}.sp_clone_schema(src text, dst text) RETURNS void AS $$
DECLARE t record; seq text;
BEGIN
  EXECUTE format('DROP SCHEMA IF EXISTS %I CASCADE', dst);
  EXECUTE format('CREATE SCHEMA %I', dst);
  FOR t IN SELECT table_name FROM information_schema.tables
           WHERE table_schema = src AND table_type = 'BASE TABLE' ORDER BY table_name LOOP
    EXECUTE format('CREATE TABLE %I.%I (LIKE %I.%I INCLUDING ALL)', dst, t.table_name, src, t.table_name);
    EXECUTE format('INSERT INTO %I.%I SELECT * FROM %I.%I', dst, t.table_name, src, t.table_name);
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = dst
               AND table_name = t.table_name AND column_name = 'id' AND is_identity = 'YES') THEN
      seq := pg_get_serial_sequence(format('%I.%I', dst, t.table_name), 'id');
      EXECUTE format('SELECT setval(%L, COALESCE((SELECT MAX(id) FROM %I.%I), 0) + 1, false)',
                     seq, dst, t.table_name);
    END IF;
  END LOOP;
END $$ LANGUAGE plpgsql;
"""


def enabled() -> bool:
    return bool(URL)


def _admin(sql: str, *params):
    import psycopg
    with psycopg.connect(URL, autocommit=True) as conn:
        conn.execute(sql, params or None)


def worker_schema() -> str:
    return f"w_{WORKER}".lower()


def setup_worker() -> None:
    """The worker's home schema (tests that use no fixture land here) and clone helper."""
    schema = worker_schema()
    _admin(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
    _admin(f"CREATE SCHEMA {schema}")
    _admin(CLONE_FN.format(schema=schema))


_counter = [0]


def template_schema(key) -> str:
    import hashlib
    return f"tpl_{WORKER}_{hashlib.sha1(repr(key).encode()).hexdigest()[:10]}".lower()


def fresh_schema() -> str:
    _counter[0] += 1
    return f"t_{WORKER}_{_counter[0]}".lower()


def reset(schema: str) -> None:
    _admin(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
    _admin(f"CREATE SCHEMA {schema}")


def clone(src: str, dst: str) -> None:
    _admin(f"SELECT {worker_schema()}.sp_clone_schema(%s, %s)", src, dst)


def drop(schema: str) -> None:
    from smartplate import db_pg
    db_pg.close_pools()
    _admin(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
