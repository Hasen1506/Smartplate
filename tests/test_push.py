"""Push reminders: RFC 8291 encryption, VAPID, subscriptions and the due-reminder sender."""
import datetime as dt
import json

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

from smartplate import config, push
from smartplate.app import create_app
from smartplate.integrations import webpush as w


@pytest.fixture
def client(seeded):
    app = create_app()
    app.config["TESTING"] = True
    return app.test_client()


def test_encryption_matches_rfc8291_worked_example():
    # RFC 8291 §5: fixed sender key, receiver key, auth secret and salt → exact bytes.
    sender = w.private_key_from("yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw")
    body = w.encrypt(b"When I grow up, I want to be a watermelon",
                     "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4",
                     "BTBZMqHH6r4Tts7J_aSIgg", sender_private=sender, salt=w.unb64u("DGv6ra1nlYgDCS1FRnbzlw"))
    assert w.b64u(body) == (
        "DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6TlzA"
        "C8wEqKK6PBru3jl7A_yl95bQpu6cVPTpK4Mqgkf1CXztLVBSt2Ks3oZwbuwXPXLWyouBWLVWGNWQexSgSxsj_Qulcy4a-fN")


def test_vapid_header_is_a_valid_es256_jwt_for_the_push_origin():
    key = w.new_private_key()
    header = w.vapid_header("https://fcm.googleapis.com/fcm/send/abc", key, "mailto:a@b.c", now=1_000_000)
    t, k = header.removeprefix("vapid t=").split(", k=")
    assert k == w.public_key_b64u(key)
    head, claims, sig = t.split(".")
    assert json.loads(w.unb64u(claims)) == {"aud": "https://fcm.googleapis.com", "exp": 1_000_000 + 12 * 3600,
                                            "sub": "mailto:a@b.c"}
    raw = w.unb64u(sig)
    public = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), w.unb64u(k))
    public.verify(encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big")),
                  f"{head}.{claims}".encode(), ec.ECDSA(hashes.SHA256()))       # raises if invalid


def _browser_subscription(endpoint="https://push.example.com/send/1"):
    ua = w.private_key_from(w.new_private_key())
    return {"endpoint": endpoint, "keys": {"p256dh": w.b64u(w._public_bytes(ua)), "auth": w.b64u(b"0123456789abcdef")}}


class FakeService:
    def __init__(self, code=201):
        self.code, self.calls = code, []

    def __call__(self, endpoint, p256dh, auth, payload, *, private_b64u, contact):
        self.calls.append((endpoint, payload))
        return self.code


def test_subscribe_validates_and_reports_this_device(client):
    st = client.get("/api/user/1/push").get_json()
    assert len(w.unb64u(st["public_key"])) == 65 and st["devices"] == 0
    for bad in [{"endpoint": "http://insecure"}, {"endpoint": "https://x", "keys": {"p256dh": "short", "auth": "x"}}]:
        assert client.post("/api/user/1/push/subscribe", json={"subscription": bad}).status_code == 400
    sub = _browser_subscription()
    r = client.post("/api/user/1/push/subscribe", json={"subscription": sub}).get_json()
    assert r["devices"] == 1 and r["this_device"]
    assert client.get("/api/user/1/push?endpoint=" + sub["endpoint"]).get_json()["this_device"]
    client.post("/api/user/1/push/unsubscribe", json={"endpoint": sub["endpoint"]})
    assert client.get("/api/user/1/push").get_json()["devices"] == 0


def test_due_reminders_are_sent_once_and_late_ones_dropped(client):
    client.post("/api/user/1/push/subscribe", json={"subscription": _browser_subscription()})
    first = client.get("/api/user/1/reminders").get_json()[0]
    when = dt.datetime.fromisoformat(first["at"])
    svc = FakeService()
    assert push.send_due(when + dt.timedelta(minutes=1), sender=svc) >= 1
    sent = [p for _, p in svc.calls]
    assert sent[0]["title"] == first["title"] and sent[0]["tag"] == f"smartplate-{first['session_id']}"
    assert sent[0]["url"] == (first["link"] or "/?tab=today")
    assert push.send_due(when + dt.timedelta(minutes=2), sender=svc) == 0              # never twice
    svc2 = FakeService()
    push.send_due(when + push.LATE_OK + dt.timedelta(minutes=5), sender=svc2)
    assert first["session_id"] not in [int(p["tag"].split("-")[1]) for _, p in svc2.calls]   # too late: dropped


def test_gone_subscriptions_are_forgotten(client):
    client.post("/api/user/1/push/subscribe", json={"subscription": _browser_subscription()})
    first = client.get("/api/user/1/reminders").get_json()[0]
    push.send_due(dt.datetime.fromisoformat(first["at"]), sender=FakeService(410))
    assert client.get("/api/user/1/push").get_json()["devices"] == 0


def test_failed_delivery_is_retried_next_tick(client):
    client.post("/api/user/1/push/subscribe", json={"subscription": _browser_subscription()})
    at = dt.datetime.fromisoformat(client.get("/api/user/1/reminders").get_json()[0]["at"])
    assert push.send_due(at, sender=FakeService(503)) == 0
    assert push.send_due(at + dt.timedelta(minutes=1), sender=FakeService(201)) >= 1


def test_test_message_and_private_profiles_need_their_key(client, monkeypatch):
    client.post("/api/user/1/push/subscribe", json={"subscription": _browser_subscription()})
    svc = FakeService()
    assert push.test_message(1, sender=svc) == {"sent": 1, "devices": 1}
    v = client.post("/api/profiles", json={"name": "P", "diet": "veg", "weekly_budget": 2000,
                                           "meals": ["dinner"], "favourites": [6]}).get_json()
    uid = v["user"]["id"]
    assert client.post(f"/api/user/{uid}/push/subscribe", json={"subscription": _browser_subscription("https://p/2")}).status_code == 401
    assert client.post(f"/api/user/{uid}/push/subscribe", json={"subscription": _browser_subscription("https://p/2")},
                       headers={"X-SmartPlate-Key": v["access_key"]}).status_code == 200


def test_worker_respects_the_switch(monkeypatch):
    monkeypatch.setattr(config, "PUSH_ENABLED", False)
    assert push.start_worker() is False


def test_service_worker_shows_pushed_reminders(client):
    sw = client.get("/sw.js").data.decode()
    assert 'addEventListener("push"' in sw and "showNotification" in sw and "notificationclick" in sw
