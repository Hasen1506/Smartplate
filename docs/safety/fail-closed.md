# Fail-closed food safety (W1, Oct 2026)

Answers sp-grill #37–#41 and #60 and the usability review's safety findings 1–6.

## The rule

**Unknown is not safe.** One checker, `smartplate/domain/allergens.py::check(user, item)`, decides
for every path: the planner, the options sheet, "have this for dinner" from a live Swiggy menu,
meal pools, the live-cart review, the dish library and the recipe library. It returns:

| status | meaning | shown? |
|---|---|---|
| `unsafe` | the dish breaks a rule ("Kozhi means chicken (Tamil/Malayalam) (not veg)") | hidden, with the reason |
| `unknown` | Ziggy can't tell (no veg mark; no sugar figure; nothing describes the ingredients) | hidden, with the reason |
| `not_checked` | passes, but the person has an allergy, a medical filter or a vegan diet and Swiggy gives no allergen/nutrition data | shown with "Not checked: … ask the restaurant" |
| `ok` | passes and the person has none of those | shown |

The word "safe" is never used for a passing dish. Every hidden dish carries a reason `code`
(`allergen:peanut`, `not_veg`, `veg_unknown`, `not_vegan`, `vegan_unknown`, `allergens_unknown`,
`diabetes`, `sugar_unknown`, `hypertension`, `salt_unknown`, `celiac`) and plain words.

### What fails closed

- **Diabetes:** no sugar figure (`nutrition_known = 0`, `sugar_g` missing/None) → `sugar_unknown`.
  A sweet word in the name (kesari, rasmalai, payasam, halwa, jamun, sakkarai, …) → `diabetes`,
  whatever the number says. Sweet words are read before the savoury templates, so "Sakkarai
  Pongal" is no longer the 2 g pongal template.
- **Veg / vegan:** no Swiggy veg mark → `veg_unknown` (stored `veg = 0, veg_known = 0`, so code that
  only reads `veg` also excludes it). A meat/fish/egg word in the name beats a veg mark (a
  mis-marked "Murgh Biryani (veg)" is hidden). Egg is non-veg (FSSAI definition, reg 2(1)(o)).
- **Allergies / celiac / vegan:** a dish whose name matches no template and no recipe
  (`safety_basis` `name`/`none`) → `allergens_unknown` / `vegan_unknown`.
- **Legacy rows:** live catalogue rows written before this change (no `safety_basis`) are not
  trusted for veg; `live_catalog.has_live()` reports the catalogue stale so it is re-read.

### Where facts come from (`safety_basis`)

`provider` (the platform said so — not available from Swiggy today) · `catalogue` (curated rows,
tests only) · `template` (dish-name template) · `recipe` (recipe-library match) · `ingredients`
(a recipe's own ingredient list) · `name` (only lexicon words) · `none`.

## One lexicon (#60)

`smartplate/domain/food_lexicon.py` replaces three disagreeing lists (live_catalog, ingredients,
food_words). English, Tamil, Malayalam, Hindi/Urdu, Telugu and Kannada words for meat, fish,
egg, the FSSAI allergen groups (+ sesame), sweets and salty foods, each with a gloss used as the
reason. Look-alikes are removed first (eggless, eggplant, muttai kose = cabbage, kari patta =
curry leaves, peanut butter is not dairy, coconut milk is not dairy, veg keema, rice flour).

Deliberately conservative choices (false positives only hide a dish):
- `kari` = meat (Tamil), so "Kaju Kari" is hidden from vegetarians until someone checks;
- `kadalai` = peanut (it also means gram), `korma/kurma/shahi/navratan` = cashew paste;
- coconut is **not** treated as a tree nut in dish names (FSSAI 5(14) doesn't list it; the
  pantry swap list still does, conservatively).

## Recipe-library inference (founder's answer to #37–#39)

`dish_library.infer(name)` matches a restaurant dish name against the 6,000-recipe library:
- a recipe whose name words are all in the dish name (strong) → union of its allergens;
- otherwise ≥ 2 close variants ("Semiya Kesari", "Paal Kesari" for "Kesari") → what more than
  half of them contain.
It only ever **adds** allergens, sweet or non-veg; a match never certifies a dish, and a missing
Swiggy veg mark stays "unknown" whatever the recipes say. A strong or variant match does let an
allergy user see the dish as `not_checked` rather than hiding it as `unknown`.

## Research notes (#40)

**FSSAI Labelling & Display Regulations 2020, reg 9** (compendium Version VIII, 9 Sep 2025,
https://www.fssai.gov.in/docs/food-law/regulations/Comp_Labelling%20Display_Version%20VIII_09_09_2025.pdf):
- 9(1): food service establishments with a **Central licence or outlets at 10+ locations** must show
  kcal per serving and serving size on menus, plus "An average active adult requires 2,000 kcal…".
- 9(2): they must also show **allergen information per 5(14)** (symbols allowed) and the **veg/non-veg logo**.
- 9(4): fuller nutrition on request; **25 % deviation tolerated**.
- 9(5): **e-commerce FBOs shall get this information from the restaurant and provide it on their
  website wherever applicable.**
- 5(14) allergens: gluten cereals, crustaceans, milk, egg, fish, peanuts and tree nuts ("Nut"), soy,
  sulphite ≥ 10 mg/kg. (Sesame is not on the FSSAI list; Ziggy keeps it.)
- 2(1)(o): non-vegetarian includes eggs.

**Swiggy Builders Club MCP** (https://mcp.swiggy.com/builders/docs/reference/food/get_restaurant_menu.md
and …/search_menu.md, read 14 Oct 2026):
- `get_restaurant_menu` items: `id, name, description?, price?, inStock?, isVeg?, isBestseller?,
  rating?, imageUrl?, hasVariants?, hasAddons?` — **no kcal, no allergens**. `description` is
  truncated in the browse view.
- `search_menu` items: `name, price?, isVeg?, menu_item_id?, inStock?, …, variations[{isVeg?}]` —
  **no kcal, no allergens**, no description.
- So Swiggy passes on the veg mark only. Ziggy now uses everything that is there: `isVeg`, a
  non-veg `variations[].isVeg` (the dish counts as non-veg), and `description` (read by the lexicon,
  restrictions only). `swiggy_live._provider_facts` reads `allergens`/`kcal`/`calories` if Swiggy
  ever adds them (basis `provider`).
- Open: ask builders@swiggy.in whether chain items (reg 9 applies to them) can expose kcal and
  allergen fields through MCP, as reg 9(5) suggests the platform already holds them.

## Not done here / founder decisions

See the W1 report: medical filters in the consumer beta (#41), the "kari" and coconut calls, and
whether allergy users should see `not_checked` dishes at all or only hand-off.
