"""SQLite persistence. One small module — no ORM needed for v1.1.

The schema deliberately carries the columns every gap feature needs (allergens,
nutrition, carbon, surge history, leftovers, calendar, community, receipts) so
the optimiser can read them all as constraints/signals from one place.
"""
import json
import sqlite3
from contextlib import contextmanager

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
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
"""


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def cursor():
    conn = connect()
    try:
        yield conn.cursor()
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with cursor() as cur:
        cur.executescript(SCHEMA)


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
