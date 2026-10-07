"""§5.2.7 — Household / group mode.

Multiple members share a plan. The hard-constraint set is the UNION of every
member's allergens/medical rules (handled in allergens.household_safe), and cost
is split per the household's policy. Planning treats the group as one consumer
whose safety profile is the strictest combination of its members.

Members are either other SmartPlate profiles that joined (the seeded "Flat 3B") or
people the owner cooks and orders for who have no profile of their own: a child, a
parent. Those are stored as user rows marked `prefs.household_member` with an
unusable access hash, so they are never listed, never opened by id, and are edited
only through the owner's profile.

Consumption: each planned meal is eaten by some of the members, by default everyone
who usually eats that meal (a member's `meals`), or whoever the owner ticked for that
meal. "Split by consumption" divides each meal's cost among the people who ate it.
"""
import hashlib
import secrets

from .. import db
from . import allergens, profile

MAX_MEMBERS = 8
SPLITS = ("even", "by_consumption")


def get(household_id: int) -> dict | None:
    with db.cursor() as cur:
        row = cur.execute("SELECT * FROM households WHERE id=?", (household_id,)).fetchone()
    return db.row_to_dict(row) if row else None


def merged_profile(members: list[dict]) -> dict:
    """A synthetic 'user' whose constraints are the strictest across members."""
    allerg, medical = set(), set()
    diet_rank = {"vegan": 0, "veg": 1, "nonveg": 2}
    widest = "vegan"
    for m in members:
        allerg |= set(m.get("allergens", []))
        medical |= set(m.get("medical", []))
        if diet_rank[m.get("diet", "nonveg")] > diet_rank[widest]:
            widest = m.get("diet", "nonveg")
    return {
        "name": "Household",
        "allergens": sorted(allerg),
        "medical": sorted(medical),
        # Group meals must satisfy everyone, so use the MOST restrictive diet.
        "diet": ("vegan" if any(m.get("diet") == "vegan" for m in members)
                 else "veg" if any(m.get("diet") == "veg" for m in members) else "nonveg"),
        "_widest_diet": widest,
    }


def combined_rules(members: list[dict]) -> dict:
    """Everyone's hard rules with who each one is for — what shared meals must meet."""
    def who(pred):
        return [m["name"] for m in members if pred(m)]
    merged = merged_profile(members)
    return {
        "allergens": [{"rule": a, "who": who(lambda m, a=a: a in (m.get("allergens") or []))} for a in merged["allergens"]],
        "medical": [{"rule": c, "who": who(lambda m, c=c: c in (m.get("medical") or []))} for c in merged["medical"]],
        "diet": {"rule": merged["diet"], "who": who(lambda m: m.get("diet", "nonveg") == merged["diet"])
                 if merged["diet"] != "nonveg" else []},
    }


# --------------------------------------------------------------------------- #
# Members
# --------------------------------------------------------------------------- #
def is_managed(member: dict) -> bool:
    return bool((db.jl(member.get("prefs"), {}) if isinstance(member.get("prefs"), str)
                 else (member.get("prefs") or {})).get("household_member"))


def member_meals(member: dict, owner: dict) -> list[str]:
    """The meals this member usually eats with the household."""
    prefs = db.jl(member.get("prefs"), {}) if isinstance(member.get("prefs"), str) else (member.get("prefs") or {})
    planned = profile.meals_planned(owner)
    meals = prefs.get("meals") if prefs.get("household_member") else profile.meals_planned({"prefs": prefs})
    return [m for m in planned if m in (meals or planned)]


def validate_member(body: dict, *, partial: bool = False) -> dict:
    if not isinstance(body, dict):
        raise ValueError("Send a JSON object")
    extra = set(body) - {"name", "diet", "allergens", "medical", "meals"}
    if extra:
        raise ValueError(f"Unsupported field: {sorted(extra)[0]}")
    out = {}
    if "name" in body or not partial:
        name = body.get("name")
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 40:
            raise ValueError("Enter a name of 1–40 characters")
        out["name"] = name.strip()
    if "diet" in body or not partial:
        if body.get("diet", "nonveg") not in profile.DIETS:
            raise ValueError("Choose vegetarian, non-vegetarian, or vegan")
        out["diet"] = body.get("diet", "nonveg")
    for key, allowed in (("allergens", profile.ALLERGENS), ("medical", profile.MEDICAL)):
        if key in body or not partial:
            out[key] = profile._choices(body.get(key, []), key, allowed)
    if "meals" in body:
        meals = profile._choices(body["meals"], "meal", profile.MEALS)
        if not meals:
            raise ValueError("Pick at least one meal this person eats with you")
        out["meals"] = [m for m in profile.MEALS if m in meals]
    return out


