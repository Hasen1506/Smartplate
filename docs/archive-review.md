# Archive review: what is useful, what to put away (14 Oct 2026)

Grill #28, #58 and #59, and Vijay's answer to #59: *"keep what's useful, save the rest by
function so I can review before deleting."* Nothing here has been deleted. Four
clearly stale documents were **moved** into `docs/archive/`, and the links to them were
updated. Everything else below is a recommendation for you to accept or overrule.

How this was measured: `git log` dates against `main` at `19fd3ef`, `git branch -r`,
`git cherry`, a per-file comparison of each branch tip with `main`, and a grep of every
`os.environ` read in `smartplate/`, `scripts/`, `wsgi.py` and `run.py`.

---

## 1. Documents

### Moved to `docs/archive/` (done, nothing deleted)

**Update (Round 2, Oct 2026):** at the founder's request the brainstorm, `project/` and
`design-audit/` (≈6.2 MB, mostly PNG screenshots) were then **deleted** from the tree. They stay
in git history: `git show 9fc21e5:docs/archive/<path>`. The 2026-09-27 audit is still in `docs/archive/`.

| File | Last change | Why it went |
|---|---|---|
| `SmartPlate_Brainstorm.html` → `docs/archive/` | 13 Jun | The June brainstorm. The product has moved past it. README link updated. |
| `project/` (Wireframes.html, test-merged.mjs, README) → `docs/archive/project/` | 15 Jun | June wireframes for a UI that no longer exists. Not run by CI. References in `docs/optimization-and-ux.md` updated. |
| `docs/design-audit/` (README and 7 PNGs) → `docs/archive/design-audit/` | 16 Jun | Audits the June grid UI. The dark redesign and `ux-review-2026-10.md` replaced it. |
| `docs/production-readiness-audit-2026-09-27.md` → `docs/archive/` | 30 Sep | A dated audit whose fixes landed (`tests/test_audit_fixes_2026_10_07.py`). Nothing links to it. |

### Keep, but these need a decision from you

