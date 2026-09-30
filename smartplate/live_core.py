"""Persistent real-menu plans and reviewed cart lines; no sample catalogue dependency."""
import datetime as dt
import math
import secrets

from . import clock, db, food_memory, live_planner
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


def build_week(user_id, body, *, revision=False):
    user = models.get_user(user_id)
    conn = live._conn(user_id)
    address = live._address(conn)
    start = dt.date.fromisoformat(body.get('start_date') or clock.now().date().isoformat())
    if not revision and not clock.now().date() <= start <= clock.now().date() + dt.timedelta(days=28):
        raise ValueError('Choose a start date within the next four weeks')
    budget = body.get('budget', user['weekly_budget'])
    reserve = body.get('fee_reserve', 50)
    if (isinstance(budget, bool) or not isinstance(budget, (int, float)) or not math.isfinite(budget)
            or not 1 <= budget <= 100000 or isinstance(reserve, bool)
            or not isinstance(reserve, (int, float)) or not math.isfinite(reserve) or not 0 <= reserve <= 500):
        raise ValueError('Enter a valid weekly estimate and fee reserve')
    times = body.get('times') or {'breakfast': '08:00', 'lunch': '13:00', 'dinner': '19:00'}
    meals = body.get('meals') or user['prefs'].get('meals') or list(models.MEALS)
    if not isinstance(meals, list) or not meals or len(meals) > 3 or len(set(meals)) != len(meals) or any(m not in models.MEALS for m in meals):
        raise ValueError('Choose the meals this budget must cover')
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
    if user['allergens'] or user['medical'] or user['diet'] == 'vegan':
        notices_safety = 'Provider menus do not verify your ingredient or medical restrictions; all selections need external safety review'
    else:
        notices_safety = None
    if len(favourites) > 5:
        raise ValueError('Use up to five favourites for a bounded menu planning request')
    candidates, notices = [], [notices_safety] if notices_safety else []
    for restaurant in favourites:
        try:
            menu = live.live_menu(user_id, restaurant['id'], restaurant['name'])
        except live.SwiggyError:
            notices.append(f"{restaurant['name']}: menu unavailable; excluded")
            continue
        if menu['truncated']:
            notices.append(f"{restaurant['name']}: planning uses its compact browse menu")
        unknown_prices = sum(i['price'] is None for i in menu['items'])
        if unknown_prices:
            notices.append(f"{restaurant['name']}: {unknown_prices} items have unverified price units and are excluded")
        for item in menu['items'][:150]:
            if item['in_stock'] is False or item['price'] is None or item['price'] <= 0 or item['price'] > 100000:
                continue
            if user['diet'] in ('veg', 'vegan') and item['veg'] is not True:
                continue
            candidates.append({**item, 'restaurant_id': restaurant['id'],
                               'restaurant': menu['restaurant']['name'], 'fetched': menu['fetched']})
    existing = week(user_id)
    if existing and not revision and existing.get('start_date') == start.isoformat():
        raise ValueError('Use revise to update an existing week and preserve its spending history')
    if revision and (not existing or body.get('expected_version') != existing.get('version')):
        raise live.CartChanged('This week changed; refresh before revising it')
    end = (start + dt.timedelta(days=7)).isoformat()
    recorded = food_memory.events(user_id, start.isoformat(), end)
    spent = sum(e['amount_paise'] for e in recorded) / 100
    fulfilled = {(e['meal_date'], e['meal']): e for e in recorded if e['meal'] in meals}
    slots = [{'date': (start + dt.timedelta(days=day)).isoformat(), 'meal': meal, 'time': times[meal]}
             for day in range(7) for meal in meals]
    active, past = [], 0
    for slot in slots:
        event = fulfilled.get((slot['date'], slot['meal']))
        slot['event'] = event
        if event:
            slot.update(item=None, reason='Meal recorded', state='recorded')
        elif dt.datetime.fromisoformat(slot['date'] + 'T' + slot['time']).replace(tzinfo=clock.now().tzinfo) < clock.now():
            slot.update(item=None, reason='Time passed; record what you actually ate and paid', state='needs_record')
            past += 1
        else:
            active.append(slot)
    memory = food_memory.memory(user_id)
    previous = {s['date'] + ':' + s['meal']: s['item']['restaurant_id'] + ':' + s['item']['id']
                for s in (existing or {}).get('slots', []) if s.get('item')}
    # Provider categories identify obvious non-meals; do not fabricate portion or
    # nutrition labels for the remaining dishes. Explicit suitability can override.
    import re
    nonmeal = re.compile(r'\b(beverages?|drinks?|desserts?|sweets?|condiments?|tea|coffee|water|soft drinks?)\b', re.I)
    candidates = [c for c in candidates if memory.get(c['restaurant_id'] + ':' + c['id'], {}).get('suitable_meals')
        or not nonmeal.search(' '.join(c.get('categories', [])) + ' ' + c['name'])]
    unique = {}
    for candidate in candidates:
        unique[(candidate['restaurant_id'], candidate['id'])] = candidate
    candidates = list(unique.values())
    solved = live_planner.solve(active, candidates, max(0, budget - spent), reserve,
                                preferences=memory, previous=previous)
    for slot, picked in zip(active, solved['items']):
        slot.update(item=picked, state='planned' if picked else 'uncovered',
                    reason=None if picked else 'No eligible item fits the remaining budget estimate')
        slot['meal_suitability'] = ('user_confirmed' if picked and slot['meal'] in memory.get(
            picked['restaurant_id'] + ':' + picked['id'], {}).get('suitable_meals', []) else 'unverified')
    minimum = solved['minimum_full_coverage']
    result = {'id': existing.get('id', secrets.token_urlsafe(16)) if revision else secrets.token_urlsafe(16),
              'version': existing.get('version', 0) + 1 if revision else 1,
              'start_date': start.isoformat(), 'address_id': address,
              'address': conn.get('address_label') or address, 'slots': slots, 'meals': meals,
              'budget': budget, 'fee_reserve': reserve, 'actual_spend': spent,
              'remaining_budget': round(budget - spent, 2),
              'estimated_future': solved['estimated_total'],
              'estimated_total': round(spent + solved['estimated_total'], 2),
              'coverage': {k: solved[k] for k in ('covered', 'required', 'minimum_full_coverage')},
              'shortfall': round(max(0, spent + minimum - budget), 2) if minimum is not None else None,
              'overspent': round(max(0, spent - budget), 2), 'unrecorded_past': past,
              'status': 'budget_insufficient' if spent > budget or solved['covered'] < len(active) else 'planned_estimate',
              'ledger_version': ledger_version(recorded), 'memory_version': memory_version(user_id),
              'solver': solved['solver'], 'meal_suitability': 'review_required',
              'fetched': clock.now().isoformat(timespec='seconds'), 'notices': notices,
              'nutrition': None, 'ingredient_safety': 'unverified',
              'source': 'swiggy', 'ordering': 'manual_review_required'}
    with db.cursor() as cur:
        cur.execute('BEGIN IMMEDIATE')
        current = cur.execute('SELECT payload FROM live_weeks WHERE user_id=?', (user_id,)).fetchone()
        if revision and (not current or db.jl(current['payload'], {}).get('version') != body['expected_version']):
            raise live.CartChanged('This week changed; refresh before revising it')
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


