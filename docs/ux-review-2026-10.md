# Using Ziggy as a real person would: UX review, 9 October 2026

## How this was done

I set up a new profile from scratch in Chromium, at phone size (390 px) and computer size
(1280 px), with production settings (no sample data). The profile: Priya, vegetarian,
orders breakfast, lunch and dinner, ₹1,500 a week. Swiggy was a stand-in that answers in
Swiggy's documented reply shapes: six restaurants near "Home, Adyar", full menus with
categories, veg marks, bestsellers, dishes with options, and one closed restaurant
(`tests/swiggy_town.py`). The real Swiggy and Zomato aren't reachable from the build
machine, so anything that depends on their real behaviour is listed as open.

The steps: onboarding, connecting Swiggy, choosing an address, Today, the Change sheet,
Saved (search, menu), Week, Cooking.

## What Ziggy is for (in one breath)

Swiggy answers "what do you want right now?". Ziggy answers "what am I eating this week,
inside my budget and my rules?". It then makes each meal one tap from a real Swiggy cart,
and says when to order. The person still checks the bill and pays in Swiggy.

What makes it different from opening Swiggy every day:

- **A week, not a moment.** A budget for the week and a daily cap, with cook days mixed in
  when they save money. Swiggy has no idea what you spent yesterday.
- **Rules that never bend.** Allergies, diet and medical rules are filters, not
  preferences. It's honest about what a menu can't prove: no ingredients means no cart
  fill for an allergy.
- **Your own list per meal (new).** Breakfast, lunch and dinner pools, built from real
  menus. Swiggy's "reorder" is per restaurant, not per meal of your week.
- **Households.** Who's eating, one portion each, a fair split.
- **Never fake.** Real menus and prices, Swiggy's own bill, estimates labelled as
  estimates. Nothing is invented when data is missing.

## What I found, and what changed

| # | Pain point (as a user would put it) | Severity | Now |
|---|---|---|---|
| 1 | "It planned **Butter Naan** for lunch and **Medu Vada** for dinner." Sides, breads and drinks were planned as whole meals. | High | **Fixed.** Dishes filed under Swiggy menu sections like Breads, Sides, Beverages or Desserts aren't planned as a meal by themselves, unless you pool them. |
| 2 | "I can't tell it what I actually eat for breakfast." The planner guessed from whole menus (Lemon Rice for breakfast *and* dinner). | High | **New: meal pools.** In Saved → My meals, drag dishes from real menus into Breakfast, Lunch and Dinner. Each meal is then planned only from its pool, and one dish in a pool may repeat every day. Today shows "From your breakfast pool". |
| 3 | "Ziggy picked 4 places I never chose, and Saved says *Nothing saved yet*." | Medium | **Fixed.** My meals lists the places the plan already uses next to your saved ones, labelled. Adding a dish saves its place. |
| 4 | "If I don't like any option, I can't see a whole restaurant's menu from the meal." | High | **New: Any place near you.** From a meal's Change sheet, see every place Swiggy lists for your address. Filter by Swiggy's own cuisine labels, open any full menu and tap **Have for dinner**. The dish is checked on the live menu and the week re-balances. |
| 5 | "My pooled dish has no nutrition, so does it count as zero calories?" | Medium | **Handled honestly.** A pooled dish with no estimate is planned with *no* nutrition figures ("No nutrition estimate for this dish"). It's left out of the totals and counted (`unknown_meals`). It's never planned outside its pool. |
| 6 | Dragging a dish from the menu up to the meal boxes doesn't work on a phone: the boxes are off-screen. | High | **Fixed.** While dragging, a drop bar with the three meals is pinned to the bottom of the screen, and the page scrolls at the edges. B, L and D buttons do the same without dragging, and work with screen readers. |
| 7 | On a computer, dragging selected text instead of moving the dish, and the third meal column ran under the side panel. | Medium | **Fixed.** Pointer capture during the drag, and the meal columns wrap to the space available. |
| 8 | The "Your week is planned" toast covered **Continue with Swiggy**. | Medium | **Fixed.** With a sheet open, toasts show at the top. |
| 9 | Two different "left this week" amounts: ₹651 on Today, ₹776 in the Change sheet. | Low | **Fixed.** The sheet now says "₹776 to spend on this meal" (the week's money minus the other meals). |
| 10 | Week rows said "Not planned · Not planned". | Low | **Fixed.** The second line is the reason, or "This meal's time has passed". |
| 11 | Restaurant results said "Adyar · 4.4★ · 25" with no unit. | Low | **Fixed.** "25 min". |
| 12 | "Your lunchs now come from it." | Low | **Fixed.** |
| 13 | Nothing on Today says the plan is a guess until you set your meals up. | Medium | **New.** A one-time card, "Tell Ziggy what you eat", links to My meals and hides once a meal has a pool. |

## Open: not fixed, and why

- **Home-cook recipes are a fixed list with estimated prices.** The planned cook meals
  (Dal + rice, Veg pulao, Egg curry) are written into the code, priced from typical
  Chennai packs. Real prices need Instamart's own tool (Swiggy offers an Instamart
  MCP), which I couldn't test. The new Recipe library (Wikibooks Cookbook, CC BY-SA)
  brings real recipes to read and shop for. They aren't planned because they have no
  prices or nutrition, and Ziggy won't invent them.
