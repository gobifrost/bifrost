---
name: bifrost-testing
description: Run and write tests for Bifrost. Use when writing or running tests; adding or modifying React components, pages, or user-facing features; debugging failing or flaky tests; before declaring UI or backend work complete. Trigger phrases - "write a test", "run tests", "add a component", "ship this feature", "ready to merge", "test is failing", "flaky", "vitest", "pytest", "playwright".
---

# Bifrost Testing

Workflow for running and writing tests in Bifrost. Covers: stack lifecycle, which command to run when, what tests a new change must include, how to handle failing and flaky tests, and when to do a UX review of new UI.

## Hard Rules (Non-Negotiable)

1. **Never wave away a known failure.** Every test selected for the change must pass. A failure discovered by a broader local or CI run must receive a durable disposition under "Known failures outside the scoped run" below; "unrelated" and "flaky" are diagnoses to investigate, not permission to ignore it.

2. **Never skip tests as a shortcut.** No `@pytest.mark.skip`, `pytest.skip()`, `test.skip()`, `test.only()`, `it.skip()`, `xfail`, or commenting a test out to silence it. A skipped test must be either **fixed** or **deleted** — and delete only if the test is genuinely no longer useful (feature removed, behavior moved, truly redundant). "I'll come back to it" is not a valid reason to skip.

3. **Flaky is a symptom, not a disposition.** Intermittent failures have previously exposed real product races as well as leaked test state. Find the cause. Do not add retries, increase timeouts, or re-run until green.

4. **No silencing.** If a test is noisy, fix the source or delete the test. Don't filter output, don't swallow the failure.

## Test Authoring Rules (Definition of Done for New Work)

### React components → sibling `*.test.tsx`

Every non-trivial React component has a sibling vitest test in the same directory: `Foo.tsx` → `Foo.test.tsx`.

- Test behavior: validation, state transitions, conditional rendering, event handlers.
- Mock hooks and external modules with `vi.mock()` at module level. Don't render the whole app.
- Use `userEvent.setup()` + `screen.getByRole()` / `getByLabel()`. No `data-testid`.
- Reference patterns: `client/src/components/applications/AppReplacePathDialog.test.tsx`, `client/src/components/workflows/WorkflowSidebar.test.tsx`.
- **Exempt:** pure presentational wrappers (`<Card>`, `<PageHeader>`, static icon components, re-exports).

### User-facing features → happy-path Playwright spec

Every user-facing feature has exactly one Playwright spec in `client/e2e/` covering the primary user journey end-to-end against live services.

- Name: `<feature>.<audience>.spec.ts` where audience is `admin`, `user`, or `unauth`.
- Happy path only. Validation errors, permission-denied paths, edge cases belong in vitest.
- Semantic selectors: `page.getByRole()`, `page.getByLabel()`, `page.getByPlaceholder()`. No `data-testid`.
- Condition-based waits: `waitForURL`, `getByRole().waitFor()`, `Promise.race([...])`. Never `page.waitForTimeout()`.

### Backend → unit and/or e2e tests

Already in `CLAUDE.md`:
- Pure logic → `api/tests/unit/`
- Anything hitting API / DB / queue / S3 → `api/tests/e2e/`

See also `authoring-rules.md` alongside this file for expanded examples.

## Workflow

### 1. Is the stack up?

```bash
./test.sh stack status
```

If `DOWN` → `./test.sh stack up`. Each worktree runs its own isolated stack (Compose project name derives from the worktree path), so two worktrees can have stacks up simultaneously without conflict.

### 2. Which test command?

- Backend logic only → `./test.sh unit` (or `./test.sh tests/unit/test_foo.py -v` for one file)
- Backend with live services → `./test.sh e2e` (or `./test.sh tests/e2e/test_foo.py -v`)
- React component behavior → `./test.sh client unit` (vitest on host, no stack needed)
- Full user flow through UI → `./test.sh client e2e`
- All available suites (manual broad run) → `./test.sh all` (backend) + `./test.sh client unit` + `./test.sh client e2e`
- Exact clean commit before opening or queueing a PR → `./test.sh pre-pr`

State is auto-reset before every test subcommand. If migrations changed, run `./test.sh stack reset` once — that rebuilds the template DB.

### 3. Before declaring implementation done: scoped verification

Run the smallest test set that gives direct evidence for the change:

- The tests added or changed with the implementation.
- Existing tests for the changed behavior and its known consumers or contracts.
- The relevant contract tripwires when DTOs, CLI/MCP surfaces, manifests, permissions, storage, scheduling, or other shared boundaries change.
- One targeted live-service or Playwright happy path when the behavior crosses that boundary.

The full backend, Vitest, and Playwright suites are **not** the default iteration loop. Targeted coverage remains mandatory because the broad gate cannot prove the changed behavior was exercised intentionally. In the handoff, list the exact targeted commands run and never imply an unrun suite is green.

