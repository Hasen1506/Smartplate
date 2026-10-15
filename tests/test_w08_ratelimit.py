"""Grill #63: rate limits live in the database (token buckets), so they survive a restart
and are shared by every worker, on SQLite and Postgres alike."""
import importlib
import threading

import pytest

from smartplate import db, ratelimit


@pytest.fixture()
def clock(seeded, monkeypatch):
    now = {"t": 1_800_000_000.0}
    monkeypatch.setattr(ratelimit, "_now", lambda: now["t"])
    ratelimit.reset()
    return now


def test_bucket_spends_then_refuses_with_wait(clock):
    for _ in range(5):
        ratelimit.check("k", 5, 900)
    with pytest.raises(ratelimit.TooMany) as info:
        ratelimit.check("k", 5, 900)
    assert info.value.wait_s == 181                     # one token every 180 s
    clock["t"] += 180
    ratelimit.check("k", 5, 900)                        # exactly one token back
    with pytest.raises(ratelimit.TooMany):
        ratelimit.check("k", 5, 900)


def test_refill_is_capped_at_the_limit(clock):
    ratelimit.check("cap", 3, 60)
    clock["t"] += 10_000
    for _ in range(3):
        ratelimit.check("cap", 3, 60)
    with pytest.raises(ratelimit.TooMany):
        ratelimit.check("cap", 3, 60)


def test_check_without_record_and_hit(clock):
    for _ in range(5):
        ratelimit.check("signin:x", 5, 900, record=False)     # looking costs nothing
        ratelimit.hit("signin:x", 5, 900)                     # each failure costs one
    with pytest.raises(ratelimit.TooMany):
        ratelimit.check("signin:x", 5, 900, record=False)
    ratelimit.hit("signin:x", 5, 900)                         # never below zero
    with db.cursor() as cur:
        assert cur.execute("SELECT MIN(tokens) FROM rate_buckets").fetchone()[0] >= 0


def test_limits_survive_a_restart(clock):
    for _ in range(2):
        ratelimit.check("profiles:203.0.113.1", 2, 3600)
    # A redeploy: the module (and any in-memory state) is loaded afresh.
    namespace = ratelimit._namespace
    importlib.reload(ratelimit)
    ratelimit._namespace = namespace                          # same keys as before the restart
    ratelimit._now = lambda: clock["t"]
    with pytest.raises(ratelimit.TooMany):
        ratelimit.check("profiles:203.0.113.1", 2, 3600)


def test_racing_requests_never_overspend(clock):
    ratelimit.check("race", 10, 3600, record=False)          # create the bucket
    wins, errors = [], []

    def go():
        try:
            ratelimit.check("race", 10, 3600)
            wins.append(1)
        except ratelimit.TooMany:
            pass
        except Exception as exc:                              # noqa: BLE001
            errors.append(exc)
    threads = [threading.Thread(target=go) for _ in range(25)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not [e for e in errors if "locked" not in str(e)]
    assert len(wins) <= 10


def test_stale_buckets_are_cleaned(clock):
    ratelimit.check("old", 5, 60)
    clock["t"] += ratelimit.STALE_S + 1
    ratelimit.check("new", 5, 60)
    with db.cursor() as cur:
        keys = [r[0] for r in cur.execute("SELECT key FROM rate_buckets").fetchall()]
    assert not any(k.endswith("old") for k in keys)
