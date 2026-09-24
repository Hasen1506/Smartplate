"""Invite-only Supabase email OTP and local account ownership."""
from __future__ import annotations

import re

import requests
from flask import session

from . import config, db

EMAIL = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


class AuthError(ValueError):
    pass


def _invited(email: str) -> bool:
    return email in config.INVITED_EMAILS


def _request(endpoint: str, payload: dict) -> dict:
    if not config.SUPABASE_URL or not config.SUPABASE_PUBLISHABLE_KEY:
        raise AuthError("Sign-in is not configured")
    try:
        response = requests.post(
            f"{config.SUPABASE_URL}/auth/v1/{endpoint}",
            json=payload,
            headers={"apikey": config.SUPABASE_PUBLISHABLE_KEY},
            timeout=12,
        )
    except requests.RequestException as exc:
        raise AuthError("Sign-in service is temporarily unavailable") from exc
    if not response.ok:
        raise AuthError("Sign-in failed; check your code or try again")
    return response.json()


def request_code(email: str) -> None:
    email = email.strip().lower()
    if not EMAIL.fullmatch(email) or not _invited(email):
        raise AuthError("This email is not on the SmartPlate invite list")
    # Invitees must already exist in Supabase; direct OTP cannot open signup.
    _request("otp", {"email": email, "create_user": False})


def verify_code(email: str, code: str) -> dict:
    email = email.strip().lower()
    if not EMAIL.fullmatch(email) or not _invited(email):
        raise AuthError("This email is not on the SmartPlate invite list")
    if not re.fullmatch(r"\d{6,8}", code):
        raise AuthError("Enter the code from your email")
    data = _request("verify", {"email": email, "token": code, "type": "email"})
    user = data.get("user") or {}
    uid = user.get("id")
    if not uid or (user.get("email") or "").lower() != email or not data.get("access_token"):
        raise AuthError("Sign-in could not be verified")
    with db.cursor() as cur:
        row = cur.execute("SELECT id FROM users WHERE supabase_uid=?", (uid,)).fetchone()
        if row is None:
            cur.execute("INSERT INTO users(supabase_uid,name,city) VALUES (?,?,?)",
                        (uid, email.split("@")[0][:80], ""))
            user_id = cur.lastrowid
        else:
            user_id = row["id"]
    session.clear()
    session.permanent = True
    session["uid"] = uid
    session["user_id"] = user_id
    session["email"] = email
    return {"id": user_id, "email": email}


def current_user_id() -> int | None:
    if config.APP_MODE == "demo":
        return None
    uid, user_id, email = session.get("uid"), session.get("user_id"), session.get("email")
    if not uid or not isinstance(user_id, int) or not isinstance(email, str) or not _invited(email):
        return None
    with db.cursor() as cur:
        row = cur.execute("SELECT id FROM users WHERE id=? AND supabase_uid=?", (user_id, uid)).fetchone()
    return row["id"] if row else None
