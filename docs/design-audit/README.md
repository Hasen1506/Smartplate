# SmartPlate — Full-Site UI/UX Design Audit

*Audited as a senior design lead on day one. The question is not "is it pretty?" — it's
**can a normal user understand the product, trust it, and finish the core action
(review the week → approve & auto-order) without reading docs?***

---

## 0. What was audited & how it was booted (honest method notes)

There is **no conventional web app / dev server** in this repo — it's a *design handoff
bundle*: two static `.dc.html` prototypes that load React from a CDN at runtime. Those
prototypes **are** the product's front end, so I booted and audited them:

- **Boot:** a small local static Node server + **headless Chromium** (`@sparticuz/chromium`
  via `puppeteer-core`). Playwright/Chromium's normal browser CDNs were **HTTP-403
  blocked** in this environment, so the browser binary was sourced from an npm tarball.
- **Render fix required:** the prototypes load React/ReactDOM from **unpkg**, which was
  **403-blocked here** — the page rendered **blank** until I vendored React from npm and
  served it locally. *(This is itself finding **F1**, a P0.)* Google Fonts was reachable,
  so the hand-drawn type rendered correctly.
- **Coverage:** 2 screens × all tab states = **7 captures**, each at 1460-wide,
  `deviceScaleFactor:2`, full-page. Each prototype lays out its **desktop and mobile**
  frames together, so every capture covers **mobile layout** too. Core flows covered:
  *plan → review → approve/auto-order*, *program the rules*, *Taste DNA*, *Nutrition*.

### Screenshots (`./assets/`)
| # | Screen / state | File |
|---|---|---|
| 01 | Weekly Plan — **Calendar grid** (default; selector + over-cap banner + 7-day grid + mobile) | [01-weekly-calendar-grid.png](./assets/01-weekly-calendar-grid.png) |
| 02 | Weekly Plan — **Command dashboard** (status ring, command bar, tiles, burn-down) | [02-weekly-command-dashboard.png](./assets/02-weekly-command-dashboard.png) |
| 03 | Setup — **Settings form** (the dense "see everything" view) | [03-setup-settings-form.png](./assets/03-setup-settings-form.png) |
| 04 | Setup — **Guardrails & dials** | [04-setup-guardrails-dials.png](./assets/04-setup-guardrails-dials.png) |
| 05 | Setup — **Plain-English program** | [05-setup-plain-english.png](./assets/05-setup-plain-english.png) |
| 06 | Setup — **Taste DNA** (flavour-fingerprint radar) | [06-setup-taste-dna.png](./assets/06-setup-taste-dna.png) |
| 07 | Setup — **Nutrition** (TDEE chain, kcal bars, debt/credit ledger) | [07-setup-nutrition.png](./assets/07-setup-nutrition.png) |

**Severity:** P0 = broken / blocks the core action / breaks trust badly · P1 = serious,
fix this cycle · P2 = should fix · P3 = polish.
**Hurts:** **U**nderstanding · **T**rust · **C**onversion.

---

## 1. First impressions

| ID | Sev | Hurts | Finding (evidence) | Specific fix |
|----|-----|-------|--------------------|--------------|
| **F1** | **P0** | T, U | **Hard, single-source CDN dependency for React, pinned with SRI, no fallback.** When unpkg was unreachable (it was — 403), the **entire app rendered as a blank dotted page** (`window.React undefined`, `[dc] failed to load React or boot`). A real user on a flaky network or a blocked CDN sees nothing. | Self-host/bundle React (and fonts) with the app; or add a fallback loader (CDN → local). Don't gate first paint on one third party + an exact SRI hash. |
| **F2** | **P1** | T | **The default Weekly Plan loads already "₹40 OVER cap"** — a red alarm banner on first view (01). The agent appears to propose an *invalid, over-budget* plan before the user touches anything. | The agent's *proposed* plan must be within the cap by default. Reserve the over-cap banner for **user-induced** overages (after a spin/swap). |
| **F3** | **P2** | U | Reads as a **wireframe artifact**, not a product: "lo-fi wireframe · v1" badge, red margin annotations, side-by-side DESKTOP/MOBILE frames, fake browser chrome (01–07). Fine for review; misleading if shipped. | Strip wireframe scaffolding for the product build; keep one responsive layout, not two mock frames. |

## 2. Navigation

