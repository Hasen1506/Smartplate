# SmartPlate — Product Roadmap & Design Notes

> Living document. Captures the product direction, the research behind it, the open
> design questions, and the build order. Derived from the design chats + the
> wireframes in `project/`. Update as decisions land.

---

## 0. The thesis (read this first)

Almost every open question reduces to one tension: **autonomy vs. control.** Users
want "Approve Day" autopilot (no decision fatigue) *and* fine-grained control ("today
I want a heavy lunch", "shuffle just this one", "let me see the menu myself"). These
are not in conflict if we hold one principle:

> **The agent decides by default. Every decision is a one-tap, reversible, in-place
> override.** Control is not a separate mode — it's friction-free escape hatches
> layered on top of autopilot.

And the positioning thesis:

> We are **not** "AI that orders food" (Google/Kroger already ship that). We are
> **a stateful optimizer that runs your food like a budget you never think about** —
> across time, money, nutrition, and kitchen inventory. The moat is the personal
> model + cross-time optimization, not the ordering.

---

## 1. Why use this over just asking an LLM to order food (the value prop)

A chatbot can place an order; it cannot hold **state across time**. That gap is the
entire moat.

- **Stateless chatbot vs. stateful optimizer.** Raw data is *not* a moat on its own;
  the moat is a self-improving loop + switching costs from accumulated personal state
  (a16z, "The Empty Promise of Data Moats"). We treat the week as a constrained
  optimization over budget, nutrition, surge, allergens, and inventory *at once* — a
  solver problem, not a chat turn.
- **Three concrete edges:**
  1. **Supply-chain / kitchen-inventory edge** — "don't order X, your spinach + eggs
     expire in 2 days." Crowded as a *standalone* feature (KitchenPal, Pantry Check,
     Portions Master), but **no pantry app also orders, budgets, and optimizes**. We
     close the loop: what's expiring → cook vs. order → placed.
  2. **Nutrition accountant** — track under-consumed micros over 30 days, prescribe
     "repair meals." Requires a persistent ledger a stateless LLM can't keep.
  3. **Budget compounding** — treat the food budget as a rolling portfolio; underspend
     Monday → surplus reallocates to Friday. Novel framing on top of our budget spine.
- **The honest threat:** "AI plans your week + builds a cart + orders" is table stakes
  by 2026 (Google/Gemini, Kroger). Defensibility = personal model + cross-time
  optimization + a loop that compounds, NOT the ordering action.

**Tagline test:** not *"AI that orders your food"* but
*"the agent that runs your food like a budget you never have to think about."*

---

## 2. Resolving the interaction questions

| Question / doubt | Design answer |
|---|---|
| Per-day timeframes are tedious to set, but aren't the same every day | Set a **default weekly rhythm once** (the existing grid = template); agent **learns deviations** and only prompts on *exceptions*. Plan is rolling, never locked. |
| User dislikes one suggestion / wants to shuffle only that / pick himself | **Inline spin (↻) on each meal card** = "another pick, same rules" (NOT a separate slot-machine screen). Tapping the card = **browse the ranked alternatives the solver already computed** → satisfies "shuffle this" + "let me choose from the menu". |
| "Today I want a heavy lunch" | Let them **bump a meal up**; agent **silently re-balances the rest of the day** — dinner's allowance dips, shown as a **draining remaining-allowance meter**. Generous, not punishing. |
| Cook day ↔ order day switch | Per-meal **mode toggle** (⌂ cook / ▢ order / ⊘ skip); flipping re-optimizes that slot. |
| Picks ★4.5 but budget insufficient (constraint conflict) | Never silently fail. **Surface the trade-off:** "To keep ★4.5 here I need ₹60 more — pull from Friday's surplus, or drop to ★4.3?" Budget-compounding at the point of friction. |

> Retention research is blunt: **broken meal-swapping is the #1 disguised churn driver.**
> If a swap takes >1–2 taps or doesn't auto-update everything downstream, users
> disengage. The spin/swap interaction is the retention spine, not a nice-to-have.

---

## 3. Programmability = life context, not more forms

Gym days, travel days, weekends all collapse into **calendar- & life-event-driven
rules**, reusing the existing **⚡ Power Rules** (`WHEN scope → THEN nudge`) with new
event-type triggers:

- **Gym/session days** → +protein, +~300 kcal, eat later.
- **Travel days** → skip or grab-and-go near `{location}`.
- **Weekends** → looser budget band, more novelty, Feast Fund treat.

Keep the form small; research shows manual-logging-heavy apps lose to ones that adapt
automatically. Calendar integration (`.ics` + event types: gym / travel / busy) is the
mechanism.

---

## 4. Taste DNA — make the moat *visible* (lives in Setup)

Our most defensible asset, so it should *look* like a living, personal artifact:

- **Flavor fingerprint** — radial/spider chart across ~6 axes (spice tolerance ·
  cuisine spread · novelty appetite · price sensitivity · protein lean · cook-vs-order
  ratio) that visibly **morphs over time**.
