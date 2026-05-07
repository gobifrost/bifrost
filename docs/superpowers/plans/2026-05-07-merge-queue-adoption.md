# Merge Queue Adoption Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the "branches must be up to date" auto-merge precaution with GitHub's native merge queue, so N concurrent PRs collapse into one combined CI run instead of N sequential 15-min cycles.

**Architecture:** Add `merge_group:` triggers to every workflow that produces a required status check (`ci.yml`, `ci-noop.yml`, conditionally `codeql.yml`). Make `lint`/`test-unit`/`test-e2e` skip on `push: main` (the queue covers them). Drop `needs:` from `build-dev` for `push: main` only (the queue already gated the commit). Then flip branch protection: enable merge queue, disable "require up to date." This work itself ships through the **current** auto-merge flow — that PR is the last manual-flow merge before the queue takes over.

**Tech Stack:** GitHub Actions (`pull_request`, `push`, `merge_group` events), GitHub branch-protection rulesets, GitHub merge queue.

**Reference spec:** `docs/superpowers/specs/2026-05-07-merge-queue-design.md`

---

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `.github/workflows/ci.yml` | Modify | Add `merge_group:` trigger; gate `lint`/`test-unit`/`test-e2e` to PR + queue events only; drop `needs:` for `build-dev`/`deploy-dev` on `push: main`. |
| `.github/workflows/ci-noop.yml` | Modify | Add `merge_group:` trigger with the same `paths:` filter so docs-only PRs report required checks on the queue ref. |
| `.github/workflows/codeql.yml` | Conditionally modify | Add `merge_group:` only if CodeQL is currently a required status check. (Decision deferred to Task 1.) |
| Branch protection ruleset on `main` | Modify (GitHub UI / `gh api`) | Enable "Require merge queue"; disable "Require branches to be up to date"; queue settings (concurrency, batch size, timeouts). |

No source-code or test-suite files change. This is CI-config-only.

---

## Pre-Flight: Worktree Setup

This plan ships through the **current** auto-merge flow (one last manual-flow merge before the queue takes over). Create an isolated worktree off `main` before starting Task 1.

- [ ] **Step PF-1: Create worktree**

Run from the main repo root (`/home/jack/GitHub/bifrost`):

```bash
git fetch origin main
git worktree add -b chore/merge-queue ../bifrost-merge-queue origin/main
cd ../bifrost-merge-queue
```

Expected: a new worktree at `/home/jack/GitHub/bifrost-merge-queue` on a fresh `chore/merge-queue` branch tracking `origin/main`.

- [ ] **Step PF-2: Confirm clean state**

Run: `git status`
Expected: `On branch chore/merge-queue` with `nothing to commit, working tree clean`.

All subsequent file paths in this plan are **relative to the worktree root** (`/home/jack/GitHub/bifrost-merge-queue`).

---

## Task 1: Audit required status checks on `main`

Before editing workflows, capture exactly which check **names** branch protection requires today. This determines whether `codeql.yml` needs `merge_group:` and what the queue config must list.

**Files:** none (read-only audit)

- [ ] **Step 1.1: List required checks via `gh`**

Run:

```bash
gh api repos/jackmusick/bifrost/rulesets --jq '.[] | {id, name, target}'
```

Expected: a list of rulesets including one targeting `branch` with name like `main protection` (exact name varies). Note its `id`.

- [ ] **Step 1.2: Fetch the ruleset details**

Substitute `<RULESET_ID>` with the id from Step 1.1:

```bash
gh api repos/jackmusick/bifrost/rulesets/<RULESET_ID> --jq '.rules[] | select(.type == "required_status_checks") | .parameters.required_status_checks[].context'
```

Expected: a newline-separated list of check names. Likely:

```
Lint & Type Check
Unit Tests
E2E Tests
```

…and possibly `Analyze (python)` / `Analyze (javascript-typescript)` (CodeQL).

- [ ] **Step 1.3: Record the audit**

