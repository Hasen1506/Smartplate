# What the planner optimises (and why)

*Answers grill #53 ("what is the algorithm's goal?") and #54 ("weights nobody can
explain"). Code: `smartplate/kernel/objective.py` (the rules and weights),
`smartplate/kernel/decide.py` ("Decide for me"), `smartplate/kernel/optimizer.py` (the
week plan).*

## The goal in one line

**Plan the meals you said you order, from what you like, inside your money, without ever
breaking a safety rule.** Money is the limit. Your choice is the objective.

**Favourites first (founder decision, 14 Oct 2026).** Your favourites take priority whenever
the money allows. Ziggy never *silently* swaps a favourite for a cheaper dish and never
silently skips one to save money: when a meal's own favourites don't fit, the meal stays
open and you choose how to build out the week (below). A cheaper dish is planned only when
you pick that scenario, or when you chose a *Decide for me* mode (which is you asking Ziggy
to choose). There is no automatic "cut 15%" target: the 10 / 15 / 20% options, in rupees,
appear only when you ask to spend less.

## The order of priority (lexicographic, not a weighted soup)

A lower rule never buys a higher one.

| # | Rule | Kind | What it means |
|---|---|---|---|
| 1 | **Safe** | filter | Never break a diet, allergy or medical rule of *anyone eating* that meal (household members too). Unknown counts as unsafe. Applied before anything else. |
| 2 | **Within the money** | hard limit | Today's money (Today) or this week's money left (Week), delivery fee included; the daily limit too, if you set one. |
| 3 | **Favourites** | what may be planned | Only what the mode you picked allows (below). Inside that, the dish with the higher *favourite score* wins. |
| 4 | **No silent skips, no silent swaps** | ranked above the score | Fill every meal you said you order with what rule 3 allows. If a meal's favourites don't fit the money, it is left **open with a build-out set** (never quietly skipped, never quietly swapped). A cheaper dish from your places is used only when you pick it there or chose a Decide-for-me mode, or when none of the meal's favourites is safe for someone eating (and the reason says so). Every open or skipped meal says which rule it was. |
| 5 | **Cheapest among equals** | tie-break | Two dishes you like about the same (favourite scores within 0.05): the cheaper one. |
| – | **Variety** | cap | A dish at most twice a week (your setting) and once a day. A short meal list of your own may repeat as often as it has to. |

Rule 3 decides *what is allowed*; rule 4 sits above the favourite *score*. In *Decide for
me* (the person asked Ziggy to choose) a cheaper dish at your places beats a skip; in the
week plan the person chooses it from the build-out set. So the key that compares two plans is:

```
(meals filled ↑,  favourite score ↑,  cost ↓,  repeats ↓)
```

## When favourites don't fit: the build-out set

`smartplate/domain/buildout.py` · `GET /api/plan/<id>/buildout` · `POST /api/plan/<id>/buildout`
(`plan_view.buildout` and a heads-up on the plan). The planner leaves a meal open with a
reason that starts "Your favourites for this meal don't fit …" and offers scenarios, each with
its effect in ₹; nothing changes until the person picks one, and every scenario is re-derived
when applied (a stale id is refused):

| Scenario | What it does |
|---|---|
| Skip another meal | Frees that meal's money so the favourite fits |
| Swap to a cheaper favourite | Another dish from the same meal list |
| Cook it | The open meal at home |
| Cheaper dish at your places | The old automatic fallback, now only when picked |
| Raise by ₹N | The exact weekly increase that makes every favourite fit |

The only "too little money" warning uses **your own cheapest favourite with its fees**; with no
favourites there is no floor and no warning (the old flat ₹60 a meal is gone). Money counts
whole bills: on Today, a Swiggy pick counts at its estimated whole bill (dish + delivery +
platform fee + packaging + GST, learned from your bills) and at the real Swiggy cart bill
once its cart is filled; Today's *Decide for me* weighs Swiggy dishes the same way. Meals over
Swiggy's ₹1,000 cart limit go as sequential split carts, each approved.

