"""Product analytics, self-hosted: one `events` table is the source of truth (grill #21).

Five events answer "do people come back, and does the loop work?":

  app_open           the app loaded a profile's plan (deduped: once per 30 min per person)
  plan_viewed        a week plan was opened                (once per 10 min per plan)
  cart_filled        a real or simulated cart was filled for a meal
  ordered_confirmed  an order was placed or a meal confirmed as had
  budget_overrun     a plan view showed spend above its budget (once per plan per day)

They are recorded server-side from the API routes that already mean these things
(`after_request` below), so the frontend needs no SDK, no third-party script and no
cookie banner. Props never carry free text, names or addresses: only ids and amounts.

`cohorts()` is the D1/D7/D30 retention query, portable across SQLite and Postgres (it
reads distinct (user, day) pairs and does the arithmetic in Python). It is served at
/admin/metrics behind SMARTPLATE_ADMIN_KEY (smartplate/ops.py).

Recording an event can never break the request it rides on: every failure is logged
and swallowed.
"""
from __future__ import annotations

import datetime as dt
import json
import logging

from . import clock, db

log = logging.getLogger("smartplate.analytics")

EVENTS = ("app_open", "plan_viewed", "cart_filled", "ordered_confirmed", "budget_overrun")
RETENTION_DAYS = (1, 7, 30)
_PROP_KEYS = {"plan_id", "session_id", "amount", "budget", "spend", "route", "source"}


def _clean_props(props: dict | None) -> dict:
    out = {}
    for k, v in (props or {}).items():
        if k in _PROP_KEYS and (v is None or isinstance(v, (int, float, str))):
            out[k] = v if not isinstance(v, str) else v[:80]
    return out


def track(name: str, user_id: int | None = None, props: dict | None = None, *,
          once_per_s: int | None = None, dedupe_key: str | None = None, at: dt.datetime | None = None) -> bool:
    """Record one event. Returns True when a row was written.

    once_per_s: skip when the same event (same user, same dedupe_key prop) was recorded
    within that many seconds, so a page that reloads its plan ten times is one open."""
    if name not in EVENTS:
        raise ValueError(f"unknown analytics event {name!r}")
    at = at or clock.now()
    clean = _clean_props(props)
    try:
        with db.cursor() as cur:
            if once_per_s:
                since = (at - dt.timedelta(seconds=once_per_s)).isoformat(timespec="seconds")
                rows = cur.execute("SELECT props FROM events WHERE name=? AND user_id IS NULL AND ts>=?",
                                   (name, since)).fetchall() if user_id is None else \
                    cur.execute("SELECT props FROM events WHERE name=? AND user_id=? AND ts>=?",
                                (name, user_id, since)).fetchall()
                if dedupe_key is None and rows:
                    return False
                if dedupe_key is not None and any(db.jl(r["props"], {}).get(dedupe_key) == clean.get(dedupe_key)
                                                  for r in rows):
                    return False
            cur.execute("INSERT INTO events(user_id, name, ts, props) VALUES (?,?,?,?)",
                        (user_id, name, at.isoformat(timespec="seconds"), json.dumps(clean, sort_keys=True)))
        return True
    except Exception:                                    # analytics never breaks a request
        log.warning("analytics: could not record %s", name, exc_info=True)
        return False


# --------------------------------------------------------------------------- #
# Which API routes mean which event (recorded after a 2xx answer).
# --------------------------------------------------------------------------- #
# (method, Flask url rule) -> (event, once_per_s, dedupe prop)
ROUTE_EVENTS = {
    ("GET", "/api/user/<int:user_id>/plan"): ("app_open", 1800, None),
    ("GET", "/api/plan/<int:plan_id>"): ("plan_viewed", 600, "plan_id"),
    ("POST", "/api/session/<int:session_id>/order/cart"): ("cart_filled", None, None),
    ("POST", "/api/session/<int:session_id>/swiggy-cart"): ("cart_filled", None, None),
    ("POST", "/api/user/<int:user_id>/swiggy/live-cart"): ("cart_filled", None, None),
    ("POST", "/api/session/<int:session_id>/order/place"): ("ordered_confirmed", None, None),
    ("POST", "/api/user/<int:user_id>/swiggy/checkout"): ("ordered_confirmed", None, None),
    ("POST", "/api/session/<int:session_id>/confirm"): ("ordered_confirmed", None, None),
}
# Routes that answer with a whole plan view: a view over budget is a budget_overrun.
PLAN_VIEW_RULES = {"/api/user/<int:user_id>/plan", "/api/plan/<int:plan_id>",
                   "/api/plan/<int:plan_id>/optimize", "/api/plan"}


def _owner(view_args: dict) -> int | None:
    from . import access
    try:
        owners = sorted(o for o in access.owners_of(view_args or {}, None, None) if o is not None)
    except Exception:
        return None
    return owners[0] if owners else None