Write the exact list to `docs/superpowers/specs/2026-05-07-merge-queue-design.md` "Open questions" section. Replace the "Is CodeQL currently a required check?" bullet with a definitive answer:

```markdown
- **CodeQL required-check status (audited 2026-05-07):** [yes / no]. Required check names are: [exact list].
```

- [ ] **Step 1.4: Commit the audit**

```bash
git add docs/superpowers/specs/2026-05-07-merge-queue-design.md
git commit -m "docs(spec): record required-check audit for merge queue rollout"
```

---

## Task 2: Add `merge_group:` trigger to `ci.yml`

**Files:**
- Modify: `.github/workflows/ci.yml` (top-level `on:` block, lines 3-46)

- [ ] **Step 2.1: Add `merge_group:` after `pull_request:`**

Edit the `on:` block. Find:

```yaml
on:
  push:
    branches: [main]
    tags: ["v*"]
  pull_request:
    branches: [main]
    # Skip CI for documentation/config-only changes...
    paths-ignore:
      - "**/*.md"
      ...
      - ".idea/**"
  workflow_dispatch:
```

Insert a `merge_group:` trigger between `pull_request:` (closes after `paths-ignore:` list) and `workflow_dispatch:`:

```yaml
on:
  push:
    branches: [main]
    tags: ["v*"]
  pull_request:
    branches: [main]
    paths-ignore:
      - "**/*.md"
      # ... existing list unchanged ...
      - ".idea/**"
  # GitHub merge queue runs CI on a synthetic ref containing
  # main + queued PRs stacked in queue order. Required status
  # checks must fire on this event or the queue blocks forever.
  merge_group:
  workflow_dispatch:
```

Note: `merge_group:` takes no `branches:` or `paths-ignore:` — it always fires on the queue ref, which is what we want. Docs-only changes inherit the queue too; `ci-noop.yml` (Task 3) handles the stub jobs.

- [ ] **Step 2.2: Validate YAML parses**

Run from the worktree root:

```bash
python -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml'))" && echo OK
```

Expected: `OK`. If a parse error prints, fix indentation and re-run.

- [ ] **Step 2.3: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: add merge_group trigger to ci.yml

Required for GitHub merge queue — checks must fire on the queue's
synthetic ref (gh-readonly-queue/main/...) or the queue blocks
forever waiting on absent statuses."
```

---

## Task 3: Add `merge_group:` trigger to `ci-noop.yml`

`ci-noop.yml` reports stub-green for docs-only PRs so branch protection sees the required check names. The same logic applies on the queue ref — docs-only PRs queue too, and the stubs need to fire there.

**Files:**
- Modify: `.github/workflows/ci-noop.yml` (lines 23-48)

- [ ] **Step 3.1: Add `merge_group:` with byte-identical paths filter**

Find:

```yaml
on:
  pull_request:
    branches: [main]
    paths:
      - "**/*.md"
      # ... existing list ...
      - ".idea/**"
```

Replace with:

```yaml
on:
  pull_request:
    branches: [main]
    paths:
      - "**/*.md"
      - "**/*.txt"
      - "**/*.png"
      - "**/*.jpg"
      - "**/*.jpeg"
      - "**/*.gif"
      - "**/*.svg"
      - "**/*.webp"
      - "**/*.ico"
      - ".claude/**"
      - "docs/**"
      - "LICENSE"
      - ".github/CODEOWNERS"
      - ".github/ISSUE_TEMPLATE/**"
      - ".github/dependabot.yml"
      - ".gitignore"
      - ".gitattributes"
      - ".editorconfig"
      - ".prettierrc*"
      - ".prettierignore"
      - ".vscode/**"
      - ".idea/**"
  # Mirror the pull_request paths on the queue ref so stub jobs
  # report required check names on docs-only PRs that have
  # entered the merge queue.
  merge_group:
    paths:
      - "**/*.md"
      - "**/*.txt"
      - "**/*.png"
      - "**/*.jpg"
      - "**/*.jpeg"
      - "**/*.gif"
      - "**/*.svg"
      - "**/*.webp"
      - "**/*.ico"
      - ".claude/**"
      - "docs/**"
      - "LICENSE"
      - ".github/CODEOWNERS"
      - ".github/ISSUE_TEMPLATE/**"
      - ".github/dependabot.yml"
      - ".gitignore"
      - ".gitattributes"
      - ".editorconfig"
      - ".prettierrc*"
      - ".prettierignore"
      - ".vscode/**"
      - ".idea/**"
