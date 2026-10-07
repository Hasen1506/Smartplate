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
Mon 2 Nov 08:00, Wed 4 Nov 12:00, and the Navratri week of 12 Oct):

| measure | result |
|---|---|
| plans changed by PR #19 (pre19 → main) | 24 / 150, **all** explained by PR19-D1 |
| budget recommendation changed by PR #19 | 0 / 150 |
| plans changed by this PR (main → current) | 91 / 150, **all** explained by GT-1/2/3 |
| budget recommendation changed by this PR | 1 / 150 (a vegetarian profile; Egg Puff left the safe menu) |
| meals left empty (skip), main → current | 744 → 557 (−25%) |
| total planned spend, main → current | ₹1,30,299 → ₹1,69,969, never over a weekly budget (0 / 150) |
| egg dishes planned for vegetarian/vegan profiles, main → current | 4 → 0 |

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

## Observed, not changed

- **Tight Week (survival) leaves many meals empty with budget left.** In the golden
  `planner_tight_week_daily_cap` profile (₹1,400, ₹250/day) 13 of 21 meals are skipped
  with ₹611 unspent, because the survival objective prices a meal above the skip penalty
  (1.6). The mode's promise is "the cheapest week that still hits your nutrition", which
  this does not do; changing the objective is a product decision, so it is pinned by the
  golden snapshot and left for review.
