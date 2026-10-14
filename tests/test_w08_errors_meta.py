"""Grill #65 (safe error text + request id) and #32 (no public feature catalogue)."""
import pytest

from smartplate import errors
from smartplate.app import create_app


@pytest.fixture()
def client(seeded):
    app = create_app()
    app.config.update(TESTING=True)
    return app.test_client()


def test_own_value_error_text_is_shown(client):
    # accounts._norm raises ValueError("Use 3–64 letters…") — written for people.
    r = client.post("/api/signin", json={"login": "x", "password": "y"})
    assert r.status_code == 400
    assert "match" in r.get_json()["error"] or "letters" in r.get_json()["error"]


def test_library_value_error_is_not_echoed(client):
    # int("not-a-number") inside a route: Python's own wording must not reach the client.
    r = client.post("/api/plan", json={"user_id": "not-a-number"})
    body = r.get_json()
    assert r.status_code == 400
    assert "invalid literal" not in body["error"] and "int()" not in body["error"]
    assert body["error"] == errors.GENERIC and body["code"] == "invalid_input"
    assert body["request_id"] == r.headers["X-Request-ID"]


def test_request_id_header_on_every_reply_and_incoming_id_kept(client):
    assert len(client.get("/healthz").headers["X-Request-ID"]) >= 8
    r = client.get("/healthz", headers={"X-Request-ID": "render-abc12345"})
    assert r.headers["X-Request-ID"] == "render-abc12345"
    r = client.get("/healthz", headers={"X-Request-ID": "bad id <script>"})
    assert r.headers["X-Request-ID"] != "bad id <script>"


def test_public_message_rules():
    def ours():
        raise ValueError("Pick a budget above ₹0")

    def parser():
        import json
        json.loads("{nope")

    def builtin():
        return int("abc")

    for fn, shown in ((ours, True), (parser, False), (builtin, False)):
        with pytest.raises(ValueError) as info:
            fn()
        # test files are outside the package: only UserError or package code counts
        assert errors.public_message(info.value) is None
    assert errors.public_message(errors.UserError("Say which restaurant")) == "Say which restaurant"


def test_package_raise_is_public():
    from smartplate import accounts
    with pytest.raises(ValueError) as info:
        accounts._password("short")
    assert "password" in errors.public_message(info.value)


def test_meta_has_no_feature_catalogue(client):
    m = client.get("/api/meta").get_json()
    assert "features" not in m
    text = str(m)
    for leak in ("§", "5.1", "Carbon / sustainability", "Health / longevity", "domain/"):
        assert leak not in text
