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
