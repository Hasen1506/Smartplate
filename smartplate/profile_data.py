"""Export/delete a private profile's saved local data, excluding credentials."""
from . import clock, db

USER_TABLES = ("calendar_events", "leftovers", "receipts", "intake_log", "favourites", "ratings",
               "logins", "devices", "push_subscriptions", "swiggy_connections", "swiggy_pending",
               "swiggy_menus", "swiggy_favourites", "swiggy_order_attempts", "swiggy_checkout_quotes",
               "swiggy_cart_intents", "swiggy_delivery_fees", "swiggy_photos", "swiggy_issues")
# A stable row order for the export (Postgres has no implicit insertion order).
EXPORT_ORDER = {"favourites": "restaurant_id", "logins": "user_id", "swiggy_connections": "user_id",
                "swiggy_pending": "created_ts, state", "swiggy_menus": "restaurant",
                "swiggy_favourites": "address_id, restaurant_id", "swiggy_order_attempts": "created_ts, fingerprint",
                "swiggy_checkout_quotes": "created_ts, token", "swiggy_cart_intents": "user_id",
                "swiggy_delivery_fees": "address_id, provider_id", "swiggy_photos": "restaurant_id, dish", "swiggy_issues": "ts", "order_queue": "session_id",
                "grocery_have": "plan_id, item", "grocery_swaps": "plan_id, token",
                "push_sent": "subscription_id, session_id, at"}
PRIVATE_COLUMNS = frozenset({"access_hash", "pw_hash", "token_hash", "access_token", "verifier",
                             "token", "fingerprint", "p256dh", "auth", "idempotency_key"})


def _private(cur, user_id):
    row = cur.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    if not row or not row["access_hash"]:
        raise ValueError("Data controls require your own private profile. Shared samples cannot be deleted.")
    return row


def _clean(rows):
    return [{k: row[k] for k in row.keys() if k not in PRIVATE_COLUMNS} for row in rows]


def _household_people(cur, hid, user_id, columns):
    """The people this profile cooks for (prefs.household_member is true), read in Python
    so the same code runs on SQLite and Postgres (no json_extract)."""
    if not hid:
        return []
    rows = cur.execute(f"SELECT {columns} FROM users WHERE household_id=? AND id<>? ORDER BY id",
                       (hid, user_id)).fetchall()
    out = []
    for row in rows:
        prefs = db.jl(row["prefs"], {})
        if isinstance(prefs, dict) and prefs.get("household_member") == 1:   # true or 1, as json_extract()=1
            out.append(row)
    return out


def export(user_id):
    """Return all saved profile/meal records, without authentication material."""
    with db.cursor() as cur:
        cur.execute("BEGIN")
        user = _private(cur, user_id)
        data = {"profile": _clean([user])[0]}
        for table in USER_TABLES:
            if table in ("swiggy_pending", "swiggy_checkout_quotes"):
                continue  # ephemeral authorization material, never exported
            data[table] = _clean(cur.execute(f"SELECT * FROM {table} WHERE user_id=? "
                                             f"ORDER BY {EXPORT_ORDER.get(table, 'id')}", (user_id,)).fetchall())
        plan_scope = "SELECT id FROM plans WHERE user_id=?"
        decision_scope = f"SELECT id FROM decisions WHERE plan_id IN ({plan_scope})"
        subscription_scope = "SELECT id FROM push_subscriptions WHERE user_id=?"
        for table in ("plans", "sessions", "decisions", "grocery_baskets", "grocery_have", "grocery_swaps"):
            scope = "user_id=?" if table == "plans" else f"plan_id IN ({plan_scope})"
            data[table] = _clean(cur.execute(f"SELECT * FROM {table} WHERE {scope} "
                                             f"ORDER BY {EXPORT_ORDER.get(table, 'id')}", (user_id,)).fetchall())
        data["orders"] = _clean(cur.execute(f"SELECT * FROM orders WHERE decision_id IN ({decision_scope}) ORDER BY id", (user_id,)).fetchall())
        data["push_sent"] = _clean(cur.execute(f"SELECT * FROM push_sent WHERE subscription_id IN ({subscription_scope}) "
                                             f"ORDER BY {EXPORT_ORDER['push_sent']}", (user_id,)).fetchall())
        hid = user["household_id"]
        data["household"] = _clean(cur.execute("SELECT * FROM households WHERE id=?", (hid,)).fetchall()) if hid else []
        data["household_people"] = _clean(_household_people(cur, hid, user_id, "id, name, diet, allergens, medical, prefs"))
        data["community_templates"] = _clean(cur.execute("SELECT * FROM community_templates WHERE author_user_id=? ORDER BY id", (user_id,)).fetchall())
    return {"format_version": 1, "exported_at": clock.now().isoformat(), "data": data,
            "notes": ["Authentication secrets are excluded.", "Demo weekly plans and Swiggy order attempts are separate records.",
                      "This exports local records; Swiggy maintains its own account and order history."]}


def delete(user_id, confirmation):
    if confirmation != "DELETE":
        raise ValueError("Type DELETE to confirm permanent removal of this Ziggy profile.")
    with db.cursor() as cur:
        cur.execute("BEGIN IMMEDIATE")
        _private(cur, user_id)
        purge(cur, user_id)
    return {"deleted": True}


def purge(cur, user_id, *, people=True):
    """Delete one profile and everything recorded for it, inside the caller's transaction.
    `people`: also delete the household people this profile cooked for (no profile of their own)."""
    plan_scope = "SELECT id FROM plans WHERE user_id=?"
    decision_scope = f"SELECT id FROM decisions WHERE plan_id IN ({plan_scope})"
    subscription_scope = "SELECT id FROM push_subscriptions WHERE user_id=?"
    cur.execute(f"DELETE FROM orders WHERE decision_id IN ({decision_scope})", (user_id,))
    cur.execute(f"DELETE FROM push_sent WHERE subscription_id IN ({subscription_scope})", (user_id,))
    for table in ("grocery_baskets", "grocery_have", "grocery_swaps", "order_queue", "decisions", "sessions"):
        cur.execute(f"DELETE FROM {table} WHERE plan_id IN ({plan_scope})", (user_id,))
    cur.execute("DELETE FROM community_templates WHERE author_user_id=?", (user_id,))
    for table in USER_TABLES + ("rating_reasons", "live_catalog_state"):
        cur.execute(f"DELETE FROM {table} WHERE user_id=?", (user_id,))
    cur.execute("DELETE FROM plans WHERE user_id=?", (user_id,))
    hid = cur.execute("SELECT household_id FROM users WHERE id=?", (user_id,)).fetchone()["household_id"]
    if hid and people:
        for person in _household_people(cur, hid, user_id, "id, prefs"):
            cur.execute("DELETE FROM users WHERE id=?", (person["id"],))
    cur.execute("DELETE FROM users WHERE id=?", (user_id,))
    if hid and not cur.execute("SELECT 1 FROM users WHERE household_id=?", (hid,)).fetchone():
        cur.execute("DELETE FROM households WHERE id=?", (hid,))
