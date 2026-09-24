"""SQLite persistence. One small module — no ORM needed for v1.1.

The schema deliberately carries the columns every gap feature needs (allergens,
nutrition, carbon, surge history, leftovers, calendar, community, receipts) so
the optimiser can read them all as constraints/signals from one place.
"""
import json
import re
import sqlite3
from contextlib import contextmanager

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    supabase_uid TEXT UNIQUE,
    name TEXT NOT NULL,
    city TEXT NOT NULL,
    diet TEXT NOT NULL DEFAULT 'nonveg',          -- veg | nonveg | vegan
    weekly_budget REAL NOT NULL DEFAULT 2000,
    rating_floor REAL NOT NULL DEFAULT 4.0,
    mode TEXT NOT NULL DEFAULT 'balanced',
    allergens TEXT NOT NULL DEFAULT '[]',          -- hard exclusions (§5.1.1)
    medical TEXT NOT NULL DEFAULT '[]',            -- e.g. ["diabetes"] (§5.1.1)
    nutrition_targets TEXT NOT NULL DEFAULT '{}',  -- kcal/protein etc (§5.2.6)
    health_targets TEXT NOT NULL DEFAULT '{}',     -- protein/veg/fasting (§5.3.15)
    carbon_pref REAL NOT NULL DEFAULT 0.0,         -- 0..1 weight (§5.3.16)
    household_id INTEGER                            -- group mode (§5.2.7)
);

CREATE TABLE IF NOT EXISTS households (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    split TEXT NOT NULL DEFAULT 'even'             -- even | by_consumption
);

CREATE TABLE IF NOT EXISTS restaurants (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    city TEXT NOT NULL,
    rating REAL NOT NULL,
    cuisines TEXT NOT NULL DEFAULT '[]',
    delivery_fee REAL NOT NULL DEFAULT 30,
    eta_min INTEGER NOT NULL DEFAULT 35,
    is_open INTEGER NOT NULL DEFAULT 1,
    flaky INTEGER NOT NULL DEFAULT 0               -- simulates §1.2 menu-load failures
);

CREATE TABLE IF NOT EXISTS menu_items (
    id INTEGER PRIMARY KEY,
    restaurant_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    price REAL NOT NULL,
    cuisine TEXT NOT NULL DEFAULT 'mixed',
    kcal REAL NOT NULL DEFAULT 0,
    protein_g REAL NOT NULL DEFAULT 0,
    carbs_g REAL NOT NULL DEFAULT 0,
    fat_g REAL NOT NULL DEFAULT 0,
    sugar_g REAL NOT NULL DEFAULT 0,
    veg INTEGER NOT NULL DEFAULT 1,
    allergens TEXT NOT NULL DEFAULT '[]',
    tags TEXT NOT NULL DEFAULT '[]',               -- comfort | light | festive ...
    carbon_kg REAL NOT NULL DEFAULT 1.0,
    item_rating REAL NOT NULL DEFAULT 4.0,
    popularity REAL NOT NULL DEFAULT 0.5,
    reviews TEXT NOT NULL DEFAULT '[]'             -- short snippets for local sentiment (§3.1)
);