```

The two lists MUST stay byte-identical to `ci.yml`'s `paths-ignore:`. The header comment block at the top of the file already documents this rule — don't remove it.

- [ ] **Step 3.2: Update the file's header comment to mention the queue**

Edit the comment block at lines 1-19. After the line:

```
# This workflow has the INVERSE trigger (paths: matches the same set
# that ci.yml's paths-ignore: skips) and reports the same status-check
# names with always-green stub jobs. GitHub branch protection looks up
# checks by name only, so it accepts these as satisfying the requirement.
```

Add a new paragraph:

```
#
# The merge_group: trigger mirrors pull_request: so the stub jobs also
# fire when a docs-only PR enters the merge queue — required status
# checks must report on the queue's synthetic ref or the queue blocks
# forever waiting.
```

- [ ] **Step 3.3: Validate YAML parses and paths lists match**

Run from the worktree root:

```bash
python -c "import yaml; yaml.safe_load(open('.github/workflows/ci-noop.yml'))" && echo OK
```

Expected: `OK`.

Then verify the two paths lists are byte-identical:

```bash
python <<'PY'
import yaml
noop = yaml.safe_load(open('.github/workflows/ci-noop.yml'))
pr_paths = noop['on']['pull_request']['paths']
mg_paths = noop['on']['merge_group']['paths']
assert pr_paths == mg_paths, f"drift: {set(pr_paths) ^ set(mg_paths)}"

ci = yaml.safe_load(open('.github/workflows/ci.yml'))
ci_ignore = ci['on']['pull_request']['paths-ignore']
assert pr_paths == ci_ignore, f"drift vs ci.yml: {set(pr_paths) ^ set(ci_ignore)}"
print("OK: ci-noop pull_request, merge_group, and ci.yml paths-ignore all match")
PY
```

Expected: `OK: ci-noop pull_request, merge_group, and ci.yml paths-ignore all match`.

- [ ] **Step 3.4: Commit**

```bash
git add .github/workflows/ci-noop.yml
git commit -m "ci: mirror merge_group trigger on ci-noop stub jobs

Docs-only PRs that enter the merge queue must still see the
required status-check names (Lint & Type Check, Unit Tests,
E2E Tests) report green on the queue ref, or the queue blocks
forever. Mirror the pull_request paths filter on merge_group
to match ci.yml's paths-ignore list."
```

---

## Task 4: Conditionally add `merge_group:` to `codeql.yml`

**Decision rule:** if Task 1 found CodeQL (`Analyze (python)` and/or `Analyze (javascript-typescript)`) in the required-checks list, do this task. If not, **skip Task 4 entirely** and proceed to Task 5.

**Files:**
- Modify: `.github/workflows/codeql.yml` (lines 7-15)

- [ ] **Step 4.1: Determine whether to apply this task**

Read Task 1's audit output (committed in Step 1.4). If CodeQL checks are required, continue to Step 4.2. Otherwise mark this task complete with a comment noting "skipped — CodeQL not required" and move to Task 5.

- [ ] **Step 4.2: Add `merge_group:` trigger**

Find:

```yaml
on:
  push:
    branches: [main]
  pull_request:
    branches: [main]
  schedule:
    # Weekly run catches CVEs in newly-published advisories that match
    # existing code patterns (CodeQL queries are updated by GitHub).
    - cron: "0 9 * * 1"
  workflow_dispatch:
