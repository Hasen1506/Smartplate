"""Runtime configuration.

Every knob here has a safe default so the app runs with zero setup. The brain is
always the *deterministic* one, so v1.1 incurs no per-decision LLM cost (see
FEASIBILITY.md §2). Planner tuning values are pinned constants, not env knobs.
"""
import os

# Persistence
# DATABASE_URL (a postgres:// URL, e.g. Neon's free tier) makes Postgres the store: data
# then lives outside the app server and survives restarts and redeploys. Unset, the app
# uses the SQLite file at SMARTPLATE_DB (local runs, tests, Codespaces).
def clean_database_url(raw: str) -> str:
    """The URL without whitespace or invisible characters. A URL never contains them, but a
    connection string copied from a web page can (an Oct 2026 paste carried four U+202F
    narrow no-break spaces inside the Neon hostname, so DNS failed). Remove them."""
    import unicodedata
    return "".join(ch for ch in raw if not ch.isspace() and unicodedata.category(ch) not in ("Cf", "Zs"))


_RAW_DATABASE_URL = os.environ.get("DATABASE_URL", "")
DATABASE_URL = clean_database_url(_RAW_DATABASE_URL)
DATABASE_URL_CLEANED = DATABASE_URL != _RAW_DATABASE_URL.strip()   # logged once at startup
# Postgres schema to use (default "public"); the test-suite gives every test its own.
PG_SCHEMA = os.environ.get("SMARTPLATE_PG_SCHEMA", "").strip()
# Connection pool: gunicorn runs one worker with 8 threads, so up to 8 connections.
PG_POOL_MAX = int(os.environ.get("SMARTPLATE_PG_POOL_MAX", "8"))
# Seconds a request waits for a pooled connection before the pool is replaced once and,
# if that fails too, the request answers 503 "database unavailable" (never a 30 s hang).
# 5 s: a healthy pooled checkout takes milliseconds, and a pool that cannot hand out a
# connection is detected and replaced after this wait (Oct 2026: 10 s made the
# first request after an idle spell take ~10 s).
PG_POOL_TIMEOUT = float(os.environ.get("SMARTPLATE_PG_POOL_TIMEOUT", "5"))
# Background pool keep-alive (db_pg.start_keepalive): on by default on Render.
PG_KEEPALIVE = os.environ.get("SMARTPLATE_PG_KEEPALIVE", "on" if os.environ.get("RENDER") else "off") == "on"
DB_PATH = os.environ.get("SMARTPLATE_DB", "smartplate.db")
# Where a persistent disk is mounted on Render (render.production.yaml). Anything else
# on Render lives on the instance's ephemeral disk and is erased on every spin-down,
# restart or redeploy (https://render.com/docs/free).
RENDER_DISK_MOUNT = os.environ.get("SMARTPLATE_DISK_MOUNT", "/var/data")
# Optional explicit declaration for other hosts: "1" (the DB path survives restarts)
# or "0" (it does not). Unset off Render means "unknown".
DB_PERSISTENT = os.environ.get("SMARTPLATE_DB_PERSISTENT", "")

# Where the app runs. Production is any Render service or any database that is not on this
# machine; there, fixture data can never be switched on.
def _production() -> bool:
    if os.environ.get("RENDER") or os.environ.get("SMARTPLATE_ENV", "").lower() == "production":
        return True
    if DATABASE_URL:
        from urllib.parse import urlsplit
        try:
            host = (urlsplit(DATABASE_URL).hostname or "").lower()
        except ValueError:
            return True
        return host not in ("localhost", "127.0.0.1", "::1", "")
    return False


PRODUCTION = _production()
# No fake data anywhere a person can see it (Oct 2026 rule). The sample Chennai catalogue,
# sample profiles, sample weather and surge history exist only for the test-suite, behind
# SMARTPLATE_FIXTURE_DATA=1, which is ignored in production.
FIXTURE_DATA = os.environ.get("SMARTPLATE_FIXTURE_DATA") == "1" and not PRODUCTION

# Health-condition filters (diabetes, blood pressure, celiac) in onboarding and settings
# (sp-grill #41: health claims). "off" archives them for the consumer beta: new conditions are
# refused with a plain message, while conditions people already saved keep being enforced
# (fail-closed) until they remove them. Default "on" until the founder decides.
MEDICAL_FILTERS = os.environ.get("SMARTPLATE_MEDICAL_FILTERS", "on").lower() != "off"