CREATE TABLE IF NOT EXISTS plans (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL,
    address_id TEXT,
    week_start TEXT NOT NULL,
    mode TEXT NOT NULL DEFAULT 'balanced',
    status TEXT NOT NULL DEFAULT 'draft',
    created_ts TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY,
    plan_id INTEGER NOT NULL,
    day INTEGER NOT NULL,                          -- 0=Mon .. 6=Sun
    meal TEXT NOT NULL,                            -- breakfast | lunch | dinner
    scheduled_ts TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',         -- active|snoozed|skipped|cooked|ordered (§5.1.5)
    desired_kind TEXT NOT NULL DEFAULT 'auto',      -- auto|delivery|cook|skip
    note TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY,
    session_id INTEGER NOT NULL,
    plan_id INTEGER NOT NULL,
    chosen_kind TEXT NOT NULL,                     -- delivery | cook | skip
    restaurant_id INTEGER,
    item_id INTEGER,
    item_name TEXT,
    cost REAL NOT NULL DEFAULT 0,
    surge_mult REAL NOT NULL DEFAULT 1.0,
    substituted INTEGER NOT NULL DEFAULT 0,
    reasons TEXT NOT NULL DEFAULT '[]',            -- explainability (§5.1.4)
    nutrition TEXT NOT NULL DEFAULT '{}',
    carbon_kg REAL NOT NULL DEFAULT 0,
    recipe_key TEXT,                               -- cook-mode recipe (§5.3.12/17)
    time_shift TEXT,                               -- off-peak shift detail (§3.3)
    restaurant_name TEXT,
    rating REAL NOT NULL DEFAULT 0,
    idempotency_key TEXT,                          -- (§5.1.3)
    created_ts TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS orders (              -- placed orders / saga log (§5.1.3, §6)
    id INTEGER PRIMARY KEY,
    decision_id INTEGER NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    provider_order_id TEXT,
    state TEXT NOT NULL,                           -- validated|carted|confirmed|placed|failed|compensated
    amount REAL NOT NULL DEFAULT 0,
    log TEXT NOT NULL DEFAULT '[]',
    created_ts TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS calendar_events (     -- §5.1.2
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL,
    day INTEGER NOT NULL,
    start_min INTEGER NOT NULL,                    -- minutes from midnight
    end_min INTEGER NOT NULL,
    kind TEXT NOT NULL DEFAULT 'busy',            -- busy | travel
    title TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS leftovers (           -- §5.2.8
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL,
    day INTEGER NOT NULL,
    meal TEXT NOT NULL,
    label TEXT NOT NULL,
    servings INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS weather (             -- §5.2.10 (simulated feed)
    id INTEGER PRIMARY KEY,
    city TEXT NOT NULL,
    day INTEGER NOT NULL,
    condition TEXT NOT NULL,                       -- clear | rain | hot | storm
    temp_c REAL NOT NULL DEFAULT 30
);

CREATE TABLE IF NOT EXISTS surge_history (       -- §5.2.11
    id INTEGER PRIMARY KEY,
    city TEXT NOT NULL,
    day INTEGER NOT NULL,
    meal TEXT NOT NULL,
    condition TEXT NOT NULL,
    multiplier REAL NOT NULL DEFAULT 1.0
);

CREATE TABLE IF NOT EXISTS festivals (           -- §5.2.9
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    iso_date TEXT NOT NULL,
    effect TEXT NOT NULL DEFAULT 'feast',         -- feast | fast | suspend
    note TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS community_templates ( -- §5.3.13
    id INTEGER PRIMARY KEY,
    author TEXT NOT NULL,
    title TEXT NOT NULL,
    city TEXT NOT NULL,
    budget REAL NOT NULL,
    mode TEXT NOT NULL,
    payload TEXT NOT NULL,                         -- serialised plan shape
    adopts INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS receipts (            -- §5.3.14
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL,
    decision_id INTEGER,
    amount REAL NOT NULL,
    category TEXT NOT NULL DEFAULT 'personal',    -- personal | business
    note TEXT NOT NULL DEFAULT '',
    iso_date TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS grocery_baskets (     -- §5.3.12 / §5.3.17
    id INTEGER PRIMARY KEY,
    plan_id INTEGER NOT NULL,
    items TEXT NOT NULL,                           -- [{name, qty, price, recipe}]
    total REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS intake_log (          -- "I made/ate X" → rolling nutrition ledger
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL,
    plan_id INTEGER,
    iso_date TEXT NOT NULL,                         -- the day eaten (drives the rolling windows)
    meal TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'manual',         -- manual | cooked | ordered
    kcal REAL NOT NULL DEFAULT 0,
    protein_g REAL NOT NULL DEFAULT 0,
    carbs_g REAL NOT NULL DEFAULT 0,
    fat_g REAL NOT NULL DEFAULT 0,
    sugar_g REAL NOT NULL DEFAULT 0,
    note TEXT NOT NULL DEFAULT '',
    created_ts TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS swiggy_connections (
    user_id INTEGER PRIMARY KEY,
    token_ciphertext TEXT NOT NULL,
    expires_ts TEXT NOT NULL,
    address_id TEXT,
    address_label TEXT
);

CREATE TABLE IF NOT EXISTS swiggy_oauth_clients (
    redirect_uri TEXT PRIMARY KEY,
    client_id TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS live_restaurants (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL,
    address_id TEXT NOT NULL,
    provider_id TEXT NOT NULL,
    name TEXT NOT NULL,
    rating REAL,
    cuisines TEXT NOT NULL DEFAULT '[]',
    area TEXT,
    eta_min INTEGER,
    status TEXT NOT NULL,
    selected INTEGER NOT NULL DEFAULT 0,
    fetched_ts TEXT NOT NULL,
    UNIQUE(user_id,address_id,provider_id)
);

CREATE TABLE IF NOT EXISTS live_menu_items (
    id INTEGER PRIMARY KEY,
    live_restaurant_id INTEGER NOT NULL,
    provider_id TEXT NOT NULL,
    name TEXT NOT NULL,
    price REAL,
    veg INTEGER,
    rating REAL,
    categories TEXT NOT NULL DEFAULT '[]',
    available INTEGER NOT NULL DEFAULT 1,
    fetched_ts TEXT NOT NULL,
    UNIQUE(live_restaurant_id,provider_id)
);
"""


def connect() -> sqlite3.Connection:
    if config.DATABASE_URL:
        import psycopg
        return psycopg.connect(config.DATABASE_URL, row_factory=_hybrid_row,
                               sslmode='require', prepare_threshold=None,
                               connect_timeout=10)
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


class _HybridRow(dict):
    """Postgres row with the name and ordinal lookup sqlite3.Row supports."""
    def __init__(self, names, values):
        super().__init__(zip(names, values))
        self._values = values

    def __getitem__(self, key):
        return self._values[key] if isinstance(key, int) else super().__getitem__(key)


def _hybrid_row(cur):
    names = [col.name for col in cur.description]
    return lambda values: _HybridRow(names, values)


class _PgCursor:
    def __init__(self, conn):
        self.conn = conn
        self.cur = conn.cursor()

    def execute(self, sql, params=()):
        if sql.strip().upper() == 'BEGIN IMMEDIATE':
            return self
        sql = sql.replace("datetime('now')", 'CURRENT_TIMESTAMP').replace('?', '%s')
        self.cur.execute(sql, params)
        return self

    def fetchone(self):
        return self.cur.fetchone()

    def fetchall(self):
        return self.cur.fetchall()

    def __iter__(self):
        return iter(self.cur)

    @property
    def lastrowid(self):
        return self.conn.execute('SELECT LASTVAL()').fetchone()[0]


@contextmanager
def cursor():
    conn = connect()
    try:
        yield _PgCursor(conn) if config.DATABASE_URL else conn.cursor()
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    if config.DATABASE_URL:
        schema = re.sub(r'\bid INTEGER PRIMARY KEY\b',
                        'id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY', SCHEMA)
        with cursor() as cur:
            for statement in schema.split(';'):
                if statement.strip():
                    cur.execute(statement)
        return
    with cursor() as cur:
        cur.executescript(SCHEMA)
        # Upgrade existing private-trial databases without clearing plans.
        columns = {r["name"] for r in cur.execute("PRAGMA table_info(users)").fetchall()}
        if "supabase_uid" not in columns:
            cur.execute("ALTER TABLE users ADD COLUMN supabase_uid TEXT")
        cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS users_supabase_uid_idx ON users(supabase_uid)")
        plan_columns = {r["name"] for r in cur.execute("PRAGMA table_info(plans)").fetchall()}
        if "address_id" not in plan_columns:
            cur.execute("ALTER TABLE plans ADD COLUMN address_id TEXT")
        session_columns = {r["name"] for r in cur.execute("PRAGMA table_info(sessions)").fetchall()}
        if "desired_kind" not in session_columns:
            cur.execute("ALTER TABLE sessions ADD COLUMN desired_kind TEXT NOT NULL DEFAULT 'auto'")


# --- small json helpers so callers don't sprinkle json.loads everywhere ---

def jl(value, default=None):
    """json-load a stored TEXT column, tolerating None/empty."""
    if not value:
        return default if default is not None else []
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default if default is not None else []


def jd(value) -> str:
    return json.dumps(value, separators=(",", ":"))


def row_to_dict(row: sqlite3.Row) -> dict:
    return {k: row[k] for k in row.keys()}
