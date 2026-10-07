"""A non-text meal description was a 500 (found by the API fuzzer on the rebased Roadmap E
branch: POST /api/user/1/intake {"text": null} crashed writing a NULL note; a number or list
crashed the parser). Both intake endpoints now answer 400 with a plain message."""
import pytest

from smartplate.app import create_app


@pytest.fixture
def client(seeded):
    app = create_app()
    app.config["TESTING"] = True
    return app.test_client()


@pytest.mark.parametrize("text", [None, 12, ["dosa"], {"a": 1}, True])
@pytest.mark.parametrize("path", ["/api/user/1/intake", "/api/intake"])
def test_non_text_meal_is_a_400_not_a_crash(client, path, text):
    r = client.post(path, json={"text": text})
    assert r.status_code == 400, (r.status_code, r.get_data(as_text=True)[:200])
    assert "what you ate" in r.get_json()["error"]


def test_a_real_meal_still_logs(client):
    r = client.post("/api/user/1/intake", json={"text": "2 idli and sambar"})
    assert r.status_code == 200 and r.get_json()["entry_id"]
    assert client.post("/api/intake", json={}).status_code == 200      # empty estimate stays allowed
