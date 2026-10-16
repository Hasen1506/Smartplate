"""Push reminders at the order-by time, even when the app is closed.

A browser that opts in gives us a Web Push subscription (endpoint + keys). Each
subscriber's reminders come from their current plan (the same list as the calendar file,
domain/reminders.py); the due ones are sent once per subscription.

Two things drive the sending, and either is enough on its own:
  * a background thread in the web process, every PUSH_TICK_S seconds, while it is awake;
  * POST /cron/tick (smartplate/ops.py), called by a free external scheduler every 5 min
    (docs/reliability.md). It wakes a sleeping Render instance and sends what is due, so
    reminders no longer depend on someone having visited in the last 15 minutes.
Both may run at once: a reminder is *claimed* in push_sent before it is sent (the
primary key makes the claim atomic), so it is never sent twice.

Nothing is dropped silently any more (grill #22). A reminder is
  * on time   - up to LATE_OK after its time: sent as is;
  * late      - later than that but before the meal's window closes: sent, saying it
                is late and why;
  * missed    - after the meal's window closed: not sent (an "order lunch" nudge at 4 pm
                is noise), but recorded as missed in push_sent so it shows up in
                /admin/metrics instead of vanishing.
"""
import datetime as dt
import logging
import threading

from . import clock, config, db, vault
from .integrations import webpush

LATE_OK = dt.timedelta(minutes=20)        # later than this, the reminder says it is late
LOOKBACK = dt.timedelta(hours=12)         # how far back a server that slept looks for unsent reminders
QUIET_MAX = dt.timedelta(minutes=60)      # a tick re-reads the database at least this often (and at midnight)
log = logging.getLogger("smartplate.push")
_worker: threading.Thread | None = None
_stop = threading.Event()
# Subscriptions whose push service answered 429/5xx wait before the next try (L-12).
_backoff: dict[int, tuple[dt.datetime, int]] = {}
BACKOFF_FIRST, BACKOFF_MAX = dt.timedelta(minutes=1), dt.timedelta(minutes=30)


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
    """`scope`: "all" (a reminder for every meal: the alerts switch) or "meals" (only the
    meals the person tapped "Remind me" on). Without it the person's choice stays as it is."""
    scope = body.get("scope") if isinstance(body, dict) else None
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
    if scope in ("all", "meals"):
        from . import today
        if scope == "all" or today.reminder_filter(user_id) is not None or not _has_scope(user_id):
            today.set_reminder_scope(user_id, scope)
    return status(user_id, endpoint)


def _has_scope(user_id: int) -> bool:
    from . import today
    today.init_schema()
    with db.cursor() as cur:
        return cur.execute("SELECT 1 FROM reminder_prefs WHERE user_id=?", (user_id,)).fetchone() is not None


def unsubscribe(user_id: int, body: dict) -> dict:
    endpoint = body.get("endpoint")
    with db.cursor() as cur:
        cur.execute("DELETE FROM push_subscriptions WHERE user_id=? AND endpoint=?", (user_id, endpoint))
    return status(user_id, endpoint)


def status(user_id: int, endpoint: str | None = None) -> dict:
    with db.cursor() as cur:
        rows = cur.execute("SELECT endpoint FROM push_subscriptions WHERE user_id=? ORDER BY id", (user_id,)).fetchall()
    from . import today
    scope = "all" if today.reminder_filter(user_id) is None else "meals"
    return {"enabled": config.PUSH_ENABLED, "public_key": public_key(), "devices": len(rows), "scope": scope,
            "this_device": bool(endpoint) and any(r["endpoint"] == endpoint for r in rows)}


def meal_closes(r: dict) -> dt.datetime | None:
    """When the reminder's meal window closes (after that a nudge is pointless)."""
    from .domain import models
    if r.get("closes"):                               # the person's own meal time (usab #14)
        try:
            return dt.datetime.fromisoformat(r["closes"])
        except (TypeError, ValueError):
            pass
    try:
        window = models.MEAL_WINDOWS[r["meal"]]
        close_min = window[3] if len(window) > 3 else window[1] + 60
        return dt.datetime.combine(dt.date.fromisoformat(r["date"]), dt.time()) + dt.timedelta(minutes=close_min)
    except (KeyError, TypeError, ValueError, IndexError):
        return None


def classify(r: dict, at: dt.datetime) -> str:
    """on_time | late | missed, for a reminder whose time has come."""
    when = dt.datetime.fromisoformat(r["at"])
    if at - when <= LATE_OK:
        return "on_time"
    if r.get("order_by"):                 # a cart-open nudge is still in time until order-by (#21)
        try:
            if at < dt.datetime.fromisoformat(r["order_by"]):
                return "on_time"
        except (TypeError, ValueError):
            pass
    closes = meal_closes(r)
    if closes is not None and at >= closes:
        return "missed"
    if closes is None and at - when > LOOKBACK:
        return "missed"
    return "late"


