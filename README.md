# SmartPlate — invite beta

A **scheduling** AI agent for food delivery — the white space the
[strategic brainstorm](SmartPlate_Brainstorm.html) identified: scheduled +
predictive + budget-constrained + delivery-integrated + autonomous substitution.

This repo has a public invite-beta path and an explicit local demo mode. It plans a week of meals with a real
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

## Public invite beta

Public mode uses Supabase email OTP for invited accounts, PostgreSQL for persistent plans,
and a per-user Swiggy OAuth connection. A user chooses a saved Swiggy delivery address,
searches open restaurants, browses their returned menus, selects up to eight restaurants,
sets budget and dietary limits, then explicitly chooses meal slots before planning.
No sample restaurant or prefilled week is available to the public planner.
The weekly grid's auto / deliver / cook / skip controls affect the solver.

The Swiggy handoff opens Swiggy for final availability, safety details, price, and order
placement. SmartPlate **does not place or schedule live orders**. Swiggy browse results
usually omit allergen and nutrition evidence; when a user's safety restriction requires
missing evidence, the planner excludes that dish. Listed prices omit delivery fees, taxes,
and checkout changes. See [release setup](docs/INVITE-BETA.md) and [ranked roadmap](docs/BETA-ROADMAP.md).

## Local demo

[Open SmartPlate in GitHub Codespaces](https://codespaces.new/Hasen1506/Smartplate/tree/codex/finish-smartplate-trial?quickstart=1)

Choose **Create codespace**, wait for setup, then open **Ports → SmartPlate / 5057 → Open in Browser**. Set `SMARTPLATE_MODE=demo` when running locally. The demo uses sample Chennai data and simulated orders. Keep the port private.

**Live ordering is intentionally outside this beta.** The read-only Swiggy discovery adapter checks tool availability at connection time; a real account and Swiggy access are required to validate production responses. See [verified findings](docs/vendor/swiggy/README.md) and [trial instructions](docs/TRY-SMARTPLATE.md).

## Run it locally

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
SMARTPLATE_MODE=demo python run.py # → http://localhost:5057
```

No build step, no Node, no API keys in demo mode. The database is created and seeded with a
sample Chennai catalog, three users, and a sample week on first demo run.

```bash
python -m pytest -q                 # current backend regression suite
```

## What's in the box

The 17 experimental gap features below belong to the local demo. The public
beta exposes the smaller, real-data planning path and suppresses simulated
orders, receipts, surge savings, weather, and carbon claims.

| Layer | Modules |
|---|---|
| **Kernel** (domain-agnostic, the reusable skeleton from §2) | `scheduler`, `budget`, `optimizer` (MILP/CBC), `variance` (substitution), `explainability`, `agent_brain` (deterministic vs optional LLM) |
| **Domain** (the 17 features as constraints/signals) | `allergens`, `nutrition`, `household`, `leftovers`, `festivals`, `weather`, `surge`, `reverse_mode`, `community`, `receipts`, `health`, `carbon`, `cooking_coach`, `sentiment` |
| **Integrations** | `swiggy_discovery` (read-only live menus), `swiggy_oauth` (per-user authorization), `swiggy_mcp` (simulated demo), `calendar_sync` (.ics) |

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
| `SMARTPLATE_MODE` | `production` | `demo` explicitly enables sample data and simulated orders |
| `SMARTPLATE_DB` | `smartplate.db` | SQLite path for local demo |
| `DATABASE_URL` | unset | Supabase PostgreSQL URL for public mode |
| `SUPABASE_URL` | unset | Supabase project URL for invite sign-in |
| `SUPABASE_PUBLISHABLE_KEY` | unset | Supabase publishable key |
| `SMARTPLATE_INVITED_EMAILS` | unset | Comma-separated invited addresses |
| `SMARTPLATE_SESSION_SECRET` | unset | Long random signing and token-encryption secret |
| `SMARTPLATE_PUBLIC_BASE_URL` | unset | Exact HTTPS origin for Swiggy callback |

## Live integration status

The beta's Swiggy path is read-only and per-user. Real cart and payment flows require
separate implementation and review. No background worker places scheduled orders.
