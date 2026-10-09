"""Swiggy sign-in + read-only tool discovery (rollout gate 1).

Implements the documented connection path from docs/vendor/swiggy/README.md:

  1. OAuth discovery  GET  {base}/.well-known/oauth-authorization-server
  2. Dynamic client registration  POST registration_endpoint (public client, PKCE)
  3. Authorization  →  Swiggy's own sign-in page (phone/OTP happen there, not here)
     with PKCE S256, `state`, scope `mcp:tools` and the MCP `resource`
  4. Token exchange  POST token_endpoint  (tokens last ~5 days; refresh isn't issued)
  5. MCP over Streamable HTTP at {base}/food: `initialize` → `notifications/initialized`
     → `tools/list`, accepting JSON or SSE responses and carrying `Mcp-Session-Id`.

What it deliberately does NOT do: call any tool. Carts, payment and placement stay
off until the discovered schemas are reviewed and the next gate is built. Tools are
classified read vs write from `annotations.readOnlyHint` when the server gives it,
else conservatively from the name.

Status: written against Swiggy's public docs and exercised against a fake server in
tests/test_followups.py. It has NOT been run against mcp.swiggy.com (unreachable from
the build environment); the first real sign-in is the verification step.

The access token is kept server-side, encrypted at rest (vault.py, keyed by
SMARTPLATE_SECRET), never returned by the API, and deleted on disconnect.
"""
import base64
import datetime as dt
import hashlib
import hmac
import json
import secrets
import urllib.error
import urllib.parse
import urllib.request

from .. import clock, config, db, vault

SCHEMA = """
CREATE TABLE IF NOT EXISTS swiggy_clients (      -- dynamic client registration, per redirect URI
    redirect_uri TEXT PRIMARY KEY,
    client_id TEXT NOT NULL,
    metadata TEXT NOT NULL,
    created_ts TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS swiggy_pending (      -- in-flight sign-ins (single-use state)
    state TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    verifier TEXT NOT NULL,
    redirect_uri TEXT NOT NULL,
    created_ts TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS swiggy_connections (
    user_id INTEGER PRIMARY KEY,
    access_token TEXT NOT NULL,                  -- sealed by vault.py; never returned by the API
    expires_ts TEXT,
    protocol_version TEXT,
    server_info TEXT NOT NULL DEFAULT '{}',
    tools TEXT NOT NULL DEFAULT '[]',
    connected_ts TEXT NOT NULL,
    discovered_ts TEXT
);
CREATE TABLE IF NOT EXISTS swiggy_menus (        -- live menus, cached briefly (swiggy_live.py)
    user_id INTEGER NOT NULL,
    restaurant TEXT NOT NULL,                    -- the SmartPlate restaurant name searched for
    payload TEXT NOT NULL,
    fetched_ts TEXT NOT NULL,
    PRIMARY KEY (user_id, restaurant)
);
CREATE TABLE IF NOT EXISTS swiggy_photos (       -- Swiggy's own dish photo, seen in a search_menu reply
    user_id INTEGER NOT NULL,
    restaurant_id TEXT NOT NULL,                 -- Swiggy restaurant id
    dish TEXT NOT NULL,                          -- normalised dish name (browse and search ids can differ)
    image TEXT,                                  -- NULL: Swiggy showed no photo when last checked
    checked_ts TEXT NOT NULL,
    PRIMARY KEY (user_id, restaurant_id, dish)
);
CREATE TABLE IF NOT EXISTS swiggy_issues (       -- recent problems and the rule behind each (swiggy_rules.py)
    user_id INTEGER NOT NULL,
    ts TEXT NOT NULL,
    rule TEXT NOT NULL,
    message TEXT NOT NULL,
    PRIMARY KEY (user_id, ts)
);
CREATE TABLE IF NOT EXISTS swiggy_favourites (
    user_id INTEGER NOT NULL,
    address_id TEXT NOT NULL,
    restaurant_id TEXT NOT NULL,
    restaurant_name TEXT NOT NULL,
    PRIMARY KEY (user_id, address_id, restaurant_id)
);
CREATE TABLE IF NOT EXISTS swiggy_order_attempts (
    user_id INTEGER NOT NULL,
    fingerprint TEXT NOT NULL,
    address_id TEXT NOT NULL,
    state TEXT NOT NULL,
    order_id TEXT,
    created_ts TEXT NOT NULL,
    PRIMARY KEY (user_id, fingerprint)
);
CREATE TABLE IF NOT EXISTS swiggy_checkout_quotes (
    token TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    cart_fingerprint TEXT NOT NULL,
    created_ts TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS swiggy_cart_intents (
    user_id INTEGER PRIMARY KEY,
    address_id TEXT NOT NULL,
    restaurant_id TEXT NOT NULL,
    restaurant_name TEXT NOT NULL,
    item_id TEXT NOT NULL
);
"""
COLUMNS = [                                      # added after gate 1 shipped
    ("swiggy_connections", "address_id", "TEXT"),
    ("swiggy_connections", "address_label", "TEXT"),
    ("swiggy_connections", "samples", "TEXT NOT NULL DEFAULT '{}'"),
    ("swiggy_order_attempts", "cart_fingerprint", "TEXT"),
    ("swiggy_pending", "browser_hash", "TEXT"),       # binds the callback to the browser that started it
    ("swiggy_order_attempts", "resolved_ts", "TEXT"),
    ("swiggy_order_attempts", "to_pay", "REAL"),
    ("swiggy_cart_intents", "menu_price", "REAL"),
    ("swiggy_cart_intents", "quantity", "INTEGER"),          # portions for a household meal; NULL = 1
]
CLIENT_VERSION = "2025-06-18"          # protocol we offer; the server's reply is what we record
PENDING_TTL = dt.timedelta(minutes=15)
READ_PREFIXES = ("get_", "search_", "fetch_", "track_", "list_")


