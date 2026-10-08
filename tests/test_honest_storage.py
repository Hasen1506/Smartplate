"""Honesty about where user data lives (8 Oct 2026 audit, S1/S2/S7).

The free Render Blueprint keeps SQLite on the instance's temporary disk, which is
erased on every spin-down, restart or redeploy. The health checks and /api/meta must
say so, so the UI can warn users, and must say "persistent" only on the mounted disk.
"""
import pytest

from smartplate import config
from smartplate.app import create_app


@pytest.fixture
def client(seeded):
    app = create_app()
    app.config["TESTING"] = True
    return app.test_client()


@pytest.fixture
def on_render_free(monkeypatch):
    monkeypatch.setenv("RENDER", "true")
    monkeypatch.setattr(config, "DB_PERSISTENT", "")
    monkeypatch.setattr(config, "RENDER_DISK_MOUNT", "/var/data")


def test_render_default_path_is_reported_ephemeral(on_render_free, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", "smartplate.db")           # render.yaml sets no SMARTPLATE_DB
    status = config.storage_status()
    assert status["persistent"] is False
    assert "temporary disk" in status["reason"]


def test_render_disk_path_is_reported_persistent(on_render_free, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", "/var/data/smartplate.db")  # render.production.yaml
    assert config.storage_status()["persistent"] is True


def test_lookalike_path_outside_the_mount_is_not_persistent(on_render_free, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", "/var/database/smartplate.db")
    assert config.storage_status()["persistent"] is False


def test_unknown_host_says_unknown_and_explicit_declaration_wins(monkeypatch):
    monkeypatch.delenv("RENDER", raising=False)
    monkeypatch.setattr(config, "DB_PATH", "/tmp/x.db")
    monkeypatch.setattr(config, "DB_PERSISTENT", "")
    assert config.storage_status()["persistent"] is None
    monkeypatch.setattr(config, "DB_PERSISTENT", "0")
    assert config.storage_status()["persistent"] is False
    monkeypatch.setattr(config, "DB_PERSISTENT", "1")
    assert config.storage_status()["persistent"] is True


def test_health_endpoints_and_meta_report_ephemeral_storage(client, on_render_free, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", "smartplate.db")
    for path in ("/healthz", "/api/meta"):
        body = client.get(path).get_json()
        key = "database" if path == "/healthz" else "storage"
        assert body[key]["persistent"] is False, path
    assert client.get("/healthz").status_code == 200   # liveness must not fail Render's health check


def test_readyz_reports_storage(client, on_render_free, monkeypatch):
    r = client.get("/readyz")
    assert "database" in r.get_json()


def test_meta_says_whether_swiggy_sign_in_is_approved(client, monkeypatch):
    monkeypatch.setattr(config, "SWIGGY_REDIRECT_APPROVED", False)
    assert client.get("/api/meta").get_json()["swiggy_redirect_approved"] is False
    monkeypatch.setattr(config, "SWIGGY_REDIRECT_APPROVED", True)
    assert client.get("/api/meta").get_json()["swiggy_redirect_approved"] is True


def test_free_blueprint_is_not_marked_approved_or_persistent():
    # The free Blueprint must not claim Swiggy approval or a persistent database.
    import os
    text = open(os.path.join(os.path.dirname(os.path.dirname(__file__)), "render.yaml")).read()
    assert "SMARTPLATE_SWIGGY_REDIRECT_APPROVED" not in text
    assert "SMARTPLATE_DB_PERSISTENT" not in text and "SMARTPLATE_DB" not in text.replace("SMARTPLATE_DB_", "")


def test_sample_plan_note_does_not_promise_swiggy_before_approval(seeded, monkeypatch):
    from smartplate.domain import live_catalog
    monkeypatch.setattr(config, "SWIGGY_REDIRECT_APPROVED", False)
    note = live_catalog.source_for(1, connected=False)["note"]
    assert "real restaurants" not in note and "waiting for Swiggy's approval" in note
