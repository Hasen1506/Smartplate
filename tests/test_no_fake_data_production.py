"""No fake data in production (Oct 2026 rule).

Production (config.FIXTURE_DATA False) seeds nothing, shows no sample profiles, sample
restaurants, sample weather or surge, and plans only from live Swiggy menus for the
chosen address plus clearly labelled home-cooked meals. A start-up migration removes the
seeded sample world from an existing database and never touches a real account.
"""
import json
import os
import tempfile

import pytest

from smartplate import cleanup, config, db
from smartplate.app import create_app


@pytest.fixture()
def prod(monkeypatch):
    """An empty database with fixture data switched off, as on Render."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)
    monkeypatch.setattr(config, "DB_PATH", path)
    monkeypatch.setattr(config, "DATABASE_URL", "")
    monkeypatch.setattr(config, "FIXTURE_DATA", False)
    monkeypatch.setattr(config, "WEATHER_PROVIDER", "simulated")   # no network; and no sample feed either
    from smartplate import ratelimit
    ratelimit.reset()
    yield path
    if os.path.exists(path):
        os.unlink(path)


SETUP = {"name": "Priya", "diet": "veg", "weekly_budget": 1500, "cook": "sometimes",
         "rhythm": {"breakfast": "skip", "lunch": "order", "dinner": "order"}}


def _new_profile(client):
    r = client.post("/api/profiles", json=dict(SETUP))
    assert r.status_code == 201, r.get_json()
    body = r.get_json()
    client.environ_base["HTTP_X_SMARTPLATE_KEY"] = body["access_key"]
    return body


def test_production_flag_can_never_be_enabled(monkeypatch):
    import importlib
    monkeypatch.setenv("SMARTPLATE_FIXTURE_DATA", "1")
    monkeypatch.setenv("RENDER", "true")
    try:
        importlib.reload(config)
        assert config.PRODUCTION and not config.FIXTURE_DATA
        monkeypatch.delenv("RENDER")
        monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@ep-x-pooler.ap-southeast-1.aws.neon.tech/neondb")
        importlib.reload(config)
        assert config.PRODUCTION and not config.FIXTURE_DATA
    finally:
        monkeypatch.undo()
        importlib.reload(config)
    assert config.FIXTURE_DATA                     # the suite itself runs with fixtures


def test_seed_refuses_outside_fixtures(prod):
    from smartplate import seed
    db.init_db()
    with pytest.raises(RuntimeError):
        seed.seed_all()


def test_fresh_production_database_has_no_sample_anything(prod):
    client = create_app().test_client()
    assert client.get("/api/users").get_json() == []
    with db.cursor() as cur:
        for table in ("users", "restaurants", "menu_items", "weather", "surge_history", "households", "plans"):
            assert cur.execute(f"SELECT COUNT(*) n FROM {table}").fetchone()["n"] == 0, table
        assert cur.execute("SELECT COUNT(*) n FROM festivals").fetchone()["n"] > 0   # the real holiday calendar
    assert client.get("/api/restaurants").get_json() == []


def test_new_profile_without_swiggy_plans_home_cooking_only_and_says_why(prod):
    client = create_app().test_client()
    view = _new_profile(client)
    cells = [c for d in view["grid"] for c in d["meals"].values()]
    assert cells and all(c["kind"] in ("cook", "skip", "leftover", "past") for c in cells), {c["kind"] for c in cells}
    assert not any(c["kind"] == "delivery" for c in cells)
    src = view["source"]
    assert src["kind"] == "none" and src["needs"] == "connect"
    assert "sample" not in json.dumps(src).lower()
    # home-cooked costs are estimates that say what they are based on
    cook = [c for c in cells if c["kind"] == "cook"]
    assert cook and all(c.get("cost_basis") for c in cook)
    # no sample weather is ever presented
    assert all(d["weather_source"] != "sample" for d in view["week_context"])
    assert view["weather_source"] != "sample"


def test_migration_removes_only_seeded_samples_and_keeps_real_accounts(seeded, monkeypatch):
    # A database seeded by an earlier version, plus a real person's profile and sign-in.
    client = create_app().test_client()
    real = _new_profile(client)
    uid = real["user"]["id"]
    r = client.post(f"/api/user/{uid}/account", json={"login": "priya", "password": "correct horse"})
    assert r.status_code == 200, r.get_json()
    # an open profile someone named "Meera" without the seed's sample marker is NOT a sample
    with db.cursor() as cur:
        cur.execute("INSERT INTO users(name, city, prefs) VALUES ('Meera', 'Chennai', '{}')")
        legacy_meera = cur.lastrowid
        # the real user's open meal still points at a sample dish (planned before the cleanup)
        sample_item = cur.execute("SELECT id, restaurant_id, name FROM menu_items WHERE source='sample' "
                                  "AND veg=1 LIMIT 1").fetchone()
        sid = cur.execute("SELECT s.id FROM sessions s JOIN plans p ON p.id=s.plan_id WHERE p.user_id=? "
                          "AND s.status='active' ORDER BY s.day DESC LIMIT 1", (uid,)).fetchone()["id"]
        cur.execute("UPDATE decisions SET chosen_kind='delivery', item_id=?, restaurant_id=?, item_name=? "
                    "WHERE session_id=?", (sample_item["id"], sample_item["restaurant_id"], sample_item["name"], sid))
    monkeypatch.setattr(config, "FIXTURE_DATA", False)
    create_app()                                           # start-up runs the migration
    with db.cursor() as cur:
        names = {r["name"] for r in cur.execute("SELECT name FROM users").fetchall()}
        assert "Sample profile" not in names and "Arjun" not in names
        assert cur.execute("SELECT 1 FROM users WHERE id=?", (legacy_meera,)).fetchone()     # not provably seeded
        assert cur.execute("SELECT 1 FROM users WHERE id=?", (uid,)).fetchone()
        assert cur.execute("SELECT 1 FROM logins WHERE user_id=?", (uid,)).fetchone()
        for table in ("weather", "surge_history"):
            assert cur.execute(f"SELECT COUNT(*) n FROM {table}").fetchone()["n"] == 0
        assert cur.execute("SELECT COUNT(*) n FROM restaurants WHERE source='sample'").fetchone()["n"] == 0
        assert cur.execute("SELECT COUNT(*) n FROM menu_items WHERE source='sample'").fetchone()["n"] == 0
        assert cur.execute("SELECT COUNT(*) n FROM households WHERE name='Flat 3B'").fetchone()["n"] == 0
        for t in ("plans", "calendar_events", "leftovers", "favourites"):
            assert cur.execute(f"SELECT COUNT(*) n FROM {t} WHERE user_id IN (1,2,3)").fetchone()["n"] == 0, t
        d = cur.execute("SELECT chosen_kind, item_id FROM decisions WHERE session_id=?", (sid,)).fetchone()
        assert d is not None and d["chosen_kind"] != "delivery"          # re-planned from real data
    # the real person still signs in, and their plan has no sample dish
    signin = client.post("/api/signin", json={"login": "priya", "password": "correct horse"})
    assert signin.status_code == 200
    view = client.get(f"/api/user/{uid}/plan").get_json()
    assert not [c for d in view["grid"] for c in d["meals"].values() if c["kind"] == "delivery"]
    # idempotent
    again = cleanup.remove_seeded_samples()
    assert again["users"] == [] and again["restaurants"] == 0 and again["menu_items"] == 0


# --------------------------------------------------------------------------- #
# Today / Plan pick from real Swiggy dishes for the chosen address (live cart test, 8 Oct)
# --------------------------------------------------------------------------- #
@pytest.fixture()
def swiggy(monkeypatch):
    from test_delivery_address import MENU, FakeAccountCart
    from smartplate.integrations import swiggy_connect
    fake = FakeAccountCart()
    fake.dishes = dict(MENU)
    monkeypatch.setattr(swiggy_connect, "_http", fake)
    return fake


def _tool_calls(fake):
    return [json.loads(c[3])["params"]["name"] for c in fake.calls]


def test_connected_user_gets_live_picks_and_reviews_with_swiggy_ids(prod, swiggy):
    from test_followups import _connect
    client = create_app().test_client()
    uid = _new_profile(client)["user"]["id"]
    _connect(client, swiggy, uid=uid)
    assert client.post(f"/api/user/{uid}/swiggy/address", json={"address_id": "addr-home"}).status_code == 200
    client.post(f"/api/user/{uid}/swiggy/favourites",
                json={"restaurant_id": "r-1", "restaurant_name": "Hotel Saravana Bhavan (Adyar)"})
    view = client.get(f"/api/user/{uid}/plan").get_json()
    assert view["source"]["kind"] == "none" and view["source"]["needs"] == "sync"   # the app syncs by itself
    pid = view["plan"]["id"]
    view = client.post(f"/api/plan/{pid}/live-menus", json={}).get_json()
    assert view["source"]["kind"] == "live"
    delivery = [c for d in view["grid"] for c in d["meals"].values()
                if c["kind"] == "delivery" and c["status"] == "active"]
    assert delivery and all(c["restaurant"] == "Hotel Saravana Bhavan (Adyar)" for c in delivery)
    assert {c["item"] for c in delivery} <= {"Veg Meals", "Mini Tiffin"}
    before = len(swiggy.calls)
    preview = client.get(f"/api/session/{delivery[0]['session_id']}/swiggy-cart/preview")
    assert preview.status_code == 200, preview.get_json()
    called = _tool_calls(type("F", (), {"calls": swiggy.calls[before:]}))
    assert "search_restaurants" not in called and "search_menu" in called      # Swiggy's own ids, no name search
    body = preview.get_json()
    assert body["restaurant_id"] == "r-1" and body["item"] == delivery[0]["item"]


def test_no_live_pick_possible_says_why_and_never_fakes_one(prod, swiggy):
    from test_followups import _connect
    client = create_app().test_client()
    uid = _new_profile(client)["user"]["id"]
    _connect(client, swiggy, uid=uid)
    client.post(f"/api/user/{uid}/swiggy/address", json={"address_id": "addr-home"})
    client.post(f"/api/user/{uid}/swiggy/favourites",
                json={"restaurant_id": "r-1", "restaurant_name": "Hotel Saravana Bhavan (Adyar)"})
    swiggy.dishes = {"Chef's Special Platter": 30000}                # nothing SmartPlate can plan with
    pid = client.get(f"/api/user/{uid}/plan").get_json()["plan"]["id"]
    r = client.post(f"/api/plan/{pid}/live-menus", json={})
    assert r.status_code == 502 and "no dishes SmartPlate can plan" in r.get_json()["error"]
    view = client.get(f"/api/plan/{pid}").get_json()
    assert not [c for d in view["grid"] for c in d["meals"].values() if c["kind"] == "delivery"]


# --------------------------------------------------------------------------- #
# The setup wizard sets a sign-in, so a profile is never bound to one browser
# --------------------------------------------------------------------------- #
def test_profile_created_with_sign_in_opens_on_another_device(prod):
    app = create_app()
    first = app.test_client()
    r = first.post("/api/profiles", json={**SETUP, "login": "Priya@Example.com", "password": "long enough pw"})
    assert r.status_code == 201, r.get_json()
    body = r.get_json()
    assert body["login"] == "priya@example.com"
    other = app.test_client()                              # a new device: no cookies, no key
    s = other.post("/api/signin", json={"login": "priya@example.com", "password": "long enough pw"})
    assert s.status_code == 200 and s.get_json()["user_id"] == body["user"]["id"]
    other.environ_base["HTTP_X_SMARTPLATE_KEY"] = s.get_json()["key"]
    assert other.get(f"/api/user/{body['user']['id']}/plan").status_code == 200


def test_bad_or_taken_sign_in_creates_no_profile(prod):
    client = create_app().test_client()
    assert client.post("/api/profiles", json={**SETUP, "login": "priya", "password": "short"}).status_code == 400
    assert client.post("/api/profiles", json={**SETUP, "login": "priya", "password": "long enough pw"}).status_code == 201
    taken = client.post("/api/profiles", json={**SETUP, "login": "PRIYA", "password": "another long pw"})
    assert taken.status_code == 400 and "taken" in taken.get_json()["error"]
    with db.cursor() as cur:
        assert cur.execute("SELECT COUNT(*) n FROM users").fetchone()["n"] == 1
