# SmartPlate — Optimization Model & Control Taxonomy

> The missing middle layer between `ROADMAP.md` (the *why* / vision) and `SPEC.md`
> (feature specs). This pins down **the actual optimization problem** the agent
> solves, **how every control maps into it**, and the **budget-recommender** and
> **IA/card-density** decisions. Grounded in the live code:
> `smartplate/kernel/optimizer.py`, `smartplate/domain/nutrition.py`,
> `smartplate/config.py`, and `project/SmartPlate Wireframes.html`.

---

## 1. The optimization problem (as built)

The "agent" is a **MILP** (PuLP/CBC), not an LLM — planning a week is milliseconds
of CPU. One run picks exactly one option per meal session.

### 1.1 Decision variables

For each **active session** `s` (a day × meal window you switched on) and each
**candidate** `i` in that session's feasible set `C_s`:

```
x[s,i] ∈ {0,1}      # choose option i for session s
```

Candidate *kinds*: `deliver` (a dish from a serviceable outlet), `cook` (a recipe),
`skip` (the always-feasible relief valve). A fridge `leftover` is a forced zero-cost cook.

### 1.2 Objective (current — minimize)

A **scalarized weighted sum** over the chosen candidates (`optimizer._objective`):

```
minimize  Σ_s Σ_i x[s,i] · (
      w_cost   · cost_norm
    − w_taste  · taste                 # subtracted: more taste ⇒ lower cost
    + w_nutri  · nutri_penalty
    + w_health · protein_penalty
    + w_carbon · carbon_penalty · (0.5 + carbon_pref)
    + w_surge  · surge_premium
    + cook_effort                      # 0.35 for a real cook, 0 for leftovers
    + weather_bias + festival_bias
  )
  + skip_penalty · Σ (non-forced skips)

cost_norm = cost / ref_cost,   ref_cost = weekly_budget / |active sessions|
```

Mode weights (`config.MODE_WEIGHTS`): **Comfort** cranks `taste`, **Tight Week
(survival)** cranks `cost`+`surge`, **Balanced** sits between.

### 1.3 Constraints

| Type | Constraint | Where |
|---|---|---|
| **Hard — selection** | `Σ_i x[s,i] = 1` ∀ s | one option per session |
| **Hard — budget** | `Σ cost·x ≤ weekly_budget` | user-set / recommender-proposed ₹ ceiling (§5) |
| **Hard — cook cap** | `Σ cook ≤ cook_cap` | **user value**; `MAX_COOK_PER_WEEK = 6` is only the cold-start default |
| **Hard — variety** | `Σ x[same dish] ≤ repeat_cap` | **user value**; `MAX_ITEM_REPEAT = 2` is only the cold-start default |
| **Hard — pre-filters on `C_s`** | allergens, diet, ★ rating floor, fasting window, serviceability, calendar travel / festival suspension | candidates removed *before* scoring |
| **Soft — objective terms** | nutrition, taste, health, carbon, surge, weather, festival bias | the weighted sum |

> **No hardcoded caps — every limit above is a *user value with a computed default*, not a fixed
> constant.** `weekly_budget` is set by the user or proposed by the budget recommender (§5); the
> cook/repeat caps default to 6/2 but are editable per user; the nutrition target is personalised
> from BMR (§3). The constants in code (`MAX_COOK_PER_WEEK`, `MAX_ITEM_REPEAT`, the seed kcal/macro
> numbers) are **cold-start priors that entered/learned data replaces** (§10) — never a ceiling the
> user can't move.
>
> **Medical = exclude *and* instruct.** Hard rules drop genuinely unsafe items (diabetes → remove
> >20 g-sugar dishes). But many dishes are safe *with a request*, so the agent also attaches
> **order-time special instructions** ("no added sugar", "less salt", "no mayo") to the Swiggy cart —
> keeping adjustable dishes in the candidate set instead of over-pruning the menu. Exclusion is the
> floor; the kitchen instruction is the refinement.

### 1.4 The honest read of "maximise nutrition while minimising cost"

Today the model is **not** literally that. It is:

