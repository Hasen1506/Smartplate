"""Web Push sender: RFC 8291 message encryption (aes128gcm) + RFC 8292 VAPID.

Small and dependency-light on purpose (only `cryptography`): the usual Python library
pulls in an sdist that fails to build on some hosts. Verified against the worked
example in RFC 8291 §5 (tests/test_push.py).

    body, headers = encrypt(payload, p256dh, auth)          # the message
    headers["Authorization"] = vapid_header(endpoint, key)  # who is sending
    POST endpoint

The push service (Google, Mozilla, Apple) only relays the ciphertext; it can't read it.
"""
import base64
import json
import os
import struct
import time
import urllib.error
import urllib.parse
import urllib.request

from cryptography.hazmat.primitives import hashes, hmac, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

RECORD_SIZE = 4096


def b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def unb64u(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _hmac(key: bytes, data: bytes) -> bytes:
    h = hmac.HMAC(key, hashes.SHA256())
    h.update(data)
    return h.finalize()


def _public_bytes(key: ec.EllipticCurvePrivateKey) -> bytes:
    return key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)


def private_key_from(raw_b64u: str) -> ec.EllipticCurvePrivateKey:
    return ec.derive_private_key(int.from_bytes(unb64u(raw_b64u), "big"), ec.SECP256R1())


def new_private_key() -> str:
    """A fresh P-256 key as base64url of its 32-byte private value."""
    key = ec.generate_private_key(ec.SECP256R1())
    return b64u(key.private_numbers().private_value.to_bytes(32, "big"))


def public_key_b64u(private_b64u: str) -> str:
    """The applicationServerKey a browser subscribes with."""
    return b64u(_public_bytes(private_key_from(private_b64u)))


def encrypt(payload: bytes, p256dh: str, auth: str, *, sender_private: ec.EllipticCurvePrivateKey | None = None,
            salt: bytes | None = None) -> bytes:
    """Encrypt one push message for a subscription (RFC 8291). `sender_private` and
    `salt` are only fixed by tests; normally both are fresh per message."""
    ua_public = unb64u(p256dh)
    auth_secret = unb64u(auth)
    as_private = sender_private or ec.generate_private_key(ec.SECP256R1())
    as_public = _public_bytes(as_private)
    salt = salt or os.urandom(16)

    ua_key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_public)
    ecdh_secret = as_private.exchange(ec.ECDH(), ua_key)
    prk_key = _hmac(auth_secret, ecdh_secret)
    ikm = _hmac(prk_key, b"WebPush: info\x00" + ua_public + as_public + b"\x01")
    prk = _hmac(salt, ikm)
    cek = _hmac(prk, b"Content-Encoding: aes128gcm\x00\x01")[:16]
    nonce = _hmac(prk, b"Content-Encoding: nonce\x00\x01")[:12]

    if len(payload) > RECORD_SIZE - 16 - 1 - 100:
        raise ValueError("push payload too large")
    ciphertext = AESGCM(cek).encrypt(nonce, payload + b"\x02", None)   # 0x02: last (only) record
    header = salt + struct.pack("!IB", RECORD_SIZE, len(as_public)) + as_public
    return header + ciphertext


def vapid_header(endpoint: str, private_b64u: str, contact: str, *, now: float | None = None) -> str:
    """`Authorization: vapid t=<JWT>, k=<public key>` for this push service (RFC 8292)."""
    parts = urllib.parse.urlsplit(endpoint)
    claims = {"aud": f"{parts.scheme}://{parts.netloc}", "exp": int((now or time.time()) + 12 * 3600), "sub": contact}
    signing_input = (b64u(json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode()) + "." +
                     b64u(json.dumps(claims, separators=(",", ":")).encode()))
    key = private_key_from(private_b64u)
    r, s = decode_dss_signature(key.sign(signing_input.encode(), ec.ECDSA(hashes.SHA256())))
    jwt = signing_input + "." + b64u(r.to_bytes(32, "big") + s.to_bytes(32, "big"))
    return f"vapid t={jwt}, k={b64u(_public_bytes(key))}"


def send(endpoint: str, p256dh: str, auth: str, payload: dict, *, private_b64u: str, contact: str,
         ttl: int = 3600, timeout: float = 10) -> int:
    """Deliver one message; returns the push service's HTTP status (201 = accepted,
    404/410 = the subscription is gone and should be deleted)."""
    body = encrypt(json.dumps(payload).encode(), p256dh, auth)
    req = urllib.request.Request(endpoint, data=body, method="POST", headers={
        "Content-Encoding": "aes128gcm", "Content-Type": "application/octet-stream", "TTL": str(ttl),
        "Urgency": "high", "Authorization": vapid_header(endpoint, private_b64u, contact)})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            return res.status
    except urllib.error.HTTPError as e:
        return e.code
