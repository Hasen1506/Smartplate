"""Notice, consent and erasure under India's DPDP Act 2023 and DPDP Rules 2025 (grill #43-#45).

What the law asks of Ziggy, in short (sources in docs/privacy-compliance.md):
- Before asking for consent, give a notice that stands on its own, in plain language, with
  an itemised list of the personal data, each purpose, how to withdraw consent as easily as
  it was given, how to use one's rights and how to complain to the Data Protection Board
  (Act s.5-6, Rule 3). `PURPOSES` is that itemised notice; /privacy shows it in full.
- Consent is per purpose, free and affirmative, and Ziggy must be able to prove it
  (s.6(1), s.6(10)): every grant and withdrawal is appended to `consent_log`.
- Withdrawal must be as easy as giving it and stops the processing (s.6(4), 6(6)); erasure
  on withdrawal or on request (s.8(7), s.12(3)).
- Children (under 18) need verifiable parental consent (s.9): Ziggy is for adults only,
  confirmed with the sign-up consent.
- Logs of processing are kept for one year (Rule 8(3)), so the consent record of a deleted
  profile (ids, purposes, timestamps; no name, diet or health data) is kept for
  `LOG_RETENTION` and then purged.
Most of these duties start on 13 May 2027 (Rule 1(4)); Ziggy follows them now.

Profiles made before consent existed (and the shared sample profiles) have no rows: their
earlier use continues (s.5(2)) until the person reviews the notice. Swiggy access is the
exception: it always needs an explicit "yes" recorded here.
"""
import datetime as dt
import re

from . import clock, config, db

NOTICE_VERSION = "2026-10-14"
LOG_RETENTION = dt.timedelta(days=365)

# The itemised notice (Rule 3(b)): what is collected, why, and what happens without it.
PURPOSES = {
    "account": {
        "required": True,
        "title": "Run your Ziggy profile and plan your meals",
        "data": ["profile name", "diet type (veg, egg, non-veg, vegan)", "allergies and foods you avoid",
                 "budget and meal times", "favourite restaurants and dishes", "your plans, ratings and spending",
                 "optional sign-in name and password (stored only as a hash)"],
        "why": "To build meal plans that fit your diet, allergies, budget and timing, and to keep them for you.",
        "without": "Ziggy can't make a profile. You can still browse without one.",
    },
    "health": {
        "required": False,
        "title": "Use health details for nutrition",
        "data": ["medical conditions you choose (for example diabetes)", "height, weight, age, sex and activity"],
        "why": "To keep meals inside your health limits and show nutrition targets.",
        "without": "Plans ignore health conditions and body details; allergies still apply.",
    },
    "learning": {
        "required": False,
        "title": "Learn from your ratings",
        "data": ["the reasons you tap after a meal (late, small, spicy, pricey, great)"],
        "why": "To make the next plans fit you better. You can see and undo what was learned.",
        "without": "Reason taps are not saved and do not change future plans. Thumbs up/down still count.",
    },
    "swiggy": {
        "required": False,
        "title": "Connect Swiggy",
        "data": ["a Swiggy access token (encrypted)", "your saved delivery addresses (label and area)",
                 "live menus and prices for your address", "your Swiggy cart", "your recent Swiggy orders"],
        "why": "To show real menus and prices, fill your Swiggy cart, and find your usual dishes from past orders.",
        "without": "Ziggy plans without live Swiggy menus, and you order in the Swiggy app yourself.",
    },
}
OPTIONAL = tuple(p for p, v in PURPOSES.items() if not v["required"])

SCHEMA = """
CREATE TABLE IF NOT EXISTS consent_log (         -- every consent given or withdrawn (DPDP s.6(10) proof)
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL,
    purpose TEXT NOT NULL,                         -- account | health | learning | swiggy | erasure
    granted INTEGER NOT NULL,
    notice_version TEXT NOT NULL,
    ts TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT ''                -- signup | settings | swiggy-connect | deletion
);
"""
_ready: set = set()


class ConsentRequired(Exception):
    """The person has not agreed to this purpose yet: the app shows the notice and asks."""

    def __init__(self, purpose: str):
        super().__init__(f"Ziggy needs your OK to {PURPOSES[purpose]['title'].lower()} first.")
        self.purpose = purpose


