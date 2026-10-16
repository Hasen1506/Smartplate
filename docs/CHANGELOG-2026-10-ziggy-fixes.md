# Ziggy fixes, October 2026 (branch `ziggy/integration`)

Base `19fd3ef` (origin/main). Ten workstreams (W1–W10) merged in the order W1, W2, W4, W6, W7,
W8, W9, W3, W5, W10, then integrated. Inputs: `sp-grill` (72 critiques), the usability review
(62 findings), Vijay's answers and his UI sketch. Nothing is pushed or deployed.

Status words: **fixed** (built + tested), **partly** (the code part is done, something real is
left), **research** (answered with sources, nothing to build yet), **founder decision** (needs
Vijay), **not done**. One line each; detail is in the commit messages and the docs named.

## Integration work (phase 2)

- **One "Decide for me" engine.** v3 Today's button (`/api/user/<id>/today/decide`) is now a thin
  wrapper over `kernel/decide.py` (the engine behind `/decide/today` and `/api/plan/<id>/decide`):
  one set of modes, rules and objective. The engine gained the max-delivery-time rule and keeps a
  typed favourite with an unknown rating. See `docs/planner-objective.md` §"One engine".
- **One favourites list on Today.** W7's suggested favourites (read once, after consent) can be
  ticked into W5's Today pools (`POST /api/user/<id>/today/pool/suggested`, shown in
  "+ New item / restaurant", which the "Get set up → Add your favourite dishes" step opens); a
  dish still on the live menu goes in as a Swiggy dish through the shared safety check, anything
  else as a typed dish with no invented price or veg mark. W7 typed favourites show in the pools
  and can be removed there. The stores stay two tables (W5 `pool_typed`/Swiggy pools, W7
  `typed_favourites`) behind one read model.
- **v3 Today money and time.** "Remind me at …" replaces Place order / Add to cart while a meal's
  cart window hasn't opened (W2); each Swiggy pick shows its whole bill (delivery + platform fee +
  packaging + GST, labelled an estimate until a real bill is seen) and the budget card shows the
  total with fees (W4); on Sunday a recap card shows real Swiggy bills only, "kept" only when the
  order history was read (W4).
- Goldens re-blessed on the merged planner and reviewed (snack-only lunches gone, non-veg
  candidates for non-veg eaters, fees priced in).
- Fixes found by the full suite: `POST /api/plan` with a missing/`null` `user_id` was a 500
  (property fuzz; now the generic 400); an unmarked typed dish said "Marked non-veg" instead of
  "Not marked veg"; a dish outside a meal's own list no longer keeps a stability bonus, so adding a
  dish to the list changes today's meal too (W6's "list dishes win when they fit" held for every
  day but the imminent one); the v3 add sheet's background reads no longer go through `guard()`
  (it dropped one read and could swallow the first tap) and keep the typed search.
- Test fixtures moved with W4's fees (cheapest delivered dish ≈ ₹108): a few budgets raised where
  the test is about something else (browser pools ₹5,000, D4 learning ₹4,500, W7 ₹2,500, W6
  student ₹1,900); the W6 non-veg share line is at a third (6 of 14 at ₹3,500 with fees).
- W9's `swiggy_live` split re-applied after every merge; known-gap xfails removed for W6
  household, W7 out-of-area and W7 address change.

## Round 2, front end / docs / clean-up (branch `ziggy/r2-frontend`)

- **Usual meals, not pools.** Phone: each Today meal line has its own *Usual ▾* drawer (pick, add,
  quantity, remove); meals not on today are under *Dinner (not today)*. Desktop (≥ 1024 px): the
  three usual-meals panels stay side by side. Storage and API still say `pool`.
- **"Ziggy" everywhere users look.** Download filenames and the anonymous community author no
  longer say SmartPlate; `tests/test_ziggy_wording.py` guards static pages, manifest, served
  pages and front-end strings. Package, repo, URLs, header, env vars, DB names and the Swiggy
  OAuth client name are unchanged.
- **No fixed ₹60 floor in the front end.** Classic onboarding warns only when the server sends
  the person's floor (`S.view.meal_floor`).
- **Left after fees.** Today's budget card shows "₹X left after fees", marked *estimate* or
  *real bill* (server `budget.left_after_fees` / `left_estimated` when present, else budget −
  `picked_bill`).