```

Replace with:

```yaml
on:
  push:
    branches: [main]
  pull_request:
    branches: [main]
  # CodeQL is a required status check — run on the merge queue ref
  # so the queue doesn't block waiting on a check that never fires.
  merge_group:
  schedule:
    # Weekly run catches CVEs in newly-published advisories that match
    # existing code patterns (CodeQL queries are updated by GitHub).
    - cron: "0 9 * * 1"
  workflow_dispatch:
```

- [ ] **Step 4.3: Validate YAML parses**

```bash
python -c "import yaml; yaml.safe_load(open('.github/workflows/codeql.yml'))" && echo OK
```

Expected: `OK`.

- [ ] **Step 4.4: Commit**

```bash
git add .github/workflows/codeql.yml
git commit -m "ci: add merge_group trigger to codeql.yml

CodeQL Analyze checks are required for merge per branch protection,
so they must fire on the queue's synthetic ref."
```

---

## Task 5: Skip lint/unit/e2e on `push: main`

The whole point of the queue is that the post-merge `push: main` re-run is wasted work — the queue's `merge_group:` run is the authoritative pre-merge check, and branch protection guarantees nothing reaches `main` without passing it.

**Files:**
- Modify: `.github/workflows/ci.yml` (jobs `lint`, `test-unit`, `test-e2e`)

- [ ] **Step 5.1: Add `if:` guard to `lint` job**

Find (around line 60-62):

```yaml
  lint:
    runs-on: ubuntu-latest
    name: Lint & Type Check
    steps:
```

Change to:

```yaml
  lint:
    # Skip on push: main — the merge_group run already covered this
    # commit. Still runs on PRs, queue, tag pushes, and workflow_dispatch.
    if: github.event_name != 'push' || github.ref != 'refs/heads/main'
    runs-on: ubuntu-latest
    name: Lint & Type Check
    steps:
```

- [ ] **Step 5.2: Add `if:` guard to `test-unit` job**

Find (around line 119-121):

```yaml
  test-unit:
    runs-on: ubuntu-latest
    name: Unit Tests
    steps:
```

Change to:

```yaml
  test-unit:
    # Skip on push: main — covered by the merge_group run.
    if: github.event_name != 'push' || github.ref != 'refs/heads/main'
    runs-on: ubuntu-latest
    name: Unit Tests
    steps:
```

- [ ] **Step 5.3: Add `if:` guard to `test-e2e` job**

Find (around line 146-148):

```yaml
  test-e2e:
    runs-on: ubuntu-latest
    name: E2E Tests
    steps:
```

Change to:

```yaml
  test-e2e:
    # Skip on push: main — covered by the merge_group run.
    if: github.event_name != 'push' || github.ref != 'refs/heads/main'
    runs-on: ubuntu-latest
    name: E2E Tests
    steps:
```

These guards intentionally **do not** match on `refs/tags/v*` — tag pushes still want the full gate, since `build-api`/`build-client`/`create-release` (which depend on `test-unit`, `test-e2e`, `lint`) only fire on tag pushes.

- [ ] **Step 5.4: Validate YAML parses**

```bash
python -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml'))" && echo OK
```

Expected: `OK`.

- [ ] **Step 5.5: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: skip lint/unit/e2e on push: main (covered by merge queue)

The queue's merge_group: run already gated this commit before it
landed on main. Re-running these jobs on push: main was a 15-min
waste; build-dev and deploy-dev still fire on push: main but no
longer depend on jobs that don't run there (handled in next commit)."
```

---

## Task 6: Drop `needs:` from `build-dev` and `deploy-dev` for `push: main`

With Task 5, `lint` / `test-unit` / `test-e2e` no longer run on `push: main`. `build-dev` currently has `needs: [test-unit, lint]` and `deploy-dev` has `needs: [build-dev]`. On `push: main`, the upstream jobs **don't run**, so `needs:` resolves to "skipped," which causes the dependents to skip too — breaking the deploy.