- **"This is uncannily you" feed** — recent picks annotated with *why* ("you rate
  Chettinad 20% higher on rainy days").
- **Trainer counter** — "built from 142 orders, 38 skips, 11 spins"; every rating =
  "+1 signal" so the user *sees the moat compounding* (Spotify-Wrapped energy, not a
  settings panel).

---

## 5. The surprise / "don't reveal the order" mechanic

Psychologically sound: **variable rewards drive 2–3× retention vs. fixed ones** (Nir
Eyal's Hook Model; Skinner). The hidden-order-with-explicit-reveal is a clean
variable-reward loop: agent saves ₹ → fund fills → surprise treat queued but hidden →
tap to reveal → rate → model sharpens.

**Guardrails so it stays delight, not dark-pattern:**
- Always inside hard rules (allergens / budget / diet never gambled).
- Reveal is **opt-in and reversible** ("Surprise me" vs. "show me first").

---

## 6. Nutrition requirements — what they actually depend on

BMI is a *screening ratio*, **not** the driver of nutritional need. The real chain:

1. **BMR** (Basal Metabolic Rate) via **Mifflin–St Jeor**: uses **weight, height, age,
   biological sex**. (BMI is not used here.)
2. **TDEE** = BMR × **activity factor** (sedentary 1.2 → very active ~1.9). This is
   where **gym/session days** raise the number.
3. **Goal adjustment**: deficit (lose), maintenance, or surplus (gain).
4. **Macros:**
   - **Protein** scales with **body weight + activity/goal** (~0.8 g/kg sedentary →
     1.6–2.2 g/kg for muscle gain), *not* calories.
   - **Fat** ~20–35% of calories; **carbs** fill the remainder (raised on training days).
5. **Micros & limits**: age, sex, **pregnancy/lactation**, and **medical conditions**
   (sodium for BP, sugar cap for diabetes, etc.) — these become **hard rules**.
6. **Context modifiers**: activity that day, climate/hydration, sleep/recovery.

**Implication for SmartPlate:** the onboarding should collect weight/height/age/sex +
activity + goal (→ TDEE), then the **daily target flexes with calendar context** (gym
day = higher target + protein bias). This is also the basis for the **nutrition debt/
credit ledger** (§7).

---

## 7. Deeper ideas (extend the roadmap)

- **Recipe-fatigue as a tracked metric.** #1 silent-churn cause is "got bored of the
  recipes" (~week 6). Treat variety as a measured feature: a **novelty dial** + agent
  proactively injecting variety. Spin + Feast Fund are the anti-boredom engine.
- **Nutrition debt/credit ledger.** Extend budget-compounding to macros: "you're 14g
  protein in debt this week → here's a repair meal."
- **Household / shared Taste DNA.** A real network effect (the durable moat kind):
  model improves per member, harder to leave.
- **Cold-start honesty.** Don't fake AI personalization on day 1 (feels random, kills
  trust). Sensible defaults + fast manual control first; *earn* and *show* the DNA
  filling over weeks.
- **Time-to-value < 7 days.** Plan must show visible ₹/time saved in week 1 ("saved ₹30
  on surge / banked a cook day") — make it the hero, not a footnote.

---

## 8. Backend / integration risks

### Swiggy access — what if they don't open an API to us?
- **Scraping is a poor primary strategy.** It typically violates ToS, breaks
  constantly against anti-bot defenses, and carries legal exposure — not something to
  build the core product on. Treat it, at most, as a fragile read-only fallback for
  **menu/price discovery** (never for placing orders), and prefer compliant routes:
  - **Official partner / merchant APIs** (Swiggy/Zomato partner programs) — the right
    long-term path; pursue a BD/partnership conversation.
  - **Deep links / cart hand-off** — we optimize, then hand the user a pre-filled cart
    to confirm in the provider app (sidesteps both API access and order liability).
  - **User-authorized automation** — the user connects their own account; actions run
    on their behalf with explicit consent + a safe-retry / no-double-order guard.
  - **Aggregator data providers** — licensed menu/price feeds where they exist.
- **Decouple the design from any one provider.** The optimizer should target an
  abstract "menu source" interface so Swiggy / Zomato / direct-restaurant / simulated
  can all plug in. The wireframe already labels orders "Swiggy — simulated", which is
  the right hedge for a prototype.

---

## 9. Bug / polish backlog (from design review)

- [x] **₹-40 renders green** → turns **red** when `left < 0`, with an "₹X OVER cap"
      state. *(done everywhere: selector, calendar-grid rail + insight tile,
      command-dashboard desktop + mobile, mobile grid.)*
- [x] **Per-day remaining-allowance meter** → per-day **kcal allowance** bar on each
      grid column; drains as picks get heavier (amber >90%, red over target). Makes
      "eat heavy at lunch → dinner headroom dips" legible.
- [x] **Ambiguous glyphs** (`▢ ⌂ ⊘`) → explicit ▢/⌂/⊘ **mode toggle** with `title`
      tooltips + legend, on both the calendar grid and the mobile day view.
- [x] **Underfunded day** → the day where cumulative spend crosses the cap is tinted
      red with a "⚠ cap runs out here" badge (desktop); past-cap days tint red in the
      mobile chip row.

---

## 10. Build order

1. **Inline spin + browse-alternatives on each meal card** ✅ *(calendar grid)*. Kills
   the standalone slot machine, fixes the #1 churn driver.
2. **Per-meal mode toggle (cook/order/skip) + draining allowance meter** ✅ *(calendar
   grid + mobile)*. Covers "eat heavy today" (kcal allowance), cook↔order↔skip, and
   the budget-red fix end-to-end.
3. **Constraint-conflict prompt** (★ vs. budget trade-off) ✅. Over-cap banner that
   keeps the ★ floor / allergens locked and resolves by money: greedy **"Trim to fit"**
   + reset. Plus the underfunded-day highlight.
4. **Interactive mobile day view** ✅ — chip-per-day picker → that day's meals with
   spin + mode toggle + kcal allowance; past-cap days tint red.

### Page-1 weekly-plan wireframe is feature-complete.
5. **Taste DNA visualization in Setup** ✅ — new **🧬 Taste DNA** tab: a flavour-
   fingerprint **radar chart** (6 axes), a trainer counter ("142 orders · 38 skips ·
   11 spins", +1 signal per rating, private & non-portable), a "why your picks feel
   uncannily right" insight feed, model-confidence, and a Household-DNA "next bet".
6. **Calendar/life-event power rules** ✅ — added 🏋 gym-day (+kcal/protein) and
   ✈ travel-day (grab-and-go / skip) rules alongside the existing weekend rule, across
   the Settings form, Guardrails band, and Plain-English program. Also fixed a broken
   Setup→Weekly back-link (em-dash vs hyphen in the filename).

### Specced in `SPEC.md` — now also prototyped in the wireframes:
0. **Location** — the cross-cutting primitive (serviceability gate, geo-pricing,
   per-window active-location resolution, privacy/anti-spoof). → `SPEC.md §0`.
   ✅ *Prototyped on the Weekly Plan*: a per-meal **🏠/💼/✈ area chip** (cycles area),
   **serviceability** that re-filters candidates (Travel → grab-and-go only; an area
   with no outlet → cook/skip), lunch-defaults-to-Work resolution.
7. **Nutrition debt/credit ledger + recipe-fatigue/novelty.** → `SPEC.md §1`.
   ✅ *Prototyped as a new 🍎 Nutrition tab in Setup*: TDEE chain (BMR×activity−goal),
   7-day kcal-vs-target bars, protein debt → repair-meal, 30-day micro watch, novelty
   dial. Per-day kcal allowance already lives on the Weekly Plan.
8. **Menu sourcing & ordering layer** (provider abstraction, partner/aggregator/
   deep-link/authorized routes, fallback ladder). → `SPEC.md §2`.
   ✅ *Reference implementation built & tested* in `src/sourcing/menu-source.js`
   (`MenuSource` interface, `SimulatedMenuSource`, `SourcingOrchestrator`): serviceability
   gate, hard filters (diet/allergen/★/price-cap), surge pricing, cheapest-serviceable
   arbitration, idempotent no-double-order, propose-vs-auto autonomy, and a
   partner→aggregator→deep-link fallback ladder. `node src/sourcing/menu-source.test.js`
   → 10/10 pass.

---

## 11. Design audit

A full-site UI/UX audit (booted the prototypes in headless Chromium, screenshotted every
page/tab incl. mobile, graded P0–P3 by understanding/trust/conversion) lives in
`docs/design-audit/README.md`, with screenshots in `docs/design-audit/assets/`. Three safe
copy/hierarchy fixes were applied on the spot; the rest are prioritised recommendations.

---

## Sources

- a16z — *The Empty Promise of Data Moats* — https://a16z.com/the-empty-promise-of-data-moats/
- Bloom VP — *The New Software Moats* — https://bloomvp.substack.com/p/the-new-software-moats-stickiness
- nshift — *The rise of agentic commerce (2026)* — https://nshift.com/blog/agentic-commerce-ai-shopping-agents-2026
- Edgar Dunn — *Agentic Commerce in Grocery* — https://www.edgardunn.com/articles/grocery-shopping-is-about-to-get-interesting
- OrganizeAt — *Diet Planner App Development (churn/retention)* — https://home.organizeat.com/blog/diet-planner-app-development/
- Appcues — *Variable rewards in product design* — https://www.appcues.com/blog/variable-rewards
- Nir & Far — *Variable Rewards* — https://www.nirandfar.com/want-to-hook-your-users-drive-them-crazy/
- Meet Penny — *Grocery list & pantry management apps* — https://www.meetpenny.com/grocery-list-and-pantry-management-apps/
