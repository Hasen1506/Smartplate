"""A small in-memory limiter for the few endpoints worth guessing at (sign-in, new
profiles). One process only, like the rest of the trial runtime."""
import time
from collections import defaultdict, deque
from threading import Lock


class TooMany(Exception):
    def __init__(self, wait_s: int):
        super().__init__(f"Too many tries. Wait {max(1, wait_s // 60)} minute(s) and try again.")
        self.wait_s = wait_s


_hits: dict[str, deque] = defaultdict(deque)
_lock = Lock()


def check(key: str, limit: int, window_s: int, *, record: bool = True) -> None:
    """Raise TooMany if `key` already has `limit` hits in the window; else count one."""
    now = time.monotonic()
    with _lock:
        q = _hits[key]
        while q and now - q[0] > window_s:
            q.popleft()
        if len(q) >= limit:
            raise TooMany(int(window_s - (now - q[0])) + 1)
        if record:
            q.append(now)


def hit(key: str) -> None:
    with _lock:
        _hits[key].append(time.monotonic())


def reset() -> None:
    with _lock:
        _hits.clear()
