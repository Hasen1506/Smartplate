"""Ground-truth test support: one frozen clock, golden snapshots, recorded Swiggy
fixtures and the invariant oracle every ground-truth test checks plans against.

Nothing here talks to the network: the suite-wide guard in conftest.py refuses any
non-loopback connection, the planner uses the seeded weather feed, and Swiggy is a
replay of recorded MCP replies (tests/fixtures/swiggy_food_recording.json).
"""
from __future__ import annotations

import datetime as dt
import difflib
import json
import os
import pathlib

HERE = pathlib.Path(__file__).parent
FIXTURES = HERE / "fixtures"
GOLDEN = FIXTURES / "golden"
RECORDING = FIXTURES / "swiggy_food_recording.json"

# Monday 2 Nov 2026, 08:00 IST: a full week ahead, Diwali on the Sunday, no fast.
MONDAY_8AM = dt.datetime(2026, 11, 2, 8, 0)
# Monday 12 Oct 2026, 07:00 IST: every day of this plan week is a Navratri fast day.
NAVRATRI_MONDAY = dt.datetime(2026, 10, 12, 7, 0)

UPDATE_GOLDEN = os.environ.get("SMARTPLATE_UPDATE_GOLDEN") == "1"


# --------------------------------------------------------------------------- #
# Clock
# --------------------------------------------------------------------------- #
class FrozenClock:
    """Pins smartplate.clock (the only clock the app reads) to a settable instant."""

    def __init__(self, monkeypatch, at: dt.datetime):
        from smartplate import clock
        self.at = at
        monkeypatch.setattr(clock, "now", lambda: self.at)
        monkeypatch.setattr(clock, "today", lambda: self.at.date())

    def set(self, at: dt.datetime) -> None:
        self.at = at

    def advance(self, **delta) -> None:
        self.at = self.at + dt.timedelta(**delta)


# --------------------------------------------------------------------------- #
# Golden snapshots
# --------------------------------------------------------------------------- #
def _canonical(data) -> str:
    return json.dumps(data, indent=1, sort_keys=True, ensure_ascii=False) + "\n"


def assert_golden(name: str, data) -> None:
    """Compare `data` with tests/fixtures/golden/<name>.json.

    Regenerate deliberately with SMARTPLATE_UPDATE_GOLDEN=1 and review the diff; a
    missing golden is a failure, never silently created in CI."""
    path = GOLDEN / f"{name}.json"
    text = _canonical(data)
    if UPDATE_GOLDEN:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return
    if not path.exists():
        raise AssertionError(f"golden {path.name} is missing; run with SMARTPLATE_UPDATE_GOLDEN=1 and review it")
    want = path.read_text(encoding="utf-8")
    if want != text:
        diff = "".join(difflib.unified_diff(want.splitlines(True), text.splitlines(True),
                                            f"golden/{path.name}", "actual", n=2))
        raise AssertionError(f"planner output drifted from golden/{path.name}:\n{diff[:6000]}")


# --------------------------------------------------------------------------- #
# Plan snapshots (what a person sees, minus volatile ids)
# --------------------------------------------------------------------------- #
def grid_snapshot(view: dict) -> dict:
    out = {}
    for day in view["grid"]:
        meals = {}
        for meal in ("breakfast", "lunch", "dinner"):
            c = day["meals"].get(meal)
            if not c:
                continue
            meals[meal] = {"kind": c["kind"], "item": c["item"], "restaurant": c.get("restaurant") or "",
                           "cost": round(c.get("cost", 0), 2), "status": c.get("status"),
                           "pinned": bool(c.get("pinned"))}
        out[f"{day['day']} {day.get('date', '')}".strip()] = meals
    return out


def plan_snapshot(view: dict) -> dict:
    b = view["budget"]
    return {"week_start": view["plan"]["week_start"], "mode": view["plan"]["mode"],
            "budget": {k: b.get(k) for k in ("budget", "spend", "remaining", "weekly_budget", "prorated", "daily_cap")},
            "counts": view["counts"], "grid": grid_snapshot(view)}


# --------------------------------------------------------------------------- #
# Invariant oracle
# --------------------------------------------------------------------------- #
CENT = 0.011            # costs are stored to the paisa; allow one paisa of float noise per check


