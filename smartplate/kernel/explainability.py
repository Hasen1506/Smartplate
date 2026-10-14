"""§5.1.4 / §3.3 — Explainability as a first-class output of every solver run.

Every decision carries a list of plain-language reasons, built from the same
signals the optimiser used. Templated → zero cost. The LLM brain can rephrase
these into prose, but the structured reasons are always the source of truth so a
support question ("why did it order this?") is answerable in seconds (§6).
"""


def reasons_for(decision: dict, context: dict) -> list[str]:
    r = []
    kind = decision["chosen_kind"]
    if kind == "skip":
        r.append(decision.get("skip_reason", "No safe option within budget and rating floor — session skipped."))
        return r
    if kind == "cook":
        dish = str(decision['item_name']).removeprefix("Cook: ")
        if decision.get("routine"):
            r.append(f"You cook this meal yourself: {dish} is a suggestion (₹{decision['cost']:.0f} in groceries).")
            return r
        if decision.get("budget_cook"):
            r.append(f"Tight Week: a ₹{decision['cost']:.0f} home-cook ({dish}) instead of skipping this meal.")
            return r
        r.append(f"Cook day: {dish} (₹{decision['cost']:.0f} in groceries), cheaper and lighter than delivery.")
        if context.get("leftover"):
            r.append(f"You logged a leftover ({context['leftover']}), so we kept this off Swiggy.")
        return r

    # delivery
    if context.get("carted"):
        r.append(f"In your Swiggy cart at the real bill's ₹{decision['cost']:.2f} — the rest of the week "
                 f"was re-balanced around it.")
    elif context.get("pinned"):
        r.append("Your pick — the rest of the week was re-balanced around it.")
    r.append(f"{decision['item_name']} from {decision['restaurant_name']} "
             f"(₹{decision['cost']:.0f} incl. delivery, rated {decision['rating']:.1f}).")
    fee = context.get("fee")
    if fee and not context.get("carted"):
        r.append(f"Delivery ₹{fee['amount']:.0f} is an estimate until a Swiggy bill from this restaurant shows "
                 f"the real fee." if fee["estimated"] else
                 f"Delivery ₹{fee['amount']:.0f}: the delivery fee from your last Swiggy bill here.")
    if context.get("discovery"):
        r.append("Something new from outside your usual places — within your variety setting.")
    if context.get("liked"):
        r.append("You liked this before.")
    r.extend(context.get("flavour") or [])
    if decision.get("substituted"):
        r.append(f"Substituted: first choice '{decision['original_name']}' "
                 f"({decision.get('sub_reason', 'unavailable')}) — picked the next best above your "
                 f"{context['rating_floor']:.1f} rating floor.")
    if context.get("sentiment") and context["sentiment"]["n"]:
        s = context["sentiment"]
        r.append(f"Recent reviews: {s['label']} ({s['n']} read).")
    if decision.get("time_shift"):
        ts = decision["time_shift"]
        r.append(f"Time-shifted to {ts['offpeak_hhmm']} to clear the surge window — saved ₹{ts['saving']:.0f}.")
    elif decision.get("surge_mult", 1.0) > 1.05:
        r.append(f"Surge ×{decision['surge_mult']:.2f} ({context.get('weather_note', 'peak demand')}) priced in.")
    if context.get("festival"):
        r.append(f"{context['festival']} — festive pick favoured.")
    if context.get("nutrition_flag"):
        r.append(context["nutrition_flag"])
    if context.get("carbon_band"):
        r.append(f"Carbon: {context['carbon_band']} (~{decision.get('carbon_kg', 0):.1f} kg CO₂e, estimate).")
    return r


def plan_summary_reasons(stats: dict) -> list[str]:
    r = [f"Total ₹{stats['spend']:.0f} of ₹{stats['budget']:.0f} budget "
         f"({stats['budget'] - stats['spend']:+.0f} headroom)."]
    if stats.get("skipped"):
        r.extend(skip_summary(stats["skipped"], stats.get("skip_reasons")))
    if stats.get("cooked"):
        r.append(f"{stats['cooked']} cook day(s) — respecting your fridge and your wallet.")
    if stats.get("surge_saved", 0) > 0:
        r.append(f"Time-shifts saved ≈ ₹{stats['surge_saved']:.0f} against surge pricing.")
    return r


# Why a meal is empty, by what actually caused it (W10 simulation: a household with one
# vegan member had 12 meals skipped "to stay within budget" with ₹5,640 unspent).
_SKIP_CAUSES = (
    ("variety", ("as often as your variety",)),
    ("money", ("budget skip", "money left", "didn't fit the budget", "daily cap", "daily limit", "of this week's",
               "fit the money")),
    ("safety", ("aren't safe", "isn't safe", "not safe", "safe for ", "safe for your rules")),
    ("you", ("you skipped", "you cook", "your fast", "fasting", "out of town", "holiday")),
    ("supply", ("no real restaurant", "on swiggy's menu", "nothing from your", "add another dish")),
)
_CAUSE_TEXT = {
    "money": "the money left doesn't cover them",
    "safety": "nothing at your places is safe for everyone eating",
    "variety": "the dishes that fit are already planned as often as your variety setting allows",
    "you": "your own choice, fast or trip",
    "supply": "nothing on the menu fits (add a dish or a place)",
    "other": "no option fits your rules",
}


def skip_cause(reason: str) -> str:
    text = (reason or "").lower()
    for cause, words in _SKIP_CAUSES:
        if any(w in text for w in words):
            return cause
    return "other"


def skip_summary(skipped: int, reasons: list[str] | None) -> list[str]:
    """One line per real cause, with its count. Without reasons, a neutral line (never a
    claim that the budget did it)."""
    if not reasons:
        return [f"{skipped} meal(s) not planned."]
    counts: dict[str, int] = {}
    for reason in reasons:
        c = skip_cause(reason)
        counts[c] = counts.get(c, 0) + 1
    return [f"{n} meal(s) skipped: {_CAUSE_TEXT[c]}." for c, n in sorted(counts.items(), key=lambda kv: -kv[1])]