def init_schema() -> None:
    where = (config.DATABASE_URL, config.PG_SCHEMA, config.DB_PATH)
    if where in _ready:
        return
    with db.cursor() as cur:
        cur.executescript(SCHEMA)
    if config.DATABASE_URL:
        from . import db_pg
        db_pg.register_schema(SCHEMA)
    _ready.add(where)


def notice() -> dict:
    return {"version": NOTICE_VERSION, "purposes": PURPOSES, "policy_url": "/privacy", "terms_url": "/terms",
            "adults_only": True}


def record(cur, user_id: int, purpose: str, granted: bool, source: str) -> None:
    cur.execute("INSERT INTO consent_log(user_id, purpose, granted, notice_version, ts, source) VALUES (?,?,?,?,?,?)",
                (user_id, purpose, 1 if granted else 0, NOTICE_VERSION,
                 clock.now().isoformat(timespec="seconds"), source))


def state(user_id: int) -> dict:
    """Current choice per purpose, plus whether this profile predates consent records."""
    init_schema()
    with db.cursor() as cur:
        rows = cur.execute("SELECT purpose, granted, notice_version, ts FROM consent_log WHERE user_id=? "
                           "ORDER BY ts, id", (user_id,)).fetchall()
    latest = {}
    for r in rows:
        latest[r["purpose"]] = {"granted": bool(r["granted"]), "version": r["notice_version"], "at": r["ts"]}
    return {"legacy": not rows, "purposes": latest, "notice_version": NOTICE_VERSION}


def granted(user_id: int | None, purpose: str) -> bool:
    """Whether processing for `purpose` may go ahead. Profiles without any consent record
    (made before notices existed, or shared samples) keep their earlier use, except Swiggy."""
    if not user_id:
        return True
    current = state(user_id)
    if purpose in current["purposes"]:
        return current["purposes"][purpose]["granted"]
    return current["legacy"] and purpose != "swiggy"


def require(user_id: int, purpose: str) -> None:
    if not granted(user_id, purpose):
        raise ConsentRequired(purpose)


def read_signup(body: dict) -> dict:
    """Take the consent answers out of a sign-up body. `consent: true` accepts the notice for
    the required purpose (and confirms 18+); an object can also opt into optional purposes:
    {"account": true, "health": true, "learning": false}."""
    raw = body.pop("consent", None) if isinstance(body, dict) else None
    if raw is True:
        raw = {"account": True}
    if not isinstance(raw, dict) or raw.get("account") is not True:
        raise ValueError("Please read the privacy notice and tick “I'm 18 or older and I agree” to create a profile.")
    choices = {"account": True}
    for purpose in OPTIONAL:
        if purpose == "swiggy":
            continue                                   # asked separately, right before connecting
        choices[purpose] = raw.get(purpose) is True
    if not choices["health"] and _has_health(body):
        raise ValueError("You added health details. Tick “Use health details for nutrition” to save them, "
                         "or remove them.")
    return choices


def _has_health(body: dict) -> bool:
    return bool(isinstance(body, dict) and (body.get("medical") or body.get("body")))


def check_setup_change(user_id: int, body: dict) -> None:
    if _has_health(body) and not granted(user_id, "health"):
        raise ConsentRequired("health")


def save_signup(user_id: int, choices: dict) -> None:
    init_schema()
    with db.cursor() as cur:
        for purpose, ok in choices.items():
            record(cur, user_id, purpose, ok, "signup")


def set_choice(user_id: int, purpose, value) -> dict:
    """Give or withdraw one optional consent. Withdrawing stops the processing and erases
    what it needed (s.6(6), s.8(7))."""
    if purpose == "account":
        raise ValueError("To withdraw consent for your profile, delete it (Account & devices → Your data).")
    if purpose not in OPTIONAL or not isinstance(value, bool):
        raise ValueError("Choose a purpose and true or false")
    init_schema()
    with db.cursor() as cur:
        record(cur, user_id, purpose, value, "settings")
        if not value:
            _erase_for(cur, user_id, purpose)
    if purpose == "swiggy" and not value:
        from .domain import live_catalog
        from .integrations import swiggy_connect
        live_catalog.clear(user_id)
        swiggy_connect.disconnect(user_id)
    return state(user_id)