| ID | Sev | Hurts | Finding | Specific fix |
|----|-----|-------|---------|--------------|
| **N1** | **P2** | U | Top-level nav is **two small, low-contrast pills** ("Weekly plan" / "Setup & rules") with no app bar, no brand, no household/user context (01–07). Weak orientation. | Real top nav: brand, clear active state, household/area context, and account. |
| **N2** | **P2** | U | **Inconsistent product voice** — "SmartPlate · Weekly Plan" vs "Program the agent" with no brand. | *Fixed on the spot:* added a "SmartPlate · Setup & rules" eyebrow to the Setup header. Standardise the title pattern across screens. |

## 3. Visual hierarchy

| ID | Sev | Hurts | Finding | Specific fix |
|----|-----|-------|---------|--------------|
| **H1** | **P1** | U, C | **Meal cards are over-dense.** Each ~135px cell stacks: meal label, tag, dish, outlet/price/rating, area chip + serviceability, ▢/⌂/⊘ toggle, ↻ spin + menu, flags, "your pick" — ×21 cells on screen (01). The primary glance ("what/where/how much") competes with 6+ controls per card. | Progressive disclosure: default card = dish + price + status only; reveal spin/mode/area/menu on hover or in a tap-to-open detail sheet. |
| **H2** | **P2** | U | The causal link **"the Step-1 selector drives the plan below"** is carried by a red annotation, not the design (01). | Group selector + result; reflect the selection in the plan's heading; show a subtle "updated" pulse on recompute. |
| **H3** | **P2** | U | A manual **"Re-optimise"** button sat next to copy saying everything "recomputes live" — and its handler actually just scrolled to the grid (mislabeled). | *Fixed on the spot:* relabeled to **"See the plan ↓"** and demoted to secondary. Longer term: drop manual re-optimise or split "live preview" vs "commit." |

## 4. Component consistency

| ID | Sev | Hurts | Finding | Specific fix |
|----|-----|-------|---------|--------------|
| **C1** | **P1** | C | **Competing dark/high-emphasis buttons** dilute the one conversion action: selector CTA, grid "Auto-order ▶", "Approve & auto-order the week", dashboard "Send/Approve" (01–02). | *Fixed on the spot:* demoted the selector CTA. Rule: **exactly one primary per view** = the approve/auto-order CTA; everything else secondary/tertiary. |
| **C2** | **P3** | U | Tab systems differ: Weekly Plan tabs are plain; Setup tabs are color-coded (DNA purple, Nutrition green) (01 vs 06/07). | Unify tab styling + active treatment across both screens. |

## 5. Loading / empty / error states

| ID | Sev | Hurts | Finding | Specific fix |
|----|-----|-------|---------|--------------|
| **L1** | **P1** | T, U | **No loading, empty, or error states exist anywhere.** The plan is always fully populated; "live re-optimising" shows no progress; there's **no surfaced error** when a provider is down or *nothing is serviceable to an area* (the cook/skip fallback lives only in logic). For an agent that **orders and pays**, silent failure is a trust killer. | Design: *optimising* (skeleton), *empty* (new user / no sessions selected), and *error* (provider down, nothing serviceable, over budget) states with a clear recovery action. |
| **L2** | **P2** | T | **Cold-start is undefined.** Every screen shows a fully-trained state — Taste DNA "142 orders · 38 skips," a full 7-day nutrition ledger (06, 07). A brand-new user would see confident data they never generated. | Explicit "still learning / building your DNA" states for week 1; don't fabricate precision before data exists. |

## 6. Trust signals

| ID | Sev | Hurts | Finding | Specific fix |
|----|-----|-------|---------|--------------|
| **T1** | **P1** | T, C | For an agent that **auto-orders and pays**, reassurance was thin and far from the CTA — no clear "nothing is ordered until you approve / cancel anytime" near the button; the **autonomy mode** (auto-order vs propose & approve) lives only in Setup, invisible on the plan. | *Fixed on the spot:* added a "🛡 Nothing is ordered until you approve — skip/swap any item, cancel anytime" caption beside the dashboard approve CTA. Also surface the current autonomy mode on the plan, and show order status + cancel after approval. |
| **T2** | **P2** | T | The **"Tell SmartPlate…" command bar is a non-functional placeholder**, yet it's the command dashboard's headline interaction (02). It promises NLP control the product may not deliver. | Only show it if it works; otherwise replace with the concrete quick-action chips already present. |
| **T3** | **P2** | T | Safety cues exist but are **quiet** — ★ ratings, "0 allergen conflicts," "safe-retry," "🔒 hard rules" (01–04). The strongest trust story (allergens/budget can *never* be violated) is buried in Setup. | Elevate the "we can't break your hard rules" guarantee where meal decisions are shown, not only in Setup. |

