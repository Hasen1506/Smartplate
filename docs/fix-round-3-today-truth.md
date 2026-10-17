# Fix round 3 — "today-truth" (16 Oct 2026)

Issues from the UX scenario audit of 16 Oct 2026 (`work/ux-audit-2026-10-16.md`) and the live audit.

| # | Issue | Status | Where |
|---|---|---|---|
| 1 | Today and Week disagree; reminders follow the Week | fixed | `today.overlay_decisions` (called by `service.plan_view`): today's picks are today's Week cell, money, next-up and reminder. A Today pick unpins an older Week pin; a Week pin of today clears the Today pick; Decide-for-me on the Week skips meals picked on Today. Today's picks carry `when` from the same timing arithmetic. |
| 14 | Fixed meal times; late dinner impossible | fixed | `prefs.meal_times`, `models.meal_minute/meal_window`; order-by, cart window, session times, push meal-close, cook reminders follow it. `POST /today/meal-times`. Today asks once "When do you usually eat dinner?" (late-dinner hint from Swiggy history buckets); Rules → "When I eat". Same calendar day only (latest 23:45). |
| 21 | "Remind me at 19:45" at 10 am | fixed | Button reads "Cart opens 18:15 · remind me"; the reminder fires when the cart opens (at order-by once that has passed); a cart-open push is "on time" until order-by. |
| 22 | Remind me = all push alerts, no fallback | fixed | `POST /today/remind` (one meal). Devices opted in from "Remind me" (push scope `meals`) get only those meals; the Me switch (scope `all`) every meal. Sheet offers "Notify me on this phone" or "Add to my calendar" (`reminders.ics?meal=&date=`), with the iPhone Home Screen note. |
| 24 | Week auto-plans dishes never chosen | fixed | New profiles: `prefs.week_fill = "chosen"` (`config.WEEK_FILL_DEFAULT`). Meals with nothing chosen stay open ("Not chosen yet"), pools are planned from, no fallback dishes or cook days. The Week shows "Open" and its own "Decide for me" (picks become pins). Hero/title/manifest reworded. Existing profiles (no `week_fill`) keep the auto week. |
| 25 | Adding a second favourite is hidden | fixed | "+ Add another …" at the end of the meal select. |
| 27 | Rule-breaking picks without confirmation | fixed | "Pick anyway?" note with a one-tap relax chip ("Relax to ≤45 min" / "Relax to 3.5★+"). |
| 28 | Decide with allergen-only pool: no reason | fixed | Note names the dish and rule; actions Add / Surprise me (Surprise only with live menus). |
| 32 | No "meals I order" setting | fixed | Rules → "Meals I order" (PATCH `/setup` meals); non-today meals behind "Show other meals". |
| 10 | Defaults grey out real favourites | fixed | Any rating / ≤45 min, or learned from the person's own places (pool rating/ETA + history ratings, ≥3 values, medians with headroom). Rules row says "Ziggy defaults" / "Learned from your places"; `rules.source` per field. |
| 11 | No Jain / eggetarian | fixed | Diets `egg`, `jain`. `allergens.JAIN_WORDS` (onion, garlic, potato, root vegetables; pyaz, lehsun, vengayam, poondu, aloo, urulai…). Jain: veg mark + no lexicon word, "ask for Jain preparation" caution and a cart instruction. Eggetarian: egg dishes yes, meat/fish never; a non-veg mark with no egg word is unknown (fail-closed). |
| live | Swap empty without Swiggy says nothing | fixed | "Swiggy isn't connected yet, so Swap can only offer dishes you add yourself" + Connect. |

Known limits: a Week cell overlaid from Today is a read model; the classic Week "Add to Swiggy cart" on that
cell still carts the plan's own dish (v3 sends today's meal taps to Swap, so the v3 path is consistent).
The per-meal reminder is for today's meals.
