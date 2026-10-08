"""First request after idle (Oct 8, 2026): ~10 s while a pool the Neon pooler had broken
was replaced. The default wait is now 5 s and a background keep-alive finds a dead pool
off the request path."""
import threading

from psycopg_pool import PoolTimeout

from smartplate import config, db_pg


def test_default_pool_timeout_is_five_seconds(monkeypatch):
    import importlib
    monkeypatch.delenv("SMARTPLATE_PG_POOL_TIMEOUT", raising=False)
    importlib.reload(config)
    try:
        assert config.PG_POOL_TIMEOUT == 5.0
    finally:
        importlib.reload(config)


class _DeadPool:
    def getconn(self, timeout=None):
        raise PoolTimeout("no connection")


def test_keepalive_replaces_a_dead_pool_off_the_request_path(monkeypatch):
    dead, replaced, stop = _DeadPool(), [], threading.Event()
    monkeypatch.setattr(db_pg, "pool", lambda: dead)
    monkeypatch.setattr(db_pg, "replace_pool", lambda p: (replaced.append(p), stop.set()))
    monkeypatch.setattr(db_pg, "KEEPALIVE_S", 0.01)
    t = threading.Thread(target=db_pg._keep_alive_loop, args=(stop,), daemon=True)
    t.start()
    t.join(2)
    assert replaced == [dead]


def test_keepalive_survives_an_unexpected_error(monkeypatch):
    calls, stop = [], threading.Event()

    class Boom:
        def getconn(self, timeout=None):
            calls.append(1)
            if len(calls) >= 3:
                stop.set()
            raise RuntimeError("network blip")

    monkeypatch.setattr(db_pg, "pool", lambda: Boom())
    monkeypatch.setattr(db_pg, "KEEPALIVE_S", 0.01)
    t = threading.Thread(target=db_pg._keep_alive_loop, args=(stop,), daemon=True)
    t.start()
    t.join(2)
    assert len(calls) >= 3


def test_static_assets_are_fingerprinted_gzipped_and_cached_long(seeded):
    import re
    from smartplate.app import create_app
    client = create_app().test_client()
    page = client.get("/")
    assert page.headers["Cache-Control"] == "no-cache"
    html = page.data.decode()
    js = re.search(r'src="(/static/app\.js\?v=[0-9a-f]{12})"', html).group(1)
    css = re.search(r'href="(/static/styles\.css\?v=[0-9a-f]{12})"', html).group(1)
    for url in (js, css):
        r = client.get(url, headers={"Accept-Encoding": "gzip, br"})
        assert r.status_code == 200 and r.headers["Content-Encoding"] == "gzip"
        assert "immutable" in r.headers["Cache-Control"] and "max-age=31536000" in r.headers["Cache-Control"]
    plain = client.get("/static/app.js")                     # an old or unversioned URL revalidates
    assert plain.status_code == 200 and plain.headers["Cache-Control"] == "no-cache"
    assert client.get("/static/app.js?v=stale").headers["Cache-Control"] == "no-cache"
