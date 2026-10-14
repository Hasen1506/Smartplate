"""Operations endpoints: /cron/tick (reminders that survive a sleeping server) and
/admin/metrics (retention and reminder health). Both are off - 404, as if they did not
exist - until their key is set, and need that key on every call.

  SMARTPLATE_CRON_KEY   GET or POST /cron/tick       header  Authorization: Bearer <key>
                                                     (or X-Cron-Key: <key>)
  SMARTPLATE_ADMIN_KEY  GET /admin/metrics           header  Authorization: Bearer <key>

See docs/reliability.md for the free scheduler set-up.
"""
from __future__ import annotations

import datetime as dt
import hmac
import time

from flask import Blueprint, jsonify, request

from . import config

bp = Blueprint("ops", __name__)
BOOTED = time.time()
# Requests that are not a person using the app (they don't keep the database awake).
NOT_ACTIVITY = ("/cron/", "/healthz", "/readyz", "/api/health", "/admin/")


def _presented() -> str:
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return (request.headers.get("X-Cron-Key") or request.headers.get("X-Admin-Key") or "").strip()


def _authorized(key: str) -> bool:
    presented = _presented()
    return bool(key) and bool(presented) and hmac.compare_digest(presented.encode(), key.encode())


def _denied(key: str):
    if not key:
        return jsonify(error="Not found"), 404               # switched off: don't advertise it
    return jsonify(error="This needs the operations key."), 401


@bp.route("/cron/tick", methods=["GET", "POST"])
def cron_tick():
    if not _authorized(config.CRON_KEY):
        return _denied(config.CRON_KEY)
    from . import push
    result = push.tick(force=request.args.get("force") == "1")
    result["uptime_s"] = round(time.time() - BOOTED)
    return jsonify(result)


@bp.get("/admin/metrics")
def admin_metrics():
    if not _authorized(config.ADMIN_KEY):
        return _denied(config.ADMIN_KEY)
    from . import analytics, clock, db
    days = max(1, min(int(request.args.get("days", 30) or 30), 365))
    since = (clock.now() - dt.timedelta(days=days)).isoformat(timespec="seconds")
    with db.cursor() as cur:
        rows = cur.execute("SELECT outcome, COUNT(*) AS n FROM push_sent WHERE sent_ts>=? GROUP BY outcome",
                           (since,)).fetchall()
    return jsonify({"retention": analytics.cohorts(), "funnel": analytics.funnel(days),
                    "reminders": {r["outcome"]: r["n"] for r in rows},
                    "process": {"uptime_s": round(time.time() - BOOTED)}})


def init_app(app) -> None:
    """Register the endpoints and the hooks: analytics events, reminder invalidation and
    the stale-connection replay (smartplate/resilience.py). Called at the end of create_app."""
    from . import analytics, push, resilience
    app.register_blueprint(bp)

    @app.after_request
    def _record_events(response):
        analytics.after_request(response)
        if request.method in ("POST", "PUT", "PATCH", "DELETE") and request.path.startswith("/api/") \
                and response.status_code < 400:
            push.invalidate()                    # a plan may have moved: next tick reads the database
        return response

    app.wsgi_app = resilience.RetryStaleDatabase(app.wsgi_app)