> *minimize a blended score (cost + a bit of everything, incl. a nutrition penalty)
> **subject to a hard budget cap**.*

So **budget is a hard constraint**, **nutrition is a soft penalty**, and **cost is
also soft** (inside the blend). You can't *simultaneously* maximize one quantity and
minimize another — that's two objectives. You resolve it exactly one of three ways:

| Framing | Objective | The rest |
|---|---|---|
| **(a) Scalarize** *(current)* | one weighted sum | weights |
| **(b) ε-constraint** | pick ONE (min cost **or** max nutrition-fit) | others become thresholds |
| **(c) Goal programming** | minimize deviation from target set-points | hard caps for medical/allergen/budget |

---

## 2. Constrained vs unconstrained — make the **mode** decide

**Recommendation:** stop treating this as one global choice. The **mode toggle =
which variable is the objective and which is the constraint.** That's a clean mental
model *and* a differentiator.

```
Tight Week   →  minimize COST       s.t. nutrition = soft floor (protein floor holds,
                                          kcal band widens), budget hard cap.   [money binds]

Balanced     →  goal-programming: minimize weighted deviation from BOTH the budget
                set-point AND the nutrition target.                            [both matter]

Comfort /    →  maximize NUTRITION-FIT + taste   s.t. budget hard cap only.    [health binds]
Health-first
```

**Why goal-programming for the default (Balanced):** it gives a crisp product line —
*"the cheapest week that still hits your nutrition"* — and never goes infeasible if
you add **slack variables** with a high price. A missed *protein* target is **not**
"carried as a debt to next week" (protein is daily — see §3.1); it's a daily-floor
miss that nudges later days up. What legitimately carries is **calories** (weekly)
and **micros** (~30-day). The slack just keeps the plan solvable, not a lump to repay.

### 2.1 Nutrient timescales — not one rolling ledger (`domain/ledger.py`)

Lumping every nutrient into one weekly debt/credit is wrong; each runs on its own clock:

| Nutrient | Clock | Treatment | Carries? |
|---|---|---|---|
| Calories | weekly (fat is the store) | bank/credit, surfaced **explicitly** | ✅ |
| **Protein** | **daily** (MPS is daily; not stored) | daily floor + rolling *adherence* signal → nudge future days; no lump repair | ❌ |
| Fat / carbs | daily, flexible | soft band | ❌ |
| Micros (iron, B12, fibre) | ~30 days (body stores) | long rolling window (the 30-day watch) | ✅ |
| Sodium / sugar (medical) | daily hard cap | never averaged or credited away | ❌ |

And banked surplus is shown, never silently "passed over": a light week leaves an
**explicit** "✦ banked +₹X / +Y kcal → today" credit (`budget.nested_caps`,
`ledger.calorie_credit`).

**Concrete code change implied:** `nutrition.penalty` currently penalizes kcal *over*
target as hard as *under* (`abs(kcal − target)`) and protein only when *under*. For a
real "hit the target" model, make the nutrition term a **band with asymmetric slack**:
free inside `[target−α, target+α]`, cheap-over / expensive-under outside, protein a
one-sided floor. Promote it from a pure objective term to a *soft constraint with
slack* so "hit the target" is the literal goal, money permitting.

---

## 3. Nutrition target & "I feel I need more" adjustments

The target is computed live (Mifflin–St Jeor), not hardcoded
(`bmrCalc` in the wireframe):

```
BMR    = 10·wt + 6.25·ht − 5·age + (sex == M ? +5 : −161)
TDEE   = BMR × activity          # Sedentary 1.2 … Athlete 1.725
target = TDEE + goal_adj         # Cut −500 · Lean −110 · Maintain 0 · Gain +250
protein = max(45, wt · 0.97)     # scales with body weight, not calories
```

"A user may feel he needs more" → **three adjustment layers, increasing locality.**
Don't add a new slider for any of them; reuse what exists:

1. **Goal** (persistent) — the Cut/Maintain/Gain dial. Already there.
2. **Context flex** (automatic, from calendar) — 🏋 gym day `+400 kcal / +25 g protein`,
   travel/weekend bands. These are **⚡ power rules**, not forms.
