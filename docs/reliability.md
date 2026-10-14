# Reliability, analytics and observability (W9, 14 Oct 2026)

What changed for grill #21, #22, #66 and #67, how to switch each piece on, and what it
costs. Everything is free unless a line says otherwise.

---

## 1. Reminders that fire while the server sleeps (#22)

**Before:** reminders came only from a thread inside the web process. Render's free
instance sleeps after 15 minutes without traffic, so an evening "order now" nudge fired
only if someone had used the app in the previous quarter-hour. A reminder more than 20
minutes late was dropped without a trace.

**Now:**
- `GET` or `POST /cron/tick` sends every due reminder. It needs the header
  `Authorization: Bearer <SMARTPLATE_CRON_KEY>` (or `X-Cron-Key: <key>`) and answers
  404 while `SMARTPLATE_CRON_KEY` is unset. The in-process thread still runs too. Both
  claim a reminder in `push_sent` before sending it, so it is never sent twice, even
  when the thread and the cron overlap.
- Nothing is dropped silently any more:
  - **on time** (up to 20 min late): sent as before.
  - **late** (more than 20 min late, while the meal's window is still open, for example
    dinner until 21:30): sent with the title "Late reminder: …" and a body that starts
    "Sorry, this is N min late (Ziggy's server was asleep)."
  - **missed** (the meal window has closed): not sent, but recorded as `missed` in
    `push_sent.outcome`. `/admin/metrics` counts reminders by outcome, so a missed
    reminder can be seen instead of vanishing.
- A reminder that fell due before the device subscribed is not that device's reminder.
  Subscribing at 13:30 doesn't immediately fire "late" lunch nudges.
- A tick reads the database only when something can be due: at the next reminder time,
  at least once an hour, at midnight, and right after any change through the API.
  Other ticks answer `{"skipped": "nothing due"}` without touching Neon. This matters
  for Neon's compute budget (§3).

### Set it up (5 minutes, free): cron-job.org
1. On Render, open smartplate → Environment, add `SMARTPLATE_CRON_KEY`, and set it to a
   long random value (for example, the output of `python -c "import secrets;print(secrets.token_urlsafe(32))"`).
2. On https://cron-job.org (free, up to once per minute, custom headers supported),
   create a job:
   - URL: `https://<your-service>.onrender.com/cron/tick`
   - Schedule: every 5 minutes, timezone Asia/Kolkata. See the hours note below.
   - Advanced → Headers: `Authorization: Bearer <the key>`
   - Method: GET. If you pick POST, also send the body `{}` with `Content-Type: application/json`.
3. Press "Test run". The answer should be `{"ok": true, ...}`.

cron-job.org stops waiting after 30 seconds, and a cold start takes 26–35 s (usability
finding 45). The first ping after a sleep may therefore show as a timeout in
cron-job.org's history. It still wakes the instance, whose own thread sends what is due
within a minute, and the next ping 5 minutes later succeeds. A reminder is at most about
10 minutes late this way, which still counts as "on time".

