# GitHub Merge Queue Adoption

**Date:** 2026-05-07
**Status:** Draft, awaiting review
**Owner:** Jack

## Problem

With auto-merge enabled and branch protection requiring "branches must be up to date with main," a queue of N ready PRs serializes into N sequential 15-minute CI cycles plus a redundant post-merge run on `main` itself. Each merge invalidates every other open PR's "up to date" status, so the next PR has to update-from-main and re-run CI before it can merge. Two PRs ready at the same time = ~30 min. Three = ~45 min. Plus `ci.yml` runs again on the merge commit before `build-dev` and the DigitalOcean deploy fire.

In practice main rarely breaks once a PR has cleared its own CI, CodeQL comments, and security checks. The strict "up to date" gate is mostly precaution, and the cost of that precaution scales linearly with concurrent PR throughput.

## Goal

Compress N queued PRs into roughly **one** combined CI run instead of N sequential runs, without weakening the safety gate that keeps main green.

Non-goals:

- Loosening required status checks.
- Changing CodeQL, Scorecard, Dependabot, secret scanning, or signed-image posture.
- Touching `dependabot-auto-merge.yml` semantics — Dependabot PRs continue to auto-merge once their checks pass; they enter the queue like any other PR.

## Solution

Adopt **GitHub's native merge queue** for `main`. The queue:

1. Accepts a PR once its own required checks are green (the PR-level run, same as today).
2. Builds a synthetic ref `gh-readonly-queue/main/pr-<a>-pr-<b>-...` containing `main + PR A + PR B + PR C` stacked in queue order.
3. Runs the required checks **once** on that combined ref via the `merge_group:` event.
4. If green, fast-forwards `main` to the tip of the queue ref atomically. If red, the offending PR is kicked from the queue and the rest re-batch.

Net effect for N queued PRs: **N PR-runs (parallel) + 1 queue-run (serial)**, instead of today's N × (PR-update + PR-final + main-rerun).

The post-merge run on `main` is removed because the queue's combined-ref check is *already* a check against a main-equivalent tree. GitHub propagates the queue check back to the resulting `main` commit for status display.

## Architecture

### Workflow trigger changes

Every workflow that produces a **required status check** must add `merge_group:` to its triggers. Without this, the queue blocks forever waiting for a check that never fires on the queue ref.

| Workflow | Current triggers | New triggers | Reason |
|---|---|---|---|
| `ci.yml` | `push: [main, v*]`, `pull_request`, `workflow_dispatch` | + `merge_group:` | `Lint & Type Check` and `Unit Tests` are required checks. **Drop `push: branches: [main]`** — replaced by the queue run. Keep `push: tags: ["v*"]` for releases. |
| `ci-noop.yml` | `pull_request` (paths) | + `merge_group:` (paths) | The docs-only stub jobs must report on the queue ref too, otherwise docs-only PRs queue but never finish. The `paths:` filter stays byte-identical to `ci.yml`'s `paths-ignore:`. |
| `codeql.yml` | `push: [main]`, `pull_request`, schedule | + `merge_group:` *if* CodeQL is a required check | If CodeQL is required for merge, it must run on the queue ref. If it's only required at the PR level, no change. (Verify in branch protection settings during rollout.) |
| `dependabot-lockfile-regen.yml` | `pull_request` (paths) | unchanged | Not a required check. Operates on the PR before it queues. |
| `dependabot-auto-merge.yml` | `pull_request_target` | unchanged | Enables auto-merge on Dependabot PRs; queue takes over once CI is green. |
| `scorecard.yml` | `branch_protection_rule`, schedule | unchanged | Not gated on merge. |

### Branch protection changes

On the `main` ruleset:

