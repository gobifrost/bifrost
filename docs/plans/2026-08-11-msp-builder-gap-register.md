# MSP Builder gap register

**Date:** 2026-08-11
**Baseline:** recovered private Solution Builder in the code-builder integration
worktree.

## What is already present

- Private owner workspaces, same-organization view/edit collaborators, multiple
  attributable chat sessions, immutable revisions, diff/download/undo, preview,
  and checkpoint recovery.
- A default **My work** catalog plus an explicit **All customer work** support
  view gated by `organization.impersonation`, with organization and owner
  filters.
- Promotion requests, administrator review, company/global promotion targets,
  runtime trust selection, and a guarded administrator Global Workspace for
  `_repo` proposals.
- Builder-aware MCP tools and the same `bifrost-build` Skill used by the native
  harness.

## P0 — required before broad production enablement

### Aggregate budget and quota enforcement

Per-turn limits and AI usage attribution exist, but monthly or rolling policy
does not. Add platform, organization, user, and Solution budgets with warning
thresholds, reservation before dispatch, hard-stop behavior, and an override
permission. The workbench must show consumed/remaining percentages and the
administrator must be able to trace each model and sandbox charge.

**Acceptance:** two concurrent turns cannot overspend the same remaining
budget; the API, native UI, MCP, and CLI receive the same typed quota response;
support can distinguish customer policy from provider outage.

### Governance audit stream

Revision history is not a substitute for an audit trail. Record collaborator
changes, support access, role/target changes, promotion requests and decisions,
trusted-runtime approvals, publish/unpublish, ownership transfer, quota
overrides, and global-workspace apply/rollback.

**Acceptance:** administrators can filter an append-only feed by organization,
owner, Solution, actor, and event; every cross-tenant support mutation has a
reason and correlation/job ID.

## P1 — MSP operating model

### Explicit ownership and customer handoff

Creation currently means “owned by me in my current organization.” Add an
intentional create-for flow for personal, MSP-internal, and customer contexts;
then add owner transfer and customer acceptance without requiring accidental
impersonation.

**Acceptance:** the target organization and initial owner are visible before
creation; transfers require acceptance, preserve history, and never silently
widen access; customer-owned work remains discoverable to authorized support.

### Support mode, not invisible super-access

The support catalog exists, but opening customer work should establish a
visible, scoped support context. Show customer, owner, effective permissions,
and whether the session is read-only, collaborative, or impersonated.

**Acceptance:** leaving support mode restores the operator's normal context;
all prompts, file changes, turns, and releases show the real support actor and
customer context; a support user cannot exceed their assigned role.

### One review, release, and handoff journey

Share, validate, request promotion, administrator review, runtime trust, and
publish are individually present but fragmented. Combine them into a guided
release flow with readiness, diff, capabilities/roles, target organization,
reviewers, approval, deployment progress, release notes, and rollback.

**Acceptance:** a builder can always answer what is live, where, for whom, from
which revision, who approved it, and how to roll it back.

### External harness lifecycle parity

MCP covers workspace operations, but the complete create/session/turn/review/
publish lifecycle must be contract-tested across native UI, MCP, and CLI.

**Acceptance:** every durable operation returns the canonical PlatformJob; the
same authorization and visibility tests run against all three surfaces; no MCP
tool reimplements router business logic.

### Collaboration beyond access grants

Add review comments anchored to revision/path, change requests, reviewer
assignment, presence, and notifications. Avoid simultaneous source mutation
until conflict semantics are designed; visible presence and review are more
valuable than premature real-time co-editing.

## P2 — scale and polish

- Favorites, recent work, tags, status/customer/owner facets, saved support
  views, and a **Needs review** queue.
- A template gallery for blank, MSP-standard, customer-standard, and cloned
  Solution starters, with template provenance and update semantics.
- Named checkpoints and a session timeline beyond failure recovery.
- Bounded pagination for **My work** as well as **All customer work**; the
  current personal query can grow without a server-side limit.
- Incident tooling that correlates Builder turns, runner jobs, model calls,
  preview deploys, releases, and provider failures, with safe replay from a
  known revision.

## Recommended order

1. Budget policy and audit events.
2. Ownership/customer-context model and visible support mode.
3. Unified release/handoff flow.
4. MCP/CLI parity matrix and contract tests.
5. Favorites/templates/saved views/checkpoint polish.