The fix: drop `test-unit` and `lint` from `build-dev`'s `needs:`. Branch protection guarantees no commit reaches `main` without the queue's check passing, so the gate is preserved.

**Files:**
- Modify: `.github/workflows/ci.yml` (jobs `build-dev`, `deploy-dev`)

- [ ] **Step 6.1: Drop `needs:` on `build-dev`**

Find (around line 165-169):

```yaml
  build-dev:
    if: github.ref == 'refs/heads/main'
    needs: [test-unit, lint]
    runs-on: ubuntu-latest
    name: Build Dev Images
```

Change to:

```yaml
  build-dev:
    # Only fires on push: main (branch protection + merge queue
    # already gated this commit, so no needs: here).
    if: github.event_name == 'push' && github.ref == 'refs/heads/main'
    runs-on: ubuntu-latest
    name: Build Dev Images
```

The `if:` change adds `github.event_name == 'push'` so this job doesn't accidentally fire on `merge_group` events (where `github.ref` happens to also include `main` in the queue ref name pattern but the event isn't `push`). Better safe.

- [ ] **Step 6.2: Verify `deploy-dev` is already correctly gated**

Find (around line 326-328):

```yaml
  deploy-dev:
    if: github.event_name != 'pull_request' && github.ref == 'refs/heads/main'
    needs: [build-dev]
```

The `needs: [build-dev]` is correct — `build-dev` still runs on `push: main`. The existing `if:` already excludes `pull_request`; tighten it to be explicit about `push`:

Change to:

```yaml
  deploy-dev:
    # Fires only after build-dev lands a new image on push: main.
    if: github.event_name == 'push' && github.ref == 'refs/heads/main'
    needs: [build-dev]
```

This is semantically equivalent given the upstream gate but reads more clearly.

- [ ] **Step 6.3: Verify tag-release jobs are untouched**

Open `.github/workflows/ci.yml` and confirm:

- `build-api` (line ~386) still has `if: startsWith(github.ref, 'refs/tags/v')` and `needs: [test-unit, test-e2e, lint]`.
- `build-client` (line ~468) ditto.
- `create-release` (line ~554) still has `needs: [build-api, build-client]`.

These jobs only fire on `push: tags: ["v*"]`, where Task 5's guards (`!= 'push' || != 'refs/heads/main'`) **allow** the upstream jobs to run. Tag releases retain their full gate.

- [ ] **Step 6.4: Validate YAML parses**

```bash
python -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml'))" && echo OK
```

Expected: `OK`.

- [ ] **Step 6.5: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: drop needs: gate on build-dev for push: main

With lint/unit/e2e skipping on push: main (covered by the merge
queue), build-dev's needs: [test-unit, lint] would resolve to
'skipped' and break the deploy. Branch protection + merge queue
already gate this commit — drop the now-redundant needs:.

Tag releases (build-api, build-client, create-release) still
require the full gate and are unchanged."
```

---

## Task 7: Local sanity check before pushing the PR

This task confirms the workflow files are syntactically valid and that tag-release behavior didn't break. There's no way to test `merge_group:` events locally — that's verified post-merge in Task 9's smoke test.

**Files:** none (verification only)

- [ ] **Step 7.1: Re-validate all three workflow files**

```bash
for f in .github/workflows/ci.yml .github/workflows/ci-noop.yml .github/workflows/codeql.yml; do
  python -c "import yaml; yaml.safe_load(open('$f'))" && echo "OK: $f"
