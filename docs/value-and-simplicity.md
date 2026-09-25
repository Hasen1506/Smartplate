# Is SmartPlate worth building, and how do we make it simple?

> Decision memo, 25 September 2026. It answers "connector or app?", "will anyone
> pay?", and "what makes it valuable?" Then it records what this branch built in
> response. Supersedes nothing: read it alongside
> [the September strategy memo](product-strategy-2026.md).

## 1. The short answer

**Yes, but only as a simple planning app, not as a connector.**

"Order food through an AI chat" is already free and already taken. Swiggy's
Builders Club exposes a 14-tool Food MCP server (addresses, restaurant and menu
search, cart, coupons, payment options, place, track, order history). Zomato
publishes its own MCP server with search, menu, cart, checkout and QR payment.
Both plug straight into ChatGPT and Claude. A SmartPlate connector would compete
with the platforms' own free connectors. It would also reach only people who
already pay for an AI chat subscription and are willing to set up an MCP server.
That audience is tiny, and "pay us too" is a hard sell to it.

What neither the delivery apps nor a chat connector does is **hold your week
together over time**:

| Need | Swiggy / Zomato app | AI chat + connector | SmartPlate |
|---|---|---|---|
| Find a dish | Endless feed, promoted listings | Good, if you type well | 3–4 usual places × 3 dishes, already filtered |
| Stay inside a weekly budget | No concept of a week | Forgets between chats | Hard weekly cap (and optional daily cap), prorated, re-balanced after every change |
| Never see an allergen | Filters are per-search and easy to miss | Depends on the prompt | Hard exclusion; even your own pick can't override it |
| Know *when* to order | ETA after you order | No | Order-by time from arrival, ETA, rush hour and rain |
| Plan around weather and holidays | Surge surprises you | No | Rain and holidays shift orders earlier and are priced in |
| Remember what you liked | Order history is a list | ~5 orders via `get_food_orders` | 👍/👎 and usual places shape every plan |
| Cook sometimes | Never suggested (against their interest) | Rarely | Cook days, recipes and one grocery list |

The moat is not ordering. It's **state + constraints + timing**: a week that
stays correct as life changes, in one tap.

## 2. Who would pay, and what for

Honest read on willingness to pay: consumers rarely pay subscriptions for
"food planning". The price test in the strategy memo (₹149–249/month, needing
₹600–1,000/month of visible value) still stands. The value SmartPlate can
*show* is:

1. **Money kept.** Surge avoided by ordering earlier (shown per meal and per
   week), budget never exceeded, cook days swapped in when the week runs tight.
2. **Decisions removed.** One meal and one button, not a 20-minute scroll.
3. **Safety.** Allergens and medical limits that can't slip through, which
   matters most to people who'd otherwise not order at all.

Where money more plausibly comes from, in order of likelihood:

| Channel | Why it fits | Risk |
|---|---|---|
| **B2B2C meal allowances** (employers, co-living, hostels/PGs, corporate cafeterias) | The payer already has a per-head food budget and wants it spent well; SmartPlate's weekly cap + allergy safety is exactly the product | Sales cycle |
| **Household plan** (₹299–499/month) | Shared rules, several members, one budget — real coordination pain | Needs retention proof |
| **Approved referral / partner programme** with Swiggy/Instamart | Monetises execution without pay-to-rank | Must never change ranking |
| **Dietitian add-on** | High willingness to pay | Regulated, operational cost |

**Recommendation:** keep the individual app free while proving retention. Run a
6-week pilot with 30–50 people, in line with the strategy memo's gates. Pitch the
B2B allowance version in parallel. Only charge individuals once week-4 retention
and verified savings exist.

## 3. Simplicity principles this branch applies

1. **Five answers to a plan.** Diet and allergies → meals and cooking → usual
   places → budget (suggested from *those* places) → optional goal. No weights,
   sliders or forms up front.
2. **Only your usual places.** The shortlist is capped: 4 places × 3 dishes,
   plus 3 "something new". Everything shown already fits your rules. Prices
   include delivery and expected surge. Hidden items are counted, not dumped
   ("4 not safe for your allergies").
3. **One meal, one button.** Today shows the next meal, when to order it, and
   *Order on Swiggy* / *Change* / *I had it*.
4. **Direct manipulation.** Drag a meal onto another day, or tap *Move*, to
   swap. The solver re-balances money and nutrition, and a **stability bonus**
   keeps every other meal as it was unless changing it genuinely helps.
5. **Proactive, not noisy.** At most six heads-ups: rain and storms, heat,
   holidays, your fasts, meals that didn't fit the budget, past meals to
   confirm, protein running low (only when you set a goal).
6. **Honest by default.** Past meals are *unknown* until you confirm them.
   Fasts apply only if you say you keep them, and are never inferred.
   Nutrition is labelled a general-wellness estimate. The weather source is
   shown.

## 4. What's real in this branch

