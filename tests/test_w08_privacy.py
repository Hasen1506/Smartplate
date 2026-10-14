"""Grill #43-#45 and usability #50: DPDP notice and consent, privacy/terms pages, deletion,
and no raw Swiggy replies (or personal data) in what Ziggy keeps or logs."""
import datetime as dt
import glob
import json
import os
import re

import pytest

from smartplate import clock, config, db, privacy
from smartplate.app import create_app
from test_followups import FakeSwiggy, _connect  # noqa: F401  (fixtures reused)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = {"name": "Priya", "diet": "veg", "weekly_budget": 2000, "meals": ["dinner"]}


@pytest.fixture
def client(seeded):
    app = create_app()
    app.config["TESTING"] = True
    return app.test_client()


@pytest.fixture
def swiggy(monkeypatch):
    from smartplate.integrations import swiggy_connect
    fake = FakeSwiggy()
    monkeypatch.setattr(swiggy_connect, "_http", fake)
    return fake


def _make(client, consent, **extra):
    return client.post("/api/profiles", json={**BASE, **extra, **({"consent": consent} if consent is not None else {})})


def _own(client, consent=True, **extra):
    r = _make(client, consent, **extra)
    assert r.status_code == 201, r.get_json()
    body = r.get_json()
    return body["user"]["id"], {"X-SmartPlate-Key": body["access_key"]}


# ---------------------------------------------------------------- notice and consent
def test_no_profile_without_the_notice_being_agreed(client):
    for consent in (None, False, {}, {"account": False}, "yes"):
        r = _make(client, consent)
        assert r.status_code == 400 and "privacy notice" in r.get_json()["error"]


def test_signup_consent_is_recorded_per_purpose(client):
    uid, h = _own(client, {"account": True, "learning": True})
    state = client.get(f"/api/user/{uid}/privacy", headers=h).get_json()
    assert state["legacy"] is False
    assert state["purposes"]["account"]["granted"] and state["purposes"]["learning"]["granted"]
    assert state["purposes"]["health"]["granted"] is False
    assert state["purposes"]["account"]["version"] == privacy.NOTICE_VERSION
    assert "swiggy" not in state["purposes"]                        # asked separately, at connect time


def test_health_details_need_their_own_consent(client):
    r = _make(client, True, medical=["diabetes"])
    assert r.status_code == 400 and "health" in r.get_json()["error"]
    uid, h = _own(client, {"account": True, "health": True}, medical=["diabetes"])
    # without the health consent, adding a medical filter later is refused with the notice
    uid2, h2 = _own(client, True)
    r = client.patch(f"/api/user/{uid2}/setup", json={"medical": ["diabetes"]}, headers=h2)
    assert r.status_code == 409 and r.get_json()["code"] == "consent_required"
    assert r.get_json()["purpose"] == "health" and r.get_json()["notice"]["data"]
    assert client.post(f"/api/user/{uid2}/privacy", json={"purpose": "health", "granted": True}, headers=h2).status_code == 200
    assert client.patch(f"/api/user/{uid2}/setup", json={"medical": ["diabetes"]}, headers=h2).status_code == 200


def test_withdrawing_health_erases_it(client):
    uid, h = _own(client, {"account": True, "health": True}, medical=["diabetes"])
    client.post(f"/api/user/{uid}/privacy", json={"purpose": "health", "granted": False}, headers=h)
    with db.cursor() as cur:
        assert json.loads(cur.execute("SELECT medical FROM users WHERE id=?", (uid,)).fetchone()[0]) == []


def test_learning_off_means_reasons_are_not_kept_or_used(client):
    from smartplate.domain import learning
    uid, _ = _own(client, True)                                      # learning not ticked
    assert learning.record(uid, 1, ["spicy"], item_id=1) == []
    uid2, h2 = _own(client, {"account": True, "learning": True})
    assert learning.record(uid2, 1, ["spicy"], item_id=1) == ["spicy"]
    assert learning.weights(uid2) != learning.weights(uid)
    client.post(f"/api/user/{uid2}/privacy", json={"purpose": "learning", "granted": False}, headers=h2)
    with db.cursor() as cur:
        assert not cur.execute("SELECT 1 FROM rating_reasons WHERE user_id=?", (uid2,)).fetchone()