# Brain: always the deterministic one (solver + templates, no per-decision LLM cost). The
# SMARTPLATE_BRAIN=llm experiment (off since June, no tests) is archived in
# smartplate/archive/experiments.py (founder decision, 14 Oct 2026).
AGENT_BRAIN = "deterministic"

# Legacy switch for the old simulated order saga only (swiggy_mcp.py, test runs with fixture
# data). It never governed the real Swiggy sign-in, menus or carts, so it is NOT what the app
# reports: /api/meta reports swiggy_mode() below (grill #34).
SWIGGY_PROVIDER = os.environ.get("SMARTPLATE_SWIGGY", "simulated")

# One Swiggy mode, reported truthfully by /api/meta (grill #34, Oct 2026):
#   demo       : this server never talks to Swiggy (no sign-in, no tool calls).
#   live_read  : sign-in, addresses, live menus, search and order history; no cart writes.
#   live_cart  : live_read + filling the user's own Swiggy cart after they review it
#                (checkout and payment happen in Swiggy). The default.
#   live_order : live_cart + placing an order the user explicitly approved in Ziggy.
# SMARTPLATE_SWIGGY_MODE picks one; unset, it is live_cart, or live_order when the older
# SMARTPLATE_LIVE_ORDERS=on is set.
SWIGGY_MODES = ("demo", "live_read", "live_cart", "live_order")
SWIGGY_MODE = os.environ.get("SMARTPLATE_SWIGGY_MODE", "").strip().lower()
if SWIGGY_MODE and SWIGGY_MODE not in SWIGGY_MODES:
    raise ValueError(f"SMARTPLATE_SWIGGY_MODE must be one of {', '.join(SWIGGY_MODES)}")
# Real Food orders are non-idempotent. Enable only after provider approval,
# staging validation and durable storage; the public Render Free demo stays off.
LIVE_ORDERS = (SWIGGY_MODE == "live_order") if SWIGGY_MODE else \
    os.environ.get("SMARTPLATE_LIVE_ORDERS", "off") == "on"


def swiggy_mode() -> str:
    """The Swiggy mode this server runs in (read at call time, so tests can switch it)."""
    if LIVE_ORDERS:
        return "live_order"
    if SWIGGY_MODE in ("demo", "live_read", "live_cart"):
        return SWIGGY_MODE
    return "live_cart"


def swiggy_allows(mode: str) -> bool:
    """True when the current mode includes `mode` (each mode includes the ones before it)."""
    return SWIGGY_MODES.index(swiggy_mode()) >= SWIGGY_MODES.index(mode)

# Planner tuning, pinned as constants (founder decision 14 Oct 2026: nobody tunes these per
# deploy, so the SMARTPLATE_SOLVER_* / _STABILITY / _PROTEIN_EVEN / _EDIT_WINDOW env knobs
# are gone; tests still monkeypatch these module attributes).
# Solver limits: relative optimality gap and a hard time cap per weekly solve.
SOLVER_GAP = 0.001
SOLVER_TIME_LIMIT_S = 10.0
# Branch-and-bound node cap: a deterministic work limit, so a hard week returns the same
# plan however busy the server is (the time limit above is only a backstop).
SOLVER_MAX_NODES = 20000

# Plan stability: bonus for keeping a meal's current pick on a re-plan (0 disables).
STABILITY_W = 0.3

# Weather — "live" (Open-Meteo forecast, cached; falls back to the sample feed when
# offline) or "simulated" (sample feed only; used by the test-suite for determinism).
WEATHER_PROVIDER = os.environ.get("SMARTPLATE_WEATHER", "live")
WEATHER_TIMEOUT_S = float(os.environ.get("SMARTPLATE_WEATHER_TIMEOUT", "3"))

# Swiggy only lets sign-in redirect to exact callback URLs it has allow-listed. The ones
# Swiggy has whitelisted for this app are listed here (founder, Oct 2026: the Render origin
# is whitelisted and sign-in works); swiggy_redirect_approved() is true when this
# deployment's callback is one of them. SMARTPLATE_SWIGGY_REDIRECT_APPROVED=1 marks any
# other deployment approved (a new domain Swiggy confirmed in writing).
APPROVED_SWIGGY_REDIRECTS = tuple(u.strip().rstrip("/") for u in os.environ.get(
    "SMARTPLATE_SWIGGY_APPROVED_REDIRECTS",
    "https://smartplate-xgxd.onrender.com/swiggy/callback").split(",") if u.strip())