| Area | Status |
|---|---|
| Onboarding → real profile → plan | ✅ `POST /api/profiles`; Mifflin–St Jeor targets from optional body stats, with safety floors |
| Usual places | ✅ Favourites drive the candidate pool; the weekly count of new places is capped by the variety setting |
| Shortlist / pick / swap / confirm / rate | ✅ `options`, `choose` (pins survive re-plans; unsafe picks refused), `swap`, `confirm` (spend + intake + expense), `rate` (👎 excludes the dish for 45 days) |
| Budgets | ✅ Weekly hard cap, prorated for partial weeks; optional per-day cap; spent meals subtracted |
| When to order | ✅ Order-by time per delivery (arrival − ETA − buffer, +15 min in rain) |
| Weather | ✅ Live Open-Meteo 16-day forecast, cached, with an offline fallback to the sample feed. Free for non-commercial use; production needs their paid plan |
| Holidays | ✅ Indian calendar Sep 2026 → Mar 2027 (Gandhi Jayanti, Navratri, Dussehra, Karva Chauth, Diwali, Bhai Dooj, Guru Nanak Jayanti, Christmas, NYE, Pongal, Republic Day, Ramadan, Eid al-Fitr, Holi); lunar dates flagged; holiday dinners carry a surge bump |
| Swiggy | 🟡 **Hand-off** by default (Swiggy's public search). With a sign-in: addresses, live menus and cart filling, never ordering or paying. Not yet run against the live service |
| Restaurants and prices | 🟡 Sample Chennai catalogue (12 real chain names, illustrative prices) |
| Solver | ✅ 0.1% optimality gap + 10 s cap. A tight vegan week that took CBC 143 s now solves in 0.3 s with the same plan |

## 5. What is *not* done, and the order to do it

**Done since this memo was first written (follow-up PR):**
- **Reminders**: order-by times (and "start cooking" 45 minutes before cook meals)
  as a calendar file with alarms, plus opt-in browser alerts while the app is open.
- **Installable**: web manifest, icons and a service worker. It adds to the home
  screen, opens offline with a friendly screen, and never caches plans or budgets.
- **Profile privacy**: onboarding profiles get a secret key (hash stored); every
  endpoint for that profile needs it; a recovery code opens it on another device.
  This is not full accounts: there's no password reset and no sync.
- **Swiggy gate 1, sign-in + read-only discovery**: OAuth 2.1 + PKCE + dynamic
  registration, MCP `initialize` + `tools/list` (JSON or SSE, session id,
  pagination), tools classified read/write. Nothing is called beyond listing.
  Tested against a strict fake server. **Not yet run against the real
  mcp.swiggy.com**, which was unreachable from the build environment. The first
  real sign-in is the verification step.

**Done in the second follow-up:**
- **Hosting**: one-click Render blueprint (`render.yaml`), gunicorn entry point,
  health check.
- **Accounts**: optional name + password per private profile, per-device sign-in
  and sign-out, rate limits. Swiggy tokens are encrypted at rest.
- **Push reminders**: Web Push at each order-by time, even with the app closed.
  Encryption is checked against RFC 8291's worked example.
- **Swiggy menus and cart**: addresses, live menus for usual places, and *Put it in
  my Swiggy cart* with the real amount to pay. Ordering and payment tools are
  refused in code. This is tested against a fake server, not yet the real one.

**Still to do, in order:**

1. **First real Swiggy sign-in** on the hosted URL, then adjust the argument and
   reply mapping from the recorded shapes (docs/vendor/swiggy/README.md).
2. **Real catalogue for planning.** Plan from live menus rather than the sample
   Chennai catalogue once replies are verified (prices, veg marks, availability).
3. **Durable hosting.** A paid instance with a disk (or Postgres) so profiles
   survive restarts, and an always-on worker so push reminders fire on time.
4. **Account recovery by email or phone** before a public launch, plus a privacy
   policy and data export/delete.
5. **Unattended ordering.** Only if Swiggy's terms explicitly allow it. The
   spend-limited, fingerprinted consent flow is already built for that day.

**A connector can come later as distribution**: expose SmartPlate's own planner
as an MCP server ("what's for dinner within my budget?"), once the app has users
whose state makes the answer valuable.

## Sources

- Swiggy Builders Club announcement (Apr 2026): https://www.swiggy.com/corporate/press-release/swiggy-to-launch-builders-club-giving-developers-and-enterprises-access-to-its-ai-commerce-stack/
- Swiggy Food MCP reference (tool list): https://mcp.swiggy.com/builders/docs/reference/food/
- `get_food_orders` history cap (~5 orders, prose): https://github.com/Swiggy/swiggy-mcp-server-manifest/issues/74
- Community Swiggy MCP projects showing `search_restaurants` / `get_restaurant_menu` / `update_food_cart`: https://github.com/rayanakarthikeyan/swiggy-syndicate-mcp
- Zomato MCP server: https://github.com/Zomato/mcp-server-manifest · https://www.analyticsvidhya.com/blog/2025/11/zomato-mcp-server/
- Open-Meteo (free, key-less, CC BY 4.0, non-commercial): https://open-meteo.com/ · https://open-meteo.com/en/docs
- India holidays 2026 (Dussehra 20 Oct, Diwali 8 Nov, Guru Nanak Jayanti 24 Nov): https://www.bankbazaar.com/indian-holiday-calendar.html · https://calendarific.com/holidays/2026/IN
- Navratri 11–20 Oct 2026, Karva Chauth 29 Oct, Bhai Dooj 11 Nov: https://panchang.org/october-2026-hindu-festivals/ · https://www.indianeagle.com/traveldiary/sharad-navratri-2026-dates-puja-rituals-fasting-colours/
- 2027: Ramadan from ~10 Feb, Eid al-Fitr ~10 Mar, Holi 23 Mar: https://calendarific.com/holidays/2027/IN · https://www.calendarlabs.com/holidays/india/2027
- Mifflin–St Jeor equation and activity factors: see `docs/optimization-and-ux.md §3`
