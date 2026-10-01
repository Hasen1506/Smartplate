"""Durable reminder leases and bounded delivery retries. Never places orders."""
import datetime as dt

from . import clock, config, db


def enqueue(pairs, at, reconcile=False):
    active = set()
    with db.cursor() as cur:
        for sub, reminder in pairs:
            if not cur.execute('SELECT 1 FROM push_subscriptions WHERE id=? AND user_id=?', (sub['id'], sub['user_id'])).fetchone():
                continue
            key = f"{reminder['session_id']}:{reminder['at']}"
            active.add((sub['id'], key))
            payload = {'title': reminder['title'], 'body': reminder['body'],
                       'url': reminder['link'] or '/?tab=today', 'tag': f"smartplate-{reminder['session_id']}"}
            cur.execute('INSERT OR IGNORE INTO reminder_jobs(user_id,subscription_id,event_key,payload,due_ts) VALUES (?,?,?,?,?)',
                        (sub['user_id'], sub['id'], key, db.jd(payload), reminder['at']))
            cur.execute("UPDATE reminder_jobs SET payload=? WHERE subscription_id=? AND event_key=? AND state='pending'",
                        (db.jd(payload), sub['id'], key))
        if reconcile:
            for row in cur.execute("SELECT id,subscription_id,event_key FROM reminder_jobs WHERE state='pending'").fetchall():
                if (row['subscription_id'], row['event_key']) not in active:
                    cur.execute("UPDATE reminder_jobs SET state='cancelled' WHERE id=?", (row['id'],))
        cur.execute("DELETE FROM reminder_jobs WHERE state IN ('sent','failed','expired','cancelled') AND due_ts < ?", ((at - dt.timedelta(days=30)).isoformat(),))
        cur.execute("UPDATE reminder_jobs SET state='expired' WHERE state IN ('pending','sending') AND due_ts < ?",
                    ((at - dt.timedelta(minutes=20)).isoformat(),))


def drain(at=None, sender=None):
    from . import push
    from .integrations import webpush
    injected_time = at is not None
    at = at or clock.now()
    sender = sender or webpush.send
    sent = 0
    # Snapshot IDs only; claims are transactional so concurrent workers cannot send
    # the same reminder while its lease is active.
    with db.cursor() as cur:
        cur.execute('INSERT OR REPLACE INTO worker_heartbeats VALUES (?,?)', ('reminders', clock.now().isoformat()))
        ids = [r['id'] for r in cur.execute("SELECT id FROM reminder_jobs WHERE state IN ('pending','sending') AND due_ts<=? "
            "AND (retry_ts IS NULL OR retry_ts<=?) AND (state!='sending' OR lease_ts IS NULL OR lease_ts<=?) "
            "ORDER BY due_ts LIMIT 100", (at.isoformat(), at.isoformat(), at.isoformat()))]
    for jid in ids:
        if not injected_time:
            at = clock.now()  # long batches must still respect the twenty-minute deadline
        with db.cursor() as cur:
            cur.execute('BEGIN IMMEDIATE')
            job = cur.execute('SELECT * FROM reminder_jobs WHERE id=?', (jid,)).fetchone()
            if not job or job['state'] not in ('pending', 'sending'):
                continue
            if (job['retry_ts'] and job['retry_ts'] > at.isoformat()) or (job['state'] == 'sending' and job['lease_ts'] and job['lease_ts'] > at.isoformat()):
                continue
            if dt.datetime.fromisoformat(job['due_ts']) < at - dt.timedelta(minutes=20):
                cur.execute("UPDATE reminder_jobs SET state='expired' WHERE id=?", (jid,))
                continue
            sub = cur.execute('SELECT * FROM push_subscriptions WHERE id=? AND user_id=?', (job['subscription_id'], job['user_id'])).fetchone()
            if not sub:
                cur.execute('DELETE FROM reminder_jobs WHERE id=?', (jid,))
                continue
            lease = (at + dt.timedelta(minutes=2)).isoformat()
            cur.execute("UPDATE reminder_jobs SET state='sending',lease_ts=?,attempts=attempts+1 WHERE id=?", (lease, jid))
            job, sub = dict(job), dict(sub)
        try:
            code = sender(sub['endpoint'], sub['p256dh'], sub['auth'], db.jl(job['payload'], {}),
                          private_b64u=push.vapid_private(), contact=config.PUSH_CONTACT)
        except Exception:
            code = 0
        with db.cursor() as cur:
            cur.execute('INSERT OR REPLACE INTO worker_heartbeats VALUES (?,?)', ('reminders', clock.now().isoformat()))
            if code in (404, 410):
                cur.execute('DELETE FROM push_subscriptions WHERE id=?', (sub['id'],))
                cur.execute('DELETE FROM push_sent WHERE subscription_id=?', (sub['id'],))
            elif 200 <= code < 300:
                cur.execute("UPDATE reminder_jobs SET state='sent',last_code=?,lease_ts=NULL WHERE id=? AND state='sending' AND lease_ts=?", (code, jid, lease))
                # Legacy audit compatibility; live events use the durable queue audit.
                if job['event_key'].split(':', 1)[0].isdigit():
                    cur.execute('INSERT OR IGNORE INTO push_sent VALUES (?,?,?,?)',
                        (sub['id'], int(job['event_key'].split(':', 1)[0]), job['due_ts'], at.isoformat()))
                sent += 1
            else:
                retryable = code == 0 or code == 429 or code >= 500
                state = 'pending' if retryable and job['attempts'] < 5 else 'failed'
                retry = at + dt.timedelta(seconds=min(600, 60 * 2 ** job['attempts']))
                cur.execute('UPDATE reminder_jobs SET state=?,last_code=?,lease_ts=NULL,retry_ts=? WHERE id=? AND lease_ts=?',
                            (state, code, retry.isoformat(), jid, lease))
    with db.cursor() as cur:
        cur.execute('INSERT OR REPLACE INTO worker_heartbeats VALUES (?,?)', ('reminders', clock.now().isoformat()))
    return sent