class SwiggyError(RuntimeError):
    """A connection step failed; the message is safe to show the user."""

    def __init__(self, message, *, code="swiggy_error", retry_after=None, http_status=None):
        super().__init__(message)
        self.code, self.retry_after, self.http_status = code, retry_after, http_status


NOT_CONNECTED_CODES = frozenset({"swiggy_not_connected", "swiggy_auth_expired"})


class SwiggyNotConnected(SwiggyError):
    """There is no usable Swiggy sign-in for this user: never connected, expired, rejected
    by Swiggy, or unreadable on this server. The fix is the user's (Connect again), not an
    upstream outage, so the API answers 409 with a Connect action rather than 502."""

    def __init__(self, message="Connect your Swiggy account first (More → Swiggy connection).", *,
                 code="swiggy_not_connected", http_status=None):
        super().__init__(message, code=code, http_status=http_status)


def init_schema() -> None:
    with db.cursor() as cur:
        cur.executescript(SCHEMA)
        for table, column, decl in COLUMNS:
            if column not in {r["name"] for r in cur.execute(f"PRAGMA table_info({table})")}:
                cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


# --------------------------------------------------------------------------- #
# HTTP seam (tests replace `_http`)
# --------------------------------------------------------------------------- #
def _http(method: str, url: str, headers: dict, body: bytes | None) -> tuple[int, dict, bytes]:
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=config.SWIGGY_TIMEOUT_S) as r:
            return r.status, {k.lower(): v for k, v in r.headers.items()}, r.read()
    except urllib.error.HTTPError as e:
        return e.code, {k.lower(): v for k, v in (e.headers or {}).items()}, e.read() or b""
    except (urllib.error.URLError, OSError) as e:
        raise SwiggyError(f"Couldn't reach Swiggy ({getattr(e, 'reason', e)}). Check the connection and try again.")


def _json(method, url, payload=None, *, form=False, token=None, extra=None):
    headers = {"Accept": "application/json"}
    body = None
    if payload is not None:
        if form:
            body = urllib.parse.urlencode(payload).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        else:
            body = json.dumps(payload).encode()
            headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    headers.update(extra or {})
    status, h, raw = _http(method, url, headers, body)
    return status, h, raw


