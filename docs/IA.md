# Ziggy navigation (IA) — the single source of truth

Status: current from 14 Oct 2026 (v3, branch `ziggy/w05-ui`). Any other description of
Ziggy's tabs or screens (README "What using it feels like", docs/IDEAS.md "Dark redesign",
docs/ux-review-2026-10.md) is **superseded** by this page where they disagree. Change the
navigation here first, then in code.

Source: the founder's sketch (13 Oct 2026) and his answers to the product grill
(#3, 8, 25, 26, 28, 29, 30, 56, 72). Order of truth for what Ziggy shows:
**your favourites → your rules → the solver, only when you ask it to decide.**

## The four tabs

Bottom bar on phones, a side rail from 900 px. Your avatar (top right) is **Me**.

| Tab | What it is for | What's on it |
|---|---|---|
| **Today** | Today only, from your own favourites | Order \| Cook toggle · Today's budget card (₹600 (B+L), − / +, presets ₹300 / ₹600 / ₹1,000, Other…) · Weather today · your rules as chips (diet, allergies, min rating, max delivery time) with *Edit rules* · one line per meal you have today (`Breakfast: [1× Cappuccino ▾] − 2× +  · ₹ · Swap`) · *Decide for me* · Place order / Send details / Cook it · price-compare row (only when a Swiggy price exists) · the Swiggy cart and checkout · *Keep this on every device?* (only after a first pick) · your usual meals (phone: a *Usual ▾* drawer on each meal line; desktop: three panels) |
| **Week** | The week, as a plan and a ledger | Week budget, days, meals (tap one of today's meals → Swap), Order list, Cooking & groceries, Order several meals, Re-plan, Plan next week. **Nutrition is opt-in** (*Show nutrition*, off by default); the desktop aside shows it only when on |
| **Cook** | Cooking, separate from ordering | Type any dish → ingredients scaled to how many eat, Instamart link per ingredient, copy list, cook along; link to this week's grocery list |
| **Saved** | Places and dishes you liked | Saved places, Good dishes, Recently had, Swiggy order history |
| Me (avatar) | Settings | Food & budget, Swiggy connection + rules, reminders, account, appearance, household, data |

Desktop (≥ 1024 px) Today: meals and actions on the left, budget / weather / rules on the
right, the three usual-meals panels (**Usual breakfasts / lunches / dinners**) **side by side**
below. Phones stack everything in the sketch's order, and there is **no separate usual-meals
block**: each meal line (Breakfast / Lunch / Dinner) has a small *Usual ▾* button that opens that
meal's usual meals right under the line (pick, + New item / restaurant, quantity, remove). A meal
that isn't on today (say dinner on a B+L day) gets its own *Usual ▾* row under the meals.

The budget card shows what's left **after fees** ("₹X left after fees"), tagged *estimate* until
Swiggy's real bill is seen, then *real bill* (founder, Oct 2026).

## Usual meals (favourites)

The founder's word (Oct 2026) is **usual meals**, not "pools". Code, storage and the API keep
the `pool` name (`/today/pool`, `domain/meal_pools.py`); nothing a person reads says pool.

- One list per meal: usual breakfasts, lunches, dinners. **No drag-and-drop.** Each has
  `+ New item / restaurant`, a quantity stepper per dish and a remove button.
- Two kinds of dish:
  - **From Swiggy**: search Swiggy → a restaurant (rating, time) → its live menu → tap a dish.
    Stored in the same pool the week planner reads (`domain/meal_pools.py`), plus the rating
    and delivery time Swiggy showed (`today.pool_meta`). Connected profiles also see their
    past Swiggy restaurants as one-tap searches.
  - **Typed by you**: name, place, price you pay, veg yes / no / not sure, order or cook.
    For people not connected to Swiggy. Shareable and cookable; never put in a Swiggy cart.
- **Greyed out, with the reason**, never hidden: breaks diet/allergy (fail-closed: not marked
  veg = not veg; can't be picked), rated below your minimum, delivery over your maximum,
  not on the menu for your address now.

## Deciding

Nothing is decided on its own. *Decide for me* opens four modes and runs only on tap:

| Mode | Candidates |
|---|---|
| From my favourites only | your pool dishes that pass every rule |
| Favourites + cook at home | + home recipes with a grocery estimate (`domain/reverse_mode.py`) |
| Favourites + new places ≥ my rating | + live dishes near you from places at or above your minimum rating |
| Surprise me | all of the above, seeded shuffle (same day → same answer) |

Objective: fill as many open meals as fit today's budget; prefer favourites, then higher
rating, then something not had in the last two days, then the cheaper total. Meals you
already picked are never changed. When a meal can't be filled, Ziggy says why.

## One way to change a meal: Swap

`Swap` (on each meal line, and on today's meals in Week) opens one sheet: your pool for that
meal ranked (rating, then price), greyed-out dishes with reasons, *Clear*, and **More ways**,
which keeps every older entry point reachable:

| Old entry point | Now |
|---|---|
| Hungry now? | Swap → *Add {meal} to the week plan now* (calls `eatNow`) |
| Or have instead (quick strip) | Swap → your pool, ranked |
| Change sheet (cook / keep / skip) | Swap → *Change in the week plan* (opens the same sheet) |
| Any place near you | Swap → *New item / restaurant* → Search Swiggy |
| Order several meals | Swap → *Order several meals* (order list) |

## First run

Two questions before value, nothing preselected:
1. **What do you eat?** Vegetarian / Non-vegetarian / Vegan (Continue stays disabled until one
   is chosen) + optional allergies.
2. **Today's budget** (₹, presets) + **which meals today** (none preselected).

Then Today, with a *Get set up* card: 1) delivery address (Connect Swiggy — or, while Swiggy
sign-in is not open, "type in the dishes you usually get"), 2) add favourites. The account
(sign-in name + password) is offered **after the first pick**, never before. The weekly
budget the profile needs is set to today's budget × 7 and can be changed in Me.

## Flags and what is archived (nothing deleted)

- `?ui=classic` (stored as `localStorage["ziggy.ui"]="classic"`) brings back the previous
  Today (next-meal hero, Hungry now, quick picks, pools nudge), three tabs and the 4-step
  onboarding. `?ui=v3` switches back. The classic browser suites run with this flag.
- The drag-and-drop pool board stays in Saved → My meals under the classic flag only.
- Code: `smartplate/static/v3/` (UI), `smartplate/today.py` + `today_api.py` (API). app.js
  only calls `globalThis.ZiggyV3` hooks (`tabs`, `body`, `screen`, `overlays`, `wire`,
  `onMealTap`, `startOnboard`, `weekTop`, `nutritionOn`) and works without them.

## API (thin, `smartplate/today_api.py`)

| Route | Does |
|---|---|
| `GET /api/user/<id>/today` | the Today read model (budget, weather, rules, pools with reasons, picks, modes, price compare, share text) |
| `POST …/today/budget {amount}` | today's budget (₹50–20,000), today only |
| `POST …/today/rules {min_rating, max_eta, nutrition}` | soft rules + nutrition opt-in (0 = no limit) |
| `POST …/today/pick {meal, ref, qty}` | pick a pool dish for a meal (`ref: null` clears) |
| `POST …/today/decide {mode}` | Decide for me |
| `POST …/today/pool {source: swiggy\|typed, …}` | add to a pool |
| `POST …/today/pool/remove {meal, ref}` · `…/pool/qty {meal, ref, qty}` | remove · quantity 1–8 |

## Open decisions for the founder

See the W5 report; the short list: onboarding order (diet first vs address first), whether
typed dishes may feed the week planner, Week still drafts a full plan on profile creation
(planner work, W6), weekly budget = today's × 7, and the Swiggy-cart one-restaurant rule
for multi-meal "Place order".
