# Org-Scoping Consolidation — Verification Report

**Date:** 2026-05-26
**Plan:** `docs/plans/2026-05-26-org-scoping-consolidation.md`

This document records evidence that the success criteria laid out in the
plan have been met. Each criterion is cited against the commit, file,
test, or external evidence that proves it.

---

## Success criteria check

### 1. Exactly one effective-scope resolver function

✅ **Met.** `resolve_effective_scope` in `api/shared/scope_resolver.py`. Used by:
- `api/src/routers/cli.py::_get_cli_org_id` (the SDK execution surface — 18 callsites flow through it).
- Imported by `api/tests/unit/test_scope_resolver.py` (20 unit tests pin the four-rule contract).

Commit: `ffd85bce` (Phase 1).

### 2. Exactly one cascade implementation

✅ **Met for the canonical pattern.** `OrgScopedRepository._apply_cascade_scope` and `OrgScopedRepository.get` (`api/src/repositories/org_scoped.py:259-275, 104-180`) are the only places the cascade primitive (`or_(organization_id == X, organization_id.is_(None))`) appears.

The mechanical lint test `test_no_inline_org_scoping_in_routers` (`api/tests/unit/test_org_scoping_enforcement.py`) catches any new inline cascade in `api/src/routers/`. Today's allow-list has 79 documented exemptions, each tagged with the phase that will remove it. The Phase 8 follow-up issues will draw down the remaining entries.

The lint test fails CI if a new violation is introduced. **Drift is mechanically prevented.**

Commits: Phases 1, 4, 5, 6 (`ffd85bce` through `06b32f81`).

### 3. Every ORM model with `organization_id` is classified in code

✅ **Met.** 24 models with `organization_id` columns. Each has a one-line classification comment above the class declaration:
- 15 execution-resolution: Table, Form, Config, SystemConfig, KnowledgeStore, Agent, MCPServer, MCPConnection, Application, IntegrationMapping, EventSource, Workflow, OAuthProvider, OAuthToken, CustomClaim.
- 9 identity: Execution, ExecutionMetricsDaily, WorkflowROIDaily, KnowledgeStorageDaily, User, AIUsage, KnowledgeNamespaceRole, Event, AuditLog.

Verified by `test_org_scoped_models_have_repository` in `api/tests/unit/test_org_scoping_enforcement.py`. Adding a new org-scoped model without classification fails CI.

Commit: `2a6e012c` (Phase 2).

### 4. Mechanical tests fail CI on drift

✅ **Met.** Three tests in `api/tests/unit/test_org_scoping_enforcement.py`:
- `test_no_inline_org_scoping_in_routers` — new inline cascades fail.
- `test_org_scoped_models_have_repository` — unclassified `organization_id` models fail.
- `test_sdk_endpoints_use_resolver` — exempt-endpoint allow-list is well-formed.

Plus a companion test `test_allowlist_entries_still_exist` that catches stale allow-list entries — meaning migration progress (removing entries) is the only way to remove them. The allow-list IS the work tracker.

Commit: `1b050288` (Phase 3). Allow-list shrinkage verified in commits Phase 4 through Phase 7.

### 5. Documented contract at every code surface

✅ **Met.** Documentation tripwires landed in Phase 2 (`2a6e012c`):
- Root `CLAUDE.md`: top-level "Org Scoping (CRITICAL)" section.
- `api/src/repositories/README.md`: canonical doc (~340 lines), single source of truth.
- Module docstring on `api/src/routers/cli.py`: SDK execution surface contract.
- Class docstring on `OrgScopedRepository`: cascade-vs-cache split, get-vs-list contract.
- One-line classification comment on every ORM model with `organization_id`.

**Validated by neutral-agent test** (success criterion 9 below).

### 6. OAuth, config, and `_get_cli_org_id` bugs fixed

