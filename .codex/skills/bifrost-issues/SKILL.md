---
name: bifrost-issues
description: Track work on Bifrost via GitHub Issues + isolated worktrees, AND own the PR/merge lifecycle (opening, queuing auto-merge, watching CI + reviews). Use when the user expresses work intent ("let's build/fix/add X", "work on Y"), pastes a list of todos/notes to triage, asks about existing issues, OR you (or the user) are about to open a PR, queue auto-merge, or merge a PR on this repo. `main` uses GitHub's native merge queue — `gh pr merge <N>` (no strategy flag) enqueues the PR; the queue rebases, runs checks once on a combined ref, and merges when green. Invoke this skill BEFORE running `gh pr create` or `gh pr merge`, not after. Light-touch on issue/work-intent triggers — nudges and helps, never blocks. Trigger phrases - "let's build", "let's fix", "work on", "add a feature", "triage", "todo", "what should I work on", "open a PR", "create an issue", "merge this PR", "queue auto-merge", "ship it", "help wanted".
---

# Bifrost Issues + Worktrees

All trackable work on `gobifrost/bifrost` lives in **GitHub Issues**, and all non-trivial work happens in **isolated git worktrees** under `.worktrees/`. This skill owns both halves: issue creation/triage and worktree setup/teardown. One skill because the triggers are identical — the moment the user expresses work intent on a non-trivial change, both pipelines fire.

## Core Principles

1. **Light-touch, never block.** Nudge once, respect the answer. If the user skips, proceed without guilt.
2. **Show drafts before filing.** Never call `gh issue create` without showing the drafted body first.
3. **GitHub conventions only.** Use `assignee`, `help wanted`, `good first issue` — not custom status/priority/milestone labels.
4. **Trivial edits don't need issues or worktrees.** See the trivial-edit definition below.
5. **Worktrees are the default for non-trivial work.** Working directly on `main` is a smell — the user keeps `main` clean to pull updates without conflicting with in-progress work.
6. **Debug stacks are per-worktree.** Each worktree gets an isolated `./debug.sh` stack (Compose project derived from the worktree path). See the `bifrost-debug` skill for boot/teardown.

## When to Activate

Activate on any of these:

- User expresses work intent: "let's build/add/fix X", "I want to X", "work on X", "implement X"
- User pastes a list of todos, notes, or ideas for triage
- User is about to open a PR (proactively check for issue linkage and worktree isolation)
- User asks about existing issues or what to work on next
- User wants to label an issue as `help wanted` or `good first issue`
- User asks "what now" or "is it merged" on a PR they just opened, or asks to wait/watch CI on their own PR — see step 7 below; this skill owns the open-to-merge handoff

Do **not** activate for:

- Trivial edits (see below)
- Pure questions about code/architecture with no intent to change anything
- Debugging sessions where the scope isn't yet clear (wait until it is)

## The Trivial-Edit Exception

A change is **trivial** and does not need an issue or worktree if it meets any of these:

- Typo fix
- Comment-only change
- Formatting or whitespace-only change
- ≤3-line edit to a single file with no behavior change
- Rename that doesn't cross a public API boundary

Anything else — multi-file, multi-line, new behavior, test changes, migrations, dependency bumps — is **non-trivial** and triggers the full flow.

## Flow: From Work Intent to PR

### 1. Issue first

Before any code, before any worktree, get an issue number.

Search for existing issues:

```bash
gh issue list --search "<2-3 key terms>" --state all --limit 5
```

If a plausible match exists, surface it: "This looks related to #N — is that the same thing, or a new one?"

If none, ask once: "Is there an issue for this? I can create one, or skip if it's trivial."

When drafting an issue body:

- Pick the template (`bug`, `feature`, `chore`) from the work description.
- Fill every field from conversation context; ask the user only for gaps.
- Show the draft (title + body + labels + assignee) before filing.
- On approval, file via `gh issue create --title "..." --body "..." --label "..." --assignee "@me"` (self-assign only if the user is doing the work).

### 2. Worktree setup

Once the issue exists:

