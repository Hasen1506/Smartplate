"""Observability (grill #67): request IDs, structured logs, latency, optional Sentry.

* Every request gets an id: the caller's X-Request-ID (or Render's Rndr-Id) when it is a
  plain token, else a new one. It is returned in the X-Request-ID header, added to the
  JSON body of every 5xx answer ("quote this when you report it"), and stamped on every
  log line written while the request runs.
* Logs are one JSON object per line when SMARTPLATE_LOG_FORMAT=json (the default on
  Render), plain text otherwise. Each request writes one `request` line with its route,
  status and milliseconds; slower than SMARTPLATE_SLOW_MS is a WARNING.
* `timed("swiggy", tool=...)` writes the same kind of line for an outbound call, as
  Swiggy's go-live checklist expects (tool, ms, status).
* SENTRY_DSN switches on Sentry error reporting when the sentry-sdk package is
  installed; unset (or not installed) it is a no-op. No request bodies, cookies or
  headers are sent (send_default_pii=False).
"""
from __future__ import annotations

import json
import logging
import re
import sys
import time
import uuid
from contextlib import contextmanager

from . import config

log = logging.getLogger("smartplate.http")
_RID_OK = re.compile(r"^[A-Za-z0-9._:-]{6,80}$")
_NO_REQUEST = "-"


def current_request_id() -> str:
    try:
        from flask import g, has_request_context
        if has_request_context():
            return getattr(g, "request_id", _NO_REQUEST)
    except Exception:
        pass
    return _NO_REQUEST


class RequestIdFilter(logging.Filter):
    def filter(self, record):
        if not hasattr(record, "request_id"):
            record.request_id = current_request_id()
        return True


class JsonFormatter(logging.Formatter):
    """One JSON object per line. Extra structured fields ride in `record.fields`."""

    def format(self, record):
        out = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)) + f".{int(record.msecs):03d}Z",
               "level": record.levelname, "logger": record.name, "msg": record.getMessage(),
               "request_id": getattr(record, "request_id", _NO_REQUEST)}
        fields = getattr(record, "fields", None)
        if isinstance(fields, dict):
            out.update({k: v for k, v in fields.items() if k not in out})
        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)
        return json.dumps(out, default=str, ensure_ascii=False)


TEXT_FORMAT = "%(asctime)s %(levelname)s %(name)s [%(request_id)s] %(message)s"
_installed: list = []


def init_logging(fmt: str | None = None, stream=None) -> logging.Handler:
    """Give the app's loggers ("smartplate.*") one handler, once per process."""
    fmt = (fmt or config.LOG_FORMAT or "text").lower()
    root = logging.getLogger("smartplate")
    if _installed:
        return _installed[0]
    handler = logging.StreamHandler(stream or sys.stderr)
    handler.addFilter(RequestIdFilter())
    handler.setFormatter(JsonFormatter() if fmt == "json" else logging.Formatter(TEXT_FORMAT))
    root.addHandler(handler)
    if root.level == logging.NOTSET or root.level > logging.INFO:
        root.setLevel(logging.INFO)
    _installed.append(handler)
    return handler


def init_sentry(dsn: str | None = None) -> bool:
    """Report errors to Sentry when a DSN is set and sentry-sdk is installed."""
    dsn = config.SENTRY_DSN if dsn is None else dsn
    if not dsn:
        return False
    try:
        import sentry_sdk
        from sentry_sdk.integrations.flask import FlaskIntegration
    except ImportError:
        logging.getLogger("smartplate").warning("SENTRY_DSN is set but sentry-sdk is not installed; "
                                                "add sentry-sdk[flask] to requirements.txt")
        return False
    sentry_sdk.init(dsn=dsn, integrations=[FlaskIntegration()], send_default_pii=False,
                    traces_sample_rate=config.SENTRY_TRACES, environment="production" if config.PRODUCTION else "dev")
    return True


def _new_request_id() -> str:
    from flask import request
    for header in ("X-Request-ID", "Rndr-Id"):
        value = (request.headers.get(header) or "").strip()
        if _RID_OK.match(value):
            return value
    return uuid.uuid4().hex[:16]


def init_app(app) -> None:
    """Install the request hooks. Call once from create_app()."""
    from flask import g, request
    init_logging()
    init_sentry()                            # no-op unless SENTRY_DSN is set

    @app.before_request
    def _start_request():
        g.request_id = _new_request_id()
        g.request_t0 = time.perf_counter()
        if config.SENTRY_DSN:
            try:
                import sentry_sdk
                sentry_sdk.set_tag("request_id", g.request_id)
            except ImportError:
                pass

    @app.after_request
    def _finish_request(response):
        rid = getattr(g, "request_id", None) or _new_request_id()
        response.headers["X-Request-ID"] = rid
        if response.status_code >= 500 and response.is_json:
            body = response.get_json(silent=True)
            if isinstance(body, dict) and "request_id" not in body:
                body["request_id"] = rid
                response.set_data(json.dumps(body))
        t0 = getattr(g, "request_t0", None)
        ms = round((time.perf_counter() - t0) * 1000, 1) if t0 is not None else None
        route = request.url_rule.rule if request.url_rule is not None else "(unmatched)"
        quiet = request.path in ("/healthz", "/api/health") or request.path.startswith("/static/")
        level = (logging.WARNING if (ms or 0) >= config.SLOW_REQUEST_MS or response.status_code >= 500
                 else logging.DEBUG if quiet else logging.INFO)
        log.log(level, "%s %s %s %sms", request.method, route, response.status_code, ms,
                extra={"fields": {"event": "request", "method": request.method, "route": route,
                                  "status": response.status_code, "ms": ms}})
        return response


@contextmanager
def timed(kind: str, **fields):
    """Log one line for an outbound call: kind, fields, ms and status ("ok"/error name).
    Set `info["status"]` inside the block to record an HTTP or tool status."""
    info = {"status": "ok"}
    t0 = time.perf_counter()
    try:
        yield info
    except BaseException as exc:
        info["status"] = type(exc).__name__
        raise
    finally:
        ms = round((time.perf_counter() - t0) * 1000, 1)
        logging.getLogger(f"smartplate.{kind}").info(
            "%s call %s %s %sms", kind, fields.get("tool", ""), info["status"], ms,
            extra={"fields": {"event": f"{kind}_call", **fields, "status": info["status"], "ms": ms}})