3. **In-the-moment** (manual, per day/meal) — "heavy lunch today" bump → the agent
   **silently re-balances the rest of the day** (dinner allowance dips), shown as the
   **draining kcal-allowance meter** already on the grid.

The **debt/credit ledger** carries the difference across days/weeks, so eating more
today isn't lost — it dips tomorrow's allowance or banks a credit and queues a repair
meal. So:  `effective_target = TDEE + goal + Σ(context modifiers) + manual_nudge`,
rendered as the little equation the BMR panel already shows.

---

## 4. Control taxonomy — the fix for "too many sliders"

Today the dials are scattered and over-many: **5 priority sliders** (Taste · Health ·
Price · Low-carbon · Dodge-surge), a **Novelty** slider, a **rating-floor** slider,
nutrition-target chips, plus toggles and power rules across 5 tabs. The cure is to
**categorize every input by how it enters the solver** (§1.3) — four buckets, which
your colour legend already hints at but doesn't enforce:

| Bucket | What it is | Solver role | UI primitive |
|---|---|---|---|
| 🔒 **Locks** | allergens, diet, medical caps, ★ floor, budget cap, fasting, serviceability | **hard** — removes candidates | switch + one threshold. *Never a "balance."* |
| 🎯 **Targets** | kcal/day, protein/day, weekly ₹ | **constraint / goal set-point** | a number with a **computed default** (BMR fills nutrition; recommender fills ₹ — §5) |
| 🎛 **Dials** | the soft leans | **objective weights** | sliders — **but few** (see cull below) |
| ⚡ **Rules** | WHEN scope → THEN nudge; calendar events | **conditional modifiers** | rule rows; replace per-day fiddling |

### 4.1 Cull the dials (5 → 1 + 2)

Five interacting weight-sliders is more than anyone can reason about; people can
meaningfully trade off ~2–3 at once, and >4 coupled sliders produce arbitrary
settings + "slider fatigue." Replace with:

- **One primary axis: Money ⇄ Everything-else** — this *is* the mode
  (Tight ↔ Balanced ↔ Comfort). It already maps to `MODE_WEIGHTS`.
- **At most 2 contextual leans** ("lean healthier", "lean greener") as ± nudges, not
  full sliders.
- **Taste is not a slider** — it's *learned* (the Taste-DNA radar). Surfacing it as a
  manual weight contradicts the moat. Remove it from Preferences.
- **Dodge-surge & low-carbon default ON**, tucked under "advanced." They're rarely
  things a user wants to actively dial week to week.

Net: **5 priority sliders → 1 mode + 2 optional leans**, and the whole Setup
collapses to **Locks / Targets / Dials / Rules**.

---

## 5. Budget recommender (new) — inverse optimization

Don't make the user *guess* ₹2,000. Invert the solver: **plan requirements →
recommended budget**, from sessions + nutrition target + nearby (serviceable) outlets
+ usual-vs-variety preference.

Run the solver (or a greedy approx) three times to produce a **band**:

| Reference | How it's computed | Product line |
|---|---|---|
| **Floor** | minimize cost **s.t. nutrition band + locks** (the §2 constrained model) | "Below ₹X we'd have to drop a session or the ★ floor." |
| **Usual basket** | cost of your *repeat/favourite* picks that still meet nutrition (history; cold-start = popularity-weighted near ★ floor) | "Your normal week costs ≈ ₹Y." |
| **Variety basket** | floor + **novelty premium** (inject new, high-rated dishes per the novelty dial) | "₹Z lets the agent explore 2 new dishes/week." |

Then show the recommendation as a **trade-off**, not a number:
*"₹1,650 hits your nutrition with your usual places · +₹300 buys 2 new dishes ·
−₹230 means one cook day or a skip."* This is the conflict-prompt logic
(`ROADMAP §2`) run **proactively** at budget-set time, and it's the recommended
**constraint value** for §2.

### 5.1 Usual vs variety basket + "rate as we suggest"

