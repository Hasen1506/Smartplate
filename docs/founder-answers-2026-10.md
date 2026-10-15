# Founder answers, October 2026

Short, decisive answers to every question in Vijay's response to the 72-critique review
(numbering follows his notes; "p1" is the opening paragraph). Each answer has its sources
alongside. All web facts were checked on 14 Oct 2026. Where something could not be confirmed,
the answer says so.

Simulation evidence is cited as **[sim]**. It comes from `tests/scenarios/` in this branch:
seven persona weeks run against the stand-in Swiggy town. Results are in the appendix.

---

## p1 · How should the app decide what you eat? Favourites/order history or autoplan?

**Answer: favourites first. The solver runs only when the user asks it to.** Opening the app
should never produce a plan. The order of truth is:

1. **Seed from Swiggy order history.** `get_food_orders` returns up to 15 recent orders across
   *all* saved addresses. Each order carries `restaurantId`, `restaurantName`, `orderedItems`,
   `orderTotal` and `orderedTime`. Group these by meal slot (time of day) and by address. The
   first screen then becomes "These are your usual places for lunch and dinner. Tick the ones you
   like." Users who don't connect type in favourites instead (your #14).
2. **Apply the user's rules** (diet, allergies, min rating, max price, max ETA) as filters.
   Anything a rule hides stays visible but greyed out, with the reason. This matches your sketch.
3. **Today = quick picks from the favourite pools** that fit today's money (see #3).
4. **"Plan my week" is a button with a mode chooser**, never a default:
   *Favourites only* · *Favourites + cook* · *Favourites + new places ≥ my min rating* ·
   *Surprise me (within my rules)*. The default mode is "Favourites only".

