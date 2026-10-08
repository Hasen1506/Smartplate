# Ideas (recorded, not implemented)

Ideas met while working on the roadmap PRs. Each is a candidate, not a commitment; none of
them is built. Grouped by the PR that surfaced it.

## From PR A (Tight Week decisions, Swiggy 409)

- **No-needless-skip rule for every mode.** Tight Week now plans lexicographically (fewest
  skips, then fewest extra home-cooks, then the best week). Balanced and Comfort still use a
  flat skip penalty (3 / 5), so a low budget there can still skip meals the money covers.
  Apply the same rule everywhere, or suggest Tight Week when a Balanced plan would skip.
- **Charge skips their nutrition shortfall in every mode** (Tight Week only today, to keep the
  other modes' plans unchanged in this PR).
- **Show "₹X left" next to each budget skip in the week grid** and a one-tap "cook it instead"
  that pins the cheapest safe recipe.
- **Extra home-cook ceiling.** A never-cook user on a very small budget can now get many
  Dal + rice days; let them set "at most N extra cook days in a Tight Week" or prefer a skip.
- **Variety for budget cooks.** Extra cook days repeat the cheapest recipe; rotate the safe
  recipes when their costs are within a few rupees.
- **One Connect banner for all live Swiggy surfaces.** The error bar now offers Connect on a
  409; the live-cart and checkout panels still show their own inline error text.

## From PR B (live menus into the planner)

- **Learn nutrition from check-ins** ("ate half", "still hungry") so a live dish's estimate
  narrows over time; show a confidence band instead of one number.
- **A curated dish-nutrition table** (IFCT 2017 values per canonical dish) in a config file,
  reviewed by a nutritionist, replacing the regex templates in `domain/live_catalog.py`.
- **Ask the restaurant about allergens**: live dishes for allergy profiles are filtered by
  name only. Add the "will ask" order instruction to every live pick for an allergy profile,
  and keep the existing rule that ordering stays a review, not a cart.
- **Refresh live menus on a schedule** (e.g. Sunday evening before the weekly plan) and on
  address change, instead of only on tap.
- **Per-locality cache** of live menus shared across users at the same address area, to cut
  MCP calls.
- **Real delivery fee per restaurant** learned from previous cart bills (the planner uses a
  ₹35 estimate until the cart is checked).

## From PR C (order any meal and the whole week)

- **Scheduled placement** the day Swiggy offers a scheduled-order (or pre-order) tool:
  `week_orders.SCHEDULING` is the single switch to flip.
- **Group a day's meals from one restaurant into one cart** (lunch + dinner from the same
  place) to pay one delivery fee; today each meal is its own cart.
- **Learn real fees per restaurant and slot** from checked bills, and feed them back to the
  planner's cost so the next week's estimate matches what is paid.
- **Push "cart ready" at each order-by time** for queued meals (web push exists), with one
  tap opening the queue row.
- **Budget guard at cart check**: if the checked total pushes the week over budget, offer to
  re-plan the remaining meals before approving.

## From PR D (learning loop)

- **Decay**: reason taps never fade today (bounded at 3 taps each). Fade a "late" after a few
  on-time deliveries from the same place, like the 45-day dislike window.
- **"Late" from data, not taps**: compare Swiggy's delivered time (order status) with the
  promised ETA and learn reliability without asking.
- **Spice as a profile trait**: three "too spicy" taps across different dishes could set a
  "mild" preference that tilts every curry, not just the tapped dishes (needs spice tags
  on live dishes).
- **Portion learning per restaurant**: "small" on two dishes from one place probably means
  the place serves small portions; scale its other dishes too, with the user's consent.
- **Show the learned effect in the plan** ("planned less: Chennai Mess — you said late twice")
  as a reason line on the decision, not only as a chip.

## From PR F (onboarding rhythm and editable planner settings)