| File | Last change | Issue | Recommendation |
|---|---|---|---|
| `README.md` | 11 Oct | One of three navigation models (Today/Week/Saved + Me), grill #28. | Keep. Make it the single IA source once W5's Today/Week UI lands. |
| `docs/IDEAS.md` | 8 Oct | Its "Dark redesign (Oct 8)" section describes Today/Plan/You, the second IA. | Keep the ideas list. Move that one section to the archive when you settle the IA. |
| `docs/ux-review-2026-10.md` | 11 Oct | The third IA (Today, Week, Saved, Cooking). Nothing links to it. | Archive it after W5 merges. |
| `ROADMAP.md`, `SPEC.md` | 14 Jun | Written for the June product. `SPEC.md` §-numbers are still cited in code comments (`domain/fatigue.py`, `domain/nutrition.py`, `src/sourcing/menu-source.js`). | Keep for now. Replace them with a one-page `ARCHITECTURE.md` that you write yourself (grill #58), then archive both. |
| `FEASIBILITY.md` | 13 Jun | Cited by `/api/meta`'s note, `config.py`, `agent_brain.py`, `optimizer.py`, `swiggy_mcp.py`. | Keep. It is still the "why no LLM per decision" record. |
| `docs/implementation-status.md` | 30 Sep | Out of date on Swiggy status. W7 is rewriting it. | W7 owns this one. |
| `docs/product-strategy-2026.md`, `docs/value-and-simplicity.md`, `docs/TRY-SMARTPLATE.md` | Sep | Current, and cited by the README and code. | Keep. |
| `docs/optimization-and-ux.md` | 16 Jun | Old, but it is the model reference that `config.py`, `kernel/*` and `tests/test_optimization_model.py` cite. | Keep. |
| `docs/agent-connector.md`, `docs/production-deployment.md`, `docs/vendor/swiggy/README.md`, `docs/testing/*` | Oct | Current. | Keep. |
| `docs/reliability.md` (new, W9) | 14 Oct | Cron, cold start, analytics, observability, WhatsApp research. | Keep. |

---

## 2. Remote branches (36 plus `origin/HEAD`)

"Merged" means the branch tip is an ancestor of `main`. Most of the others were merged
by uploading files one at a time ("sync: …", "update …") or by squashing, so git does
not see them as merged even though their features are in `main`. "Content in main" is
checked per file: every file the branch added exists in `main` (usually changed since).

### Merged: safe to delete (8, plus `main` and `origin/HEAD`, which stay)
`claude/clever-noether-0fug9d` (16 Jun), `claude/eager-lamport-04qk34` (13 Jun),
`claude/gallant-euler-yue13i` (15 Jun), `claude/loving-wozniak-er45jh` (11 Oct),
`claude/sweet-ride-mtomps` (15 Jun), `claude/trusting-sagan-khy44t` (15 Jun),
`codex/finish-smartplate-trial` (7 Sep), `codex/research-value-additions-for-app` (6 Sep).


### Not merged, but the content is in `main` (superseded): delete after a quick look (23)

| Branch | Last commit | Commits not in main (by patch) |
|---|---|---|
| `claude/epic-clarke-jsojew` | 15 Jun | 0 (a merge of main into itself) |
| `durable-postgres` | 8 Oct | 0 |
| `fix/honest-storage-and-swiggy-copy` | 8 Oct | 0 |
| `fix/pg-pool-self-heal` | 8 Oct | 0 |
| `hark/not-found-banner` | 8 Oct | 0 |
| `redesign-dark-v2` | 8 Oct | 0 |
| `fix/fork-safe-db-pool` | 8 Oct | 2 (test tweak: compare pooled connections by identity) |
| `hark/address-add-refresh` | 8 Oct | 2 |
| `hark/cart-address-echo` | 8 Oct | 2 |
| `hark/cart-confirm` | 8 Oct | 2 |
| `codex/smartplate-readiness-20260927` | 30 Sep | 83 (file-by-file uploads; all its files exist in main) |
| `hark/bugfix-2026-10-07` | 7 Oct | 39 (same) |
| `hark/no-fabricated-data` | 7 Oct | 11 (same) |
| `hark/real-bill-connect` | 8 Oct | 19 (same) |
| `hark/test-suite` | 7 Oct | 51 (same) |
| `roadmap/a-decisions` … `roadmap/g-epicure` (7 branches) | 7 Oct | 9–40 each (same; their `tests/test_roadmap_*.py` are all in main) |
| `fix/live-only-real-data` | 8 Oct | 6 (ends on a "wip:" commit) |

### Not merged, with files `main` does not have: review before deleting (4)

| Branch | Last commit | Files only on this branch | Recommendation |
|---|---|---|---|
| `claude/trusting-pascal-23oj5x` | 16 Jun | `docs/competitive-and-ux-research.md` (298 lines) | Copy the doc into `docs/archive/` if you want it, then delete the branch. |
| `codex/restore-swiggy-discovery` | 24 Sep | `.env.example`, `docs/BETA-ROADMAP.md`, `docs/INVITE-BETA.md`, `smartplate/auth.py`, `integrations/live_catalog.py`, `swiggy_discovery.py`, `swiggy_oauth.py` + 3 test files | An earlier Swiggy discovery and OAuth design that `swiggy_connect.py` replaced. `.env.example` is worth reviving (see §4). Archive the rest. |
| `codex/smartplate-live-core-20260930` | 1 Oct | `agent_api.py`, `food_memory.py`, `live_basket.py`, `live_core.py`, `live_planner.py`, `monitoring.py`, `reminder_queue.py`, `reminder_worker.py`, `static/live-core.js`, 2 docs, 5 test files | An unmerged alternative architecture. Its `reminder_worker.py` (an always-on process) and `monitoring.py` (request ids) are superseded by W9's `/cron/tick` and `observability.py`. Keep the branch until you've read `docs/agent-architecture.md` on it, then delete. |
| `ui/v3-tonight` | 11 Oct | `static/v3/*`, `tests/test_email_signin.py`, `tests/test_ui_v3.py`, 3 more test files | A "Tonight" v3 UI. W5 is building a new `static/v3/` now, so check for overlap before W5 merges. Keep until then. |

Suggested clean-up, which you run yourself (it pushes): `git push origin --delete <branch>` for the 31 branches in the first two groups (8 merged + 23 superseded; never `main`). The exact list is in `docs/branches-cleanup.md`.

---

## 3. Modules and feature flags

No Python module is completely unused. Every file in `smartplate/` is imported by
something. The candidates for archiving are features that are on but secondary, or
experiments that are off:

| Flag (env var) | Off since | Code | Tests | Recommendation |
|---|---|---|---|---|
| `SMARTPLATE_BRAIN=llm` | 13 Jun | `kernel/agent_brain.py` (an LLM brain stub, unused) | 0 | **Archive.** It has been off for 4 months and has no tests. Keep the deterministic brain and pin it. |
| `SMARTPLATE_RATING_FLOOR=soft` (+ `SMARTPLATE_SAFETY_FLOOR`) | 15 Jun | 2 branches in `kernel/optimizer.py` | 0 | **Archive.** It is untested, and "hard" is the brand promise. |
| `SMARTPLATE_VARIETY=on` (+ `_W`) | 15 Jun | 1 term in `optimizer.py` | 0 | **Archive** the env knob. If W6's "surprise me" mode needs variety, it should be a per-user setting, not a server flag. |
| `SMARTPLATE_USUAL_FIRST=on` (+ `_W`) | 16 Jun | 1 term in `optimizer.py` | 2 files | **Decide with W6.** "Favourites first" is now the product direction, so this may become the default, set per user. |
| `SMARTPLATE_LIVE_ORDERS` | 30 Sep | 9 call sites | 15 files | **Keep.** It is the safety switch for real orders. |
| `SMARTPLATE_STABILITY`, `SMARTPLATE_PROTEIN_EVEN`, solver gap/time/nodes, `SMARTPLATE_EDIT_WINDOW` | — | planner tuning | some | **Keep the values, drop the env knobs.** Make them constants: nobody tunes them per deploy. |

Feature modules that are on but not on Ziggy's core path ("favourites → rules → solver"):
`domain/community.py` (shared weeks), `domain/carbon.py`, `domain/surge.py` (W4 is dropping
surge), `domain/sentiment.py`, `domain/reverse_mode.py`, `domain/cooking_coach.py`,
`integrations/calendar_sync.py`, the `/api/demo/idempotency` endpoint and the `_FEATURE_MAP`
in `app.py`. PLAN.md says to archive these behind flags rather than delete them. That
belongs to the workstreams that own those files (W4, W6, W7). This list is the input
for that work.

---

## 4. Environment variables, grouped by function

There are 57 distinct reads, 6 of them new from W9. Grill #59 counted about 43 in
`config.py` alone.

| Group | Variables | Recommendation |
|---|---|---|
| **Hosting and storage** | `DATABASE_URL`, `SMARTPLATE_DB`, `SMARTPLATE_DB_PERSISTENT`, `SMARTPLATE_DISK_MOUNT`, `SMARTPLATE_ENV`, `RENDER`*, `RENDER_EXTERNAL_URL`*, `PORT`*, `DEBUG`, `WERKZEUG_RUN_MAIN`*, `SMARTPLATE_TZ` | Keep. (* set by the platform) |
| **Security** | `SMARTPLATE_SECRET`, `SMARTPLATE_BEHIND_PROXY`, `SMARTPLATE_PUBLIC_URL`, `SMARTPLATE_ALLOWED_HOSTS` | Keep. |
| **Postgres tuning** | `SMARTPLATE_PG_SCHEMA` (tests), `SMARTPLATE_PG_POOL_MAX`, `SMARTPLATE_PG_POOL_TIMEOUT`, `SMARTPLATE_PG_KEEPALIVE`, `SMARTPLATE_PG_KEEPALIVE_IDLE` (new) | Keep, but document them as "don't touch". |
| **Swiggy** | `SMARTPLATE_SWIGGY`, `SMARTPLATE_SWIGGY_MCP`, `SMARTPLATE_SWIGGY_TIMEOUT`, `SMARTPLATE_SWIGGY_REDIRECT_APPROVED`, `SMARTPLATE_SWIGGY_CALLBACK_PATH`, `SMARTPLATE_SWIGGY_MAX_CLIENTS`, `SMARTPLATE_LIVE_ORDERS` | Keep. W7 is folding provider and approval into one mode enum. |
| **Reminders and operations** | `SMARTPLATE_PUSH`, `SMARTPLATE_PUSH_TICK`, `SMARTPLATE_PUSH_CONTACT`, `SMARTPLATE_VAPID_PRIVATE`, `SMARTPLATE_CRON_KEY` (new), `SMARTPLATE_ADMIN_KEY` (new) | Keep. |
| **Observability** | `SMARTPLATE_LOG_FORMAT` (new), `SMARTPLATE_SLOW_MS` (new), `SENTRY_DSN` (new), `SENTRY_TRACES_SAMPLE_RATE` (new) | Keep. |
| **Planner tuning** | `SMARTPLATE_SOLVER_GAP`, `SMARTPLATE_SOLVER_TIME_LIMIT`, `SMARTPLATE_SOLVER_MAX_NODES`, `SMARTPLATE_STABILITY`, `SMARTPLATE_PROTEIN_EVEN`, `SMARTPLATE_EDIT_WINDOW` | Pin them as constants (archive the knob). |
| **Experiments (off)** | `SMARTPLATE_BRAIN`, `SMARTPLATE_RATING_FLOOR`, `SMARTPLATE_SAFETY_FLOOR`, `SMARTPLATE_VARIETY`, `SMARTPLATE_VARIETY_W`, `SMARTPLATE_USUAL_FIRST`, `SMARTPLATE_USUAL_FIRST_W` | Archive (see §3). |
| **Data and test** | `SMARTPLATE_FIXTURE_DATA`, `SMARTPLATE_EPICURE_DIR`, `SMARTPLATE_EPICURE_CHECKSUMS`, `SMARTPLATE_WEATHER`, `SMARTPLATE_WEATHER_TIMEOUT` | Keep. They are test seams. |

Archiving the planner and experiment knobs alone brings the operator-facing list from 57
to about 44, of which only about 15 are likely to be set on Render (an estimate from render.yaml and the README). A `.env.example` (one exists on
`codex/restore-swiggy-discovery`) listing only those would replace grill #59's
"settings.toml" with less machinery.

### Config dataclasses: not done, on purpose
`config.py` holds module-level constants, and 30 test files monkeypatch them
(`monkeypatch.setattr(config, "X", …)`). W7 is also editing `config.py` (the mode enum).
Converting to grouped dataclasses now would touch every one of those tests and
guarantee a merge conflict, for no change in behaviour. That is not low-risk, so it was
skipped. Do it after the merge, group by group, in the order of the table above.
