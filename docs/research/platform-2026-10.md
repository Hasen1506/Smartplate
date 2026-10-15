# Platform truth, Oct 2026 (W7)

Short, sourced notes behind the W7 changes. Pages were opened on 14 Oct 2026 unless noted.
Founder facts (Oct 2026): **Swiggy is connected and the Render origin is whitelisted.**

## Swiggy status and mode

- Sign-in works from `https://smartplate-xgxd.onrender.com/swiggy/callback`; real carts were
  filled on 8–9 Oct (docs/IDEAS.md). The 30 Sep "Onrender isn't whitelisted yet" rejection is history.
- `/api/meta` now reports one mode, `swiggy_mode` = `demo | live_read | live_cart | live_order`
  (`SMARTPLATE_SWIGGY_MODE`, default `live_cart`), plus `swiggy_can` and a derived
  `swiggy_redirect_approved` (true when the deployment's callback is in
  `SMARTPLATE_SWIGGY_APPROVED_REDIRECTS`). Grill #34, #49.
- Domain (#49): `smartplate.app` is taken, so the Render URI stays the approved one. If a new
  domain is bought, Swiggy must whitelist its exact callback first, then add it to the list.

## Order history (`get_food_orders`), grill #7 / #17

- Returns the most recent food orders **across all saved addresses**, newest first; `addressId`
  is required but only resolves coordinates. `orderCount` default 5, **max 15**. Fields:
  `orderId, restaurantId, restaurantName, restaurantAreaName?, orderTotal, orderStatus,
  orderType, orderedItems (string), orderedTime (string), isActiveOrder, actions[]` with
  `reorderMeta.orderItems[] {menu_item_id|item_id, name, quantity, variants, addons}`.
  **No rating field.** https://mcp.swiggy.com/builders/docs/reference/food/get_food_orders.md
- Swiggy is the data fiduciary; its data may serve only the user's immediate task; analytics
  needs separate consent + DPA. https://mcp.swiggy.com/builders/docs/operate/data-and-compliance.md
- What Ziggy does: on an explicit tap with consent text, read once, keep only restaurant
  id/name, dish name/menu id, time-of-day bucket, count, last date, locally-known rating and a
  score; user ticks what becomes a favourite; one DELETE removes all. Founder #14: learning stays
  on what the user types in, so nothing else is learned from Swiggy data.

## Payments, grill #48

- Since 10 Jul 2026 Swiggy MCP has a shared Payment stage on Food, Instamart and Dineout:
  `get_payment_options` → `place_food_order` with `intentApp` (UPI app) or `generateUPIQR=true`
  → order in `PENDING_PAYMENT` → `check_payment_status` (long-poll, widget-driven) →
  `confirm_order` (Food: `orderId + addressId + lat + lng`) → `PLACED`/`FAILED`. Never ask for a
  VPA. https://mcp.swiggy.com/builders/blog/2026-07-10-mcp-payments-upi.md
- Ziggy today: `live_cart` (default) never places or pays; `live_order` places only a reviewed,
  freshly approved COD order, and (R2, `integrations/swiggy_upi.py`) a UPI order through the
  Payment stage with the same explicit approval of the exact cart fingerprint + total:
  `GET …/swiggy/checkout/preview?payment=upi[&app=]` → `POST …/swiggy/checkout` (PENDING_PAYMENT,
  the person pays in their own UPI app) → `POST …/swiggy/orders/<id>/payment`
  (check_payment_status, then confirm_order). When the Payment-stage tools aren't offered to
  Ziggy, or UPI isn't offered for the cart, or the mode is below live_order, the answer is
  "Pay in Swiggy" with the checkout link. Ziggy never collects money and never asks for a VPA
  (payment-aggregator rules; see W10's founder answers #48). Still open: the payment-picker UI.

## Attribution

- Every integration must pair Swiggy's **square logo** with the exact phrase **"Powered by
  Swiggy"**, from discovery to cart, order and tracking; a footer or tooltip is not enough;
  text ≥14 px, contrast ≥4.5:1; no "official"/endorsement claims.
  https://mcp.swiggy.com/builders/docs/operate/brand-guidelines.md (dated 9 Oct 2026)
- Done: the phrase on live results, the cart review and the order review; `attribution` in the
  suggested-favourites payload and in `/api/meta` → `providers`. **Not done:** the square logo asset (download from the brand page) and
  badges on the menu sheet and order-tracking views.

## Other platforms (provider seam, grill #46)

| Platform | Status in Ziggy | Why |
|---|---|---|
| Swiggy Food | Wired (`providers/swiggy_food.py`) | Builders Club MCP |
| Typed in by you | Wired (`providers/manual.py`, `/typed-favourites`, `/manual-address`) | Works with zero platform API |
| Swiggy Instamart | Stub | Same programme/OAuth, separate server; needs enabling + build |
| Toing | Stub | No public API and not a Builders Club server; only user-typed prices |
| Zomato | Stub | README: "We are not allowing any third party apps to be built on top of Zomato MCP right now"; access "for personal use only". https://github.com/Zomato/mcp-server-manifest |

Swiggy's ground rules also bar aggregation that hides Swiggy's brand and competitive
benchmarking (https://mcp.swiggy.com/builders/access/, per W10's research), so a
"Swiggy ₹140 · Toing ₹180" row may only show a Toing price the user typed, labelled as theirs.
