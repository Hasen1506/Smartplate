# Swiggy integration findings — 7 September 2026

The documentation-opening blocker is resolved. HTML pages work through web retrieval; their `.md` twins also worked through direct HTTPS retrieval. Do not guess private APIs or copy the recipe unchanged.

## Authentication and transport

[Authentication](https://mcp.swiggy.com/builders/docs/start/authenticate/) documents OAuth 2.1 with PKCE S256, dynamic client registration at `/auth/register`, authorization at `/auth/authorize`, token exchange at `/auth/token`, and discovery at `/.well-known/oauth-authorization-server` and `/.well-known/oauth-protected-resource`. Scope `mcp:tools` authorizes tools; resource/prompt scopes are separate. Phone/OTP collection belongs to Swiggy's authorization UI. The page says access tokens last five days and refresh issuance is not wired despite advertised metadata. Reauthorize on expiration or revocation. Redirects must match registration; HTTPS is required except localhost development. The page's DCR and manual redirect-allowlisting guidance needs validation against the registered client.

[Framework guide](https://mcp.swiggy.com/builders/docs/start/developer/build-an-agent/) specifies Streamable HTTP at `https://mcp.swiggy.com/food`, with Bearer authorization. It does not pin the negotiated MCP protocol version. Discover it with `initialize`, then obtain every actual `inputSchema` through authenticated `tools/list`. Do not label a client-offered version as the server's verified version. No authenticated discovery was performed in this work.

## Concrete contract mismatch

| Contract | Recipe example | Detailed tool reference |
|---|---|---|
| Cart update | `items` | Required `cartItems`, `restaurantId`, `addressId` |
| Placement | `paymentMethod: COD`, no address | Required `addressId`; payment method must match the returned available options |

Sources: [recipe](https://mcp.swiggy.com/builders/docs/build/recipes/order-food/), [cart update](https://mcp.swiggy.com/builders/docs/reference/food/update_food_cart/), [placement](https://mcp.swiggy.com/builders/docs/reference/food/place_food_order/).

The cart reference supports variants or variantsV2 (not both), variant-dependent addons, and `restaurantName` for display. Call `get_food_cart` after a mutation. Use provider identifiers exactly as returned; sample local SQLite IDs are not Swiggy IDs. Cart pricing includes `to_pay`, delivery and tax components; use the returned payable amount instead of a planner estimate. Coupon presence without a positive discount does not establish applied savings.

## Real checkout requirements

The placement reference requires confirmation of the cart, full selected delivery address and selected available payment method. `PENDING_PAYMENT` is not success: UPI requires payment status success and then `confirm_order`. Food cancellation is directed to Swiggy support, not a cancellation tool. The documented reference does not establish a SmartPlate Swiggy Money integration.

The recipe describes a ₹1,000 Builders Club cart cap, a single-restaurant cart, and tracking at intervals of at least ten seconds. It explicitly says placement is not idempotent. After an ambiguous failure, inspect food order history before considering a retry. The sample app's simulated cart → payment → placement sequence is not the real Swiggy payment sequence.

[Production guidance](https://mcp.swiggy.com/builders/docs/build/ship-to-production/) requires user-visible cart confirmation and check-before-retry handling for non-idempotent placement, and describes production onboarding. The reviewed sources do not explicitly permit SmartPlate to place an entire scheduled week unattended. Do not enable unattended ordering based on an inference from ordinary tool availability.

## Tool inventory and the history limit (update, 25 September 2026)

Public references list 14 Food tools: `get_addresses`, `search_restaurants`,
`get_restaurant_menu` (150-item limit per call), `search_menu`, `update_food_cart`,
`get_food_cart`, `flush_food_cart`, `fetch_food_coupons`, `apply_food_coupon`,
`place_food_order`, `get_food_orders`, `track_food_order`, `get_payment_options`,
`report_error`. `get_food_orders` returns only about 5 recent orders as prose, with no
pagination ([Swiggy/swiggy-mcp-server-manifest#74](https://github.com/Swiggy/swiggy-mcp-server-manifest/issues/74)).
So "usual places" cannot be imported from Swiggy history. SmartPlate collects them at
onboarding and learns from ratings instead. This inventory comes from public docs and
community projects. Authenticated `tools/list` is still the source of truth.

Until OAuth is wired, the app uses a **hand-off**: a link to Swiggy's public web
search for the planned restaurant + dish (`https://www.swiggy.com/search?query=…`).
The person orders in Swiggy and confirms in SmartPlate.

## Sign-in + discovery implementation (gate 1)

`smartplate/integrations/swiggy_connect.py` implements the steps above:
- OAuth metadata discovery.
- Dynamic client registration (public client; one registration per redirect URI).
- A PKCE S256 authorization URL with `state`, `scope=mcp:tools` and `resource`.
- A single-use `state` callback at `/swiggy/callback`.
- Token exchange.
- MCP Streamable HTTP: `initialize` (JSON or SSE) → `notifications/initialized` →
  paginated `tools/list`, carrying `Mcp-Session-Id`.

It stores the negotiated protocol version, server info and every tool's
`inputSchema`, then stops. **No tool is called.** The token stays server-side and
is deleted on disconnect. Behind a proxy (Codespaces), the redirect URI comes from
`X-Forwarded-Proto/Host`, or `SMARTPLATE_PUBLIC_URL` if set. It must match what
Swiggy allows.

Verified only against a strict fake server (tests/test_followups.py). The first
real sign-in will show whether dynamic registration is accepted for the Codespaces
URL or whether the redirect must be allow-listed with Builders Club.

## Menus and cart (gates 2–3, 25 September 2026)

`smartplate/integrations/swiggy_live.py` uses the sign-in for:

| In the app | Tools called | Notes |
|---|---|---|
| More → Swiggy connection → *Choose delivery address* | `get_addresses` | The chosen `addressId` is stored with the connection |
| Places → *Today's Swiggy menu* (usual places) | `search_restaurants`, `get_restaurant_menu` | Best name match ≥ 0.6; up to 150 items; cached 6 hours; non-veg hidden for veg/vegan profiles |
| Today → *Put it in my Swiggy cart* | `search_restaurants`, `get_restaurant_menu`, `update_food_cart`, `get_food_cart` | Planned dish matched by name ≥ 0.55; `cartItems`, `restaurantId`, `addressId` per the reference; shows `to_pay` vs the planned cost |

Every call goes through one function with an allow-list (`get_addresses`,
`search_restaurants`, `get_restaurant_menu`, `search_menu`, `get_food_cart`,
`update_food_cart`, `flush_food_cart`). `place_food_order`, payment options and
coupons are refused in code, and a test checks it. The person opens Swiggy to
review the cart and pay.

Because real schemas are only visible after sign-in, arguments are built from each
tool's discovered `inputSchema`: documented names first, then common spellings. A
required field SmartPlate can't fill is named in the error, not guessed. Replies are
read from `structuredContent` or JSON text content, and records are found by their
fields. The last reply **shape** per tool (field names and types, no values) is saved
in `swiggy_connections.samples`, so the first real run shows what to adjust.

Unverified assumptions, to check on the first real sign-in:
- **Prices:** menu prices of 1000 or more are treated as paise (Swiggy's web data uses
  paise). The cart's `to_pay` is authoritative.
- **Cart items:** the item id key is taken from the `cartItems` item schema
  (`menu_item_id` in the reference). Dishes that require a variant or add-on will fail
  with Swiggy's message.
- **Search:** `search_restaurants` is expected to take a free-text query plus
  `addressId`.

## What remains before real ordering

1. Run the first real sign-in on a hosted HTTPS URL. Confirm dynamic registration or
   allow-list the redirect with Builders Club.
2. Read `samples` and adjust argument and reply mapping where the real shapes differ.
3. Variants and add-ons in the cart; coupons only if they show a real saving.
4. Keep pending/unknown payment outcomes separate from success, and never retry
   placement without checking `get_food_orders`, if placement is ever enabled.
5. Verify support/refund behaviour and any agreement governing unattended scheduling
   before enabling it.

No real cart, address, payment or order was touched while building this.
