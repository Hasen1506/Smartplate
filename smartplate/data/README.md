# Data snapshots

`wikibooks_recipes.json` (when present) is imported from the
[Wikibooks Cookbook](https://en.wikibooks.org/wiki/Cookbook:Table_of_Contents) by
`smartplate/integrations/wikibooks_recipes.py` (or the *Import Wikibooks recipes*
workflow). Its recipe text is by Wikibooks contributors and licensed
[CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/); each recipe links to
its page, whose history lists the authors. That file stays under CC BY-SA 4.0 whatever
the licence of the rest of this repository.

## `indian_dishes.json.gz`: the Indian dish library

**Decision (14 Oct 2026, founder):** keep this dataset. More dishes make "type a dish, get
its ingredients" useful from day one. The notes below say exactly what it is, where it came
from and what Ziggy does to stay on the right side of its licence.

- **What it is.** Built by `smartplate/integrations/indian_recipes.py` from the
  [6000+ Indian Food Recipes Dataset](https://data.mendeley.com/datasets/xsphgmmh7b/1)
  by Kanishka Jain, Mendeley Data, Version 1, published 1 Nov 2020,
  DOI [10.17632/xsphgmmh7b.1](https://doi.org/10.17632/xsphgmmh7b.1).
- **Licence of the dataset:** [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/),
  as stated on the Mendeley page. CC BY 4.0 allows reuse, including commercial use, with
  attribution and a note of changes. Ziggy credits it beside every dish search, in the
  `credits` field of `/api/user/<id>/dishes`, and on the `/credits` page.
- **Upstream provenance.** The dataset's author says it "has been created using Archana's
  Kitchen Website" (<https://www.archanaskitchen.com/>), with a crawler, and that Hindi
  entries were machine-translated with `googletrans`. Archana's Kitchen did not publish
  the recipes under CC BY 4.0 itself, so the Mendeley licence can only cover what the
  dataset author could license. That is why Ziggy keeps **facts only**: dish names,
  ingredient lists, prep/cook times, servings, cuisine, course, diet labels and a link to
  each original recipe. Facts such as an ingredient list are generally not protected
  expression; the method text (the authors' own writing) is **not** copied, and the app
  links to the original page for it. `--with-steps` exists only for someone who has the
  right to republish the method text.
- **Changes made (CC BY 4.0 §3(a)(1)(B)).** Rows whose ingredients were never translated
  from Hindi are dropped (the diet and allergen rules read English words); names and
  ingredients are normalised; Ziggy adds its own diet and allergen labels read from the
  ingredient words. Safety does not rely on the dataset's own diet column.
- **If Archana's Kitchen objects.** Remove the rows or the file (`indian_recipes.py`
  rebuilds it with filters) and tell the user plainly. Asking them for written
  permission is the clean long-term fix; it is a founder decision, not a code change.

Not used: RecipeNLG and Recipe1M (licensed for non-commercial research only), and Kaggle
recipe scrapes that state no licence at all (INDoRI and similar).
