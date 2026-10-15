"""Single-process trial runtime: serialize edits against checkout and seed once."""
from threading import Lock, RLock

from . import db

state_lock = RLock()          # startup/seed and the simulated order saga only
_user_locks: dict[int, RLock] = {}
_registry = Lock()


def user_lock(user_id: int) -> RLock:
    """One lock per profile: a slow Swiggy call or solve for one person never
    blocks anybody else's requests (M-05). Requests naming several profiles take
    their locks in id order, so two of them can't deadlock."""
    with _registry:
        return _user_locks.setdefault(int(user_id), RLock())


def initialize():
    with state_lock:
        from .integrations import swiggy_connect
        fingerprint = db.schema_fingerprint(swiggy_connect.SCHEMA, getattr(swiggy_connect, "COLUMNS", ()))
        if not db.schema_is_current(fingerprint):   # a warm boot skips ~70 round trips (W9, grill #66)
            db.init_db()
            swiggy_connect.init_schema()
            db.mark_schema(fingerprint)
        from . import cleanup, config
        from .domain import festivals
        if config.FIXTURE_DATA:                 # the test-suite's sample world, never production
            with db.cursor() as cur:
                empty = cur.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
            if empty:
                from .seed import seed_all
                seed_all()
        else:
            removed = cleanup.remove_seeded_samples()
            cleanup.replan(removed["replanned_plans"])
        from . import privacy, ratelimit, vault, websession
        ratelimit.init_schema()
        websession.init_schema()
        privacy.init_schema()
        websession.purge_idle()                 # sessions unused for 60 days
        privacy.purge_expired()                 # consent records of erased profiles after one year
        if config.SECRET:                       # after a key rotation: re-seal tokens under the new key
            vault.reseal_all()
        festivals.ensure_calendar()             # the real India holiday calendar
        cleanup.remove_invented_community()
        if config.DATABASE_URL:
            from . import db_pg
            import logging
            logging.getLogger("smartplate.db").info("database warm in %.2f s", db_pg.warm())
            if config.PG_KEEPALIVE:
                db_pg.start_keepalive()