def item_violation(user: dict, item: dict) -> str | None:
    """Independent restatement of the hard rules (not a call into allergens.py), so a
    regression there can't make the oracle agree with itself.

    Vegetarian follows the Indian (FSSAI) convention the app's veg/non-veg marks use:
    eggs are non-vegetarian."""
    people = [user] + list(user.get("household_members") or [])
    item_allergens = set(item.get("allergens") or [])
    for p in people:
        clash = set(p.get("allergens") or []) & item_allergens
        if clash:
            return f"allergen {sorted(clash)} for {p.get('name')}"
        med = set(p.get("medical") or [])
        if "diabetes" in med and item.get("sugar_g", 0) > 20:
            return "sugar > 20 g for diabetes"
        if "hypertension" in med and "high_sodium" in (item.get("tags") or []):
            return "high sodium for hypertension"
        if "celiac" in med and "gluten" in item_allergens:
            return "gluten for celiac"
        diet = p.get("diet") or "nonveg"
        if diet in ("veg", "vegan") and (not item.get("veg", 1) or "egg" in item_allergens
                                         or "nonveg" in (item.get("tags") or [])):
            return f"non-veg for a {diet} profile"
        if diet == "vegan" and "dairy" in item_allergens:
            return "dairy for a vegan profile"
    return None


def plan_state(plan_id: int) -> dict:
    from smartplate import db
    from smartplate.domain import models
    plan = models.get_plan(plan_id)
    user = models.get_user(plan["user_id"])
    with db.cursor() as cur:
        sessions = [db.row_to_dict(r) for r in cur.execute(
            "SELECT * FROM sessions WHERE plan_id=? ORDER BY day, id", (plan_id,))]
        rows = [db.row_to_dict(r) for r in cur.execute(
            "SELECT * FROM decisions WHERE plan_id=? ORDER BY id", (plan_id,))]
        menu = {r["id"]: db.row_to_dict(r) for r in cur.execute(
            "SELECT m.*, r.delivery_fee, r.rating AS restaurant_rating FROM menu_items m "
            "JOIN restaurants r ON r.id=m.restaurant_id")}
    for it in menu.values():
        it["allergens"] = json.loads(it["allergens"] or "[]")
        it["tags"] = json.loads(it["tags"] or "[]")
    by_session: dict[int, list] = {}
    for d in rows:
        by_session.setdefault(d["session_id"], []).append(d)
    return {"plan": plan, "user": user, "sessions": sessions, "decisions": by_session, "menu": menu}


