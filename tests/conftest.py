"""Shared pytest fixtures — each test module gets a fresh, seeded temp database."""
import os
import tempfile

import pytest


@pytest.fixture()
def seeded(monkeypatch):
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.environ["SMARTPLATE_DB"] = path
    # config reads the env var at import time; patch the already-imported value too.
    from smartplate import config, db, seed
    monkeypatch.setattr(config, "DB_PATH", path)
    # deterministic plans: never let a live forecast change what the solver picks
    monkeypatch.setattr(config, "WEATHER_PROVIDER", "simulated")
    db.init_db()
    info = seed.seed_all()
    yield info
    os.unlink(path)