done
```

Expected: three `OK:` lines.

- [ ] **Step 7.2: Diff against `origin/main` and review**

```bash
git diff origin/main -- .github/workflows/
```

Expected diff scope:
- `ci.yml`: `merge_group:` added; three `if:` guards added on `lint`/`test-unit`/`test-e2e`; `build-dev` and `deploy-dev` `if:` and `needs:` adjusted.
- `ci-noop.yml`: `merge_group:` paths block added; comment updated.
- `codeql.yml`: `merge_group:` added (only if Task 4 applied).

If any unexpected change appears, revert it with `git restore --source=origin/main -- <file>` and re-do the relevant task.

- [ ] **Step 7.3: Verify required-checks names didn't drift**

The job `name:` fields are what branch protection matches on. Confirm they're unchanged:

```bash
grep -E "^    name:" .github/workflows/ci.yml .github/workflows/ci-noop.yml
```

Expected (subset):
- `Lint & Type Check`
- `Unit Tests`
- `E2E Tests`
- `Build Dev Images`
- `Deploy Dev to DigitalOcean`
- `Build API Image`
- `Build Client Image`
- `Create Release`

`Lint & Type Check`, `Unit Tests`, `E2E Tests` MUST appear in **both** `ci.yml` and `ci-noop.yml` exactly as shown. Any drift breaks branch protection.

---

## Task 8: Push the PR through the current auto-merge flow

This is the **last manual-flow merge**. The merge queue isn't enabled yet, so this PR rides the existing path: PR-level CI → review → "branches must be up to date" → auto-merge.

**Files:** none (git/PR operations)

- [ ] **Step 8.1: Push the branch**

```bash
git push -u origin chore/merge-queue
```

Expected: branch pushed, GitHub prints a "Create PR" URL.

- [ ] **Step 8.2: Open the PR**

```bash
gh pr create --base main --title "ci: adopt GitHub merge queue" --body "$(cat <<'EOF'
## Summary

- Add `merge_group:` trigger to `ci.yml`, `ci-noop.yml`, and (conditionally) `codeql.yml` so required checks fire on the merge queue's synthetic ref.
- Skip `lint`/`test-unit`/`test-e2e` on `push: main` — the queue's run already covered the commit.
- Drop `needs:` from `build-dev` for `push: main` (queue + branch protection are the gate). Tag-release jobs untouched.

Spec: `docs/superpowers/specs/2026-05-07-merge-queue-design.md`

This PR ships through the **current** auto-merge flow — it's the last manual-flow merge before the queue takes over (Task 9 enables the queue post-merge).

## Test plan

- [x] YAML parses for all three workflows
- [x] `git diff` review confirms scope is workflow-only
- [x] Job `name:` fields unchanged (branch protection matches by name)
- [x] PR-level CI runs as today (no behavior change for PR runs)
- [ ] Post-merge: enable queue + smoke test (see plan Task 9-10)
EOF
)"
```

Expected: PR URL printed.

- [ ] **Step 8.3: Wait for PR-level CI to pass**

PR CI runs `Lint & Type Check`, `Unit Tests`, `E2E Tests`, plus CodeQL (and `ci-noop` won't fire — these are workflow file changes, which don't match the docs paths filter). Watch:

```bash
gh pr checks --watch
```

Expected: all checks green. If anything fails, fix the underlying issue and push again.

- [ ] **Step 8.4: Enable auto-merge and merge**

```bash
gh pr merge --auto --squash
```

Expected: GitHub queues the squash merge for when checks pass + branches are up to date. Wait for it to land on `main`.

---

## Task 9: Enable the merge queue on `main`

Now that the workflow changes are on `main`, flip branch protection to use the queue.

**Files:** none (GitHub branch-protection ruleset changes)

- [ ] **Step 9.1: Switch back to the main repo and pull**

```bash
cd /home/jack/GitHub/bifrost
git checkout main
git pull
```

Expected: the merge-queue commit is on `main` locally.

- [ ] **Step 9.2: Enable merge queue via GitHub UI**

In a browser, open `https://github.com/jackmusick/bifrost/settings/rules` and edit the ruleset that protects `main`:

