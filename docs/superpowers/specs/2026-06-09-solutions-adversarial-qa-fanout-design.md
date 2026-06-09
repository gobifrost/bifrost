# Solutions adversarial UI/CLI QA fan-out

**Date:** 2026-06-09
**Worktree:** solutions-success-criteria
**Status:** design — awaiting review

## Goal

Drive the Solutions feature end-to-end as an adversarial QA team: 6 agents,
each in its own git worktree with its own port-mode debug stack, each owning
one adversarial risk axis, driving the **real UI (Playwright) and real CLI** —
not API-level pokes. Findings are structured, independently verified to kill
false positives, then synthesized into a ranked fix backlog.

This is the "drive it, don't just test it" pass: green unit/e2e tests are not
"done" for a feature this broad. Prior shakeouts at the API level found real
bugs but also produced false positives; this pass adds real browser/CLI
interaction and an independent verification stage.

## Orchestration: a Workflow

The fan-out is a `Workflow` (deterministic orchestration): it owns the
concurrency semaphore, the per-agent worktree+stack lifecycle, the
find→verify pipeline, and the synthesis. Token cost is explicitly not a
constraint — coverage and confidence are the goal.

Phases:
1. **Pre-flight cleanup** (serial, runs once)
2. **Fan-out: 6 axis agents** (parallel, capped at 6)
3. **Verify pass** (parallel, one verifier per non-OK finding)
4. **Synthesis** (serial, one agent)

## Phase 1 — Pre-flight cleanup

Runs before any QA agent. Frees RAM and disk; reports what it freed.

