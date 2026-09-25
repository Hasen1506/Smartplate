"""Push reminders at the order-by time, even when the app is closed.

A browser that opts in gives us a Web Push subscription (endpoint + keys). A single
background thread wakes every minute, works out each subscriber's reminders from their
current plan (the same list as the calendar file, domain/reminders.py), and sends the
ones that are now due. Each reminder is sent at most once per subscription; if the
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
                views[uid] = reminders.upcoming(service.current_plan(uid), at - LATE_OK)
            except Exception:                         # a deleted profile, a plan that can't solve
                log.exception("push: reminders for user %s failed", uid)
                views[uid] = []
        for r in views[uid]:
            if dt.datetime.fromisoformat(r["at"]) <= at and (sub["id"], r["session_id"], r["at"]) not in sent:
                out.append((sub, r))
    return out


def send_due(at: dt.datetime | None = None, sender=None, pairs=None) -> int:
    """Send every due reminder once. `sender` is the delivery seam for tests."""
    sender = sender or webpush.send
    at = at or clock.now()
    count = 0
    for sub, r in (due(at) if pairs is None else pairs):
        payload = {"title": r["title"], "body": r["body"], "url": r["link"] or "/?tab=today",
                   "tag": f"smartplate-{r['session_id']}"}
        try:
            code = sender(sub["endpoint"], sub["p256dh"], sub["auth"], payload,
                          private_b64u=vapid_private(), contact=config.PUSH_CONTACT)
        except Exception:                             # network trouble: try again next tick
            log.warning("push: delivery to %s failed", sub["endpoint"][:40], exc_info=True)
            continue
        with db.cursor() as cur:
            if code in (404, 410):                    # the browser unsubscribed; forget it
                cur.execute("DELETE FROM push_subscriptions WHERE id=?", (sub["id"],))
                cur.execute("DELETE FROM push_sent WHERE subscription_id=?", (sub["id"],))
                continue
            if 200 <= code < 300:
                cur.execute("INSERT OR IGNORE INTO push_sent(subscription_id, session_id, at, sent_ts) VALUES (?,?,?,?)",
                            (sub["id"], r["session_id"], r["at"], at.isoformat(timespec="seconds")))
                count += 1
    return count


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
    from .runtime import state_lock
    while not _stop.wait(config.PUSH_TICK_S):
        try:
            with state_lock:                          # plans may roll forward while we read them
                pairs = due()
            if pairs:                                 # network calls happen outside the lock
                send_due(pairs=pairs)
        except Exception:
            log.exception("push: tick failed")


def start_worker() -> bool:
    """Start the background sender once per process (wsgi.py and run.py call this)."""
    global _worker
    if not config.PUSH_ENABLED or (_worker and _worker.is_alive()):
        return False
    _stop.clear()
    _worker = threading.Thread(target=_loop, name="smartplate-push", daemon=True)
    _worker.start()
    return True


def stop_worker() -> None:
    _stop.set()
