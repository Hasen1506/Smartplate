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
        r.append(f"Cook day: {decision['item_name']} (₹{decision['cost']:.0f}) — cheaper and lighter than delivery.")
        if context.get("leftover"):
            r.append(f"You logged a leftover ({context['leftover']}), so we kept this off Swiggy.")
        return r

    # delivery
    r.append(f"Ordered {decision['item_name']} from {decision['restaurant_name']} "
             f"(₹{decision['cost']:.0f}, rated {decision['rating']:.1f}).")
    if decision.get("substituted"):
        r.append(f"Substituted: first choice '{decision['original_name']}' "
                 f"({decision.get('sub_reason', 'unavailable')}) — picked the next best above your "
                 f"{context['rating_floor']:.1f} rating floor.")
    if context.get("sentiment") and context["sentiment"]["n"]:
        s = context["sentiment"]
        r.append(f"Reviews look {s['label']} (sentiment {s['score']:+.2f} over {s['n']} notes).")
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
        r.append(f"{stats['skipped']} session(s) skipped to stay within budget / rating floor.")
    if stats.get("cooked"):
        r.append(f"{stats['cooked']} cook day(s) — respecting your fridge and your wallet.")
    if stats.get("surge_saved", 0) > 0:
        r.append(f"Time-shifts saved ≈ ₹{stats['surge_saved']:.0f} against surge pricing.")
    return r