## 7. Conversion paths

| ID | Sev | Hurts | Finding | Specific fix |
|----|-----|-------|---------|--------------|
| **V1** | **P1** | C | The **single conversion action** (approve & auto-order the week) is muddied by 3–4 near-identical CTAs and a long dense scroll before reaching it (01–02). | A **persistent/sticky approve bar**: sessions · ₹ spent/left · one primary "Review & approve." |
| **V2** | **P2** | C | **Mobile conversion is thinner than desktop.** Spin/mode/area edits exist in the mobile grid, but the mobile command dashboard is sparse (status + mini burn-down + approve) and its command bar is fake (02). | Make the approve flow + edit affordances first-class on mobile; drop the dead command bar. |
| **V3** | **P2** | U, C | **Icon-only controls** (▢ ⌂ ⊘ ↻ 🏠💼✈) depend on a legend that scrolls away and tooltips that don't exist on touch (01). Users won't attempt edits they can't decode — the chat history flagged this too. | Label-on-first-use, a persistent mini-legend, and text labels at wider breakpoints. |
| **V4** | **P3** | U | **Low-contrast, tiny data text** — 11–12px `#7a746a` on `#faf8f2` in a hand-drawn font — for exactly the figures users must trust (prices, ★, kcal) (01, 03, 07). | Increase size/contrast for data; reserve the display font (Gaegu/Caveat) for headings, use a legible face for figures. |

---

## 8. Fixed on the spot (safe: copy / hierarchy only — no payment, delete, or publish actions touched)

1. **Button hierarchy + honest label** — demoted the selector's dark "Re-optimise ▶"
   (which only scrolled to the grid) to a secondary **"See the plan ↓"**, so it no longer
   competes with the real CTA. *(H3, C1)*
2. **Trust reassurance at the CTA** — added "🛡 Nothing is ordered until you approve —
   skip/swap any item, cancel anytime" beside the approve button. *The order/auto-order
   button itself was left untouched.* *(T1)*
3. **Brand consistency** — added a "SmartPlate · Setup & rules" eyebrow to the Setup
   header so naming matches the Weekly Plan. *(N2)*

*(Bigger items — density, sticky approve bar, loading/empty/error states, default-within-
budget — are recommendations only, above.)*

---

## 9. The 5 issues hurting CONVERSION the most

1. **V1 — the approve action is buried & duplicated.** No single, persistent "review &
   approve" path; 3–4 competing CTAs. The money moment is the hardest to find.
2. **H1 — meal-card density drowns the decision.** Users can't quickly answer
   "what/where/how much," so they hesitate to commit the whole week.
3. **T1 — weak "you're in control" reassurance at the money action.** Auto-ordering &
   paying with no visible "nothing happens until you approve" suppresses commitment.
4. **L1 — no loading/error states.** Silent failure (or no feedback during "optimising")
   makes users distrust pressing a button that spends real money.
5. **F2 — the default plan is shown over-budget.** Leading with a red "OVER cap" alarm
   makes the agent look broken before the user even starts.

## 10. The 5 quick wins fixable today

1. **One primary per view.** Finish demoting all non-approve CTAs to secondary so
   "Approve & auto-order" is unmistakably *the* button. *(started — C1/V1)*
2. **Keep the "nothing ordered until you approve" reassurance** and add the current
   **autonomy mode** chip on the plan header. *(T1 — caption already added)*
3. **Persistent mini-legend** for ▢ / ⌂ / ⊘ / ↻ / 🏠💼✈ (sticky, not scroll-away) so
   edits are discoverable without docs. *(V3)*
4. **Default the proposed plan within the ₹2,000 cap;** show the over-cap banner only
   after a user override pushes it over. *(F2 — small init/data change)*
5. **Bump contrast & size of data text** (prices, ★, kcal) and remove the React CDN
   single-point-of-failure by vendoring it locally (also fixes blank-screen risk). *(V4, F1)*
