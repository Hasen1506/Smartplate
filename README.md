# SmartPlate — private trial

A **scheduling** AI agent for food delivery — the white space the
[strategic brainstorm](SmartPlate_Brainstorm.html) identified: scheduled +
predictive + budget-constrained + delivery-integrated + autonomous substitution.

This repo is the **working v1.1 app**. It plans a week of meals with a real
mixed-integer optimiser (the "agent" is a solver, **not** an LLM — see
[FEASIBILITY.md](FEASIBILITY.md)) and integrates **all 17 gap features** from
§5.1, §5.2 and §5.3 of the brainstorm. The Debt/EMI integration is intentionally
out of scope, as requested.

> **Read [FEASIBILITY.md](FEASIBILITY.md) first.** It answers "is this possible?"
> (yes) and "does agentic execution cost too much, should we hold?" (no — v1.1
> runs at ≈₹0 per decision because the planner is a CPU solver, not a paid model).

For the differentiated product thesis, revenue experiments, production gates, and
terms-safe Swiggy/Swiggy Money rollout, see
[the September 2026 strategy memo](docs/product-strategy-2026.md). For "is this
worth building, connector or app, who pays, and how do we keep it simple?", see
[value and simplicity](docs/value-and-simplicity.md).

## What using it feels like

1. **Set up in five answers**: diet and allergies, which meals, your usual places,
   a weekly budget (suggested from those places), an optional goal.
2. **Today** shows the next meal, what it costs including delivery, and **when to
   order it** (ahead of the rush, with extra time on rainy days). Tap *Order on
   Swiggy*, *Change* or *I had it*.
3. **Change** opens a short list: your usual places, three dishes each, plus three
   new picks, all already filtered for your allergies and budget. No endless menu.
4. **Week**: drag a meal onto another day (or tap *Move*) to swap. The budget and
   nutrition re-balance, and nothing else in the week gets shuffled.
5. **Heads-up**: rain, heat, holidays, the fasts you keep, meals that didn't fit
   the budget, and past meals to confirm.
6. **Reminders**: one tap adds every order-by time to your phone calendar, with
   alarms. Browser alerts also work while the app is open.
7. **Installable and private**: add it to your home screen. A profile you create
   is private to your browser; its recovery code opens it on another device.

## Try it in your browser

