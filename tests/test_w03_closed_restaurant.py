"""A closed restaurant is information, not an outage (usability #38, journey row 13).

Swiggy lists closed places (availabilityStatus "CLOSED") and refuses their menu. The
search answer carries that status so the app can grey the row, and opening the menu
answers 409 with code "restaurant_closed" (not a 502 "failed" that the app offers to
Retry). Production settings, the stand-in neighbourhood in tests/swiggy_town.py, a
frozen clock, no network.
"""
import os
import tempfile

import pytest

from smartplate import config, ratelimit
from smartplate.app import create_app
from smartplate.integrations import swiggy_connect
from swiggy_town import TownFake
from test_followups import _connect


@pytest.fixture
def town(frozen, monkeypatch):
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)
    monkeypatch.setattr(config, "DB_PATH", path)
    monkeypatch.setattr(config, "DATABASE_URL", "")
    monkeypatch.setattr(config, "FIXTURE_DATA", False)
    monkeypatch.setattr(config, "WEATHER_PROVIDER", "simulated")
    monkeypatch.setattr(config, "SWIGGY_PROVIDER", "live")
    monkeypatch.setattr(config, "SWIGGY_REDIRECT_APPROVED", True)
    monkeypatch.setattr(config, "PUSH_ENABLED", False)
    ratelimit.reset()
    fake = TownFake()
    monkeypatch.setattr(swiggy_connect, "_http", fake)
    client = create_app().test_client()
    uid = client.post("/api/profiles", json={
        "name": "Priya", "diet": "veg", "weekly_budget": 3500, "cook": "never",
        "rhythm": {"breakfast": "order", "lunch": "order", "dinner": "order"}}).get_json()["user"]["id"]
    _connect(client, fake, uid=uid)
    assert client.post(f"/api/user/{uid}/swiggy/address", json={"address_id": "addr-home"}).status_code == 200
    yield client, uid
    if os.path.exists(path):
        os.unlink(path)


def test_search_marks_closed_places_and_their_menu_is_a_409_not_an_outage(town):
    client, uid = town
    found = client.get(f"/api/user/{uid}/swiggy/restaurants?browse=1").get_json()["restaurants"]
    by_name = {r["name"]: r for r in found}
    assert by_name["Night Owl Parotta"]["availability"] == "CLOSED"
    assert by_name["Annapoorna Tiffin Room"]["availability"] == "OPEN"

    r = client.get(f"/api/user/{uid}/swiggy/live-menu?restaurant_id=r-106&restaurant_name=Night%20Owl%20Parotta")
    assert r.status_code == 409
    body = r.get_json()
    assert body["code"] == "restaurant_closed"
    assert "closed right now" in body["error"]

    # an open place still opens
    ok = client.get(f"/api/user/{uid}/swiggy/live-menu?restaurant_id=r-101&restaurant_name=Annapoorna%20Tiffin%20Room")
    assert ok.status_code == 200 and ok.get_json()["items"]
