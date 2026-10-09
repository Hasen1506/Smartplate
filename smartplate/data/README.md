# Data snapshots

`wikibooks_recipes.json` (when present) is imported from the
[Wikibooks Cookbook](https://en.wikibooks.org/wiki/Cookbook:Table_of_Contents) by
`smartplate/integrations/wikibooks_recipes.py` (or the *Import Wikibooks recipes*
workflow). Its recipe text is by Wikibooks contributors and licensed
[CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/); each recipe links to
its page, whose history lists the authors. That file stays under CC BY-SA 4.0 whatever
the licence of the rest of this repository.

Not used: RecipeNLG and Recipe1M (licensed for non-commercial research only), and
recipe datasets scraped from commercial sites without a licence.

`indian_dishes.json.gz` is built by `smartplate/integrations/indian_recipes.py` from the
[6000+ Indian Food Recipes Dataset](https://data.mendeley.com/datasets/xsphgmmh7b/1)
(Kanishka Jain, Mendeley Data V1, DOI 10.17632/xsphgmmh7b.1, CC BY 4.0), whose recipes
come from [Archana's Kitchen](https://www.archanaskitchen.com/). It keeps facts only: dish
names, ingredient lists, times, servings, cuisine, course, diet labels and a link to each
original recipe. The method text is not copied (pass `--with-steps` only if you have the
right to republish it). Rows whose ingredients were never translated from Hindi are left
out, since the diet and allergen rules read English words.

`indian_dishes.json.gz` is built by `smartplate/integrations/indian_recipes.py` from the
[6000+ Indian Food Recipes Dataset](https://data.mendeley.com/datasets/xsphgmmh7b/1)
(Kanishka Jain, Mendeley Data V1, DOI 10.17632/xsphgmmh7b.1, CC BY 4.0). Its recipes
come from [Archana's Kitchen](https://www.archanaskitchen.com/). The file keeps facts
only: dish names, ingredient lists, times, servings, cuisine, course, diet labels and a
link to each original recipe. The method text is not copied; pass `--with-steps` only if
you have the right to republish it. Rows whose ingredients were never translated from
Hindi are left out, because the diet and allergen rules read English words.
