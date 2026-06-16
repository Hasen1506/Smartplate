# SmartPlate — Full-Site UI/UX Design Audit (v2)

*Audited as a senior design lead on day one. The question is not "is it pretty?" — it's
**can a normal user understand the product, trust it, and finish the core action
(pick the week → review → approve & auto‑order) without reading docs?***

> **v2 note.** This supersedes the v1 audit. Since v1, the team self‑hosted React (the
> P0 CDN single‑point‑of‑failure, *F1*, is gone) and the default plan now loads **within
> cap** (*F2*). This pass re‑booted the current artifact, fixed a batch of small things on
> the spot (§1), and re‑scoped the findings to what's true **today**.

---

## 0. What was audited & how it was booted (method)

The repo ships two things that both call themselves "SmartPlate":

1. **The Flask app** (`run.py` → `smartplate/`) — a working JSON API + a vanilla‑JS plan
   view. It boots cleanly (`python run.py` → `http://localhost:5057`, `GET /` → 200) and
   the backend is the **source of truth for the product model** (nutrition, medical rules,
   ledger). It is referenced in §4.
2. **The interactive design prototype** — `project/SmartPlate Wireframes.html`, a
   **self‑contained, offline** file (an inlined mini‑React runtime + template interpreter,
   no network at runtime). This is the **front‑end the team is actively iterating**: it has
   the Calendar‑grid / Command‑dashboard views, the Setup screens, Taste DNA and Nutrition.
   **This audit is of that prototype**, because that's where the product's UX decisions live.

- **Boot:** headless **Chromium via Playwright**, rendered from `file://` at 1460‑wide,
  `deviceScaleFactor: 2`, full‑page. Each screen lays its **desktop and mobile** frames out
  together, so every capture covers **mobile layout** too.
- **Verified, not eyeballed:** the prototype's own end‑to‑end harness
  (`project/test-merged.mjs`, jsdom) boots the shipped file and asserts the interactive bits
  work — **all assertions pass**, including the new behaviours from §1.
- **One environment caveat:** Google Fonts (Gaegu/Caveat) were cert‑blocked in the audit
  sandbox, so captures render in a fallback cursive. Layout/alignment judgments are
  unaffected; exact type colour/size is judged from the source.

### Screenshots (`./assets/`)
| # | Screen / state | File |
|---|---|---|
| 01 | Weekly Plan — **Calendar grid** (selector + budget + aligned 7‑day grid + mobile) | [01-weekly-calendar-grid.png](./assets/01-weekly-calendar-grid.png) |
| 02 | Weekly Plan — **Command dashboard** (status ring, command bar, tiles, burn‑down) | [02-weekly-command-dashboard.png](./assets/02-weekly-command-dashboard.png) |
| 03 | Setup — **Settings form** (the one "see everything" config view) | [03-setup-settings-form.png](./assets/03-setup-settings-form.png) |
| 04 | Setup — **Nutrition** (BMR/TDEE → this week → per‑nutrient → repair & variety) | [04-setup-nutrition.png](./assets/04-setup-nutrition.png) |
| 05 | Setup — **Taste DNA** (flavour‑fingerprint radar) | [05-setup-taste-dna.png](./assets/05-setup-taste-dna.png) |
| — | Calendar grid **before** the alignment fix (rows stagger) | [grid-before.png](./assets/grid-before.png) |
| — | Calendar grid **after** the alignment fix (rows line up) | [grid-after.png](./assets/grid-after.png) |

**Severity:** P0 = broken / blocks the core action / breaks trust badly · P1 = serious,
fix this cycle · P2 = should fix · P3 = polish.
**Hurts:** **U**nderstanding · **T**rust · **C**onversion.

---

## 1. Fixed on the spot this pass (safe: layout / copy / hierarchy — no payment, delete, or publish action touched)