> **CRITICAL: Use absolute paths in every Bash call.** The Bash tool's CWD leaks between calls — once you `cd` somewhere (e.g. for `npm ci`), subsequent calls silently inherit that CWD. **Always** write the full worktree path explicitly. Don't trust `cd <worktree>` to stick.

```bash
# Preflight: make sure main isn't stale
git fetch origin main
git log --oneline main..origin/main   # if non-empty, warn and offer to pull

# Create the worktree (absolute path)
git worktree add -b <issue-num>-<short-slug> \
  /home/jack/GitHub/bifrost/.worktrees/<issue-num>-<short-slug> origin/main

# Node deps in the worktree (needed for vitest/tsc/lint).
# This `cd` will leak into later Bash calls — keep using absolute paths after.
cd /home/jack/GitHub/bifrost/.worktrees/<slug>/client && npm ci
```

**Conventions:**

- Branch name and worktree dir use `<issue-num>-<short-slug>`. Slug is hyphenated, ≤40 chars.
- `.worktrees/` is already gitignored in this repo — verify once, warn if not.
- Base from `origin/main`, not local `main` (local can be stale; origin is the shared truth).
- **Do not copy `.env` / `.env.test` files** — `./test.sh` and `./debug.sh` find them on their own. Copying creates drift between worktrees and is a footgun.

### 3. Migration-drift check

Migrations are run by the `bifrost-init` container against a single Postgres shared across worktrees. Before starting work in the new worktree:

```bash
# Any new migrations on main that this branch doesn't have?
git diff --stat origin/main -- api/alembic/versions/
```

**Do NOT compare migration folders with `ls | tail`** — a `__pycache__` directory in one but not the other silently skews the result. Use `git diff --stat` or `ls api/alembic/versions/*.py`.

If main has migrations the branch lacks, warn: "main has migrations ahead of this branch — merge or rebase before running the stack, otherwise the DB schema won't match your code." **Don't auto-rebase.**

### 4. Dev stack coordination

Debug stacks are per-worktree — `./debug.sh` derives its Compose project name from the worktree path, so two worktrees running their own stacks don't conflict. See the `bifrost-debug` skill for the lifecycle (boot, status, teardown, two-mode behavior).

**Most worktrees don't need a debug stack.** The test stack (`./test.sh stack up`) is already per-worktree and is enough for unit/E2E coverage. Type generation can extract OpenAPI from the test-stack's API container. Only invoke the `bifrost-debug` skill when you actually need to click around the UI in this worktree.

### 5. Doing the work

