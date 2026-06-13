# SmartPlate v1.1 — Feasibility Report

**Question asked:** Build a fully working v1.1 app with **every** gap idea from §5.1, §5.2 and §5.3
of the brainstorm integrated (Debt/EMI integration explicitly out of scope). *Is it even possible?
Agentic execution costs a lot — do we hold on?*

**Verdict: Yes, it is possible — and no, you do not need to hold on.** This document explains why,
where the one real caveat is (live Swiggy order placement), and how the cost concern is engineered away.
The working app that implements this report ships in the same commit.

---

## 1. The one-paragraph answer

The v1.1 "agent" is a **constraint solver, not a language model**. Planning a week of meals is a
mixed-integer optimisation problem (MILP) solved by CBC in **single-digit milliseconds on a CPU you
already pay for** — effectively free. Sentiment runs on a **local lexicon** (no API), the taste model
is **statistical** (per-user ratings/variance), and every explanation is **templated from solver
output**. LLM calls are *optional polish* (conversational plan editing), gated behind an interface and
**off by default**. So the expensive, recurring "agentic execution" cost the question worries about is
**not on the critical path for v1.1**. Build now; switch the LLM brain on later when revenue justifies it.

---

## 2. What "agentic execution cost" actually is — and why it is near-zero here

There are two very different things people call "the agent":

| | What it is | Cost model | Used in v1.1? |
|---|---|---|---|
| **The planner** | MILP/LP solver (CBC via PuLP) that chooses items, restaurants, cook-vs-order, and time-shifts under hard + soft constraints | CPU-bound, ~1–10 ms per weekly re-plan. Marginal cost ≈ **₹0** | **Yes — this is the product** |
| **The narrator** | An LLM that turns a request into a plan edit, or writes prose explanations | Per-token API cost; recurring; scales with users | **No — optional, off by default** |

The brainstorm itself already points the right way (§6): *"Google NLP API is ~100× more expensive than a
local HuggingFace distilbert… plan to self-host from v1; it's an evening's work."* We go one step further
and make the **default** sentiment + explanation path require **no model server at all** — a lexicon and
string templates. The architecture keeps an `AgentBrain` seam:

```
AgentBrain (interface)
├── DeterministicBrain   ← default. Solver + templates + lexicon. Cost ≈ 0. Ships in v1.1.
└── LLMBrain             ← optional. Natural-language plan editing & prose. Costs money. Pluggable later.
```

**Cost ceiling for v1.1 in production:** a Fargate task + small RDS. No per-decision API spend. You can
run a real pilot (hundreds of users) for the price of a couple of coffees a day, because the decision
engine never calls a paid model. **This is the reason the answer to "do we hold on?" is no.**

---

## 3. Is full feature integration possible? Feature-by-feature feasibility

All 17 gap ideas are buildable **now** with real logic. None of them actually require an LLM; every one
is either a constraint, a signal, or a data surface that feeds the same solver.

### §5.1 — v1 gaps (all 5: built and enforced)
| # | Feature | How it's real in v1.1 | Risk |
|---|---------|------------------------|------|
| 1 | Allergy / medical hard-exclusions | Allergens & medical limits are **pre-filtered out of the candidate set** → the solver *cannot* pick them, even under Survival Mode | None (design-enforced) |
| 2 | Calendar integration | ICS parsing + a calendar source; "meeting 1pm" shifts the lunch trigger, "out of town" suspends the day | Live Google/Outlook OAuth deferred; ICS + simulated feed work now |
| 3 | Idempotency keys | Every order write carries `key = sha256(user, plan, session, trigger_ts)`; duplicate writes are suppressed (proven by test) | None |
| 4 | Explainability log | First-class output of **every** solver run; stored per decision, surfaced in UI | None |
| 5 | Pause / snooze / "I cooked" | Session states `active / snoozed / skipped / cooked`; re-plan respects them | None |

### §5.2 — v1.1 / v2 adds (all 6: built)
| # | Feature | How it's real in v1.1 | Risk |
|---|---------|------------------------|------|
| 6 | Nutrition layer | kcal + macros per item; daily targets as **soft constraints** | Real chain nutrition data needs licensing; seeded data now |
| 7 | Household / group mode | Multiple members, **unioned** allergen hard-constraints, merged plan, cost split | None for planning; live payment split deferred |
| 8 | Leftover / home-cooking | "Cooked dal → skip Wed dinner" reduces sessions and budget draw | None |
| 9 | Festival / cultural calendar | Indian calendar (Diwali, Ramadan/iftar, Pongal, Onam, Eid) biases/suspends sessions | Dates seeded; annual refresh needed |
| 10 | Weather adaptation | Rain → surge + comfort bias; heat → lighter bias | Live weather API deferred; simulated feed now |
| 11 | Surge prediction | Day-of-week × weather × history model returns a multiplier the planner avoids by time-shifting | Real accuracy needs live order history |

