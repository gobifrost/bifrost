---
name: bifrost-secupdate
description: Drain the Bifrost Security tab — work through open Dependabot PRs (auto-merge eligible vs needs-review classification), Dependabot alerts (with/without PR), CodeQL alerts (severity-first triage with subagent fan-out for class-level rules), secret-scanning alerts (real vs FP). Use when user says "drain the security queue", "work through alerts", or after `/loop` triggers from bifrost-secaudit.
---

# Bifrost Security Update Loop

The action sibling of `bifrost-secaudit`. Where audit shows you what's there, secupdate moves it. Three iron rules, plus a series of explicit halt conditions, plus the actual loop.

## Iron rules (echoed from SECURITY.md auto-merge policy)

1. **Patch + minor + security-advisory** Dependabot PRs → enable auto-merge if CI green or pending. The `dependabot-auto-merge.yml` workflow already does this for new PRs; re-evaluate any PRs where the workflow may have skipped.
2. **Major bumps + Docker base-image bumps** → label `needs-review` (workflow does this), do not auto-merge. Investigate per-PR.
3. **Code-scanning real findings** → propose a fix + test. **PAUSE POINT** before pushing.
4. **Code-scanning false positives** → dismiss with a per-class rationale comment, via `gh api -X PATCH .../code-scanning/alerts/{n}`.

Halt conditions (stop and ask):

- CI fails 3× in a row on the same dep PR (the bump itself is broken upstream)
- A dep needs to be swapped for an alternative (substantial code change)
- Code-scanning finding requires a refactor across >5 files
- Major version bump for a "platform" dep (TypeScript, Python, Node, eslint, vite)
- Anything that wants to push directly to main

## When to activate

- User says "drain the queue", "work through alerts", "resolve the security tab", "secupdate", "knock these out"
- User invokes `/bifrost-secupdate`
- User has just run `bifrost-secaudit` and wants to act on the report
- Scheduled run (Phase 3 only — not yet enabled)

## When NOT to activate

- User wants a snapshot — that's `bifrost-secaudit`
- User is debugging a specific failing PR — direct edit is faster
- User has explicitly opted out for this session ("just look, don't act")

## Pre-flight

Before starting the loop, capture state:

```bash
# Branch protection — confirm checks are still required (else --auto is unsafe)
gh api repos/gobifrost/bifrost/branches/main/protection \
  --jq '.required_status_checks.contexts // [] | length'
# Must be ≥1; if 0, halt and surface ("branch protection regressed; not safe to auto-merge").

# Auto-merge workflow last run
gh run list --repo gobifrost/bifrost --workflow "dependabot-auto-merge.yml" --limit 1 \
  --json conclusion --jq '.[0].conclusion'
# If "failure", investigate before assuming new PRs will auto-merge — the workflow may need a fix first.

# All open PRs to know the working set
gh pr list --repo gobifrost/bifrost --state open \
  --json number,title,author,labels,statusCheckRollup,mergeable,createdAt
```

## The loop

### Phase A — Dependabot PRs

For each open Dependabot PR (`author == "app/dependabot"`):

```python
for pr in dependabot_prs:
    metadata = parse_title(pr.title)  # e.g., "patch", "minor", "major", "security"
    ci_state = aggregate_ci(pr.statusCheckRollup)
    has_review_label = "needs-review" in pr.labels

    # Patch / minor / security with green CI → auto-merge
    if metadata.bump in ("patch", "minor") or metadata.is_security:
        if ci_state == "green":
            if not pr.auto_merge_enabled:
                gh_pr_merge_auto(pr.number)
        elif ci_state == "failed":
            # Decide: transient (network / docker hub timeout) vs real
            if is_transient(pr.failure_logs):
                gh_run_rerun_failed(pr.failed_run_id)
            else:
                # PAUSE POINT — real failure on a minor/patch is unusual,
                # surface it. Could be a peer-dep mismatch or a bumped
                # transitive that broke our code.
                halt_and_report(pr, "minor/patch CI failure — needs investigation")
        # ci_state == "pending" → no action, GitHub's auto-merge waits

    # Major or docker → needs-review, no auto-merge
    elif metadata.bump == "major" or metadata.ecosystem == "docker":
        if not has_review_label:
            gh_pr_edit_add_label(pr.number, "needs-review")
        # Don't auto-merge. Per-PR decision happens in section B below.
```

**Specifics for failure handling:**

- **Transient indicators:** "Docker Hub", "registry-1.docker.io", "context deadline exceeded", "ETIMEDOUT", "401 Unauthorized" against actions registry. Re-run via `gh run rerun <run_id> --failed`.
- **Real-but-fixable indicators:** TypeScript errors after a typed-package bump (recharts, react-hooks, etc.), peer-dep mismatch, ESM/CJS import error. Surface details and PAUSE.
- **Real-and-blocked indicators:** ERESOLVE that no upstream version solves, package's API was rewritten in a major. Surface and PAUSE.