- Keep two candidate pools per session: **familiar** (high personal affinity / repeat)
  and **novel** (high quality, low recency, from the fatigue model). The **novelty
  dial** mixes them.
- Every novel suggestion the user **rates** is a Taste-DNA training signal (`+1 signal`,
  the trainer counter). Rating new dishes is *how the variety basket earns its keep* —
  it sharpens the model and de-risks future novelty. Frame as a variable-reward loop
  (the Feast Fund / surprise mechanic, `ROADMAP §5`): "Trying something new? Rate it → I learn."

---

## 6. Information architecture & card density

Principle (from `ROADMAP §0`): **autopilot-first.** Show the *plan*, not the
*controls*; controls are one-tap, reversible escape hatches layered on top.

- **Weekly Plan = the product.** Decision-dense, action-light. Lives here daily.
- **Setup = the program.** Four buckets only (§4). Set rarely; defaults computed.
- **Taste DNA + Nutrition = the moat made visible.** Not controls — *artifacts* that
  show the model compounding (radar morphing, ledger, trainer counter).
- **Budget recommender** sits at the seam (sessions + targets → proposed ₹).

### 6.1 Card face — keep drink/gravy behind `menu ▾` (the instinct is right)

A meal card's face should carry only four things:

1. the **decision** (dish · outlet),
2. the **money** (₹ + surge flag),
3. a **nutrition glance** (kcal · protein),
4. **one primary action** (↻ spin / ▢⌂⊘ mode).

Add-ons (drink, spice/gravy, portion detail) are **secondary** — pinning them open
re-clutters exactly what was decompressed. Two fixes so "hidden" ≠ "undiscovered":

- **Badge state on the face** when an add-on is non-default (a small 🥤 / 🌶 when a
  drink or spicy level is already applied) — applied state is visible without expanding.
- **Reveal inline on intent** (hover / long-press / tap ▾), not always-on.

---

## 7. "What did you cook" depth — recommend building it (P2)

Today cook days **reuse the menu picker + portions**
("home-cooked — set portions with × above; logged to nutrition"). The follow-up is a
true free-text **"I made X"** with its own nutrition lookup. **Build it** — but as the
*planned ≠ eaten* logging already in `SPEC §1.7 / P4`, not a separate feature:

- Free-text/voice "dal + 2 rotis" → parse → nutrition lookup (IFCT/USDA/Nutritionix or
  estimate) → write an `eaten` ledger entry.
- Keep the menu-picker path as the fast default; free-text is the escape hatch for
  off-plan cooking.
