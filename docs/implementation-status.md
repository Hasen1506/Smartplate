# What PR #17 actually implements

This inventory describes the draft branch, not the public Render deployment.
The detailed audit records the original main-branch defects and future suggestions.
It is not a claim that every expansion has been built.

## Implemented code

| Capability | Implementation | Verification |
|---|---|---|
| Personal connection | Private profile required; OAuth dynamic registration, PKCE, single-use state, server-side encrypted token; sample connections cannot expose addresses or order history | `tests/test_followups.py`, `tests/test_accounts.py` |
| Private data controls | Header-authenticated export excludes credentials and other users; explicit deletion removes local dependent records atomically; new published templates track ownership privately | `tests/test_profile_data.py` |
| Recovery and browser protections | Recovery-code rotation revokes old codes/device access and pending checkout/OAuth approvals; local script CSP blocks inline scripts and embedding; sign-out clears private UI state | `tests/test_accounts.py`, `tests/test_api.py`, `tests/frontend.test.cjs` |
| Real local restaurants | Search receives a saved Swiggy address ID; address pagination and address-specific favourites; fresh menu uses exact provider restaurant ID | `tests/test_swiggy_live.py` |
| Actual menu selection | Browse provider items, then fresh scoped `search_menu` checks exact item ID/name, restaurant, stock and options; bounded pagination finds later-page items | `tests/test_swiggy_live.py` |
| Dish search beyond browse limits | User searches within a verified provider restaurant and loads subsequent provider offsets; preserves real item IDs and excludes another restaurant's results | `tests/test_swiggy_live.py`, `tests/frontend.test.cjs` |
| Safe cart preparation | Explicit item review; refuses existing/malformed carts; confirms restaurant/item/quantity/address after mutation; persistent intent restores prepared carts after reload | `tests/test_swiggy_live.py`, `tests/frontend.test.cjs` |
| User-approved COD purchase | Live placement deployment switch defaults off; current cart, offered payment, full address, restaurant, quantity and payable total are reviewed; latest five-minute approval is consumed atomically | `tests/test_swiggy_live.py`, `tests/frontend.test.cjs` |
| Duplicate/uncertain submission protection | One attempt per approval; all unresolved attempts block the profile even after a changed price/address; timeout or pending payment cannot report a confirmed purchase; no automatic placement retries | `tests/test_swiggy_live.py` |
| Purchase visibility | Provider recent orders beside local attempts, plus tracking for recorded provider order IDs; uncertainty stays blocked pending operator reconciliation | `tests/test_swiggy_live.py` |
| Provider errors | Failed MCP/payload responses surface errors; 401 expires the saved connection; 429 exposes Retry-After where usable | `tests/test_swiggy_live.py` |
| Honest demo boundary | Connected Today/Places use real menu flow; sample weekly optimization and estimates remain labelled; no fabricated fallback restaurants for live search | `tests/frontend.test.cjs` |
| Export and calendar input protection | CSV text labels cannot become spreadsheet formulas; calendar import rejects foreign plans, invalid IDs and malformed events without partial inserts | `tests/test_export_security.py`, `tests/test_followups.py` |
| Deployment preparation | Paid single-instance disk Blueprint, database readiness, live-order startup guard, consistent SQLite backup and restore test; private exports use header authentication | `tests/test_hosting.py`, `tests/test_backup_sqlite.py`, `tests/test_followups.py` |
| Browser walkthroughs in CI | Actual Chromium on desktop and phone viewports exercises favourites, menu search, cart/reload, approval/placement/tracking, credential-free export, recovery rotation and deletion; checks script/CSP errors and horizontal overflow | `tests/browser_smoke.py` |

All provider integration and browser tests use a fake MCP server with documented response
shapes. That fake is test infrastructure, never a source of live user menu data.
CI cannot prove that a real account has the tools, payloads, stock or COD support.

## Exact product boundary

The implemented ordering pilot selects one plain menu item, quantity one, from a
real restaurant for a selected saved address. SmartPlate can place it after an
explicit fresh review when Swiggy offers COD and the operator sets mode `live_order`.
Swiggy's current Builders Club limit is checked at ₹1,000. Other payments,
customizations and profiles needing vegan, allergen or medical ingredient
verification use Swiggy checkout. No source available here establishes ingredient
or cross-contact safety. A cart changed outside SmartPlate must be reviewed or
cleared in Swiggy before selecting an item again.