**How to present choices:** show one answer per meal (1× Cappuccino, 1× Burrito ▾). The ▾ opens
at most three alternatives from the same pool, then "other options". Rule-breakers are greyed
with the reason. This is exactly your sketch, and it collapses the five overlapping swap entry
points (grill #30) into one.

**Typing a dish to cook:** it belongs under the **Cook** side of the Order | Cook toggle (your
sketch), not a separate cooking-coach screen. "Cook it" leads to the ingredient list. An
Instamart basket would be the natural next step: Instamart is part of the same Swiggy MCP
programme (19–20 tools, including `your_go_to_items`).

*Sources:* get_food_orders schema
https://mcp.swiggy.com/builders/docs/reference/food/get_food_orders.md · Swiggy MCP servers and
tool counts https://mcp.swiggy.com/builders/docs/reference/index.md · full tool list
https://mcp.swiggy.com/builders/llms.txt

## p1b / 10 / 11 / 15 · Can Ziggy aggregate Swiggy + Toing + Instamart + Zomato and compare prices?

**Answer: no for Zomato and Toing today. Yes for the Swiggy family (Food + Instamart + Dineout).
Don't build a demand aggregator.**

| Platform | Programmatic access (Oct 2026) | Can Ziggy use it? |
|---|---|---|
| Swiggy Food | Builders Club MCP, 19–20 tools, invite-based production access | **Yes**. This is what you have. |
| Swiggy Instamart / Dineout | Same programme and OAuth | **Yes**. Good for "Cook it", which goes to an Instamart cart. |
| Toing (Swiggy's budget app) | Not one of the 4 Builders Club servers (Food, Instamart, Dineout, Scenes). No public API found | **No**. Only user-typed prices. |
| Zomato | Official MCP exists, but its README says *"We are not allowing any third party apps to be built on top of Zomato MCP right now… (for personal use only)"* | **No**, not for a product. |

Swiggy's own ground rules also forbid exactly what a price-comparison aggregator does:
"Building aggregation layers that hide Swiggy's brand or confuse users" and "Using the APIs for
competitive intelligence or benchmarking". Revocation can happen "without notice" for scraping
or bulk catalogue export.

The "Swiggy ₹140 · Toing ₹180" row in your sketch is therefore only safe when the Toing number
is one the **user typed in** (a manual price). Never fetch it. Toing launched in Chennai on
13 Apr 2026 and is in about 50 cities, with no platform or packaging fee. So "is this cheaper on
Toing?" is a fair question for the user to answer themselves. Ziggy can't answer it
automatically.

**What is viable instead:** a cross-app **spend ledger**. Swiggy data comes in via MCP. Zomato
and Toing come in via user-entered or forwarded receipts. Grill #72(b) points the same way, and
it needs no write access.

*Sources:* Zomato MCP README https://github.com/Zomato/mcp-server-manifest · Builders Club ground
rules https://mcp.swiggy.com/builders/access/ · revocation terms
https://mcp.swiggy.com/builders/docs/operate/access.md · 4 servers
https://mcp.swiggy.com/builders/docs · Toing in Chennai
https://expressnews.asia/2026/04/toing-the-affordable-food-app-now-available-in-chennai/ ·
Toing in 50 cities (Swiggy Q1 FY27 release)
https://www.swiggy.com/corporate/wp-content/uploads/2026/07/Press-Release-signed.pdf

## 2 · Is budget really the pain?

**Answer: unproven. Treat it as the #1 hypothesis to test, not a fact.**

- **Average spend:** Swiggy food delivery did ₹9,490 Cr GOV in Q1 FY27 across 19.2 M monthly
  transacting users. That is about ₹1,650 per user per month (my calculation).
- **Order size:** average order value was ₹700 in Q4 FY26. So the average user orders only about
  2–3 times a month. Heavy orderers (4+ a week, about ₹11k+ a month) are the people for whom a
  budget matters.
- **Price-sensitive demand is real:** 2 of 3 new Toing users were new or dormant category users.
  That demand exists, but Swiggy is serving it directly.

**Test:** 15 heavy orderers, 2 weeks, a Wizard-of-Oz WhatsApp "₹X left · 3 picks from your
usuals" (grill #2). The kill criteria are in grill §"3 kill-criteria".

*Sources:* Q1 FY27 https://www.swiggy.com/corporate/wp-content/uploads/2026/07/Press-release_Q1FY27.pdf ·
AOV ₹700 https://www.swiggy.com/corporate/wp-content/uploads/2026/05/Press-release_Q4FY26.pdf ·
Toing new users https://www.swiggy.com/corporate/wp-content/uploads/2026/07/Press-Release-signed.pdf

## 3 · "Today" quick-select rules: qty × price × min rating × item × restaurant × …?

**Answer: three stages, all built on fields Swiggy actually returns.**

1. **Hard filters (never relaxed):**
   - safe for diet and allergies, where unknown counts as unsafe (W1);
   - `availabilityStatus == "OPEN"`;
   - deliverable to the chosen address;
   - `inStock`;
   - no required options (`hasVariants` or `hasAddons` with a minimum) unless the user picks them;
   - fits today's money after fees: item × qty + delivery + ₹17.58 platform fee + GST and
     packaging estimate.
2. **User rules (the user sets each; defaults shown):**
   - min rating 4.0★ (`avgRating` / dish `rating`);
   - max ETA 30 min (`deliveryTimeMinutes`);
   - max distance (`distanceKm`);
   - max price per item and per meal;
   - quantity (portions);
   - veg / egg / non-veg;
   - "only my favourites" vs "favourites + new ≥ min rating";
   - bestsellers only (`isBestseller`);
   - min number of ratings (`totalRatings`, so a 4.8★ from 12 ratings doesn't outrank a 4.3★
     from 10k).
3. **Ranking (explained in one line per pick):**
   - favourite score = times ordered × recency × time-of-day match;
   - then money headroom left after the pick;
   - then rating, then ETA, then distance.

Show 1 pick per meal, 3 alternatives behind ▾, and greyed rule-breakers with "why". Swiggy's own
guidance says to "only recommend OPEN" places and to "always mention distance for far
restaurants". Swiggy's menus carry **no nutrition or allergen fields**, which is why
safety has to come from W1's lexicon and fail-closed rules.

**[sim]** The stand-in town shows why rule 1 matters. A vegan peanut-allergic diabetic could
choose **Kozhi Varuval (chicken), Verkadalai Sundal (peanut), Kaju Kari and Rasmalai** as
dinner today (4 xfails, fixed by W1).

*Sources:* search_restaurants fields (distanceKm, deliveryTimeMinutes, availabilityStatus, offer)
https://mcp.swiggy.com/builders/docs/reference/food/search_restaurants.md · search_menu fields
(no nutrition/allergen) https://mcp.swiggy.com/builders/docs/reference/food/search_menu.md ·
platform fee ₹17.58
https://economictimes.indiatimes.com/tech/technology/swiggy-increases-platform-fee-to-rs-17-58-from-rs-14-99-days-after-zomatos-hike/articleshow/129769000.cms

## 6 · Is measuring real savings even possible?

**Answer: yes, roughly, for Swiggy orders only, with three honest limits.**

**How:**
- Baseline = the user's actual spend before Ziggy: `get_food_orders` (up to 15 orders, with
  `orderTotal` and `orderedTime`) plus `get_food_order_details` (itemised pricing).
- Measured = the same pull 4 weeks later.
- Report: "Weeks 1–4: ₹X. Before Ziggy: ₹Y."

**Limits:**
1. 15 orders is about 3–4 weeks for a heavy orderer, so pull and store a snapshot weekly.
2. Zomato and Toing orders are invisible unless the user adds them.
3. It's a before/after comparison, not proof that Ziggy caused the change. Say "you spent", never
   "Ziggy saved you".

Using Swiggy-originated data for analytics needs **separate, explicit consent** and a DPA.
Showing users their own spend is the "immediate task", so it is allowed. Aggregating across
users for metrics is analytics, so it needs that consent.

*Sources:* get_food_orders
https://mcp.swiggy.com/builders/docs/reference/food/get_food_orders.md · data and compliance
https://mcp.swiggy.com/builders/docs/operate/data-and-compliance.md

## 9 · Nutrition: what is needed to make it functional?

**Answer: keep it opt-in and Week-view only. Making it honest takes five things:**

1. **Dish → recipe match with a confidence score.** The kept recipe dataset does this. Below a
   threshold, show "unknown", never 0. W1 and #37 cover this.
2. **Ingredient values from IFCT 2017** (ICMR-NIN, 528 key foods, free PDF plus open data
   packages), instead of name templates.
3. **Targets from ICMR-NIN's Dietary Guidelines for Indians 2024**, replacing the Western
   2000 kcal / 25-40-35 defaults (grill #42).
4. **Show ranges and an "estimate" label.** Where a chain with 10+ outlets publishes kcal under
   FSSAI labelling regulation 9, use the published value instead.
5. **Never use it as a medical filter.** Diabetes and hypertension stay hard "unknown = excluded"
   rules (grill #41).

Swiggy's menu tools return no kcal or allergen data, so there is no shortcut. The use you
describe is fine and scoped: "planning a week → see the combo against daily needs".

*Sources:* IFCT 2017 https://www.nin.res.in/ebooks/IFCT2017.pdf ; open data
https://github.com/ifct2017/ifct2017 · DGI 2024
https://www.nin.res.in/dietaryguidelines/pdfjs/locale/DGI_2024.pdf · Swiggy menu fields
https://mcp.swiggy.com/builders/docs/reference/food/search_menu.md

## 18 · How are users actually saving money?

**Answer: four mechanisms, in this order of size.**

1. **Fewer, fuller orders.** Every order pays a fixed ₹17.58 platform fee (Swiggy One members
   too) plus delivery. Merging two small orders saves about ₹20–60 for free.
2. **Cheaper picks among their own favourites.** For example, Masala Dosa instead of Ghee Roast
   on weekdays. Offer this only when the user asks (your #27).
3. **Cook nights** from the Cook toggle.
4. **Knowing the number.** In field data, budgets reduce spending even when people don't stick
   to them, and the effect lasts about 6 months (Lukas & Howard, *JCR* 2023).

"Surge avoided" is **not** a saving (grill #6). **[sim]** The app still claims
"Time-shifts saved ≈ ₹247 against surge pricing" for some personas (xfail, fixed by W4).

*Sources:* platform fee applies to One members
https://www.financialexpress.com/business/industry-swiggy-raises-platform-fee-to-rs-17-58-per-order-matches-zomatos-hike-4182811/ ·
Lukas & Howard, "The Influence of Budgets on Consumer Spending", JCR 49(5)
https://academic.oup.com/jcr/article-abstract/49/5/697/6603733

## 20 · What does "co-branding under an enterprise context" mean?

**Answer:** every Builders Club integration, developer or enterprise, **must** show Swiggy's
square logo plus the exact phrase "Powered by Swiggy". It goes next to results, the cart and the
order confirmation, and a footer alone is not enough. "Co-branding" is something more: a
custom joint presentation (for example "Ziggy × Swiggy") or co-branding assets. Swiggy agrees
that **per partner**, and only enterprise partners get those assets.

Without an enterprise deal you may not "imply endorsement or partnership", and you must avoid
"official app" or "certified" claims. Practically, Ziggy needs the "Powered by Swiggy" badge on
the Today card, the pools and the cart now.

*Sources:* brand guidelines (9 Oct 2026)
https://mcp.swiggy.com/builders/docs/operate/brand-guidelines.md · support / co-branding
https://mcp.swiggy.com/builders/docs/operate/support.md · enterprise co-branding assets
https://mcp.swiggy.com/builders/docs/operate/access.md

## 21 · Analytics: build or integrate?

**Answer: build the small part, integrate the rest.**

- **Build** one `events(user_id, name, ts, props)` table with five events (W9 owns this). It is
  about 50 lines, it lives in your Postgres, and it stays inside the Swiggy data rules.
- **Integrate PostHog Cloud** for funnels, cohorts and retention charts. It is free up to
  1 M events a month, which is far beyond a pilot.
- **Integrate Sentry** for errors (free Developer plan: 1 user, 5k errors).
- **Rule:** send only Ziggy-originated events (opened, tapped, set budget) to PostHog. Never
  send Swiggy order contents without explicit analytics consent.

*Sources:* PostHog https://posthog.com/product-analytics/pricing · Sentry
https://sentry.io/pricing/ · Swiggy analytics consent
https://mcp.swiggy.com/builders/docs/operate/data-and-compliance.md

## 22 · Is ordering via WhatsApp possible, not just reminders?

**Answer: technically yes, but do reminders first.** One developer publicly showed a WhatsApp
bot ordering on Swiggy via the MCP and WhatsApp Cloud API (Aug 2026), so the pieces exist.

For Ziggy it means:
- Swiggy production approval for that surface.
- "Powered by Swiggy" attribution in text, since text-only channels must still carry it.
- Meta's per-message fees. Since 1 Oct 2026 in India: utility ₹0.115 and marketing ₹0.8631 per
  delivered message. Service replies are free for the first 1,000 per number per month, then
  ₹0.115.

A daily "₹X left · 3 picks" utility message costs about ₹3.5 per user per month before GST.
**Order of work:**
1. Reminders with a deep link into Ziggy's Today.
2. "Reply 1/2/3 to put it in your Swiggy cart".
3. Payment in chat, using Swiggy's UPI intent link (#48).

*Sources:* WhatsApp rates
https://www.makinforu.com/insights/whatsapp-api-pricing-india ;
https://leapstream.in/blog/whatsapp-business-api-pricing-india · WhatsApp + Swiggy MCP demo
https://www.linkedin.com/posts/kanishk-singh-728126211_feedme-bot-activity-7499019548864430080-P0HA ·
text-channel attribution https://mcp.swiggy.com/builders/docs/operate/brand-guidelines.md

## 27 · How do we set reduction targets that aren't dysfunctional?

**Answer: base the target on real spend, keep it moderately hard, and let it bend.**

The research says:
- Moderately challenging sub-goals beat both easy and extreme ones. In a 2026 *JMR* study of an
  investing app, users with moderate weekly targets stayed active a year later; extreme targets
  drove drop-off.
- Missing a strict goal triggers the "what-the-hell" effect: people abandon the goal entirely.
- A small **emergency reserve** (slack you can use at a small cost) keeps people going after a
  slip (Sharif & Shu).
- Optimistic budgets still reduce spending (Lukas & Howard).

**Rule set for Ziggy:**
1. **Baseline** = the median of the user's last 3–4 weeks of real spend, from order history or
   the ledger. Never use sample prices (#27 / grill #27).
2. **Default target** = baseline × 0.85. Offer 0.80 and 0.90, and never go below 0.70 in one step.
3. **Floor**: target ≥ (meals they said they order × the cheapest safe favourite, including
   fees). If the floor is above the target, say so plainly ("₹300 covers about 3 meals from your
   places") and offer cook nights. Never skip meals silently.
4. **Weekly sub-goal plus a ₹ reserve** (about 10%). Going over uses the reserve, not a red
   "failed".
5. **Step-downs only after a week inside the target.** Re-base monthly.
6. **"Suggest cheaper"** stays a button. Externals appear only if the user chose
   "+ new places ≥ min rating".

**[sim]** A ₹300 week plans 3 of 14 meals. A second ₹265 pin takes the week to ₹530, with
`budget.over=true` but **no warning shown** (xfail, fixed by W4).

*Sources:* JMR 2026 (UTS press release)
https://www.eurekalert.org/news-releases/1119528 · what-the-hell effect and budget accuracy
https://finhealthnetwork.org/wp-content/uploads/2023/12/BE-Guide-Tools-to-Manage-Spending-2023.pdf ·
emergency reserves (Sharif & Shu)
https://www.anderson.ucla.edu/documents/sites/faculty/review%20publications/research/Sharif-Shu_EmergencyReserveFailure_OBHDP2019.pdf ·
Lukas & Howard https://academic.oup.com/jcr/article-abstract/49/5/697/6603733

## 47 · Carts above ₹1,000: hand off or split?

**Answer: hand off. Don't split one restaurant's order to get under the cap.** The ₹1,000 cap is
a documented Builders Club v1 limit. Splitting to dodge it looks like "bypassing… platform
safeguards", which Swiggy lists as not allowed. Above ₹1,000, show "Open in Swiggy". Then ask
builders@swiggy.in for a higher cap once there is a household or B2B use case.

*Sources:* order-food recipe (₹1000 cap)
https://mcp.swiggy.com/builders/docs/build/recipes/order-food.md · ground rules
https://mcp.swiggy.com/builders/access/

## 48 · Can users pay inside Ziggy?

**Answer: yes, through Swiggy's checkout. Never through Ziggy's own payment account.**

- Since July 2026 the Swiggy MCP has an in-chat **UPI payment stage**:
  `get_payment_options` → `place_food_order` with a UPI app intent or a QR code →
  `PENDING_PAYMENT` → `confirm_order`. Swiggy Money was added on 30 Sep 2026.
- The money goes to Swiggy. "MCP itself doesn't touch card data; order flows resolve to Swiggy's
  own checkout."
- Collecting money yourself for restaurants makes you a payment aggregator. Under RBI's 2025
  directions that needs ₹15 Cr net worth to apply. Not an option.
- The code's COD-only path is outdated (grill #48).

*Sources:* UPI in MCP https://mcp.swiggy.com/builders/blog/2026-07-10-mcp-payments-upi.md ·
Swiggy Money https://mcp.swiggy.com/builders/llms.txt · Swiggy checkout handles payment
https://mcp.swiggy.com/builders/docs/operate/data-and-compliance.md · RBI PA directions
https://www.rbi.org.in/Scripts/BS_ViewMasDirections.aspx?id=12896

## 49 · Smartplate's domain is taken. Is "Ziggy" a problem?

**Answer: yes, rename before any public launch.** Indian courts judge trademark similarity
phonetically, from the view of a buyer with "imperfect recollection". The Delhi High Court is
especially strict for food: in the 2025 Domino's cases it said the threshold for deceptive
similarity is lower for food products. It ordered Swiggy and Zomato themselves to delist
look-alike names.

"Ziggy" for a Swiggy-ordering app, in Swiggy-like colours, invites a complaint from the platform
whose approval you need. Swiggy's brand rules also say "keep your product's identity distinct".

Pick a name that doesn't rhyme with Swiggy or Zomato. Check it on the IP India public search
(classes 9, 35, 39, 42, 43). I did not run that search. Fine for private testing now.

*Sources:* LiveLaw on Domino's / food threshold
https://www.livelaw.in/high-court/delhi-high-court/threshold-of-deceptive-similarity-is-lower-for-food-products-delhi-hc-directs-swiggy-zomato-to-delist-restaurants-infringing-dominos-trademark-295682 ·
phonetic test (Cadila; Zegna v Jenya)
https://www.khuranaandkhurana.com/2021/09/21/how-much-do-phonetic-similarities-matter-in-trademark-disputes/ ·
brand guidelines https://mcp.swiggy.com/builders/docs/operate/brand-guidelines.md

## 53 / 54 · What is the algorithm's goal, and is this the best option?

**Answer: the goal is lexicographic, not a weighted soup.** In priority order:

1. **Safe:** never violate diet, allergy or medical rules (unknown counts as unsafe).
2. **Inside today's or this week's money**, fees included.
3. **Favourites first:** the user's own places and dishes. Change only what they allowed.
4. **Fill the meals they said they order** (no silent skips). A cheaper favourite beats a skip,
   and if nothing fits, say why.
5. **Cheapest** among equals, then variety, then rating.

Each pick gets one sentence of reason. Money is the constraint and the user's choice is the
objective. Grill #54 asked for exactly this. A greedy pass with these rules is enough for about
14–21 slots. Keep the MILP only if W6's benchmark shows it's needed.

**[sim]** Two gaps show today:
- A household of 4 with one vegan member and ₹6,000 gets 12 of 14 meals skipped "to stay within
  budget / rating floor" while ₹5,640 sits unspent. The cause is rule 1 against menus without
  ingredient data, and it is blamed on the wrong thing (xfail, fixed by W6).
- The student week skips 6 of 14 meals. The skip *message* is honest, but skipping instead of
  going cheaper is the policy question in #27.

## 55 · Will paid Render fix latency and accuracy?

**Answer: paid fixes the wait, not the accuracy.**

- **Render:** a Free web service spins down after 15 minutes with no traffic and takes about a
  minute to wake. Paid instances never spin down. The smallest paid instance is $7 a month
  (0.5 CPU, 512 MB). `render.production.yaml` already names `0.5c-512mb`.
- **Neon:** Free compute suspends after 5 minutes but resumes "within a few hundred
  milliseconds", so it isn't the big delay.
- **Accuracy** comes from real prices and fees (W4) and from precomputing today's picks on a
  schedule (W9's cron). CPU doesn't help: plans already solve in about 1–2 s locally **[sim]**.

**Verdict:** pay the $7 for the pilot weeks.

*Sources:* https://render.com/pricing · https://render.com/docs/free ·
https://neon.com/docs/introduction/plans

## 62 · How should identity actually work?

**Answer: HttpOnly session cookies plus a real sign-in, with the recovery code kept as a backup.**

- **Today:** a long-lived profile key in `localStorage`, stored server-side as SHA-256. OWASP is
  blunt: "Do not store authentication tokens, session IDs… in localStorage". Use
  `__Host-` cookies with `HttpOnly; Secure; SameSite=Strict` instead.
- **Sign-in:** Google sign-in or an email magic link (cheap, gives recovery), with passkeys
  later.
- **Swiggy can't be the identity:** its tools return "no raw phone / email / name".
- **Phone OTP over WhatsApp** is possible (authentication message, ₹0.115) but adds a vendor.

W8 owns the change.

*Sources:* OWASP Session Management
https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html · Swiggy no-PII
https://mcp.swiggy.com/builders/docs/operate/data-and-compliance.md

## 64 · The encryption key: what is it, and what should you do?

**Answer:** `SMARTPLATE_SECRET` is the key that encrypts stored Swiggy OAuth tokens
(`vault.py`, Fernet: AES-128-CBC + HMAC-SHA256). If it is unset, the app generates a key and
**stores it in the same database as the tokens it protects**. Anyone with a DB dump can then
decrypt them.

**To do:**
1. In Render → service → Environment, confirm `SMARTPLATE_SECRET` exists. Both render YAMLs
   use `generateValue: true`, so a Blueprint deploy creates one.
2. Never change it casually: old tokens won't decrypt, and users must reconnect Swiggy.
3. Make production refuse to start without it (W8).
4. For rotation later, use `MultiFernet` with the new key first.

*Sources:* repo `smartplate/vault.py`, `render.yaml`, `render.production.yaml` · Fernet docs
https://cryptography.io/en/latest/fernet/

## 66 · After 5 minutes idle, does saving something lose data?

**Answer: no data is lost, but the first save can be slow.**

- Neon suspends compute after 5 minutes of inactivity, but your data stays in storage. The next
  query wakes it in a few hundred ms.
- What *is* lost on a Render Free spin-down is the **local filesystem**, so nothing durable may
  live there. Postgres is used in production.
- The real risk is the about 1-minute cold start of the web service. A save typed during that
  time waits, and the request may time out.

**Fix:** paid Render (#55), plus the client keeps unsent edits and retries (W3/W9).

*Sources:* https://neon.com/docs/introduction/scale-to-zero · https://render.com/docs/free

## 70 · What does "tonight's 3 dinners" mean?

**Answer:** it is grill #3's name for the core screen. In the evening, show the money left this
week plus three dinner options that fit it, so one tap opens the Swiggy cart. Grill #70 asked
that this shortlist be **cached for offline use** so it survives a flaky network. Your sketch's
Today card (budget + 1× pick ▾ + other options) **is** that screen, with picks from your pools
instead of from the solver.

## 72 · Pivots, how to structure modules, and a "how are we feeling" mood mode

**Pivots.** Run (b), the cross-app spend ledger, together with (a), the WhatsApp/Telegram
"₹X left · 3 picks" message, as a 2-week test. They share one backend and need no write access.
Skip the tiffin aggregator (e). B2B for PGs (c) comes after retention is proven.

**Modules** (keep the code, but put non-wedge modules behind flags):

```
core/        money (ledger, fees, targets) · safety (lexicon, rules) · favourites (pools, order history)
today/       quick picks, rules, Today API
plan/        week modes (favourites / +cook / +new / surprise), solver
providers/   swiggy_food, swiggy_instamart (Cook it), manual_receipts  ← adapter seam for any future platform
channels/    pwa, push, whatsapp (later)
labs/        nutrition, carbon, festivals, sentiment, epicure, community… (flags, off by default)
```

**Mood mode ("how are we feeling?"):** fine as an **optional chip** on Today ("comfort ·
light · celebrate · quick"). It re-ranks picks *within* your pools and rules. It never infers
mood, and never uses cameras or biometrics. Research shows mood shifts what people choose
(positive mood favours healthier, long-term choices; Gardner et al., *JCP* 2014). But
emotion-aware recommenders are still mostly research prototypes. Ship it as a labs flag after
the money loop works.

**Desktop:** your sketch's side-by-side Breakfast / Lunch / Dinner pools is right for ≥1024 px
(W5).

*Sources:* Gardner et al. 2014
https://www.sciencedirect.com/science/article/abs/pii/S1057740814000060 · affective recommender
survey https://arxiv.org/abs/2508.20289

## Pricing: ₹149 / ₹299 plans, and the recommended monetisation

`docs/product-strategy-2026.md` proposes Plus ₹149–249 a month and Household ₹299–499 a month.

**Answer: don't charge consumers yet.**
- ₹149 is about 9% of the average user's ₹1,650 a month of Swiggy food spend.
- Freemium conversion in Indian health and food apps runs around 0.5–1% (grill #12).
- **Recommended:**
  1. **Free** consumer product through the pilot.
  2. Then **one** paid tier at ₹99 a month (or ₹249 a quarter), only for heavy orderers. Sell it
     on a measured outcome ("your last 4 weeks: ₹X under your target").
  3. In parallel, try a **B2B pilot**: a PG or hostel weekly plan, or an employer meal-allowance
     policy layer.
- **Rules:** no affiliate or commission revenue from Swiggy unless contracted. Never take a
  "share of savings" you can't measure (#6).

---

## Decisions only the founder can make (prioritised)

1. **Segment:** salaried singles (₹2–5k a week), or students/PGs? Name 10 real people in it this
   week.
2. **Allergy and medical profiles in the beta:** exclude them until W1 ships and you've reviewed
   it, or allow them? (Today the stand-in town lets a peanut-allergic vegan pick peanut and
   chicken dishes.)
3. **New name** before any public link. Then buy the domain and register the exact Swiggy
   redirect URI on it (grill #49).
4. **Pay $7 a month for Render** during the pilot? (Recommended: yes.)
5. **Default target rule:** 15% below real spend, with a 10% reserve and floors? Or a different
   number?
6. **Skip vs cheaper:** when favourites don't fit the money, should Ziggy go cheaper *within
   favourites* automatically, or only when asked? (Your #27 says only when asked; the
   simulations show 6 of 14 meals skipped for a ₹1,500 student.)
7. **Consumer pricing:** stay free through the pilot, with B2B pilot outreach in parallel. Yes
   or no?
8. **WhatsApp:** start the Wizard-of-Oz reminder test now (manual, about ₹0.12 a message), before
   more UI?
9. **Recipe dataset licence:** keep it (your decision) with credit, *and* email Archana's Kitchen
   for written permission. Who sends it?
10. **Analytics consent:** ask users for separate "help improve Ziggy" consent so Swiggy-derived
    events can be measured. Otherwise metrics use only Ziggy-originated events.

---

## Appendix: scenario simulation results (`tests/scenarios/`)

Run with `python -m pytest tests/scenarios -q`. Result: **11 passed, 15 xfailed** in about 30 s on an idle machine (about 2.5 min with 10 agents sharing the CPU)
(frozen clock, no network, deterministic).

**Passing invariants**

- Seven persona weeks:
  - student ₹1,500 veg;
  - salaried single ₹3,500 non-veg;
  - vegan + diabetic + peanut allergy at Velachery;
  - household of 4 with mixed diets;
  - ₹300 week;
  - first open on Sunday noon;
  - peri-urban Minjur.
- What every week passes:
  - no ground-truth-unsafe dish is ever *planned*;
  - planned spend ≤ budget, the user's own pins aside;
  - the `over` and `remaining` budget numbers tell the truth;
  - carts are refused up front for profiles Swiggy menus can't verify;
  - when a cart is filled, the bill lines add up to `to_pay`;
  - no order is ever placed;
  - the recap counts unconfirmed meals as unknown.
- Multi-address: Minjur sees 2 places and OMR sees 6; out-of-area places can't be pooled; each
  address keeps its own favourites.
- Critique #31's "switch 5 plans": balanced, survival, comfort, try-different and a higher
  budget all keep the rules, and more money never plans fewer meals.

**Known gaps (strict xfail; each flips to "unexpected pass" when the fixing branch merges)**

| Owner | Gap |
|---|---|
| W1 | Unmarked Tamil non-veg (Kozhi Varuval, Eral Thokku, Nandu Masala) accepted as a vegan's dinner |
| W1 | Peanut dishes (Verkadalai Sundal, Satay Veg Bowl) accepted and pooled for a peanut allergy |
| W1 | Rasmalai (no sugar estimate) pooled for a diabetic |
| W1 | Kaju Kari (dairy/cashew) accepted for a vegan |
| W2 | Sunday noon: Today has nothing for today |
| W2 | Tomorrow's meal can be put in the Swiggy cart at Sunday noon |
| W2 | "I had it" accepted at 08:00 for a lunch that hasn't happened (new finding) |
| W2 | On evenings whose dinner was skipped, Today shows tomorrow |
| W4 | Planned cost = price + ₹35 delivery; platform fee, GST and packaging missing |
| W4 | "Time-shifts saved ≈ ₹247 against surge pricing" claimed |
| W4 | "Your usual week costs about ₹X" shown to a profile one minute old |
| W4 | A pin that breaks a ₹300 week raises no warning |
| W6 | Household with a vegan member: 12/14 meals skipped and blamed on the budget, with ₹5,640 left (new finding) |
| W7 | After switching to Minjur, the week still lists a place that doesn't deliver there (new finding) |
| W7 | The out-of-area error shows raw provider JSON and a wrong "clear the cart" fix (new finding) |

Also seen in the sample catalogue (fixture data, not live): "Filter Coffee + Vada" is tagged
gluten-only, with no dairy, and is planned for a household with a vegan member.