def _parse_rpc(headers: dict, raw: bytes, want_id) -> dict:
    """A JSON-RPC reply from Streamable HTTP: plain JSON, or an SSE stream of events."""
    ctype = headers.get("content-type", "")
    text = raw.decode("utf-8", "replace")
    messages = []
    if "text/event-stream" in ctype:
        for block in text.replace("\r\n", "\n").split("\n\n"):
            data = "\n".join(line[5:].lstrip() for line in block.split("\n") if line.startswith("data:"))
            if data:
                messages.append(json.loads(data))
    elif text.strip():
        parsed = json.loads(text)
        messages = parsed if isinstance(parsed, list) else [parsed]
    for m in messages:
        if isinstance(m, dict) and m.get("id") == want_id:
            if "error" in m:
                raise SwiggyError(f"Swiggy returned an error: {m['error'].get('message', 'unknown')}")
            return m.get("result") or {}
    raise SwiggyError("Swiggy's reply didn't include a result")


# --------------------------------------------------------------------------- #
# OAuth 2.1 + PKCE
# --------------------------------------------------------------------------- #
def _metadata() -> dict:
    status, _, raw = _json("GET", f"{config.SWIGGY_MCP_BASE}/.well-known/oauth-authorization-server")
    if status != 200:
        raise SwiggyError("Swiggy's sign-in service didn't respond as documented")
    meta = json.loads(raw)
    for key in ("authorization_endpoint", "token_endpoint"):
        if key not in meta:
            raise SwiggyError("Swiggy's sign-in metadata is missing required endpoints")
    if "S256" not in meta.get("code_challenge_methods_supported", ["S256"]):
        raise SwiggyError("Swiggy's sign-in doesn't offer PKCE S256")
    return meta


def redirect_allowed(redirect_uri: str) -> bool:
    """Only the configured public host, explicitly allowed hosts, or localhost (M-04)."""
    parsed = urllib.parse.urlsplit(redirect_uri)
    host = (parsed.hostname or "").lower()
    if parsed.path != config.SWIGGY_CALLBACK_PATH or parsed.query or parsed.fragment:
        return False
    if host in ("localhost", "127.0.0.1"):
        return True
    if parsed.scheme != "https":
        return False
    allowed = set(config.ALLOWED_HOSTS)
    if config.PUBLIC_URL:
        allowed.add((urllib.parse.urlsplit(config.PUBLIC_URL).hostname or "").lower())
    return host in allowed


def _client_id(meta: dict, redirect_uri: str) -> str:
    with db.cursor() as cur:
        row = cur.execute("SELECT client_id FROM swiggy_clients WHERE redirect_uri=?", (redirect_uri,)).fetchone()
        registered = cur.execute("SELECT COUNT(*) FROM swiggy_clients").fetchone()[0]
    if row:
        return row["client_id"]
    if not redirect_allowed(redirect_uri):
        raise SwiggyError("This server's public address isn't configured for Swiggy sign-in. "
                          "Set SMARTPLATE_PUBLIC_URL (or SMARTPLATE_ALLOWED_HOSTS) to the app's HTTPS address.")
    if registered >= config.SWIGGY_MAX_CLIENTS:
        raise SwiggyError("Ziggy has already registered its Swiggy sign-in addresses. "
                          "Ask the operator to check SMARTPLATE_PUBLIC_URL.")
    reg = meta.get("registration_endpoint")
    if not reg:
        raise SwiggyError("Swiggy requires a pre-registered client for this address. "
                          "Register the redirect URI with Swiggy Builders Club first.")
    status, _, raw = _json("POST", reg, {
        "client_name": "SmartPlate", "redirect_uris": [redirect_uri],
        "grant_types": ["authorization_code"], "response_types": ["code"],
        "token_endpoint_auth_method": "none", "scope": "mcp:tools"})
    if status not in (200, 201):
        raise SwiggyError(f"Swiggy refused to register this app for {redirect_uri} (HTTP {status}). "
                          "It may need to be allow-listed by Swiggy.")
    data = json.loads(raw)
    with db.cursor() as cur:
        cur.execute("INSERT OR REPLACE INTO swiggy_clients(redirect_uri, client_id, metadata, created_ts) "
                    "VALUES (?,?,?,?)", (redirect_uri, data["client_id"], db.jd(data), clock.now().isoformat()))
    return data["client_id"]


def pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def private_owner(user_id: int) -> bool:
    with db.cursor() as cur:
        row = cur.execute("SELECT access_hash FROM users WHERE id=?", (user_id,)).fetchone()
    return bool(row and row["access_hash"])


def browser_digest(nonce: str) -> str:
    return hashlib.sha256(("swiggy-oauth:" + nonce).encode()).hexdigest()


def start(user_id: int, redirect_uri: str, browser_nonce: str | None = None) -> str:
    """Begin sign-in; returns the Swiggy authorization URL to send the browser to.

    `browser_nonce` is also set as an HttpOnly cookie on the browser that asked;
    the callback is accepted only from a browser presenting it (H-01)."""
    if not private_owner(user_id):
        raise SwiggyError("Create or sign in to your own private Ziggy profile before connecting Swiggy. "
                          "Sample profiles are shared by every visitor.")
    if not redirect_uri.startswith("https://") and "://localhost" not in redirect_uri \
            and "://127.0.0.1" not in redirect_uri:
        raise SwiggyError("Swiggy sign-in needs HTTPS (or localhost for development)")
    if not redirect_allowed(redirect_uri):
        raise SwiggyError("This server's public address isn't configured for Swiggy sign-in. "
                          "Set SMARTPLATE_PUBLIC_URL (or SMARTPLATE_ALLOWED_HOSTS) to the app's HTTPS address.")
    init_schema()
    meta = _metadata()
    client_id = _client_id(meta, redirect_uri)
    verifier, challenge = pkce_pair()
    state = secrets.token_urlsafe(24)
    with db.cursor() as cur:
        cur.execute("DELETE FROM swiggy_pending WHERE created_ts < ?",
                    ((clock.now() - PENDING_TTL).isoformat(),))
        cur.execute("INSERT INTO swiggy_pending(state, user_id, verifier, redirect_uri, created_ts, browser_hash) "
                    "VALUES (?,?,?,?,?,?)", (state, user_id, verifier, redirect_uri, clock.now().isoformat(),
                                            browser_digest(browser_nonce) if browser_nonce else None))
    q = urllib.parse.urlencode({
        "response_type": "code", "client_id": client_id, "redirect_uri": redirect_uri,
        "code_challenge": challenge, "code_challenge_method": "S256", "state": state,
        "scope": "mcp:tools", "resource": f"{config.SWIGGY_MCP_BASE}/food"})
    return f"{meta['authorization_endpoint']}?{q}"