Verify the authoring rules above are satisfied for any new code.

### 4. Before opening or queueing a PR: clean-commit gate

Targeted verification is necessary but not sufficient for a PR. Commit the exact candidate, make sure the worktree contains current `origin/main`, then run:

```bash
./test.sh pre-pr
```

This is mandatory for every code PR and must be rerun after any commit, amend, rebase, or merge. It refuses a dirty or stale worktree and reports the exact passing SHA. It covers every locally reproducible required PR and merge-queue boundary: repository freshness checks, production client and API builds, API/client lint and type checks, complete backend unit and E2E suites, complete Vitest, and zero-retry critical browser smoke.

GitHub remains authoritative only for boundaries a workstation cannot reproduce: the synthetic merge-queue ref, registry push, signing, attestation, repository permissions, and third-party service availability. If CI fails in a locally reproducible gate after `pre-pr` passed for the same SHA, treat that as a defect in `pre-pr` or the harness and add the exposing condition to the local gate before retrying the PR.

### 5. UX review (conditional, conversation-driven)

**Trigger:** The user is in "I just built a new UI feature, let's write the first Playwright spec and make sure the UX is solid" mode. Signal comes from conversation, not from `git diff`. If unsure, ask once.

**Process:**
1. Write the Playwright spec covering the happy path.
2. `./test.sh client e2e --screenshots <spec-file>`
3. After the spec passes, Read each screenshot under `client/playwright-results/` (or `client/test-results/` — check the Playwright config) and report layout / spacing / contrast / alignment issues.
4. Iterate: tweak the component → rerun → re-review until the user signs off.

**Skip when:** bugfixes, backend changes, routine pre-merge sanity checks. Most runs do not need a UX review.

### 6. Known failures outside the scoped run

A scoped change may be complete without running every suite. If a broader local run or CI later finds another failure, however, the failure becomes owned work and must be classified from evidence:

1. Capture the exact failure, logs, and test order or concurrency conditions. Do not start with blind reruns.
2. Run the failing test alone, then reproduce the condition that exposed it (for example, after a suspected neighbor or under the relevant concurrency). A pass in isolation does not prove the failure is harmless.
3. Choose a permanent outcome:
   - **Regression caused by the change:** fix the product and add or strengthen the scoped regression test.
   - **Real product race or nondeterminism:** fix the product boundary and preserve a deterministic regression test.
   - **Leaked test state or harness defect:** make the test own its cleanup or fix isolation at the narrowest safe layer.
   - **Overcomplicated or duplicated test:** simplify it to one stable contract, move edge cases to unit/component tests, and retain at most the useful end-to-end happy path.
   - **Obsolete or redundant signal:** delete the test and document which remaining test covers the behavior, or why the behavior no longer matters.
4. Validate the fix under the condition that previously failed. Repetition is acceptable after a hypothesized fix to demonstrate stability; it is not acceptable as a way to fish for a green run.

If the repair is bounded, make it in the current change. If it is substantial and unrelated, split it into a dedicated blocking repair change rather than expanding the feature indefinitely. The scoped feature can be reported as implemented, but it must not be merged through a red required gate and the failure must not be left as an unowned follow-up.

Diagnostics:
- Logs per service: `/tmp/bifrost-<project-name>/*.log` (per-worktree).
- JUnit: `/tmp/bifrost/test-results.xml`.
- To isolate a test, run it alone: `./test.sh tests/e2e/path/test_foo.py::TestClass::test_method -v`.

### 7. Prefer simple tests

Test complexity is a liability, not evidence of rigor. Prefer one observable contract, minimal fixtures, deterministic state, explicit cleanup, and the lowest test layer that can catch the regression. An end-to-end test should prove the primary integration or user journey, not reproduce every validation rule and edge case already covered below it.

When simplifying or deleting a test, preserve its unique behavioral signal. Do not preserve incidental implementation assertions merely because they already exist.

## Definition-of-Done Checklist

Before declaring work complete, every box must be checked:

- [ ] New non-trivial React component has a sibling `*.test.tsx`
- [ ] New user-facing feature has a happy-path Playwright spec
- [ ] Backend logic has a unit test; endpoint/workflow changes have an e2e test
- [ ] No new `skip`, `xfail`, `.only`, or commented-out tests introduced
- [ ] Targeted suite green
- [ ] `./test.sh pre-pr` green for the exact clean `HEAD` before any PR is opened or queued
- [ ] Exact test commands and unrun broader suites reported honestly
- [ ] Every known out-of-scope failure has a durable fix or a dedicated blocking repair change
- [ ] UX review done if new UI was built

If any box is unchecked, keep working. Do not declare done.

## What This Skill Is Not

Not a coverage-threshold enforcer. Not a pixel-diff tool. Not a lint for pure refactors. It's a workflow guide with hard rules on what matters: red tests, skipped tests, and missing coverage on new code.
