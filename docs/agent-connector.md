# Ziggy as a connector for AI assistants: proposal

Status: a proposal, not built. The first release is the Ziggy app, used by people.
This note shows how the same backend could be offered later as a remote MCP server,
so assistants such as Claude or ChatGPT (and any other client that accepts remote MCP
servers with OAuth) could plan and order with Ziggy. Check each directory's current
submission rules before applying; they change.

## Why an assistant would want Ziggy

Swiggy and Zomato already publish MCP servers that can search, fill a cart and order
(see `docs/vendor/swiggy/README.md`). What an assistant can't get from them is the
plan around the order: a weekly budget, nutrition against the person's own targets,
allergy and diet rules that are never overridden, cook-at-home days with a grocery
list, a household split and what the person liked. Ziggy's connector should expose
that layer and leave food ordering to the delivery app's own tools, or Ziggy's
existing cart fill.

## Tools

Every tool acts on the signed-in person's own profile. The annotations follow the
MCP spec (`readOnlyHint`, `destructiveHint`) so clients can ask before writes.

| Tool | Kind | What it does (existing endpoint) |
|---|---|---|
| `get_today` | read | Next meal, why it was picked, order-by time, quick alternatives (`/api/user/<id>/plan` → `next_up`, `/api/session/<id>/options`) |
| `get_week` | read | The week grid, budget envelope, nutrition a day vs targets, heads-up (`/api/plan/<id>`) |
| `get_meal_options` | read | Safe dishes per place, cook options, what was hidden and why (`/api/session/<id>/options`) |
| `get_grocery_list` | read | Lines for the cook meals ahead, scaled to who eats (`plan.coach.basket`) |
| `get_saved` | read | Liked dishes, recent meals, saved places (`/api/user/<id>/saved`) |
| `choose_meal` | write | Pick a dish or recipe for a meal; the week re-balances (`/choose`) |
| `skip_meal` / `eat_now` | write | Skip, or add today's meal now (`/status`, `/api/plan/<id>/eat-now`) |
| `swap_meals` | write | Swap two meals (`/api/plan/<id>/swap`) |
| `rate_meal` | write | Good / Not again plus reasons (`/rate`) |
| `set_budget` | write | Weekly budget and daily cap, then re-plan (`/setup`) |
| `prepare_swiggy_cart` | write | Put the planned dish in the person's Swiggy cart and return Swiggy's real bill. Never pays (`/swiggy-cart`) |

Not offered at first: placing or paying for an order, deleting a profile, or
changing allergies and medical rules. Those stay in the app, where the person sees
everything first.

## Rules carried over (Swiggy's and Ziggy's own)

- The person confirms every cart change. The bill shown is Swiggy's own `to_pay`,
  never an estimate.
- No unattended ordering, and no week ordered in one call. Placing an order is not
  idempotent, so after an unclear failure, check the order history before any retry.
- One restaurant per cart. Respect the Builders Club cart cap and track orders no
  more often than every 10 seconds.
- Swiggy tokens stay on the server, encrypted. The connector gets its own OAuth
  grant and never sees them.
- Allergy and diet rules are filters, not preferences: no tool can override them.
- Estimates stay labelled as estimates (nutrition from dish names, delivery fees
  until a real bill, grocery prices).

## Auth and scopes

OAuth 2.1 with PKCE, the same shape Swiggy uses. Three scopes: `plan:read`,
`plan:write` and `cart:write`. A person can grant read-only access first. Removing a
grant in Me → Account & devices revokes it at once.

## Release steps

1. **Now: the app only.** People use Ziggy directly. Nothing here is exposed.
2. **Read-only connector.** `get_*` tools behind `plan:read`, rate-limited per profile
   like the Swiggy routes are today.
3. **Plan edits.** `choose_meal`, `skip_meal`, `eat_now`, `swap_meals`, `rate_meal`
   and `set_budget` behind `plan:write`.
4. **Cart fill.** `prepare_swiggy_cart` behind `cart:write`, once Swiggy approves the
   production client. Paying stays in Swiggy.