def finish(state: str, code: str, browser_nonce: str | None = None) -> int:
    """OAuth callback: validate state and the starting browser, exchange the code, run
    discovery. Returns user id. A discovery failure after the token is saved is not a
    sign-in failure: the connection stays and tools can be refreshed later (L-08)."""
    init_schema()
    with db.cursor() as cur:
        row = cur.execute("SELECT * FROM swiggy_pending WHERE state=?", (state or "",)).fetchone()
        cur.execute("DELETE FROM swiggy_pending WHERE state=?", (state or "",))       # single use
    if not row or dt.datetime.fromisoformat(row["created_ts"]) < clock.now() - PENDING_TTL:
        raise SwiggyError("This sign-in link expired or was already used. Start again from Ziggy.")
    expected = row["browser_hash"]
    if not expected or not browser_nonce or not hmac.compare_digest(expected, browser_digest(browser_nonce)):
        raise SwiggyError("This Swiggy sign-in was started in a different browser. Open Ziggy on this "
                          "device and tap Connect Swiggy again.", code="swiggy_browser_mismatch")
    if not code:
        raise SwiggyError("Swiggy didn't return a sign-in code")
    if not private_owner(row["user_id"]):
        raise SwiggyError("This sign-in belongs to a shared profile. Start again from your own private profile.")
    meta = _metadata()
    status, _, raw = _json("POST", meta["token_endpoint"], {
        "grant_type": "authorization_code", "code": code, "redirect_uri": row["redirect_uri"],
        "client_id": _client_id(meta, row["redirect_uri"]), "code_verifier": row["verifier"],
        "resource": f"{config.SWIGGY_MCP_BASE}/food"}, form=True)
    if status != 200:
        raise SwiggyError(f"Swiggy didn't accept the sign-in (HTTP {status}). Try again.")
    try:
        tok = json.loads(raw)
    except ValueError:
        raise SwiggyError("Swiggy's sign-in reply couldn't be read. Try again.") from None
    if not isinstance(tok, dict) or not tok.get("access_token"):
        raise SwiggyError("Swiggy's reply had no access token")
    try:
        lifetime = int(tok["expires_in"]) if tok.get("expires_in") is not None else None
    except (TypeError, ValueError):
        lifetime = None                                  # unknown lifetime: Swiggy's 401 will tell us
    if lifetime is not None and not 0 < lifetime <= 90 * 86400:
        lifetime = None
    expires = (clock.now() + dt.timedelta(seconds=lifetime)).isoformat() if lifetime else None
    with db.cursor() as cur:
        cur.execute("INSERT INTO swiggy_connections(user_id, access_token, expires_ts, connected_ts) VALUES (?,?,?,?) "
                    "ON CONFLICT(user_id) DO UPDATE SET access_token=excluded.access_token, "
                    "expires_ts=excluded.expires_ts, connected_ts=excluded.connected_ts, "
                    "address_id=NULL, address_label=NULL, samples='{}'", (row["user_id"], vault.seal(tok["access_token"]), expires, clock.now().isoformat()))
        # Reconnection may sign into a different Swiggy account. Re-select an
        # address and review a cart instead of carrying over old account state.
        cur.execute("DELETE FROM swiggy_menus WHERE user_id=?", (row["user_id"],))
        cur.execute("DELETE FROM swiggy_checkout_quotes WHERE user_id=?", (row["user_id"],))
        cur.execute("DELETE FROM swiggy_cart_intents WHERE user_id=?", (row["user_id"],))
    from . import swiggy_live
    swiggy_live.forget_session(row["user_id"])     # a new sign-in never reuses old sessions or addresses
    try:
        discover(row["user_id"])
    except SwiggyError:
        import logging
        logging.getLogger(__name__).warning("Swiggy connected but tool discovery failed; user can refresh tools")
    return row["user_id"]


# --------------------------------------------------------------------------- #
# MCP discovery (read-only)
# --------------------------------------------------------------------------- #
def _rpc(token, method, params, rid, session_id=None):
    extra = {"Accept": "application/json, text/event-stream"}
    if session_id:
        extra["Mcp-Session-Id"] = session_id
    payload = {"jsonrpc": "2.0", "method": method, "params": params}
    if rid is not None:
        payload["id"] = rid
    status, headers, raw = _json("POST", f"{config.SWIGGY_MCP_BASE}/food", payload, token=token, extra=extra)
    if status == 401:
        raise SwiggyNotConnected("Your Swiggy sign-in has expired. Connect again.", code="swiggy_auth_expired",
                                 http_status=401)
    if status == 429:
        wait = headers.get("retry-after", "")
        wait = min(int(wait), 86400) if str(wait).isdigit() else None
        raise SwiggyError("Swiggy is limiting requests. Wait before trying again; an order is never retried automatically.",
                          code="swiggy_rate_limited", retry_after=wait, http_status=429)
    if status >= 400:
        raise SwiggyError(f"Swiggy's MCP server returned HTTP {status}", http_status=status)
    return headers, (_parse_rpc(headers, raw, rid) if rid is not None else None)


def user_rpc(user_id, token, *args):
    """Persist a provider-rejected sign-in without retrying a purchase."""
    try:
        return _rpc(token, *args)
    except SwiggyError as error:
        if error.code == "swiggy_auth_expired":
            with db.cursor() as cur:
                cur.execute("UPDATE swiggy_connections SET expires_ts=? WHERE user_id=?",
                            (clock.now().isoformat(), user_id))
        raise


def classify(tool: dict) -> str:
    hint = (tool.get("annotations") or {}).get("readOnlyHint")
    if hint is not None:
        return "read" if hint else "write"
    return "read" if tool.get("name", "").startswith(READ_PREFIXES) else "write"


