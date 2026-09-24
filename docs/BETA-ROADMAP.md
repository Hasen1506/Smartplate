# Product exploration after the invite beta

The current release concentrates on a trustworthy loop: choose *when* to eat,
discover *real* serviceable restaurants, understand why a plan was chosen,
then review and order in Swiggy. These are ranked opportunities, not features
claimed to exist today. Each idea names the failure that would make it unsafe
or unhelpful, so it can be tested before it reaches the main experience.

## Next: make the live planner dependable

1. **Provider freshness ledger.** Give every restaurant and dish a fetched-at
   timestamp and refresh selected menus before each planning run. When a dish
   disappears, preserve the old decision as history and mark it unavailable
   instead of silently substituting. Test stale menus, closed restaurants,
   401/revocation, 429/backoff, pagination and duplicate provider IDs.
2. **Price confidence bands.** Record listed price, fee/tax uncertainty and
   price age separately. Solve against a conservative upper bound once Swiggy
   exposes charges; until then, show an item-price-only range and reserve a
   user-defined cushion. Never call the current listed subtotal a checkout total.
3. **Safety evidence registry.** Nutrition and allergen attributes should carry
   source, timestamp and confidence. Missing evidence stays unknown; a user's
   own correction does not become a restaurant guarantee. Route allergen and
   medical restrictions to a clear manual verification flow, with no
   algorithmic “safe” badge until the evidence exists.
4. **Feasibility diagnosis.** When a week collapses into skips, report the
   binding reason per meal: no serviceable restaurant, allergy uncertainty,
   rating floor, budget, cook cap or repeat cap. Suggest the smallest *soft*
   preference change that helps, while never proposing to relax a safety rule.
5. **Planner performance budget.** Cap candidate count by a diverse,
   constraint-aware shortlist, not a fixed first-six slice. Keep at least
   enough alternatives to satisfy weekly repeat limits. Measure solve time
   and fall back to an explicit partial plan after a time limit.
6. **Address and account lifecycle.** Revoke provider tokens on disconnect,
   purge address-scoped caches, and offer account/data export and deletion.
   Keep a separate audit of who changed a plan and when without storing
   address PII or raw access tokens in logs.

## Then: make the week feel personal

7. **Meal-window defaults from behavior.** Let people set just two or three
   useful windows first, then learn their routine from accepted edits. Make
   inferred defaults visible and reversible. Avoid silently moving a meal.
8. **Leftover inventory with decay.** A cooked or ordered dish can generate
   portions, expiry and reheating constraints. The planner consumes portions
   only after the user confirms them; “I ate elsewhere” releases that slot.
9. **Household fairness.** Model per-member budget, dietary constraints and
   appetite rather than merging everyone into one profile. Show who benefits
   or pays more and reject a shared meal if any member's hard safety rule fails.
10. **Budget horizon.** Let users pick day/week/month caps, see committed vs
    possible spend, and roll underspend forward only by explicit choice. Test
    timezone boundaries and changes to a budget midweek.
11. **Taste DNA with exploration limits.** Learn cuisines and dishes from
    accepts/skips, then add one bounded novelty slot. Avoid filter bubbles by
    letting users reset or inspect what the model inferred.
12. **A useful food journal.** Intake entries distinguish measured, estimated
    and unknown nutrition. Reconciliation after gaps asks what happened rather
    than treating silence as zero food.
13. **One-action repair.** “I'm late,” “restaurant closed,” “I already cooked,”
    and “less money left” should each produce a diff of changed meals, spend
    and constraints before applying it. Preserve user-fixed slots.

## Larger bets to validate before building

14. **Opt-in calendar and travel context.** Read free/busy or location only
    with explicit permission, then use it to suggest meal windows. A revoked
    calendar connection must remove future assumptions without damaging plans.
15. **Weather and festival signals from live sources.** Treat these as soft
    context, not a fabricated price multiplier. Compare recommendations with
    and without the signal to prove that it improves decisions.
16. **Restaurant reliability, ethically measured.** Track only user-observed
    delays, cancellations and menu drift. Avoid penalizing new or small
    restaurants from thin data; separate provider availability from quality.
17. **Swiggy-native cart handoff.** If an official safe cart flow becomes
    available, quote a fresh cart, handle variants/add-ons, and require a new
    confirmation when totals or substitutions change. Build reconciliation
    and idempotency before any payment or order mutation.
18. **Group planning with split consent.** Each member approves their own
    spend and restrictions. No member's account can authorize another person's
    Swiggy payment.
19. **Experimentable “why this meal?” cards.** Compare concise reasons,
    trade-off visuals and counterfactual swaps. Measure whether users trust and
    understand decisions, not just whether they click a button.
20. **Adaptive model without an LLM dependency.** Learn weights from explicit
    choices and reversals, keep deterministic constraint enforcement, and use
    generated text only where it adds value. Evaluate against the fixed solver
    before allowing any model to change the week.

Graduation rule: a bet enters the default experience only after its data
contract, privacy boundary, failure behavior and user value have been tested.
