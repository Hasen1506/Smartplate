"""Per-user delegated Swiggy OAuth. Tokens never enter browser storage."""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import secrets
from urllib.parse import urlencode

import requests
from cryptography.fernet import Fernet
from flask import session

from .. import config, db

ROOT = "https://mcp.swiggy.com"


class ConnectionError(ValueError):
    pass


def _cipher() -> Fernet:
    if not config.SESSION_SECRET:
        raise ConnectionError("Token encryption is not configured")
    key = base64.urlsafe_b64encode(hashlib.sha256(
        ("smartplate-swiggy-token:" + config.SESSION_SECRET).encode()).digest())
    return Fernet(key)


def _callback_url() -> str:
    if not config.PUBLIC_BASE_URL or not (config.PUBLIC_BASE_URL.startswith("https://") or
                                          config.PUBLIC_BASE_URL.startswith("http://localhost:")):
        raise ConnectionError("Configure SMARTPLATE_PUBLIC_BASE_URL with an allowlisted HTTPS origin")
    return config.PUBLIC_BASE_URL + "/api/swiggy/callback"


def _post(endpoint: str, payload: dict) -> dict:
    try:
        response = requests.post(ROOT + endpoint, json=payload, timeout=12)
    except requests.RequestException as exc:
        raise ConnectionError("Swiggy connection is temporarily unavailable") from exc
    if not response.ok:
        raise ConnectionError("Swiggy rejected the connection request")
    return response.json()


def _client_id(redirect_uri: str) -> str:
    with db.cursor() as cur:
        row = cur.execute("SELECT client_id FROM swiggy_oauth_clients WHERE redirect_uri=?",
                          (redirect_uri,)).fetchone()
    if row:
        return row["client_id"]
    data = _post("/auth/register", {
        "client_name": "SmartPlate", "redirect_uris": [redirect_uri],
        "grant_types": ["authorization_code"], "response_types": ["code"],
        "token_endpoint_auth_method": "none",
    })
    client_id = data.get("client_id")
    if not isinstance(client_id, str) or not client_id:
        raise ConnectionError("Swiggy did not register the application")
    with db.cursor() as cur:
        cur.execute("INSERT INTO swiggy_oauth_clients(redirect_uri,client_id) VALUES (?,?)",
                    (redirect_uri, client_id))
    return client_id


def authorization_url(user_id: int) -> str:
    callback = _callback_url()
    client_id = _client_id(callback)
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    state = secrets.token_urlsafe(32)
    session["swiggy_oauth"] = {"state": state, "verifier": verifier,
                               "client_id": client_id, "user_id": user_id}
    return ROOT + "/auth/authorize?" + urlencode({
        "response_type": "code", "client_id": client_id, "redirect_uri": callback,
        "code_challenge": challenge, "code_challenge_method": "S256",
        "state": state, "scope": "mcp:tools",
    })


def complete(user_id: int, code: str, state: str) -> None:
    pending = session.pop("swiggy_oauth", None)
    if not pending or pending.get("user_id") != user_id or not secrets.compare_digest(
            pending.get("state", ""), state):
        raise ConnectionError("Swiggy connection expired; start again")
    data = _post("/auth/token", {
        "grant_type": "authorization_code", "code": code,
        "code_verifier": pending["verifier"], "client_id": pending["client_id"],
        "redirect_uri": _callback_url(),
    })
    token, lifetime = data.get("access_token"), data.get("expires_in")
    if not isinstance(token, str) or not token or not isinstance(lifetime, (int, float)) or lifetime <= 0:
        raise ConnectionError("Swiggy returned an invalid access token")
    expires = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=lifetime)).isoformat()
    with db.cursor() as cur:
        cur.execute("INSERT INTO swiggy_connections(user_id,token_ciphertext,expires_ts) VALUES (?,?,?) "
                    "ON CONFLICT(user_id) DO UPDATE SET token_ciphertext=excluded.token_ciphertext, "
                    "expires_ts=excluded.expires_ts, address_id=NULL, address_label=NULL",
                    (user_id, _cipher().encrypt(token.encode()).decode(), expires))


def token_for(user_id: int) -> str:
    with db.cursor() as cur:
        row = cur.execute("SELECT token_ciphertext,expires_ts FROM swiggy_connections WHERE user_id=?",
                          (user_id,)).fetchone()
    if not row:
        raise ConnectionError("Connect your Swiggy account first")
    if dt.datetime.fromisoformat(row["expires_ts"]) <= dt.datetime.now(dt.timezone.utc):
        raise ConnectionError("Swiggy access expired; reconnect your account")
    return _cipher().decrypt(row["token_ciphertext"].encode()).decode()


def selected_address(user_id: int) -> str:
    with db.cursor() as cur:
        row = cur.execute("SELECT address_id FROM swiggy_connections WHERE user_id=?",
                          (user_id,)).fetchone()
    if not row or not row["address_id"]:
        raise ConnectionError("Select a Swiggy delivery address first")
    return row["address_id"]


def status(user_id: int) -> dict:
    try:
        token_for(user_id)
    except ConnectionError as exc:
        return {"connected": False, "message": str(exc), "address_id": None}
    with db.cursor() as cur:
        row = cur.execute("SELECT address_id,address_label FROM swiggy_connections WHERE user_id=?",
                          (user_id,)).fetchone()
    return {"connected": True, "address_id": row["address_id"],
            "address_label": row["address_label"]}


def disconnect(user_id: int) -> None:
    token = None
    try:
        token = token_for(user_id)
    except ConnectionError:
        pass
    if token:
        try:
            requests.post(ROOT + "/auth/logout", headers={"Authorization": "Bearer " + token}, timeout=8)
        except requests.RequestException:
            pass
    with db.cursor() as cur:
        cur.execute("DELETE FROM swiggy_connections WHERE user_id=?", (user_id,))
        cur.execute("DELETE FROM live_menu_items WHERE live_restaurant_id IN "
                    "(SELECT id FROM live_restaurants WHERE user_id=?)", (user_id,))
        cur.execute("DELETE FROM live_restaurants WHERE user_id=?", (user_id,))
