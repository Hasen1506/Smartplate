"""The live catalogue refresh as a background job (UX audit #16, 16 Oct 2026).

On first open after connecting, the app used to wait on one request that read every saved
place's menu (minutes, live). Now `start` returns at once and the app polls `status`; the
plan is re-optimised when the menus are in. One job per person at a time; the rate limit
is the same as the blocking refresh. State lives in this process (one gunicorn worker,
Procfile), so a restart just means the app starts the job again on its next open.
"""
import logging
import threading

from . import clock, ratelimit

log = logging.getLogger(__name__)
_lock = threading.Lock()
_jobs: dict[int, dict] = {}


def start(plan_id: int, user_id: int) -> dict:
    with _lock:
        job = _jobs.get(user_id)
        running = bool(job and job["state"] == "running")
    if running:
        return status(user_id)
    ratelimit.check(f"live-menus:{user_id}", 10, 3600)
    job = {"state": "running", "plan_id": plan_id, "started": clock.now().isoformat(timespec="seconds")}

    def go():
        from . import service
        from .domain import live_catalog
        from .integrations.swiggy_connect import SwiggyError
        try:
            result = live_catalog.refresh(user_id)
            service.reoptimize(plan_id)
            done = {"state": "done", "refreshed": {k: result.get(k) for k in ("kind", "label", "skipped")}}
        except SwiggyError as exc:
            done = {"state": "error", "error": str(exc), "code": exc.code}
        except Exception:
            log.exception("Background catalogue refresh failed")
            done = {"state": "error", "error": "Reading your Swiggy menus failed. Try again from the Week tab."}
        with _lock:
            _jobs[user_id] = {**job, **done, "finished": clock.now().isoformat(timespec="seconds")}

    t = threading.Thread(target=go, daemon=True, name=f"catalogue-{user_id}")
    job["_thread"] = t
    with _lock:
        _jobs[user_id] = job
    t.start()
    return status(user_id)


def status(user_id: int) -> dict:
    with _lock:
        job = _jobs.get(user_id)
    if not job:
        return {"state": "none"}
    return {k: v for k, v in job.items() if not k.startswith("_")}


def wait(user_id: int, timeout: float = 30.0) -> dict:
    """Tests: wait for this person's job to finish."""
    with _lock:
        job = _jobs.get(user_id)
    t = job.get("_thread") if job else None
    if t:
        t.join(timeout)
    return status(user_id)