- **Rhythm by weekday** ("I cook dinner on weekends, order on weekdays"; "office cafeteria
  Monday to Thursday"): today the rhythm is one setting per meal for every day.
- **Re-normalise the calorie split over the meals actually planned** (a dinner-only user
  still gets dinner's 35% share as the target); offer it as the default for new users after
  a golden-reviewed change.
- **Snacks as a rhythm option** (planned, not only logged).
- **Learn the variety cap from swaps**: users who keep swapping back to the same dish want a
  higher cap; suggest it instead of making them find the setting.
- **Home-cooked meal without a recipe**: when no suggested recipe fits the user's rules, let
  them log what they actually cooked so nutrition still adds up.

## From PR G (Epicure ingredient embeddings)

- **Coconut for tree-nut allergies, by choice.** Coconut is treated as a tree nut (the
  conservative reading); US FDA guidance no longer lists it as one. Let a tree-nut-allergic
  user say "coconut is fine for me" instead of losing every coconut swap.
- **Plan the "safe with a swap" recipes.** They are shown in Cooking & groceries but the
  planner still excludes them as written; a recipe variant with the swap applied could be a
  planned cook option.
- **Real stock and prices for swaps** once an Instamart tool exists: mark lines out of stock
  automatically and price the replacement instead of keeping the original's estimate.
- **Grow the dish lexicon from live menus**: log dish names with no recognised words (no
  flavour vector today) and review them, so "more like this" works on more real dishes.
- **Fade "more like this"** after a few weeks, or when the user swaps away from the similar
  dishes it brought in; today the last five taps stay until removed in Settings.
- **Learn "more like this" from 👍** on dishes with a flavour vector, with the user's consent,
  instead of only from the explicit tap.
- **Cuisine lean per meal** ("Indian lunches, East Asian dinners") and a lean that applies to
  cook-day recipes once there are more than four.
- **Explain similarity in words.** Epicure's factor modes (factor_poles.npy) are loaded and
  checked but not shown: their machine-written labels ("East-Asian roots…" for curd rice)
  read wrongly on Indian dishes. A small hand-written label set could make "similar because…"
  lines trustworthy.
- **Southeast Asian lean** when the pole has more than one mode behind it (today it is a single
  coconut-dessert mode, so it is not offered).
- **"Use it up" suggestions**: leftover paneer or half a pack of dal → recipes and dishes
  nearest to it.

## From PR E (household, weekly recap, grocery list from cook meals)

- **Invite a real profile into the household** with a one-time code, so a partner keeps their
  own phone and history; today the owner adds people without profiles, and only seeded
  sample profiles share a household as full profiles.
- **Portions per person**: a child eats half a serving. The grocery list and the split could
  weigh each person, not just count heads.
- **Relax a rule when its person isn't eating**: shared meals always meet everyone's rules
  (one kitchen, cross-contact). A "Dev is away this week" switch could plan without Dev's
  rules, with a clear warning.
- **Per-person nutrition targets** for the household recap (today the recap uses the owner's
  targets and the owner's intake log).
- **Settle up**: turn "who owes what" into a UPI request or a running balance across weeks.
- **Sunday recap push**: one push notification with the week's recap and next week's
  plan, using the existing web push.
- **Pantry memory**: remember "have it" across weeks for staples (rice, oil, spices) until the
  user says they ran out.
- **Order the grocery list** on Instamart when a cart tool exists, with the same review step
  as food orders.

## From the no-fabricated-data fix (live prices without invented surge, no invented community members)

- **Sample plans still use the demo surge model.** Live Swiggy dishes are now priced exactly
  as Swiggy lists them. The labelled sample catalogue still shows "Surge ×1.25 priced in" and
  "time-shifted … saved ₹X" from seeded multipliers. Drop surge from sample plans too, or mark
  those lines "(sample)".
- **A real peak-time signal**, if one ever exists: compare checked cart totals by slot
  (lunch rush versus 15:00) from the user's own bills before claiming any time-shift saving.
- **Sample weather fallback** is labelled in Insights and in the week's footer line, but the
  weather icon on each day carries no "(sample)" mark when the live forecast is unavailable.
  Mark the icon too, or hide it.

## Proposals 1 and 2, approved and being built (real bill corrects the plan; one Connect path)

Approved by the user on Oct 8, 2026, and built in this PR. The source ideas above stay listed
where they were first met: PR C "Budget guard at cart check" and "Learn real fees per
restaurant", PR B "Real delivery fee per restaurant", and PR A "One Connect banner for all live
Swiggy surfaces".

- **The real Swiggy bill corrects the plan.** When a cart check returns Swiggy's bill, the
  meal's real total replaces its planned cost in the week, and the week is compared with its
  budget before approval. If the week goes over, the user sees by how much and chooses
  "Approve anyway" or "Re-plan the remaining meals". Placing never happens silently over
  budget. The re-plan is the normal planner run: it keeps every meal that is ordered, pinned
  or already in a checked cart (at its real total) and re-plans the open meals within the
  money left, with the same allergy, diet and nutrition-target rules as every plan.
- **Learned delivery fee per restaurant.** The "Delivery" line of a real cart bill is stored
  for that restaurant and delivery address and replaces the flat ₹35 estimate for it. A live
  dish says "delivery ₹X estimated" until a real bill has been seen, then "delivery ₹X from
  your bill". A bill with no delivery line teaches nothing (never guessed).
- **One "Connect Swiggy" path.** Every live surface (error bar, live cart, order list, live
  menus, plan source line) shows the same message and the same Connect button when Swiggy is
  not connected or the sign-in expired, never a Retry. The action the user started (order a
  meal, check a week's cart, open a live menu, plan from live menus) resumes once after
  connecting and choosing an address.

Ideas met while building these (not built):

- **Learn the other fees too.** Platform fee, packaging and GST also differ from the plan.
  They depend on the dish and the cart size, so they need more than one bill per restaurant
  before the planner can use them.
- **Fee by meal slot.** Swiggy's delivery fee can change with time of day and demand. Keep
  the fee per restaurant and slot once a user has bills for more than one slot.
- **Fee age.** A fee learned months ago may be stale. Show its date and fall back to
  "estimated" after a few weeks without a new bill.

## Delivery address: one chosen address for cart, planning and live menus (Oct 8, 2026)

Asked for by the user on Oct 8, 2026 ("do whatever is needed for accuracy"): their current
address was missing from SmartPlate, there was no way to add or refresh it, and "Refresh
Swiggy cart" kept saying the cart was for a different delivery address.

Where addresses come from: only Swiggy's saved addresses, read with `get_addresses` through
the Swiggy connection. SmartPlate stores one chosen `addressId` on the connection and passes
it to every menu, cart and checkout call.

- **Address not listed? Add it in Swiggy, then Refresh addresses.** Swiggy's public Food
  reference now lists `create_address` and `delete_address`, but SmartPlate's code and its
  recorded Swiggy replies have never exercised them, so SmartPlate does not create addresses
  yet. The address list says where to add one and has a Refresh button that reads Swiggy
  fresh (no 60-second cache). The chosen address is marked "SmartPlate delivers here".
- **A stale default never survives a refresh.** If the chosen address is no longer in Swiggy,
  SmartPlate forgets it (and its cached menus, cart intent and live catalogue) and asks the
  user to choose again. If Swiggy changed its text, the label updates.
- **The cart follows the chosen address.** An empty Swiggy cart that Swiggy last tied to
  another address is still empty: adding an item sends the chosen `addressId`, and the bill
  is accepted only when Swiggy echoes that address back. A cart with items for another
  address is shown with the address it is for and a "Deliver there instead" choice, never as
  a bare error.
- **Planning follows the chosen address.** Changing the address drops the live catalogue that
  was read for the old one, so the planner never plans from another area's menus or prices.
  The plan's source line names the address its live menus were read for.

Ideas met while building this (not built):

- **Add a delivery address inside SmartPlate** with Swiggy's `create_address`, once a real
  sign-in has recorded its `inputSchema` and reply (the reference needs map coordinates;
  SmartPlate must not guess them).
- **Say which profile is gone.** When the trial server erases its database, live actions on a
  still-open profile answer a bare "not found". Say the profile no longer exists on this
  server and how to recreate it.

## Live finding: Swiggy's cart echoes an address id outside the address list (Oct 8, 2026)

On the deployed app, with the user's Minjur address chosen, "Add to Swiggy cart" put Veg
Biryani (Minjur Bhavan) in the cart. Swiggy's own checkout showed it at Minjur with the real
bill (Item total ₹160, Delivery fee ₹6 for 0.3 km, GST and other charges ₹19.06, To pay
₹185). But `get_food_cart` echoed an `addressId` that is none of the ids `get_addresses`
returns, so SmartPlate refused the bill as "for an address that isn't in your Swiggy list".
That is very likely the same cause as the earlier "cart for a different delivery address"
error.

- **Built now:** only an echo that is one of the user's *other* listed addresses means the
  cart is for another address. An echo outside the list proves nothing, so the bill is read
  for the chosen address (Swiggy prices delivery from the `addressId` passed to
  `get_food_cart`) and its delivery fee is learned. The cart stays unverified, so placement
  still refuses it. SmartPlate keeps which kind of echo it last saw (no values) beside the
  reply shapes.
- **Idea, not built:** ask Swiggy Builders Club how the cart's `addressId` relates to the
  `get_addresses` ids, so a verified cart can be placed once live orders are approved.

## Live finding: the bill was never learned because the cart check was stricter than Swiggy's cart (Oct 8, 2026)

After #32, "Add to Swiggy cart" again put Veg Biryani in the Minjur cart. Swiggy's checkout
showed ₹160 + delivery ₹6 (0.3 km) + GST and other charges ₹19.06 = ₹185. SmartPlate said
"could not confirm the item in Swiggy's cart", so the ₹6 fee was not learned and the plan
kept "delivery ₹35 estimated".

- **Built now:** one check for "this is exactly the reviewed dish" is used for the add,
  Refresh cart and checkout. Exactly one dish with the reviewed dish id, and quantity 1
  however it is spelled (1, 1.0, "1"). A restaurant id that is present must match. A
  missing restaurant is accepted, because the get_food_cart reference says the cart does not
  always return it and the dish id is restaurant-scoped. When the check fails, the message
  names the part (dish, quantity, restaurant) and says the item may already be in the
  Swiggy cart. Which part failed is kept beside the reply shapes (no values).
- **Idea, not built:** show a "Remove from Swiggy cart" button in SmartPlate
  (`flush_food_cart`), so a test add can be undone without opening Swiggy.

## Durable storage at no cost: Postgres through DATABASE_URL (Oct 8, 2026)

The free Render instance keeps SQLite on a disk that is erased on every spin-down, restart
and redeploy. With `DATABASE_URL` set (a free Neon database), every table now lives in
Postgres, the health checks say `persistent: true` and the erasure banner goes away.
SQLite stays the default for local runs, Codespaces and tests; CI runs the Python suite on both.

Ideas met while building this (not built):

- **Let Neon sleep while nobody has reminders.** The push worker queries the database every
  minute while the Render instance is awake, so Neon's compute stays awake with it. That is
  fine while Render itself sleeps after 15 idle minutes, but an always-on instance would use
  about 180 of Neon's 100 free compute-hours a month. Skip the tick's query when no push
  subscription exists (kept in memory, refreshed on subscribe/unsubscribe).
- **Retire the paid-disk Blueprint** (`render.production.yaml`) once the Neon setup has run
  in production for a while: one storage story is simpler to explain and to test.
