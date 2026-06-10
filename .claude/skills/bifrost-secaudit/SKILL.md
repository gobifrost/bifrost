---
name: bifrost-secaudit
description: Snapshot the current Security tab state for gobifrost/bifrost — open Dependabot alerts (counts by severity + ecosystem), open Dependabot PRs (counts by category + needs-review status), open CodeQL alerts (severity + top rules), open secret-scanning alerts, current Scorecard, stale PRs (>14 days). Markdown report. Use when user asks "where do I stand on security", "what's in the queue", or invokes /bifrost-secaudit.
---

# Bifrost Security Audit

A read-only snapshot of the repo's current security posture. Always safe to run; never modifies anything; output is a single markdown report. Pairs with `bifrost-secupdate` (which actually drains the queue).

## When to activate

- User says "audit", "where do we stand on security", "what's in the queue", "Scorecard score?", "any new CVEs?"
- User invokes `/bifrost-secaudit`
- Beginning of a session where security work is the focus
- Scheduled run (via `/schedule`) — output goes to a sticky issue or Slack

## When NOT to activate

- User wants to take action — that's `bifrost-secupdate`
- User wants to triage a specific alert ID — direct gh-api call is fine
- User wants Scorecard *changes* over time — needs a separate trend skill

## The audit

Run all sections. Each is one or two `gh api` calls; all are read-only. Every count gates the next section's emphasis (e.g. if CodeQL count is huge, surface only top rules in summary). Any failure in one section just notes "skipped" and continues — never fail the whole audit because one API is slow.

### 1. Open PRs (categorize)

```bash
gh pr list --repo gobifrost/bifrost --state open --json number,title,author,labels,createdAt --limit 50
```

Bucket each:
- **Dependabot patch/minor** (auto-merge eligible) — `author == "app/dependabot"`, no `needs-review` label, title contains a patch/minor bump indicator
- **Dependabot major** (review required) — `app/dependabot` + `needs-review` label
- **Community** — non-bot author
- **Stale** — `now - createdAt > 14 days`

Output: counts per bucket, with the top 3 oldest in each.

### 2. Dependabot alerts

```bash
gh api "repos/gobifrost/bifrost/dependabot/alerts?state=open" --jq '[.[] | {sev: .security_vulnerability.severity, eco: .dependency.package.ecosystem, name: .dependency.package.name}]'
```

Aggregate:
- Count by severity (critical / high / moderate / low)
- Count by ecosystem (npm / pip / docker / actions)
- Top 5 by severity * recency

For each: is there an open Dependabot PR for this dep? Cross-reference by name — that gates whether it's "alert needs action" vs "alert waiting on PR."

### 3. CodeQL alerts (paginated — required)

CodeQL pagination is essential. The single-page `?per_page=100` only ever returns 100; this repo currently has ~1000+ alerts.

```bash
for page in {1..15}; do
  gh api "repos/gobifrost/bifrost/code-scanning/alerts?state=open&per_page=100&page=$page" \
    --jq '.[] | "\(.rule.severity) | \(.rule.id)"' 2>/dev/null
done | sort | uniq -c | sort -rn > /tmp/codeql-summary.txt
```

Aggregate:
- Total count
- Severity breakdown (error / warning / note)
- Top 15 rule IDs by count
- Flag any `*ID` rules — these are Scorecard-derived and shouldn't be in the active triage list (they'll auto-resolve on the next Scorecard run)

### 4. Secret-scanning

```bash
gh api "repos/gobifrost/bifrost/secret-scanning/alerts?state=open"
```

Per-alert: secret type, where detected, masking status. Should always be 0 for a healthy repo. Surface every one.

### 5. Scorecard

```bash
# Latest Scorecard run
gh run list --repo gobifrost/bifrost --workflow "scorecard.yml" --limit 1 \
  --json conclusion,createdAt,headSha
```

Note the score and the most-recent-run date. If the run is older than 14 days, flag.

### 6. Stale PRs (>14 days)

Already collected in section 1; re-surface here for emphasis.

### 7. Auto-merge workflow health

```bash
gh run list --repo gobifrost/bifrost --workflow "dependabot-auto-merge.yml" \
  --limit 10 --json conclusion,createdAt,headBranch
```

Count failures in last 10 runs. ≥2 failures = surface as a warning ("auto-merge workflow appears unhealthy").

### 8. Branch protection (sanity check)

```bash
gh api repos/gobifrost/bifrost/branches/main/protection \
  --jq '{required_checks: .required_status_checks.contexts, strict: .required_status_checks.strict}'
```

Should always return ≥3 required checks. If empty or missing, flag as a critical regression.

## Output format

Single markdown report. Total counts at top, then each section. Keep it scannable — this is meant to be glanced at in the morning, not deeply read. **No prose paragraphs; tables and bullets.**

```markdown
# Bifrost Security Audit — <ISO date>

## TL;DR

- Dependabot: <X> alerts open, <Y> PRs open (<Y_minor> patch/minor + <Y_major> major review-needed)
- CodeQL: <Z> alerts open (<errors>e / <warnings>w / <notes>n) — top rules: <ruleA, ruleB>
- Secret-scanning: <S> open
- Stale PRs: <P> open >14d
- Scorecard run: <date>, <conclusion>
- Auto-merge workflow: <failures>/10 recent runs

## Open PRs (<count>)

| # | Title | Author | Age | Bucket |
|---|---|---|---|---|
| 41 | bump playwright ... | dependabot | 2d | major |
...

## Dependabot Alerts (<count>)

| Severity | Count |
| critical | 0 |
| high | 0 |
| moderate | 9 |
| low | 2 |

| Ecosystem | Count |
| npm | 11 |

Top alerts (with associated PR if any):
- moderate `axios` — covered by #57 (merged)
- ...

## CodeQL Alerts (<count>)

| Severity | Count |
| error | <e> |
| warning | <w> |
| note | <n> |

Top 15 rules:
| Count | Severity | Rule ID |
| 267 | error | py/log-injection |
...

## Secret-Scanning Alerts (<count>)

(none) or list each.

## Scorecard

- Last run: <date> (<conclusion>)
- Score: (link to most recent run)

## Stale PRs (>14d)

| # | Title | Age |
...

## Auto-merge Health

- Last 10 runs: <successes> green, <failures> failed
- (only flag if failures ≥ 2)

## Branch Protection

| Check | Required |
| Lint & Type Check | ✓ |
...

## Suggested next action

(One sentence. The user knows what to do; just point at the biggest pile.)
```

## Halt conditions

- API rate-limited → note in output and continue with cached/partial data
- `gh` not authenticated → return "skill cannot run; run `gh auth login`"
- `repos/gobifrost/bifrost` returns 404 → user is in wrong repo / not authenticated as owner

## What this skill does NOT do

- Take any action — read-only. To act, switch to `bifrost-secupdate`.
- Filter or hide alerts — show everything.
- Compare against a previous run — point-in-time snapshot only.
- Output JSON for tooling — markdown only. (Tooling can call the same gh-api commands.)
- Cover security beyond the GitHub Security tab — no SAST other than CodeQL, no dynamic scan, no secrets in env files.