- **Kill stray stacks.** Stop debug/test stacks belonging to OTHER worktrees
  (keep `solutions-success-criteria`'s own debug stack). Identified by Compose
  project label. Each test stack ≈ 1.3–1.6 GB, debug ≈ 2.2 GB.
- **Prune merged agent worktrees.** For each `worktree-agent-*` branch merged
  into `main`: `git worktree remove <path>` then `git branch -d <branch>`.
  Named feature worktrees (entity-access, bridge-cse, pr-288-review,
  bifrost-plugin-testing, etc.) are LEFT UNTOUCHED.
- **Report:** RAM freed, disk freed, worktrees/branches removed, what was kept.

This phase is plain orchestration (Bash), not an agent — it's deterministic
and must complete before stacks are spun up.

## Phase 2 — Resource budget & the 6 axis agents

### Budget

- **Ceiling: 6 concurrent** agents. Each runs its own **port-mode** debug
  stack (~2.2 GB) → ~13 GB of stacks, well under ~49 GB available with dev
  headroom on this 62 GB machine.
- Semaphore = 6. If missions exceed 6 they queue and run in waves as stacks
  free. (The base matrix is exactly 6, so one wave.)

### Per-agent lifecycle

Each axis agent, in its own isolated worktree:

1. **Create worktree** off the current branch (`solutions-success-criteria`
   HEAD) — `git worktree add` under a temp path, or the workflow's worktree
   isolation. Per-worktree-path Compose project name gives stack isolation
   automatically (`debug.sh` derives it from the path).
2. **Boot a PORT-MODE stack.** CRITICAL: Chrome cannot drive netbird-mode
   stacks (Vite HMR websocket hangs — known issue). Force port mode by running
   `./debug.sh up` with `NETBIRD_SETUP_KEY` unset:
   `env -u NETBIRD_SETUP_KEY ./debug.sh up`. Capture the `localhost:<port>`
   URL from `./debug.sh status`.
3. **Install the API-matched CLI** in a scratch venv outside the repo
   (`/tmp/bifrost-cli-<axis>`), per the CLAUDE.md recipe: venv →
   `pip install "<API_URL>/api/cli/download"` → `bifrost login` (writes
   `.env`). This avoids version-mismatch masking.
4. **Drive** the UI with Playwright against `localhost:<port>` and the CLI from
   the scratch venv. Capture screenshots for UI findings.
5. **Write structured findings** (schema below) to its own findings fragment.
6. **Tear down** its debug stack (`./debug.sh down`) on completion to free the
   slot, and remove its worktree.

### The 6 axes

Each agent owns ONE axis end-to-end across ALL surfaces (forms UI, apps/
standalone UI, Solutions install/manage page, CLI install/deploy/start/watch,
MCP/agent tools, export/import):

1. **Scope / cross-org isolation.** Forged `X-Bifrost-App` headers, foreign
   install ids, superuser-override scope pinning, cross-org table/config/
   workflow reach, S3 prefix isolation. (The #1 fear — re-verify it stays
   clean under real UI/CLI, not just the API harness.)
2. **Install / uninstall / redeploy lifecycle.** Full round-trips; redeploy
   updates; non-destructive uninstall + orphaning (`orphaned_at`, `solution_id`
   NULL'd); re-install name collisions; same-slug independence.
3. **Read-only / managed-entity enforcement.** Attempt to mutate
   solution-managed forms/agents/tables/configs through every surface (UI
   edit, CLI update, MCP tools, `/remap`, direct API). Deploy must be the sole
   writer; everything else 409s or skips.
4. **Global-repo / data-fallback behavior.** `global_repo_access` on/off →
   confirm code sealing (a sealed install cannot import `_repo/` modules).
   AND **explicitly exercise + document the ungated DATA fallback**: a sealed
   install CAN currently read `_repo/` tables/configs/storage. Produce
   evidence on whether that asymmetry actually bites (wrong data served,
   surprising cross-tier reads) — this converts the "leave ungated" decision
   (§5) from a punt into an evidence-backed call.
5. **UI/UX correctness.** Form render + submit (incl. solution-managed forms
   running the install's own workflow); standalone v2 apps; the now-inline
   `BifrostHeader` rendered STANDALONE (no platform theme — confirm it's
   styled, the fix just landed); the Solutions install/manage page; config-
   schema prompts; secret-typed config handling.
6. **CLI / docs-literalism.** Follow the PUBLISHED docs verbatim (the
   integrations-docs site + `docs/llm.txt` + CLAUDE.md recipes); try stale
   patterns a real user copying old docs would hit; `watch` refusal in a
   solution workspace (just landed); `solution start` local dev; `export
   --portable` / `import` round-trip; scaffold → deploy first-run path.

## Phase 3 — Verify pass

For each finding marked non-OK (severity ≥ low, `reproduced=true` claimed):

- Spawn an **independent verifier** (fresh agent, its own port-mode stack)
  prompted to REFUTE the finding — reproduce the exact repro steps and report
  whether it actually manifests. Default to "refuted" if it cannot reproduce.
- A finding is **confirmed** only if the verifier reproduces it. This is the
  adversarial re-verify that caught real-vs-false in prior shakeouts.
- Verifiers reuse the find→verify pipeline shape (pipeline by item, so a
  finding verifies as soon as its axis reports — no barrier).

## Phase 4 — Synthesis

One agent consumes all confirmed findings and:
- **Dedups** across axes (the same bug surfaced from multiple angles).
- **Ranks** by severity (critical/high/medium/low).
- Separates **confirmed-real** from **refuted** (refuted listed with why, so
  they're not re-investigated).
- Produces the **fix backlog**: per item, the repro, the code-ref, the
  proposed fix shape, and which prior fix/axis it relates to.
- Writes `docs/plans/2026-06-09-solutions-qa-fanout-findings.md`.

## Findings schema

Each finding (structured output, validated):

```
axis:        one of the 6
surface:     forms-ui | apps-ui | solutions-page | cli | mcp | export-import
title:       one line
did:         exact steps taken (commands, clicks, URLs)
observed:    what actually happened (with screenshot path for UI)
expected:    what should have happened
severity:    critical | high | medium | low | info
reproduced:  bool (the agent's own reproduction)
code_ref:    file:line best guess at the cause (if known)
```

## Data-fallback gating decision (folded in)

**Decision: leave data fallback UNGATED for now — by design.**

`global_repo_access` gates CODE fallback to `_repo/` (the module loader).
Data (tables/configs/storage) currently falls back to `_repo/` regardless.
We are NOT closing that asymmetry yet, because: once this resolution/gating
machine is proven by the QA pass, adding a data gate later is a mechanical
follow-the-pattern change — the flag is already carried to the engine
(`solution_global_repo_access` on the execution context), so a future change
just consults it in the table/config/storage read paths the same way
`module_cache_sync` already does for code. Gating prematurely risks baking in
the wrong shape before the pattern is battle-tested.

Axis 4 produces the evidence on whether the ungated asymmetry actually causes
problems in practice. If it does, the follow-up is small and patterned; if it
doesn't, "leave ungated" stands as a documented, evidence-backed decision
rather than a punt. Either way the README's gate-2 prose and the
chokepoint-design open-question section get updated with the verdict.

## Out of scope

- Fixing the bugs the fan-out finds — this pass PRODUCES the backlog; fixes are
  a separate cycle (some may be quick enough to fold in, decided per-finding).
- Building the data-fallback gate — deferred by decision above.
- Netbird-mode UI testing — port mode only (Chrome/netbird incompatibility).

## Risks & mitigations

- **Test/debug-stack flakiness** (the api-exit-0 flake, compose v5.1.4): each
  agent uses port-mode debug stacks (not the test stack), and the workflow
  caps concurrency at 6 so the host isn't thrashed. Agents that can't boot a
  healthy stack after N tries report BLOCKED rather than producing phantom
  findings.
- **Resource exhaustion:** 6×2.2 GB ≈ 13 GB ceiling, monitored; pre-flight
  cleanup frees headroom first. Agents tear down stacks on completion.
- **False positives:** the dedicated verify pass (Phase 3) is the mitigation.
- **Worktree/branch cleanup safety:** only `worktree-agent-*` branches MERGED
  into main are pruned; named feature worktrees are never touched.