- Make the change in the worktree, not in main.
- Use `./test.sh stack up` once per worktree, then `./test.sh` many times.
- Run `./test.sh`, `pyright`, `ruff`, `npm run tsc`, `npm run lint`, `./test.sh client unit` before claiming done (CLAUDE.md's verification checklist).
- `pyright`/`ruff` require a repo-root `.venv`: `python -m venv .venv && ./.venv/bin/pip install -r requirements.txt pyright ruff` (matches `.github/workflows/ci.yml`).

### 6. PR linkage

When opening the PR:

1. Scan for issue numbers in: branch name, commit messages, conversation. The branch-name convention `<issue-num>-<slug>` should yield one automatically.
2. Ensure the PR body includes `Fixes #N` (use `Closes #N` for non-bug issues; GitHub auto-closes the issue on merge either way).
3. If multiple issues are addressed, one `Fixes #N` line per issue.

### 7. Hand off to merge (auto when safe, else watch CI)

The PR isn't done at "opened." Carry it through to merged. The path depends on whether `main` has branch protection with required status checks — `--auto` is only safe when GitHub will refuse to merge until checks pass.

**Capability check (do this once, immediately after opening the PR):**

```bash
# Detect required-checks gating. ≥1 → protection exists; 0 / 404 → no protection.
gh api repos/gobifrost/bifrost/branches/main/protection \
  --jq '.required_status_checks.contexts // [] | length' 2>/dev/null
```

If the count is ≥1, `--auto` is safe. Otherwise it isn't (a `--auto` merge would fire the moment GitHub considers the PR mergeable, which without required checks is *immediately*).

**Watch reviews and CI together — `gh pr checks` alone will miss code review comments.**

Once a PR is open, three independent signals can come in: CI check transitions, code-review comments (CodeQL bot, human reviewers, copilot/Anthropic), and merge-state changes. The watcher must cover all three. Don't just `tail -f` `gh pr checks` — that silently misses CodeQL findings until they're already old, and an auto-merge queued PR can sit forever blocked on an "actionable" review you never noticed.

**Combined watcher pattern (use this, not a checks-only loop):**

```bash
# Hash reviews + check states + comments together; emit only on change.
# Terminal condition is INTENTIONALLY narrow: only state==MERGED|CLOSED.
# Don't try to be clever with "BLOCKED + all checks done" — mergeStateStatus
# is BLOCKED from the moment a PR opens against a protected branch, and
# `statusCheckRollup` may briefly contain entries with empty conclusion+state
# while checks are being scheduled, which makes the "all done" predicate
# fire prematurely. Watch until merge or close; the user can interrupt.
prev=""
prev_c=""
while true; do
  s=$(gh pr view <N> --repo gobifrost/bifrost \
        --json reviews,statusCheckRollup,reviewDecision,mergeStateStatus,state 2>/dev/null) || { sleep 60; continue; }
  c=$(gh api repos/gobifrost/bifrost/pulls/<N>/comments --jq '.[] | "\(.user.login):\(.id):\(.path):\(.line):\(.body|gsub("\n";" ")|.[0:100])"' 2>/dev/null | sort)
  cur=$(printf '%s\n%s' "$s" "$c" | sha256sum | cut -d' ' -f1)
  if [ "$cur" != "$prev" ]; then
    echo "=== $(date -u +%H:%M:%S) PR <N> update ==="
    jq -r '"  reviewDecision: \(.reviewDecision // "(none)")\n  mergeStateStatus: \(.mergeStateStatus)\n  state: \(.state)\nchecks:", (.statusCheckRollup[] | "  \(.name // .context // "?"): \(.conclusion // .state // "queued")"), "reviews:", (.reviews[] | "  \(.author.login) \(.state)")' <<<"$s"
    if [ -n "$prev_c" ] && [ "$c" != "$prev_c" ]; then
      echo "NEW review comments:"
      diff <(echo "$prev_c") <(echo "$c") | grep '^>' | head -10
    fi
    prev_c=$c
    prev=$cur
  fi
  st=$(jq -r '.state' <<<"$s")
  case "$st" in MERGED|CLOSED) echo "PR is $st"; break ;; esac
  sleep 60
done
```

**When a review comment lands** (CodeQL, copilot, human): pull the body and address it before merging. CodeQL findings on this repo's pre-push hook are usually real — see `feedback_codeql_friendly_idioms.md` in memory. Never queue `--auto` and walk away from a fresh review.

**Path A — protection exists (preferred for ship-when-green):**

1. Confirm with the user once ("Queue it through the merge queue so it ships when CI is green?").
2. On approval: `gh pr merge <N> --repo gobifrost/bifrost` (NO strategy flag — the queue dictates squash; passing `--squash` is rejected with "The merge strategy for main is set by the merge queue"). Enqueuing IS the auto-merge-when-green: the queue owns the rebase, runs checks once on a combined ref, and merges when green. (`--auto` is accepted if you want gh to wait for the PR to be mergeable before enqueuing, but NOTE the queue is the actual gating mechanism — you don't need it.)
3. **Arm the combined watcher above.** Enqueuing does not exempt you from picking up review comments — CodeQL still fires after queueing, and the queue will not merge past a `BLOCKED`/`DIRTY` mergeStateStatus.
4. If a review is required (`required_pull_request_reviews` is set) and the user isn't admin, surface that the merge is gated on approval and offer to request reviewers via `gh pr edit <N> --add-reviewer <login>`. Don't pick reviewers unprompted.

**Path B — no protection (fallback):**

1. Arm the combined watcher above.
2. **If CI fails or a review comment lands:** stay in the existing worktree on the existing branch — fixes are additional commits to `<issue-num>-<slug>`, not a fresh worktree. The PR auto-updates on push. New worktree only if the branch is being abandoned entirely.
3. **If CI passes and reviews are clean:** check `gh pr view <N> --json viewerCanAdminister,reviewDecision,mergeStateStatus` to decide.
   - `viewerCanAdminister: true` → self-merge: confirm once ("CI's green and reviews are clean — merge?"), then `gh pr merge <N> --squash --delete-branch=false`.
   - Otherwise → contributor needs review: surface reviewer-request as in Path A step 4.
4. **Never `--auto` on Path B.** Without required checks, it would fire before CI completes.

**Both paths:**

- Don't merge unprompted. "Merge?" or "queue auto-merge?" is the user's decision — confirm, never assume.
- If CI is already failing on the PR when you reach this step, surface the failure and don't offer merge until it's fixed.
- **Review comments are not optional.** A `gh pr checks` watcher that doesn't also poll reviews/comments will leave the user thinking nothing is happening while CodeQL has been waiting on you for 20 minutes.

### 8. Cleanup (after merge)

After the PR is merged, offer (do not run unprompted):

```bash
git worktree remove .worktrees/<issue-num>-<slug>
git branch -d <issue-num>-<slug>
git push origin --delete <issue-num>-<slug>   # if --delete-branch=false was used at merge time
```

Then `cd` back to the main checkout (`/home/jack/GitHub/bifrost`) so subsequent commands target main and not a stale worktree path.

## Behavior: Batch Triage

When the user pastes a list of todos, notes, or ideas:

1. **Parse and sort** each item into `bug`, `feature` (→ `enhancement` label), `chore`, or `idea`.
2. **Draft each** as an issue with a good title and a template-shaped body. For `idea` items too vague to fit `feature.yml`, use a blank issue with the `idea` label.
3. **Present the batch** grouped by type:
   ```
   ## Bugs (3)
   1. [title] — [one-line summary]
   2. ...

   ## Features (2)
   ...
   ```
4. **Ask before filing**: "File all of these? Any to drop or edit? Assign yourself to any?"
5. **On approval**, file each via `gh issue create`. Print the resulting issue numbers.

Do not auto-create worktrees for batch-triaged issues — the user is cataloging, not starting work.

## Behavior: Label & Assignment Management

On user request:

- **Mark as takeable:** `gh issue edit N --add-label "help wanted"`
- **Mark as beginner-friendly:** `gh issue edit N --add-label "good first issue"`
- **Self-assign:** `gh issue edit N --add-assignee "@me"`
- **Assign to another contributor:** `gh issue edit N --add-assignee <login>`

Do **not** add priority labels, milestones, or status labels.

## Escape Hatches

- User says "skip issue", "just do it", "no issue needed" → step aside for that request.
- User says "work in main", "don't worktree this" → respect, but warn once about the `main` conflict risk.
- `gh` not authenticated → surface the error and offer to proceed without an issue. Don't block the real work.
- User indicates the change is actually trivial after you prompted → drop the nudge immediately.

## The `gh issue create` Pattern

```bash
gh issue create \
  --title "[bug]: <summary>" \
  --body "$(cat <<'EOF'
## Summary
...

## Steps to reproduce
...

## Expected behavior
...

## Actual behavior
...

## Environment
...

## Notes
- File paths and line numbers where relevant
- Proposed approach if known
EOF
)" \
  --label "bug" \
  --assignee "@me"   # only if user is doing the work
```

Mirror the relevant template in `.github/ISSUE_TEMPLATE/`. Use `[bug]:`, `[feature]:`, or `[chore]:` title prefixes.

## What This Skill Does NOT Do

- Block any action. All nudges are cancellable.
- File issues without showing the draft first.
- Auto-rebase / auto-merge migration drift — only warns.
- Auto-stop a `./debug.sh` in another worktree — user controls that manually.
- Manage milestones, projects, priority, or status labels.
- Touch closed issues (reopening is a human decision).
- Work across repos — scoped to `gobifrost/bifrost`.
