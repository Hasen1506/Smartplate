# SmartPlate — Feature Specs (Roadmap items 7 & 8 + Location)

> Detailed specs for the next build phase, beyond the two wireframes. Companion to
> `ROADMAP.md`. Three parts: **Location** (the cross-cutting primitive everything
> depends on), **Nutrition Ledger + Recipe-Fatigue** (item 7), and **Menu Sourcing &
> Ordering** (item 8).

---

## 0. Location — the cross-cutting primitive

Location is not a field; it's the **context that gates and prices everything**. Every
candidate dish, price, surge value, ETA, and even some nutrition estimates is a
function of *where* and *when*. Build it once as a primitive the other two systems
consume.

### 0.1 Data model

```
LocationContext {
  id            // 'home' | 'work' | 'transit' | 'custom:<n>'
  label         // "Home · Adyar"
  lat, lng
  radius_km     // search bound (Home 3km, Work 2km — already in Setup)
  source        // 'saved' | 'gps' | 'calendar-event' | 'manual-override'
  confidence    // gps accuracy / staleness
}
```

The Setup screen already captures **saved areas** (🏠 Home·Adyar·3km, 💼 Work·T.Nagar·2km)
and the per-meal-window grid — this primitive formalizes them.

### 0.2 Active-location resolution (per meal window)

Which area applies to a given window is resolved in priority order:

1. **Calendar life-event** (✈ travel day → transit/destination; already a power rule).
2. **Explicit user override** for that slot.
3. **Time-of-day default** (lunch → Work, dinner → Home — derivable from the windows grid).
4. Fallback to live **GPS** or the primary saved area.

This reuses the **⚡ power rules** and the **per-day × window grid** already built — no
new UI primitive, just wiring.

### 0.3 Serviceability gate (HARD constraint)

> "Serviceability" = the set of outlets that can deliver to *this point* within the
> window's max-ETA, *at the planned time*. ([Folio3](https://foodtech.folio3.com/blog/what-is-hyperlocal-food-delivery-model/))

Serviceability is a **hard pre-filter**, applied like allergens — *before* scoring.
A dish enters the candidate set only if `serviceable(location, time, eta_budget) == true`.
Outlets open/close and zones shift by time, so this is recomputed per (location, window).

### 0.4 Geo-priced everything

`price`, `delivery_fee`, `surge_multiplier`, and `eta` are all functions of
`(outlet, location, time)`. This means:
- The **budget spine** and **budget-compounding** are location-dependent.
- The **"dodge surge" dial** is a geo+time optimization.
- The **★-vs-budget conflict prompt** (already built) is often *triggered* by geo price
  drift between plan-time and order-time → re-validate at order time (see §2.6).

### 0.5 Privacy & trust

- Explicit location consent; store the **coarsest** precision that works (area, not
  pinpoint); resolve on-device where possible.
