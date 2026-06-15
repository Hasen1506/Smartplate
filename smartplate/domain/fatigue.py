"""Recipe-fatigue / novelty model — the real variety mechanism (SPEC.md §1.4).

The `≤ 2 repeats/dish` cap in the optimiser is a crude *floor on diversity*: it
stops the same dish three times but says nothing about how novel a week *feels*
or whether the agent is injecting fresh picks. This module models that properly:

  FatigueScore(dish) = f(recency, frequency)      # higher = more burned-out
  Novelty(dish)      = 1 - familiarity            # higher = fresher to this user

and sorts candidates into the **familiar** and **novel** pools the spin and the
budget recommender draw from. A **variety level** turns "mostly the usual but I
want some new things" into a target count of novel picks per week.

Cold-start honesty (ROADMAP §7): with no personal order history we proxy
familiarity with popularity (a popular dish is a safe "usual"), and say so —
the model sharpens as real history arrives.
"""

# How aggressively to inject novelty. A level maps to the *fraction* of planned
# sessions that should land on a novel pick. The user sets this with one dial.
VARIETY_LEVELS = {
    "usual": 0.0,        # all familiar — no surprises
    "light": 0.2,        # mostly usual, 1-2 new things a week
    "mixed": 0.45,       # an even-ish split
    "adventurous": 0.8,  # lean hard into new dishes
}
DEFAULT_LEVEL = "light"

# A dish at/above this novelty score is "novel"; below it is "familiar".
NOVEL_CUTOFF = 0.5


def familiarity(item: dict, history: dict | None = None) -> float:
    """0..1 — how well-trodden this dish is for the user.

    With history (``{item_id: {"count": n, "last_seen_day": d}}``) recency and
    frequency drive it; without it we fall back to popularity (cold-start proxy).
    """
    pop = float(item.get("popularity", 0.5))
    if not history:
        return max(0.0, min(1.0, pop))
    h = history.get(item.get("id"))
    if not h:
        # never ordered by this user → unfamiliar, lightly informed by popularity
        return round(0.25 * pop, 4)
    count = h.get("count", 0)
    freq = min(1.0, count / 4.0)                 # 4+ orders ⇒ fully familiar
    recency = 1.0 / (1.0 + h.get("days_since", 30) / 7.0)  # seen recently ⇒ more familiar
    return round(max(0.0, min(1.0, 0.6 * freq + 0.4 * recency)), 4)


def novelty(item: dict, history: dict | None = None) -> float:
    return round(1.0 - familiarity(item, history), 4)


def fatigue(item: dict, history: dict | None = None) -> float:
    """How burned-out the user is on this dish (inverse of novelty)."""
    return familiarity(item, history)


def is_novel(item: dict, history: dict | None = None) -> bool:
    return novelty(item, history) >= NOVEL_CUTOFF


def pools(items: list[dict], history: dict | None = None) -> dict:
    """Split candidates into familiar/novel, each sorted best-first within kind.

    'familiar' is sorted most-familiar-first (the safe usual); 'novel' is sorted
    most-novel-first (the freshest experiment). The spin (↻) walks the right pool
    for the user's variety level; the recommender prices the two baskets.
    """
    fam, nov = [], []
    for it in items:
        n = novelty(it, history)
        (nov if n >= NOVEL_CUTOFF else fam).append((n, it))
    fam.sort(key=lambda t: t[0])               # least novel = most familiar first
    nov.sort(key=lambda t: -t[0])              # most novel first
    return {"familiar": [it for _, it in fam], "novel": [it for _, it in nov]}


def target_novel_count(level: str, n_sessions: int) -> int:
    """How many of the week's sessions should be a novel pick, for this level."""
    frac = VARIETY_LEVELS.get(level, VARIETY_LEVELS[DEFAULT_LEVEL])
    return int(round(frac * max(0, n_sessions)))


def variety_pref(user: dict) -> str:
    """The user's variety level, read from their stored prefs (default 'light').

    Stored in the nutrition_targets blob so no schema change is needed; absent ⇒
    the honest default rather than an assumption.
    """
    nt = user.get("nutrition_targets") or {}
    lvl = nt.get("variety")
    return lvl if lvl in VARIETY_LEVELS else DEFAULT_LEVEL