### Phase B — Dependabot alerts without PRs

```bash
gh api "repos/gobifrost/bifrost/dependabot/alerts?state=open" \
  --jq '.[] | {n: .number, sev: .security_vulnerability.severity, name: .dependency.package.name}'
```

For each:

1. **Has an associated open Dependabot PR?** Cross-reference the `name` field against open PRs. If yes → wait for the PR; don't act on the alert.
2. **No PR but a fix exists upstream?** This means Dependabot scheduled run hasn't picked it up yet. Either wait for the next Monday run, or trigger via comment `@dependabot recreate` on a recent PR (so it re-evaluates the manifest).
3. **No fix exists upstream yet?** **PAUSE POINT.** Surface to user with options: (a) wait, (b) workaround in code (input sanitization, etc.), (c) swap dep for an alternative, (d) accept risk + dismiss alert.

### Phase C — CodeQL alerts (severity-first)

CodeQL is the largest category. Triage in tiers:

**Tier 1 — Errors with potential systemic fixes** (parallel subagent fan-out)

For each error-severity rule with N≥3 findings, dispatch a triage subagent that:

1. Lists all findings for the rule
2. Reads 5–10 representative samples
3. Returns a verdict: DISMISS_AS_CLASS / FIX_AS_CLASS / MIXED — never takes action

```
You are triaging CodeQL alerts for rule `<RULE_ID>` in gobifrost/bifrost.

Steps:
1. List all open alerts for this rule (via gh api code-scanning/alerts pagination)
2. Read 5-10 representative samples at file:line
3. Determine verdict

Return a strict markdown report:

## Verdict: <DISMISS_AS_CLASS | FIX_AS_CLASS | MIXED>
## Rationale
<2-3 sentences>
## If FIX_AS_CLASS: proposed fix
<one-paragraph framework-level fix>
## If MIXED: per-file decisions
- file:line — fix | dismiss (reason)
## Sample evidence
<5-10 file:line excerpts with surrounding code>

Do NOT take action. Only report.
```

After all subagents return, the controller (this skill, in the main session) aggregates:

- **DISMISS_AS_CLASS verdicts:** show the user the table; on approval, run the bulk dismiss script per rule:
  ```bash
  RULE_ID="py/unsafe-cyclic-import"
  REASON="SQLAlchemy ORM relationship() lazy-eval — see verdict"
  for page in {1..15}; do
    gh api "repos/gobifrost/bifrost/code-scanning/alerts?state=open&per_page=100&page=$page" \
      --jq ".[] | select(.rule.id == \"$RULE_ID\") | .number" 2>/dev/null
  done | xargs -I {} -P 4 gh api -X PATCH \
    "repos/gobifrost/bifrost/code-scanning/alerts/{}" \
    -f state=dismissed -f "dismissed_reason=false positive" \
    -f "dismissed_comment=$REASON"
  ```
  > `dismissed_reason` must be `false positive`, `won't fix`, or `used in tests` (with spaces, not underscores). `dismissed_comment` is capped at 280 chars.
- **FIX_AS_CLASS verdicts:** dispatch an implementer subagent (separate worktree) to write the fix as its own PR. Per-rule PR, never bundled.
- **MIXED verdicts:** dismiss the FP file:line list; file an issue per real finding cluster, link to alerts.

**Tier 2 — Singleton errors and small clusters**

Rules with 1–3 findings. One subagent reads them all, returns the same verdict shape. Same aggregation.

**Tier 3 — Warnings**

Same triage shape, but the bar for "fix as class" is lower (most warnings are real low-priority cleanup). Default to MIXED — fix what's safe in a sweep, dismiss the truly noise-y ones.

**Tier 4 — Notes**

Many notes are mechanical safe cleanups (`py/unused-import`, `py/repeated-import`, `py/test-equals-none`). For these:

- Dispatch a "bulk-fix" implementer subagent that fixes ALL findings of the rule across the codebase in one PR
- Verify lint / tests pass on the fix
- Open the PR; auto-merge once CI green

For non-mechanical notes (`py/unused-global-variable` — which often points to public API surface), use the regular triage subagent + verdict aggregation pattern.

**Tier 5 — Scorecard-derived `*ID` rules**

Wait. These auto-resolve on the next weekly Scorecard run. Don't dispatch.

### Phase D — Secret-scanning alerts

```bash
gh api "repos/gobifrost/bifrost/secret-scanning/alerts?state=open"
```

For each:

