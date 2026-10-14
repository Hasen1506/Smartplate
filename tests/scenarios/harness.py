"""Persona week simulations: drive the app through the Flask test client the way the UI
does (open, set budget, pick favourites, plan, swap, cart, confirm, recap) against a
stand-in Swiggy neighbourhood, and check outcome invariants the founder cares about:

  * no unsafe dish is ever planned, pooled or accepted for someone it can hurt;
  * the planned week stays inside the money the user set (their own pins excepted);
  * "Today" always has an answer while the app is open;
  * messages are honest (no celebration over an empty week, no invented savings).

Everything is deterministic: a frozen clock, no network, Swiggy is tests/swiggy_town.py
plus a few scenario-only additions defined here (a hostile Tamil-menu restaurant and
address-dependent supply, so peri-urban Minjur sees a thin neighbourhood and OMR /
Velachery a dense one). Nothing in tests/swiggy_town.py itself is changed.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import re

import pytest
import swiggy_town
from swiggy_town import TownFake
from test_swiggy_live import ok

# --------------------------------------------------------------------------- #
# The scenario neighbourhood
# --------------------------------------------------------------------------- #
# A hostile restaurant (findings usab #1-#3): Tamil non-veg names with NO veg mark,
# peanut / cashew dishes under Tamil and Hindi names, sweets with no nutrition template.
HOSTILE = {
    "r-201": ("Kadal Kozhi Mess", "Velachery", 4.3, 30, ["Chettinad", "Seafood"], True, [
        ("Kozhi Varuval", 18000, None, "Starters", True, False),      # chicken, unmarked
        ("Eral Thokku", 26000, None, "Mains", False, False),          # prawn, unmarked
        ("Nandu Masala", 32000, None, "Mains", False, False),         # crab, unmarked
        ("Verkadalai Sundal", 8000, True, "Snacks", False, False),    # peanut
        ("Kaju Kari", 22000, True, "Curries", False, False),          # cashew
        ("Satay Veg Bowl", 22500, True, "Bowls", False, False),       # peanut sauce
        ("Rasmalai", 9000, True, "Sweets", False, False),             # sweet, no template
        ("Veg Kothu Parotta", 15000, True, "Parotta", True, False),
    ]),
}
PLACES = {**swiggy_town.PLACES, **HOSTILE}

# Which places deliver to which address. Minjur (peri-urban, the founder's real area) is
# thin: two places. OMR and Velachery are dense.
ADDRESSES = {
    "addr-minjur": ("Home", "DVS Nagar, Minjur", ["r-101", "r-104"]),
    "addr-omr": ("Work", "OMR, Perungudi", ["r-101", "r-102", "r-103", "r-104", "r-105", "r-106"]),
    "addr-velachery": ("Friend", "Velachery Main Rd", list(PLACES)),
}

# Ground truth for every dish, written independently of the app's lexicons:
# nonveg, allergens, and whether it is a sweet a diabetic must not be planned blind.
NONVEG = {"Chicken Dum Biryani", "Mutton Biryani", "Chicken 65", "Egg Curry", "Family Pack Biryani",
          "Grilled Chicken Salad", "Butter Chicken", "Chicken Fried Rice",
          "Kozhi Varuval", "Eral Thokku", "Nandu Masala"}
ALLERGENS = {
    "Verkadalai Sundal": {"peanut"}, "Satay Veg Bowl": {"peanut"}, "Kaju Kari": {"tree_nut", "dairy"},
    "Paneer Biryani": {"dairy"}, "Paneer Tikka Salad": {"dairy"}, "Paneer Butter Masala": {"dairy"},
    "Dal Makhani": {"dairy"}, "Butter Naan": {"dairy", "gluten"}, "Lassi": {"dairy"}, "Raita": {"dairy"},
    "Curd Rice": {"dairy"}, "Filter Coffee": {"dairy"}, "Ghee Roast Dosa": {"dairy"}, "Kesari": {"dairy"},
    "Chilli Paneer": {"dairy"}, "Rasmalai": {"dairy"}, "Egg Curry": {"egg"},
    "Ven Pongal": {"dairy"},     # ghee
}
TOWN_DISHES = {row[0] for place in PLACES.values() for row in place[6]}
SWEETS = {"Kesari", "Rasmalai", "Lassi", "Cold Pressed Juice"}


def unsafe_for(person: dict, dish: str) -> str | None:
    """Ground-truth safety of one named dish for one person (None = fine)."""
    diet = person.get("diet", "nonveg")
    allergens = ALLERGENS.get(dish, set())
    if diet in ("veg", "vegan", "egg") and dish in NONVEG and not (diet == "egg" and dish == "Egg Curry"):
        return f"{dish} is non-veg for a {diet} eater"
    if diet == "vegan" and allergens & {"dairy", "egg"}:
        return f"{dish} has dairy/egg for a vegan"
    clash = set(person.get("allergens") or []) & allergens
    if clash:
        return f"{dish} has {sorted(clash)}"
    if "diabetes" in (person.get("medical") or []) and dish in SWEETS:
        return f"{dish} is a sweet for a diabetic"
    return None


class ScenarioTown(TownFake):
    """TownFake with three real-shaped addresses and address-dependent supply."""

    def tool(self, name, args):
        if name == "get_addresses":
            rows = [{"id": aid, "addressTag": tag, "addressLine": line, "phoneNumber": "98xxxxxx10"}
                    for aid, (tag, line, _) in ADDRESSES.items()]
            return ok({"addresses": rows, "pagination": {"page": 1, "pageSize": 10, "total": len(rows),
                                                         "totalPages": 1, "hasMore": False}})
        if name == "search_restaurants":
            reply = super().tool(name, args)
            near = ADDRESSES.get(args.get("addressId"), (None, None, list(PLACES)))[2]
            rows = reply["structuredContent"]["data"]["restaurants"] if "data" in reply.get(
                "structuredContent", {}) else None
            if rows is not None:
                reply["structuredContent"]["data"]["restaurants"] = [r for r in rows if r["id"] in near]
            return reply
        if name in ("get_restaurant_menu", "search_menu"):
            rid = args.get("restaurantId") or args.get("restaurantIdOfAddedItem")
            near = ADDRESSES.get(args.get("addressId"), (None, None, list(PLACES)))[2]
            if rid and rid not in near:
                return {"structuredContent": {"success": False, "error": {
                    "message": "This restaurant does not deliver to the selected address"}}, "isError": True}
        return super().tool(name, args)


# --------------------------------------------------------------------------- #
# Personas
# --------------------------------------------------------------------------- #
@dataclasses.dataclass
class Persona:
    key: str
    name: str
    diet: str
    weekly_budget: int
    address: str = "addr-omr"
    allergens: tuple = ()
    medical: tuple = ()
    rhythm: dict = dataclasses.field(default_factory=lambda: {"breakfast": "skip", "lunch": "order",
                                                               "dinner": "order"})
    cook: str = "never"
    # favourites the persona would tap: (meal, restaurant id, dish name)
    favourites: tuple = ()
    household: tuple = ()          # extra members: dicts with name/diet/allergens
    opens_at: dt.datetime = dt.datetime(2026, 11, 2, 8, 0)     # Mon 2 Nov 2026 08:00 IST

    def profile_body(self) -> dict:
        return {"name": self.name, "diet": self.diet, "weekly_budget": self.weekly_budget,
                "rhythm": dict(self.rhythm), "cook": self.cook, "allergens": list(self.allergens),
                "medical": list(self.medical), "favourites": [],
                "consent": {"account": True, "health": True, "learning": True}}   # W8: sign-up needs the notice agreed

    def people(self) -> list[dict]:
        me = {"name": self.name, "diet": self.diet, "allergens": list(self.allergens),
              "medical": list(self.medical)}
        return [me, *[{"medical": [], "allergens": [], **m} for m in self.household]]


SUNDAY_NOON = dt.datetime(2026, 11, 1, 12, 0)

PERSONAS = {
    "student_veg": Persona(
        "student_veg", "Karthik", "veg", 1500,
        favourites=(("lunch", "r-101", "South Indian Meals"), ("dinner", "r-101", "Masala Dosa"),
                    ("dinner", "r-105", "Veg Hakka Noodles"))),
    "salaried_nonveg": Persona(
        "salaried_nonveg", "Ravi", "nonveg", 3500,
        favourites=(("lunch", "r-102", "Chicken Dum Biryani"), ("dinner", "r-104", "Butter Chicken"),
                    ("dinner", "r-103", "Grilled Chicken Salad"))),
    "vegan_diabetic_peanut": Persona(
        "vegan_diabetic_peanut", "Meera", "vegan", 2500, address="addr-velachery",
        allergens=("peanut",), medical=("diabetes",),
        favourites=(("lunch", "r-103", "Rajma Rice Bowl"), ("dinner", "r-103", "Dal Khichdi"))),
    "household_mixed": Persona(
        "household_mixed", "Lakshmi", "veg", 6000, cook="sometimes",
        household=({"name": "Suresh", "diet": "nonveg"}, {"name": "Anu", "diet": "veg"},
                   {"name": "Dev", "diet": "vegan"}),
        favourites=(("dinner", "r-104", "Veg Thali"), ("lunch", "r-101", "South Indian Meals"))),
    "low_budget": Persona(
        "low_budget", "Priya", "veg", 300,
        favourites=(("dinner", "r-101", "Idli (2 pcs)"),)),
    "sunday_noon": Persona(
        "sunday_noon", "Arun", "veg", 2000, opens_at=SUNDAY_NOON,
        favourites=(("lunch", "r-101", "South Indian Meals"), ("dinner", "r-104", "Veg Thali"))),
    "minjur_periurban": Persona(
        "minjur_periurban", "Vijay", "veg", 2500, address="addr-minjur",
        favourites=(("lunch", "r-101", "South Indian Meals"), ("dinner", "r-104", "Dal Makhani"))),
}


# --------------------------------------------------------------------------- #
# Driving the app
# --------------------------------------------------------------------------- #
def dish_id(rid: str, dish: str) -> str:
    for n, row in enumerate(PLACES[rid][6]):
        if row[0] == dish:
            return f"{rid}-i{n}"
    raise KeyError((rid, dish))


class Week:
    """One persona's app session. Every step returns the JSON the UI would get and
    records what was shown, so invariant checks can look at all of it."""

    def __init__(self, client, fake, clock, persona: Persona):
        self.c, self.fake, self.clock, self.p = client, fake, clock, persona
        self.views: list[dict] = []            # every plan view the user saw
        self.view_people: list[int] = []       # how many of persona.people() were eating at each view
        self.members = 0                       # household members added so far
        self.accepted: list[tuple] = []        # (what, dish) the app accepted for this user
        self.log: list[str] = []

    # -- steps ---------------------------------------------------------------
    def open_app(self) -> dict:
        from test_followups import _connect
        r = self.c.post("/api/profiles", json=self.p.profile_body())
        assert r.status_code == 201, r.get_json()
        v = r.get_json()
        self.uid, self.plan = v["user"]["id"], v["plan"]["id"]
        self._saw(v)
        _connect(self.c, self.fake, uid=self.uid)
        if self.p.household:
            r = self.c.post(f"/api/user/{self.uid}/household", json={"name": f"{self.p.name}'s home"})
            assert r.status_code in (200, 201), r.get_json()
        for m in self.p.household:
            r = self.c.post(f"/api/user/{self.uid}/household/members",
                            json={"name": m["name"], "diet": m["diet"], "allergens": list(m.get("allergens", ())),
                                  "medical": list(m.get("medical", ()))})
            assert r.status_code in (200, 201), r.get_json()
            self.members += 1
        r = self.c.post(f"/api/user/{self.uid}/swiggy/address", json={"address_id": self.p.address})
        assert r.status_code == 200, r.get_json()
        self.log.append(f"opened at {self.clock.at:%a %H:%M}, address {self.p.address}")
        return v

    def set_budget(self, amount: int) -> dict:
        r = self.c.patch(f"/api/user/{self.uid}/setup", json={"weekly_budget": amount})
        assert r.status_code == 200, r.get_json()
        return self._saw(r.get_json())

    def pick_favourites(self) -> list[dict]:
        """Tap each favourite into its meal pool. Returns the replies (accepted or refused)."""
        out = []
        for meal, rid, dish in self.p.favourites:
            r = self.c.post(f"/api/user/{self.uid}/pools", json={
                "meal": meal, "restaurant_id": rid, "restaurant_name": PLACES[rid][0],
                "item_id": dish_id(rid, dish)})
            out.append({"meal": meal, "dish": dish, "status": r.status_code, "body": r.get_json()})
            if r.status_code == 200:
                self.accepted.append(("pool", dish))
        return out

    def try_pool(self, meal: str, rid: str, dish: str):
        r = self.c.post(f"/api/user/{self.uid}/pools", json={
            "meal": meal, "restaurant_id": rid, "restaurant_name": PLACES[rid][0], "item_id": dish_id(rid, dish)})
        if r.status_code == 200:
            self.accepted.append(("pool", dish))
        return r

    def plan_week(self) -> dict:
        r = self.c.post(f"/api/plan/{self.plan}/live-menus", json={})
        assert r.status_code in (200, 502), r.get_json()        # 502: Swiggy had nothing plannable
        v = r.get_json() if r.status_code == 200 else self.view()
        return self._saw(v)

    def view(self) -> dict:
        r = self.c.get(f"/api/plan/{self.plan}")
        assert r.status_code == 200, r.get_json()
        return self._saw(r.get_json())

    def swap_first_two_dinners(self) -> dict | None:
        v = self.view()
        dinners = [d["meals"]["dinner"] for d in v["grid"] if d["meals"].get("dinner")
                   and d["meals"]["dinner"]["status"] == "active"]
        if len(dinners) < 2:
            return None
        r = self.c.post(f"/api/plan/{self.plan}/swap", json={"a": dinners[0]["session_id"],
                                                             "b": dinners[1]["session_id"]})
        assert r.status_code == 200, r.get_json()
        return self._saw(r.get_json())

    def try_different(self) -> dict:
        r = self.c.post(f"/api/plan/{self.plan}/try-different", json={})
        assert r.status_code in (200, 400), r.get_json()
        return self._saw(r.get_json()) if r.status_code == 200 else self.view()

    def choose_live(self, cell: dict, rid: str, dish: str):
        r = self.c.post(f"/api/session/{cell['session_id']}/choose-live", json={
            "restaurant_id": rid, "restaurant_name": PLACES[rid][0], "item_id": dish_id(rid, dish)})
        if r.status_code == 200:
            self.accepted.append(("choose", dish))
            self._saw(r.get_json())
        return r

    def cart(self, cell: dict):
        """Preview the Swiggy cart for one planned meal, then fill it with the fingerprint."""
        pre = self.c.get(f"/api/session/{cell['session_id']}/swiggy-cart/preview")
        if pre.status_code != 200:
            return pre, None
        body = pre.get_json()
        fill = self.c.post(f"/api/session/{cell['session_id']}/swiggy-cart",
                           json={"expected_fingerprint": body.get("fingerprint")})
        return pre, fill

    def confirm(self, cell: dict):
        return self.c.post(f"/api/session/{cell['session_id']}/confirm", json={})

    def recap(self) -> dict:
        r = self.c.get(f"/api/plan/{self.plan}/recap")
        assert r.status_code == 200, r.get_json()
        return r.get_json()

    # -- reading what was shown -------------------------------------------------
    def _saw(self, v: dict) -> dict:
        if isinstance(v, dict) and "grid" in v:
            self.views.append(v)
            self.view_people.append(1 + self.members)
        return v

    @staticmethod
    def cells(v: dict, *, kind: str | None = None):
        for d in v["grid"]:
            for meal, c in d["meals"].items():
                if c and (kind is None or c["kind"] == kind):
                    yield d, meal, c

    def delivery_cells(self, v: dict | None = None):
        return [c for _, _, c in self.cells(v or self.views[-1], kind="delivery")]

    def next_open_cell(self, v: dict | None = None) -> dict | None:
        v = v or self.view()
        for d, meal, c in self.cells(v):
            if c["status"] == "active" and c["kind"] in ("delivery", "cook", "skip"):
                return c
        return None


# --------------------------------------------------------------------------- #
# Invariants
# --------------------------------------------------------------------------- #
def unsafe_in_week(week: Week) -> list[str]:
    """Every dish planned in any view, or accepted from the user, checked against ground truth
    for every eater (the account holder plus household members)."""
    bad = set()
    people = week.p.people()
    for v, n in zip(week.views, week.view_people):
        for _, _, c in week.cells(v, kind="delivery"):
            if c["item"] not in TOWN_DISHES:
                continue                     # sample-catalogue dish: not this oracle's ground truth
            for person in people[:n]:
                why = unsafe_for(person, c["item"])
                if why:
                    bad.add(f"planned: {why} ({person['name']})")
    for what, dish in week.accepted:
        for person in people[:1]:
            why = unsafe_for(person, dish)
            if why:
                bad.add(f"{what}: {why}")
    return sorted(bad)


def over_budget(v: dict) -> float:
    """Planned spend beyond the week's money, not counting the user's own pins."""
    b = v["budget"]
    pinned = sum(c.get("cost") or 0 for _, _, c in Week.cells(v) if c.get("pinned"))
    return round(b["spend"] - pinned - b["budget"], 2) if b["spend"] - b["budget"] > 0.011 else 0.0


CELEBRATION = re.compile(r"\b(planned|all set|done|sorted)\b", re.I)


def dishonest_messages(v: dict) -> list[str]:
    """Copy that claims something the plan doesn't support."""
    out = []
    counts = v.get("counts", {})
    total = sum(counts.values()) or 1
    planned = counts.get("delivery", 0) + counts.get("cook", 0)
    for h in v.get("heads_up", []):
        text = f"{h.get('title', '')} {h.get('body', '')}"
        if h.get("kind") == "saving" and "earlier" in text:
            out.append(f"surge saving claimed: {h['title']}")       # surge is a demo signal (grill #6)
        if "usual week" in text.lower() and v.get("learning", {}).get("orders", 0) == 0:
            out.append(f"'usual week' claimed with no order history: {h['body']}")
    for reason in v.get("summary_reasons") or []:
        r = reason if isinstance(reason, str) else str(reason)
        if planned / total < 0.5 and CELEBRATION.search(r) and "skip" not in r.lower():
            out.append(f"celebration over a mostly empty week: {r}")
        if "surge" in r.lower() or "rush" in r.lower():
            out.append(f"surge/rush saving in summary: {r}")
    return out