- **Repo clean-up.** `docs/archive/design-audit/`, `docs/archive/project/` and the June brainstorm
  deleted (≈ 6.2 MB; in git history at `9fc21e5`); `.gitignore` covers logs, local DBs, shots and
  scratch dirs; `docs/branches-cleanup.md` lists the 31 remote branches to delete.

## Last three gaps (branch `ziggy/last-three-gaps`, 16 Oct 2026)

- **Usab #17, ₹300 pin warning, now on Today.** The server already raised it (round 2), but
  v3 Today only showed the low-budget note, so the warning never reached the screen people use.
  The budget card now shows the plan's "₹N over this week's target" heads-up (the one with the
  raise-budget action, only while `budget.over`), with *Change the weekly target* and *See the
  week*. Node test in `tests/frontend_v3.test.cjs`; the scenario test also checks the action.
- **Usab #18, "usual week".** Already honest since round 2 (needs order history); no strict
  or non-strict xfail is left in `tests/scenarios/test_known_gaps.py`.
- **Today's fees, one sum.** "₹X with delivery, fees & GST" (`budget.picked_bill`) was summed
  from the fee estimate even after Swiggy's cart gave the real bill, while "₹Y left after fees"
  used the real bill, so the two didn't add up to today's budget and the pick said "~₹140
  (estimate)" next to a *real bill* tag. Now both come from the same per-pick total (real cart
  bill once carted, else the estimate, the same `domain/fees` model the plan uses), a carted
  pick's bill line is Swiggy's (`estimated: false`, "Swiggy's cart bill"), and
  `bill_estimated` follows the basis. Test:
  `test_todays_bill_and_left_add_up_estimate_then_real_cart_bill`.
- Tests: Python 1,040 passed, 6 skipped, 0 xfailed, 0 failed (SQLite, 85 files, `-n 2` in
  chunks); node `tests/*.cjs` 132 passed; `menu-source.test.js` passed; Playwright, 9 files, 24 passed.

## Fix round 3: live UX audit (branch `ziggy/r3-integration`, 16 Oct 2026)

Issue numbers are those of the 16 Oct UX scenario audit (`work/ux-audit-2026-10-16.md`). Four
workstreams, merged in this order on top of `ziggy/last-three-gaps` (PR #44, included here).

**Connect + budget** (`ziggy/r3-connect-budget`)
- Fixed: **Connect Swiggy** (live audit) asks Swiggy's own allow-list before sign-in; a callback
  Swiggy hasn't whitelisted is a clear `503 swiggy_redirect_not_whitelisted`, not Swiggy's dead
  page; the Swiggy notice is an in-sheet tick (no native `confirm`, which webviews suppress) and
  connect errors show in the sheet.
- Fixed: **#15** onboarding asks Day/Week on the same weekly number plus "How many eat?" (1/2/3/4+,
  used as default portions); presets are relative to the person's day. **#23** one budget model:
  the weekly budget is the number, a day is a seventh of it, every figure names its period, a
  mid-week plan says "of ₹X for Fri–Sun", a weekly change resets today's figure. **#12** recovery
  code card on the first Today. **#36** theme renamed "Saffron". **#37** landing copy. **#38**
  weather tile only with a live forecast, with place and Open-Meteo credit, stacked under 480 px.

**Today truth** (`ziggy/r3-today-truth`, details in `docs/fix-round-3-today-truth.md`)
- Fixed: **#1** one source of truth for today (Today's picks are the Week cell, money, next-up and
  reminder). **#14** per-person meal times (late dinner up to 23:45; order-by, cart window,
  reminders follow). **#21** "Cart opens 18:15 · remind me". **#22** per-meal reminders with a
  calendar (.ics) fallback. **#24** new profiles' weeks hold only chosen meals ("Open" + Decide for
  me); landing reworded. **#25** "+ Add another". **#27** "Pick anyway?" with a one-tap relax.
  **#28** Decide says why a meal stayed empty. **#32** "Meals I order". **#10** rule defaults
  learned from the person's places. **#11** Jain and eggetarian diets. Swap without Swiggy says
  it isn't connected.