SWIGGY_REDIRECT_APPROVED = os.environ.get("SMARTPLATE_SWIGGY_REDIRECT_APPROVED", "0") == "1"

# Swiggy sign-in + read-only discovery (docs/vendor/swiggy/README.md).
SWIGGY_MCP_BASE = os.environ.get("SMARTPLATE_SWIGGY_MCP", "https://mcp.swiggy.com").rstrip("/")
SWIGGY_TIMEOUT_S = float(os.environ.get("SMARTPLATE_SWIGGY_TIMEOUT", "10"))
# Public base URL for the OAuth redirect when a proxy hides it (else derived per request).
# Render sets RENDER_EXTERNAL_URL for every web service, so hosted installs need no setup.
PUBLIC_URL = (os.environ.get("SMARTPLATE_PUBLIC_URL") or os.environ.get("RENDER_EXTERNAL_URL") or "").rstrip("/")
# Hosts the Swiggy OAuth redirect may use besides PUBLIC_URL's host and localhost
# (comma-separated). Without PUBLIC_URL or this list, Swiggy sign-in only works on
# localhost: a Host / X-Forwarded-Host header is never trusted to pick it (M-04).
ALLOWED_HOSTS = tuple(h.strip().lower() for h in os.environ.get("SMARTPLATE_ALLOWED_HOSTS", "").split(",") if h.strip())
# At most this many dynamically registered Swiggy OAuth clients (one per redirect URI).
SWIGGY_MAX_CLIENTS = int(os.environ.get("SMARTPLATE_SWIGGY_MAX_CLIENTS", "3"))
# The registered redirect path must exactly match Swiggy's approval. A proposed
# URL in an application document is not proof that the host belongs to this app.
SWIGGY_CALLBACK_PATH = os.environ.get("SMARTPLATE_SWIGGY_CALLBACK_PATH", "/swiggy/callback")
if SWIGGY_CALLBACK_PATH not in ("/swiggy/callback", "/auth/swiggy/callback"):
    raise ValueError("SMARTPLATE_SWIGGY_CALLBACK_PATH must be a supported callback route")


def swiggy_callback_url() -> str | None:
    """This deployment's Swiggy callback when its public URL is configured, else None."""
    return f"{PUBLIC_URL}{SWIGGY_CALLBACK_PATH}" if PUBLIC_URL else None


def swiggy_redirect_approved() -> bool:
    """Whether Swiggy accepts this deployment's sign-in redirect (derived, not hand-set)."""
    if swiggy_mode() == "demo":
        return False
    return SWIGGY_REDIRECT_APPROVED or swiggy_callback_url() in APPROVED_SWIGGY_REDIRECTS

# Server secret for encrypting stored tokens and signing browser sessions (grill #64).
# SMARTPLATE_SECRET (SMARTPLATE_VAULT_KEY is accepted as another name). render.yaml generates
# one. Production refuses to start without a strong one (secret_problem below): the key must
# live outside the data it protects. Off production a random key is kept in the database so a
# local trial needs zero setup. Rotation: put the old value in SMARTPLATE_SECRET_PREVIOUS
# (comma-separated) and a new one in SMARTPLATE_SECRET; old tokens still open and are re-sealed
# under the new key (docs/production-deployment.md, "The encryption key").
SECRET = (os.environ.get("SMARTPLATE_SECRET") or os.environ.get("SMARTPLATE_VAULT_KEY") or "").strip()
SECRET_PREVIOUS = tuple(v.strip() for v in os.environ.get("SMARTPLATE_SECRET_PREVIOUS", "").split(",") if v.strip())
MIN_SECRET_LENGTH = 32
# Values from examples, tutorials and old .env files. A secret equal to one of these is public.
DEV_SECRETS = frozenset({"dev", "devsecret", "dev-secret", "secret", "changeme", "change-me", "change_me",
                         "test", "testing", "password", "smartplate", "ziggy", "replace-me", "your-secret-here",
                         "insecure", "default", "local", "example"})


