# Try SmartPlate

[Open the trial branch in GitHub Codespaces](https://codespaces.new/Hasen1506/Smartplate/tree/codex/finish-smartplate-trial?quickstart=1).

Choose **Create codespace** (or resume it). Setup installs the Python dependencies and starts SmartPlate automatically. Open **Ports → SmartPlate / 5057 → Open in Browser** if a tab does not open automatically. Keep port visibility **Private**. This is the actual Flask + SQLite + PuLP application, not the separate HTML wireframe. Your Codespaces account controls availability and usage.

## Trial walkthrough

1. Open **Settings**. Rename the sample profile, set your weekly budget, diet, rating floor, nutrition targets and cooking frequency. The initial medical/allergy selections are fictional examples; adjust them. Save and replan.
2. Open a meal, skip or snooze it, then restore it. Switch between Balanced, Comfort and Tight Week.
3. Review simulated orders. Confirm to exercise menu failures, within-price substitutions and saved order history. No real order or payment occurs.
4. Refresh. Your selected profile, latest plan and order history are restored. Replanning leaves ordered meals fixed and spends only the remaining budget.
5. Open **Expenses**. Completed simulated orders produce sample expense records; regenerating does not duplicate them. CSV exports are trial records, not invoices.

The sample catalog and weather/festival contexts are synthetic. Nutrition filtering demonstrates software constraints, not medical suitability. Community adoption records interest; it does not clone a template into the planner. Real Swiggy orders, actual payments and an unattended scheduling worker are not enabled. See [the verified Swiggy integration findings](vendor/swiggy/README.md).

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