## Modes ("Decide for me" only)

Nothing is decided when the app opens. The person taps **Decide for me** and picks one:

| Mode | May plan |
|---|---|
| Favourites only (default) | Dishes in your breakfast / lunch / dinner lists, and every dish at your places |
| Favourites + cook | … plus a home-cook, within your cook days |
| Favourites + new places | … plus places you haven't saved, rated at least your min rating |
| Surprise me | Anything inside your rules, chosen for novelty (dishes you've never ordered, a stable per-day shuffle) |

All modes stay inside your rules: **min rating** (★, restaurant), **max price a meal**
(per portion, before delivery; `prefs.max_price_per_meal`), **quantity** (portions;
household meals use one per person eating), **diet and allergies**.

Horizons: **Today** plans the rest of today's meals inside today's money (your daily limit,
else the week's money left shared evenly over the days left, today included; the budget
card can override it). **Week** plans every open meal of the week.
`POST /api/user/<id>/decide/today {mode, money?}` · `POST /api/plan/<id>/decide {mode, apply?}`.

## Favourite score: the only weights

Each term is 0..1 before its weight. These are the only numbers that rank dishes.

| Weight | Value | Plain words |
|---|---|---|
| `W_RATING` | 0.35 | The dish's rating out of 5 (the restaurant's if the dish has none) |
| `W_TIMES` | 0.30 | How often you ordered it: n/(n+2), so once ≈ 0.33, four times ≈ 0.67 |
| `W_SLOT` | 0.20 | Right for this meal (a tiffin dish at breakfast, a full plate at lunch). Tiffin (dosa, idli, pongal) is fine at dinner; only snack-alone (vada, bonda, chai…) is never a lunch or dinner |
| `W_RECENCY` | 0.15 | Ordered lately: 1 today, fading to 0 over 30 days |
| `POOL_BONUS` | 0.50 | You put this dish in this meal's list yourself |
| `PLACE_BONUS` | 0.20 | A dish at one of your places |
| `LIKED_BONUS` | 0.20 | You gave it a thumbs-up |
| `DIET_BONUS` | 0.15 | A non-veg eater gets a non-veg dish (usab #11; Decide for me) |
| `SCORE_STEP` | 0.05 | Scores this close count as equal, so the cheaper dish wins |

## Every pick says why, in one sentence

- "Your dinner favourite (you've ordered it 4×, 4.4★); ₹155 fits this week's money."
- "From Annapoorna, one of your places (4.4★) — cheaper than your usual, so this meal isn't skipped; ₹90 fits today's money."
- "From Punjab Da Dhaba, one of your places — your usual lunch isn't safe for Dev; ₹675 fits this week's money."
- "Skipped: ₹59 of today's money is left; the cheapest allowed lunch is ₹90."
- "Nothing at your places is safe for Dev (vegan) for lunch."

A household meal with no single dish safe for everyone gets a **split** from one place
(one cart): "Split so everyone eats safely: 3× Veg Thali for Lakshmi, Suresh, Anu; 1×
Dal Khichdi for Dev." (Decide-for-me proposals only, see *Open* below.)

## How it is solved

| Problem | Method | Why |
|---|---|---|
| Today (≤ 4 meals) | Exact branch and bound over the key above | Milliseconds; provably the best plan by the rules |
| Week, "Decide for me" | Staged MILP (PuLP/CBC): most meals, then highest favourite score, then cheapest; candidates per meal bounded to 24 undominated ones; a greedy fill-then-upgrade pass is its floor | The greedy alone filled exactly as many meals but was 0–13% (mean ≈5%) below the exact MILP on favourite score over 20 random weeks, so by grill #53's own test (≥ 2% gap) the MILP stays; it takes ≈0.06–0.4 s |
| Week plan (the existing grid, re-plans, pins, carts) | PuLP/CBC MILP, now with rule 4 in **every** mode | Keeps pins, carts, festivals, fasts, leftovers, calendar and nutrition that the grid relies on |

The week MILP still has soft terms (cost, taste, nutrition, carbon, surge, stability).
Those act only *after* rule 4 is fixed (fewest skips first), and only between dishes
the rules allow. Its new pieces map onto the rules above:

- **Rule 4 in every mode**: in Comfort and Balanced a skip the money could avoid now costs
  `LEX_SKIP_PEN` = 20 (was 5 and 3). One meal's cost/taste/nutrition terms swing by a few
  units, so no realistic trade elsewhere in the week pays for a skip. Tight Week keeps its
  exact staged solve (fewest skips, then fewest extra cooks, then the week). A staged
  "fewest skips first" solve in every mode was tried and is behind `SKIP_STAGE`: it made the
  follow-up solve 10–50× slower to prove.
- **Pool fallback**: a meal with a list of your own dishes can fall back to other safe
  dishes *at your places* (`POOL_FALLBACK_PEN` = 1.0, so a list dish always wins when it
  fits), but only when the list isn't safe for someone eating or a Decide-for-me mode
  allows it. Too dear is not a reason to swap silently (see the build-out set).
- **Whole places** (grill #5): with saved places, every safe dish there is a candidate (up
  to `MAX_FAVOURITE_CANDIDATES` = 40 a meal, round-robin across places), not the old short
  list of about 6; the live refresh reads every saved place (up to 12), not the first 4.
- **Diet mix** (usab #11): the non-veg week was veg because each meal was offered only its
  cheapest few dishes, all veg. A non-veg eater's lunches and dinners now always include the
  best non-veg dishes as candidates; taste and money then choose (`prefs.nonveg_share` = 0
  turns it off). A week-wide share constraint and a per-dish "non-veg" weight were both
  measured: each made CBC hit its 10 s limit on a 3-meal week, so neither ships. In
  *Decide for me* the favourite score carries `DIET_BONUS` (no solver cost there).
- **Not a whole meal** (usab #12): vada, bonda, samosa, chai, juice and similar are never
  planned alone as lunch or dinner (unless you chose them).
- **Tiffin at dinner** (founder, 14 Oct 2026): dosa, idli and pongal are allowed at dinner.
  The week MILP offers each dinner its best `DINNER_TIFFIN_CANDIDATES` = 2 tiffin dishes;
  offering all of them made the 6-place week hit the 10 s limit unproven. How the solver
  stays fast on x86 with them: "Solver formulation" below.
- **Favourites never cheapened silently**: a pooled meal whose list is too dear for the money
  left is not swapped to a cheaper dish; it is left open for the build-out set. The pool
  fallback (below) runs only when no list dish is safe for someone eating, or when a
  Decide-for-me mode the person chose allows cheaper dishes (`allow_cheaper`).
- **Honest skips**: every skip in every mode says the real cause (money left vs cheapest
  option, daily limit, variety cap, nothing safe for a named household member); the plan
  summary counts skips *by cause* instead of "skipped to stay within budget".

### Solver formulation (x86 fix, 14 Oct 2026)

Tiffin at dinner put the same cheap dosa on every breakfast, lunch and dinner of the week.
The plan didn't change, but on GitHub's x86 CBC build the ₹3,500 six-place week went from
1,001 to 8,592 branch-and-bound nodes (≈7–8 s, over the 6 s test bound; the ARM build
needed 212). The incumbent was found at the root; all the time went into proving a 1.3%
gap, because one dish placed on any of seven days is worth almost the same, so branching
on one meal's choice only moved the dish to another day. Three changes, none of which
changes which whole plan is best:

- **Dish counts are integer variables.** For every dish offered at more than one meal,
  `n = Σ x` (bounded by the repeat cap) is its own whole-number variable, so the solver
  branches on "this dish 0 / 1 / 2 times this week", which settles every day at once.
- **The protein-evenness term is exact per option.** A meal's shortfall is
  `Σ_i x_i · max(0, target − protein_i)` (one option is chosen, so this equals the old
  `slack ≥ target − Σ protein_i x_i` for every whole plan), and the relaxation can no longer
  average half a dosa and half a chicken plate into "no shortfall".
- **CBC preprocessing is off** for the week solve (`preprocess off`): it substitutes the
  dish counts back out as sums of binaries, undoing the first change.

Measured with pulp's CBC 2.10.3 binaries (x86 under qemu, as on GitHub's runner), same
profile and production settings: x86 8,592 → 0 nodes (root bound 4.156 → 4.2127 = the
optimum), ARM 212 → 0; the plan, its objective and every golden are unchanged. The
dominated-candidate and "one tiffin per place" reductions were not needed (on this
profile one tiffin per place is already the same two dishes).

## Performance (grill #55)

Measured on this branch, 2 shared vCPUs, production solver settings (0.1% gap, 20,000
nodes, 10 s backstop), fresh week solves of five seeded profiles:

| profile | before W6 (19fd3ef) | after W6 |
|---|---|---|
| veg, ₹1,500, lunch + dinner | 2.2 s | 0.16 s |
| non-veg, ₹3,500, 3 meals, 6 places | 0.2 s | 0.23 s |
| non-veg, ₹1,400, ₹250 daily cap, 3 meals | 10.2 s (time limit) | 0.52 s |
| non-veg, ₹2,500, lunch + dinner | – | 0.17 s |
| veg, ₹5,000, 3 meals, cooks often | – | 0.17 s |

Re-plans after a tap: 0.1–0.4 s. *Decide for me*: Today 1–2 ms in the engine (≈80–160 ms
with database reads); week 60–180 ms. Tests: `test_decide_today_is_fast_on_a_big_menu`
(800 dishes, < 1 s), `test_week_decide_is_fast`, and
`test_week_replan_proves_optimal_fast_with_production_settings` (proven optimal, < 6 s).

**Paid Render?** Not for the planner: warm solves are well under a second on one shared
CPU. The $7 instance (0.5 CPU, never sleeps) is worth it for the ~1 minute free-tier cold
start (founder answer #55), not for plan quality.

## Open (founder decisions)

1. **Split household meals in the week grid / cart.** A decision row holds one dish, so a
   split (one dish per diet group) is shown in Decide-for-me proposals but `apply` leaves
   that meal for the week planner (which plans a dish safe for everyone, or skips with the
   reason). Per-person cart lines need W4/W7's cart model.
2. **"New places" needs a live search.** In live mode the catalogue holds only your
   places, so *Favourites + new places* and *Surprise me* only add places already read
   (e.g. from a search). Reading new places per decision is a Swiggy call per tap.
3. ~~Rule 4 vs your list.~~ Decided 14 Oct 2026: favourites first; a too-dear list leaves
   the meal open with build-out scenarios instead of a silent cheaper swap or skip.

## One "Decide for me" engine (integration, Oct 2026)

`kernel/decide.py` is the only engine. v3 Today's button (`POST /api/user/<id>/today/decide`,
`smartplate/today.py`) is a thin wrapper: it maps the v3 mode keys (`favourites`,
`favourites_cook`, `favourites_new`, `surprise`) to the engine's (`favourites_only`,
`favourites_plus_cook`, `favourites_plus_new`, `surprise_me`; both spellings are accepted),
passes the Today pool as the favourites (tier *pool*, typed dishes included with no invented
rating), today's budget left as the money, and the Today rules (min rating, max delivery
time) as engine rules, then saves the engine's answer as today's picks with its one-line
*why*. Differences that are data, not logic: on Today, "places" is empty (favourites = the
Today pool; "+ new places" adds live Swiggy dishes only when a live menu exists), choosing
"+ cook at home" lifts the weekly cook-day cap for today, and the daily limit is replaced by
today's budget. Rule changes in the engine: `max_eta` (hide a dish whose known delivery time
is over the limit; the week sets none) and a pooled dish with an unknown rating is not hidden
by the rating rule. W5's "avoid what was picked in the last two days" score is dropped in
favour of the engine's history/recency terms.