def secret_problem(secret: str | None = None) -> str | None:
    """Why the encryption secret is not fit for production, or None when it is."""
    value = SECRET if secret is None else secret
    if not value:
        return "SMARTPLATE_SECRET is not set"
    if value.lower() in DEV_SECRETS or len(set(value)) < 8:
        return "SMARTPLATE_SECRET is a well-known example value"
    if len(value) < MIN_SECRET_LENGTH:
        return f"SMARTPLATE_SECRET is shorter than {MIN_SECRET_LENGTH} characters"
    return None
# Privacy notice details only the founder can give (shown on /privacy and /terms; DPDP Rule 9
# requires a published contact who can answer questions about processing).
OPERATOR = os.environ.get("SMARTPLATE_OPERATOR", "").strip()            # legal name running Ziggy
PRIVACY_CONTACT = os.environ.get("SMARTPLATE_PRIVACY_CONTACT", "").strip()   # e.g. privacy@yourdomain.in
GRIEVANCE_NAME = os.environ.get("SMARTPLATE_GRIEVANCE_NAME", "").strip()     # the person who answers
BACKUP_WINDOW = os.environ.get("SMARTPLATE_BACKUP_WINDOW", "6 hours on Neon's free plan").strip()

# Behind one reverse proxy (Render, Codespaces): trust its X-Forwarded-* headers.
BEHIND_PROXY = os.environ.get("SMARTPLATE_BEHIND_PROXY", "1" if os.environ.get("RENDER") else "0") == "1"

# Web Push (order-time reminders). Keys are generated on first use when unset.
PUSH_CONTACT = os.environ.get("SMARTPLATE_PUSH_CONTACT", "mailto:smartplate@example.invalid")
# (No meal floor: the old SMARTPLATE_MEAL_FLOOR ₹60 estimate is gone, founder decision 14 Oct
# 2026. The only floor is the user's own cheapest favourite with its fees: spend.cheapest_meal.)
PUSH_TICK_S = float(os.environ.get("SMARTPLATE_PUSH_TICK", "60"))
PUSH_ENABLED = os.environ.get("SMARTPLATE_PUSH", "on") == "on"
VAPID_PRIVATE = os.environ.get("SMARTPLATE_VAPID_PRIVATE", "")   # base64url P-256 key; generated if unset

# Operations (docs/reliability.md). Each endpoint is OFF (404) until its key is set.
# CRON_KEY: an external scheduler calls /cron/tick every 5 min with it, so reminders are
# sent even while the free instance sleeps. ADMIN_KEY: opens /admin/metrics (retention).
CRON_KEY = os.environ.get("SMARTPLATE_CRON_KEY", "").strip()
ADMIN_KEY = os.environ.get("SMARTPLATE_ADMIN_KEY", "").strip()
# Logs: "json" (one JSON object per line, default on Render) or "text".
LOG_FORMAT = os.environ.get("SMARTPLATE_LOG_FORMAT", "json" if os.environ.get("RENDER") else "text").lower()
# Error tracking: set SENTRY_DSN to report errors (sentry-sdk[flask] is in requirements.txt); unset = off.
SENTRY_DSN = os.environ.get("SENTRY_DSN", "").strip()
SENTRY_TRACES = float(os.environ.get("SENTRY_TRACES_SAMPLE_RATE", "0") or 0)
# Requests slower than this many ms are logged at WARNING (all are logged at INFO).
SLOW_REQUEST_MS = int(os.environ.get("SMARTPLATE_SLOW_MS", "2000"))

# Default order-execution policy. Swiggy's cautious posture (§7.1) means we
# default to an editable window rather than silent auto-placement.
ORDER_EDIT_WINDOW_MIN = 30          # pinned (was SMARTPLATE_EDIT_WINDOW)

# Optimiser objective weights per mode. Survival cranks cost; comfort cranks taste.
MODE_WEIGHTS = {
    "comfort": {"cost": 0.15, "taste": 1.0, "nutrition": 0.4, "carbon": 0.1, "health": 0.4, "surge": 0.3},
    "balanced": {"cost": 0.5, "taste": 0.6, "nutrition": 0.6, "carbon": 0.3, "health": 0.6, "surge": 0.5},
    "survival": {"cost": 1.0, "taste": 0.25, "nutrition": 0.7, "carbon": 0.15, "health": 0.5, "surge": 0.8},
}

