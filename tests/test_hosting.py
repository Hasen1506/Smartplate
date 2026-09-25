"""Hosting: the health check, and the start command every host file agrees on."""
import importlib
import os

import pytest

from smartplate.app import create_app

ROOT = os.path.dirname(os.path.dirname(__file__))


@pytest.fixture
def client(seeded):
    app = create_app()
    app.config["TESTING"] = True
    return app.test_client()


def test_health_check(client):
    r = client.get("/healthz")
    assert r.status_code == 200 and r.get_json() == {"ok": True}


def test_single_worker_start_command_everywhere():
    procfile = open(os.path.join(ROOT, "Procfile")).read()
    blueprint = open(os.path.join(ROOT, "render.yaml")).read()
    for text in (procfile, blueprint):
        assert "gunicorn wsgi:app --workers 1" in text
    assert "healthCheckPath: /healthz" in blueprint
    assert "gunicorn" in open(os.path.join(ROOT, "requirements.txt")).read()


def test_render_url_becomes_public_url(monkeypatch):
    from smartplate import config
    monkeypatch.delenv("SMARTPLATE_PUBLIC_URL", raising=False)
    monkeypatch.setenv("RENDER_EXTERNAL_URL", "https://smartplate-x.onrender.com/")
    try:
        assert importlib.reload(config).PUBLIC_URL == "https://smartplate-x.onrender.com"
    finally:
        monkeypatch.delenv("RENDER_EXTERNAL_URL")
        importlib.reload(config)
