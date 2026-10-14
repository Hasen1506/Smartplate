# Remote branches to delete (31)

From `docs/archive-review.md` §2 (14 Oct 2026). The **integrator deletes them on GitHub**
(Branches page → trash icon, or the command below); nobody else pushes. Tip commits are listed
so a branch that moved since this list was made can be spotted and left alone, and so any of
them can be recreated from its tip (`git push origin <tip>:refs/heads/<branch>`) while a clone
still has that commit.

Re-checked on `ziggy/r2-frontend` against the fetched `origin/*` refs: all 31 exist; the first 8
are ancestors of `origin/main`.

## Merged into `main` (8): safe to delete

| Branch | Tip |
|---|---|
| `claude/clever-noether-0fug9d` | `69f197c` |
| `claude/eager-lamport-04qk34` | `8f85456` |
| `claude/gallant-euler-yue13i` | `0c6deb3` |
| `claude/loving-wozniak-er45jh` | `24449e9` |
| `claude/sweet-ride-mtomps` | `c659dd9` |
| `claude/trusting-sagan-khy44t` | `7c7724b` |
| `codex/finish-smartplate-trial` | `8a10abb` |
| `codex/research-value-additions-for-app` | `4cd53de` |

## Superseded: content already in `main` (23): delete after a quick look

Not git-merged (uploaded file by file or squashed), but every file they added exists in `main`.
`fix/live-only-real-data` ends on a "wip:" commit; `fix/fork-safe-db-pool` carries a 2-commit
test tweak (compare pooled connections by identity) worth a glance first.

| Branch | Tip |
|---|---|
| `claude/epic-clarke-jsojew` | `693fbee` |
| `durable-postgres` | `931d418` |
| `fix/honest-storage-and-swiggy-copy` | `217c144` |
| `fix/pg-pool-self-heal` | `a4c1c13` |
| `hark/not-found-banner` | `b04d9c2` |
| `redesign-dark-v2` | `3643f12` |
| `fix/fork-safe-db-pool` | `2ab6a42` |
| `hark/address-add-refresh` | `2815479` |
| `hark/cart-address-echo` | `9d3ed0f` |
| `hark/cart-confirm` | `9e546b4` |
| `codex/smartplate-readiness-20260927` | `e2db3c0` |
| `hark/bugfix-2026-10-07` | `bf34484` |
| `hark/no-fabricated-data` | `80084d9` |
| `hark/real-bill-connect` | `a2dd4cf` |
| `hark/test-suite` | `8b8213f` |
| `roadmap/a-decisions` | `629f67d` |
| `roadmap/b-live-menus` | `d0647cc` |
| `roadmap/c-week-orders` | `b71ce83` |
| `roadmap/d-learning` | `b13d3f0` |
| `roadmap/e-household` | `69395b2` |
| `roadmap/f-onboarding-rhythm` | `cdd50b5` |
| `roadmap/g-epicure` | `d53df53` |
| `fix/live-only-real-data` | `367ae62` |

## Keep for now (not in this list)

`main`, `origin/HEAD`, `ziggy/integration` (draft PR #43), `ziggy/w10-answers`, and the four
branches with files `main` doesn't have — `claude/trusting-pascal-23oj5x`,
`codex/restore-swiggy-discovery` (has the `.env.example` worth reviving),
`codex/smartplate-live-core-20260930`, `ui/v3-tonight` — see archive-review §2 for each.

## One command (integrator only; it pushes)

```sh
git push origin --delete \
  claude/clever-noether-0fug9d \
  claude/eager-lamport-04qk34 \
  claude/gallant-euler-yue13i \
  claude/loving-wozniak-er45jh \
  claude/sweet-ride-mtomps \
  claude/trusting-sagan-khy44t \
  codex/finish-smartplate-trial \
  codex/research-value-additions-for-app \
  claude/epic-clarke-jsojew \
  durable-postgres \
  fix/honest-storage-and-swiggy-copy \
  fix/pg-pool-self-heal \
  hark/not-found-banner \
  redesign-dark-v2 \
  fix/fork-safe-db-pool \
  hark/address-add-refresh \
  hark/cart-address-echo \
  hark/cart-confirm \
  codex/smartplate-readiness-20260927 \
  hark/bugfix-2026-10-07 \
  hark/no-fabricated-data \
  hark/real-bill-connect \
  hark/test-suite \
  roadmap/a-decisions \
  roadmap/b-live-menus \
  roadmap/c-week-orders \
  roadmap/d-learning \
  roadmap/e-household \
  roadmap/f-onboarding-rhythm \
  roadmap/g-epicure \
  fix/live-only-real-data
```