- Known limits: an overlaid Week cell is a read model (the classic Week cart button still carts the
  plan's own dish); per-meal reminders are for today's meals.

**History → favourites** (`ziggy/r3-history-favs`)
- Fixed: **#4** past dishes matched by name on the live menu before falling back to typed. **#5**
  readable suggestion checklist at 390 px. **#6** snack bucket (15:30–18:30), name/section hints,
  one dish in two meals; snack is an optional favourites list, never planned. **#7** whole past
  orders as usual orders with quantities; sides not offered alone. **#8** history chips open the
  menu in one tap; "Save these N places". **#2** an address change keeps favourites, greyed with
  the reason, and says what changed. **#29** one-line consent + Details. **#30** refresh from my
  orders. **#31** ratings filled from menus/search. **#33** sample profiles hidden once you have
  your own.

**Instant menus + offers** (`ziggy/r3-menus-offers`, report `work/menus-offers-report.md`)
- Fixed: **#3** menus kept 6 h per address, served at once, revalidated in the background,
  catalogue first look, page 1 first, Add sheet prefetches the person's own places (≤6). **#16**
  no unseen Swiggy calls on boot; catalogue refresh is a background job. **#17** sections, in-menu
  search, thumbnails, veg-only toggle. **#18** an open place takes the sheet. **#19** dishes with
  options can be usual meals (options chosen in Swiggy). **#9** offers parsed and shown (never
  claimed as applied), list price struck through, Bestseller, one "Best value"; cart coupons read
  only at the cart, never applied. **#34** ETA in minutes. **#35** compare row hidden until a
  second source exists. **#26** Swap "Nearby now". **#13** same-dish refill returns the existing
  cart; a busy cart is `409 swiggy_cart_busy`, not 502.
- Partly: **#20** the review shows Swiggy's exact price when the same dish/price is on the menu just
  read; menu-list prices stay "≈".

**Integration decisions**
- Landing/title/manifest use one wording: "Your meals, planned within your budget." with "Works
  with your Swiggy account" and a "Swiggy sign-in is coming soon" note only while sign-in is
  closed (connect-budget's #37 copy; it also drops "already decided", which was today-truth's #24
  ask). Today-truth's alternative hero ("Your meals, your picks…") was not kept.
- A snack picked on Today now counts in the Week's money too (`today.snack_spend`, `budget.snack_today`),
  so Today and Week agree; it is never a Week cell, has no order-by/reminder, and per-person meal
  times don't apply to it. Swap's "Nearby now" budget share also counts a picked snack.