The weekly planner still uses the demo catalogue. There is no production weekly
optimizer fed by all live favourites, no unattended ordering, and no guaranteed
live weekly spend ceiling. The current real menu flow is user selection, not a
fully autonomous meal agent.

The welcome screen describes the connected real-menu flow. New profile creation
leads to connection setup. Weekly screens explicitly mark sample dishes, nutrition
and prices; their budget card is an estimate and does not claim actual live spend.

## Platform status (Oct 2026, W7)

- **Swiggy is connected and the Render origin is whitelisted.** Founder confirmation,
  Oct 2026: sign-in from `https://smartplate-xgxd.onrender.com` works and real carts were
  filled on 8–9 Oct (docs/IDEAS.md). The 30 September "Onrender isn't whitelisted yet"
  rejection is history. `/api/meta` derives `swiggy_redirect_approved` from the
  whitelisted callback (`config.APPROVED_SWIGGY_REDIRECTS`) instead of a hand-set flag.
- **One mode, reported truthfully.** `SMARTPLATE_SWIGGY_MODE` = `demo` | `live_read` |
  `live_cart` (default) | `live_order`; `/api/meta` reports `swiggy_mode` and `swiggy_can`.
  `SMARTPLATE_SWIGGY` now only governs the old simulated saga in fixture test runs, so
  `swiggy_provider` says `live` in production (it used to say `simulated` while real carts
  were being filled, grill #34).
- **Payments.** Nothing is paid without the user's explicit approval in Ziggy. Default
  mode `live_cart` never places an order: checkout and payment happen in Swiggy.
  `live_order` places only a reviewed, approved Cash on Delivery order. Swiggy's MCP UPI
  stage (`get_payment_options` -> `place_food_order` with a UPI intent/QR ->
  `PENDING_PAYMENT` -> `confirm_order`, added July 2026) is a documented future switch,
  not built (docs/research/platform-2026-10.md). Ziggy never collects money itself.
- **Order-history favourites.** `POST /api/user/<id>/favourites/suggested` with
  `{"consent": true}` reads `get_food_orders` once (up to 15 orders) and keeps minimal
  suggestions; `GET` shows them with the consent text; `DELETE` removes them.
- **Without Swiggy.** `PUT /api/user/<id>/manual-address` and
  `/api/user/<id>/typed-favourites` work with no platform account; ordering is a hand-off.
- **Provider seam.** `smartplate/providers/`: Swiggy Food wired; Instamart, Toing and
  Zomato are stubs with the terms note (Zomato MCP is personal-use only; Toing has no API).
- **Address change** re-reads menus for the new address and re-plans open meals, telling
  the user which places were dropped. Provider errors are shown in plain words, never JSON.

## Still external or operational

1. (Resolved Oct 2026) Swiggy accepts the Render callback; keep
   `SMARTPLATE_SWIGGY_APPROVED_REDIRECTS` in step if the domain changes.
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
email-based account recovery; monitoring and support automation; verified
charge/refund accounting; multi-item/options/UPI ordering; live weekly optimization;
households; Instamart pantry shopping; Dineout bookings; workplace allowances;
travel/events; and clinical nutrition. They need additional data contracts,
business rules or provider permissions. Presenting empty UI or simulated outcomes
for them would not satisfy a real ordering product.

Profile deletion applies to the active local database and new community posts
linked to their author profile. Legacy public templates without an author ID
cannot be reliably assigned from a name and need operator review. Off-host
backups require a deletion/retention policy so restoring old data does not undo
an account deletion. Swiggy's independent account/orders are managed in Swiggy.

## Remaining live tests

Use `production-deployment.md` for the pilot sequence: exact-origin OAuth,
restart during OAuth, private profiles on two browsers, saved addresses across
pages, no serviceability, closed restaurants, empty/truncated menus, price/stock
changes, options, expired authorization, rate limits, externally edited cart,
unavailable COD, a reviewed supervised order, tracking/history, and an ambiguous
response without retry. CI covers desktop/phone Chromium with an isolated provider;
actual devices, keyboard navigation and other browsers still need pilot checks.
Also verify persistent redeploy, off-host restore and reminder delivery. Keep placement off until these
gates are met.
