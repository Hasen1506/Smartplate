"""Push reminders at the order-by time, even when the app is closed.

A browser that opts in gives us a Web Push subscription (endpoint + keys). A single
background thread wakes every minute, works out each subscriber's reminders from their
current plan (the same list as the calendar file, domain/reminders.py), and sends the
ones that are now due. Reminder jobs persist across restarts with bounded retries and delivery leases; if the
server was asleep, one that is up to 20 minutes late is still sent, older ones are
dropped (an "order now" nudge an hour late is noise).

Hosting note: the thread only runs while the server is awake. Render's free plan
sleeps after ~15 minutes without visits, so reliable reminders need an always-on
instance. The calendar file keeps working either way.
"""
import datetime as dt
import logging
import threading

from . import clock, config, db, vault
from .integrations import webpush

LATE_OK = dt.timedelta(minutes=20)
log = logging.getLogger("smartplate.push")
_worker: threading.Thread | None = None
_stop = threading.Event()


def vapid_private() -> str:
    return config.VAPID_PRIVATE or vault.stored("vapid_private", webpush.new_private_key)


def public_key() -> str:
    return webpush.public_key_b64u(vapid_private())


# Browsers' push services. The server POSTs to a subscription's endpoint, so only these
# hosts are accepted (anything else would let a caller aim the server at other sites).
PUSH_HOSTS = ("fcm.googleapis.com", ".push.services.mozilla.com", ".notify.windows.com",
              "web.push.apple.com", ".push.apple.com")


def _push_host(endpoint: str) -> bool:
    from urllib.parse import urlsplit
    parts = urlsplit(endpoint)
    host = (parts.hostname or "").lower()
    return parts.port in (None, 443) and any(host == h.lstrip(".") or (h.startswith(".") and host.endswith(h))
                                             for h in PUSH_HOSTS)


def subscribe(user_id: int, body: dict) -> dict:
    sub = body.get("subscription") if isinstance(body.get("subscription"), dict) else body
    endpoint = sub.get("endpoint")
    keys = sub.get("keys") if isinstance(sub.get("keys"), dict) else {}
    if not isinstance(endpoint, str) or not endpoint.startswith("https://") or len(endpoint) > 1000 \
            or not _push_host(endpoint):
        raise ValueError("That isn't a push subscription this app can use")
    p256dh, auth = keys.get("p256dh"), keys.get("auth")
    try:
        if len(webpush.unb64u(p256dh)) != 65 or len(webpush.unb64u(auth)) != 16:
            raise ValueError
    except Exception:
        raise ValueError("The push subscription's keys are missing or malformed") from None
    with db.cursor() as cur:
        cur.execute("INSERT INTO push_subscriptions(user_id, endpoint, p256dh, auth, created_ts) VALUES (?,?,?,?,?) "
                    "ON CONFLICT(endpoint) DO UPDATE SET user_id=excluded.user_id, p256dh=excluded.p256dh, "
                    "auth=excluded.auth", (user_id, endpoint, p256dh, auth, clock.now().isoformat(timespec="seconds")))
    return status(user_id, endpoint)


def unsubscribe(user_id: int, body: dict) -> dict:
    endpoint = body.get("endpoint")
    with db.cursor() as cur:
        cur.execute("DELETE FROM push_subscriptions WHERE user_id=? AND endpoint=?", (user_id, endpoint))
    return status(user_id, endpoint)


def status(user_id: int, endpoint: str | None = None) -> dict:
    with db.cursor() as cur:
        rows = cur.execute("SELECT endpoint FROM push_subscriptions WHERE user_id=?", (user_id,)).fetchall()
    return {"enabled": config.PUSH_ENABLED, "public_key": public_key(), "devices": len(rows),
            "this_device": bool(endpoint) and any(r["endpoint"] == endpoint for r in rows)}


def due(at: dt.datetime | None = None) -> list[tuple[dict, dict]]:
    """(subscription, reminder) pairs to send now: due, not too late, not sent yet."""
    from . import service
    from .domain import reminders
    at = at or clock.now()
    with db.cursor() as cur:
        subs = [db.row_to_dict(r) for r in cur.execute("SELECT * FROM push_subscriptions ORDER BY user_id, id")]
        sent = {(r["subscription_id"], r["session_id"], r["at"]) for r in cur.execute("SELECT * FROM push_sent")}
    out, views = [], {}
    for sub in subs:
        uid = sub["user_id"]
        if uid not in views:
            try:
                views[uid] = _reminders(uid, at - LATE_OK)
            except Exception:                         # a deleted profile, a plan that can't solve
                log.exception("push: reminders for user %s failed", uid)
                views[uid] = []
        for r in views[uid]:
            if dt.datetime.fromisoformat(r["at"]) <= at and (sub["id"], r["session_id"], r["at"]) not in sent:
                out.append((sub, r))
    return out


def _reminders(user_id, since):
    from . import live_core, service
    from .domain import reminders
    from .integrations import swiggy_connect
    if swiggy_connect.private_owner(user_id):
        if not swiggy_connect.status(user_id).get('connected'):
            return []
        return live_core.reminders(user_id, since)
    return reminders.upcoming(service.current_plan(user_id), since)


def send_due(at: dt.datetime | None = None, sender=None, pairs=None) -> int:
    from . import reminder_queue
    at = at or clock.now()
    reconcile = pairs is None
    if pairs is None:
        from .runtime import state_lock
        with state_lock:
            pairs, reconcile = _scheduled_pairs(at)
            reminder_queue.enqueue(pairs, at, reconcile=reconcile)
    else:
        reminder_queue.enqueue(pairs, at, reconcile=False)
    return reminder_queue.drain(at, sender)


def _scheduled_pairs(at):
    reconcile = True
    with db.cursor() as cur:
        subs = [dict(r) for r in cur.execute('SELECT * FROM push_subscriptions')]
    pairs, views = [], {}
    for sub in subs:
        try:
            if sub['user_id'] not in views:
                views[sub['user_id']] = _reminders(sub['user_id'], at - LATE_OK)
            pairs.extend((sub, r) for r in views[sub['user_id']])
        except Exception:
            log.warning('Reminder schedule unavailable; user_id=%s', sub['user_id'])
            reconcile = False  # a transient read error must not cancel persisted jobs
    return pairs, reconcile


def test_message(user_id: int, sender=None) -> dict:
    """Send "Reminders are on" to this profile's devices right away."""
    sender = sender or webpush.send
    with db.cursor() as cur:
        subs = [db.row_to_dict(r) for r in cur.execute("SELECT * FROM push_subscriptions WHERE user_id=?", (user_id,))]
    ok = 0
    for sub in subs:
        code = sender(sub["endpoint"], sub["p256dh"], sub["auth"],
                      {"title": "SmartPlate reminders are on", "body": "You'll get a nudge at each order-by time.",
                       "url": "/?tab=today", "tag": "smartplate-test"},
                      private_b64u=vapid_private(), contact=config.PUSH_CONTACT)
        ok += 200 <= code < 300
    return {"sent": ok, "devices": len(subs)}


def _loop():
    while not _stop.wait(config.PUSH_TICK_S):
        try:
            send_due()
        except Exception:
            log.exception("push: tick failed")


def start_worker() -> bool:
    """Start the background sender once per process (wsgi.py and run.py call this)."""
    global _worker
    if not config.PUSH_ENABLED or not config.PUSH_WORKER or (_worker and _worker.is_alive()):
        return False
    _stop.clear()
    _worker = threading.Thread(target=_loop, name="smartplate-push", daemon=True)
    _worker.start()
    return True


def stop_worker() -> None:
    _stop.set()