def _upcoming(uid: int, since: dt.datetime) -> list[dict]:
    from . import service
    from .domain import reminders
    from .runtime import user_lock
    with user_lock(uid):                              # only this profile's requests wait (M-05)
        return reminders.upcoming(service.current_plan(uid), since)


def _meal_filter(uid: int):
    from . import today
    return today.reminder_filter(uid)


def due(at: dt.datetime | None = None, _next: list | None = None) -> list[tuple[dict, dict]]:
    """(subscription, reminder) pairs whose time has come and that were never sent or
    recorded. Each reminder carries `outcome` (on_time | late | missed) and `late_min`.
    `_next`, when given, receives the earliest reminder time still in the future."""
    at = at or clock.now()
    with db.cursor() as cur:
        subs = [db.row_to_dict(r) for r in cur.execute("SELECT * FROM push_subscriptions ORDER BY user_id, id")]
        # One reminder per meal per subscription, even if a re-plan moves its order-by time (L-12).
        sent = {(r["subscription_id"], r["session_id"]) for r in cur.execute("SELECT * FROM push_sent")}
    out, views = [], {}
    for sub in subs:
        uid = sub["user_id"]
        if uid not in views:
            try:
                views[uid] = _upcoming(uid, at - LOOKBACK)
                only = _meal_filter(uid)              # per-meal "Remind me" (usab #22)
                if only is not None:
                    views[uid] = [r for r in views[uid] if (r["date"], r["meal"]) in only]
            except Exception:                         # a deleted profile, a plan that can't solve
                log.exception("push: reminders for user %s failed", uid)
                views[uid] = []
        try:
            subscribed = dt.datetime.fromisoformat(sub["created_ts"])
        except (TypeError, ValueError):
            subscribed = None
        for r in views[uid]:
            when = dt.datetime.fromisoformat(r["at"])
            if when > at:
                if _next is not None:
                    _next.append(when)
                continue
            if (sub["id"], r["session_id"]) in sent:
                continue
            if subscribed is not None and when < subscribed:
                continue                              # due before this device opted in: not its reminder
            out.append((sub, {**r, "outcome": classify(r, at),
                              "late_min": max(0, int((at - when).total_seconds() // 60))}))
    return out


def payload_for(r: dict) -> dict:
    title, body = r["title"], r["body"]
    if r.get("outcome") == "late":
        title = f"Late reminder: {title}"
        body = (f"Sorry, this is {r['late_min']} min late (Ziggy's server was asleep). " + body).strip()
    return {"title": title, "body": body, "url": r.get("link") or "/?tab=today",
            "tag": f"smartplate-{r['session_id']}"}


def _claim(sub: dict, r: dict, at: dt.datetime, outcome: str) -> bool:
    """Record the reminder before sending it. False: another tick already has it."""
    with db.cursor() as cur:
        if cur.execute("SELECT 1 FROM push_sent WHERE subscription_id=? AND session_id=?",
                       (sub["id"], r["session_id"])).fetchone():
            return False
        cur.execute("INSERT OR IGNORE INTO push_sent(subscription_id, session_id, at, sent_ts, outcome) "
                    "VALUES (?,?,?,?,?)", (sub["id"], r["session_id"], r["at"], at.isoformat(timespec="seconds"), outcome))
        return cur.rowcount == 1


def _settle(sub: dict, r: dict, outcome: str | None) -> None:
    """Final state of a claimed reminder; None releases the claim so the next tick retries."""
    with db.cursor() as cur:
        if outcome is None:
            cur.execute("DELETE FROM push_sent WHERE subscription_id=? AND session_id=? AND at=? AND outcome='sending'",
                        (sub["id"], r["session_id"], r["at"]))
        else:
            cur.execute("UPDATE push_sent SET outcome=? WHERE subscription_id=? AND session_id=? AND at=?",
                        (outcome, sub["id"], r["session_id"], r["at"]))


def deliver(at: dt.datetime | None = None, sender=None, pairs=None) -> dict:
    """Send every due reminder once; record missed ones. Returns counts by outcome."""
    sender = sender or webpush.send
    at = at or clock.now()
    stats = {"sent": 0, "late": 0, "missed": 0, "failed": 0, "rejected": 0, "gone": 0, "busy": 0}
    for sub, r in (due(at) if pairs is None else pairs):
        with db.cursor() as cur:
            active = cur.execute("SELECT 1 FROM push_subscriptions WHERE id=? AND user_id=? AND endpoint=?",
                                 (sub["id"], sub["user_id"], sub["endpoint"])).fetchone()
        if not active:  # profile deleted or alerts disabled after the due snapshot
            continue
        outcome = r.get("outcome", "on_time")
        if outcome == "missed":
            if _claim(sub, r, at, "missed"):
                stats["missed"] += 1
                log.info("push: reminder for meal %s missed (%s min late, meal window closed)",
                         r["session_id"], r.get("late_min"))
            continue
        wait = _backoff.get(sub["id"])
        if wait and wait[0] > at:
            continue
        if not _claim(sub, r, at, "sending"):
            continue                                  # another tick (thread or cron) has it
        try:
            code = sender(sub["endpoint"], sub["p256dh"], sub["auth"], payload_for(r),
                          private_b64u=vapid_private(), contact=config.PUSH_CONTACT)
        except Exception:                             # network trouble: try again next tick
            log.warning("push: delivery to %s failed", sub["endpoint"][:40], exc_info=True)
            _settle(sub, r, None)
            stats["failed"] += 1
            continue
        if code in (404, 410):                        # the browser unsubscribed; forget it
            with db.cursor() as cur:
                cur.execute("DELETE FROM push_subscriptions WHERE id=?", (sub["id"],))
                cur.execute("DELETE FROM push_sent WHERE subscription_id=?", (sub["id"],))
            stats["gone"] += 1
            continue
        if code == 429 or code >= 500:                # push service busy: back off, don't hammer it
            tries = (wait[1] if wait else 0) + 1
            _backoff[sub["id"]] = (at + min(BACKOFF_FIRST * 2 ** (tries - 1), BACKOFF_MAX), tries)
            _settle(sub, r, None)
            stats["busy"] += 1
            continue
        if not 200 <= code < 300:                     # 400/413 etc.: this payload will never be accepted
            _settle(sub, r, "rejected")
            stats["rejected"] += 1
            log.warning("push: %s rejected a reminder with HTTP %s; not retrying", sub["endpoint"][:40], code)
            continue
        _backoff.pop(sub["id"], None)
        _settle(sub, r, "late" if outcome == "late" else "sent")
        stats["late" if outcome == "late" else "sent"] += 1
    return stats


def send_due(at: dt.datetime | None = None, sender=None, pairs=None) -> int:
    """Send every due reminder once (late ones say so). Returns how many were delivered.
    `sender` is the delivery seam for tests."""
    stats = deliver(at, sender=sender, pairs=pairs)
    return stats["sent"] + stats["late"]


# --------------------------------------------------------------------------- #
# Ticks: the in-process thread and POST /cron/tick share this.
# --------------------------------------------------------------------------- #
_tick_lock = threading.Lock()
_quiet_until: list = [None]       # nothing can be due before this (no database read needed)


def invalidate() -> None:
    """A plan or subscription may have changed: the next tick reads the database."""
    _quiet_until[0] = None


def tick(at: dt.datetime | None = None, sender=None, *, force: bool = False) -> dict:
    """One sending round. Skips the database while nothing can be due (so an external
    pinger every 5 minutes doesn't keep a free Neon database awake all day), and never
    runs twice at once in this process (the push_sent claim covers other processes)."""
    at = at or clock.now()
    if not config.PUSH_ENABLED:
        return {"ok": True, "skipped": "push disabled"}
    quiet = _quiet_until[0]
    if not force and quiet is not None and at < quiet:
        return {"ok": True, "skipped": "nothing due", "next_check": quiet.isoformat(timespec="seconds")}
    if not _tick_lock.acquire(blocking=False):
        return {"ok": True, "skipped": "a tick is already running"}
    try:
        upcoming: list = []
        pairs = due(at, _next=upcoming)               # per-profile locks inside, never the whole API
        stats = deliver(at, sender=sender, pairs=pairs) if pairs else {}
        try:                                          # opt-in "auto-fill my cart" meals whose window opened
            from .domain import autofill
            auto = autofill.run_due(at, sender=sender)
            if any(auto.values()):
                stats = {**stats, "auto_cart": auto}
            upcoming += autofill.next_opening(at)
        except Exception:
            log.exception("push: auto-fill carts failed")
        retry_soon = any(stats.get(k) for k in ("failed", "busy"))
        midnight = dt.datetime.combine(at.date() + dt.timedelta(days=1), dt.time())   # a new day/week's plan
        nxt = min([at + QUIET_MAX, midnight] + upcoming)
        _quiet_until[0] = None if retry_soon else nxt
        return {"ok": True, "checked_at": at.isoformat(timespec="seconds"), **stats,
                "next_check": None if retry_soon else nxt.isoformat(timespec="seconds")}
    finally:
        _tick_lock.release()


def test_message(user_id: int, sender=None) -> dict:
    """Send "Reminders are on" to this profile's devices right away."""
    sender = sender or webpush.send
    with db.cursor() as cur:
        subs = [db.row_to_dict(r) for r in cur.execute("SELECT * FROM push_subscriptions WHERE user_id=? ORDER BY id", (user_id,))]
    ok = 0
    for sub in subs:
        code = sender(sub["endpoint"], sub["p256dh"], sub["auth"],
                      {"title": "Ziggy reminders are on", "body": "You'll get a nudge at each order-by time.",
                       "url": "/?tab=today", "tag": "smartplate-test"},
                      private_b64u=vapid_private(), contact=config.PUSH_CONTACT)
        ok += 200 <= code < 300
    return {"sent": ok, "devices": len(subs)}


def _loop():
    while not _stop.wait(config.PUSH_TICK_S):
        try:
            tick()
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
