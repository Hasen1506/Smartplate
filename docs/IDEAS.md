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