def _erase_for(cur, user_id: int, purpose: str) -> None:
    if purpose == "health":
        row = cur.execute("SELECT prefs FROM users WHERE id=?", (user_id,)).fetchone()
        prefs = db.jl(row["prefs"], {}) if row else {}
        if isinstance(prefs, dict):
            prefs["body"] = None
        cur.execute("UPDATE users SET medical='[]', prefs=? WHERE id=?", (db.jd(prefs), user_id))
    elif purpose == "learning":
        cur.execute("DELETE FROM rating_reasons WHERE user_id=?", (user_id,))


def note_erasure(user_id: int) -> None:
    """Keep the proof that a profile was erased (Rule 8(3) one-year processing log)."""
    init_schema()
    with db.cursor() as cur:
        record(cur, user_id, "erasure", False, "deletion")


def purge_expired() -> int:
    """Drop consent records of erased profiles once the one-year log period is over."""
    init_schema()
    cutoff = (clock.now() - LOG_RETENTION).isoformat(timespec="seconds")
    with db.cursor() as cur:
        gone = [r["user_id"] for r in cur.execute(
            "SELECT user_id FROM consent_log WHERE purpose='erasure' AND ts<?", (cutoff,)).fetchall()]
        for uid in gone:
            if not cur.execute("SELECT 1 FROM users WHERE id=?", (uid,)).fetchone():
                cur.execute("DELETE FROM consent_log WHERE user_id=?", (uid,))
    return len(gone)


# ---- redaction for anything Ziggy keeps or logs about a Swiggy reply (grill #45) ----
_PATTERNS = [
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), "[email]"),
    (re.compile(r"(?<!\d)(?:\+?91[\s-]?)?[6-9]\d{4}[\s-]?\d{5}(?!\d)"), "[phone]"),
    (re.compile(r"(?i)\b(lat(?:itude)?|lng|lon(?:gitude)?)\b[\"']?\s*[:=]\s*[\"']?-?\d{1,3}\.\d+"), r"\1=[geo]"),
    (re.compile(r"-?\d{1,3}\.\d{4,}\s*,\s*-?\d{1,3}\.\d{4,}"), "[geo]"),
    (re.compile(r"(?i)\b(bearer|token|access_token|authorization)\b[\"']?\s*[:=]?\s*[\"']?[A-Za-z0-9._~+/=-]{12,}"),
     r"\1 [secret]"),
    (re.compile(r"(?i)\"(address(?:line)?|annotation|flat(?:no)?|landmark|street|phone|mobile|customer_?name|"
                r"name_of_customer|email)\"\s*:\s*\"[^\"]*\""), r'"\1": "[redacted]"'),
    (re.compile(r"(?<!\d)\d{6}(?!\d)"), "[pin]"),
]


def redact(text) -> str:
    """Text with emails, phone numbers, coordinates, PIN codes, tokens and address fields
    masked. For error messages and logs about Swiggy replies; never for values shown to
    their owner on purpose."""
    out = str(text or "")
    for rx, repl in _PATTERNS:
        out = rx.sub(repl, out)
    return out


# ---- /privacy and /terms ----
def page(name: str) -> str:
    """The notice or terms page with the founder's details filled in (all HTML-escaped)."""
    import html
    import os
    path = os.path.join(os.path.dirname(__file__), "static", f"{name}.html")
    with open(path, encoding="utf-8") as f:
        text = f.read()
    esc = html.escape
    if config.PRIVACY_CONTACT:
        contact = f'<a href="mailto:{esc(config.PRIVACY_CONTACT)}">{esc(config.PRIVACY_CONTACT)}</a>'
    else:
        contact = "the contact that will be published here before Ziggy opens to the public"
    operator = esc(config.OPERATOR) if config.OPERATOR else "the founder of Ziggy (name to be published before launch)"
    who = esc(config.GRIEVANCE_NAME) if config.GRIEVANCE_NAME else "The founder"
    grievance = (f"{who} answers questions about your data and handles grievances: {contact}. "
                 "Please include your profile number so we can find your data.")
    # Automatic deletion of unused profiles is a founder decision not yet taken (docs/privacy-compliance.md).
    inactive = "We do not yet delete unused profiles automatically; delete yours whenever you like."
    values = {"NOTICE_VERSION": NOTICE_VERSION, "OPERATOR": operator, "CONTACT": contact, "GRIEVANCE": grievance,
              "INACTIVE_RULE": esc(inactive), "BACKUP_WINDOW": esc(config.BACKUP_WINDOW)}
    for key, value in values.items():
        text = text.replace("{{" + key + "}}", value)
    return text
