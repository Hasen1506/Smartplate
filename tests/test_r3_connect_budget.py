"""Fix round 3 "connect-budget": Swiggy's own allow-list answer before sending anyone to a
dead sign-in page, and one budget model (weekly is the number; a day is a seventh; a mid-week
plan names the days it covers; Today follows a weekly change; "How many eat?")."""
import json

import pytest

from smartplate.app import create_app
from smartplate.integrations import swiggy_connect
from test_followups import FakeSwiggy, _allow_app_host, _secure_profile, APP


class AllowList(FakeSwiggy):
    """FakeSwiggy plus Swiggy's public "is this redirect whitelisted?" check."""

    def __init__(self, answer):
        super().__init__()
        self.answer, self.checked = answer, []

    def __call__(self, method, url, headers, body):
        if "/auth/check-redirect-uri" in url:
            self.checked.append(url)
            if self.answer is None:
                raise swiggy_connect.SwiggyError("Couldn't reach Swiggy")
            return 200, {}, json.dumps({"whitelisted": self.answer, "signupEnabled": True}).encode()
        return super().__call__(method, url, headers, body)


@pytest.fixture
def client(seeded):
    app = create_app()
    app.config["TESTING"] = True
    return app.test_client()


def _start(client, monkeypatch, answer):
    monkeypatch.setattr(swiggy_connect, "_WHITELIST_SEEN", {})
    fake = AllowList(answer)
    monkeypatch.setattr(swiggy_connect, "_http", fake)
    _allow_app_host()
    _secure_profile(client, 1)
    return fake, client.post("/api/user/1/swiggy/connect", json={"consent": True}, base_url=APP)


def test_not_whitelisted_redirect_is_said_in_ziggy_not_on_a_dead_swiggy_page(client, monkeypatch):
    fake, r = _start(client, monkeypatch, False)
    assert r.status_code == 503
    body = r.get_json()
    assert body["code"] == "swiggy_redirect_not_whitelisted"
    assert "hasn't approved" in body["error"] and body["rule"]["id"] == "approval"
    assert "authorize_url" not in body
    assert "redirect_uri=https%3A%2F%2Fapp.example%2Fswiggy%2Fcallback" in fake.checked[0]
    assert fake.registrations == 0                       # nothing registered for a refused address


def test_whitelisted_redirect_goes_to_swiggy_and_the_answer_is_cached(client, monkeypatch):
    fake, r = _start(client, monkeypatch, True)
    assert r.status_code == 200 and r.get_json()["authorize_url"].startswith("https://mcp.swiggy.com/auth/authorize?")
    client.post("/api/user/1/swiggy/connect", json={"consent": True}, base_url=APP)
    assert len(fake.checked) == 1                         # one check per 10 minutes, not per tap


def test_unknown_allow_list_answer_fails_open(client, monkeypatch):
    _, r = _start(client, monkeypatch, None)
    assert r.status_code == 200 and "authorize_url" in r.get_json()


def test_localhost_is_never_checked():
    assert swiggy_connect.redirect_whitelisted("http://localhost:8000/swiggy/callback") is True


def _profile(client, weekly=21000, **extra):
    r = client.post("/api/profiles", json={"name": "Me", "diet": "veg", "weekly_budget": weekly, "cook": "sometimes",
                                           "rhythm": {"breakfast": "skip", "lunch": "order", "dinner": "order"},
                                           "consent": {"account": True}, **extra})
    assert r.status_code == 201, r.get_json()
    view = r.get_json()
    client.environ_base["HTTP_X_SMARTPLATE_KEY"] = view["access_key"]
    return view


def test_plan_budget_names_its_period_and_keeps_the_weekly_number(client):
    view = _profile(client)
    b = view["budget"]
    assert b["weekly_budget"] == 21000 and b["daily"] == 3000
    assert 1 <= b["days_covered"] <= 7
    expected = round(21000 * b["days_covered"] / 7, 2)
    assert b["budget"] == expected
    assert b["covers"] == ("Mon–Sun" if b["days_covered"] == 7 else
                           "Sun" if b["days_covered"] == 1 else f"{['Mon','Tue','Wed','Thu','Fri','Sat','Sun'][7 - b['days_covered']]}–Sun")


def test_week_change_to_300_is_stored_as_300_and_today_follows_it(client):
    view = _profile(client)
    uid = view["user"]["id"]
    assert client.post(f"/api/user/{uid}/today/budget", json={"amount": 3000}).status_code == 200
    r = client.patch(f"/api/user/{uid}/setup", json={"weekly_budget": 300})
    b = r.get_json()["budget"]
    assert b["weekly_budget"] == 300                     # never the pro-rated or min-cost figure
    assert b["budget"] == round(300 * b["days_covered"] / 7, 2)
    t = client.get(f"/api/user/{uid}/today").get_json()["budget"]
    assert t["amount"] == round(300 / 7) and t["source"] == "week"   # not the old ₹3,000 for today
    # Undo = the old weekly number back, exactly
    b = client.patch(f"/api/user/{uid}/setup", json={"weekly_budget": 21000}).get_json()["budget"]
    assert b["weekly_budget"] == 21000
    assert client.get(f"/api/user/{uid}/today").get_json()["budget"]["amount"] == 3000


def test_today_presets_are_relative_to_the_persons_day(client):
    view = _profile(client, weekly=4200)
    t = client.get(f"/api/user/{view['user']['id']}/today").get_json()["budget"]
    assert t["presets"] == [450, 600, 750]


def test_how_many_eat_is_kept_and_validated(client):
    view = _profile(client, eaters=3)
    assert view["user"]["prefs"]["eaters"] == 3
    uid = view["user"]["id"]
    r = client.patch(f"/api/user/{uid}/setup", json={"eaters": 0})
    assert r.status_code == 400
    r = client.patch(f"/api/user/{uid}/setup", json={"eaters": 2})
    assert r.get_json()["user"]["prefs"]["eaters"] == 2
    from smartplate.domain import profile
    assert profile.eaters({"prefs": {}}) == 1


def test_live_weather_names_place_and_source():
    from smartplate import today
    from unittest import mock
    with mock.patch("smartplate.domain.weather.week", return_value=[{"condition": "rain", "temp_c": 30, "rain_prob": 90,
                                                                     "source": "live"}] * 7), \
            mock.patch("smartplate.service.current_plan", return_value={"plan": {"week_start": "2026-10-12"}}), \
            mock.patch("smartplate.clock.today", return_value=__import__("datetime").date(2026, 10, 16)):
        w = today._weather({"id": 1, "city": "Chennai"})
    assert w["place"] == "Chennai" and w["attribution"] == "Open-Meteo"
