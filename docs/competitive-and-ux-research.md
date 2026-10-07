# SmartPlate — Competitive Differentiation & UX Research

> Deep-research report (June 2026). Two questions, weighted equally: **(A) is there a real moat
> vs. Zomato/Swiggy?** and **(B) what do the open UX decisions resolve to?** Researched across
> five angles — Zomato/Swiggy 2026 reality, India nutrition/coaching apps, global meal-planning/
> macro apps, moat/defensibility theory, and evidence-based UX — then adversarially verified.
>
> **Method caveat (read this):** direct page fetches were blocked (HTTP 403) the whole session, so
> every quote here is from search-engine page extracts, cross-corroborated across ≥2 independent
> outlets for each high-confidence claim. Treat city counts, exact thresholds, and pricing as
> "indicative, re-check live"; treat the launch facts and UX principles as solid. Sources at the end.

---

## TL;DR — the bottom line

1. **Your fear is ~70% justified at the *feature* level.** By mid-2026, scheduling, per-dish
   macros, high-protein/low-cal filters, meal subscriptions, *and even AI-orders-for-you* all ship
   on Zomato and/or Swiggy — and **a nutrition app that orders food already exists in India
   (HealthifyMe).** You cannot differentiate on "we have feature X." That ship has sailed.
2. **The intersection is still empty, and that's the real white space:** nobody plans your **whole
   week**, across your **existing favourite restaurants**, to **your** kcal/protein **target**,
   under a **budget cap**, with **autonomous substitution** and **cross-time compounding**. Every
   competitor has *pieces* (filters, a score, a subscription, a recommendation); none has the
   *stateful optimiser*.
3. **But the optimiser is not a moat by itself** — it's a MILP anyone can build, and "Taste DNA"
   data **asymptotes fast** (a16z/NfX). Food *aggregation* is a commodity and a startup graveyard.
   So differentiation ≠ defensibility.
4. **The durable moat is workflow + habit + segment**, exactly as your `ROADMAP §0` already says:
   become the **system-of-record for your food (budget + nutrition) and the default daily
   decision**, and own the **cost-constrained "Tight-Week" segment incumbents are structurally
   disincentivised to serve.** Defend with retention and execution, not features.
5. **The clock is real but the threat is also an opening.** Swiggy's Jan-2026 MCP launch is heading
   toward planning — *and* it is the official rail your `SPEC §2` deep-link/hand-off needs.
   **Be the stateful brain that drives Swiggy's MCP, not another app that fights it on ordering.**

---

## Part A — Competitive reality, 2026 (the "it already exists" audit)

### What Zomato / Swiggy already ship

| Capability you planned | Already exists? | Detail (2024–2026) |
|---|---|---|
| **Schedule an order ahead** | ✅ Both | Zomato: 2 h–2 days ahead, 35k+ restaurants, 30 cities (Oct 2024). Swiggy: scheduled 30-min slots (earlier). |
| **Recurring / meal subscription** | ✅ Swiggy | "Swiggy Daily" homestyle meals, daily/weekly/monthly, pause/skip/swap — *but* launched 2019, shut 2020, relaunched 2024 (weak traction history). |
| **Per-dish calories + macros** | ✅ Both | Zomato **Healthy Mode** (Sept 2025): cal/protein/carbs/fat/fibre on every dish. Swiggy **EatRight** (Jan 2026): 1.8M+ tagged dishes, 200k+ restaurants, 50+ cities, **~1 in 9 orders**. |
| **Protein / calorie *target* filter** | ✅ Zomato (partial) | Healthy Mode lets you **set preferred protein & carb ranges** and recommendations adjust. This is closer to "targets" than expected — but it's **per-order, not a plan.** |
| **Health/diet filters** | ✅ Both | High Protein, Low Cal, Low Carb, Low Fat, High Fibre, No-Added-Sugar, gluten-free, etc. |
| **Budget / price filter** | ✅ Both | Price range, "cost for two," sort by cost low→high. ~5–6 filter dimensions total. |
| **AI orders for you** | ✅ Swiggy | **MCP launch Jan 27 2026**: order Swiggy Food/Instamart/Dineout via **ChatGPT, Claude, Gemini** — AI searches, compares prices, applies offers, builds cart, places order (COD-only, early/buggy). |
| **A nutrition app that orders** | ✅ HealthifyMe | Since Dec 2023 (Ria 2.0): order Ria-recommended, diet-aligned meals **in-app via Swiggy**. Live as of Apr 2025. Built on 2020 "FitPicks." |