### §5.3 — bigger bets (all 6: built)
| # | Feature | How it's real in v1.1 | Risk |
|---|---------|------------------------|------|
| 12 | Reverse mode (Instamart cook days) | Planner chooses an **order-vs-cook mix**; emits a grocery basket | Live Instamart MCP deferred; modelled now |
| 13 | Community plan templates | Save / browse / clone Survival plans | Moderation needed at scale |
| 14 | Receipt / expense surface | Auto-tag business meals, **CSV export** | None |
| 15 | Health / longevity | Protein/day, veg count, fasting-window awareness as constraints | None |
| 16 | Carbon / sustainability | Per-item CO₂e estimate; optional soft objective | Estimates are coarse (labelled as such — no greenwashing) |
| 17 | Cooking-mode coach | On skip-delivery weeks, emits recipes + Instamart pull | Recipe corpus is seeded/small |

**Conclusion:** 17/17 are integrated into one shared engine. The pattern that makes this tractable is that
almost every feature is **a constraint or a signal on the same optimiser**, not a separate subsystem.

---

## 4. The one honest caveat: live Swiggy order *placement*

What **cannot** be fully built or tested by us right now is the *live* leg of placing real orders on Swiggy,
because it depends on things outside the code:

- **Gated MCP access + OAuth** to a real Swiggy account.
- **ToS permission for scheduled / unattended orders.** The brainstorm flags this as the #1 legal risk
  (§7.1): Swiggy's Jan-2026 rollout was cautious (confirmation steps, COD-only). *If* their ToS forbids
  auto-placing without per-order confirmation, the "30-minute editable window" must become "auto-cancel
  unless confirmed." **This is a contract/ToS question, not an engineering blocker.**

**How we de-risk it:** everything is built against a `SwiggyMCP` adapter interface with a **simulated
provider** that mimics the real one — including the known menu-load failure mode (§1.2) so the
substitution engine is exercised against the exact failure Swiggy itself hits. The order path already
carries idempotency keys and runs as a saga with compensation (§6). The day access is granted, you swap
`SimulatedSwiggyProvider` → `LiveSwiggyProvider`; **nothing else changes.** Every other feature is fully
real today.

This is the correct sequencing the brainstorm recommends (§8): prove the engine end-to-end against a
realistic simulator *before* spending the Swiggy-relationship capital.

---

## 5. Architecture that delivers it

```
React SPA (served by Flask, no build step)
        │  JSON API
┌───────▼──────────────────────────────────────────────┐
│ Flask app                                             │
│                                                       │
│  KERNEL (domain-agnostic, the reusable bit)           │
│   • scheduler   trigger windows, retries, snooze      │
│   • budget      envelope, planned-vs-actual, variance │
│   • optimizer   MILP via PuLP/CBC  ← Survival Mode     │
│   • variance    substitution + rating floor + sentiment│
│   • explainability   reason log per decision          │
│   • agent_brain  Deterministic (default) | LLM (opt)  │
│                                                       │
│  DOMAIN (the 17 features as constraints/signals)      │
│   allergens·nutrition·household·leftovers·festivals·  │
│   weather·surge·reverse_mode·community·receipts·      │
│   health·carbon·cooking_coach·sentiment               │
│                                                       │
│  INTEGRATIONS                                          │
│   swiggy_mcp (Simulated | Live)   calendar_sync (ICS) │
│                                                       │
│  SQLite persistence + seed data                       │
└───────────────────────────────────────────────────────┘
```

The kernel is deliberately the **same scheduling+budget skeleton** the brainstorm says is shared with the
Debt/EMI tool (§2) — we just don't wire the Debt domain in, per your instruction. The seam is left clean
so a future cross-app signal (§4.3 v2) is a small add, not a rewrite.

---

## 6. Cost summary (the bottom line you asked for)

| Cost driver | v1.1 (this build) | If you turned on the optional LLM brain |
|---|---|---|
| Per weekly re-plan | ~1–10 ms CPU ≈ **₹0** | + LLM tokens (only when user uses NL editing) |
| Sentiment / taste | Local lexicon + stats ≈ **₹0** | optional model server |
| Explanations | Templated ≈ **₹0** | optional prose generation |
| Infra to pilot | 1 small container + SQLite/RDS | same |

**Recommendation: do not hold.** The recurring agentic cost you were worried about lives entirely in the
*optional* narrator, which v1.1 doesn't need. Ship the deterministic engine, run a real pilot for pocket
change, and only switch on paid LLM features once they're earning. The single thing to resolve before a
*public* launch is the **Swiggy ToS clause on scheduled orders** — a reading task, not a build task.

---

## 7. What ships alongside this report

A running app (`python run.py`, open `http://localhost:5057`) with: weekly meal planning, Survival Mode
MILP optimiser, substitution + rating floor + sentiment, and **all 17 gap features** wired into the
optimiser and exposed in the UI and API — running against a simulated Swiggy MCP with idempotent,
saga-based order placement. See `README.md` to run it and `tests/` for the proofs (allergen safety,
idempotency, Survival Mode, feature integration).
