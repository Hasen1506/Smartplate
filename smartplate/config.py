"""Runtime configuration.

Every knob here has a safe default so the app runs with zero setup. The single
most important flag is AGENT_BRAIN: it defaults to the *deterministic* brain so
v1.1 incurs no per-decision LLM cost (see FEASIBILITY.md §2).
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
# 5 s: a healthy pooled checkout takes milliseconds, and a pool stuck on connections the
# Neon pooler dropped is detected and replaced after this wait (Oct 2026: 10 s made the
# first request after an idle spell take ~10 s).
PG_POOL_TIMEOUT = float(os.environ.get("SMARTPLATE_PG_POOL_TIMEOUT", "5"))
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

# Brain selection — "deterministic" (free, default) or "llm" (optional, paid).
AGENT_BRAIN = os.environ.get("SMARTPLATE_BRAIN", "deterministic")

# Swiggy MCP provider — "simulated" (default) or "live" (requires real access).
SWIGGY_PROVIDER = os.environ.get("SMARTPLATE_SWIGGY", "simulated")
# Real Food orders are non-idempotent. Enable only after provider approval,
# staging validation and durable storage; the public Render Free demo stays off.
LIVE_ORDERS = os.environ.get("SMARTPLATE_LIVE_ORDERS", "off") == "on"

# Solver limits: relative optimality gap and a hard time cap per weekly solve.
SOLVER_GAP = float(os.environ.get("SMARTPLATE_SOLVER_GAP", "0.001"))
SOLVER_TIME_LIMIT_S = float(os.environ.get("SMARTPLATE_SOLVER_TIME_LIMIT", "10"))
# Branch-and-bound node cap: a deterministic work limit, so a hard week returns the same
# plan however busy the server is (the time limit above is only a backstop).
SOLVER_MAX_NODES = int(os.environ.get("SMARTPLATE_SOLVER_MAX_NODES", "20000"))

# Plan stability: bonus for keeping a meal's current pick on a re-plan (0 disables).
STABILITY_W = float(os.environ.get("SMARTPLATE_STABILITY", "0.3"))

# Weather — "live" (Open-Meteo forecast, cached; falls back to the sample feed when
# offline) or "simulated" (sample feed only; used by the test-suite for determinism).
WEATHER_PROVIDER = os.environ.get("SMARTPLATE_WEATHER", "live")
WEATHER_TIMEOUT_S = float(os.environ.get("SMARTPLATE_WEATHER_TIMEOUT", "3"))

# Swiggy only lets sign-in redirect to exact callback URLs it has allow-listed. Set to
# "1" once Swiggy has approved THIS deployment's callback (More -> Swiggy connection
# shows it). Until then the UI says plainly that connecting Swiggy is not yet possible.
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

# Server secret for encrypting stored tokens. render.yaml generates one; without it a
# random key is created once and kept in the database (fine for a trial, not for
# production, where the key must live outside the data it protects).
SECRET = os.environ.get("SMARTPLATE_SECRET", "")
# Behind one reverse proxy (Render, Codespaces): trust its X-Forwarded-* headers.
BEHIND_PROXY = os.environ.get("SMARTPLATE_BEHIND_PROXY", "1" if os.environ.get("RENDER") else "0") == "1"

# Web Push (order-time reminders). Keys are generated on first use when unset.
PUSH_CONTACT = os.environ.get("SMARTPLATE_PUSH_CONTACT", "mailto:smartplate@example.invalid")
PUSH_TICK_S = float(os.environ.get("SMARTPLATE_PUSH_TICK", "60"))
PUSH_ENABLED = os.environ.get("SMARTPLATE_PUSH", "on") == "on"
VAPID_PRIVATE = os.environ.get("SMARTPLATE_VAPID_PRIVATE", "")   # base64url P-256 key; generated if unset

# Default order-execution policy. Swiggy's cautious posture (§7.1) means we
# default to an editable window rather than silent auto-placement.
ORDER_EDIT_WINDOW_MIN = int(os.environ.get("SMARTPLATE_EDIT_WINDOW", "30"))

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


# Rating-floor policy. The brand promise is "we never silently substitute below
# your rating floor" — so the *execution/substitution* path is always hard.
# For the *planner*, the floor can optionally be split into a low hard safety
# floor + a soft preference above it, so a high aspirational ★ bends under budget
# instead of exploding it / forcing constant toggling (docs §4 — the ★ footgun).
#   "hard" (default) : the user's ★ is a hard candidate filter (current behaviour).
#   "soft"           : filter only at HARD_SAFETY_FLOOR; the user's ★ becomes a weight.
RATING_FLOOR_MODE = os.environ.get("SMARTPLATE_RATING_FLOOR", "hard")
HARD_SAFETY_FLOOR = float(os.environ.get("SMARTPLATE_SAFETY_FLOOR", "3.5"))

# Variety nudge. When "on", the planner softly biases toward novel (less-familiar)
# delivery picks, scaled by the user's variety level (domain/fatigue). OFF by default
# so it never silently shifts a plan until the user opts into more variety — the
# honest version of the "composition constraint" in docs §4.
VARIETY_NUDGE = os.environ.get("SMARTPLATE_VARIETY", "off")
VARIETY_NUDGE_W = float(os.environ.get("SMARTPLATE_VARIETY_W", "0.6"))

# Protein evenness: a day-level penalty for backloading protein into one meal (the
# per-meal even target is daily_protein / meals-that-day). Set 0 to disable.
PROTEIN_EVEN_W = float(os.environ.get("SMARTPLATE_PROTEIN_EVEN", "0.25"))

# Usual-first (docs §5.2): a soft preference for the user's *familiar* picks so the
# planner makes the fewest substitutions that still clear the locks/targets — a novel
# pick pays a small premium and only wins when it buys real goal-fit. OFF by default
# (the kept/swapped diagnostic is always computed regardless); the budget recommender
# and the variety nudge already cover the explore side.
USUAL_FIRST = os.environ.get("SMARTPLATE_USUAL_FIRST", "off")
USUAL_FIRST_W = float(os.environ.get("SMARTPLATE_USUAL_FIRST_W", "0.5"))

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