def record_request(method: str, rule: str | None, view_args: dict | None, status: int, body=None) -> list[str]:
    """Record the events one answered request implies. Returns the names recorded."""
    if not rule or not 200 <= status < 300:
        return []
    view_args = dict(view_args or {})
    done = []
    hit = ROUTE_EVENTS.get((method, rule))
    uid = None
    if hit or rule in PLAN_VIEW_RULES:
        uid = _owner(view_args)
    if hit:
        name, once, key = hit
        props = {k: view_args[k] for k in ("plan_id", "session_id") if k in view_args}
        props["route"] = rule
        if track(name, uid, props, once_per_s=once, dedupe_key=key):
            done.append(name)
    if rule in PLAN_VIEW_RULES and isinstance(body, dict):
        env = body.get("budget") if isinstance(body.get("budget"), dict) else None
        plan_id = (body.get("plan") or {}).get("id") if isinstance(body.get("plan"), dict) else None
        if env and env.get("over") and plan_id is not None:
            uid = uid or (body.get("user") or {}).get("id")
            if track("budget_overrun", uid, {"plan_id": plan_id, "budget": env.get("budget"),
                                             "spend": env.get("spend")},
                     once_per_s=86400, dedupe_key="plan_id"):
                done.append("budget_overrun")
    return done


def after_request(response):
    """Flask hook (registered in app.py)."""
    try:
        from flask import request
        rule = request.url_rule.rule if request.url_rule is not None else None
        if rule is None or not rule.startswith("/api/"):
            return response
        if (request.method, rule) not in ROUTE_EVENTS and rule not in PLAN_VIEW_RULES:
            return response
        body = response.get_json(silent=True) if rule in PLAN_VIEW_RULES and response.is_json else None
        record_request(request.method, rule, request.view_args, response.status_code, body)
    except Exception:
        log.warning("analytics: after_request failed", exc_info=True)
    return response


# --------------------------------------------------------------------------- #
# Retention
# --------------------------------------------------------------------------- #
def cohorts(today: dt.date | None = None) -> dict:
    """D1/D7/D30 retention by first-seen week.

    A person's cohort day is the day of their first event. They count as retained on Dk
    when they have any event on cohort day + k ("classic" retention, the strict one);
    `retained_by` also reports "came back on or after Dk" (unbounded retention). A
    cohort day too recent to have reached Dk is reported as not yet measurable (None)."""
    today = today or clock.today()
    with db.cursor() as cur:
        rows = cur.execute("SELECT DISTINCT user_id, substr(ts, 1, 10) AS day FROM events "
                           "WHERE user_id IS NOT NULL").fetchall()
    days_by_user: dict[int, set[dt.date]] = {}
    for r in rows:
        days_by_user.setdefault(r["user_id"], set()).add(dt.date.fromisoformat(r["day"]))
    weeks: dict[str, dict] = {}
    overall = {f"d{k}": [0, 0] for k in RETENTION_DAYS}            # [retained, eligible]
    overall_by = {f"d{k}": [0, 0] for k in RETENTION_DAYS}
    for days in days_by_user.values():
        first = min(days)
        week = (first - dt.timedelta(days=first.weekday())).isoformat()
        w = weeks.setdefault(week, {"cohort_week": week, "users": 0,
                                    **{f"d{k}": [0, 0] for k in RETENTION_DAYS}})
        w["users"] += 1
        for k in RETENTION_DAYS:
            target = first + dt.timedelta(days=k)
            if target > today:
                continue                                           # not measurable yet
            key = f"d{k}"
            hit = target in days
            later = any(d >= target for d in days)
            w[key][1] += 1
            w[key][0] += hit
            overall[key][1] += 1
            overall[key][0] += hit
            overall_by[key][1] += 1
            overall_by[key][0] += later

    def pct(pair):
        return None if pair[1] == 0 else round(100.0 * pair[0] / pair[1], 1)

    out_weeks = []
    for week in sorted(weeks):
        w = weeks[week]
        out_weeks.append({"cohort_week": week, "users": w["users"],
                          **{f"d{k}": pct(w[f"d{k}"]) for k in RETENTION_DAYS},
                          **{f"d{k}_eligible": w[f"d{k}"][1] for k in RETENTION_DAYS}})
    return {"as_of": today.isoformat(), "users": len(days_by_user),
            "retention": {k: pct(v) for k, v in overall.items()},
            "retained_by": {k: pct(v) for k, v in overall_by.items()},
            "eligible": {k: v[1] for k, v in overall.items()},
            "cohorts": out_weeks}


def funnel(days: int = 30, today: dt.date | None = None) -> dict:
    """People who did each event in the last `days` days, in loop order, plus raw counts."""
    today = today or clock.today()
    since = (today - dt.timedelta(days=days - 1)).isoformat()
    with db.cursor() as cur:
        rows = cur.execute("SELECT name, COUNT(*) AS n, COUNT(DISTINCT user_id) AS people FROM events "
                           "WHERE ts>=? GROUP BY name", (since,)).fetchall()
    found = {r["name"]: (r["n"], r["people"]) for r in rows}
    return {"window_days": days,
            "steps": [{"event": e, "events": found.get(e, (0, 0))[0], "people": found.get(e, (0, 0))[1]}
                      for e in EVENTS]}