| # | Fix | Why it mattered | Hurt it relieves |
|---|-----|-----------------|------------------|
| **1** | **Calendar‑grid alignment.** The 7 day‑columns were independent vertical stacks, so a tall card in one day shoved every card below it out of row — the breakfast/lunch/dinner rows didn't line up (see `grid-before`). Rebuilt as a **CSS subgrid**: each day‑column shares the parent's 4 row tracks (header · B · L · D), so the meal rows align and equal‑height across all 7 days (`grid-after`). | This was the reported bug. Misaligned rows make a *matrix* unreadable — the eye can't scan "every lunch" across the week. | **U** |
| **2** | **One "skip", not "off" *and* "skip".** The selector had a redundant paused/off state next to skip. Merged them: every window is in the plan, and **SKIP is the single "not eating" state**. | Two visually different "I'm not eating this" states is a needless decision and a decode cost. | **U** |
| **3** | **Step‑1 cells now cycle ▢ deliver → ⌂ cook → ⊘ skip** (active goes dark), kept in sync with the grid. Before, a cell only toggled on/off and showed a **hardcoded** glyph you couldn't change. | The reported "it only switches between two random symbols" — the symbol was the slot's default, not a control. | **U, C** |
| **4** | **Editable meal‑window times.** The "by 8:30am / 1:00pm / 8:30pm" labels were static text; they're now real **`<input type="time">`** controls bound to state. | "When should SmartPlate plan?" implied you set the *when* — but the time was read‑only. | **U, C** |
| **5** | **A real symbol legend** at the top of the grid: ▢ ⌂ ⊘, ↻ spin, menu ▾, 🏠/💼/✈ area, ⭐ usual / ✦ new — spelled out once, where the dense cards are. | Icon‑only controls with no on‑screen key (old *V3*) → users won't attempt edits they can't decode. | **U, C** |
| **6** | **Removed two redundant config views.** Setup had **three** ways to express the *same* rules — Settings form, "Guardrails & dials", and "Plain‑English program". Cut the latter two from the UI; Setup is now **Settings form · Nutrition · Taste DNA**. | Three parallel editors of one config is confusing and triples the maintenance/consistency surface. | **U** |
| **7** | **Nutrition section re‑framed.** Added a top‑to‑bottom reading guide (① target → ② week → ③ per‑nutrient → ④ when you drift) and split the two bottom cards under one honest header: **a nutrient gap (we order specific dishes) vs boredom (we keep it fresh)** — two different problems, two different fixes. | The repair‑meals and variety cards looked identical and their relationship (the user's "how do I structure this?") was unstated. | **U** |
| **8** | **Density decompressed + week‑level controls (resolves H1).** Each meal card keeps one pick + ↻ spin / menu ▾, now with a **🔒 pin** toggle; the rail gains **↻ Re‑optimise unpinned** (re‑rolls only non‑pinned slots) and a **＋ order now** off‑plan hatch (＋ item per card) that writes to the budget + kcal ledger. | The 4‑alternatives‑per‑slot density problem (H1); freeze/extra‑order had no home. | **U, C** |
| **9** | **Day awareness + plan states (resolves H2; advances L1).** Today is accented (border + TODAY tag) in the desktop column and the mobile day‑picker; past days de‑emphasised; **skip = greyed/dashed @ ~0.5 opacity + ⊘ tag** (not blurred); over‑cap days are red‑outlined with a located recovery line; an all‑skip week shows an empty‑state note. | No "where am I" cue (H2); missing skip/empty/over‑cap states (L1). | **U, T** |
| **10** | **Drink/add‑on priced per outlet.** The hardcoded `+₹40 / 150 kcal` drink became a per‑outlet `drinkInfo(m)` (₹30–60 · ~120–180 kcal); outlets that serve none hide the 🥤 button. | A fake flat add‑on misrepresents the cart and the kcal ledger. | **U, T** |
| **11** | **⚡ Rules = suggested toggles + a template gallery (resolves the authoring‑burden read).** Rows reframed as auto‑derived suggestions you toggle; **＋ add power rule** opens a fill‑in‑the‑blank *When [scope ▾] → then [nudge ▾]* gallery, not raw syntax. | A blank WHEN→THEN editor implied everyone must author logic. | **U** |
| **12** | **Returning‑after‑a‑gap reconcile + usual‑first framing.** A dismissible "welcome back" banner ([followed the plan · ate out · log it]) states roll‑over nutrients won't over‑correct; the plan summary shows "kept N usuals · swapped M", and the over‑cap conflict offers the three‑way ✦ add‑outlet · raise‑budget · relax‑target choice. | Silent re‑anchoring after a gap; no usual‑first / infeasible surfacing. | **T, U** |
| **13** | **Loading / error states (completes L1).** An **optimising…** skeleton, a **provider‑down / nothing‑serviceable error** with ↻ Retry + switch‑area recovery, and the all‑skip empty state — reachable via a demo state switcher on the grid. | An agent that orders & pays can't show silent success/failure; these were the last missing async states. | **T, U** |

*Bigger items (density, loading/error states, cold‑start honesty, sticky approve bar) are
recommendations only — §2–§3, §5–§6.*

---

## 2. First impressions

| ID | Sev | Hurts | Finding (evidence) | Specific fix |
|----|-----|-------|--------------------|--------------|
| **F3** | **P1** | U, T | **It still reads as a wireframe, not a product.** "lo‑fi wireframe · v1" badge, hand‑drawn type, side‑by‑side DESKTOP/MOBILE mock frames inside fake browser chrome (01–05). Fine for internal review; a real user shown this would not trust it with a credit card. | For the product build, strip the wireframe scaffolding (fake browser chrome, hand‑drawn type, "wireframe" badge; product typeface for data). **Ship two form‑factor‑tailored experiences — a desktop app and a mobile app — instead of the side‑by‑side mock frames.** Each is designed for its device (desktop = the dense week‑matrix; mobile = the day‑picker flow), not one layout that merely reflows. *(Implementation can still be a single responsive codebase with two distinct breakpoint layouts — the requirement is two genuinely device‑specific designs, and the dual‑mock presentation goes away.)* **Progress this pass:** the two form‑factor mocks (desktop week‑matrix · mobile day‑picker) are now **genuinely complete and in sync** — every new interaction (🔒 pin, ↻ re‑optimise unpinned, ＋ order now/item, Today highlight, skip de‑emphasis, per‑outlet drink) is present in *both*, so they read as two tailored designs rather than one reflow. Stripping the wireframe scaffolding (fake chrome, hand‑drawn type, "wireframe" badge) is still the product‑build step. |
| **F4** | **P2** | U | **Two co‑equal views of the same week** — "Calendar grid" and "Command dashboard" (01–02) — with no guidance on which is "home". Power users like both; a first‑timer has to evaluate two layouts before doing anything. | Pick a **default** and make the other a toggle/"view as" — don't greet a new user with a fork. |

## 3. Navigation · hierarchy · consistency · states

| ID | Sev | Hurts | Finding | Specific fix |
|----|-----|-------|---------|--------------|
| **H1** | ~~P1~~ **resolved** | U, C | **Meal cards are still over‑dense.** Even now‑aligned, each cell stacks dish, outlet/₹/★, a −/×N/+ portion stepper, drink/spice, area chip + serviceability, ▢/⌂/⊘, and ↻ spin / menu ▾ (01). The 3‑second glance ("what / where / how much") competes with ~7 controls. | Progressive disclosure: default card = **dish · ₹ · status**; reveal portions/area/spin in a tap‑to‑open sheet. (Alignment fixed the *rows*; density is the remaining hierarchy problem.) **Decision:** one pick + ↻ spin / menu ▾ (the 4 best live *in* menu ▾), **pin & re‑optimize‑the‑rest** at week level, and a **＋ extra‑order** hatch that writes to the ledger — `optimization-and-ux.md §6.2`. **Built this pass (§1.8): 🔒 pin + ↻ Re‑optimise unpinned + ＋ order now/＋ item.** |
| **H2** | ~~P2~~ **resolved** | U | **No day awareness in the grid.** A returning user can't tell which column is *today* or which meal window is current. | Highlight **Today** (border + label) and the current window; de‑emphasise past days. *(Decision: `optimization-and-ux.md §6.3`.)* **Built this pass (§1.9): TODAY tag + accent on the desktop column and mobile day‑picker; past days dimmed.** |
| **L1** | ~~P1~~ **resolved** | T, U | **No loading / empty / error states.** The prototype renders synchronously from static data; there is no "optimising…", no "nothing serviceable to this area", no "provider down" (01–02). For an agent that **orders and pays**, silent success is as untrustworthy as silent failure. | Design *optimising* (skeleton), *empty* (new user / all‑skip week), and *error* (provider down, nothing serviceable, over‑cap) states, each with one clear recovery action. **Decision:** skip = greyed + dashed @ ~0.5 opacity (not blur); over‑cap = red‑outline the *specific* day + red burn‑down + a located recovery line, not a blocking banner — `optimization-and-ux.md §6.3`. **Built this pass (§1.9, §1.13): all four states ship — optimising skeleton, provider‑down/nothing‑serviceable error (↻ Retry · switch‑area), all‑skip empty state, located over‑cap recovery; reachable via a demo state switcher.** |
| **L2** | **P2** | T | **Cold‑start is still faked.** Taste DNA shows "142 orders · 38 skips · 11 spins" and a full radar (05); a brand‑new user would see confident history they never created. | A "still learning — building your DNA" state for week 1; don't render precision before the data exists. (The Nutrition ledger already does this; mirror it in DNA.) |
| **C2** | **P3** | U | Tab treatments differ (weekly sub‑tabs plain; Setup tabs colour‑coded). | Unify active‑tab styling across both screens. |

## 4. The product model — answering the questions that drove this work

*(Grounded in the Flask backend, which is the source of truth: `smartplate/domain/`.)*

### 4.1 What does a user actually worry about, and what are their nutrient targets?
The model carries six concern layers, in priority order:

1. **Hard safety (never violated, even in Survival mode):** allergens, medical conditions,
   diet, a ★ rating floor, and the budget cap. Filtered out *before* optimisation.
2. **Nutrition targets (soft, never hardcoded):** **personalised from the user's own BMR/TDEE**
   (Mifflin–St Jeor, *Setup → Nutrition*). The kcal/macro numbers in `domain/nutrition.py`
   (≈ kcal 2000 · protein 60 g · carbs 250 g · fat 65 g · sugar 40 g) are **cold‑start defaults
   shown only until the user enters their numbers**, not fixed targets. Split per meal (kcal
   25/40/35 for B/L/D; protein **evenly**, because backloading protein into one meal is suboptimal).
3. **Health:** protein floor, veg servings/day, optional fasting window (`domain/health.py`).
4. **Variety / recipe‑fatigue:** boredom is the #1 churn driver, so it's a tracked dial.
5. **Budget:** nested day/week/month caps; underspend rolls forward as a visible banked credit.
6. **Context:** weather, festivals, calendar conflicts, surge‑dodging.

### 4.2 If the plan misses a nutrient, what do we recommend to order?
Macros are **soft** — a miss never makes the plan infeasible; the gap becomes **debt** in a
nutrient‑specific ledger (`domain/ledger.py`), repaid on the right clock:

- **Calories** bank *weekly* — a light day leaves credit for a later one.
- **Protein** is *daily* and **can't be repaid later**, so the agent **nudges upcoming meals
  higher** rather than queuing a lump repair.
- **Iron / fibre** run on a **~30‑day clock** → the **"Repair meals" watch** softly biases
  the plan toward leafy/legume‑rich dishes until you're back in range (the UI now shows the
  concrete action, e.g. *"slipped in: +1 spinach dal · +1 rajma bowl"*).

