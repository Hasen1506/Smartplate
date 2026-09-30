"""Request correlation and authenticated operations status without user data."""
import datetime as dt
import hmac
import json
import logging
import os
import shutil
import time
import uuid

from flask import g, jsonify, request

from . import clock, config, db

log = logging.getLogger('smartplate.requests')
log.setLevel(logging.INFO)
if not log.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter('%(message)s'))
    log.addHandler(handler)


def begin():
    g.request_id = uuid.uuid4().hex
    g.request_started = time.monotonic()


def finish(response):
    response.headers['X-Request-ID'] = g.get('request_id', uuid.uuid4().hex)
    if request.path.startswith('/api/') or request.path in ('/readyz', '/ops/status'):
        # Never log query strings, IDs in concrete URLs, bodies, headers or errors
        # from the provider. Routes identify operations without identifying users.
        log.info(json.dumps({'request_id': response.headers['X-Request-ID'],
            'method': request.method, 'route': request.url_rule.rule if request.url_rule else 'unmatched',
            'status': response.status_code,
            'duration_ms': round(1000 * (time.monotonic() - g.get('request_started', time.monotonic())), 1)}))
    return response


def status():
    token = os.environ.get('SMARTPLATE_OPS_TOKEN', '')
    if not token or not hmac.compare_digest(request.headers.get('Authorization', ''), 'Bearer ' + token):
        return jsonify(error='Operations authorization required'), 401
    with db.cursor() as cur:
        states = {r['state']: r['count'] for r in cur.execute('SELECT state,COUNT(*) count FROM reminder_jobs GROUP BY state')}
        unresolved = cur.execute("SELECT COUNT(*) FROM swiggy_order_attempts WHERE state IN ('started','unknown')").fetchone()[0]
        beat = cur.execute("SELECT updated_ts FROM worker_heartbeats WHERE name='reminders'").fetchone()
        schema = cur.execute('SELECT COUNT(*) FROM schema_migrations').fetchone()[0]
        integrity = cur.execute('PRAGMA quick_check').fetchone()[0] == 'ok'
    age = (clock.now() - dt.datetime.fromisoformat(beat['updated_ts'])).total_seconds() if beat else None
    ready = integrity and (not config.PUSH_ENABLED or (age is not None and age <= max(180, config.PUSH_TICK_S * 3)))
    return jsonify(ok=ready, database_ok=integrity, migration_records=schema,
        reminder_jobs=states, reminder_worker_age_seconds=age, unresolved_orders=unresolved,
        disk_free_bytes=shutil.disk_usage(os.path.dirname(os.path.abspath(config.DB_PATH))).free,
        live_orders_enabled=config.LIVE_ORDERS), 200 if ready else 503