- **Why it matters:** closes the planned→eaten gap (the ledger silently drifts without
  it) and home-cooking awareness is itself a moat (Swiggy won't help you cook).
- **Scope guard:** arbitrary home-dish nutrition is fuzzy → ship "estimate + confirm,"
  not false precision (cold-start honesty). Start with a small common-dish library +
  the portion math that already exists.

---

## 8. The moat (synthesis)

Per `ROADMAP` + the brainstorm + a16z ("data isn't the moat; the compounding loop is"):

- It is **not** the ordering (table stakes by 2026).
- It **is** the **stateful cross-time optimizer** (budget + nutrition compounding across
  week/month), the **personal Taste DNA** (uncopyable private model), and **constraint
  judgment** (refuse below ★ floor, hit nutrition, Tight-Week's spend-inversion that
  serves the cost-churned segment Swiggy won't prioritize).
- UX expression: autopilot that's *visibly learning* + one-tap reversible overrides +
  "we do the math" surfaces (budget recommender, repair meals, surge-dodge savings).

---

## 9. Build order (incremental)

1. ✅ **Nutrition as a band** — `nutrition.penalty` → on-target band, over penalised
   less than under, one-sided protein floor; `nutrition.shortfall` for the gap.
2. ✅ **Mode = objective/constraint switch** — `config.MODE_META` gives each mode an
   objective, a "what gives," a `nutri_tol`, and a plain-language `outcome`.
3. ✅ **Kill the magic numbers** — cook cap + cook effort overridable per user;
   carbon truly off at `carbon_pref == 0`.
4. ✅ **Rating footgun** — optional soft ★ above a hard safety floor (config flag,
   default hard); recommender suggests a floor; substitution stays hard.
5. ✅ **Budget recommender** — `kernel/recommender.py`: Floor / Usual / Variety bands,
   expected surge priced in, suggested ★ floor, one-off support.
6. ✅ **Novelty/variety model** — `domain/fatigue.py`: familiar/novel pools + levels
   (replaces the crude repeat-cap as the *mechanism*; the cap stays as a floor).
7. ✅ **Feasibility/shortfall diagnostic** on every solve (`optimizer._diagnostics`).
8. ✅ **Control-taxonomy UI** — Setup nav + section headers regrouped to
   **Locks / Dials / Rules**; the 5 priority sliders culled to one mode dial
   (money ⇄ everything-else) + 2 leans; the Taste slider dropped (it's learned).
9. ✅ **Variety UI** — a **level** control (Usual/Light/Mixed/Adventurous) replaces
   the raw novelty slider; novel picks carry a subtle **✦** (not V/U — see §6.1);
   a week chip shows "⭐ usual · ✦ new"; the recommender band is surfaced as a card.
10. ✅ **Novelty nudge** in the MILP — a soft bonus for novel picks scaled by the
    variety level, **gated off by default** (`SMARTPLATE_VARIETY=on`) so it can't
    silently shift behaviour. (A hard composition constraint remains a later option.)
11. ✅ **"I made X" lookup + persistence** — `domain/intake.py` parses free text
    ("dal + 2 rotis") → nutrition estimate (matched/unmatched + confidence), and
    **persists** it (`intake_log`) so entries accumulate; `POST /api/intake`
    (estimate), `POST /api/user/<id>/intake` (log), `GET /api/user/<id>/ledger`.
12. ✅ **Nutrient-specific rolling ledger** — `ledger.rolling_view` over accumulated
    intake: calories bank weekly (explicit credit), protein daily adherence +
    today's distribution, sugar a daily cap.
13. ✅ **Protein evenness in the solve** — a day-level slack term penalising
    backloading (per-meal target = daily ÷ meals-that-day), `SMARTPLATE_PROTEIN_EVEN`.

---

## 10. What shipped in code (and the review corrections it encodes)

| Change | Where | Verified by |
|---|---|---|
| Banded, asymmetric nutrition + `shortfall` | `domain/nutrition.py` | `test_optimization_model` (band/over-under/tol/shortfall) |
| Mode framing (objective / gives / tol / outcome) | `config.py` (`MODE_META`, `mode_meta`) | `test_mode_tolerance_orders_correctly` |
| Per-mode kcal tolerance threaded into the solve | `kernel/optimizer.py` (`build_context` → `nutri_tol`) | existing optimizer tests stay green |
| Cook cap + effort overridable (no fake 6 / 0.35) | `kernel/optimizer.py` (`_cook_cap`, `_cook_effort`) | `test_cook_cap_respected` |
| Carbon off at `pref == 0` (no silent 0.5 baseline) | `kernel/optimizer.py` (`_objective`) | `test_carbon_off_when_pref_zero` |
| Soft ★ above a hard safety floor (opt-in) | `config.py` + `optimizer._rating_filter/_rating_pen` | suite green in default (hard) mode |
| "Never substitute below your ★" held in soft mode | `kernel/variance.py` (`_next_best` guard) | `test_substitution` |
| Budget recommender (Floor/Usual/Variety + ★) | `kernel/recommender.py`, `service.py`, `app.py` | `test_recommend_*` |
| Novelty/variety model + levels | `domain/fatigue.py` | `test_novelty_*`, `test_pools_*` |
| Feasibility/shortfall diagnostic | `optimizer._diagnostics` | `test_optimize_returns_diagnostics` |
| Mode outcomes exposed to UI | `app.py` `/api/meta.mode_outcomes` | smoke |
| Canonical Mifflin activity factors (5 levels) | `project/SmartPlate Wireframes.html` | `project/test-merged.mjs` |
| Slider cull (5 → mode + 2 leans) + Locks/Dials/Rules grouping | `project/SmartPlate Wireframes.html` | `test-merged.mjs` |
| Variety level control + novel **✦** marking + week chip | `project/SmartPlate Wireframes.html` | `test-merged.mjs` |
| Budget-recommender card on the Weekly Plan | `project/SmartPlate Wireframes.html` | `test-merged.mjs` |
| Gated novelty nudge in the solve | `config.py`, `optimizer.py` | `test_novelty_nudge_*` |
| "I made X" free-text lookup | `domain/intake.py`, `service.py`, `app.py` | `test_intake_*` |
| Nested day/week/month caps (tightest binds, roll-forward, explicit credit) | `kernel/budget.py` (`nested_caps`) + Weekly Plan horizon toggle | `test_ledger_budget`, `test-merged.mjs` |
| Nutrient-specific ledger (protein daily · calories weekly · micros 30-day · medical daily-cap) | `domain/ledger.py` + corrected Nutrition tab | `test_ledger_budget`, `test-merged.mjs` |
| Per-meal protein distribution (even split ≈daily/3, not kcal-share; flags backloading) | `nutrition.protein_meal_target`, `ledger.protein_distribution` + "Protein across the day" viz | `test_protein_*` |
| Protein evenness as a day-level term in the solve (penalises backloading) | `optimizer.optimize` (slack var) + `config.PROTEIN_EVEN_W` | `test_protein_evenness_keeps_plan_feasible` |
| Intake persistence + rolling nutrient-specific ledger | `db.intake_log`, `intake.record/recent/by_day`, `ledger.rolling_view`, `service`, `app` | `test_intake_persists_and_ledger_accumulates`, `test_intake_log_and_ledger_endpoints` |

**On V/U badges (the question that prompted this slice):** rejected. Badging the
"usual" majority is noise, and a literal "V" collides with veg/vegan in a food app.
We mark **only the new picks**, with a subtle **✦** (not a letter), plus one
week-level "⭐ usual · ✦ new" chip that ties to the variety level and the rate-to-train loop.

**Magic numbers → cold-start priors.** Every constant we flagged is now either
user-overridable (cook cap/effort, via `health_targets`), honestly off until the
user asks for it (carbon), or a prior that learned data replaces (surge already did
this — it's the pattern the others now follow). Nothing is a silent fake.

**Rating policy.** The brand promise ("never silently substitute below your ★") is
hard at execution *regardless* of planner mode. The planner can optionally treat a
high aspirational ★ as a soft preference above a low hard safety floor, so it bends
under budget instead of exploding it — and the recommender suggests a floor so the
user needn't guess.

**Horizon & budget granularity (now built).** The *session* is the atomic unit;
horizon (day/week/month) only sets how many sessions are in the window and which
time-bucket the spend-sum is capped over. Budget caps **nest** (daily ∧ weekly ∧
monthly; the tightest binds) and **roll forward** — `kernel/budget.nested_caps`
computes the effective daily cap and the **explicit** `banked_today` credit, and the
Weekly Plan has a Day/Week/Month horizon toggle. A one-off order is just `N = 1`.

**The three modes, verbally.** A mode is a statement about *what's allowed to give*:
Tight Week minimises **cost** and lets nutrition give (protein floor holds, kcal band
widens); Balanced minimises **deviation from both** set-points; Comfort maximises
**nutrition-fit + taste** with budget as the only hard ceiling. The recommender's
Floor / Usual / Variety bands line up with these three.

---

## Sources / cross-refs

- `smartplate/kernel/optimizer.py`, `smartplate/domain/nutrition.py`, `smartplate/config.py`
- `project/SmartPlate Wireframes.html` (sliders, BMR panel, Taste-DNA radar, cards)
- `ROADMAP.md` §0 (autopilot thesis), §2 (interaction answers), §6 (nutrition chain), §5 (variable rewards)
- `SPEC.md` §1 (ledger / fatigue), §2 (menu sourcing / arbitration)