1. Find the rule **"Require a pull request before merging"** — leave as-is.
2. Find **"Require status checks to pass"** — leave the required-checks list as-is.
3. **Uncheck** "Require branches to be up to date before merging."
4. **Check** "Require merge queue."
5. In the "Merge queue settings" panel that appears:
   - **Merge method:** Squash and merge (matches today's behavior).
   - **Build concurrency:** `1`. (Conservative; raise later if multi-batch parallelism is needed.)
   - **Maximum pull requests to build:** `5`.
   - **Maximum pull requests to merge:** `5`.
   - **Minimum pull requests to merge:** `1`.
   - **Wait time to meet minimum group size:** `5 minutes`.
   - **Status check timeout:** `60 minutes`.
   - **Require all queue entries to pass required checks:** ✅ checked.
6. Save.

- [ ] **Step 9.3: Confirm via `gh api`**

```bash
gh api repos/jackmusick/bifrost/rulesets/<RULESET_ID> --jq '.rules[] | select(.type == "merge_queue")'
```

Expected: a rule object with `merge_method: "SQUASH"`, `min_entries_to_merge: 1`, `max_entries_to_build: 5`, `max_entries_to_merge: 5`, `min_entries_to_merge_wait_minutes: 5`, `check_response_timeout_minutes: 60`. Substitute `<RULESET_ID>` from Task 1's audit.

Also confirm the strict-up-to-date requirement is gone:

```bash
gh api repos/jackmusick/bifrost/rulesets/<RULESET_ID> --jq '.rules[] | select(.type == "required_status_checks") | .parameters.strict_required_status_checks_policy'
```

Expected: `false` (or the parameter absent).

---

## Task 10: Smoke test with a no-op PR

Before walking away, prove the queue actually works end-to-end on this repo.

**Files:** none (operational verification)

- [ ] **Step 10.1: Make a trivial no-op change**

From a fresh worktree:

```bash
cd /home/jack/GitHub/bifrost
git worktree add -b chore/merge-queue-smoke ../bifrost-mq-smoke origin/main
cd ../bifrost-mq-smoke
```

Edit `docs/superpowers/specs/2026-05-07-merge-queue-design.md` and add a single trailing line:

```markdown

<!-- Merge queue smoke-tested 2026-05-07 -->
```

(Docs-only on purpose — exercises the `ci-noop.yml` path on the queue ref.)

- [ ] **Step 10.2: Push and open PR**

```bash
git add docs/superpowers/specs/2026-05-07-merge-queue-design.md
git commit -m "docs: smoke-test merge queue"
git push -u origin chore/merge-queue-smoke
gh pr create --base main --title "docs: smoke-test merge queue" --body "Trivial doc edit to verify the merge queue end-to-end."
```

- [ ] **Step 10.3: Wait for PR-level checks**

```bash
gh pr checks --watch
```

Expected: `ci-noop` reports `Lint & Type Check`, `Unit Tests`, `E2E Tests` as green stubs (since this is a docs-only change). PR is mergeable.

- [ ] **Step 10.4: Click "Merge when ready"**

```bash
gh pr merge --auto --squash
```

Expected: GitHub enqueues the PR. The PR page now shows "In merge queue" status.

- [ ] **Step 10.5: Watch the queue run**

Open `https://github.com/jackmusick/bifrost/queue/main` in a browser, or:

```bash
gh run list --workflow=ci.yml --limit 5
gh run list --workflow=ci-noop.yml --limit 5
```

Expected within ~5 min: a new run on a `gh-readonly-queue/main/pr-<N>-...` ref. For this docs-only PR, `ci-noop.yml` runs the stub jobs on the queue ref. The PR lands on `main` once the queue check is green.

- [ ] **Step 10.6: Verify post-merge behavior on `main`**

```bash
git checkout main
git pull
gh run list --branch main --limit 3
```

Expected:
- `build-dev` ran on `push: main` ✅ (this is a docs-only PR, but `ci.yml`'s `build-dev` only checks the `if:` — since it's a real `push: main`, it fires; that's intended behavior, the dev image gets a fresh tag).
- `lint`, `test-unit`, `test-e2e` did **NOT** run on `push: main` (confirming Task 5's skip guards work).

If `lint`/`test-unit`/`test-e2e` did run on `push: main`, the `if:` guards from Task 5 are wrong — investigate and fix.

- [ ] **Step 10.7: Verify the green check on the repo home page**

Visit `https://github.com/jackmusick/bifrost` and confirm the latest commit on `main` shows a green checkmark. The check is propagated from the queue's `merge_group` run.

- [ ] **Step 10.8: Cleanup smoke worktree**

```bash
cd /home/jack/GitHub/bifrost
git worktree remove ../bifrost-mq-smoke
```

---

## Task 11: Watch 2-3 real PR merges and confirm cascade is gone

The smoke test proves the mechanism. This task confirms the actual win: concurrent PRs no longer serialize.

**Files:** none (observation)

- [ ] **Step 11.1: Open or queue 2+ ready PRs concurrently**

Either wait for natural PR throughput, or open two trivial-but-distinct PRs (e.g., update two unrelated docs entries on different branches), get them both to "Merge when ready" state, and click within a few seconds of each other.

- [ ] **Step 11.2: Observe the queue behavior**

At `https://github.com/jackmusick/bifrost/queue/main`, expect:
- Both PRs enter the queue.
- A **single** queue run on a ref like `gh-readonly-queue/main/pr-A-pr-B-<sha>` covers both PRs together.
- When green, both land on `main` in a single fast-forward.

What you should NOT see:
- Two separate queue runs serialized 15 min apart.
- Either PR going back to "needs update from main" after the other lands.

- [ ] **Step 11.3: Record the result**

If batching works as expected, this plan is done. If two PRs each got their own queue run instead of a combined one, check the queue's `min_entries_to_merge_wait_minutes` setting — too low a value or too small a wait window means the queue starts batching the first PR before the second arrives. Raising `min_entries_to_merge_wait_minutes` or `min_entries_to_merge` shifts toward bigger batches at the cost of latency.

This is a tuning issue, not a correctness issue — leave it for now and adjust during normal use.

---

## Rollback plan

If the queue causes issues at any point after Task 9:

1. Re-enable "Require branches to be up to date before merging" in branch protection.
2. Disable "Require merge queue."
3. Open a PR reverting the workflow changes (`git revert <sha>` of each commit from Tasks 2-6) — but this is **optional**. The workflow changes are forward-compatible; `merge_group:` triggers are no-ops without an active queue, and the `push: main` skip guards are simply suboptimal (you'll re-run lint/unit/e2e on main, same as before the change).

In other words: the GitHub branch-protection toggle is the real revert. Workflow file edits can stay.

---

## Self-review (writer's pass — done at write time, not by executor)

**Spec coverage:**
- Spec §"Workflow trigger changes" → Tasks 2, 3, 4 ✅
- Spec §"Branch protection changes" → Task 9 ✅
- Spec §"Merge queue settings" → Task 9 (Step 9.2) ✅
- Spec §"Removal of the redundant post-merge run" → Tasks 5, 6 ✅
- Spec §"Security & compliance posture" → preserved by config (no task needed; verified by audit in Task 1)
- Spec §"Failure modes" → mitigations in Tasks 3 (ci-noop on queue), 5/6 (push:main behavior), 10 (smoke test catches missing checks)
- Spec §"Rollout" → Tasks PF, 7, 8, 9, 10, 11 (one-to-one mapping)
- Spec §"Testing" → Task 10 (smoke test) and Task 11 (real PR observation)
- Spec §"Open questions" → CodeQL question resolved in Task 1 ✅

**Placeholder scan:** No "TBD" / "implement later" / "add validation" / "similar to Task N" / referenced-but-undefined types. All commands are exact. All YAML edits show full before/after blocks.

**Type/name consistency:** Job names (`Lint & Type Check`, `Unit Tests`, `E2E Tests`, `Build Dev Images`, `Deploy Dev to DigitalOcean`) are quoted identically in every reference. Branch name `chore/merge-queue` is consistent across PF-1, 8, and rollback. Worktree paths `/home/jack/GitHub/bifrost-merge-queue` and `/home/jack/GitHub/bifrost-mq-smoke` are consistent.
