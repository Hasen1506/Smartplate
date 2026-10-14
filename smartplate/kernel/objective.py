"""The planner's objective, written down once (docs/planner-objective.md).

Ziggy chooses meals in a strict order of priority. A lower rule never buys a
higher one: no amount of "favourite" pays for an unsafe dish, and no saving pays
for a skipped meal the money could cover.

  1. SAFE        never break a diet, allergy or medical rule of anyone eating
                 (unknown counts as unsafe). A filter, applied before anything else.
  2. MONEY       stay inside today's / this week's money, fees included. A hard limit.
  3. FAVOURITES  only from what the user allowed: their favourite dishes and places,
                 plus cooking or new places (≥ their min rating) when the mode they
                 picked says so. Within that, the higher favourite score wins.
  4. NO SILENT SKIPS  fill every meal they said they order. A cheaper favourite beats
                 a skip; a skip happens only when nothing allowed fits, and says why.
  5. CHEAPEST    among picks that are equally liked, the cheaper one.
  then VARIETY   a dish at most `max_repeat` times a week and once a day.

Rule 3 decides *what may be planned*; rule 4 is ranked above the favourite *score*
(the founder's "a cheaper favourite beats a skip"), so the comparison key of a plan is

    (meals filled ↑, favourite score ↑, cost ↓, repeats ↓)

Favourite score of one dish for one meal: a few named weights, each term in 0..1.
"""
from __future__ import annotations

# --- favourite score: the only weights there are --------------------------------------
W_RATING = 0.35        # the dish's rating out of 5 (the restaurant's when the dish has none)
W_TIMES = 0.30         # how often they ordered it: n / (n + 2), so 1×≈0.33, 4×≈0.67
W_SLOT = 0.20          # right for this meal (a tiffin dish at breakfast, a plate at lunch)
W_RECENCY = 0.15       # ordered lately: 1 today, fading to 0 over RECENCY_DAYS
POOL_BONUS = 0.50      # they put this dish in this meal's list themselves
PLACE_BONUS = 0.20     # a dish at one of their places
LIKED_BONUS = 0.20     # they gave it a thumbs-up
DIET_BONUS = 0.15      # a non-veg eater gets a non-veg dish (usab #11: a non-veg week was all veg)
RECENCY_DAYS = 30
# Scores closer than this count as "equally liked", so rule 5 (cheaper) decides.
SCORE_STEP = 0.05

# Tier of a candidate: where it comes from (rule 3).
POOL, PLACE, NEW, COOK = "pool", "place", "new", "cook"


def times_term(n: int) -> float:
    n = max(0, int(n or 0))
    return n / (n + 2)


def recency_term(days_since: int | None) -> float:
    if days_since is None:
        return 0.0
    return max(0.0, 1.0 - max(0, days_since) / RECENCY_DAYS)


def slot_term(item: dict, meal: str, pooled: bool = False) -> float:
    """1 when the dish suits this meal, 0.5 when it is only acceptable, 0 when it doesn't."""
    from ..domain import timing
    if not timing.slot_suitability(item, meal, favourited_for_slot=pooled):
        return 0.0
    tags = item.get("tags") or []
    if pooled:
        return 1.0
    if meal == "breakfast":
        return 1.0 if "breakfast" in tags else 0.5
    if meal == "lunch":
        return 0.5 if "breakfast" in tags else (1.0 if "main" in tags else 0.75)
    return 0.5 if "breakfast" in tags else 1.0


def favourite_score(item: dict, meal: str, *, tier: str, history: dict | None = None,
                    liked: bool = False, nonveg_eater: bool = False) -> tuple[float, dict]:
    """(score, parts): how much this person likes having `item` for `meal`."""
    h = (history or {}).get(item.get("id")) or {}
    rating = item.get("item_rating") or item.get("restaurant_rating") or 0.0
    parts = {
        "rating": round(W_RATING * min(5.0, float(rating)) / 5.0, 4),
        "times": round(W_TIMES * times_term(h.get("count", 0)), 4),
        "slot": round(W_SLOT * slot_term(item, meal, pooled=tier == POOL), 4),
        "recency": round(W_RECENCY * recency_term(h.get("days_since") if h else None), 4),
        "pool": POOL_BONUS if tier == POOL else 0.0,
        "place": PLACE_BONUS if tier in (POOL, PLACE) else 0.0,
        "liked": LIKED_BONUS if liked else 0.0,
        "diet": DIET_BONUS if (nonveg_eater and not item.get("veg", 1)) else 0.0,
    }
    return round(sum(parts.values()), 4), parts


def quantize(score: float) -> int:
    """Scores in the same SCORE_STEP band compare equal (then the cheaper one wins)."""
    return int(round(score / SCORE_STEP))


def plan_key(filled: int, score_q: int, cost: float, repeats: int = 0) -> tuple:
    """Bigger is better. The lexicographic order of rules 4, 3 (score), 5 and variety."""
    return (filled, score_q, -round(cost, 2), -repeats)


# --- plain words -------------------------------------------------------------------------
def why_pick(pick: dict, *, money_word: str = "this week's") -> str:
    """One sentence on why this dish (or this skip) was chosen."""
    if pick["kind"] == "skip":
        return pick.get("reason") or "Skipped."
    cost = pick["cost"]
    if pick["kind"] == "cook":
        return f"Cook {pick['name']} at home (≈₹{cost:.0f} in groceries): you allowed cook days."
    tier, parts = pick.get("tier"), pick.get("parts") or {}
    bits = []
    if parts.get("times"):
        bits.append(f"you've ordered it {pick.get('times_ordered', 0)}×")
    if parts.get("liked"):
        bits.append("you liked it")
    rating = pick.get("rating")
    if rating:
        bits.append(f"{rating:.1f}★")
    detail = f" ({', '.join(bits)})" if bits else ""
    split = ""
    if len(pick.get("lines") or []) > 1:
        split = " Split so everyone eats safely: " + "; ".join(
            f"{ln['qty']}× {ln['name']} for {', '.join(ln['for'])}" for ln in pick["lines"]) + "."
    if tier == POOL:
        lead = f"Your {pick['meal']} favourite{detail}"
    elif tier == PLACE and pick.get("pool_unsafe_for"):
        lead = (f"From {pick['restaurant']}, one of your places{detail} — your usual {pick['meal']} "
                f"isn't safe for {pick['pool_unsafe_for']}")
    elif tier == PLACE:
        lead = (f"From {pick['restaurant']}, one of your places{detail}"
                + (" — cheaper than your usual, so this meal isn't skipped" if pick.get("cheaper_than_pool") else ""))
    else:
        lead = f"New place {pick['restaurant']}{detail}, above your {pick.get('min_rating', 0):.1f}★ rule"
    return f"{lead}; ₹{cost:.0f} fits {money_word} money.{split}"
