# Durable Agent Platform Backend — Overnight Execution Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the complete backend foundation for durable AgentRuns, debugger APIs/CLI, and the Agent Evaluation Studio while leaving all authored UI work to a later Astra pass.

**Architecture:** PostgreSQL owns admitted work, checkpoints, joins, timers, test definitions, and results. RabbitMQ and Redis are wake-up/notification transports only. Extend the existing Pydantic AI runner using serialized message history and deferred tool results; do not build a second agent framework. Evaluation suites run as PlatformJobs and synthetic tool execution uses a coherent per-case state store.

**Tech Stack:** FastAPI, SQLAlchemy/PostgreSQL, Alembic, RabbitMQ, Redis notifications, Pydantic AI 2.35.3, pytest, Click CLI, existing PlatformJob runtime.

---

## Read this first

- Product/architecture specification: `docs/superpowers/specs/2026-09-18-durable-agent-runtime-and-evaluation-studio-design.md`
- Runtime plan: `docs/superpowers/plans/2026-09-18-durable-agent-runtime-backend.md`
- Debugger plan: `docs/superpowers/plans/2026-09-18-agent-debugger-backend.md`
- Studio plan: `docs/superpowers/plans/2026-09-18-agent-evaluation-studio-backend.md`
- PlatformJob rules: `docs/architecture/platform-jobs.md`
- Repository instructions: `AGENTS.md`, `api/AGENTS.md`, and any nearer file before editing.

## Hard boundaries

- Work only in a new isolated worktree. Start from commit `7558ce1fe` on local branch `codex/durable-agent-runtime-design`.
- Do not edit `/home/jack/GitHub/bifrost/.worktrees/bifrost-tests-poc`; it contains unrelated uncommitted work.
- Do not author React components, routes, navigation, CSS, screenshots, or browser tests. Generated API/type artifacts required by repository checks are allowed; hand-authored UI is not.
- Do not deploy, publish, push, or change live Bifrost/Halo configuration.
- Do not replace Pydantic AI, RabbitMQ, the workflow engine, or PlatformJob.
- Do not add approval waits, multi-region scheduling, A2A, or historical replay/fork.
- Preserve existing synchronous APIs and current AgentRun detail responses. `agents.run(timeout=...)` controls caller waiting only and must never cancel server work.
- Run tests through `./test.sh`; do not use host `pytest`.

## Worktree and branch

- [ ] Create and enter the implementation worktree:

```bash
cd /home/jack/GitHub/bifrost
git worktree add .worktrees/durable-agent-platform-backend -b feature/durable-agent-platform-backend 7558ce1fe
cd .worktrees/durable-agent-platform-backend
git status --short
```

- [ ] Confirm the worktree is clean and the four documents above exist.
- [ ] Run the narrow baseline before editing:

```bash
./test.sh api/tests/unit/services/test_agent_run_service.py api/tests/unit/services/test_agent_run_consumer.py api/tests/unit/services/test_autonomous_agent_executor.py api/tests/unit/jobs/schedulers/test_execution_cleanup.py
```

- [ ] Record any pre-existing failure in the final handoff; never weaken a test to make the baseline green.

## Execution order

- [ ] Use Terra as the primary implementation agent and Luna for bounded exploration or mechanical test work. Do not start a long-running Sol review without Jack's confirmation. Never run parallel writers against overlapping files.
- [ ] Complete every task in the durable runtime plan, including migration, compatibility, failure-injection tests, and documentation.
- [ ] Complete every task in the debugger backend plan.
- [ ] Complete every task in the Evaluation Studio backend plan.
- [ ] After each green task, commit with the exact or equivalent small commit named in that task. Never accumulate the whole project into one commit.
- [ ] When a task exposes a real architectural contradiction, stop that task, write the evidence into `docs/superpowers/plans/2026-09-18-agent-platform-implementation-notes.md`, and continue only independent tasks. Do not paper over safety failures.

## Final integration gate

- [ ] Run formatter/linter/type checks required by the edited packages.
- [ ] Run DTO/contract guards after contract changes:

```bash
./test.sh api/tests/unit/test_dto_flags.py api/tests/unit/test_contract_version.py
```

- [ ] Regenerate source-of-truth artifacts if their checks require it:

```bash
python api/scripts/skill-truth/generate.py
```

- [ ] Run all new runtime, debugger, Studio, PlatformJob, event, router, and CLI tests as one explicit command.
- [ ] Run the full backend suite only after the focused gate is green:

```bash
./test.sh api/tests
```

- [ ] Inspect `git diff --check`, `git status --short`, the migration graph, and the commit list.
- [ ] Verify with searches that no authored UI files changed and no hardcoded ten-minute child timeout remains.

## Required final report

End with all of the following:

1. Branch, worktree, HEAD, and ordered commit list.
2. Delivered behavior grouped as runtime, debugger, and Studio.
3. Migration revision(s) and migration-head result.
4. Every test command with pass/fail counts; identify pre-existing failures separately.
5. Any incomplete item, safety compromise, compatibility concern, or decision that needs review.
6. Authored files versus generated artifacts.
7. A copy-ready handoff prompt using the template below.

## Mandatory handoff prompt

```text
Review and continue the durable Agent Platform backend on branch
feature/durable-agent-platform-backend in
/home/jack/GitHub/bifrost/.worktrees/durable-agent-platform-backend.

Start by reading:
- docs/superpowers/specs/2026-09-18-durable-agent-runtime-and-evaluation-studio-design.md
- docs/superpowers/plans/2026-09-18-agent-platform-overnight-master.md
- docs/superpowers/plans/2026-09-18-durable-agent-runtime-backend.md
- docs/superpowers/plans/2026-09-18-agent-debugger-backend.md
- docs/superpowers/plans/2026-09-18-agent-evaluation-studio-backend.md
- docs/superpowers/plans/2026-09-18-agent-platform-implementation-notes.md, if present

Audit the implementation against the approved invariants, inspect every commit,
run the focused and full verification gates, reproduce failures, and fix backend
defects rather than merely reporting them. Pay special attention to atomic lease
claims, checkpoint ordering, uncertain side effects, completion-event outbox
behavior, parent/child wake races, tenant authorization, synthetic-tool isolation,
and compatibility of existing AgentRun APIs.

After the backend is verified, explicitly use Astra for all UI work. Have Astra
implement the debugger and Evaluation Studio from the approved UX section using
the existing Bifrost design system and components. Do not let Astra redesign the
backend contracts casually; if a UI need exposes a contract gap, document it and
make the smallest reviewed backend correction. Add browser tests and run the
repository UI verification gates before declaring the feature complete.
```