- **Enable** "Require merge queue."
- **Disable** "Require branches to be up to date before merging." The queue's combined-ref run is the new "up to date" semantic — stronger than the old one because it tests the literal tree that lands on main.
- Keep "Require status checks to pass" with the same checks listed (`Lint & Type Check`, `Unit Tests`, `E2E Tests` if currently required, CodeQL if currently required).
- Keep "Require pull request before merging" and the existing review requirements.
- Keep "Block force pushes" and "Restrict deletions."

### Merge queue settings

- **Merge method:** Squash (matches current default) or whatever the repo uses today; preserve existing behavior.
- **Build concurrency:** Start at 1 (one queue run at a time, simplest mental model). Can raise to 5 later if throughput demands; raising it lets multiple batches run in parallel at the cost of more runner minutes when the head of the queue fails.
- **Maximum PRs to merge per group:** 5 (default). Caps how many PRs combine into a single batch.
- **Maximum / minimum wait:** Default (5 min max, 5 min min). Keeps small batches from waiting forever for more PRs.
- **Status check timeout:** 60 min (well above the 15-min CI runtime, gives slack for queued runners).

### Removal of the redundant post-merge run

Today, `ci.yml`'s `push: branches: [main]` trigger causes `Lint`, `Unit Tests`, `E2E Tests` to re-run on the merge commit before `build-dev` fires. With the queue, the `merge_group:` run is the authoritative pre-merge check, so this re-run is pure waste.

**Approach:** keep `ci.yml`'s `push: branches: [main]` trigger, but make `lint`, `test-unit`, `test-e2e` jobs **skip** on `push` events to main (since the queue already ran them). `build-dev` and `deploy-dev` keep firing on `push` to main as today, with their `needs: [...]` dropped (the queue already gated the commit). Tag-release jobs (`build-api`, `build-client`, `release`) are unaffected — they trigger on `push: tags: ["v*"]` and still need their gate jobs to run on the tag ref.

```yaml
# Sketch — final form decided during implementation
jobs:
  lint:
    if: github.event_name == 'pull_request' || github.event_name == 'merge_group'
    ...
  test-unit:
    if: github.event_name == 'pull_request' || github.event_name == 'merge_group'
    ...
  test-e2e:
    if: github.event_name == 'pull_request' || github.event_name == 'merge_group' || startsWith(github.ref, 'refs/tags/v')
    ...
  build-dev:
    if: github.event_name == 'push' && github.ref == 'refs/heads/main'
    needs: []  # queue already gated this commit
    ...
```

The green checkmark on the repo's main page reads from the latest commit's check runs. GitHub attributes the queue's check to the resulting main commit, so the green check still appears without an explicit re-run.

### Dev image safety

Concern: prod auto-pulls `:dev` on every successful main build. With the queue, `build-dev` only fires after a green queue run, which is *strictly* a stronger guarantee than today (today's `push: main` re-run can finish red after an already-merged commit). No regression here.

## Security & compliance posture

| Control | Effect |
|---|---|
| Branch protection (required status checks) | Preserved. Queue requires it. |
| OpenSSF Scorecard "Branch-Protection" | Neutral-to-positive. Queue enforces stricter "up to date" semantics than the manual setting it replaces. |
| OpenSSF Scorecard "CI-Tests" | Unaffected — checks still run, on a stronger ref. |
| OpenSSF Scorecard "Pinned-Dependencies" | Unaffected. |
| CodeQL | Unaffected on PRs. Add `merge_group:` only if CodeQL is currently a required check. |
| Dependabot auto-merge | Unaffected. Dependabot PRs enter the queue like any other PR. |
| Dependabot alerts / lockfile regen | Unaffected. |
| Secret scanning | Unaffected. |
| Signed images (cosign) + provenance attestations | Unaffected — `build-dev` still runs on `push: main` with OIDC. |
| Direct pushes to main | Still blocked by branch protection. |

No score should regress. The "up to date" requirement going away is replaced by a stronger queue-level check, which reads as **more** rigorous to Scorecard's branch-protection scoring, not less.

## Failure modes

1. **Queue blocks forever waiting on a missing check.** Cause: a required check workflow doesn't have `merge_group:` in its triggers. Fix: audit required checks against workflow triggers before flipping the queue on.
2. **`ci-noop.yml` doesn't fire on the queue ref.** Branch protection sees the named check missing on docs-only PRs, queue stalls. Fix: `merge_group:` trigger added with the same `paths:` filter.
3. **Queue run fails because of a flaky test.** Same blast radius as today (a flaky test fails the post-merge main run). Difference: the queue *kicks the offending PR* and re-batches the rest, instead of leaving main red. Strictly better.
4. **Combined batch exposes a semantic conflict.** This is exactly what the queue is designed to catch. The conflicting PR is kicked, fix-up commit pushed, re-queues. No drama on main.
5. **Long queue during heavy PR days.** Bounded by `merge_group_max_entries_to_build` (5). At worst, batches serialize at 1 batch per 15 min. Still strictly better than today's per-PR serialization.

## Rollout

Single-PR change. No staged rollout needed because the queue is a binary flip — easy to revert.

This work itself ships through the new flow's predecessor: a git worktree branched off `main`, edits made there, PR opened, merged via the **current** auto-merge process (the queue isn't enabled yet). That last manual merge is what flips the lights on; from the next PR onward, everything goes through the queue.

