# Try SmartPlate

[Open the trial branch in GitHub Codespaces](https://codespaces.new/Hasen1506/Smartplate/tree/codex/finish-smartplate-trial?quickstart=1).

Choose **Create codespace** (or resume it). Setup installs the Python dependencies and starts SmartPlate automatically. Open **Ports → SmartPlate / 5057 → Open in Browser** if a tab does not open automatically. Keep port visibility **Private**. This is the actual Flask + SQLite + PuLP application, not the separate HTML wireframe. Your Codespaces account controls availability and usage.

## Trial walkthrough

1. On the welcome screen choose **Set up my week**. Answer the five steps: what you eat
   (and must avoid), which meals to plan and how often you cook, your usual places,
   a weekly budget (tap a suggestion or type one; optionally cap a single day), and
   an optional goal. You land on **Today** with the rest of this week planned.
   The budget is prorated if you start mid-week.
2. On **Today**, check the next meal's price and *Order by* time. *Order on Swiggy*
   opens Swiggy's public search for that dish (no account access). *I had it*
   records the meal. Then rate it 👍 or 👎 (👎 keeps that dish out of plans for a while).
3. Tap **Change** to see the short list: usual places first, three new picks, cook
   options, skip, or *Let SmartPlate choose*. Unsafe dishes never appear, and a pick
   you make is kept on later re-plans.
4. Open **Week** and drag one meal onto another (or tap a meal → *Move to another
   day*) to swap them. Only those two change.
5. Star or unstar restaurants in **Places**. **More** has Settings (budget, meals,
   allergies, fasts you keep, goal), Coming up (holidays), nutrition insights,
   simulated auto-ordering with a spend limit, cooking and groceries, expenses (CSV),
   community weeks, the Swiggy status and profile switching.
6. Refresh the page: your profile and plan are restored. After the week ends, the
   next one is planned automatically.

Want to look around first? On the welcome screen open **Or look around a sample
profile**. Its allergy and medical selections are fictional examples.

The restaurant catalogue and prices are samples for Chennai. Weather is a live
Open-Meteo forecast when the machine is online and a sample pattern otherwise.
Holiday dates cover Sep 2026 – Mar 2027. Nutrition targets are general wellness
estimates, not medical advice. No real order or payment happens inside SmartPlate.
See [the verified Swiggy integration findings](vendor/swiggy/README.md) and
[value and simplicity](value-and-simplicity.md).

## Local alternative

Use Python 3.12:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python run.py
```

Open `http://localhost:5057`. Windows activation: `.venv\Scripts\activate`.

The app initializes and seeds an empty database automatically even when launched through `create_app()`. Existing profiles and plans are preserved. To keep data, retain `smartplate.db` (or the path specified by `SMARTPLATE_DB`); deleting a Codespace also removes its local database unless backed up.

## Runtime boundaries

This is a private, single-process trial. In-process locking serializes API reads/edits/checkout and duplicate simulation calls. Do not expose it publicly or launch multiple independent workers: app-owned authentication and cross-process transactional order coordination are not implemented. The Codespaces port supplies the private access boundary. The Flask development server is appropriate for this trial, not a production service.

## Verification

```bash
python -m pytest -q
node --check smartplate/static/app.js
node src/sourcing/menu-source.test.js
node --test tests/frontend.test.cjs
```

The tests cover fresh startup, saved settings and plans, browser-independent UI state, checkout consent, skipped/ordered sessions, substitution spend ceilings, duplicate requests, payment ambiguity and expense deduplication. Live Swiggy authentication, actual payment, cloud Codespace creation and visual browser QA have not been validated in this environment.