### The hours trade-off (founder decision)
Pinging every 5 minutes also keeps the instance awake. That ends cold starts for the
hours you ping, but it spends Render's free instance hours: **750 per workspace per
month, shared by every free service in that workspace**. When they run out, *all* your
free services are suspended until the 1st
([render.com/docs/free](https://render.com/docs/free)).

| Ping schedule (IST) | Ziggy awake | Hours left for your other free services | Cold starts |
|---|---|---|---|
| 24 h, every 5 min | ~720–744 h/month | ~6–30 h | none |
| **06:00–23:59, every 5 min (recommended if Ziggy shares the workspace)** | ~560 h/month | ~190 h | only for a 00:00–06:00 visitor |
| Reminder windows only (07–10, 11–14, 18–22) | ~310 h/month | ~440 h | yes, outside those hours |
| Render Starter, $7/month | always | all 750 h | none (paid instances never sleep) |

Other schedulers looked at:
- **Render Cron Job:** at least $1/month per job (render.com/pricing). It runs a command, so it would `curl` the endpoint. No advantage over cron-job.org.
- **GitHub Actions `schedule`:** each run is billed at least a minute on a private repo, so every-5-minutes is about 8,600 minutes a month, far over the free minutes, which are already tight until 1 Nov. Scheduled runs can also start late. Not recommended. Nothing was added to CI.

---

## 2. Cold start (#66)

Measured locally in this worktree (2 CPUs, SQLite):
- importing the app: ~1.1 s (numpy ~0.11 s via `domain/epicure.py`, pulp ~0.02 s);
  `create_app()`: ~0.4 s, mostly Flask compiling ~120 routes.
- **A warm boot used to run ~80 SQL statements** (30 `CREATE TABLE IF NOT EXISTS`, 25
  `PRAGMA table_info` checks, cleanups). On Postgres, each one is a network round trip to
  Neon, plus Neon's own wake-up.

Changed:
- **The schema migration is skipped when nothing changed.** A hash of the full schema and
  its migrations (plus the Swiggy tables) is stored in `app_secrets` after a successful
  migration. A boot that finds the same hash skips the migration. Any schema change, or
  any doubt (no table yet, no mark, an error), runs the full migration as before. Tested:
  a warm boot now runs under 30 statements, with no DDL and no `PRAGMA`.
- `/healthz` (Render's health check) never touches the database. This is now tested.
- **Not changed: lazy-loading numpy and pulp.** The first real request (opening a plan)
  needs both, so deferring them only moves ~0.15 s from boot to that request, and it
  would touch files other workstreams own. On Render's 0.1-CPU free instance, imports may
  take several times longer (an estimate; not measured on Render).

What remains is Render's own spin-up, which Render puts at "about one minute"
(render.com/docs/free). Code can't remove it. The only fixes are keeping the instance
awake (§1) or a paid instance. The cold-start screen that looks like the app's own
"You're offline" screen (usability finding 46) is a frontend fix and belongs to W3.

---

## 3. "After 5 min of no use, typing to save something: no storage occurs?" (#66)

**Reproduced** in `tests/test_stale_connection.py`. The test uses a database whose
connections all fail after an idle spell with the error psycopg raises for a connection
the server closed ("SSL connection has been closed unexpectedly"). That is what Neon's
free tier does: it suspends its compute after 5 idle minutes, and that cannot be turned
off ([neon.com/faqs/free-plan-limits-and-quotas](https://neon.com/faqs/free-plan-limits-and-quotas)).
Without the fix, the save answered **503 "database_unavailable"** and the change was
**not stored**. The app only swapped a dead connection when the *first* statement
failed in a way it could recognise. Any other stale failure ended the request.

**Fix** (`smartplate/resilience.py`): when a request ends in that 503 *and committed no
database write*, the pool is replaced and the whole request runs once more, with the
same body. "No write committed" is counted per request (`db.writes_committed()`), so
nothing can be applied twice. Routes that talk to Swiggy or place orders are never
replayed. If the database is really down, the user still gets the 503 after one retry,
never a loop. Tests: the save after idle is stored; a short pause needs no replay; a
committed write is never replayed; Swiggy and checkout paths are never replayed.

Two related changes:
- The background database keep-alive (a ping every 45 s) now stops after 10 minutes with
  no *person* using the app (`SMARTPLATE_PG_KEEPALIVE_IDLE`, default 600 s on Render).
  `/cron/tick`, `/healthz` and `/admin/*` don't count as people. Otherwise the cron
  above would keep Neon awake around the clock. Neon's free plan has 100 CU-hours per
  project per month (about 400 hours at 0.25 CU), and running out suspends the database
  until next month.
- The first request after Neon wakes pays its resume, which Neon puts at a few hundred
  milliseconds ([neon.com/docs/introduction/scale-to-zero](https://neon.com/docs/introduction/scale-to-zero)).

**For W3 (frontend):** if a save still answers 503, the app must keep what the person
typed and offer Retry, instead of re-rendering and losing it. That is the other half of
"no storage occurs".

---

## 4. Observability (#67)

- **Request ids:** every response carries `X-Request-ID`. An incoming `X-Request-ID` (or
  Render's `Rndr-Id`) is kept when it is a plain token. Every 5xx JSON body includes
  `request_id`, and so does every log line written during the request.
- **Logs:** one JSON object per line on Render (`SMARTPLATE_LOG_FORMAT=json` is the
  default there; text locally). Each request writes one line: `route` (the Flask rule,
  so ids aren't in it), `status`, `ms`. Requests slower than `SMARTPLATE_SLOW_MS` (2000)
  and every 5xx are logged at WARNING. Health checks and static files are logged at DEBUG.
- **Outbound calls:** `observability.timed("swiggy", tool=..., session=...)` writes the
  per-call line Swiggy's go-live checklist asks for (tool, ms, status). It is **not yet
  wired into the Swiggy transport**, because W7 owns those files and the swiggy_live
  split was kept purely mechanical. One `with timed(...)` around the MCP call does it.
- **Sentry:** set `SENTRY_DSN`; `sentry-sdk[flask]` ships in `requirements.txt` and `create_app()` initialises it (no request bodies, cookies or headers sent).
  Without a DSN it is a no-op. With a DSN but no package, it logs one warning and
  carries on. It sends no request bodies, cookies or headers (`send_default_pii=False`).
  The package was not added to requirements, because CI runs `pip-audit --strict` on
  every dependency. Add it when you sign up.

---

## 5. Analytics: build or integrate? (#21)

Vijay asked: "I need to build or are these apps which we can just integrate?"

**Built, and kept as the source of truth:** an `events(user_id, name, ts, props)` table
on SQLite and Postgres, with the five events recorded server-side from the API routes
that already mean them:

| Event | Recorded when |
|---|---|
| `app_open` | the app loads a profile's plan (once per 30 min per person) |
| `plan_viewed` | a week plan is opened (once per 10 min per plan) |
| `cart_filled` | a cart is filled (simulated order, Swiggy cart, live cart) |
| `ordered_confirmed` | an order is placed or a meal confirmed |
| `budget_overrun` | a plan view shows spend over budget (once per plan per day) |

`GET /admin/metrics` (header `Authorization: Bearer <SMARTPLATE_ADMIN_KEY>`; 404 while
unset) returns D1/D7/D30 retention by weekly cohort (exact-day and "came back on or
after"), the 30-day funnel, and reminder outcomes. Props hold ids and amounts only.
Events are deleted with the profile and included in its data export.

**Why not only an off-the-shelf tool:** this needed no SDK, no third-party script (the
CSP stays `connect-src 'self'`), no cookie or consent banner, and no data leaving your
database. That last point matters for India's DPDP Act and for what you tell Swiggy.

**PostHog, optional, later:** its cloud free tier gives 1M events a month
([posthog.com/pricing](https://posthog.com/pricing)), more than a pilot will use. Add it
when you want dashboards, funnels and session replay without writing queries, at
roughly 50+ active users. The low-risk way is to forward these same five events
server-side through PostHog's capture API, which keeps the table as the record and
needs no browser script. A browser SDK would mean a CSP change and a consent notice.

---

## 6. WhatsApp: reminders, and ordering? (#22, Vijay: "Reminders via WhatsApp is planned but is ordering via it also possible?")

**Reminders: yes, cheap.** These go out through the WhatsApp Business Platform (Cloud
API directly from Meta, or a provider such as Gupshup or Interakt, which add a platform
fee).
- A reminder is business-initiated, so it must be a pre-approved **utility template**.
  Meta charges per delivered template. The India list rate is **₹0.115 + 18% GST ≈
  ₹0.136** per message (Meta rate cards, 2026; per-message pricing since 1 Jul 2025:
  [developers.facebook.com/documentation/business-messaging/whatsapp/pricing](https://developers.facebook.com/documentation/business-messaging/whatsapp/pricing.md)).
  Since 1 Oct 2026, utility templates sent inside an open 24-hour window are also
  charged, and free-form service replies are free for the first 1,000 per number per
  month, then ₹0.115 each
  ([Business Standard, 1 Oct 2026](https://www.business-standard.com/world-news/whatsapp-business-pricing-changes-from-today-what-indian-firms-should-know-126100100165_1.html);
  [Twilio notice](https://help.twilio.com/articles/53100480177819)).
  Meta's developer page still showed the older "free inside the window" wording when it
  was read for this note.
- **Cost:** 2 reminders a day ≈ 60 a month ≈ **₹8 per user per month**. 100 pilot users ≈ ₹800 a month.
- **Needs:** a Meta Business account (business verification), a phone number not
  already on WhatsApp, an approved template ("Time to order {{meal}}: {{dish}} from
  {{place}}, ₹{{amount}}. Order by {{time}}."), and the user's explicit opt-in.
- **Fit with this work:** WhatsApp would be one more sender behind the same `/cron/tick`.
  The due list, the claim and the late/missed policy are shared.

**Ordering inside WhatsApp: technically possible, but not yet allowed for you to assume.**
- Swiggy's Food MCP exposes the whole flow: addresses, search, menu, cart, coupons,
  `place_food_order` with COD or UPI, and tracking. It has a ₹1,000 cart cap,
  `place_food_order` is not idempotent, and the user must confirm the cart and total
  first ([mcp.swiggy.com/builders/docs/build/recipes/order-food.md](https://mcp.swiggy.com/builders/docs/build/recipes/order-food.md)).
  A Ziggy WhatsApp bot could drive those tools for a user who has connected Swiggy, using
  list or button messages and a UPI intent link for payment.
- **Meta's rules:** since 15 Jan 2026, WhatsApp bars general-purpose AI assistants, but
  task-specific business bots (ordering, reminders, order status) are allowed
  ([WhatsApp Business Solution Terms](https://www.whatsapp.com/legal/business-solution-terms);
  [TechCrunch](https://techcrunch.com/2025/10/18/whatssapp-changes-its-terms-to-bar-general-purpose-chatbots-from-its-platform/)).
  Ziggy's ordering bot would be the second kind.
- **Open question for Swiggy:** whether Builders Club access covers a WhatsApp channel
  acting on a user's Swiggy session. Ask before building anything. Their OAuth redirect
  approval was for the web app.
- **Recommended order:** (1) WhatsApp reminders with an "Order now" button that opens
  Ziggy's ready cart in the browser: cheap, no new Swiggy approval. (2) In-chat
  ordering only after Swiggy confirms the channel.

---

## Environment variables added by W9

| Variable | Default | Purpose |
|---|---|---|
| `SMARTPLATE_CRON_KEY` | unset (off) | Key for `/cron/tick` |
| `SMARTPLATE_ADMIN_KEY` | unset (off) | Key for `/admin/metrics` |
| `SMARTPLATE_LOG_FORMAT` | `json` on Render, else `text` | Log format |
| `SMARTPLATE_SLOW_MS` | 2000 | Slow-request WARNING threshold |
| `SENTRY_DSN`, `SENTRY_TRACES_SAMPLE_RATE` | unset, 0 | Optional Sentry |
| `SMARTPLATE_PG_KEEPALIVE_IDLE` | 600 on Render, else 0 (always ping) | Stop the DB keep-alive after this long without a person using the app |
