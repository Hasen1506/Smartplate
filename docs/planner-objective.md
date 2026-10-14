# What the planner optimises (and why)

*Answers grill #53 ("what is the algorithm's goal?") and #54 ("weights nobody can
explain"). Code: `smartplate/kernel/objective.py` (the rules and weights),
`smartplate/kernel/decide.py` ("Decide for me"), `smartplate/kernel/optimizer.py` (the
week plan).*

## The goal in one line

**Plan the meals you said you order, from what you like, inside your money, without ever
breaking a safety rule.** Money is the limit. Your choice is the objective.

## The order of priority (lexicographic, not a weighted soup)

A lower rule never buys a higher one.

| # | Rule | Kind | What it means |
|---|---|---|---|
| 1 | **Safe** | filter | Never break a diet, allergy or medical rule of *anyone eating* that meal (household members too). Unknown counts as unsafe. Applied before anything else. |
| 2 | **Within the money** | hard limit | Today's money (Today) or this week's money left (Week), delivery fee included; the daily limit too, if you set one. |
| 3 | **Favourites** | what may be planned | Only what the mode you picked allows (below). Inside that, the dish with the higher *favourite score* wins. |
| 4 | **No silent skips** | ranked above the score | Fill every meal you said you order. A cheaper dish from your places beats a skip. A meal is skipped only when nothing allowed fits, and the reason says which rule it was. |
| 5 | **Cheapest among equals** | tie-break | Two dishes you like about the same (favourite scores within 0.05): the cheaper one. |
| – | **Variety** | cap | A dish at most twice a week (your setting) and once a day. A short meal list of your own may repeat as often as it has to. |

Rule 3 decides *what is allowed*; rule 4 sits above the favourite *score*. That is the
founder's "a cheaper favourite beats a skip". So the key that compares two plans is:

```
(meals filled ↑,  favourite score ↑,  cost ↓,  repeats ↓)
```

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
| `W_SLOT` | 0.20 | Right for this meal (a tiffin dish at breakfast, a full plate at lunch; no idli as dinner unless it's in your dinner list) |
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
- **Pool fallback**: a meal with a list of your own dishes now falls back to other safe
  dishes *at your places* (`POOL_FALLBACK_PEN` = 1.0, so a list dish always wins when it
  fits) instead of skipping when the list is too dear or not safe for someone eating.
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
- **Honest skips**: every skip in every mode says the real cause (money left vs cheapest
  option, daily limit, variety cap, nothing safe for a named household member); the plan
  summary counts skips *by cause* instead of "skipped to stay within budget".

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
3. **Rule 4 vs your list.** A meal with its own list now falls back to other dishes at the
   same places rather than skipping. If Vijay wants "my list or nothing", that is one flag.
