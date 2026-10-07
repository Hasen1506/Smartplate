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
        db.init_db()
        from .integrations import swiggy_connect
        swiggy_connect.init_schema()
        with db.cursor() as cur:
            empty = cur.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
        if empty:
            from .seed import seed_all
            seed_all()
