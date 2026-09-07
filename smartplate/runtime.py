"""Single-process trial runtime: serialize edits against checkout and seed once."""
from threading import RLock

from . import db

state_lock = RLock()


def initialize():
    with state_lock:
        db.init_db()
        with db.cursor() as cur:
            empty = cur.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
        if empty:
            from .seed import seed_all
            seed_all()