- **Menu prices show "≈ est."** This is deliberate. Swiggy doesn't document whether a
  bare menu price is in rupees or paise, so Ziggy marks inferred units until the cart
  prices the order. On the first real sign-in, `samples` will show the unit, and the
  label can drop for exact fields.
- **"Every place near you" is Swiggy's broad search.** Swiggy's tools need a search word,
  so the list is Swiggy's answer for the broad word Ziggy already uses to start a plan.
  On the real server, check how many places that returns. If it's too few, add a second
  search per cuisine from the results.
- **One cart at a time.** Swiggy holds one cart, for one restaurant. Several restaurants
  means several carts in a row: Week → *Order several meals* already walks through them
  one by one.
- **Real Swiggy behaviour** (reply shapes, rate limits, the closed-restaurant flag)
  still needs the first real sign-in on the hosted server.

## Live test on real Swiggy, 9 October 2026

Signed in to the real Swiggy MCP server (`mcp.swiggy.com`, OAuth, OTP typed on Swiggy's own
page), delivering to a real saved address in Vadapalani, Chennai. ₹2,000 a week, nothing
saved, so Ziggy picked the places itself. Ziggy never placed an order, and nothing went
into the cart without a review.

| # | Found on the real server | Now |
|---|---|---|
| L1 | A real menu came back in **sections** (`categories[].items`, max 10 each, with subcategories and pages). Ziggy read only the biggest list: **22 dishes from 4 places**. | **Fixed.** Every section and page is read (up to 5 pages of 8 sections). The same 4 places give **261 dishes**, each with its section path. Prices in sectioned menus are whole rupees, so they're no longer marked as estimates. |
| L2 | It planned **Sambar**, **Chapati (1 Pc)**, **Idli Podi 150 Gm** and **Dal Panchmel** as whole meals. | **Fixed.** A dish is a side if its section says so (soups, starters, curries, dals, pickles…), if its name is a bare side (sambar, rasam, raita, a roti), or if it is a pack sold by weight. You can still pick a side yourself. |
| L3 | Dosa for lunch, a thali at 8 am. | **Fixed, softly.** Breakfast prefers dishes filed under breakfast sections. Lunch prefers whole plates (Meals, Thali, rice, biryani, bowls). These are preferences, not walls: when a place has few plates, a dosa at lunch still beats no lunch. (CI caught the first version, which was a hard filter and skipped lunches.) |
| L4 | Search results included **sponsored places** ("Name (Ad)") and closed ones. | **Fixed.** Ads are marked, not chosen automatically. Closed places are left out. Nearer places come first, then rating. "Load more places" pages through Swiggy's ~200 results. |
| L5 | ₹2,000 a week on a Friday is ₹857 for the 7 meals left, so the plan is cheap sandwiches. That's honest arithmetic, but changing the budget meant a trip to Settings. | **New: Change budget, in place.** From Today, the Week budget card or the budget heads-up: ±₹250, a typed amount or presets, with "about ₹X a meal for the N meals left". Saving re-plans, and Undo puts the old budget back. Live: ₹3,000 went from 5 orders to 6. |
| L6 | "I don't like these picks" had no answer short of changing each meal. | **New: Try different dishes.** Ziggy's picks for the open meals get a small penalty and the week is re-planned inside the same rules. Your picks, orders and carts stay. Undo brings them back. Live: 5 of 5 meals changed. |
| L7 | Opening Swiggy is a feed to scroll. | **New: Order list.** Every dish from your places and pools as plain rows: dish, place, price. Meal chips, a filter, a quantity, the total with delivery against what's left for the meal, then *Have it for* or *Add N to Swiggy cart*. Live: 261 dishes from 4 places. |
| L8 | Some dishes need options (sizes, add-ons) that Swiggy's tools don't let Ziggy choose. | **Said plainly.** The cart review says "Customize it in Swiggy" and links there. It never guesses an option. |

Still open: Swiggy holds **one cart for one restaurant**, and Ziggy checks the cart by
reading back exactly one dish and quantity. So the order list adds one dish (in any
quantity) per cart. Several dishes in one cart need the checkout guard to verify a
multi-line echo first. The Swiggy token from this test expires on 14 October.

## Recipes: what could be used

- **RecipeNLG** (2.2M recipes) and **Recipe1M**: licensed for non-commercial research and
  education only. Not used.
- **Epicure**: releases ingredient embeddings, not the 4.1M recipes. Already used for
  swaps and "more like this", under CC BY 4.0.
- **Wikibooks Cookbook**: CC BY-SA 4.0, so reuse is allowed with attribution and
  share-alike. Imported by `smartplate/integrations/wikibooks_recipes.py`, run where
  Wikimedia is reachable (or with the *Import Wikibooks recipes* workflow). The snapshot
  goes in `smartplate/data/wikibooks_recipes.json` and is credited in the app and on
  `/credits`.
- **6000+ Indian Food Recipes Dataset** (Kanishka Jain, Mendeley Data, DOI
  10.17632/xsphgmmh7b.1). It is listed as CC BY 4.0, but it was scraped from Archana's
  Kitchen. **Used for facts only:** dish names, ingredient lists, times, servings, cuisine,
  course, diet and the link to the original page. That gives 6,296 dishes after dropping
  rows whose translation failed. The method text is **not** copied by default
  (`--with-steps` is opt-in): whether the authors' own writing may be republished is the
  site owner's call, not the scraper's. Cook along shows the ingredients, timers and a
  link to the method. Credited in the app beside every search.
- **INDoRI** and other Kaggle scrapes: no clear licence. Not used.