- Importing past orders uses the 6 h kept menu (no second Swiggy read when it is whole) and lets a
  dish with options in as a Swiggy dish (#19), instead of typing it in.
- Swap shows today-truth's empty/not-connected message and menus-offers' "Nearby now" together.
- The cart coupons read never errors the cart screen (no coupon tool → empty list), and what is
  typed in the Swiggy search or the "Type it in" form survives a background re-render (the
  background menu job and the suggestions load redraw the open sheet).
- Tests: `tests/test_r3_integration.py` (snack in Week money and meal times; kept-menu import).
  The persona scenario expects other-address favourites kept and greyed (#2). Browser tests follow the new sheet title "Add to your usual …" and the hidden compare row. CI
  now runs every r3 node and browser test (and `frontend_w03`).
- Tests: Python 1,102 passed, 6 skipped, 0 failed (SQLite, 86 files + `tests/scenarios`, `-n 2` in
  chunks); node `tests/*.cjs` 165 passed; `menu-source.test.js` 10 passed; Playwright, 12 files, 29 passed.

## sp-grill critiques (1–72)

| # | Topic | Status | Note |
|---|---|---|---|
| 1 | The user is still the founder | founder decision | W10 answers: name 10 real people in one segment; pilot plan in `docs/founder-answers-2026-10.md` |
| 2 | Is budget really the pain | research | W10: sourced answer, test in the pilot |
| 3 | Wedge says "tonight's dinner" | fixed | W5 Today horizon (today's budget, today's meals) is the first screen |
| 4 | Segment not chosen | founder decision | W10 decision #1 |
| 5 | Chennai/Minjur only | partly | W6 plans from every safe dish at your places; W7 re-checks the week on address change; geography is a pilot question |
| 6 | Surge "saved" is demo money | fixed | W4: surge model archived behind `SMARTPLATE_SURGE_MODEL` (off), no surge copy |
| 7 | Moat table vs strawmen | partly | W7 day-0 favourites from order history; positioning copy is the founder's |
| 8 | "Decisions removed" contradicts IA | fixed | W5/W6: never auto-decides; the solver runs only on "Decide for me" |
| 9 | Nutrition dilutes the pitch | fixed | W5: nutrition is an opt-in toggle in Week, off by default |
| 10 | WTP ₹149–249 | founder decision | W10 decision #7 (free through the pilot) |
| 11 | Food subscriptions WTP | research | W10 |
| 12 | Freemium base rate | research | W10 |
| 13 | Swiggy attacks "food within budget" | research | W10 (Toing / 99 Store notes) |
| 14 | Platform owns the data | partly | W7: order history read once with consent, minimal fields, deletable (Swiggy data rules) |
| 15 | Zomato MCP | research | W7/W10 `docs/research/platform-2026-10.md`; adapter seam only |
| 16 | HealthifyMe photo logging | research | W10; nothing built |
| 17 | No switching cost before week 4 | partly | W7 suggested favourites from history on day 0; v3 ticks them into pools |
| 18 | No acquisition channel | founder decision | W10 |
| 19 | MCP / chat channel dismissed | research | W10 + W9 (WhatsApp reminders first) |
| 20 | "Ziggy" vs "Swiggy" | founder decision | W10 decision #3 (rename before a public link) |
| 21 | Can't measure retention | fixed | W9 events table, five events, D1/D7/D30 cohorts; consent for Swiggy-derived events is a founder decision |
| 22 | Push only while awake | fixed | W9 `/cron/tick` (idempotent, late/missed recorded); needs the cron job set up |
| 23 | Confirmation fatigue | not done | Past meals still need a tap; W2 made the plan summary honest only |
| 24 | No weekly recap | fixed | W4 recap API; integration: Sunday card on v3 Today (real bills only) |
| 25 | Forced Swiggy OAuth before value | fixed | W5 two-question onboarding; W7 works without Swiggy (typed address + favourites) |
| 26 | "Four quick steps" is 7 inputs | fixed | W5: diet (+ optional allergies), then today's budget + meals |
| 27 | Suggested budget from sample data | fixed | W4 budget from real spend with floors and a 25 % max cut |
| 28 | Three navigation models | fixed | W5 `docs/IA.md` single source; old sections marked superseded |
| 29 | Drag-and-drop pools | fixed | W5: v3 pools are lists with "+ New item"; drag board only in classic |
| 30 | Too many entry points | fixed | W5: one Swap sheet on v3; old ones behind `?ui=classic` |
| 31 | UX review used a fake Swiggy | partly | W10 persona simulations on the stand-in town; real-user sessions still needed |
| 32 | Copy named after brainstorm sections | partly | W8 dropped the feature catalogue from `/api/meta`; W3 plain words in classic |
| 33 | "Hard weekly cap" overstates | fixed | W4: weekly *target* vs real spend |
| 34 | `/api/meta` says simulated | fixed | W7 one mode enum (demo / live_read / live_cart / live_order) |
| 35 | Recipe licensing contradictions | partly | W8 credits page + data README; permission email is a founder decision |
| 36 | "Never fake" next to sample data | partly | Sample catalogue is test-fixture only; v3 never invents price/rating/time; seeded demo copy remains in classic |
| 37 | Diabetes rule fails open | fixed | W1 one fail-closed checker (unsafe / unknown / not checked) |
| 38 | Veg check fails open | fixed | W1: unmarked = not veg, everywhere (cart, pools, menus, Today) |
| 39 | Allergen name-substring | partly | W1 shared lexicon (Tamil/Hindi/Malayalam/Telugu) and fail-closed; still name-based, no ingredient data |
| 40 | FSSAI disclosure | research | W1 notes (FSSAI reg 9, Swiggy MCP fields) |
| 41 | Health claims | founder decision | W1 `SMARTPLATE_MEDICAL_FILTERS` flag to archive medical filters for the beta |
| 42 | 25/40/35 split, 2000 kcal default | not done | |
| 43 | No DPDP notice | fixed | W8 privacy notice + terms, per-purpose consent, withdraw = erase |
| 44 | Residency / processors | partly | W8 doc; accepting Render/Neon DPAs is a founder action |
| 45 | Logging Swiggy reply shapes | fixed | W8 redaction; no raw dumps |
| 46 | One platform, one approver | partly | W7 provider adapter seam (Swiggy wired, others stubs that say why) |
| 47 | ₹1,000 cart cap | fixed | W4 split into even carts or hand-off, extra fees stated |
| 48 | COD only vs UPI | research | W7: UPI documented as a future switch |
| 49 | Redirect URI | partly | W7: Render origin whitelisted; final domain waits on the rename |
| 50 | Fees, GST, packaging missing | fixed | W4 fee model learned from real bills; integration: per-pick bill on v3 Today |
| 51 | Paise/rupee heuristic | fixed | W4 explicit units + 3× reprice guard |
| 52 | Surge in Tight Week | fixed | W4 (off) |
| 53 | MILP overkill | fixed | W6: exact branch-and-bound for today, staged MILP + greedy floor for a week, benchmarked |
| 54 | Unexplainable weights | fixed | W6 explicit lexicographic objective, `docs/planner-objective.md` |
| 55 | Latency on 0.1 CPU | fixed | W6: veg ₹1,500 week 2.2 s → 0.16 s |
| 56 | Feature sprawl | partly | W5 old UI behind a flag; W9 archive inventory (nothing deleted) |
| 57 | `swiggy_live.py` god-module | partly | W9 split out `swiggy_schema` + `swiggy_money` (1,864 → 1,643 lines); more to split |
| 58 | 58 % AI-authored commits | not done | Process point; W9 archive review only |
| 59 | ~43 env vars | partly | W9 inventory; archiving the off-since-June flags is a founder decision |
| 60 | Duplicated lexicons | fixed | W1 one shared food lexicon |
| 61 | Tests against fakes | partly | W10 scenarios + Hypothesis properties + hostile menus; still fakes, no real-Swiggy contract test |
| 62 | Identity in localStorage | fixed | W8 HttpOnly signed rotating cookie + CSRF |
| 63 | In-memory rate limits | fixed | W8 database-backed token buckets |
| 64 | Token key next to data | fixed | W8 env-only key in production, rotation, weak keys refused |
| 65 | `str(error)` to the user | fixed | W8 own messages only, generic text + request id |
| 66 | Free Render + Neon | partly | W9 stale-connection replay, warm boot, keep-alive; paid instance is a founder decision |
| 67 | No observability | fixed | W9 request ids, JSON logs, latency lines, optional Sentry |
| 68 | English only | partly | W3 Tamil string table + switch (Today/Week/onboarding); native review needed |
| 69 | Accessibility | partly | W3 focus follows sheets, labelled icon buttons; W5 radio a11y; no full audit |
| 70 | Offline | fixed | W3 service worker keeps the last plan and fingerprinted assets |
| 71 | Founder focus / docs ratio | founder decision | W9 moved stale docs to `docs/archive` |
| 72 | Pivot alternatives | research | W10 ranked answers; founder decision |

## Usability findings (1–62)

| # | Finding | Status | Note |
|---|---|---|---|
| 1 | Unmarked Tamil non-veg passes | fixed | W1 lexicon + fail-closed |
| 2 | Allergens in Indian-language names | fixed | W1 |
| 3 | Diabetes passes unknown sugar | fixed | W1 (unknown = not checked) |
| 4 | Wrong recipe template (Sakkarai Pongal) | fixed | W1: library inference only adds restrictions |
| 5 | Two safety engines disagree | fixed | W1 one checker for every path |
| 6 | "Why hidden?" never answered | fixed | W1 per-dish reasons; v3 menu lists hidden dishes with reasons |
| 7 | "Tomorrow" counts down to today | fixed | W2 order-by on the meal's own date |
| 8 | Sunday is a dead day | fixed | W2 |
| 9 | Tomorrow's cart filled today | fixed | W2 cart window (409 + Remind me); integration: v3 shows "Remind me at …" |
| 10 | Skips a meal instead of going cheaper | fixed | W6 fewest skips first, pool fallback; default policy is founder decision #6 |
| 11 | Non-veg user gets a veg week | partly | W6 non-veg candidates: 6 of 14 at ₹3,500 with fees (was 2); no share constraint (too slow) |
| 12 | Small items planned as meals | fixed | W6 snacks never alone at lunch/dinner |
| 13 | "Order" chosen, "Cook" planned | fixed | W2 |
| 14 | "Better fits" isn't better | not done | not re-checked |
| 15 | Alternatives over the money left not flagged | partly | W2 sheet limits counted like Today |
| 16 | Household conflicts resolve silently | partly | W6 names the member a dish isn't safe for, proposes splits; splits can't be applied to the grid yet |
| 17 | ₹300 profile accepts a ₹355 pin silently | fixed | Round 2 (`8703ba8`): a pin that takes the week over its target raises a "₹N over this week's target" heads-up naming the person's own picks, with a raise-the-target action; last-three-gaps: v3 Today's budget card now shows it (it was only on classic Today), with *Change the weekly target* / *See the week*. Strict xfail removed; `test_a_pin_that_breaks_the_week_is_flagged` passes |
| 18 | "Your usual week costs ₹5,915" with no history | fixed | Round 2 (`8703ba8`): "Your usual week" only once order history exists, else "Planning every one of these meals from places near you costs about ₹X a week". Strict xfail removed; `test_no_usual_week_claim_without_order_history` passes |
| 19 | Plan cost leaves out fees | fixed | W4 |
| 20 | Two budget numbers | fixed | W2 sheet money matches Today |
| 21 | "≈₹125est." label | fixed | W4 labelled estimates with ranges |
| 22 | Per-meal budget line wrong | fixed | W3 live per-meal line |
| 23 | No sanity check on ₹300 | fixed | W4 low-budget heads-up. Round 2: no fixed ₹60-a-meal floor; the front end warns only from the server's per-user floor (cheapest favourite with fees), the onboarding ₹60 warning is gone |
| 24 | "Delivery ₹35 est." everywhere | fixed | W4 learned per restaurant, ₹17.11 default |
| 25 | Account asked too early | fixed | W5 account offered after the first pick |
| 26 | Non-veg preselected | fixed | W3 + W5 no diet default |
| 27 | Breakfast preset to Skip | fixed | W5: no meal preselected |
| 28 | Medical needs folded away | partly | v3 onboarding asks allergies; medical is in Settings pending founder decision on #41 |
| 29 | Input count | fixed | W5 |
| 30 | Developer wording on Connect Swiggy | fixed | W3 |
| 31 | Connect Swiggy straight after onboarding | fixed | W5 "Get set up" card on Today |
| 32 | Recovery code in plain text | fixed | W3 copy/download + W8 shown once |
| 33 | "Forget this profile" | fixed | W3 "Sign out" |
| 34 | Back leaves the app | fixed | W3 history |
| 35 | Too many ways to change a meal | fixed | W5 one Swap |
| 36 | Drag-and-drop editor | fixed | W5 |
| 37 | Jargon ("pool" …) | fixed | Founder (Round 2): "usual meals" everywhere users look; v3 Today says usual breakfasts / lunches / dinners |
| 38 | Closed restaurants look open | fixed | W3 "Closed now / Opens at", 409 restaurant_closed |
| 39 | Saved: two empty states | fixed | W3 |
| 40 | Tab names vs docs | fixed | W5 `docs/IA.md` |
| 41 | Toast covers the next button | fixed | W3 toasts at the top on phones |
| 42 | Nutrition card on desktop | partly | W5 opt-in; numbers unchanged |
| 43 | "Good morning" at 11:52 | not done | |
| 44 | Vegan "Chettinad" in the dataset | partly | W1 checker screens it on every path; dataset labels not corrected |
| 45 | Cold start 26–35 s | partly | W3 "waking up" + retry, W9 keep-alive; paid instance is a founder decision |
| 46 | Cold start looks offline | fixed | W3 |
| 47 | Offline reload loses the plan | fixed | W3 |
| 48 | Push in a background thread | fixed | W9 cron endpoint |
| 49 | Warm speed | n/a | already good |
| 50 | No privacy policy | fixed | W8 |
| 51 | `/api/meta` feature catalogue | fixed | W8 |
| 52 | Landing has no proof | not done | |
| 53 | Icon buttons without names | fixed | W3 |
| 54 | No overflow at 390/1280 | n/a | passes; W5 browser E2E at 375/1280 |
| 55 | "Money kept" is the paid feature | partly | W4 recap + v3 Sunday card; needs order history read to claim "kept" |
| 56 | Useless without Swiggy | fixed | W7 typed address/favourites; v3 typed pools, decide, share, cook |
| 57 | Elephant only on errors | not done | |
| 58 | Nothing rewards coming back | not done | recap only |
| 59 | Unique feature | research | W10 |
| 60 | Medical filters are the riskiest | founder decision | W1 flag (#41) |
| 61 | Generic food icons | not done | |
| 62 | "You pay in Swiggy" copy | not done | |

## Test totals (integration branch, phase 2)

| Suite | Result (14 Oct 2026, local) |
|---|---|
| Python, SQLite, all 80 files | 978 passed, 6 skipped, 2 xfailed (known W4 gaps: usab #17 ₹300 pin warning, #18 "usual week" claim), 0 failed. Both xfails removed since (round 2); see "Last three gaps" for current totals |
| node --check app.js, v3.js | OK |
| node --test tests/*.cjs | 127 passed |
| src/sourcing/menu-source.test.js | 10 passed |
| Browser (Playwright, 9 files) | 24 passed |
| Postgres | not verified locally (sandbox lost the local cluster mid-run); rely on the CI test-postgres job on this PR |