def revise_week(user_id, body):
    existing = week(user_id)
    if not existing:
        raise ValueError('Build a week before revising it')
    version = body.get('expected_version')
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise ValueError('Supply the current integer plan version')
    if version != existing.get('version'):
        raise live.CartChanged('This week changed; refresh before revising it')
    if clock.now().date() >= dt.date.fromisoformat(existing['start_date']) + dt.timedelta(days=7):
        raise ValueError('This week ended; build a new week')
    # Revision may start in the past; build_week only accepts future new weeks.
    return build_week(user_id, {**body, 'start_date': existing['start_date'],
        'meals': existing.get('meals') or sorted({s['meal'] for s in existing['slots']}),
        'budget': body.get('budget', existing['budget']), 'fee_reserve': body.get('fee_reserve', existing['fee_reserve']),
        'times': body.get('times') or {s['meal']: s['time'] for s in existing['slots']}}, revision=True)


def week_status(user_id):
    plan = week(user_id)
    if not plan:
        return {'status': 'needs_plan', 'plan': None}
    end = (dt.date.fromisoformat(plan['start_date']) + dt.timedelta(days=7)).isoformat()
    recorded = food_memory.events(user_id, plan['start_date'], end)
    spent = sum(e['amount_paise'] for e in recorded) / 100
    elapsed = any(s.get('state') == 'planned' and dt.datetime.fromisoformat(s['date'] + 'T' + s['time']) < clock.now() for s in plan['slots'])
    changed = (ledger_version(recorded) != plan.get('ledger_version') or memory_version(user_id) != plan.get('memory_version') or elapsed)
    return {'status': 'revision_required' if changed else plan.get('status', 'planned_estimate'),
            'plan': plan, 'events': recorded, 'actual_spend': spent,
            'remaining_budget': round(plan['budget'] - spent, 2)}



def ledger_version(events):
    import hashlib
    return hashlib.sha256(db.jd([(e['id'], e['meal'], e['amount_paise']) for e in events]).encode()).hexdigest()


def memory_version(user_id):
    import hashlib
    return hashlib.sha256(db.jd(food_memory.memory(user_id)).encode()).hexdigest()
