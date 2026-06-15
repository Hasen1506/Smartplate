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
| **Hard — budget** | `Σ cost·x ≤ weekly_budget` | the ₹ ceiling |
| **Hard — cook cap** | `Σ cook ≤ 6 / week` | realism (`MAX_COOK_PER_WEEK`) |
| **Hard — variety** | `Σ x[same dish] ≤ 2 / week` | `MAX_ITEM_REPEAT` |
| **Hard — pre-filters on `C_s`** | allergens, diet, ★ rating floor, fasting window, serviceability, calendar travel / festival suspension | candidates removed *before* scoring |
| **Soft — objective terms** | nutrition, taste, health, carbon, surge, weather, festival bias | the weighted sum |

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
you add **slack variables** with a high price (a missed protein target becomes a
*debt that carries* to next week via the ledger, not an unsolvable plan).

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

1. **Control taxonomy refactor** — regroup Setup into Locks / Targets / Dials / Rules;
   cull 5 priority sliders → mode + 2 leans; drop the Taste slider. *(UI only.)*
2. **Nutrition as a band with slack** — rework `nutrition.penalty` → asymmetric band +
   one-sided protein floor; add slack vars so plans stay feasible (`optimizer`).
3. **Mode = objective/constraint switch** — wire Tight/Balanced/Comfort to the §2
   framings, not just weight magnitudes.
4. **Budget recommender** — three-run inverse optimization + trade-off card.
5. **Card-face badges** for applied add-ons; keep `menu ▾` collapsed.
6. **"I made X" logging** (P2) — free-text → nutrition lookup → `eaten` ledger.

---

## Sources / cross-refs

- `smartplate/kernel/optimizer.py`, `smartplate/domain/nutrition.py`, `smartplate/config.py`
- `project/SmartPlate Wireframes.html` (sliders, BMR panel, Taste-DNA radar, cards)
- `ROADMAP.md` §0 (autopilot thesis), §2 (interaction answers), §6 (nutrition chain), §5 (variable rewards)
- `SPEC.md` §1 (ledger / fatigue), §2 (menu sourcing / arbitration)
