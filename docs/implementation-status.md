# What PR #17 actually implements

This inventory describes the draft branch, not the public Render deployment.
The detailed audit records the original main-branch defects and future suggestions.
It is not a claim that every expansion has been built.

## Implemented code

| Capability | Implementation | Verification |
|---|---|---|
| Personal connection | Private profile required; OAuth dynamic registration, PKCE, single-use state, server-side encrypted token; sample connections cannot expose addresses or order history | `tests/test_followups.py`, `tests/test_accounts.py` |
| Real local restaurants | Search receives a saved Swiggy address ID; address pagination and address-specific favourites; fresh menu uses exact provider restaurant ID | `tests/test_swiggy_live.py` |
| Actual menu selection | Browse provider items, then fresh scoped `search_menu` checks exact item ID/name, restaurant, stock and options; bounded pagination finds later-page items | `tests/test_swiggy_live.py` |
| Safe cart preparation | Explicit item review; refuses existing/malformed carts; confirms restaurant/item/quantity/address after mutation; persistent intent restores prepared carts after reload | `tests/test_swiggy_live.py`, `tests/frontend.test.cjs` |
| User-approved COD purchase | Live placement deployment switch defaults off; current cart, offered payment, full address, restaurant, quantity and payable total are reviewed; latest five-minute approval is consumed atomically | `tests/test_swiggy_live.py`, `tests/frontend.test.cjs` |
| Duplicate/uncertain submission protection | One attempt per approval; all unresolved attempts block the profile even after a changed price/address; timeout or pending payment cannot report a confirmed purchase; no automatic placement retries | `tests/test_swiggy_live.py` |
| Purchase visibility | Provider recent orders beside local attempts, plus tracking for recorded provider order IDs; uncertainty stays blocked pending operator reconciliation | `tests/test_swiggy_live.py` |
| Provider errors | Failed MCP/payload responses surface errors; 401 expires the saved connection; 429 exposes Retry-After where usable | `tests/test_swiggy_live.py` |
| Honest demo boundary | Connected Today/Places use real menu flow; sample weekly optimization and estimates remain labelled; no fabricated fallback restaurants for live search | `tests/frontend.test.cjs` |
| Deployment preparation | Paid single-instance disk Blueprint, database readiness, live-order startup guard, consistent SQLite backup and restore test; private exports use header authentication | `tests/test_hosting.py`, `tests/test_backup_sqlite.py`, `tests/test_followups.py` |

All provider integration tests use a fake MCP server with documented response
shapes. That fake is test infrastructure, never a source of live user menu data.
CI cannot prove that a real account has the tools, payloads, stock or COD support.

## Exact product boundary

The implemented ordering pilot selects one plain menu item, quantity one, from a
real restaurant for a selected saved address. SmartPlate can place it after an
explicit fresh review when Swiggy offers COD and the operator enables placement.
Swiggy's current Builders Club limit is checked at ₹1,000. Other payments,
customizations and profiles needing vegan, allergen or medical ingredient
verification use Swiggy checkout. No source available here establishes ingredient
or cross-contact safety. A cart changed outside SmartPlate must be reviewed or
cleared in Swiggy before selecting an item again.

The weekly planner still uses the demo catalogue. There is no production weekly
optimizer fed by all live favourites, no unattended ordering, and no guaranteed
live weekly spend ceiling. The current real menu flow is user selection, not a
fully autonomous meal agent.

## Still external or operational

1. Swiggy must accept the exact deployed callback. The Render callback was rejected
   in the observed 30 September browser attempt; the email does not identify its URI.
2. A signed-in pilot must verify real tool discovery and address/menu/cart/payment
   contracts before a supervised COD order. Capture redacted failures as well.
3. The operator must apply the persistent deployment, preserve secrets/migrate any
   existing data, arrange encrypted off-host backups and prove restore, and own
   purchase/refund support. The PR supplies code and a runbook; it does not activate
   a paid hosting subscription or a backup destination.
4. A timeout after submission has no safe automatic matching rule without real
   provider identifiers. History helps investigate; an operator must reconcile
   that attempt before clearing its block. Never infer acceptance from matching
   names or totals alone.

## Recommendations that remain separate projects

These audit suggestions are not implemented capabilities: Postgres/migrations
for multiple instances; durable reminder jobs; stronger session identity and
account recovery/deletion/full export; monitoring and support automation; verified
charge/refund accounting; multi-item/options/UPI ordering; live weekly optimization;
households; Instamart pantry shopping; Dineout bookings; workplace allowances;
travel/events; and clinical nutrition. They need additional data contracts,
business rules or provider permissions. Presenting empty UI or simulated outcomes
for them would not satisfy a real ordering product.

## Remaining live tests

Use `production-deployment.md` for the pilot sequence: exact-origin OAuth,
restart during OAuth, private profiles on two browsers, saved addresses across
pages, no serviceability, closed restaurants, empty/truncated menus, price/stock
changes, options, expired authorization, rate limits, externally edited cart,
unavailable COD, a reviewed supervised order, tracking/history, and an ambiguous
response without retry. Also check phone layouts/keyboard navigation, persistent
redeploy, backup restore and reminder delivery. Keep placement off until these
gates are met.