# --------------------------------------------------------------------------- #
# Fixtures (imported by the scenario test modules; a conftest.py here would shadow
# tests/conftest.py, which other test helpers import by name)
# --------------------------------------------------------------------------- #
@pytest.fixture
def town(gt, monkeypatch):
    """A frozen-clock, seeded app with Swiggy answered by the scenario neighbourhood."""
    from smartplate import config
    from smartplate.app import create_app
    from smartplate.integrations import swiggy_connect
    monkeypatch.setattr(swiggy_town, "PLACES", PLACES)       # scenario-only additions, restored after
    monkeypatch.setattr(config, "SWIGGY_PROVIDER", "live")
    fake = ScenarioTown()
    monkeypatch.setattr(swiggy_connect, "_http", fake)
    return {"client": create_app().test_client(), "fake": fake, "clock": gt}


@pytest.fixture
def run_week(town):
    """Build a Week for a persona, with the clock at the persona's first open."""
    def make(persona):
        town["clock"].set(persona.opens_at)
        return Week(town["client"], town["fake"], town["clock"], persona)
    return make


def today_answer(week: Week, v: dict) -> str | None:
    """What the app would show under "Today" right now, or None when it has nothing for
    today. Accepts any of: the plan's next meal being today, a `today` block in the plan
    view, or a Today endpoint (the new Today/Week UI may add one) with picks in it."""
    at = week.clock.at
    nxt = v.get("next_up")
    if nxt and nxt.get("date") == at.date().isoformat():
        return f"{nxt['meal']}: {nxt['cell'].get('item')}"
    block = v.get("today")
    if isinstance(block, dict) and any(block.get(k) for k in ("picks", "options", "meals", "answer", "items")):
        return "today block"
    for url in (f"/api/user/{week.uid}/today", f"/api/plan/{week.plan}/today"):
        r = week.c.get(url)
        if r.status_code == 200 and isinstance(r.get_json(), dict):
            body = r.get_json()
            if any(body.get(k) for k in ("picks", "options", "meals", "answer", "items")):
                return f"{url}"
    return None


def open_each_evening(week: Week, hour: int = 18) -> list[tuple[str, str | None]]:
    """Open the app at `hour` on each remaining day of the plan week; what Today said."""
    start = dt.date.fromisoformat(week.views[-1]["plan"]["week_start"])
    out = []
    for i in range(7):
        at = dt.datetime.combine(start + dt.timedelta(days=i), dt.time(hour, 0))
        if at < week.clock.at:
            continue
        week.clock.set(at)
        out.append((at.strftime("%a %H:%M"), today_answer(week, week.view())))
    return out