[Open SmartPlate in GitHub Codespaces](https://codespaces.new/Hasen1506/Smartplate/tree/codex/finish-smartplate-trial?quickstart=1)

Choose **Create codespace**, wait for setup, then open **Ports → SmartPlate / 5057 → Open in Browser**. The trial starts automatically. It uses the real Python planner with sample Chennai data and simulated orders. Settings, latest plans and order history persist in the Codespace database. Keep the port private.

**Real Swiggy ordering is not finished.** The documentation is accessible now, but its recipe and reference disagree. OAuth, authenticated tool schemas, real catalog mapping and live checkout still need integration. See [verified findings](docs/vendor/swiggy/README.md) and [trial instructions](docs/TRY-SMARTPLATE.md). Earlier architectural documents describe intentions beyond the trial’s current behavior.

## Run it locally

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python run.py                       # → http://localhost:5057
```

No build step, no Node, no API keys. The database is created and seeded with a
demo Chennai catalog, three users, and a sample week on first run.

```bash
python -m pytest -q                 # current backend regression suite
```

## What's in the box

| Layer | Modules |
|---|---|
| **Kernel** (domain-agnostic, the reusable skeleton from §2) | `scheduler`, `budget`, `optimizer` (MILP/CBC), `variance` (substitution), `explainability`, `agent_brain` (deterministic vs optional LLM) |
| **Domain** (the 17 features as constraints/signals) | `allergens`, `nutrition`, `household`, `leftovers`, `festivals`, `weather`, `surge`, `reverse_mode`, `community`, `receipts`, `health`, `carbon`, `cooking_coach`, `sentiment` |
| **Everyday flows** | `everyday` (onboarding, shortlist, pick, swap, confirm, rate, heads-up), `domain/profile` (goals → targets), `domain/taste` (usual places, ratings), `domain/timing` (order-by, hand-off) |
| **Integrations** | `swiggy_mcp` (Simulated \| Live), `calendar_sync` (.ics), Open-Meteo weather (`domain/weather`) |

### The 17 gap features (all integrated)

**§5.1 (v1 gaps):** ① allergy/medical hard-exclusions · ② calendar integration ·
③ idempotency keys · ④ explainability log · ⑤ pause/snooze/cooked.

**§5.2 (v1.1 adds):** ⑥ nutrition layer · ⑦ household/group mode ·
⑧ leftover awareness · ⑨ festival calendar · ⑩ weather adaptation · ⑪ surge prediction.

**§5.3 (bigger bets):** ⑫ reverse mode (cook days) · ⑬ community templates ·
⑭ receipt/expense surface · ⑮ health/longevity · ⑯ carbon scoring · ⑰ cooking coach.

The **17 features** tab in the UI lists each one with the file that implements it.

## How the agent thinks

Each weekly session becomes a choice between delivery candidates, a cook option,
and skip. **Hard constraints** (allergens, medical limits, diet, rating floor,
fasting window, calendar/festival suspension) are filtered out of the candidate
set, so the solver *cannot* violate them — even under Tight-Week (Survival) mode.
**Soft constraints** (taste, nutrition, health, carbon, surge, weather/festival
bias) are weighted terms in the objective. Budget is a hard cap; skip is the
always-feasible relief valve. Every decision emits plain-language reasons.

At execution the plan is placed against the (simulated) Swiggy MCP with
idempotency keys and a compensating saga; a menu-load failure (§1.2) triggers
substitution to the next-best option **above** your rating floor, never below.
The UI now retrieves a server-authored checkout preview and binds confirmation to
its fingerprint and maximum total, so a plan changed after review is rejected
instead of silently ordered.

## Configuration

| Env var | Default | Meaning |
|---|---|---|
| `PORT` | `5057` | HTTP port |
| `SMARTPLATE_BRAIN` | `deterministic` | `llm` opts into the paid narrator (off the critical path) |
| `SMARTPLATE_SWIGGY` | `simulated` | `live` is unsupported and checkout returns a clear error |
| `SMARTPLATE_DB` | `smartplate.db` | SQLite path |
| `SMARTPLATE_WEATHER` | `live` | `live` = Open-Meteo forecast (cached, falls back to the sample feed offline); `simulated` = sample feed only |
| `SMARTPLATE_SOLVER_GAP` | `0.001` | Relative optimality gap for the weekly MILP |
| `SMARTPLATE_SOLVER_TIME_LIMIT` | `10` | Seconds per solve before returning the best plan found |
| `SMARTPLATE_SWIGGY_MCP` | `https://mcp.swiggy.com` | Swiggy MCP base for sign-in + read-only tool discovery |
| `SMARTPLATE_PUBLIC_URL` | derived | Public base URL for the Swiggy OAuth redirect when a proxy hides it |
| `SMARTPLATE_TZ` | `Asia/Kolkata` | Timezone for meal times, "today" and past meals (servers often run in UTC) |
| `SMARTPLATE_STABILITY` | `0.3` | Bonus for keeping a meal's current pick on re-plans (0 disables) |

## Live integration status

Ordering today is a **hand-off**: *Order on Swiggy* opens Swiggy's public search for
that restaurant and dish, the person orders there, then taps *I had it* so the budget
and nutrition stay accurate. The simulated auto-ordering path (idempotent,
spend-limited) remains under More. The real Swiggy path is not a drop-in replacement: it needs OAuth, live identifiers and cart schemas, address/payment selection, pending-payment handling and order reconciliation. No background worker places scheduled orders. See [the verified integration notes](docs/vendor/swiggy/README.md).
