# Ziggy — private trial

*Ziggy (Zomato + Swiggy, with a little elephant) is the app; the repository, package and
API header keep the original SmartPlate name.*

A **scheduling** AI agent for food delivery — the white space the
[strategic brainstorm](SmartPlate_Brainstorm.html) identified: scheduled +
predictive + budget-constrained + delivery-integrated + autonomous substitution.

This repo is the **working v1.1 app**. It plans a week of meals with a real
mixed-integer optimiser (the "agent" is a solver, **not** an LLM — see
[FEASIBILITY.md](FEASIBILITY.md)) and integrates **all 17 gap features** from
§5.1, §5.2 and §5.3 of the brainstorm. The Debt/EMI integration is intentionally
out of scope, as requested.

> **Read [FEASIBILITY.md](FEASIBILITY.md) first.** It answers "is this possible?"
> (yes) and "does agentic execution cost too much, should we hold?" (no — v1.1
> runs at ≈₹0 per decision because the planner is a CPU solver, not a paid model).

For the differentiated product thesis, revenue experiments, production gates, and
terms-safe Swiggy/Swiggy Money rollout, see
[the September 2026 strategy memo](docs/product-strategy-2026.md). For "is this
worth building, connector or app, who pays, and how do we keep it simple?", see
[value and simplicity](docs/value-and-simplicity.md).

## What using it feels like

Three tabs (Today, Week, Saved) and your avatar for everything else (Me). Light, dark
or your phone's setting, and an optional Zomato-red-to-Swiggy-orange colour mesh.

1. **Get started**: four quick steps (what you eat, how your days go, a weekly budget,
   a sign-in). Ziggy plans the week while the elephant thinks, then offers to connect
   Swiggy right away (phone + OTP on Swiggy's own page, then tap your address).
2. **Today** shows the next meal, why it was picked (in plain words, never a score),
   its estimated price and **when to order it**. **Add to Swiggy cart** is one tap: the
   elephant rolls across the button, then Swiggy's real bill appears line by line and
   *Pay in Swiggy* opens checkout. Ziggy never pays for you. Right under it, **Or have
   instead** shows up to four other dishes for the same meal (and a cook-at-home
   option) with Swiggy's own photos: one tap swaps the meal, no sheet.
   **Hungry now?** appears when it's mealtime and the week leaves that meal out (or
   you skipped it): one tap puts a dish that fits into today, the next adds it to
   the cart, and the rest of the week re-balances.
3. **Change** (on any meal) opens one sheet: *Better fits*, *Other places* (switch
   restaurants with one tap) and *Cook*, plus Skip (with Undo), Keep and Swap with
   another meal.
4. **Week**: a day strip, the chosen day's meals, the budget at a glance, cooking and
   the grocery list (each line opens Instamart's search; *Copy list* for pasting),
   and ordering several meals at once. On a computer, a side panel shows the week's
   balance: money left, nutrition a day against your own targets, the order / cook /
   skip mix, a day-by-day glance and the household split.
5. **Saved**: your places (search Swiggy and tap ♥), dishes you rated Good, and meals
   you've had, each with *Order again*.
6. **Heads-up and reminders**: rain, heat, holidays, the fasts you keep, budget
   warnings, and a nudge at order-by time (push while the web process is awake, or
   export the times to your calendar).
7. **Installable and private**: add it to your home screen. A profile you create
   is private; add a sign-in name and password to open it on your other devices.
8. **Swiggy, once the exact redirect is approved**: choose your saved address,
   search real nearby restaurants, star favourites, inspect current menus and put
   one exact item in the cart. Review and pay in Swiggy. An optional Cash on
   Delivery order path requires a separate server flag and explicit approval.

## Try it in your browser

**Host your own copy (free, about 5 minutes):**

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/Hasen1506/Smartplate)

Sign in to Render with GitHub, keep the defaults and choose **Deploy Blueprint**.
When the build finishes, open the `https://smartplate-….onrender.com` link on your
phone and use **Add to Home Screen**. The blueprint is [render.yaml](render.yaml).

On Render's free plan the app sleeps after about 15 minutes idle, so the first visit
afterwards takes about a minute. Its disk is also wiped on spin-down, restart or redeploy,
so by default profiles there are a demo. **To keep them at no cost, give the app a free
Postgres database on Neon** (next section). Any host that runs a `Procfile` (Railway,
Koyeb, Heroku) works the same way; run **one** worker process.

### Keep your data: free Postgres on Neon

With `DATABASE_URL` set, Ziggy stores everything (profiles, private-profile keys,
sign-ins, plans, ratings, receipts, Swiggy links, push subscriptions) in that Postgres
database instead of the SQLite file, so restarts, spin-downs and redeploys lose nothing.
Without it the app uses SQLite, as before (local runs, Codespaces, tests).

