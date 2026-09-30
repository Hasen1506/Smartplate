"""Export/delete a private profile's saved local data, excluding credentials."""
from . import clock, db

USER_TABLES = ("calendar_events", "leftovers", "receipts", "intake_log", "favourites", "ratings",
               "logins", "devices", "push_subscriptions", "swiggy_connections", "swiggy_pending",
               "swiggy_menus", "swiggy_favourites", "swiggy_order_attempts", "swiggy_checkout_quotes",
               "swiggy_cart_intents")
PRIVATE_COLUMNS = frozenset({"access_hash", "pw_hash", "token_hash", "access_token", "verifier",
                             "token", "fingerprint", "p256dh", "auth", "idempotency_key"})


def _private(cur, user_id):
    row = cur.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    if not row or not row["access_hash"]:
        raise ValueError("Data controls require your own private profile. Shared samples cannot be deleted.")
    return row


def _clean(rows):
    return [{k: row[k] for k in row.keys() if k not in PRIVATE_COLUMNS} for row in rows]


def export(user_id):
    """Return all saved profile/meal records, without authentication material."""
    with db.cursor() as cur:
        cur.execute("BEGIN")
        user = _private(cur, user_id)
        data = {"profile": _clean([user])[0]}
        for table in USER_TABLES:
            if table in ("swiggy_pending", "swiggy_checkout_quotes"):
                continue  # ephemeral authorization material, never exported
            data[table] = _clean(cur.execute(f"SELECT * FROM {table} WHERE user_id=?", (user_id,)).fetchall())
        plan_scope = "SELECT id FROM plans WHERE user_id=?"
        decision_scope = f"SELECT id FROM decisions WHERE plan_id IN ({plan_scope})"
        subscription_scope = "SELECT id FROM push_subscriptions WHERE user_id=?"
        for table in ("plans", "sessions", "decisions", "grocery_baskets"):
            scope = "user_id=?" if table == "plans" else f"plan_id IN ({plan_scope})"
            data[table] = _clean(cur.execute(f"SELECT * FROM {table} WHERE {scope}", (user_id,)).fetchall())
        data["orders"] = _clean(cur.execute(f"SELECT * FROM orders WHERE decision_id IN ({decision_scope})", (user_id,)).fetchall())
        data["push_sent"] = _clean(cur.execute(f"SELECT * FROM push_sent WHERE subscription_id IN ({subscription_scope})", (user_id,)).fetchall())
        data["community_templates"] = _clean(cur.execute("SELECT * FROM community_templates WHERE author_user_id=?", (user_id,)).fetchall())
    return {"format_version": 1, "exported_at": clock.now().isoformat(), "data": data,
            "notes": ["Authentication secrets are excluded.", "Demo weekly plans and Swiggy order attempts are separate records.",
                      "This exports local records; Swiggy maintains its own account and order history."]}


def delete(user_id, confirmation):
    if confirmation != "DELETE":
        raise ValueError("Type DELETE to confirm permanent removal of this SmartPlate profile.")
    with db.cursor() as cur:
        cur.execute("BEGIN IMMEDIATE")
        _private(cur, user_id)
        plan_scope = "SELECT id FROM plans WHERE user_id=?"
        decision_scope = f"SELECT id FROM decisions WHERE plan_id IN ({plan_scope})"
        subscription_scope = "SELECT id FROM push_subscriptions WHERE user_id=?"
        cur.execute(f"DELETE FROM orders WHERE decision_id IN ({decision_scope})", (user_id,))
        cur.execute(f"DELETE FROM push_sent WHERE subscription_id IN ({subscription_scope})", (user_id,))
        for table in ("grocery_baskets", "decisions", "sessions"):
            cur.execute(f"DELETE FROM {table} WHERE plan_id IN ({plan_scope})", (user_id,))
        cur.execute("DELETE FROM community_templates WHERE author_user_id=?", (user_id,))
        for table in USER_TABLES:
            cur.execute(f"DELETE FROM {table} WHERE user_id=?", (user_id,))
        cur.execute("DELETE FROM plans WHERE user_id=?", (user_id,))
        cur.execute("DELETE FROM users WHERE id=?", (user_id,))
    return {"deleted": True}
