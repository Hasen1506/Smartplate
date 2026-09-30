# Real-menu core and localhost walkthrough

## Approval status, 30 September 2026

The owner supplied a follow-up from Swiggy MCP Support explicitly confirming
`https://smartplate-xgxd.onrender.com/swiggy/callback` is whitelisted and the old
`smartplate.app` URI was removed. Keep the Render public URL and `/swiggy/callback`
path. Buying the proposed domain is unnecessary for this approved integration.
This confirms provider approval; it does not substitute for completing login,
discovering the enabled tools and verifying account-specific payloads.

## Implemented flow

Connect your private profile → choose an actual Swiggy address → search local
restaurants → star verified restaurant IDs → browse/search current menus.
Connected Week builds a persistent seven-day plan from up to five favourites and
positive, explicitly interpreted menu prices. It excludes known unavailable items
and requires a vegetarian flag for vegetarian plans. It uses the profile's meals,
an editable budget and a fee reserve; gaps stay empty. Prices are estimates,
nutrition is unknown, and ingredient safety is unverified. Each meal opens a fresh
item review, rather than automatically placing a scheduled order.

Quantity/options allows up to ten dishes and twenty portions from one restaurant.
Users explicitly select required variants; both documented variant formats are
supported. After preparing variants, the cart supplies variant-specific add-ons.
The app checks selected IDs, limits, availability, quantities and the full cart
again. The final confirmation shows all lines, selected options, delivery address,
payable total and offered COD method. Changed or external carts require review;
an uncertain placement is blocked from retry. Orders above ₹1,000, unavailable
COD, vegan profiles and ingredient/medical exclusions require Swiggy checkout.

Sources: [Access](https://mcp.swiggy.com/builders/docs/operate/access/),
[menu options](https://mcp.swiggy.com/builders/docs/reference/food/search_menu/),
[cart update](https://mcp.swiggy.com/builders/docs/reference/food/update_food_cart/).

## Run the isolated localhost walkthrough

```sh
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
python -m pip install -r requirements.txt
python scripts/local_walkthrough.py
```

Open `http://localhost:5000/__local`. The launcher creates a temporary private
profile/database, completes stub OAuth and selects a stub address. It binds only
to loopback, rejects other Host values and refuses Render. The UI labels example
provider data. No real credential, cart, payment or order is used. Production
entrypoints never import the stub. Reopen `/__local` to restore this temporary
profile on the same running instance; restart creates a fresh walkthrough.

Try star restaurant → Week → build week → review a meal → save a plain dish to
basket draft → add Veg Meals, quantity two, choose Large and confirm identical
options → review whole basket → prepare → review available add-ons → choose Curd
and confirm identical add-ons → review the payable total → confirm the stub order.
Reload after preparing to verify persistence. A draft before preparation is only
in that tab and is cleared when switching profile/address or disconnecting.

Detailed access docs describe a local dev stub without credentials and staging
after access. Localhost development alone is not evidence that a real account or
production payment has been verified. The same production adapters and UI handle
these contracts; the launcher replaces only the provider transport and database.

## Operations

SQLite uses WAL, FULL synchronous writes, a 15-second busy timeout and a migration
ledger. This release supports one web process on one durable disk. It does not
provide multi-instance database replication or a PostgreSQL migration. Use the
existing SQLite backup/restore scripts and verify an off-host backup restore.

Reminder jobs persist with a unique event key, two-minute delivery lease, bounded
retries (six attempts), late expiry after twenty minutes and thirty-day completed
job retention. Schedule edits cancel pending jobs. They never place orders. Push
delivery is at least once: a crash after transmission and before acknowledgement
can repeat a notification; its stable browser tag helps collapse repeats.
Private profiles receive real-week reminders only, not sample-plan reminders.

The web entrypoint starts the sender when `SMARTPLATE_PUSH=on`. Alternatively,
keep `SMARTPLATE_PUSH=on`, set `SMARTPLATE_PUSH_WORKER=off` for the web
process and run `python -m smartplate.reminder_worker`
on the same durable database with the same stable secret/VAPID configuration.
Run `--once` for a scheduler tick. Keep a single schedule producer; leases protect
delivery claims, not the application's legacy solver across multiple processes.
Reliable delivery requires an always-awake host and valid push contact.

Set a strong `SMARTPLATE_OPS_TOKEN` outside source control and poll `/ops/status`
with `Authorization: Bearer …`. It returns aggregate queue counts, worker age,
database check, unresolved order count, disk free and the live-order switch.
It returns 503 when the expected reminder worker is stale. `/readyz` remains the
database readiness check. API responses include generated `X-Request-ID`; request
logs contain route templates, status and elapsed time, without body/query/token
or concrete profile IDs. Provider response/error text is not included in these
request logs. Configure host access logs separately to avoid logging OAuth query
strings. Monitoring still needs an external alert recipient and retention policy.

## Real-account release checks

Now that the callback is approved, complete login on the actual Render app,
confirm `tools/list`, select the intended address and validate returned menus and
customizations. Compare documented IDs and payloads to the live responses. Test
stock/price changes, pagination, token expiry, rate limits, unavailable COD and
an externally edited cart. Keep placement disabled until durable storage, stable
encryption key, restore verification and a support/cancellation process exist.
One supervised purchase requires explicit user confirmation of the actual dish,
address and final amount. Contracts and stub tests cannot certify a real purchase.

Instamart, Dineout, UPI, automatic scheduled purchases and verified nutrition remain
future work requiring their own provider contracts and operational validation.