def test_account_consent_is_withdrawn_by_deleting(client):
    uid, h = _own(client, True)
    r = client.post(f"/api/user/{uid}/privacy", json={"purpose": "account", "granted": False}, headers=h)
    assert r.status_code == 400 and "delete" in r.get_json()["error"]


def test_profiles_made_before_consent_keep_working_except_swiggy(seeded):
    # Sample/legacy profiles have no consent rows: earlier use continues (DPDP s.5(2)).
    assert privacy.granted(1, "learning") and privacy.granted(1, "health")
    assert not privacy.granted(1, "swiggy")


# ---------------------------------------------------------------- Swiggy
def test_swiggy_connect_needs_its_notice_and_records_it(client, swiggy):
    from test_followups import _allow_app_host, _secure_profile
    _allow_app_host()
    _secure_profile(client, 1)
    r = client.post("/api/user/1/swiggy/connect", json={}, base_url="https://app.example")
    assert r.status_code == 409 and r.get_json()["purpose"] == "swiggy"
    assert "recent Swiggy orders" in " ".join(r.get_json()["notice"]["data"])
    r = client.post("/api/user/1/swiggy/connect", json={"consent": True}, base_url="https://app.example")
    assert r.status_code == 200 and privacy.granted(1, "swiggy")


def test_order_history_import_needs_swiggy_consent(client, swiggy):
    _connect(client, swiggy)                                         # consents while connecting
    with db.cursor() as cur:                                         # a connection made before notices existed
        cur.execute("DELETE FROM consent_log WHERE user_id=1")
    r = client.get("/api/user/1/swiggy/order-history")
    assert r.status_code == 409 and r.get_json()["code"] == "consent_required"


def test_withdrawing_swiggy_disconnects(client, swiggy):
    _connect(client, swiggy)
    assert client.get("/api/user/1/swiggy").get_json()["connected"]
    assert client.post("/api/user/1/privacy", json={"purpose": "swiggy", "granted": False}).status_code == 200
    assert not client.get("/api/user/1/swiggy").get_json()["connected"]


# ---------------------------------------------------------------- pages
def test_privacy_and_terms_pages(client, monkeypatch):
    for path in ("/privacy", "/terms"):
        r = client.get(path)
        page = r.data.decode()
        assert r.status_code == 200 and "{{" not in page and privacy.NOTICE_VERSION in page
    page = client.get("/privacy").data.decode()
    for must in ("Render", "Neon", "Singapore", "Swiggy", "Data Protection Board", "18", "90 days",
                 "Delete my Ziggy profile", "Download my data", "health", "one year"):
        assert must in page, must
    monkeypatch.setattr(config, "PRIVACY_CONTACT", "privacy@ziggy.example")
    monkeypatch.setattr(config, "OPERATOR", "Vijay <script>")
    page = client.get("/privacy").data.decode()
    assert "mailto:privacy@ziggy.example" in page and "<script>" not in page and "Vijay &lt;script&gt;" in page


def test_app_links_privacy_terms_and_delete():
    with open(os.path.join(ROOT, "smartplate", "static", "app.js"), encoding="utf-8") as f:
        js = f.read()
    assert 'href="/privacy"' in js and 'href="/terms"' in js
    assert "Delete my Ziggy profile" in js and 'id="ob-c-account"' in js


def test_notice_api_lists_every_purpose(client):
    n = client.get("/api/privacy/notice").get_json()
    assert set(n["purposes"]) == {"account", "health", "learning", "swiggy"} and n["adults_only"]
    for p in n["purposes"].values():
        assert p["data"] and p["why"] and p["without"]


