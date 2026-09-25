"""Profile privacy for a shared trial — a per-profile secret, not a full account system.

A profile created through onboarding gets a random key. Only its SHA-256 is stored;
the key itself is returned once and kept in the person's browser. Every endpoint that
reads or changes that profile (its user, plans, sessions, expenses, reminders) must
present it in the `X-SmartPlate-Key` header — or, for plain download links (CSV, .ics),
as `?key=` on a GET. Sample profiles, and profiles made before keys existed, have no
key and stay open.

What this is not: sign-in, password recovery or multi-device sync. Losing the key
(clearing the browser) loses access; the "recovery code" (`<id>.<key>`) shown in the
app is the way to move a profile to another device. Real accounts are the next step
before a public launch.
"""
import hashlib
import hmac
import secrets

from . import db

HEADER = "X-SmartPlate-Key"


def new_key() -> tuple[str, str]:
    key = secrets.token_urlsafe(24)
    return key, digest(key)


def digest(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def owners_of(view_args: dict, body: dict | None, query_user: int | None = None) -> set[int]:
    """Every user a request names — URL path, `?user_id=` and the JSON body. Access is
    required for ALL of them, so an open profile's id in one place can never vouch for
    a private profile named in another."""
    owners = set()
    with db.cursor() as cur:
        if "user_id" in view_args:
            owners.add(view_args["user_id"])
        if "plan_id" in view_args:
            row = cur.execute("SELECT user_id FROM plans WHERE id=?", (view_args["plan_id"],)).fetchone()
            if row:
                owners.add(row["user_id"])
        if "session_id" in view_args:
            row = cur.execute("SELECT p.user_id FROM sessions s JOIN plans p ON p.id=s.plan_id WHERE s.id=?",
                              (view_args["session_id"],)).fetchone()
            if row:
                owners.add(row["user_id"])
    if query_user is not None:
        owners.add(query_user)
    if body and isinstance(body.get("user_id"), int) and not isinstance(body.get("user_id"), bool):
        owners.add(body["user_id"])
    return owners


def allowed(user_id: int | None, presented: str | None) -> bool:
    if user_id is None:
        return True
    with db.cursor() as cur:
        row = cur.execute("SELECT access_hash FROM users WHERE id=?", (user_id,)).fetchone()
    if not row or not row["access_hash"]:
        return True                                     # sample / legacy profile: open
    return bool(presented) and hmac.compare_digest(row["access_hash"], digest(presented))