So the recommendation is **specific dishes ordered into the plan**, surfaced in *Nutrition →
④ When you drift* — explicitly separated from the **variety** watch, which fixes *boredom*,
not a nutrient gap.

### 4.3 Diabetes ("juice always low / no sugar") — and other conditions
Medical conditions are **hard, per‑item rules applied before optimisation**
(`domain/allergens.py → MEDICAL_RULES`), so the solver can never pick an unsafe item:

| Condition | Rule in the model | User‑visible effect |
|-----------|-------------------|---------------------|
| `diabetes` | `item.sugar_g ≤ 20` | A sugary juice/dessert (>20 g added sugar) is **excluded outright** — only low/no‑sugar drinks survive. *"juice is always low."* |
| `hypertension` | item not tagged `high_sodium` | High‑sodium dishes removed. |
| `celiac` | no `gluten` in allergens | Gluten removed (also treated as an allergen). |

A user's profile stores two lists — `allergens` (e.g. `["peanut"]`) and `medical`
(e.g. `["diabetes"]`) — and **every** item must clear **all** of them; in household mode the
**union** of members' rules applies. Beyond the per‑item cap, **sugar & sodium are also daily
*hard* caps in the ledger** (never averaged or credited away like calories). **To add a new
condition:** add one lambda over item fields to `MEDICAL_RULES` and surface a 🔒 chip in
*Settings form → Locks*.