# ---------------------------------------------------------------- deletion
def test_delete_keeps_only_a_dated_erasure_record_for_one_year(client, monkeypatch):
    uid, h = _own(client, True)
    r = client.delete(f"/api/user/{uid}", json={"confirmation": "DELETE"}, headers=h)
    assert r.status_code == 200
    with db.cursor() as cur:
        assert not cur.execute("SELECT 1 FROM users WHERE id=?", (uid,)).fetchone()
        rows = cur.execute("SELECT purpose FROM consent_log WHERE user_id=?", (uid,)).fetchall()
    assert "erasure" in [r[0] for r in rows]
    assert privacy.purge_expired() == 0                             # not yet a year
    later = clock.now() + privacy.LOG_RETENTION + dt.timedelta(days=1)
    monkeypatch.setattr(clock, "now", lambda: later)
    privacy.purge_expired()
    with db.cursor() as cur:
        assert not cur.execute("SELECT 1 FROM consent_log WHERE user_id=?", (uid,)).fetchone()


# ---------------------------------------------------------------- redaction (#45)
def test_redact_masks_personal_data():
    raw = json.dumps({"addressLine": "12 Lake View Rd, Adyar", "phone": "+91 98765 43210",
                      "email": "priya@example.com", "lat": 13.00123, "lng": 80.25678, "pincode": "600020",
                      "access_token": "eyJhbGciOiJIUzI1NiJ9.abcdefghijk"}) + " call 9876543210"
    out = privacy.redact(raw)
    for leak in ("Lake View", "98765", "9876543210", "priya@", "13.00123", "80.25678", "600020", "eyJhbGci"):
        assert leak not in out, leak


def test_swiggy_problem_log_and_errors_are_redacted(client, monkeypatch):
    from smartplate.integrations import swiggy_connect, swiggy_rules
    swiggy_rules.note(1, swiggy_connect.SwiggyError("Swiggy said: deliver to 12 Lake View Rd 600020, ph 9876543210"))
    with db.cursor() as cur:
        msg = cur.execute("SELECT message FROM swiggy_issues WHERE user_id=1").fetchone()[0]
    assert "9876543210" not in msg and "600020" not in msg


def test_swiggy_error_never_echoes_a_raw_reply(seeded, monkeypatch):
    from smartplate.integrations import swiggy_connect as sc
    from smartplate.integrations import swiggy_live
    reply = {"isError": True, "content": [{"type": "text", "text": json.dumps(
        {"address": {"addressLine": "12 Lake View Rd", "phone": "9876543210"}})}]}
    monkeypatch.setattr(swiggy_live, "_conn", lambda uid: {"access_token": "t", "tools": "[]", "samples": "{}"})
    monkeypatch.setattr(swiggy_live, "_tool", lambda conn, name: None)
    monkeypatch.setattr(swiggy_live, "_mcp_session", lambda uid, tok, fresh=False: ("s", False))
    monkeypatch.setattr(sc, "user_rpc", lambda *a, **k: (None, reply))
    with pytest.raises(sc.SwiggyError) as info:
        swiggy_live.call(1, "get_addresses", {})
    assert "9876543210" not in str(info.value) and "Lake View" not in str(info.value)


PHONE = re.compile(r"(?<![\d.])(?:\+91[\s-]?)?[6-9]\d{9}(?![\d.])")
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
SAFE_EMAIL = re.compile(r"@(example\.(com|org|net|invalid)|.*\.example|.*\.invalid)$|noreply")


def test_recorded_swiggy_fixtures_hold_no_personal_data():
    """A pre-commit-style guard (#45): recordings and fixtures must stay synthetic."""
    paths = glob.glob(os.path.join(ROOT, "tests", "fixtures", "swiggy*.json")) + \
        glob.glob(os.path.join(ROOT, "docs", "vendor", "**", "*"), recursive=True)
    for path in paths:
        if os.path.isdir(path):
            continue
        with open(path, encoding="utf-8", errors="ignore") as f:
            text = f.read()
        assert not PHONE.search(text), (path, PHONE.search(text).group(0))
        bad = [m for m in EMAIL.findall(text) if not SAFE_EMAIL.search(m)]
        assert not bad, (path, bad[:3])
