"""Persistent real-menu plans and reviewed cart lines; no sample catalogue dependency."""
import datetime as dt
import math
from collections import Counter

from . import clock, db
from .domain import models
from .integrations import swiggy_live as live

SCHEMA = """
CREATE TABLE IF NOT EXISTS live_weeks (
 user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
 address_id TEXT NOT NULL, payload TEXT NOT NULL, updated_ts TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS swiggy_cart_lines (
 user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
 payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reminder_jobs (
 id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 subscription_id INTEGER NOT NULL REFERENCES push_subscriptions(id) ON DELETE CASCADE,
 event_key TEXT NOT NULL, payload TEXT NOT NULL, due_ts TEXT NOT NULL,
 state TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
 retry_ts TEXT, lease_ts TEXT, last_code INTEGER,
 UNIQUE(subscription_id,event_key)
);
CREATE TABLE IF NOT EXISTS worker_heartbeats (name TEXT PRIMARY KEY, updated_ts TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS reminder_due ON reminder_jobs(state,due_ts);
"""


def init_schema():
    with db.cursor() as cur:
        cur.executescript(SCHEMA)


def week(user_id):
    conn = live._conn(user_id)
    address = live._address(conn)
    with db.cursor() as cur:
        row = cur.execute('SELECT * FROM live_weeks WHERE user_id=? AND address_id=?',
                          (user_id, address)).fetchone()
    return db.jl(row['payload'], {}) if row else None


def build_week(user_id, body):
    user = models.get_user(user_id)
    conn = live._conn(user_id)
    address = live._address(conn)
    start = dt.date.fromisoformat(body.get('start_date') or clock.now().date().isoformat())
    if not clock.now().date() <= start <= clock.now().date() + dt.timedelta(days=28):
        raise ValueError('Choose a start date within the next four weeks')
    budget = body.get('budget', user['weekly_budget'])
    reserve = body.get('fee_reserve', 50)
    if (isinstance(budget, bool) or not isinstance(budget, (int, float)) or not math.isfinite(budget)
            or not 1 <= budget <= 100000 or isinstance(reserve, bool)
            or not isinstance(reserve, (int, float)) or not math.isfinite(reserve) or not 0 <= reserve <= 500):
        raise ValueError('Enter a valid weekly estimate and fee reserve')
    times = body.get('times') or {'breakfast': '08:00', 'lunch': '13:00', 'dinner': '19:00'}
    meals = user['prefs'].get('meals') or list(models.MEALS)
    if not isinstance(times, dict):
        raise ValueError('Choose meal reminder times')
    for meal in meals:
        if meal not in times:
            raise ValueError('Choose a time for each meal')
        parsed = dt.datetime.strptime(times[meal], '%H:%M')
        if parsed.strftime('%H:%M') != times[meal]:
            raise ValueError('Use a meal time in HH:MM format')
    favourites = live.live_favourites(user_id)
    if not favourites:
        raise ValueError('Star real restaurants in Places before planning your week')
    if len(favourites) > 5:
        raise ValueError('Use up to five favourites for a bounded menu planning request')
    candidates, notices = [], []
    for restaurant in favourites:
        try:
            menu = live.live_menu(user_id, restaurant['id'], restaurant['name'])
        except live.SwiggyError:
            notices.append(f"{restaurant['name']}: menu unavailable; excluded")
            continue
        if menu['truncated']:
            notices.append(f"{restaurant['name']}: planning uses its compact browse menu")
        for item in menu['items'][:150]:
            if item['in_stock'] is False or item['price'] is None or item['price'] <= 0:
                continue
            if user['diet'] in ('veg', 'vegan') and item['veg'] is not True:
                continue
            candidates.append({**item, 'restaurant_id': restaurant['id'],
                               'restaurant': menu['restaurant']['name'], 'fetched': menu['fetched']})
    slots, usage, spend = [], Counter(), 0
    for day in range(7):
        for meal in meals:
            affordable = [i for i in candidates if spend + i['price'] + reserve <= budget]
            picked = min(affordable, key=lambda i: (usage[(i['restaurant_id'], i['id'])], i['price'], i['id']), default=None)
            slot = {'date': (start + dt.timedelta(days=day)).isoformat(), 'meal': meal,
                    'time': times[meal], 'item': picked,
                    'reason': None if picked else 'No item with a verified price fits the remaining estimate'}
            if picked:
                usage[(picked['restaurant_id'], picked['id'])] += 1
                spend = round(spend + picked['price'] + reserve, 2)
            slots.append(slot)
    result = {'start_date': start.isoformat(), 'address_id': address,
              'address': conn.get('address_label') or address, 'slots': slots,
              'budget': budget, 'fee_reserve': reserve, 'estimated_total': spend,
              'fetched': clock.now().isoformat(timespec='seconds'), 'notices': notices,
              'nutrition': None, 'ingredient_safety': 'unverified',
              'source': 'swiggy', 'ordering': 'manual_review_required'}
    with db.cursor() as cur:
        cur.execute('INSERT OR REPLACE INTO live_weeks VALUES (?,?,?,?)',
                    (user_id, address, db.jd(result), clock.now().isoformat()))
        cur.execute("DELETE FROM reminder_jobs WHERE user_id=? AND event_key LIKE 'live:%' AND state!='sent'", (user_id,))
    return result


def reminders(user_id, since):
    plan = week(user_id)
    if not plan:
        return []
    result = []
    for slot in plan['slots']:
        if not slot['item']:
            continue
        at = dt.datetime.fromisoformat(slot['date'] + 'T' + slot['time']).replace(tzinfo=since.tzinfo)
        if at < since:
            continue
        item = slot['item']
        result.append({'session_id': f"live:{slot['date']}:{slot['meal']}", 'at': at.isoformat(),
                       'title': f"Review {slot['meal']}",
                       'body': f"{item['name']} at {item['restaurant']}. Refresh price and stock before ordering.",
                       'link': '/?tab=week'})
    return result