def create(owner: dict, name: str) -> int:
    if owner.get("household_id"):
        raise ValueError("You already share a household")
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 40:
        raise ValueError("Name the household in 1–40 characters")
    with db.cursor() as cur:
        cur.execute("INSERT INTO households(name, split) VALUES (?, 'even')", (name.strip(),))
        hid = cur.lastrowid
        cur.execute("UPDATE users SET household_id=? WHERE id=?", (hid, owner["id"]))
    return hid


def update(owner: dict, body: dict) -> None:
    hid = _require(owner)
    if not isinstance(body, dict) or not body or set(body) - {"name", "split"}:
        raise ValueError("Change the household's name or how costs are split")
    if "name" in body and (not isinstance(body["name"], str) or not 1 <= len(body["name"].strip()) <= 40):
        raise ValueError("Name the household in 1–40 characters")
    if "split" in body and body["split"] not in SPLITS:
        raise ValueError("Split costs evenly or by who ate each meal")
    with db.cursor() as cur:
        if "name" in body:
            cur.execute("UPDATE households SET name=? WHERE id=?", (body["name"].strip(), hid))
        if "split" in body:
            cur.execute("UPDATE households SET split=? WHERE id=?", (body["split"], hid))


def add_member(owner: dict, body: dict) -> int:
    hid = _require(owner)
    data = validate_member(body)
    with db.cursor() as cur:
        n = cur.execute("SELECT COUNT(*) n FROM users WHERE household_id=?", (hid,)).fetchone()["n"]
        if n >= MAX_MEMBERS:
            raise ValueError(f"A household can have up to {MAX_MEMBERS} people")
        prefs = {"household_member": True, "owner_id": owner["id"],
                 "meals": data.get("meals") or profile.meals_planned(owner)}
        # never openable: a random hash nobody holds the key for
        unusable = hashlib.sha256(secrets.token_bytes(32)).hexdigest()
        cur.execute("INSERT INTO users(name, city, diet, weekly_budget, allergens, medical, household_id, prefs, "
                    "access_hash) VALUES (?,?,?,?,?,?,?,?,?)",
                    (data["name"], owner["city"], data["diet"], 0, db.jd(data["allergens"]), db.jd(data["medical"]),
                     hid, db.jd(prefs), unusable))
        return cur.lastrowid


def _managed_member(owner: dict, member_id: int) -> dict:
    hid = _require(owner)
    with db.cursor() as cur:
        row = cur.execute("SELECT * FROM users WHERE id=? AND household_id=?", (member_id, hid)).fetchone()
    if not row:
        raise KeyError("Member not found")
    m = db.row_to_dict(row)
    if not is_managed(m):
        raise ValueError(f"{m['name']} has their own SmartPlate profile and edits it there")
    return m


def update_member(owner: dict, member_id: int, body: dict) -> None:
    m = _managed_member(owner, member_id)
    data = validate_member(body, partial=True)
    if not data:
        raise ValueError("Nothing to update")
    prefs = db.jl(m["prefs"], {})
    sets, vals = [], []
    for key in ("name", "diet"):
        if key in data:
            sets.append(f"{key}=?")
            vals.append(data[key])
    for key in ("allergens", "medical"):
        if key in data:
            sets.append(f"{key}=?")
            vals.append(db.jd(data[key]))
    if "meals" in data:
        prefs["meals"] = data["meals"]
        sets.append("prefs=?")
        vals.append(db.jd(prefs))
    with db.cursor() as cur:
        cur.execute(f"UPDATE users SET {', '.join(sets)} WHERE id=?", (*vals, member_id))


