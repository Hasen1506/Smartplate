"""Shared pytest fixtures — each test module gets a fresh, seeded temp database."""
import ipaddress
import os
import socket
import tempfile

import pytest

# The sample Chennai world (seeded profiles, catalogue, weather) is test fixture data only:
# it needs this flag, and smartplate.config ignores it in production. Set before config loads.
os.environ["SMARTPLATE_FIXTURE_DATA"] = "1"
for _var in ("RENDER", "SMARTPLATE_ENV"):
    os.environ.pop(_var, None)

# Epicure: the suite always uses the small offline fixture (real file formats, a subset of
# the vectors), checked against the fixture's own SHA-256 sums. Set before smartplate.config loads.
_EPICURE_FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "epicure")
os.environ["SMARTPLATE_EPICURE_DIR"] = _EPICURE_FIXTURE
os.environ["SMARTPLATE_EPICURE_CHECKSUMS"] = os.path.join(_EPICURE_FIXTURE, "SHA256SUMS")

# Postgres run (CI's second job): SMARTPLATE_TEST_DATABASE_URL points at a throwaway local
# database and every test gets its own schema (tests/pg_support.py). Unset: SQLite files.
import pg_support  # noqa: E402

if pg_support.enabled():
    os.environ["DATABASE_URL"] = pg_support.URL
    os.environ["SMARTPLATE_PG_SCHEMA"] = pg_support.worker_schema()
    os.environ["SMARTPLATE_PG_POOL_MAX"] = "4"
else:
    os.environ.pop("DATABASE_URL", None)          # never touch a real database from the suite

try:                                    # deterministic property runs everywhere (see tests/test_gt_properties.py)
    from hypothesis import HealthCheck, settings

    settings.register_profile(
        "ci", derandomize=True, database=None, deadline=None, print_blob=True, max_examples=25,
        suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow,
                               HealthCheck.data_too_large])
    settings.register_profile(
        "deep", derandomize=False, database=None, deadline=None, print_blob=True, max_examples=400,
        suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow,
                               HealthCheck.data_too_large])
    settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "ci"))
except ImportError:                     # production installs never need hypothesis
    pass


def _loopback(host) -> bool:
    if host in (None, "", "localhost"):
        return True
    try:
        return ipaddress.ip_address(str(host).split("%")[0]).is_loopback
    except ValueError:
        return False


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """The suite never leaves the machine: any non-loopback connection is refused.
    Live weather, Swiggy and Web Push all have seams the tests replace; reaching the
    real thing is a test bug, so it fails fast instead of depending on the network."""
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex
    real_getaddrinfo = socket.getaddrinfo

    def _check(address):
        host = address[0] if isinstance(address, tuple) else None
        if isinstance(address, (str, bytes)):          # AF_UNIX
            return
        if not _loopback(host):
            raise OSError(f"network access blocked in tests: {address!r}")

    def connect(self, address):
        _check(address)
        return real_connect(self, address)

    def connect_ex(self, address):
        _check(address)
        return real_connect_ex(self, address)

    def getaddrinfo(host, *args, **kwargs):
        if not _loopback(host):
            raise socket.gaierror(f"network access blocked in tests: {host!r}")
        return real_getaddrinfo(host, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)
    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)


def pytest_configure(config):
    config.addinivalue_line("markers", "sqlite_only: exercises the SQLite file itself; on the Postgres "
                                       "run it still runs, against SQLite")
    if pg_support.enabled():
        pg_support.setup_worker()


@pytest.fixture(autouse=True)
def engine_for_test(request, monkeypatch):
    """On the Postgres run, a test about SQLite files runs against SQLite."""
    from smartplate import config
    if pg_support.enabled() and request.node.get_closest_marker("sqlite_only"):
        monkeypatch.setattr(config, "DATABASE_URL", "")
    yield


_SEEDED = {}      # per worker process: a seeded database image per (fixture, day, solver settings)


def _fresh_seeded_db(path: str, kind: str) -> dict:
    """Give `path` a freshly seeded database. Seeding (schema, catalogue, sample profiles
    and the starter plan's solve) is identical for every test of one kind on one day, so
    it runs once per worker and later tests get a byte copy of that database: same data,
    a new file, no state shared between tests. Cuts ~0.1 s of setup from every test."""
    from smartplate import clock, config, db, seed
    key = (kind, clock.today().isoformat(), config.SOLVER_GAP, config.SOLVER_MAX_NODES,
           config.WEATHER_PROVIDER, config.SWIGGY_PROVIDER, db.engine())
    if config.DATABASE_URL:
        return _fresh_seeded_schema(key)
    if key not in _SEEDED:
        db.init_db()
        info = seed.seed_all()
        with open(path, "rb") as f:
            _SEEDED[key] = (f.read(), info)
        return dict(info)
    image, info = _SEEDED[key]
    with open(path, "wb") as f:
        f.write(image)
    return dict(info)