### What still does **not** exist (your white space)

- **A weekly meal-planning *calendar* across arbitrary favourite restaurants.** The "Swiggy week
  planner" mockups online are *student/design concepts*, not shipped. Swiggy Daily is a homestyle
  *subscription*, not "plan my week from the places I already love."
- **Budget as a cross-time *optimisation*** (a rolling cap that compounds Mon→Fri), not a one-shot
  filter. Among *all* apps studied, only Eat This Much treats cost as a planning constraint — and
  it doesn't deliver.
- **Dish-first search that adds directly.** Search is **restaurant-first**: "biryani" returns a
  list of *restaurants*; you open one to add. Even Swiggy's dish landing pages are restaurant lists.
- **Optimisation to *your* personalised target across *your* favourites.** Zomato/Swiggy *curate
  and score*; HealthifyMe *recommends*; FITTR/ToneOp/Food Darzee lock you to *their* kitchens. None
  optimises your real target × budget over the open restaurant catalogue you already use.
- **Autonomous substitution above a rating floor + the nutrition/budget ledger.** Requires
  persistent state a stateless recommender or a per-order filter can't keep.

### The India nutrition-adjacency (the subtler threat)

- **HealthifyMe is the closest competitor** and has *already crossed* into ordering. But it
  **recommends**; it has no budget engine and no cross-time optimiser. It's now diversifying into
  GLP-1/obesity-care (Novo Nordisk, Dec 2025) — pulling *away* from "optimise my takeout," not toward it.
- **EatFit → Curefoods pivoted *away* from macros** into a mainstream multi-brand cloud-kitchen IPO
  play ("wants to eat Domino's"; ₹800 Cr DRHP, FY25 rev ₹745.7 Cr). Less of a macro-target threat
  than the brand history implies.
- **FITTR / ToneOp Eats / Food Darzee** do macro-tied meals — but as **subscriptions from their own
  kitchens**, geographically limited, not across your favourites.

**Read:** the threat vectors are *recommendation/curation* (HealthifyMe, Zomato, Swiggy) and
*proprietary kitchens* (FITTR/ToneOp/EatFit). The **optimisation-over-open-catalogue-across-time**
lane is genuinely open. It is also narrow and copyable — which is Part B.

---

## Part B — The moat, honestly

### What is **not** defensible (don't build the story on these)

- **"We show you healthy food / macros."** Taken (Zomato Healthy Mode, Swiggy EatRight).
- **"AI orders for you."** Table stakes — Swiggy shipped it via MCP in Jan 2026. Your own
  `ROADMAP §1` already calls this "table stakes by 2026"; the research confirms it *arrived*.
- **"Taste DNA is the moat."** This is the big correction. a16z (*Empty Promise of Data Moats*) and
  NfX show most "data network effects" are **data *scale* effects that asymptote** — often a few
  hundred–thousand data points capture ~90% of the value, so a competitor matches "good enough"
  personalisation fast. Your `ROADMAP §1` *cites this paper* — yet `ROADMAP §4`/`opt-ux §8` still
  lean on Taste DNA as "the moat." **Demote it: Taste DNA is a *retention enhancer and a visible
  trust artifact*, not a defensive wall.**
- **"We aggregate Swiggy/Zomato."** Aggregation is a commodity and a graveyard: Grubhub's CEO called
  delivery "the dumbest business you could ever be in"; DoorDash "no moat other than scale"; Grubhub
  fell 70%→<15% share. SpoonRocket, Sprig, Munchery, Maple died on unit economics; **MealMe
  abandoned the consumer aggregator for a B2B API**; **PlateJoy was acquired and shut (Jul 2025).**

### What **is** defensible (build the story on these)

1. **Workflow integration / system-of-record.** a16z's 2025 *Moats Before (Gross) Margins* locates
   AI-app defensibility in **owning the end-to-end workflow** and being the **central system**, plus
   proprietary data *generated in a closed loop*. Translation for you: be the place a user's **food
   budget ledger + nutrition ledger** live and are *run from*. That's hard to rip out — not because
   of the data volume, but because you're the workflow.
2. **Habit / the default daily decision.** For a daily-use consumer app this is the most credible
   moat (Hooked; Reforge compounding loops). Habits form in **week 1 or are lost**; aim for
   consumer-grade 6-month retention (~30–40%+). Your **autopilot + visible week-1 ₹/kcal-saved** is
   exactly the right lever — make the saving the hero, not a footnote.
