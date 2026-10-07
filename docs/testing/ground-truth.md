# Ground-truth test suite

Four layers, all deterministic (frozen clock, seeded data, no network) and all run in CI.

| layer | files | what it proves |
|---|---|---|
| Golden E2E (API) | `tests/test_gt_journey.py` | Sign-up (veg, peanut allergy, ₹2,500) → week → pin/swap → Swiggy search → menu → dish review → approval → after-meal check-in → expenses/insights → settings, compared step by step with `fixtures/golden/journey_asha.json` |
| Golden E2E (browser) | `tests/browser_journey.py` | The same first week clicked through real Chromium, compared with `fixtures/golden/browser_journey_asha.json` |
| Golden planner | `tests/test_gt_golden_planner.py` | Exact plans for 7 fixed profiles (breakfast-only, three meals, fast days, daily cap, prorated, medical, usual places) plus the snack log and nutrition ledger |
| Property-based | `tests/test_gt_properties.py` | Invariants that must never break (below), over random profiles, instants, re-plans and request sequences |
| Differential | `tests/test_gt_differential.py` | pre-#19 vs #19 vs this branch on random profiles; every difference is intentional ([differential-pr19.md](differential-pr19.md)) |
| Regressions | `tests/test_gt_regressions.py` | One test per bug this suite found (GT-1…GT-5) |

## Invariants (Hypothesis)

The oracle in `tests/gt_support.py::check_plan` restates the hard rules independently of the
app code, then every plan, re-plan and checkout is checked against it:

1. Nothing unsafe is planned, pinned, offered or carted: allergens, diabetes/hypertension/celiac
   rules, household members.
2. Vegetarian and vegan profiles never get non-veg; egg is non-veg (FSSAI).
3. Solver-chosen spend never exceeds the weekly room left after pins and money already spent,
   nor the daily cap (all-skip is always feasible, so a feasible plan always exists).
4. Observed fast days keep breakfast and lunch clear.
5. Every enabled meal slot exists once and holds exactly one decision; a week with money and
   safe dishes to spare has no empty meals.
6. Pinned meals survive re-plans, mode switches, favourites, budget changes, ratings, swaps,
   the clock moving on and new meals; a pin that becomes unsafe is dropped, never kept.
7. Prices add up: item + delivery fee × surge = cost; Σ items = checkout total = receipts;
   a live Swiggy total is item + fees exactly or refused, never guessed.
8. Nothing is ordered without an explicit approval of the exact fingerprint and total, and no
   live cart change or order happens without the reviewed fingerprint / fresh approval token.
9. Fuzzed requests to every API route never return 500 (5xx only for Swiggy upstream errors
   (502) or live checkout being off (503)).
10. Variety: a dish at most twice a week and once a day unless the user pinned it there.

## Determinism

- **Clock:** `gt_support.FrozenClock` pins `smartplate.clock` (the only clock the app reads) to
  Mon 2 Nov 2026 08:00 IST; the browser clock is pinned with `page.clock.set_fixed_time`.
- **Network:** `conftest.py::no_network` (autouse, whole suite) refuses every non-loopback
  connection. Weather uses the seeded feed.
- **Swiggy:** `fixtures/swiggy_food_recording.json` holds the recorded Food MCP replies, keyed by
  tool, exact arguments and cart state. Journeys fail on any unrecorded call; fuzzing tests get
  Swiggy's refusal instead. The recording was made from the documented-contract fake in
  `tests/test_swiggy_live.py` — no real Swiggy account, cart or order is ever used.
- **LLM:** none in the default path (`AgentBrain` is the deterministic brain).
- **Hypothesis:** profile `ci` (default) is derandomized with no example database, and each
  property has an example budget sized to its cost (8 for pin survival, which re-plans a whole
  week up to five times, up to 60 for the API fuzzer), so unit + property tests run in about a
  minute; `HYPOTHESIS_PROFILE=deep` ignores the budgets and runs ~400 examples per property.
- **Solver (same week on every machine):** the x86 CI runner and an ARM laptop ship different
  CBC builds, and on CI 5 goldens drifted between them. Three changes make the plan a property
  of the profile, not of the machine:
  1. a fixed, content-derived tie-break (< 1e-4, below any real preference) makes the optimum
     unique, so the build can't choose between equal weeks;
  2. tests solve to a proven optimum (gap 0) and stop only on the deterministic branch-and-bound
     node cap (`SOLVER_MAX_NODES`: 20,000 in production, 3,000 in tests), never on the wall clock;
     production keeps a 10 s time limit as a backstop only;
  3. valid delivery-count cuts (money left ÷ cheapest delivery, per week and per day) tighten the
     relaxation. One known hard solve remains: the daily-capped Dev profile's first (balanced)
     week stops at the node cap without a proof (~2.4 s locally); its golden is taken after the
     switch to Tight Week, which re-plans from scratch (`stable=False`) and is proven optimal.
  Every golden plan asserts `proven_optimal`; a week that only reaches the node cap is covered by
  the property tests, which check rules rather than exact dishes.
- Goldens are stable across `PYTHONHASHSEED` values; CI also pins `PYTHONHASHSEED=0`.
- **Speed:** each worker seeds the database once per fixture kind (`conftest.py::_fresh_seeded_db`)
  and gives every later test a byte copy, a new file with identical data, which cuts ~0.1 s of
  setup from each of ~230 tests.

## Commands

```bash
pip install -r requirements-dev.txt
pytest -q -n auto                                   # unit + property + golden + differential (~55 s on 2 cores)
pip install playwright==1.63.0 && playwright install chromium
pytest -q tests/browser_journey.py tests/browser_smoke.py   # browser E2E
# PLAYWRIGHT_CHROMIUM_EXECUTABLE=/usr/bin/chromium to use a system Chromium

SMARTPLATE_UPDATE_GOLDEN=1 pytest tests/test_gt_golden_planner.py tests/test_gt_journey.py   # re-bless goldens
SMARTPLATE_RECORD_SWIGGY=1 pytest tests/test_gt_journey.py tests/browser_journey.py          # re-record Swiggy
HYPOTHESIS_PROFILE=deep pytest tests/test_gt_properties.py                                   # soak
```

Re-blessing a golden or the recording is a code change: review the JSON diff and say why it moved.