def discover(user_id: int) -> dict:
    conn = _connection(user_id)
    if not conn:
        raise SwiggyNotConnected()
    if not conn["access_token"]:
        raise SwiggyNotConnected("Your Swiggy sign-in can no longer be read on this server. Connect again.")
    headers, init = user_rpc(user_id, conn["access_token"], "initialize", {
        "protocolVersion": CLIENT_VERSION, "capabilities": {},
        "clientInfo": {"name": "SmartPlate", "version": "1.1.0"}}, 1)
    session_id = headers.get("mcp-session-id")
    user_rpc(user_id, conn["access_token"], "notifications/initialized", {}, None, session_id)
    tools, cursor, rid = [], None, 2
    while True:
        _, page = user_rpc(user_id, conn["access_token"], "tools/list", {"cursor": cursor} if cursor else {}, rid, session_id)
        tools += page.get("tools", [])
        cursor, rid = page.get("nextCursor"), rid + 1
        if not cursor or rid > 20:
            break
    summary = [{"name": t.get("name"), "description": (t.get("description") or "")[:300], "kind": classify(t),
                "input_schema": t.get("inputSchema") or {}} for t in tools]
    with db.cursor() as cur:
        cur.execute("UPDATE swiggy_connections SET protocol_version=?, server_info=?, tools=?, discovered_ts=? "
                    "WHERE user_id=?", (init.get("protocolVersion"), db.jd(init.get("serverInfo") or {}),
                                        db.jd(summary), clock.now().isoformat(), user_id))
    return status(user_id)


def _connection(user_id: int) -> dict | None:
    if not private_owner(user_id):
        return None
    init_schema()
    with db.cursor() as cur:
        row = cur.execute("SELECT * FROM swiggy_connections WHERE user_id=?", (user_id,)).fetchone()
    if not row:
        return None
    conn = db.row_to_dict(row)
    conn["access_token"] = vault.unseal(conn["access_token"])      # None if the server key changed
    return conn


def status(user_id: int) -> dict:
    """What the UI may see — never the token."""
    conn = _connection(user_id)
    if not conn:
        return {"connected": False, "requires_private_profile": not private_owner(user_id)}
    expired = bool(conn["expires_ts"]) and dt.datetime.fromisoformat(conn["expires_ts"]) <= clock.now()
    tools = db.jl(conn["tools"])
    # A changed/missing vault key makes a stored token unreadable even before its
    # advertised expiry. Never present that state as an active connection.
    token_unreadable = not bool(conn["access_token"])
    return {"connected": not expired and not token_unreadable, "expired": expired,
            "needs_reconnect": token_unreadable, "expires": conn["expires_ts"],
            "connected_at": conn["connected_ts"], "discovered_at": conn["discovered_ts"],
            "protocol_version": conn["protocol_version"], "server": db.jl(conn["server_info"], {}),
            "tools": [{k: t[k] for k in ("name", "description", "kind")} for t in tools],
            "read_tools": sum(t["kind"] == "read" for t in tools),
            "write_tools": sum(t["kind"] == "write" for t in tools),
            "address": {"id": conn["address_id"], "label": conn["address_label"]} if conn.get("address_id") else None}


def disconnect(user_id: int) -> dict:
    from . import swiggy_live
    swiggy_live.forget_session(user_id)
    init_schema()
    with db.cursor() as cur:
        cur.execute("DELETE FROM swiggy_connections WHERE user_id=?", (user_id,))
        cur.execute("DELETE FROM swiggy_pending WHERE user_id=?", (user_id,))
        cur.execute("DELETE FROM swiggy_menus WHERE user_id=?", (user_id,))
        cur.execute("DELETE FROM swiggy_photos WHERE user_id=?", (user_id,))
        cur.execute("DELETE FROM swiggy_issues WHERE user_id=?", (user_id,))
        cur.execute("DELETE FROM swiggy_checkout_quotes WHERE user_id=?", (user_id,))
        cur.execute("DELETE FROM swiggy_cart_intents WHERE user_id=?", (user_id,))
    return {"connected": False}