3. **The Tight-Week / budget-inversion segment.** This is your most *structurally* defensible angle.
   Swiggy/Zomato monetise **order frequency + surge**; a mode whose whole job is to make you **spend
   less** is *adversarial to their economics*, so they're disincentivised to build it well. Serving
   the cost-churned user is a wedge incumbents won't chase. (No external source needed — it's an
   incentive argument — but it's the sharpest point in your deck.)
4. **Cross-time compounding (budget portfolio + nutrition accountant).** The rolling ledger
   (calories weekly, protein daily, micros ~30-day; budget that banks Mon→Fri) **requires persistent
   state** a stateless recommender or per-order filter cannot keep. Genuinely novel framing, and it
   *needs* the optimiser, which ties features to the defensible workflow.

### Positioning (sharper than the current docs)

Your `ROADMAP §0` line is right and should be the *only* headline:

> **"The optimiser that runs your food like a budget you never think about."**

Drop, from the front of the story: "healthy food," "AI that orders," and "Taste DNA." Lead with
**budget autopilot + cross-time optimisation + the cost-constrained segment**, and let nutrition be a
*constraint the optimiser respects*, not the pitch. Differentiation = the **integration**;
defensibility = **habit + workflow + the unloved segment**, defended by retention/execution.

---

## Part C — The threat, the clock, and the reframe

Swiggy's MCP launch (Jan 27 2026) is the single most competitively relevant event: it is openly
positioned toward **future meal planning, dietary preferences, and recurring orders** — i.e. your lane.
Two implications:

- **The window is narrowing.** "Plan + cart + order" will be commoditised; you must be in-market
  with the *cross-time optimiser + budget segment* before the planning layer matures on Swiggy.
- **But MCP is also your rail, not just your rival.** Your `SPEC §2.2` ladder
  (partner API → aggregator → **deep-link/cart hand-off** → simulated) now has an **official
  mechanism**: SmartPlate can be the **stateful brain that drives Swiggy's MCP** — we optimise the
  week, MCP places it. This **de-risks the #1 dependency** (order placement / ToS) flagged in
  `FEASIBILITY §4` and turns "we might not get ordering access" into "we ride the rail Swiggy just
  built." Strongly recommend updating `SPEC §2.2`/`FEASIBILITY §4` to name MCP as the primary
  near-term execution route.

---

## Part D — Your rants, answered

Legend: **[Solved]** = already decided in your docs (research validates) · **[Refine]** = decided
but needs adjustment · **[Open]** = genuinely unresolved, recommendation below.

### 1. Budget — "user may or may not have a number" · **[Solved — validated]**
Your **budget recommender** (`opt-ux §5`: Floor/Usual/Variety bands via inverse optimisation) is the
right answer and is *rare in the market* — Eat This Much's "daily price limit" is the only comparable
pattern found, and it has no delivery. **Keep it. Default to the computed number; never force the
user to guess.** Frame as a trade-off slope ("+₹300 buys 2 new dishes; −₹230 = a cook day"), per §5.

### 2. Autonomy modes + the "rename Variety → Max Nutrition" question · **[Refine]**
You're conflating **two different axes** — and that *is* the source of your own confusion:
- **Mode** = *what's allowed to give* (the objective/constraint switch, `opt-ux §2`): cost vs.
  nutrition vs. both.
