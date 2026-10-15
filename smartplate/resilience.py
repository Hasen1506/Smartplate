"""Writes after an idle spell are never lost to a stale database connection (grill #66).

The founder's report: "after 5 min of no use, typing to save something - no storage
occurs". Neon's free tier closes idle connections and suspends its compute after 5
minutes. db_pg already swaps a connection found dead on its first statement, but a
connection can also die in a way that only shows on a later statement, or the pool can
fail to produce one while Neon wakes; either ended the request with 503
"database_unavailable" and whatever the person typed was not stored.

`RetryStaleDatabase` wraps the WSGI app: when a request ends in that 503 and it
committed no database write (db.writes_committed() == 0, so nothing could be done
twice), the pool is replaced and the whole request runs once more, with its body
replayed. Routes with side effects outside the database (anything that talks to
Swiggy, placing an order) are never replayed.
"""
from __future__ import annotations

import io
import logging

from . import db

log = logging.getLogger("smartplate.db")
MAX_BODY = 256 * 1024                       # app.config MAX_CONTENT_LENGTH
NEVER_REPLAY = ("/swiggy", "/checkout", "/order/place", "/execute", "/cron/")
MARKER = b'"database_unavailable"'


def replayable(environ) -> bool:
    path = environ.get("PATH_INFO", "")
    return not any(part in path for part in NEVER_REPLAY)


class RetryStaleDatabase:
    def __init__(self, app, on_stale=None):
        self.app = app
        self.on_stale = on_stale

    def _run(self, environ):
        captured = {}

        def start_response(status, headers, exc_info=None):
            captured["status"], captured["headers"], captured["exc_info"] = status, headers, exc_info
            return lambda data: captured.setdefault("written", []).append(data)

        db.reset_writes()
        chunks = self.app(environ, start_response)
        try:
            body = b"".join(captured.get("written", [])) + b"".join(chunks)
        finally:
            if hasattr(chunks, "close"):
                chunks.close()
        return captured, body

    def __call__(self, environ, start_response):
        length = environ.get("CONTENT_LENGTH") or "0"
        try:
            size = int(length)
        except ValueError:
            size = 0
        raw = None
        if 0 <= size <= MAX_BODY and replayable(environ):
            raw = environ["wsgi.input"].read(size) if size else b""
            environ["wsgi.input"] = io.BytesIO(raw)
        captured, body = self._run(dict(environ))
        if (raw is not None and captured.get("status", "").startswith("503") and MARKER in body
                and db.writes_committed() == 0):
            log.warning("database unavailable on %s %s with nothing written; new pool, running it again",
                        environ.get("REQUEST_METHOD"), environ.get("PATH_INFO"))
            try:
                (self.on_stale or db.on_stale_connection)()
            except Exception:
                log.warning("could not replace the database pool", exc_info=True)
            retry_env = dict(environ)
            retry_env["wsgi.input"] = io.BytesIO(raw)
            captured, body = self._run(retry_env)
        start_response(captured["status"], captured["headers"], captured.get("exc_info"))
        return [body]