def check_plan(plan_id: int, *, pins_or_spent_allowed_over: bool = True) -> list[str]:
    """Every hard invariant a stored plan must satisfy. Returns human-readable failures."""
    from smartplate.domain import festivals, models, profile, reverse_mode
    from smartplate.kernel import optimizer, scheduler

    st = plan_state(plan_id)
    plan, user, sessions, menu = st["plan"], st["user"], st["sessions"], st["menu"]
    bad: list[str] = []
    at = optimizer.now()

    # 1. every enabled meal slot exists once and carries exactly one decision
    wanted = set(profile.meals_planned(user))
    slots = [(s["day"], s["meal"]) for s in sessions]
    if len(slots) != len(set(slots)):
        bad.append(f"duplicate meal slots: {sorted(slots)}")
    have_meals = {m for _, m in slots}
    if not wanted <= have_meals and any(s["status"] == "active" for s in sessions):
        bad.append(f"enabled meals {sorted(wanted)} not all planned (have {sorted(have_meals)})")
    for s in sessions:
        n = len(st["decisions"].get(s["id"], []))
        if n != 1:
            bad.append(f"{models.DAYS[s['day']]} {s['meal']} has {n} decisions (want exactly 1)")

    latest = {sid: ds[-1] for sid, ds in st["decisions"].items()}
    fests = festivals.for_week_all(plan["week_start"])
    spend, fixed, by_day, fixed_by_day = 0.0, 0.0, {}, {}
    for s in sessions:
        d = latest.get(s["id"])
        if not d:
            continue
        label = f"{models.DAYS[s['day']]} {s['meal']}"
        kind = d["chosen_kind"]
        # 2. hard food rules (delivery and cook alike)
        if kind == "delivery":
            item = menu.get(d["item_id"])
            if not item:
                bad.append(f"{label}: unknown item {d['item_id']}")
            else:
                why = item_violation(user, item)
                if why and s["status"] not in ("ordered", "confirmed"):
                    bad.append(f"{label}: planned {item['name']} — {why}")
                # 3. item + fees, times the recorded surge, is the cost
                base = item["price"] + item["delivery_fee"]
                if abs(round(base * (d["surge_mult"] or 1.0), 2) - d["cost"]) > CENT:
                    bad.append(f"{label}: cost {d['cost']} != ({item['price']} + {item['delivery_fee']}) × {d['surge_mult']}")
        if kind == "cook" and d.get("recipe_key"):
            r = reverse_mode.recipe(d["recipe_key"])
            why = item_violation(user, r) if r else "unknown recipe"
            if why and s["status"] not in ("ordered", "confirmed"):
                bad.append(f"{label}: cook {d['recipe_key']} — {why}")
        # 4. fast days: an observed fast keeps breakfast and lunch clear
        if s["status"] == "active" and not scheduler.is_past(s, at):
            for f in fests.get(s["day"], []):
                if festivals.suspends_session(f, s["meal"], user) and kind in ("delivery", "cook") \
                        and not d.get("item_name", "").startswith("Leftover"):
                    if not _pinned(s):
                        bad.append(f"{label}: {kind} planned on a {f['name']} fast day")
        # money: what the plan commits for this week
        if kind in ("delivery", "cook"):
            spent = s["status"] in ("ordered", "confirmed")
            past = s["status"] == "active" and scheduler.is_past(s, at)
            # an unconfirmed past meal is unknown, not spent (the app never assumes it)
            if spent or (_pinned(s) and not past):
                fixed += d["cost"]
                fixed_by_day[s["day"]] = fixed_by_day.get(s["day"], 0.0) + d["cost"]
            elif not past:
                spend += d["cost"]
                by_day[s["day"]] = by_day.get(s["day"], 0.0) + d["cost"]

    # variety: a dish at most twice a week and once a day, unless the user pinned it there
    week_n, pinned_n, day_items = {}, {}, {}
    for s in sessions:
        d = latest.get(s["id"])
        if not d or d["chosen_kind"] != "delivery":
            continue
        open_now = s["status"] == "active" and not scheduler.is_past(s, at)
        if open_now:
            week_n[d["item_id"]] = week_n.get(d["item_id"], 0) + 1
            pinned_n[d["item_id"]] = pinned_n.get(d["item_id"], 0) + (1 if _pinned(s) else 0)
        if s["status"] in ("active", "ordered") and not (s["status"] == "active" and scheduler.is_past(s, at)):
            day_items.setdefault((s["day"], d["item_id"]), []).append(_pinned(s))
    for iid, n in week_n.items():
        if n > max(optimizer.MAX_ITEM_REPEAT, pinned_n[iid]):
            bad.append(f"{menu[iid]['name']} planned {n}× this week (cap {optimizer.MAX_ITEM_REPEAT}, {pinned_n[iid]} pinned)")
    for (day, iid), pins in day_items.items():
        if len(pins) > 1 and not any(pins):
            bad.append(f"{models.DAYS[day]}: {menu[iid]['name']} {len(pins)}× in one day")

    # 5. budget: planned (solver-chosen) spend never exceeds what the cap leaves after
    #    the user's own pins and money already spent. All-skip is always feasible.
    cap = optimizer.week_cap(user, plan)
    if spend > max(0.0, cap - fixed) + CENT:
        bad.append(f"solver spend {spend:.2f} exceeds weekly room {max(0.0, cap - fixed):.2f} (cap {cap}, fixed {fixed:.2f})")
    dcap = profile.daily_cap(user)
    if dcap:
        for day, amount in by_day.items():
            room = max(0.0, dcap - fixed_by_day.get(day, 0.0))
            if amount > room + CENT:
                bad.append(f"{models.DAYS[day]}: solver spend {amount:.2f} exceeds daily room {room:.2f}")
    return bad


def _pinned(session: dict) -> bool:
    return bool(session.get("pinned"))