> **Exclude *and* instruct — don't over‑prune the menu.** Hard exclusion is the floor, not the
> whole answer. Many dishes are safe *with a request*, so at order time the agent also attaches
> **special instructions to the Swiggy cart** — *"no added sugar", "less salt / no extra salt",
> "no mayo"* — keeping adjustable items in the candidate set instead of dropping every sweet/salty
> dish outright. A diabetic keeps far more of the menu when *"hold the sugar syrup"* is an option,
> not just *"exclude all desserts."* So medical handling is **two‑layer**: (1) hard‑exclude the
> genuinely unsafe, (2) **soft‑adjust the rest via cart instructions**. *(This needs the menu to
> expose modifiable attributes / a free‑text instruction field — both exist on the Swiggy item
> model; the local MCP can be built against them now.)*

> **Design gap (P2, T):** this is the single strongest trust story — *"we can't break your
> diabetes/allergen rules, ever"* — yet it lives mostly in Setup. **Surface a 🔒 low‑sugar /
> allergen‑safe chip on the plan itself**, next to the meals, where the decision is shown.

## 5. The 5 issues hurting CONVERSION the most

1. **H1 — meal‑card density drowns the decision.** Users can't answer "what / where / how
   much" at a glance, so they hesitate to approve the whole week. *(Alignment is fixed; density isn't.)*
