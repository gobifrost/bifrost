# OAuth Connection Health Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show Connected, Degraded, Failed, or None on integration cards and expose connection counts in a clickable popover.

**Architecture:** Aggregate distinct default and mapping-override OAuth token rows in the integrations list query while leaving mapping counts independent. Derive the four display states from the existing response counters in a focused React component, then exercise the popover through component and browser coverage.

**Tech Stack:** FastAPI, SQLAlchemy, PostgreSQL, React, TypeScript, Radix Popover, Vitest, Playwright

---

### Task 1: Aggregate concrete OAuth connections

**Files:**
- Modify: `api/src/routers/integrations.py`
- Modify: `api/src/models/contracts/integrations.py`
- Test: `api/tests/e2e/oauth/test_per_mapping_connect.py`

- [x] Add a failing E2E test that creates a successful default token plus a failed mapping override, calls `GET /api/integrations`, and expects both distinct connections in `connection_status_counts` while preserving the mapping count.
- [x] Run `./test.sh tests/e2e/oauth/test_per_mapping_connect.py -v` and confirm the default-token assertion fails.
- [x] Replace the mapping-only aggregation with separate mapping counts and a distinct union of the latest default token plus override token IDs; treat `completed` and legacy `connected` as successful.
- [x] Update response-field descriptions from mapping counts to concrete connection counts.
- [x] Rerun the targeted backend test and confirm it passes.

### Task 2: Render aggregate health and breakdown

**Files:**
- Create: `client/src/pages/Integrations/IntegrationConnectionStatus.tsx`
- Create: `client/src/pages/Integrations/IntegrationConnectionStatus.test.tsx`
- Modify: `client/src/pages/Integrations/IntegrationList.tsx`
- Delete: status-only coverage from `client/src/pages/Integrations/IntegrationList.test.tsx`

- [x] Write failing component tests for Connected, Degraded, Failed, None, Not monitored, and the opened popover breakdown.
- [x] Run `./test.sh client unit src/pages/Integrations/IntegrationConnectionStatus.test.tsx` and confirm the missing component fails.
- [x] Implement the status derivation, accessible badge button, and compact popover using the existing UI primitives.
- [x] Replace the inline list status implementation with the component import and keep row navigation from firing when the popover is used.
- [x] Run the focused component tests after moving the prior status-only integration-list coverage into the new focused test file.

### Task 3: Add the browser journey and capture states

**Files:**
- Create: `client/e2e/integration-connection-health.admin.spec.ts`

- [x] Add one Playwright journey that loads representative integration summaries, verifies all four labels, opens the Degraded flyout, and captures named screenshots.
- [x] Run `./test.sh client e2e --screenshots e2e/integration-connection-health.admin.spec.ts`.
- [x] Inspect every generated screenshot and correct any spacing, contrast, clipping, or alignment problem.

### Task 4: Merge-readiness verification

**Files:**
- Modify generated types only if the OpenAPI descriptions alter generated output: `client/src/lib/v1.d.ts`

- [x] Run the targeted backend, component, and Playwright checks again after final edits.
- [x] Run API quality, client type checking, and client linting.
- [x] Commit the exact candidate, confirm it is based on current `origin/main`, and run `./test.sh pre-pr` against the clean commit.
- [x] Report the candidate SHA, exact verification commands, screenshots, and any known failure.
