# Differential: planner and recommender before and after PR #19

`tests/test_gt_differential.py` extracts three versions of the `smartplate` package from
git and plans the same random profiles with each, every version on its own database and
frozen clock:

| name    | what                         | `smartplate/` tree |
|---------|------------------------------|--------------------|
| pre19   | main before PR #19 (`4bd8408`) | `cd15aa2e…`      |
| main    | main with PR #19 (`33c6916`)   | `db0833e7…`      |
| current | this branch                   | working tree      |

Every difference must be explained by one of the intentional changes below; any other
difference fails the test with the meal-by-meal diff. CI checks out full history
(`fetch-depth: 0`) so both baselines are available without network access.

## Measured on 150 seeded random profiles

Generated with the same machinery (seed `20261007`, the three plan instants the test uses:
Mon 2 Nov 08:00, Wed 4 Nov 12:00, and the Navratri week of 12 Oct). Re-measured on
7 Oct 2026 after the solver was made deterministic: every version now gets the planner's
tie-break and solves to a proven optimum (gap 0, node cap 3,000, no wall-clock limit), so a
difference is a real change, not two equally good weeks resolved differently.

| measure | result |
|---|---|
| plans changed by PR #19 (pre19 → main) | 8 / 150, **all** explained by PR19-D1 |
| budget recommendation changed by PR #19 | 0 / 150 |
| plans changed by this PR (main → current) | 86 / 150, **all** explained by GT-1/2/3 (GT-1: 59, GT-2: 62, GT-3: 73; a plan can have several) |
| budget recommendation changed by this PR | 1 / 150 (a vegetarian profile; Egg Puff left the safe menu, so its floor rose ₹1,513.75 → ₹1,557.50) |
| meals left empty (skip), main → current | 744 → 557 (−25%) |
| total planned spend, main → current | ₹1,30,299 → ₹1,69,980, never over a weekly budget (0 / 150) |
| egg dishes planned for vegetarian/vegan profiles, main → current | 4 → 0 |
| unexplained differences | 0 |

The previous measurement (before the tie-break was applied to the baselines) counted 24 plans
changed by #19 and 91 by this PR; the extra 16 and 5 were equal-cost weeks that CBC happened to
resolve differently, which is exactly the machine-dependence fixed below.

The CI test (`test_planner_differences_are_all_intentional`) runs 20 derandomized Hypothesis
profiles per push (about 14 s on the CI runner); the 150-profile figures above come from the same
`Version` machinery run by hand.

## Intentional differences

### PR #19
- **PR19-D1 — home-cooking recipes obey allergies and medical rules.** Before #19 a cook
  option was gated by diet only, so an egg- or gluten-allergic (or celiac) person could be
  told to cook egg curry with roti or masala oats. Plans differ only for profiles with an
  egg or gluten allergy or celiac; the replaced recipe is always a safe one.
- **No recommender change.** `recommender.recommend` is byte-identical across #19 and its
  menu-safety call only gained household members, which a single profile doesn't have.
- **Execution-side changes in #19** (past meals are never ordered, app-clock timestamps,
  household union in `allergens.violates`) don't change a new profile's plan and are covered
  by the existing `tests/test_audit_fixes_2026_10_07.py`.

### This PR
- **GT-1 — egg is non-vegetarian.** Indian veg/non-veg marks (FSSAI) count egg as
  non-vegetarian. "Egg Puff + Chai" was seeded `veg=1`; it is now `veg=0`, and
  `allergens.violates` treats any dish with an egg allergen as non-veg for veg and vegan
  profiles even if a catalogue row is mislabelled. Vegetarian and vegan plans change wherever
  Egg Puff was a candidate.
- **GT-2 — enough distinct dishes to fill the week.** Each meal offered the same six
  cheapest dishes and a dish may appear at most twice a week, so a week could hold at most
  12 deliveries: a 21-meal vegetarian week at ₹3,700 skipped 9 meals "for budget" while
  spending 37% of it. Each meal now offers `max(6, ceil(open meals / 2) + 1)` dishes. Plans
  with more than 10 open meals change.
- **GT-3 — "mostly my usual places" never empties a meal.** The cap on new places is now
  `max(variety share, meals the usual places and cooking can't cover)`, and when the usual
  places have too few dishes more new candidates are offered. Profiles with favourites
  change.
- **GT-4 — pinned meals count toward "twice a week".** Only affects plans with pins (none
  in the random profiles; pinned by `test_gt_regressions.py::test_gt4…`).
- **GT-5 — simulated substitution keeps the variety rules.** Execution only; pinned by
  `test_gt_regressions.py::test_gt5…`.

## Observed, not changed — open product decisions

- **Tight Week (survival) leaves many meals empty with budget left.** In the golden
  `planner_tight_week_daily_cap` profile (₹1,400 a week, ₹250 a day, three meals) 13 of 21
  meals are skipped with ₹611 unspent, because the survival objective prices a meal above the
  skip penalty (1.6). The mode promises "the cheapest week that still hits your nutrition",
  which this does not do. Changing the objective is a product decision, so the behaviour is
  pinned by the golden snapshot and left for review.
- **Live Swiggy checkout preview answers 502 when Swiggy isn't connected.** With live
  ordering on and no Swiggy connection, `GET /api/user/<id>/swiggy/checkout/preview` returns
  **502** `{"code": "swiggy_error", "error": "Connect your Swiggy account first (More → Swiggy
  connection)."}` — an upstream-failure status for what is really a precondition the person can
  fix (`POST …/swiggy/checkout` correctly answers 409 `cart_changed`). The message is right; the
  status makes monitoring count it as a Swiggy outage. The fuzz property tolerates 502/503 only on
  these Swiggy paths. Whether it should be 409/428 is a product/API decision, left for review.

## Determinism across machines (this PR)

CI run 37587458773 failed with 5 goldens drifting between the x86 CI runner and an ARM
machine: equal-cost weeks were resolved differently by the two CBC builds, and a daily-capped
week hit the 10-second limit, so its plan depended on runner speed. Fixed without retries or
quarantine:

- a fixed, content-derived tie-break (< 1e-4) makes the optimum unique;
- tests solve to a proven optimum and stop only on the deterministic node cap
  (`SOLVER_MAX_NODES`, 3,000 in tests, 20,000 in production), never the wall clock;
- valid delivery-count cuts (money left ÷ cheapest delivery, per week and per day);
- each golden plan asserts `proven_optimal`.
