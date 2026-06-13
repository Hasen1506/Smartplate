"""§5.2.7 — Household / group mode.

Multiple members share a plan. The hard-constraint set is the UNION of every
member's allergens/medical rules (handled in allergens.household_safe), and cost
is split per the household's policy. Planning treats the group as one consumer
whose safety profile is the strictest combination of its members.
"""
from .. import db
from . import allergens


def get(household_id: int) -> dict | None:
    with db.cursor() as cur:
        row = cur.execute("SELECT * FROM households WHERE id=?", (household_id,)).fetchone()
    return db.row_to_dict(row) if row else None


def merged_profile(members: list[dict]) -> dict:
    """A synthetic 'user' whose constraints are the strictest across members."""
    allerg, medical = set(), set()
    diet = "vegan"  # most restrictive; relax if any member eats wider
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
        "diet": "veg" if any(m.get("diet") in ("veg", "vegan") for m in members) else "nonveg",
        "_widest_diet": widest,
    }


def split_cost(household: dict, members: list[dict], total: float) -> list[dict]:
    n = max(len(members), 1)
    if household.get("split") == "by_consumption":
        # Placeholder: even split until per-member consumption tracking lands.
        pass
    share = round(total / n, 2)
    return [{"member": m["name"], "share": share} for m in members]


def safe(members: list[dict], item: dict) -> str | None:
    return allergens.household_safe(members, item)