1. **Real secret?** Rotate it immediately. Add a `.gitignore` entry. **PAUSE** to confirm rotation and post-rotation steps before dismissing the alert.
2. **False positive?** Common causes: `node_modules/<pkg>` ships test fixtures with placeholder credentials; CI workflow shows env-var name like `OPENAI_KEY` that gets pattern-matched as the actual key.

   Dismiss with `gh api -X PATCH /repos/.../secret-scanning/alerts/{n}` and `state=resolved`, `resolution=false_positive`. Always include a comment explaining why.

## Concrete commands library

```bash
# Snapshot all open Dependabot PRs with their CI state
gh pr list --repo gobifrost/bifrost --state open \
  --author "app/dependabot" \
  --json number,title,labels,statusCheckRollup --jq '.[] | {n: .number, title: .title, ci: [.statusCheckRollup[] | select(.name == "Lint & Type Check" or .name == "Unit Tests" or .name == "E2E Tests") | "\(.name): \(.conclusion // .status)"]}'

# Re-run failed checks on a PR (after concluding the failure was transient)
RUN_ID=$(gh pr view <N> --repo gobifrost/bifrost --json statusCheckRollup --jq '.statusCheckRollup[] | select(.conclusion == "FAILURE") | .detailsUrl' | grep -oE 'runs/[0-9]+' | head -1 | sed 's|runs/||')
gh run rerun $RUN_ID --repo gobifrost/bifrost --failed

# Trigger Dependabot rebase (when main has moved and PR is "branches up to date" gated)
gh pr comment <N> --repo gobifrost/bifrost --body "@dependabot rebase"

# Trigger Dependabot recreate (when dependabot.yml changed and PR needs new ignore rules applied)
gh pr comment <N> --repo gobifrost/bifrost --body "@dependabot recreate"

# Enable auto-merge (squash, default in this repo)
gh pr merge <N> --repo gobifrost/bifrost --auto --squash

# Bulk-dismiss CodeQL alerts for a rule (CONFIRMED VERDICT REQUIRED)
RULE_ID="<rule>"; REASON="<one sentence>"
for page in {1..15}; do
  gh api "repos/gobifrost/bifrost/code-scanning/alerts?state=open&per_page=100&page=$page" \
    --jq ".[] | select(.rule.id == \"$RULE_ID\") | .number" 2>/dev/null
done | xargs -I {} -P 4 gh api -X PATCH \
  "repos/gobifrost/bifrost/code-scanning/alerts/{}" \
  -f state=dismissed -f "dismissed_reason=false positive" \
  -f "dismissed_comment=$REASON"

# Dismiss a single CodeQL alert
gh api -X PATCH "repos/gobifrost/bifrost/code-scanning/alerts/<n>" \
  -f state=dismissed -f "dismissed_reason=false positive" \
  -f "dismissed_comment=<reason>"
```

## PAUSE POINT markers

These are the explicit places to halt and ask the user:

1. Dependabot **patch/minor PR** with **non-transient CI failure** (rare, surface details)
2. Dependabot **alert with no upstream fix** — needs a code workaround / dep swap / accept-risk decision
3. CodeQL **MIXED verdict** that proposes >5 file fixes — confirm the file list before pushing
4. CodeQL **FIX_AS_CLASS** for a rule with >50 findings — confirm the framework approach before dispatching the implementer
5. Secret-scanning **real-secret** alert — confirm rotation steps before dismissing
6. **Major version bump** on platform dep (TS, Python, Node, eslint, vite) — confirm strategy
7. **Branch protection regression** detected — halt all auto-merge, surface as critical
8. **Auto-merge workflow ≥3 failures in last 10 runs** — workflow itself may be broken; halt before relying on auto-merge

## Behavior in scheduled mode

When invoked via `/schedule` (Phase 3 of the rollout in `docs/superpowers/plans/2026-04-25-resume-oss-hardening.md`):

- **Iron rule 1** auto-merge actions are taken without user input — they're already explicit policy
- **Phases B and C and D** require human approval — DO NOT take action; instead, post a comment on a sticky issue (e.g. "Weekly security state — auto-updated") with the proposed action list and let the user respond next morning
- All PAUSE POINTS become "skip + surface for next session"
- Scheduled mode never opens new PRs without user approval — only state changes are auto-merge enabling and CodeQL bulk-dismiss for already-approved-as-class rules

This is forward-looking; until Phase 3 actually flips, ignore this section.

## What this skill does NOT do

- Push code changes directly to main — branch protection prevents it anyway, but never rely on that
- Approve PRs — only auto-merge enabling, which is GitHub-native
- Dismiss CodeQL alerts as a class without a triage subagent verdict
- Force a major version bump that requires code refactor — surface and pause
- Manage scheduled-mode rollout itself — that's a separate, manual decision
- Cover non-GitHub security surfaces (Snyk, Sonatype, etc) — out of scope