1. **Create an isolated worktree** off `main` for the workflow edits (per the project's `using-git-worktrees` flow).
2. **Audit required checks** in branch protection. Note exactly which check names are required (likely `Lint & Type Check`, `Unit Tests`, `E2E Tests`; possibly CodeQL).
3. **Edit workflows:**
   - Add `merge_group:` to `ci.yml` and `ci-noop.yml`.
   - Add `merge_group:` to `codeql.yml` *only if* CodeQL is in the required-checks list.
   - Update `if:` guards on `lint`, `test-unit`, `test-e2e` so they don't run on `push: main` (the queue covers it).
   - Update `if:` guards / `needs:` on `build-dev` and `deploy-dev` so they fire on `push: main` without depending on jobs that no longer run there.
4. **Open the PR** from the worktree branch. CI on the PR itself runs as usual.
5. **Merge the PR** via the current auto-merge process (last manual-flow merge before the queue takes over).
6. **Enable the merge queue** on the `main` ruleset, disable "Require branches to be up to date."
7. **Smoke test:** open a tiny no-op PR, click "Merge when ready," confirm it queues, runs CI on a `gh-readonly-queue/...` ref, lands on main, and `build-dev` fires.
8. **Watch the next 2-3 PR merges** to confirm the cascade is gone.

Rollback: re-enable "Require branches to be up to date," disable merge queue. Workflow changes are forward-compatible — `merge_group:` triggers are no-ops without a queue.

## Testing

No automated tests for this change — it's CI-config-only. The verification is the smoke test in step 7 above plus observing the next few real PRs.

What we're looking for:

- Queue check runs on the `gh-readonly-queue/...` ref.
- Required checks all report green on the queue ref.
- `main` commit shows a green check propagated from the queue.
- `build-dev` fires on `push: main` after the queue lands the commit.
- No `Lint & Type Check` / `Unit Tests` / `E2E Tests` re-run on `push: main`.
- Multiple PRs queued at once batch into a single combined run.

## Audit results (2026-05-07)

Branch protection ruleset id `15329014` ("Protect Branch") on `main`:

- Required status checks: `Lint & Type Check`, `Unit Tests`, `E2E Tests`. **CodeQL is NOT a required check** → no edit needed for `codeql.yml` (Task 4 of the plan is skipped).
- `strict_required_status_checks_policy: true` — this is the "branches must be up to date" toggle that causes the cascade. Disabling it is part of the rollout (Task 9, Step 9.2).

## Open questions

- **Build concurrency: 1 or higher?** Starting at 1 for predictability. Can raise later if multiple batches per day become common.
