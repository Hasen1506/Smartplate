"""Copy a SmartPlate SQLite database into Postgres (one-off move to DATABASE_URL).

Usage:
    DATABASE_URL='postgresql://…neon.tech/neondb?sslmode=require' \\
        python scripts/sqlite_to_postgres.py path/to/smartplate.db [--replace] [--dry-run]

Every table is copied whole, ids included, so profiles, private-profile keys, sign-ins,
devices, plans, ratings, receipts, Swiggy links (still sealed by SMARTPLATE_SECRET or
the copied app_secrets key) and push subscriptions come across unchanged.

* The source is never modified: it is copied to a temporary file, brought up to the
  current schema there, and read from that copy.
* The target must hold no SmartPlate data yet. A deployed app seeds the three sample
  profiles into an empty database on its first start; to copy over those too, pass
  --replace, which empties the SmartPlate tables in the target first.
* It all happens in one transaction: on any error nothing is written. Row counts are
  checked table by table before the commit.
"""
import argparse
import os
import shutil
import sqlite3
import sys
import tempfile
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from smartplate import config, db  # noqa: E402

BATCH = 500


def _upgrade_copy(source: Path) -> Path:
    """A temporary copy of `source`, migrated to the current schema."""
    fd, tmp = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    with closing(sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)) as current:
        with closing(sqlite3.connect(tmp)) as saved:
            current.backup(saved)
    url, path = config.DATABASE_URL, config.DB_PATH
    config.DATABASE_URL, config.DB_PATH = "", tmp
    try:
        db.init_db()
        from smartplate.integrations import swiggy_connect
        swiggy_connect.init_schema()
    finally:
        config.DATABASE_URL, config.DB_PATH = url, path
    return Path(tmp)


def _tables(conn: sqlite3.Connection) -> list[str]:
    return [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' "
                                       "AND name NOT LIKE 'sqlite_%' ORDER BY name")]


def _columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]


def copy(source: Path, *, replace: bool = False, dry_run: bool = False, out=print) -> dict:
    """Copy `source` into the Postgres database at config.DATABASE_URL. Returns the
    row count per table."""
    if not config.DATABASE_URL:
        raise SystemExit("Set DATABASE_URL to the Postgres database to copy into.")
    source = Path(source)
    if not source.is_file():
        raise SystemExit(f"No SQLite database at {source}")
    upgraded = _upgrade_copy(source)
    counts: dict[str, int] = {}
    try:
        db.init_db()                                   # target schema (tables, migrations)
        from smartplate.integrations import swiggy_connect
        swiggy_connect.init_schema()
        with closing(sqlite3.connect(upgraded)) as src:
            src.row_factory = sqlite3.Row
            tables = _tables(src)
            conn = db.connect()
            try:
                cur = conn.cursor()
                target = {r["name"] for r in cur.execute(
                    "SELECT table_name AS name FROM information_schema.tables "
                    "WHERE table_schema = current_schema()")}
                missing = [t for t in tables if t not in target]
                if missing:
                    raise SystemExit(f"These SQLite tables have no Postgres twin: {', '.join(missing)}")
                cur.execute("BEGIN IMMEDIATE")          # one writer: the app must not write meanwhile
                busy = [t for t in tables if cur.execute(f"SELECT 1 FROM {t} LIMIT 1").fetchone()]
                if busy and not replace:
                    raise SystemExit("The Postgres database already holds SmartPlate data "
                                     f"({', '.join(busy)}). Re-run with --replace to overwrite it.")
                if busy:
                    cur.execute("TRUNCATE " + ", ".join(tables) + " RESTART IDENTITY")
                for table in tables:
                    have = set(_columns(src, table))
                    target_cols = [r["name"] for r in cur.execute(f"PRAGMA table_info({table})")]
                    cols = [c for c in target_cols if c in have]
                    rows = src.execute(f"SELECT {', '.join(cols)} FROM {table} ORDER BY rowid").fetchall()
                    sql = f"INSERT INTO {table}({', '.join(cols)}) VALUES ({','.join('?' * len(cols))})"
                    for i in range(0, len(rows), BATCH):
                        cur.executemany(sql, [tuple(r) for r in rows[i:i + BATCH]])
                    copied = cur.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                    if copied != len(rows):
                        raise RuntimeError(f"{table}: copied {copied} of {len(rows)} rows")
                    counts[table] = len(rows)
                    out(f"{table:28} {len(rows):>7} rows")
                if dry_run:
                    conn.rollback()
                    out("Dry run: rolled back, nothing written.")
                else:
                    conn.commit()
                    out(f"Copied {sum(counts.values())} rows in {len(counts)} tables; "
                        f"{counts.get('users', 0)} profiles.")
            except BaseException:
                conn.rollback()
                raise
            finally:
                conn.close()
    finally:
        upgraded.unlink(missing_ok=True)
    return counts


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("sqlite", type=Path, help="the SQLite database file to copy from")
    parser.add_argument("--replace", action="store_true",
                        help="empty the SmartPlate tables in Postgres first (e.g. the sample profiles "
                             "a fresh deploy seeds)")
    parser.add_argument("--dry-run", action="store_true", help="copy, verify, then roll back")
    args = parser.parse_args(argv)
    copy(args.sqlite, replace=args.replace, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