MODE_LABELS = {  # internal name -> softer UI label (§7.4)
    "comfort": "Comfort",
    "balanced": "Balanced",
    "survival": "Tight Week",
}

# A mode is not a bag of weights — it is a statement about *which variable is the
# objective and which is the constraint that may give* (docs/optimization-and-ux.md §2).
# `objective`  : what the solve is really chasing.
# `gives`      : what is allowed to bend so the objective can win.
# `nutri_tol`  : ± fraction of a meal's kcal target that counts as "on target"
#                (no penalty). Tight Week lets calories drift; Comfort holds them close.
# `outcome`    : the plain-language line to show the user — surface the behaviour,
#                not the weights (§8: transparency is the trust moat).
MODE_META = {
    "comfort":  {"objective": "nutrition+taste", "gives": "money",     "nutri_tol": 0.08,
                 "outcome": "The best week your budget allows — money's the only limit."},
    "balanced": {"objective": "both-on-target",  "gives": "neither",   "nutri_tol": 0.15,
                 "outcome": "Best taste-for-money that stays on your nutrition target."},
    "survival": {"objective": "cost",            "gives": "nutrition", "nutri_tol": 0.28,
                 "outcome": "The cheapest week that still hits your nutrition."},
}


def mode_meta(mode: str) -> dict:
    return MODE_META.get(mode, MODE_META["balanced"])


# Rating floor: always a hard gate (the brand promise "never below your floor"). The soft
# SMARTPLATE_RATING_FLOOR / SMARTPLATE_SAFETY_FLOOR experiment and the SMARTPLATE_VARIETY
# nudge (both off since June, untested) are archived in smartplate/archive/experiments.py.

# Protein evenness: a day-level penalty for backloading protein into one meal (the
# per-meal even target is daily_protein / meals-that-day). 0 disables. Pinned constant.
PROTEIN_EVEN_W = 0.25

# Usual-first / favourites first (docs §5.2), the default since 14 Oct 2026 (was the
# SMARTPLATE_USUAL_FIRST=on experiment): a dish the user has never ordered pays a small
# premium against the ones they order, so the planner keeps their usuals unless a swap buys
# real goal-fit. Applies only once there is order history (a new profile has no usuals).
USUAL_FIRST_W = 0.5

# Epicure ingredient embeddings (domain/epicure.py): downloaded at build/setup time by
# scripts/fetch_epicure.py. "pinned" checks every file against the SHA-256 sums in the
# code; tests point both settings at the small offline fixture in tests/fixtures/epicure.
EPICURE_DIR = os.environ.get("SMARTPLATE_EPICURE_DIR",
                             os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "epicure"))
EPICURE_CHECKSUMS = os.environ.get("SMARTPLATE_EPICURE_CHECKSUMS", "pinned")


def storage_status() -> dict:
    """Whether the database survives a restart or redeploy, and why we think so.

    persistent is True, False or None (unknown). It is decided from configuration only,
    so the answer is deterministic and needs no disk probing."""
    if DATABASE_URL:
        return {"engine": "postgres", "persistent": True,
                "reason": "external Postgres (DATABASE_URL): survives restarts and redeploys"}
    path = DB_PATH
    if path == ":memory:" or path.startswith("file::memory:"):
        return {"engine": "sqlite", "persistent": False, "reason": "in-memory database"}
    if DB_PERSISTENT in ("1", "0"):
        ok = DB_PERSISTENT == "1"
        return {"engine": "sqlite", "persistent": ok,
                "reason": "declared by SMARTPLATE_DB_PERSISTENT"}
    if os.environ.get("RENDER"):
        mount = RENDER_DISK_MOUNT.rstrip("/") + "/"
        if os.path.abspath(path).startswith(mount):
            return {"engine": "sqlite", "persistent": True, "reason": f"on the Render disk at {RENDER_DISK_MOUNT}"}
        return {"engine": "sqlite", "persistent": False,
                "reason": "on Render's temporary disk: erased on every spin-down, restart or redeploy"}
    return {"engine": "sqlite", "persistent": None, "reason": "host not recognised; persistence unknown"}
