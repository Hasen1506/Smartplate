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
