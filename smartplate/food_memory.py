"""Explicit food preferences and auditable spending; no invented user history."""
import datetime as dt
import math

from . import clock, db
from .domain import models

SCHEMA = """
CREATE TABLE IF NOT EXISTS food_memory (
 user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 item_key TEXT NOT NULL, payload TEXT NOT NULL, updated_ts TEXT NOT NULL,
 PRIMARY KEY(user_id,item_key)
);
CREATE TABLE IF NOT EXISTS food_events (
 id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 event_key TEXT NOT NULL, meal_date TEXT NOT NULL, meal TEXT NOT NULL,
 amount_paise INTEGER NOT NULL, source TEXT NOT NULL, payload TEXT NOT NULL,
 created_ts TEXT NOT NULL, UNIQUE(user_id,event_key)
);
"""


def init_schema():
    with db.cursor() as cur:
        cur.executescript(SCHEMA)


def memory(user_id):
    with db.cursor() as cur:
        rows = cur.execute('SELECT item_key,payload FROM food_memory WHERE user_id=? ORDER BY item_key', (user_id,)).fetchall()
    return {row['item_key']: db.jl(row['payload'], {}) for row in rows}


def remember(user_id, body):
    restaurant, item = body.get('restaurant_id'), body.get('item_id')
    if not all(isinstance(v, str) and 0 < len(v) <= 120 and ':' not in v for v in (restaurant, item)):
        raise ValueError('Use the exact restaurant and item IDs from a real menu')
    preference = body.get('preference', 'neutral')
    meals = body.get('suitable_meals', [])
    note = body.get('note', '')
    if preference not in ('like', 'neutral', 'avoid') or not isinstance(meals, list) or len(meals) > 3 or any(m not in models.MEALS for m in meals):
        raise ValueError('Choose a preference and valid suitable meals')
    if not isinstance(note, str) or len(note) > 500:
        raise ValueError('Keep food notes within 500 characters')
    payload = {'restaurant_id': restaurant, 'item_id': item, 'preference': preference,
               'suitable_meals': sorted(set(meals)), 'note': note, 'source': 'user_stated'}
    with db.cursor() as cur:
        count = cur.execute('SELECT COUNT(*) FROM food_memory WHERE user_id=?', (user_id,)).fetchone()[0]
        existing = cur.execute('SELECT 1 FROM food_memory WHERE user_id=? AND item_key=?', (user_id, restaurant + ':' + item)).fetchone()
        if count >= 500 and not existing:
            raise ValueError('Food memory limit reached; remove an old preference first')
        cur.execute('INSERT OR REPLACE INTO food_memory VALUES (?,?,?,?)',
                    (user_id, restaurant + ':' + item, db.jd(payload), clock.now().isoformat()))
    return payload


def forget(user_id, restaurant_id, item_id):
    with db.cursor() as cur:
        cur.execute('DELETE FROM food_memory WHERE user_id=? AND item_key=?', (user_id, restaurant_id + ':' + item_id))
    return {'forgotten': True}


def events(user_id, start, end):
    with db.cursor() as cur:
        rows = cur.execute('SELECT * FROM food_events WHERE user_id=? AND meal_date>=? AND meal_date<? ORDER BY meal_date,id',
                           (user_id, start, end)).fetchall()
    return [{**dict(row), 'payload': db.jl(row['payload'], {})} for row in rows]


def record(user_id, body, *, provider_order=None):
    from .live_planner import paise
    date = dt.date.fromisoformat(body.get('date', ''))
    if not clock.now().date() - dt.timedelta(days=365) <= date <= clock.now().date():
        raise ValueError('Record a meal that already happened within the last year')
    meal, amount, event_key = body.get('meal'), body.get('amount'), body.get('event_key')
    if meal not in models.MEALS or isinstance(amount, bool) or not isinstance(amount, (int, float)) or not math.isfinite(amount) or not 0 <= amount <= 100000:
        raise ValueError('Choose a meal and actual amount paid in rupees')
    if not isinstance(event_key, str) or not 8 <= len(event_key) <= 120 or (event_key.startswith('swiggy:') and not provider_order):
        raise ValueError('Use a unique event key of 8–120 characters; provider keys are reserved')
    note = body.get('note', '')
    if not isinstance(note, str) or len(note) > 500:
        raise ValueError('Keep meal notes within 500 characters')
    payload = {'note': note, 'order_id': provider_order}
    source = 'swiggy_confirmed' if provider_order else 'user_reported'
    with db.cursor() as cur:
        cur.execute('BEGIN IMMEDIATE')
        row = cur.execute('SELECT * FROM food_events WHERE user_id=? AND event_key=?', (user_id, event_key)).fetchone()
        if row:
            if (row['meal_date'], row['meal'], row['amount_paise'], row['payload']) != (date.isoformat(), meal, paise(amount), db.jd(payload)):
                raise ValueError('This event key already records a different meal; duplicate spending was refused')
            return {'id': row['id'], 'duplicate': True, 'source': row['source']}
        # One fulfilled meal slot; extras belong in the amount of the original record.
        if cur.execute('SELECT 1 FROM food_events WHERE user_id=? AND meal_date=? AND meal=?', (user_id, date.isoformat(), meal)).fetchone():
            raise ValueError('This meal is already recorded; remove it before correcting its amount')
        cur.execute('INSERT INTO food_events(user_id,event_key,meal_date,meal,amount_paise,source,payload,created_ts) VALUES (?,?,?,?,?,?,?,?)',
                    (user_id, event_key, date.isoformat(), meal, paise(amount), source, db.jd(payload), clock.now().isoformat()))
        return {'id': cur.lastrowid, 'duplicate': False, 'source': source}


def remove_event(user_id, event_id):
    with db.cursor() as cur:
        row = cur.execute('SELECT source FROM food_events WHERE id=? AND user_id=?', (event_id, user_id)).fetchone()
        if not row or row['source'] != 'user_reported':
            raise ValueError('Only your manually reported meals can be removed')
        cur.execute('DELETE FROM food_events WHERE id=? AND user_id=?', (event_id, user_id))
    return {'removed': True}


def assign_order(user_id, body):
    """An order's verified payable amount is immutable; only meal allocation changes."""
    order_id, meal = body.get('order_id'), body.get('meal')
    if not isinstance(order_id, str) or meal not in models.MEALS:
        raise ValueError('Choose a recorded order and the meal it covered')
    with db.cursor() as cur:
        cur.execute('BEGIN IMMEDIATE')
        row = cur.execute("SELECT * FROM food_events WHERE user_id=? AND event_key=? AND source='swiggy_confirmed_order'",
                          (user_id, 'swiggy:' + order_id)).fetchone()
        if not row:
            raise ValueError('This confirmed order is not recorded for your profile')
        if cur.execute('SELECT 1 FROM food_events WHERE user_id=? AND meal_date=? AND meal=? AND id!=?',
                       (user_id, row['meal_date'], meal, row['id'])).fetchone():
            raise ValueError('This meal already has a spending record')
        cur.execute('UPDATE food_events SET meal=? WHERE id=?', (meal, row['id']))
    return {'allocated': True, 'amount': row['amount_paise'] / 100, 'source': row['source']}