2. **No single, unmistakable approve path.** "Auto‑order ▶" sits in the grid rail while
   "Approve & auto‑order the week" lives on the dashboard — the money moment is split across
   two views. A **persistent sticky approve bar** (sessions · ₹ spent/left · one primary CTA)
   would carry it.
3. **L1 — no "optimising / error" feedback.** Pressing a button that spends real money with
   no visible progress or failure path suppresses commitment.
4. **F4 — the two‑view fork on arrival.** Making a new user choose Calendar grid vs Command
   dashboard before acting adds friction at the worst moment.
5. **Trust cues are far from the money.** The "we can never break your hard rules" guarantee
   (allergens, ₹ cap, diabetes sugar cap) is buried in Setup, not shown beside the approve CTA.

## 6. The 5 quick wins fixable today

1. **Default a "home" view** (recommend Calendar grid) and demote the other to a "view as"
   toggle — removes the arrival fork *(F4)*. *(Small.)*
2. **Surface the hard‑rule guarantee on the plan** — a 🔒 *allergen‑safe · ≤₹2,000 · low‑sugar*
   chip next to the approve CTA, reusing data the model already enforces *(§4.3, conversion #5)*.
3. **Add a "still learning" cold‑start state to Taste DNA** mirroring the Nutrition ledger's,
   so week‑1 users aren't shown fabricated history *(L2)*.
4. **Bump contrast/size of the figures users must trust** (₹, ★, kcal — today ~11–12px muted
   on cream) and reserve the display font for headings.
5. **Collapse each meal card to dish · ₹ · status by default**, moving portions/spin/area into
   the tap‑to‑open sheet — the highest‑leverage step toward "approve without reading docs" *(H1)*.

---

*Method footnote: prototype booted headless (Playwright/Chromium) and validated with its own
jsdom harness (`project/test-merged.mjs`, all green). The Flask app boots independently
(`python run.py`). Fixes in §1 are committed alongside this report.*
