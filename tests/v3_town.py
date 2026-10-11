"""A local Ziggy for the v3 front end: production settings (no sample data), a frozen clock,
and the stand-in Swiggy neighbourhood (tests/swiggy_town.py). Swiggy's own sign-in page is
played by a Playwright route, so the browser goes through the real OAuth redirect and
callback. Nothing real is touched. Used by tests/browser_v3.py; run it by itself to click
around: `python tests/v3_town.py` (prints the URL)."""
import datetime as dt
import os
import sys
import tempfile
import urllib.parse
from threading import Thread

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("SMARTPLATE_FIXTURE_DATA", "1")      # tests/conftest.py does the same

from werkzeug.serving import make_server  # noqa: E402

TONIGHT = dt.datetime(2026, 11, 2, 18, 30)                 # Monday, 18:30 IST: dinner is next


def start(setattr_=setattr, at=TONIGHT):
    """Start the app on a free port. `setattr_` is monkeypatch.setattr under pytest."""
    from smartplate import clock, config, ratelimit
    from smartplate.integrations import swiggy_connect
    from swiggy_town import TownFake

    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)
    os.environ["SMARTPLATE_DB"] = path
    world = {"at": at, "db": path}
    for name, value in {"DB_PATH": path, "DATABASE_URL": "", "FIXTURE_DATA": False, "WEATHER_PROVIDER": "simulated",
                        "SWIGGY_PROVIDER": "live", "SWIGGY_REDIRECT_APPROVED": True, "PUSH_ENABLED": False,
                        "LIVE_ORDERS": False}.items():
        setattr_(config, name, value)
    setattr_(clock, "now", lambda: world["at"])
    setattr_(clock, "today", lambda: world["at"].date())
    ratelimit.reset()
    fake = TownFake()
    setattr_(swiggy_connect, "_http", fake)
    from smartplate.app import create_app
    app = create_app()
    srv = make_server("127.0.0.1", 0, app, threaded=True)
    thread = Thread(target=srv.serve_forever, daemon=True)
    thread.start()

    def stop():
        srv.shutdown()
        thread.join(timeout=5)
        srv.server_close()
        if os.path.exists(path):
            os.unlink(path)
    world.update(url=f"http://127.0.0.1:{srv.server_port}", fake=fake, app=app, stop=stop)
    return world


def swiggy_signin_page(page, fake):
    """Play Swiggy's sign-in page: approve, then redirect back to the app's callback."""
    def approve(route):
        q = fake.approve(route.request.url)
        back = f"{q['redirect_uri']}?{urllib.parse.urlencode({'state': q['state'], 'code': 'code-1'})}"
        route.fulfill(status=302, headers={"Location": back})
    page.route("https://mcp.swiggy.com/auth/authorize**", approve)


if __name__ == "__main__":
    w = start()
    print(w["url"] + "/v3", flush=True)
    try:
        Thread.join(Thread(target=lambda: None))
        import time
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        w["stop"]()