- **Variety/novelty** = a *separate small dial* (Usual ↔ Adventurous, `opt-ux §4.1/§10`).
Naming the **mode** "Variety" collides with the novelty dial. **Rename the modes to one stable,
mutually-exclusive set** (consistency & recognition: Nielsen #4/#6, Jakob's Law). Recommendation:

> **Save** (min cost) · **Balance** · **Nourish** (max nutrition-fit)  *(= your Tight/Balanced/Comfort)*

"Max Nutrition" is fine too, but note your mode 3 also buys *taste/variety*, not only nutrition — so
"Nourish"/"Best" reads less narrowly. **Keep "variety" only as the novelty *level*, never a mode name.**

### 3. Rating — floor vs off vs soft · **[Solved — strongly validated]**
Baymard is unambiguous: a **minimum-rating floor** (single-select, mutually-exclusive links, e.g. "4★
& up"), **never** an empty dead-end (68% of sites fail this; always offer recovery). This *exactly*
matches your design (`opt-ux §10`: soft ★ above a hard safety floor + recommender-suggested floor +
the over-cap conflict prompt as the recovery path). **Decision: floor with a computed default
(~3.8–4.0), adjustable; no "off"; if the floor makes the plan infeasible, fire your conflict prompt
(raise budget / add outlet / relax floor) — never return garbage or nothing.**

### 4. Dish-first search ("biryani" → selectable dish list, not restaurants) · **[Open — and your best UX edge]**
**Build it.** This is the most evidence-backed differentiator in your whole rant:
- Zomato/Swiggy are **restaurant-first** (verified) — even dish pages list restaurants.
- Baymard's food-delivery research + the DoorDash case study say users searching a *specific item*
  want to **compare items and add directly**, not drill into each menu; and **94% of mobile sites
  fail "search within current scope."**
Recommendation: typing "biryani" returns a **ranked list of specific dishes** across your
serviceable + favourite outlets, each showing **₹ · kcal · protein · ★** inline, **add directly**.
Autocomplete should distinguish **dish vs cuisine vs restaurant** scope. This *is* your candidate set,
surfaced as text — cheap to build on the engine you already have.

### 5. Day-plan vs week-plan + "day plan freezes the rest" · **[Refine]**
The **horizon toggle** (day/week/month) is already built (`opt-ux §10`) and **time-awareness is a
must** (highlight Today + current window, `§6.3`) — validated. But **don't *grey-out-and-block*** the
other panes: GOV.UK warns disabled/greyed controls have poor contrast + keyboard/clarity problems.
Use **progressive-disclosure focus** instead (NN/g): **de-emphasise** the out-of-scope days and scope
the workspace to today, keeping a **clear one-tap "switch to week."** Focus, don't freeze.

### 6. Protein/calorie as sliders? · **[Solved — strongly validated]**
**No sliders for kcal/protein.** NN/g: sliders are for "approximate is fine," not exact values; use
numeric inputs with **computed defaults** (Power of Defaults). MacroFactor — the category leader —
**hides the calorie slider entirely** and *computes* the number. Your `opt-ux §4.1` (kill 5 sliders →
1 mode + 2 leans; targets are numbers from BMR) is dead right. **Bonus borrow:** MacroFactor's
**dynamic target** (re-estimate over weeks from intake vs. trend) and **"adherence-neutral"** design
(no red shame bars) — both fit your ledger and would feel premium.

### 7. Calendar layout — left = recommendations, right = day grid · **[Open — validated]**
**Master-detail / split-view** is the standard pattern (Apple HIG, Microsoft): list/options one side,
detail/plan the other, **collapsing to a single column on mobile** (your existing day-picker).
Recommendation: **left = candidates/recommendations for the focused slot/day; right = the week grid;**
mobile collapses to the day view you already built. Sound as proposed.

### 8. Restaurant display + "usual / favorites / variety / recommended" naming confusion · **[Refine]**
Your instinct that this is "quite confusing" is correct, and the fix is naming discipline
(consistency/recognition). **Collapse to two stable labels and one mode dial:**
- Every candidate is either **Usual** (your repeats/favourites) or **New** (✦ novel) — *two labels,
  everywhere, all modes.* (Matches your `opt-ux §10` "⭐ usual · ✦ new"; you already rejected V/U badges.)
- The **mode** (Save/Balance/Nourish) changes the **mix** of Usual vs New, **not the labels.**
- **"Recommended" = "the pick"** (the optimiser's single choice). Don't make it a third bucket.
**Don't show restaurant "buckets" whose names change per mode** — that's the confusion you felt.

### 9. "Show 4 options? 4 usual? 4 variety? or restaurants + filters?" · **[Solved]**
Your density instinct (`opt-ux §6.2`) is right and choice-overload research backs it (Iyengar/Lepper;
Hick's Law — with the caveat that choice-overload is context-dependent, not a universal law). **Per
slot: one pick + ↻ spin (next-best) + menu ▾ (the ranked list where Usual/New live).** *Not* four
parallel cards. The **"3 options" framing belongs at the mode/budget level** (pick Save/Balance/Nourish
→ get a plan; or the Floor/Usual/Variety budget bands), **not as 4 cards per meal.**

---

## Part E — Risks to internalise

- **Differentiation ≠ defensibility.** The optimiser is copyable (your own `FEASIBILITY`: it's a
  millisecond MILP). Win on **habit + retention + the budget segment**, not cleverness.
- **The category is a graveyard.** SpoonRocket/Sprig/Munchery/Maple (unit economics), MealMe
  (consumer→B2B), PlateJoy (acquired→shut). Most died on **economics and retention**, not features.
  Your asset is being **software on top** (no kitchens/fleet) — keep it that way; do **not** become
  an operator.
- **Retention is the real scoreboard.** Target ~30–40%+ 6-month retention; prove week-1 value
  (₹/kcal saved) or you're in the graveyard regardless of how good the optimiser is.
- **The window is open but closing** as Swiggy's MCP planning layer matures. Speed + the
  incentive-misaligned budget segment are your protection.

---

## Sources

**Zomato / Swiggy 2026**
- Zomato order scheduling (2 h–2 days) — https://www.businesstoday.in/technology/news/story/zomato-rolls-out-order-scheduling-feature-for-pre-planned-deliveries-451643-2024-10-26
- "After Swiggy, Zomato launches advance scheduling" — https://yourstory.com/2024/10/zomato-launches-advance-order-scheduling-following-swiggy
- Swiggy Daily meal subscription — https://www.thenewsminute.com/article/swiggy-launches-meal-subscription-app-daily-everyday-home-style-food-102913 ; shutdown 2020 — https://entrackr.com/2020/09/exclusive-swiggy-shuts-down-homestyle-food-ordering-app-swiggy-daily/ ; relaunch — https://www.outlookbusiness.com/corporate/ahead-of-ipo-swiggy-restarts-daily-a-homestyle-food-ordering-service
- Zomato Healthy Mode (scores + macros + protein/carb range filters) — https://www.business-standard.com/companies/news/zomato-launches-healthy-mode-feature-nutritional-meal-ratings-125092900533_1.html ; official — https://www.zomato.com/blog/zomato-healthy-mode-explained-scores-nutrition-info-smart-filters/
- Swiggy EatRight (Jan 5 2026; 1.8M dishes, 200k restaurants, 50+ cities) — https://www.business-standard.com/companies/news/swiggy-launches-eatright-in-over-50-cities-targets-health-conscious-users-126010500674_1.html ; https://www.storyboard18.com/brand-marketing/swiggy-launches-eatright-to-tap-rising-demand-for-health-focused-food-across-tier-2-cities-87039.htm
- Swiggy Health Hub (2020 origin) — https://www.bwdisrupt.com/article/swiggy-launches-health-hub-to-make-healthy-eating-convenient-303545
- Swiggy AI ordering via MCP / ChatGPT / Claude / Gemini (Jan 27 2026) — https://www.analyticsinsight.net/news/swiggy-orders-now-possible-through-chatgpt-gemini-claude-40000-items-available ; hands-on "not quite working" — https://www.medianama.com/2026/01/223-ordering-chatgpt-swiggy-services-working/
- Search is restaurant-first — https://blog.swiggy.com/food/a-beginners-guide-to-ordering-food-online/ ; dish landing page = restaurant list — https://www.swiggy.com/chicken-biryani-dish-restaurants-near-me
- Swiggy Bolt (10-min) scale — https://www.business-standard.com/companies/news/swiggy-expands-10-minute-food-delivery-service-bolt-to-over-500-cities-125050201122_1.html

**India nutrition/coaching apps**
- HealthifyMe orders in-app via Swiggy (Ria 2.0, Dec 2023) — https://inc42.com/buzz/healthify-joins-forces-with-swiggy-to-offer-tailored-meals-for-users/ ; afaqs (Dec-15 launch + grocery plans) — https://www.afaqs.com/news/mktg/healthify-rebrands-itself-launches-ai-coach-collaborates-with-swiggy
- HealthifyMe origin "FitPicks" (2020, 700+ restaurants, 249% surge) — https://www.businessinsider.in/business/startups/news/swiggy-partners-with-healthifyme-after-its-health-food-orders-rise-by-249/articleshow/73270002.cms
- Ria 3.0 + 500 dietitians + Novo Nordisk obesity PSP (Dec 2025) — https://analyticsindiamag.com/ai-news-updates/healthify-novo-nordisk-india-launch-ai-enabled-patient-support-program-for-obesity-care/
- EatFit → Curefoods pivot — https://the-ken.com/story/curefoods-is-no-longer-just-eatfit-it-wants-to-eat-dominos/ ; DRHP ₹800 Cr / FY25 ₹745.7 Cr — https://entrackr.com/news/curefoods-files-drhp-to-raise-rs-800-cr-in-fresh-issue-founder-ankit-nagori-to-skip-ofs-9448193
- FITTR macro meal subscription — https://www.fittrackai.in/blog/fittr-app-review-2026-pros-cons-and-best-alternative ; ToneOp Eats — https://toneopeats.com/

**Meal-planning / macro UX patterns**
- Eat This Much budget ("daily price limit") — https://help.eatthismuch.com/help/how-can-i-get-lower-budget-meal-plans ; calorie calculator — https://www.eatthismuch.com/calculator ; day-free/week-premium + variety tradeoff — https://www.plantoeat.com/blog/2023/10/eat-this-much-app-review-pros-and-cons/ , https://help.eatthismuch.com/help/my-meals-arent-getting-much-variety-whats-up-with-that
- MacroFactor dynamic TDEE / hides calorie slider — https://help.macrofactorapp.com/en/articles/20-expenditure , https://help.macrofactorapp.com/en/articles/91-program-styles ; adherence-neutral — https://macrofactorapp.com/adherence-neutral/
- Cronometer targets-as-rings/defaults — https://support.cronometer.com/hc/en-us/articles/360060170532-Nutrient-Targets
- Mealime week-first / recipe-fatigue gap — https://www.plantoeat.com/blog/2023/04/mealime-app-review-pros-and-cons/
- PlateJoy onboarding quiz / shutdown — https://mealthinker.com/blog/platejoy-alternative

**Moat / defensibility**
- a16z "Empty Promise of Data Moats" — https://a16z.com/the-empty-promise-of-data-moats/ (summary tweet — https://x.com/a16z/status/1126490186106302464)
- NfX "truth about data network effects" (asymptote) — https://www.nfx.com/post/truth-about-data-network-effects
- Abraham Thomas "Data and Defensibility" (recommenders not a moat) — https://pivotal.substack.com/p/data-and-defensibility
- a16z "Moats Before (Gross) Margins" (workflow/system-of-record) — https://a16z.com/moats-before-gross-margins/
- Stratechery Aggregation Theory — https://stratechery.com/2015/aggregation-theory/
- Grubhub "dumbest business" — https://www.cnbc.com/2019/12/13/grubhub-uber-eats-and-doordash-drove-an-online-food-delivery-boom.html ; DoorDash "no moat but scale" — https://ramkumarssite.com/2020/12/21/my-analysis-of-doordash-ipo/
- Startup outcomes: Sprig — https://techcrunch.com/2017/05/26/on-demand-food-startup-sprig-is-shutting-down-today/ ; SpoonRocket — https://www.inc.com/kenny-kline/how-spoonrocket-blew-135-million-and-ended-in-bankruptcy.html ; Munchery — https://techcrunch.com/2018/05/11/munchery-shuts-down-operations-in-la-new-york-and-seattle/ ; MealMe consumer→B2B — https://techcrunch.com/2024/10/31/mealme-startup-integrating-food-ordering-tech-into-app-picks-up-8m/
- Habit/retention: Hooked — https://www.nirandfar.com/hooked/ ; first-week habit — https://amplitude.com/blog/the-hook-model ; retention benchmarks — https://www.caseyaccidental.com/p/what-is-good-retention-an-exhaustive-benchmark-study-with-lenny-rachitsky

**UX evidence**
- Rating filters / no dead-ends — https://baymard.com/learn/ecommerce-filter-ui
- Dish-vs-restaurant search — https://medium.com/design-bootcamp/case-study-enhance-ux-of-food-delivery-app-doordash-1824a5b46fd3 ; search-within-scope (94% fail) — https://baymard.com/blog/search-within-current-category ; food-delivery research — https://baymard.com/research/online-food-delivery
- Sliders for approximate only — https://www.nngroup.com/articles/gui-slider-controls/ , https://www.nngroup.com/articles/sliders-knobs/ ; Power of Defaults — https://www.nngroup.com/articles/the-power-of-defaults/
- Hick's Law — https://lawsofux.com/hicks-law/ ; choice overload (jam study) — https://pubmed.ncbi.nlm.nih.gov/11138768/
- Progressive disclosure — https://www.nngroup.com/articles/progressive-disclosure/ ; avoid disabled buttons — https://design-system.service.gov.uk/components/button/
- Consistency #4 / recognition #6 — https://www.nngroup.com/articles/ten-usability-heuristics/ ; Jakob's Law — https://lawsofux.com/jakobs-law/
- Master-detail / split-view — https://developer.apple.com/design/human-interface-guidelines/split-views
