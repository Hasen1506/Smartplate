# SmartPlate product strategy and production gates

> Decision memo, September 2026. Provider capabilities in this document are
> intentionally hypotheses until the whitelisted Swiggy MCP manifest and the
> applicable automation/payment terms are reviewed.

## The product, in one sentence

**SmartPlate is a household food autopilot that keeps a weekly spending ceiling
while balancing convenience, nutrition, inventory and taste—and keeps every
purchase within an explicit user-authored policy.**

Ordering is an execution channel, not the moat. The defensible loop is persistent:
observe context → optimise the week → reserve spend → execute safely → reconcile
what was ordered and eaten → learn from every exception. A delivery app or chatbot
can suggest a meal; SmartPlate should prove that it handled the whole week.

## Three outcomes worth paying for

1. **“I will not accidentally overspend on food.”** Show committed, pending,
   settled, refunded and safely available amounts, including fees and price drift.
2. **“Dinner is handled without another decision.”** Generate the default, then
   make swap, cook, skip and rescue actions one-tap and automatically rebalance the
   rest of the week.
3. **“My household eats better without constant logging.”** Merge hard safety
   rules, learn each member's preferences, use leftovers before expiry and ask for
   lightweight delivered/eaten confirmation.

The home screen should lead with verified outcomes—not a catalogue of features:
rupees saved against a documented trailing baseline, decisions avoided, meals
handled, waste avoided and target adherence. Never call promotional credit or an
invented list-price difference “savings.”

## Differentiation roadmap

### P0: trust and the closed loop

- Make the **SmartPlate Promise** explicit: never exceed a hard spend ceiling,
  violate a declared hard exclusion, place a duplicate order or silently change a
  reviewed order. Always explain changes.
- Model the real lifecycle: `planned → approval_required → approved → cart_created
  → payment_authorized → submitted → accepted → delivered → eaten → reconciled`.
- Record planned, quoted, authorised, charged and refunded totals. Budget and
  nutrition claims are hypothetical without planned-to-actual reconciliation.
- Offer `recommend only`, `prepare cart`, and—only when scopes and terms expressly
  permit it—`rules-based autopilot`. Prepare-cart should be the launch default.
- Give every automation policy per-order/day/week ceilings, price-drift and ETA
  limits, approved locations/merchants, substitution rules, quiet hours, expiry,
  notifications and a kill switch.

### P1: pantry, rescue and household intelligence

- Compare delivery with the true cost of cooking from current pantry quantities;
  optimise explicitly for ingredients nearing expiry and measure waste avoided.
- Rescue a meal when a meeting, rain, location, outlet, price, workout or household
  attendance changes—without breaking the weekly cap.
- Learn from accepted first choices, swap direction, repeated skips, checkout price
  sensitivity, ratings and waste. Let users inspect, correct, export and delete the
  learned model.
- Add household fairness: identify whom each option satisfies, explain compromises,
  split components for conflicting diets, and rotate whose soft preference wins.

### P2: calibrated nutrition and distribution

- Attach source and confidence to nutrition values. Prefer weekly patterns and
  robust measures (protein, fibre, vegetables) over false per-meal precision.
- Never imply cross-contamination safety from menu metadata alone. Medical
  recommendations require appropriate expert and regulatory review.
- Add constraint-aware community templates only after core retention works; all
  cloned plans must be remapped to live serviceability, prices and user rules.

## Swiggy MCP integration contract

Do not infer live methods from the simulator. Before implementing a provider, record:

- server identity/version, tool schemas, read/write classification and scopes;
- whether workflows are consumer- or merchant-facing;
- location/serviceability semantics, menu freshness and quote expiry;
- cart, order, cancellation, refund and payment lifecycles;
- provider idempotency, ambiguous-submit reconciliation, rate limits and events;
- sandbox support, retention rules and explicitly permitted automation patterns.

Roll out in gates: **read-only discovery → official cart handoff with fresh quote →
user-confirmed submission → unattended policy automation**, with each gate enabled
only when both technical scope and contractual permission exist. Tool availability,
account consent, OAuth scope and contractual permission are four separate checks.

Never scrape private endpoints, replay tokens, reverse-engineer app authentication,
bypass confirmations or anti-bot controls, conceal automation, fabricate offer
eligibility, or treat an exposed tool as blanket permission.

## Swiggy Money: the useful integration hypothesis

Its exact current nature and allowed operations are unverified. If the MCP and terms
support it, use Swiggy Money for **budget assurance**, not a decorative payment button:

```text
weekly cap
  = reserved for plans + authorised pending + settled + safely available
  - refunds in flight
```

Keep promotional/restricted balances distinct from cash; never auto-top-up by
default; link every reservation, authorisation, settlement and refund to a user
consent reference, plan, order and idempotency key; release stale reservations; and
show the funding source before confirmation. Recurring mandates must be separately
revocable. Do not split or sequence payments unless explicitly supported.

## Revenue and viability

Suggested pricing is an experiment, not market fact:

| Stream | Offer | Why it fits |
|---|---|---|
| Subscription | Free planning; Plus ₹149–249/month; Household ₹299–499/month | Aligns recommendations with users rather than commissions |
| B2B2C | Employer meal allowance and shift-work orchestration | Predictable per-member revenue and clear budget outcome |
| Approved referral | Provider/grocery/meal-kit programmes | Monetises execution, but must never override ranking rules |
| Premium service | Dietitian-reviewed targets or household onboarding | High willingness to pay, with added operational/regulatory cost |

Avoid hidden pay-to-rank, selling health/behaviour data, unverifiable savings-share,
invisible menu mark-ups and food-focused lending. Solver compute may be inexpensive;
provider data, notifications, support, refunds, fraud and licensed nutrition are not.
At ₹199/month, test for at least ₹600–₹1,000/month of visible recurring value from
verified savings, reduced waste and time—not a fabricated counterfactual.

## Six-week validation before broad autonomy

Pilot with 30–50 varied households. Measure first valid plan time, verified week-one
savings, plan acceptance, swap success, rescue use, planned-to-ordered and
ordered-to-eaten conversion, cap violations, ambiguous/duplicate orders, safety
incidents, weekly retention, willingness to pay and support minutes per order.

Pause the autonomy thesis if users will not connect transaction data, plans require
frequent correction, menu freshness is unreliable, verified value does not exceed
the likely subscription price by a meaningful multiple, or support cost remains
structurally high. Duplicate orders and hard-safety violations have a target of zero.

## Production gates visible in this repository

The new checkout fingerprint and spend ceiling are necessary consent controls, but
not payment authorisation. Before connecting real commerce, add authentication and
plan ownership, strict request/enum validation, restricted CORS, idempotent receipt
generation, durable order events/reconciliation, observability and provider sandbox
contract tests. Retain simulated mode until all gates pass.