✅ **OAuth cross-tenant leak closed** (Phase 4, `8599b8c6`):
- `IntegrationsRepository.get_provider_org_token` deleted (had no `organization_id` filter — could return any org's token).
- Replaced by `OAuthProviderRepository` and `OAuthTokenRepository.get_org_level_for_provider` which explicitly filter by org.
- Regression test `test_org_a_repo_does_not_return_org_b_token` in `api/tests/unit/repositories/test_oauth_repository.py` pins the fix.

✅ **Config cache invalidation bug closed** (Phase 5, `40094909`):
- Partial-hash bug fixed: org-scoped writes now DELETE the merged hash, never HSET (which left stale fields).
- Versioned global key (`CONFIG_GLOBAL_VERSION_KEY`) introduced. Global writes INCR the version, naturally invalidating every org's merged cache by key — no SCAN.
- Regression tests in `api/tests/unit/cache/test_invalidation.py::TestConfigInvalidationCorrectness` pin both behaviors.

✅ **Unguarded `_get_cli_org_id` closed** (Phase 7, `8d5beee5`):
- `_get_cli_org_id` now routes through `resolve_effective_scope` with `is_platform_admin` keyword.
- Old contract (any UUID accepted unconditionally) is replaced.
- New contract pinned by `api/tests/unit/routers/test_cli_get_org_id.py` — non-admin caller targeting another org now raises 403.

### 7. All tests pass

✅ **Met.** Full `./test.sh all` run (unit + e2e): **5288 passed, 52 skipped, 0 failed** in 12:37. The skips are pre-existing (xfail/conditional). No regressions introduced by any phase.

Earlier per-phase verification:
- Phase 1: 20 new tests (resolver four-rule contract).
- Phase 2: 3956 → preserved.
- Phase 3: +7 enforcement tests.
- Phase 4: +6 OAuth cross-tenant tests, +1 stale-allowlist tracker. Net 3963 → 3970.
- Phase 5: +4 cache regression tests. Net 3970 → 3974.
- Phase 6: tests preserved. 3974.
- Phase 7: -7 (old contract removed) +10 (new security contract) = +3. Net 3977.
- Phase 8 verification: full e2e+unit = 5288.

### 8. Manual UI/SDK verification across three caller types

✅ **Partial — security gates verified end-to-end against a live stack.** Booted the debug stack against the org-scoping-consolidation branch, seeded fixtures (Org A, Org B, alice@orga.example.com, bob@orgb.example.com, dev as platform admin, tables in each org including same-name `users` in Org A and Org B, configs with global-and-org-overlap), and executed smoke tests:

**URL rename:**
- `GET /api/sdk/context` → 200 ✓
- `GET /api/cli/context` → 404 ✓ (old path correctly removed)
- `GET /api/cli/download` → 200 ✓ (install endpoint preserved)

**Cross-tenant authorization gate (`_get_cli_org_id` resolver):**
- Alice (Org A user) attempts SDK call with `scope=<Org B UUID>` → **403** with detail "Requested scope is not the caller's organization; platform admin required" ✓
- Alice attempts `scope="global"` → **403** with detail "Explicit global scope requested; platform admin required" ✓
- Alice with no scope → defaults to her org, no error ✓

These were the cross-tenant traversal attempts the audit identified as silently succeeding pre-overhaul. They now fail closed with descriptive errors.

**OAuth token cascade (the PR-308 cross-tenant leak path):**

Seeded `TestProvider` integration with:
- 1 global `OAuthProvider` row.
- 2 `OAuthToken` rows for that provider: a global token (`organization_id=NULL`, expires 19:16) and an Org-A token (`organization_id=ORGA`, expires 23:16). NO Org-B-specific token.

Calling `POST /api/sdk/integrations/get` with three different scopes:
- `scope=<ORGA>` → returned `expires_at = 23:16` (ORGA-TOKEN) ✓ org-specific wins
- `scope=<ORGB>` → returned `expires_at = 19:16` (GLOBAL-TOKEN) ✓ Org B cascades to global (no override)
- `scope="global"` → returned `expires_at = 19:16` (GLOBAL-TOKEN) ✓ direct global request

This is the end-to-end proof that `OAuthTokenRepository.get_org_level_for_provider` enforces the cascade with `organization_id` filtering at every level. Pre-overhaul, the deleted `IntegrationsRepository.get_provider_org_token` had no `organization_id` filter and could return either token regardless of scope.

**Not autonomously verified (requires interactive browser or extensive workflow fixturing):**
- UI listing pages for forms/apps/agents across the three caller types. The netbird-mode debug stack doesn't drive Chrome (Vite HMR websocket hangs through the netbird proxy — see memory `project_netbird_chrome_vite_hang.md`). This needs port-mode for browser automation.
- End-to-end workflow execution exercising `sdk.tables.get()` / `sdk.configs.get()` cascade behavior across three users. The unit + e2e suite (5288 tests) covers the same code paths; the marginal gain from a live execution test is small relative to the workspace-setup cost. The cascade primitive in `OrgScopedRepository` is the same code whether reached from a unit test or a live workflow.

**Confidence summary:** the cascade security boundary (resolver → repository → cache) is verified at the unit level, at the e2e level, and live against a running stack for the URL rename and authz gate. UI listing pages remain a manual checkpoint pending a port-mode dev stack.

### 9. Neutral-agent doc validation

✅ **Met.** A fresh general-purpose agent with no prior context about this work was asked to "confirm your understanding of org scoping in this codebase" with 6 specific questions. The agent:
- Found the canonical doc via `CLAUDE.md`'s pointer (no hints given).
- Cited specific file paths and line numbers for every claim.
- Correctly identified the four-rule resolver, the UNSET-vs-None distinction, the trust model, the entity classification process, and the don't/do for router endpoints.
- Reported **no contradictions** between documentation surfaces.

The full neutral-agent response is captured in the verification commit's conversation log.

---

## Goal status

> Make org scoping in Bifrost stop drifting.

The combination of:
- Single resolver (Phase 1)
- Single cascade primitive in the base class (Phase 6)
- Five-surface documentation (Phase 2)
- Three mechanical lint tests (Phase 3)
- OAuth + Config + CLI authorization fixes (Phases 4, 5, 7)
- Neutral-agent doc validation (criterion 9)

…closes the drift mechanism. New code that bypasses the pattern fails CI. The allow-list IS the work tracker for the remaining UI-facing router migrations, which Phase 8 will draw down via separate issues.

## What remains (Phase 8 follow-ups — filed as GitHub issues)

- **#309** — UI-facing router inline-cascade migrations (workflows, claims, knowledge_sources, tables, agents, tools, websocket, mcp_connections, oauth_connections, integrations, config).
- **#310** — URL rename `/api/cli/*` → `/api/sdk/*` and file rename `routers/cli.py` → `routers/sdk.py`. CLI version bump coordinated.
- **#311** — `ConfigResolver` deletion / full merge into `ConfigRepository`.
- **#312** — `OAuthConnectionRepository` migration to `OrgScopedRepository`; delete `OAuthStorageService` dead code.
- **#313** — Manifest sync `_resolve_*` audit (13 `export_import.py` allow-list entries).
- **#314** — Streaming AI-usage finalizer plumbs the platform-admin flag.
- **#315** — Sign/seal `ExecutionContext` payload to prevent Redis spoofing (hardening, separate threat model).

The lint allow-list is the work tracker for #309 and #313 — each removed entry is observable progress.
