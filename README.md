# SmartPlate v1.1

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
[the September 2026 strategy memo](docs/product-strategy-2026.md).

## Run it

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python run.py                       # → http://localhost:5057
```

No build step, no Node, no API keys. The database is created and seeded with a
demo Chennai catalog, three users, and a sample week on first run.

```bash
pytest                              # 37 tests: allergen safety, idempotency, MILP, substitution, features
```

## What's in the box

| Layer | Modules |
|---|---|
| **Kernel** (domain-agnostic, the reusable skeleton from §2) | `scheduler`, `budget`, `optimizer` (MILP/CBC), `variance` (substitution), `explainability`, `agent_brain` (deterministic vs optional LLM) |
| **Domain** (the 17 features as constraints/signals) | `allergens`, `nutrition`, `household`, `leftovers`, `festivals`, `weather`, `surge`, `reverse_mode`, `community`, `receipts`, `health`, `carbon`, `cooking_coach`, `sentiment` |
| **Integrations** | `swiggy_mcp` (Simulated \| Live), `calendar_sync` (.ics) |

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
| `SMARTPLATE_SWIGGY` | `simulated` | `live` swaps in the real MCP adapter (requires gated access) |
| `SMARTPLATE_DB` | `smartplate.db` | SQLite path |

## The one thing not built here

Live Swiggy *order placement* needs gated MCP access + a ToS clause permitting
scheduled/unattended orders (the brainstorm's #1 legal risk, §7.1). Everything is
built against the `SwiggyMCP` adapter with a simulated provider; swapping in the
live provider is the only remaining external dependency. See FEASIBILITY.md §4.