def safe_meal_items(user: dict) -> list[dict]:
    """Menu items a planner may offer this user for a meal (hard rules + rating floor)."""
    from smartplate.domain import models
    from smartplate.kernel import optimizer
    floor = float(user.get("rating_floor", 4.0))
    return [it for it in models.menu_for_city(user["city"])
            if item_violation(user, it) is None and optimizer.meal_suitable(it, "lunch")
            and it["restaurant_rating"] >= floor and it["item_rating"] >= floor - 0.3]


# --------------------------------------------------------------------------- #
# Recorded Swiggy Food MCP (replay)
# --------------------------------------------------------------------------- #
def _key(name: str, args: dict) -> str:
    return name + " " + json.dumps(args, sort_keys=True, separators=(",", ":"))


def make_replay():
    """A Swiggy MCP server that answers tools/call only from the recording.

    OAuth, initialize and tools/list come from the repo's contract fake; every tool
    reply is looked up by (tool, exact arguments, cart state). An unrecorded call
    fails the test, so a change in what SmartPlate sends to Swiggy is never silently
    absorbed. Set SMARTPLATE_RECORD_SWIGGY=1 to re-record from the contract fake."""
    from test_swiggy_live import FakeLive

    class ReplaySwiggy(FakeLive):
        def __init__(self, record: bool = False):
            super().__init__()
            self.record = record or os.environ.get("SMARTPLATE_RECORD_SWIGGY") == "1"
            self.dishes = {"Mini Tiffin": 12500, "Ghee Pongal": 14000, "Peanut Chutney Dosa": 13000}
            self.cart_state = "empty"
            data = json.loads(RECORDING.read_text()) if RECORDING.exists() else {"calls": []}
            self.recording = {(c["key"], c["cart"]): c["reply"] for c in data["calls"]}
            self.recorded = dict(self.recording)        # record mode adds to what is already recorded
            self.unrecorded = []

        def reset(self):
            """Fresh cart and call log for the next example (the recording is unchanged)."""
            self.cart, self.cart_state, self.calls = None, "empty", []

        strict = True        # journeys: an unrecorded call fails the test

        def tool(self, name, args):
            key = (_key(name, args), self.cart_state)
            if self.record:
                try:
                    reply = super().tool(name, args)
                except AssertionError:               # outside the contract fake: Swiggy refuses it
                    return self._refusal(name)
                self.recorded[key] = reply
            else:
                if key not in self.recording:
                    if self.strict:
                        self.unrecorded.append(key[0])
                        raise AssertionError(f"unrecorded Swiggy call {key[0]} (cart {key[1]}); re-record "
                                             "with SMARTPLATE_RECORD_SWIGGY=1 after reviewing why it changed")
                    return self._refusal(name)       # fuzzing: answer like Swiggy does for a bad request
                reply = json.loads(json.dumps(self.recording[key]))
            if name == "update_food_cart":
                self.cart_state = "filled"
            if name == "place_food_order":
                self.cart_state = "empty"
            return reply

        @staticmethod
        def _refusal(name):
            return {"isError": True, "content": [{"type": "text", "text": f"{name}: not found for this request"}]}

        def save(self):
            RECORDING.parent.mkdir(parents=True, exist_ok=True)
            calls = [{"key": k, "cart": c, "reply": r} for (k, c), r in sorted(self.recorded.items())]
            RECORDING.write_text(json.dumps({
                "provenance": "Recorded from the documented Swiggy Food MCP contract fake (tests/test_swiggy_live.py "
                              "FakeLive): no real Swiggy account, cart or order is ever used by tests.",
                "calls": calls}, indent=1, sort_keys=True) + "\n")

    return ReplaySwiggy


def connect_swiggy(client, fake, uid: int, key: str) -> None:
    """Swiggy sign-in for a profile whose key the caller already holds (no key rotation)."""
    from test_followups import APP, _allow_app_host
    _allow_app_host()
    headers = {"X-SmartPlate-Key": key}
    url = client.post(f"/api/user/{uid}/swiggy/connect", json={}, base_url=APP, headers=headers).get_json()["authorize_url"]
    q = fake.approve(url)
    r = client.get(f"/swiggy/callback?state={q['state']}&code=code-1", base_url=APP, headers=headers)
    assert r.status_code == 302 and r.headers["Location"].endswith("swiggy=connected"), r.headers.get("Location")
    r = client.post(f"/api/user/{uid}/swiggy/address", json={"address_id": "addr-home"}, headers=headers)
    assert r.status_code == 200, r.get_json()