1. **Create the database.** Sign in at [console.neon.tech](https://console.neon.tech)
   and choose **New project**: name `smartplate`, region **AWS Asia Pacific (Singapore)**
   (the Render service runs in Singapore), default Postgres version. The Free plan is enough.
2. **Copy the connection string.** On the project dashboard choose **Connect**, keep
   branch `main` and database `neondb`, turn **Connection pooling** on and copy the URL. It
   looks like `postgresql://neondb_owner:…@ep-…-pooler.ap-southeast-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require`.
   Treat it as a password.
3. **Give it to Render.** Render dashboard → service **smartplate** → **Environment** →
   **Add Environment Variable**: key `DATABASE_URL`, value the URL → **Save, rebuild, and
   deploy**. (`render.yaml` declares `DATABASE_URL` with `sync: false`: Render asks for it
   when a new Blueprint is created, but an existing service needs this manual step.)
   Leave `SMARTPLATE_SECRET` unchanged: it encrypts the Swiggy tokens stored in the database.
4. **Check it.** Open `https://smartplate-xgxd.onrender.com/healthz`: it must show
   `"engine": "postgres", "persistent": true`, and the yellow “Trial server … erased”
   banner is gone. On first start the app creates its tables and the three sample
   profiles in Neon. Make a test profile, then **Manual Deploy → Restart service** (or
   wait for a spin-down) and open it again: it is still there.

**Moving an existing SQLite database (optional).** If you have a SmartPlate SQLite file
worth keeping (a local or Codespaces `smartplate.db`, or a snapshot from
`scripts/backup_sqlite.py`), copy it in before step 3, while Neon is still empty:

```bash
pip install -r requirements.txt
export DATABASE_URL='postgresql://…neon.tech/neondb?sslmode=require'   # the URL from step 2
python scripts/sqlite_to_postgres.py smartplate.db --dry-run   # copy, verify, roll back
python scripts/sqlite_to_postgres.py smartplate.db             # the real copy
```

It copies every table with its ids, checks the row counts and commits once (on any error
nothing is written); the source file is never changed. If the app has already started on
Neon and seeded its sample profiles, add `--replace` to overwrite them with the file's
contents. The free Render instance's own disk can't be copied this way: Render's free
plan has no shell, and setting `DATABASE_URL` redeploys onto a fresh disk, so profiles
created on the free trial server before the switch are not carried over.

**Free-tier notes.** Neon's Free plan ([plans](https://neon.com/docs/introduction/plans))
includes 1 GB of storage per project and 100 compute-hours a month, and suspends the
database after 5 minutes without queries; the first request after that waits a moment
while it wakes, and the app's connection pool re-checks connections before use. The free
Render instance itself sleeps after 15 minutes idle, which keeps Neon's compute use low;
an uptime pinger that keeps Render awake around the clock would also keep Neon awake
(about 180 compute-hours a month at the smallest size), past the free allowance.
To go back to SQLite, delete `DATABASE_URL` (data then lives on the temporary disk again).

For a private persistent pilot, review the separate [production Blueprint](render.production.yaml)
and [deployment runbook](docs/production-deployment.md) before applying it.

**Swiggy sign-in on a hosted URL requires an exact approved redirect URI.** In
Me → Swiggy, copy the callback URL shown for this deployment (for
example, `https://your-service.onrender.com/swiggy/callback`) and give that exact
URI to [Swiggy Builders Club](https://mcp.swiggy.com/builders/docs/operate/access/).
An approval for another hostname or path does not cover this URL. Swiggy creates
the OAuth client ID through dynamic client registration, so there is no separate
client ID to apply for; provider onboarding and redirect approval still apply.
As of 30 September 2026, Swiggy's sign-in page rejects the current Render callback
with “Onrender isn't whitelisted yet”; the earlier approval email did not identify
the URI. The optional `SMARTPLATE_SWIGGY_CALLBACK_PATH=/auth/swiggy/callback` supports that
path if Swiggy has approved the resulting URL on a host you control. Set
`SMARTPLATE_PUBLIC_URL` to that host's public HTTPS origin only when it actually
routes to this app. Never point OAuth at a proposed or third-party domain. Until
the approved URI and live contract are verified, use the manual Swiggy hand-off.
Once Swiggy has approved this deployment's exact callback, set
`SMARTPLATE_SWIGGY_REDIRECT_APPROVED=1`; until then the app tells visitors that
connecting Swiggy is not available on that server yet, instead of promising real restaurants.

`/healthz`, `/readyz` and `/api/meta` report whether the database survives a restart
(`database.persistent` / `storage.persistent`). It is persistent with `DATABASE_URL`
(Postgres) or, on Render, under the disk mount (`/var/data`); SQLite on the free
instance's temporary disk is not, and then the app shows every visitor a banner saying
their data can be erased.

**Or use GitHub Codespaces (private to you):**
[Open SmartPlate in GitHub Codespaces](https://codespaces.new/Hasen1506/Smartplate?quickstart=1).
Choose **Create codespace**, wait for setup, then open **Ports → SmartPlate / 5057 →
Open in Browser**. Keep the port private.

Both use the real Python planner with a sample Chennai catalogue. Sample profiles
are shared by everyone who opens the app; a profile you create is private.
Swiggy ordering is a hand-off to Swiggy's own search (see
[verified findings](docs/vendor/swiggy/README.md) and
[trial instructions](docs/TRY-SMARTPLATE.md)).

## Run it locally

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python scripts/fetch_epicure.py     # Epicure ingredient embeddings (~3.8 MB, pinned + checksummed)
python run.py                       # → http://localhost:5057
```

Without the Epicure files the app still runs; ingredient swaps, “more like this” and the
cuisine lean are simply hidden. Epicure-Core is © 2026 Jakub Radzikowski and Josef Chen
(KAIKAKU.AI), CC BY 4.0 — see `/credits` in the app.

No build step, no Node, no API keys. The database is created and seeded with a
demo Chennai catalog, three users, and a sample week on first run.

```bash
pip install -r requirements-dev.txt   # test + audit tools (not needed in production)
python -m pytest -q                 # current backend regression suite (SQLite)
# the same suite against Postgres: a throwaway local database, one schema per test
SMARTPLATE_TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/smartplate_test python -m pytest -q
```

## What's in the box

| Layer | Modules |
|---|---|
| **Kernel** (domain-agnostic, the reusable skeleton from §2) | `scheduler`, `budget`, `optimizer` (MILP/CBC), `variance` (substitution), `explainability`, `agent_brain` (deterministic vs optional LLM) |
| **Domain** (the 17 features as constraints/signals) | `allergens`, `nutrition`, `household`, `leftovers`, `festivals`, `weather`, `surge`, `reverse_mode`, `community`, `receipts`, `health`, `carbon`, `cooking_coach`, `sentiment` |
| **Everyday flows** | `everyday` (onboarding, shortlist, pick, swap, confirm, rate, heads-up), `domain/profile` (goals → targets), `domain/taste` (usual places, ratings), `domain/timing` (order-by, hand-off) |
| **Integrations** | `swiggy_connect` (sign-in + tool discovery), `swiggy_live` (addresses, menus, cart; never orders), `swiggy_mcp` (simulated ordering), `webpush` (RFC 8291/8292), `calendar_sync` (.ics), Open-Meteo weather (`domain/weather`) |
| **Accounts and reminders** | `access` (profile keys), `accounts` (sign-in, devices), `vault` (token encryption), `ratelimit`, `push` (order-time push worker), `domain/reminders` |

### The 17 gap features (all integrated)

**§5.1 (v1 gaps):** ① allergy/medical hard-exclusions · ② calendar integration ·
③ idempotency keys · ④ explainability log · ⑤ pause/snooze/cooked.

**§5.2 (v1.1 adds):** ⑥ nutrition layer · ⑦ household/group mode ·
⑧ leftover awareness · ⑨ festival calendar · ⑩ weather adaptation · ⑪ surge prediction.

**§5.3 (bigger bets):** ⑫ reverse mode (cook days) · ⑬ community templates ·
⑭ receipt/expense surface · ⑮ health/longevity · ⑯ carbon scoring · ⑰ cooking coach.

The **17 features** tab in the UI lists each one with the file that implements it.

## How the agent thinks

Each weekly session becomes a choice between delivery candidates, a cook option,
and skip. **Hard constraints** (allergens, medical limits, diet, rating floor,
fasting window, calendar/festival suspension) are filtered out of the candidate
set, so the solver *cannot* violate them — even under Tight-Week (Survival) mode.
**Soft constraints** (taste, nutrition, health, carbon, surge, weather/festival
bias) are weighted terms in the objective. Budget is a hard cap; skip is the
always-feasible relief valve. Every decision emits plain-language reasons.

At execution the plan is placed against the (simulated) Swiggy MCP with
idempotency keys and a compensating saga; a menu-load failure (§1.2) triggers
substitution to the next-best option **above** your rating floor, never below.
The UI now retrieves a server-authored checkout preview and binds confirmation to
its fingerprint and maximum total, so a plan changed after review is rejected
instead of silently ordered.

## Configuration

| Env var | Default | Meaning |
|---|---|---|
| `PORT` | `5057` | HTTP port |
| `SMARTPLATE_BRAIN` | `deterministic` | `llm` opts into the paid narrator (off the critical path) |
| `SMARTPLATE_SWIGGY` | `simulated` | `live` enables MCP sign-in after provider approval; current Render origin is rejected by Swiggy |
| `SMARTPLATE_LIVE_ORDERS` | `off` | `on` permits explicitly approved Cash on Delivery placement after durable storage and provider validation |
| `DATABASE_URL` | unset | Postgres connection URL (e.g. Neon, see “Keep your data”). Set: all data lives in Postgres and survives restarts; TLS is required unless the URL says otherwise. Unset: SQLite at `SMARTPLATE_DB` |
| `SMARTPLATE_PG_POOL_MAX` | `8` | Most Postgres connections the app holds (one gunicorn worker × 8 threads) |
| `SMARTPLATE_PG_SCHEMA` | `public` | Postgres schema to use |
| `SMARTPLATE_DB` | `smartplate.db` | SQLite path (used when `DATABASE_URL` is unset) |
| `SMARTPLATE_WEATHER` | `live` | `live` = Open-Meteo forecast (cached, falls back to the sample feed offline); `simulated` = sample feed only |
| `SMARTPLATE_SOLVER_GAP` | `0.001` | Relative optimality gap for the weekly MILP |
| `SMARTPLATE_SOLVER_TIME_LIMIT` | `10` | Seconds per solve before returning the best plan found |
| `SMARTPLATE_SWIGGY_MCP` | `https://mcp.swiggy.com` | Swiggy MCP base for sign-in + read-only tool discovery |
| `SMARTPLATE_PUBLIC_URL` | derived | Public base URL for the Swiggy OAuth redirect. Required for Swiggy sign-in on any host other than localhost (Render sets `RENDER_EXTERNAL_URL`, which is used automatically) |
| `SMARTPLATE_ALLOWED_HOSTS` | empty | Extra comma-separated hosts allowed in the Swiggy OAuth redirect besides `PUBLIC_URL`'s host and localhost. `Host`/`X-Forwarded-Host` headers alone are never trusted for it |
| `SMARTPLATE_SWIGGY_MAX_CLIENTS` | 3 | Maximum Swiggy OAuth clients the server registers dynamically (one per redirect URI) |
| `SMARTPLATE_TZ` | `Asia/Kolkata` | Timezone for meal times, "today" and past meals (servers often run in UTC) |
| `SMARTPLATE_STABILITY` | `0.3` | Bonus for keeping a meal's current pick on re-plans (0 disables) |
| `SMARTPLATE_SECRET` | generated | Key for encrypting stored Swiggy tokens. Set it in production (render.yaml generates one); without it a key is kept in the database |
| `SMARTPLATE_PUSH` | `on` | `off` stops the push-reminder worker |
| `SMARTPLATE_VAPID_PRIVATE` | generated | Web Push signing key (base64url P-256). Keep it stable or browsers must re-subscribe |
| `SMARTPLATE_PUSH_CONTACT` | `mailto:smartplate@example.invalid` | Contact the push services see; use a real address in production |
| `SMARTPLATE_BEHIND_PROXY` | `1` on Render | Trust one proxy hop's `X-Forwarded-*` headers (rate limits, redirect URLs) |
| `SMARTPLATE_EPICURE_DIR` | `data/epicure` | Where `scripts/fetch_epicure.py` puts the Epicure files and the app reads them |
| `SMARTPLATE_EPICURE_CHECKSUMS` | `pinned` | `pinned` checks every Epicure file against the SHA-256 sums in `domain/epicure.py`; a path to a `SHA256SUMS` file is for tests |

## Live integration status

Without a Swiggy sign-in, ordering is a **hand-off**: *Order on Swiggy* opens Swiggy's
public search for that restaurant and dish, the person orders there, then taps *I had
it* to record the planned meal estimate. This does not verify the actual purchase,
charged amount or nutrition of the food received.

With a sign-in (Me → Swiggy), Ziggy reads saved addresses,
searches real restaurants, shows menus, maintains favourites and puts an exact
reviewed dish in the cart. It can place a user-confirmed Cash on Delivery order
only when `SMARTPLATE_LIVE_ORDERS=on`; otherwise checkout remains in Swiggy.
These flows have automated API and real Chromium desktop/phone walkthrough tests
against an isolated fake server. The real sign-in last checked on 30 September failed
at Swiggy's allowlist page, so the first authenticated menu and cart calls remain
to be tested after provider approval.
The simulated auto-ordering path (idempotent, spend-limited) remains under More. No
background worker places orders. See [the integration notes](docs/vendor/swiggy/README.md).

See [implementation status](docs/implementation-status.md) for the exact pilot
scope, tested code and remaining recommendations. Private profiles now support
credential-free data export, explicit deletion and recovery-code/device revocation
in Me → Account & devices. Real restaurant menus also support paginated dish search.