def _fresh_seeded_schema(key) -> dict:
    """Postgres twin of the file copy: clone a seeded template schema into a new one."""
    import pytest as _pytest
    from smartplate import config, db, seed
    mp = _pytest.MonkeyPatch()        # seeds the template under its own schema, then undone
    template = pg_support.template_schema(key)
    if key not in _SEEDED:
        pg_support.reset(template)
        mp.setattr(config, "PG_SCHEMA", template)
        db.init_db()
        _SEEDED[key] = (None, seed.seed_all())
        mp.undo()
    schema = pg_support.fresh_schema()
    pg_support.clone(template, schema)
    _CURRENT_SCHEMA.append(schema)
    config.PG_SCHEMA = schema
    return dict(_SEEDED[key][1])


_CURRENT_SCHEMA = []


@pytest.fixture(autouse=True)
def _pg_schema_cleanup(monkeypatch):
    """Restores the worker schema after a test that got its own and drops that schema."""
    from smartplate import config
    if not pg_support.enabled():
        yield
        return
    monkeypatch.setattr(config, "PG_SCHEMA", config.PG_SCHEMA)     # restored on teardown
    yield
    while _CURRENT_SCHEMA:
        pg_support.drop(_CURRENT_SCHEMA.pop())


@pytest.fixture()
def seeded(monkeypatch):
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.environ["SMARTPLATE_DB"] = path
    # config reads the env var at import time; patch the already-imported value too.
    from smartplate import config, db, ratelimit, seed
    ratelimit.reset()
    monkeypatch.setattr(config, "DB_PATH", path)
    # deterministic plans: never let a live forecast change what the solver picks
    monkeypatch.setattr(config, "WEATHER_PROVIDER", "simulated")
    info = _fresh_seeded_db(path, "seeded")
    yield info
    os.unlink(path)


@pytest.fixture()
def frozen(monkeypatch):
    """Pin the app clock to Monday 2 Nov 2026 08:00 IST (settable/advanceable)."""
    from gt_support import MONDAY_8AM, FrozenClock
    return FrozenClock(monkeypatch, MONDAY_8AM)


@pytest.fixture()
def gt(monkeypatch, frozen):
    """A ground-truth world: frozen clock first, then a fresh seeded database, the
    simulated order provider and live ordering off. Yields the clock."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.environ["SMARTPLATE_DB"] = path
    from smartplate import config, db, ratelimit, seed
    ratelimit.reset()
    monkeypatch.setattr(config, "DB_PATH", path)
    monkeypatch.setattr(config, "WEATHER_PROVIDER", "simulated")
    monkeypatch.setattr(config, "SWIGGY_PROVIDER", "simulated")
    monkeypatch.setattr(config, "LIVE_ORDERS", False)
    monkeypatch.setattr(config, "PUSH_ENABLED", False)
    # Prove optimality instead of stopping within 0.1%: with the planner's tie-break the
    # optimum is unique, so every machine (x86 or ARM CBC build) returns the same week.
    monkeypatch.setattr(config, "SOLVER_GAP", 0.0)
    # Only the deterministic node cap may stop a solve in tests, never the wall clock.
    monkeypatch.setattr(config, "SOLVER_TIME_LIMIT_S", 300.0)
    monkeypatch.setattr(config, "SOLVER_MAX_NODES", 3000)
    _fresh_seeded_db(path, "gt")
    yield frozen
    os.unlink(path)


@pytest.fixture()
def swiggy_replay(monkeypatch):
    """Swiggy Food MCP answered from tests/fixtures/swiggy_food_recording.json."""
    from gt_support import make_replay
    from smartplate.integrations import swiggy_connect
    fake = make_replay()()
    monkeypatch.setattr(swiggy_connect, "_http", fake)
    yield fake
    if fake.record:
        fake.save()


def solo(user_id: int) -> None:
    """Take a seeded profile out of its household, so it orders one portion for itself.
    Arjun (3) shares 'Flat 3B' with Meera, who is vegan with a dairy allergy: with her
    eating, every meal is two portions and Ziggy won't fill a Swiggy cart (menus don't
    list ingredients). Cart and bill tests that are about one person use this."""
    from smartplate import db
    with db.cursor() as cur:
        cur.execute("UPDATE users SET household_id=NULL WHERE id=?", (user_id,))
