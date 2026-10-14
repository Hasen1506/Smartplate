# Feature map (internal)

Moved out of the public `/api/meta` reply in Oct 2026 (grill #32): it named features after the
brainstorm's section numbers and advertised modules (carbon scoring, health/longevity) to anyone.
The modules are unchanged; this is only the developer index.

## §5.1

| # | Feature | Where |
|---|---|---|
| 1 | Allergy & medical hard-exclusions | `smartplate/domain/allergens.py` |
| 2 | Calendar integration | `smartplate/integrations/calendar_sync.py` |
| 3 | Idempotency keys on order placement | `smartplate/integrations/swiggy_mcp.py` |
| 4 | Explainability log per decision | `smartplate/kernel/explainability.py` |
| 5 | Pause / snooze / cooked | `smartplate/kernel/scheduler.py` |

## §5.2

| # | Feature | Where |
|---|---|---|
| 6 | Nutrition layer | `smartplate/domain/nutrition.py` |
| 7 | Household / group mode | `smartplate/domain/household.py` |
| 8 | Leftover & home-cooking awareness | `smartplate/domain/leftovers.py` |
| 9 | Festival / cultural calendar | `smartplate/domain/festivals.py` |
| 10 | Weather adaptation | `smartplate/domain/weather.py` |
| 11 | Surge prediction | `smartplate/domain/surge.py` |

## §5.3

| # | Feature | Where |
|---|---|---|
| 12 | Reverse mode (Instamart cook days) | `smartplate/domain/reverse_mode.py` |
| 13 | Community plan templates | `smartplate/domain/community.py` |
| 14 | Receipt / expense surface | `smartplate/domain/receipts.py` |
| 15 | Health / longevity integration | `smartplate/domain/health.py` |
| 16 | Carbon / sustainability scoring | `smartplate/domain/carbon.py` |
| 17 | Cooking-mode coach | `smartplate/domain/cooking_coach.py` |