def remove_member(owner: dict, member_id: int) -> None:
    m = _managed_member(owner, member_id)
    with db.cursor() as cur:
        cur.execute("DELETE FROM users WHERE id=?", (m["id"],))
        _forget_eater(cur, owner, m["id"])


def leave(owner: dict) -> None:
    """Stop sharing: the people without a profile go with the household; profiles that
    joined keep their own plans."""
    hid = _require(owner)
    with db.cursor() as cur:
        rows = cur.execute("SELECT id, prefs FROM users WHERE household_id=?", (hid,)).fetchall()
        managed = [r["id"] for r in rows if is_managed(dict(r))]
        for mid in managed:
            cur.execute("DELETE FROM users WHERE id=?", (mid,))
        cur.execute("UPDATE users SET household_id=NULL WHERE id=?", (owner["id"],))
        if not cur.execute("SELECT 1 FROM users WHERE household_id=?", (hid,)).fetchone():
            cur.execute("DELETE FROM households WHERE id=?", (hid,))
        cur.execute("UPDATE sessions SET eaters=NULL WHERE plan_id IN (SELECT id FROM plans WHERE user_id=?)",
                    (owner["id"],))


def _forget_eater(cur, owner, member_id):
    for s in cur.execute("SELECT s.id, s.eaters FROM sessions s JOIN plans p ON p.id=s.plan_id "
                         "WHERE p.user_id=? AND s.eaters IS NOT NULL", (owner["id"],)).fetchall():
        left = [e for e in db.jl(s["eaters"]) if e != member_id]
        cur.execute("UPDATE sessions SET eaters=? WHERE id=?", (db.jd(left) if left else None, s["id"]))


def _require(owner: dict) -> int:
    if not owner.get("household_id"):
        raise ValueError("Set up a household first")
    return owner["household_id"]


# --------------------------------------------------------------------------- #
# Who ate what, and who owes what
# --------------------------------------------------------------------------- #
def eaters(session: dict, members: list[dict], owner: dict) -> list[int]:
    """Member ids eating this meal: the owner's ticks for it, else everyone who usually
    eats that meal, else everyone."""
    ids = [m["id"] for m in members]
    chosen = db.jl(session.get("eaters")) if session.get("eaters") else None
    if chosen:
        picked = [i for i in ids if i in chosen]
        if picked:
            return picked
    usual = [m["id"] for m in members if session["meal"] in member_meals(m, owner)]
    return usual or ids


def set_eaters(session: dict, members: list[dict], value) -> None:
    ids = {m["id"] for m in members}
    if value is not None:
        if (not isinstance(value, list) or not value
                or any(isinstance(v, bool) or not isinstance(v, int) or v not in ids for v in value)):
            raise ValueError("Tick at least one person from your household")
    with db.cursor() as cur:
        cur.execute("UPDATE sessions SET eaters=? WHERE id=?",
                    (db.jd(sorted(set(value))) if value is not None else None, session["id"]))


def split_cost(household: dict, members: list[dict], total: float, meals: list[dict] | None = None,
               owner: dict | None = None) -> list[dict]:
    """Shares of `total`. Even: total ÷ people. By consumption: each meal's cost divided
    among the people who ate it (`meals` are decisions with their session's meal/eaters).
    Shares are rounded to the paisa and always add up to the total."""
    n = max(len(members), 1)
    raw = {m["id"]: total / n for m in members}
    if household.get("split") == "by_consumption" and meals is not None and owner is not None:
        raw = {m["id"]: 0.0 for m in members}
        for d in meals:
            who = eaters(d, members, owner)
            for i in who:
                raw[i] += d.get("cost", 0) / len(who)
    shares = {i: round(v, 2) for i, v in raw.items()}
    drift = round(round(total, 2) - sum(shares.values()), 2)
    if members and drift:
        biggest = max(shares, key=lambda i: shares[i])
        shares[biggest] = round(shares[biggest] + drift, 2)
    return [{"member_id": m["id"], "member": m["name"], "share": shares[m["id"]]} for m in members]


def safe(members: list[dict], item: dict) -> str | None:
    return allergens.household_safe(members, item)