- **Anti-spoofing** for any location-gated reward (Feast Fund treats, geofenced offers):
  location can be falsified, so don't gate money on unverified GPS alone.
  ([position-falsification research](https://arxiv.org/pdf/2510.27346))

---

## 1. Item 7 — Nutrition Debt/Credit Ledger + Recipe-Fatigue Management

**Purpose:** be the *nutrition accountant* a stateless LLM can't be — track intake vs.
goals across time, carry deficits/surpluses, prescribe "repair" meals, and actively
manage variety (recipe fatigue is the #1 silent-churn driver).

### 1.1 Targets (from §6 of ROADMAP)

```
DailyTarget = goalAdjust( TDEE )           // TDEE = BMR(Mifflin–St Jeor) × activityFactor
  // flexes with calendar context: 🏋 gym day → +kcal, +protein, shift later (a power rule)
```
- Inputs collected at onboarding: weight, height, age, sex, activity, goal.
- **Macros:** protein scales with body-weight + activity (not calories); fat 20–35% of
  kcal; carbs fill the remainder (raised on training days).
- **Medical caps** (sodium, sugar, oil) are **hard limits**, not targets.

### 1.2 The ledger

```
LedgerEntry { date, window, dishId, source,
              kcal, protein, fat, carbs, fiber, sodium, sugar, micros{},
              cost, status: 'planned'|'ordered'|'eaten'|'skipped' }

RollingBalance {
  kcal:    { today, week7 }              // soft, carries
  protein: { week7_debt_or_credit }      // soft, carries → drives repair meals
  sodium/sugar: { vs_medical_cap }       // HARD — never carried as "credit"
  micros:  { day30_deficit[] }           // e.g. iron, fiber → repair suggestions
}
```

### 1.3 Behavior — nutrition compounding (mirror of budget compounding)

- **Intraday:** eat a heavy lunch → today's dinner **allowance dips** (already shown on
  the Weekly Plan as the per-day kcal bar). This spec generalizes it to **protein +
  the 7-day window** and to a small **protein ring** per day.
- **Across the week:** under-eat protein Mon → bank a **credit** → bias later-week
  candidates toward protein ("repair meal queued Fri").
- **Repair prescription:** a 30-day micro deficit (iron/fiber/…) adds a **soft weight**
  toward repair dishes — surfaced as a gentle suggestion, never a hard override.
- **Hard vs soft:** medical caps win and are never "averaged away"; targets, repair,
  and variety are soft weights in the objective.

### 1.4 Recipe-fatigue / novelty engine

```
FatigueScore(dish|cuisine|outlet) = f(recency, frequency)   // higher = more burned-out
```
- Down-weights recently-seen items (extends the **no-repeat-within-N** already in Setup).
- A **Novelty dial** (already a Setup dial: "Variety") tunes how aggressively the agent
  injects new dishes.
- **Feeds the inline spin** on the Weekly Plan: spin = "give me novelty *within*
  constraints", and each rating is a Taste-DNA training signal (closes the loop).

### 1.5 Location ties

- Dish nutrition is **per-outlet** (portion/prep vary) → estimates come from the
  location-specific menu source (§2), not a generic table.
- Repair-meal availability is gated by **serviceability** at the active location.

### 1.6 UI surfaces

- **Weekly Plan (extend what exists):** per-day kcal allowance → add a small **protein
  ring** + a **debt/credit chip** ("protein −14g wk → repair Fri").
- **Setup / new Nutrition view:** 7-day & 30-day trend bars, the debt/credit ledger,
  repair suggestions, the Novelty dial, and "you're low on X" nudges. Pairs naturally
  with the **Taste DNA** tab.
- **Cold-start honesty:** show a "still learning" state; don't fake precision before
  data exists (premature AI personalization kills trust).

### 1.7 Edge cases

- Planned ≠ eaten: support quick **"ate something else"** logging (estimate or photo).
- Untracked eat-outs: allow manual quick-add so the ledger doesn't silently drift.
- Conflicting goals: deficit + gym day → target flexes up that day, deficit resumes.
- Medical cap vs target: cap always wins.

### 1.8 Metrics

% days within target band · protein adherence · variety index · repair-meal acceptance
· **week-1 visible value** (kcal/₹ saved or served) — the retention lock.

### 1.9 Phasing

- **P1** kcal + protein ledger + per-day allowance *(partly built on Weekly Plan)*.
- **P2** 30-day micro deficits + repair suggestions.
- **P3** fatigue/novelty model feeding the spin + Taste DNA.
- **P4** actual-intake logging (confirm/photo) to close planned→eaten.

---

## 2. Item 8 — Menu Sourcing & Ordering Layer

**Purpose:** feed the optimizer a **real, location-aware candidate set** and execute
orders — while decoupling from any single provider and staying compliant.

### 2.1 The abstraction (decouple from any provider)

```
interface MenuSource {
  serviceable(location, time): boolean
  searchCandidates(location, window, constraints): Dish[]   // already allergen/diet/rating filtered
  price(dish, location, time): { price, fee, surge, eta }
  placeOrder(cart, { idempotencyKey }): OrderRef
  status(orderRef): OrderStatus
}
```
Implementations: `Swiggy`, `Zomato`, `DirectRestaurant`, `Simulated`. The optimizer only
ever sees `MenuSource` — the wireframe's "Swiggy — simulated" label is exactly this hedge.

### 2.2 Provider reality (researched — June 2026)

Swiggy/Zomato publish **partner / POS-side** APIs (menu management + order management)
aimed at **restaurants and POS vendors**, *not* a public consumer "order on my behalf"
API. ([Zomato Developer Platform](https://www.zomato.com/developer/integration/docs/overview/),
[Actowiz API guide](https://www.actowizsolutions.com/ultimate-guide-restaurant-food-delivery-apis.php))
Realistic routes, best → last resort:

1. **Partner / POS APIs** — official, but merchant-side; needs a BD relationship.
2. **Aggregator / integration providers** (Dyno APIs, Actowiz, RealDataAPI) — consolidate
   Swiggy/Zomato menu+order; pragmatic, but do ToS/licensing due-diligence.
3. **Deep-link / cart hand-off** — we optimize, then hand a **pre-filled cart** to the
   user's provider app to confirm. Sidesteps consumer-order API *and* order liability.
   Best near-term default.
4. **User-authorized automation** — user connects their own account; act on their behalf
   with explicit consent + **safe-retry / no-double-order**.
5. **Scraping** — last resort, **read-only menu/price discovery only, never ordering**;
   fragile, ToS/legal risk (see ROADMAP §8).

### 2.3 Location & serviceability (the gate)

- **Per-window active location** (§0.2) drives `searchCandidates` (lunch=Work, dinner=Home,
  travel-day=transit) — reuses saved areas + calendar power rules already in Setup.
- **`serviceable()` is a hard pre-filter** (§0.3): no serviceable outlet → fall to the
  **cook / skip** relief valves (which need no provider at all).
- **Radius/zone** (3km/2km already set) bounds search; geofencing for location-gated
  treats; serviceability shifts by time-of-day.

### 2.4 Pricing, surge & freshness

- `price/fee/surge/eta` keyed on `(outlet, location, time)`; feeds budget compounding +
  the "dodge surge" dial.
- **Cache** menus/prices per `(outlet, location, time-bucket)` with a TTL; **re-validate
  at order time**. Price drift between plan-time and order-time is the common trigger for
  the **★-vs-budget conflict prompt** (already built) → run Trim-to-fit or ask.

### 2.5 Order execution & autonomy

- **Idempotency keys + safe-retry + no-double-order** (the wireframe's "🛡 safe-retry").
  Double-order rate must be ≈ 0.
- Honors the **autonomy levels** already in Setup: *auto-order* / *propose & approve* /
  *edit each*. Status via webhooks/polling.

### 2.6 Fallback ladder (degrade gracefully)

```
partner API  →  aggregator  →  deep-link hand-off  →  simulated/manual
```
At every rung the **cook / skip** options remain available, so the plan is never blocked
by provider/serviceability gaps.

### 2.7 Multi-provider arbitration (later)

When 2+ providers are serviceable, pick the **cheapest serviceable option that clears the
★ floor within the ETA budget** — turning provider choice into another optimizer input.

### 2.8 Privacy & trust

Per §0.5 — minimize location precision, consent-gated account linking, anti-spoof any
location-gated reward.

### 2.9 Metrics

Serviceable-candidate coverage · price-drift at order time · order success rate ·
**double-order rate (≈0)** · ETA accuracy.

### 2.10 Phasing

- **P1** Simulated source + **serviceability gate** + **deep-link hand-off**.
- **P2** Aggregator/partner **read** for real menus/prices (location-aware).
- **P3** Authorized **ordering** + safe-retry + autonomy levels.
- **P4** Multi-provider arbitration (cheapest serviceable across providers).

---

## Sources

- Zomato Developer Platform (POS/menu/order APIs) — https://www.zomato.com/developer/integration/docs/overview/
- Actowiz — Zomato/Swiggy/EazyDiner API guide — https://www.actowizsolutions.com/ultimate-guide-restaurant-food-delivery-apis.php
- Folio3 — Hyperlocal food delivery model (2026) / serviceability — https://foodtech.folio3.com/blog/what-is-hyperlocal-food-delivery-model/
- Medium (Pattnaik) — Location intelligence in food delivery — https://anubpattnaik.medium.com/location-intelligence-in-food-delivery-e142494be584
- CitrusBits — Geofencing & location intelligence for restaurants — https://citrusbits.com/geofencing-and-location-intelligence-how-restaurants-can-benefit/
- arXiv — Position-falsification attacks on location-based services — https://arxiv.org/pdf/2510.27346
