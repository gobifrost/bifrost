# Code Builder recovery ledger

**Recovery branch:** `codex/code-builder-recovery-20260807`  
**Corrected main:** `ae0833d402bfccb5b4d634c9ed6f711db705fbb2`  
**Builder source of truth:** `1696d8693632c21c34be9469e0cbdf5502d0fee9`  
**Remote backup:** `backup/code-builder-1696d8693-20260807`  
**Started:** 2026-08-07

## Recovery contract

The scheduler correction intentionally withdrew the unfinished private Solution
Builder from public `main` without declaring its product work disposable. This
branch reconstructs Builder on top of corrected scheduler and PlatformJob code.

The recovery must satisfy all of these invariants:

1. `code-builder` remains unchanged at `1696d8693`.
2. The remote backup remains an exact ref to `1696d8693`.
3. Corrected `main` is authoritative for scheduler leadership, PlatformJob
   claiming and execution, scheduler diagnostics, fixtures, and shared
   debug/test infrastructure.
4. Commits `68e97dd35`, `9dde18dc0`, `451ac4c0b`, and `352274cf3` are not
   replayed. Builder-specific behavior is reconstructed deliberately.
5. The bodies of the withdrawn Builder Alembic revisions remain no-op
   tombstones. Builder schema returns only through new forward migrations after
   `20260807_withdraw_builder`.
6. The transaction-ordering fixes in Solution deploy cleanup and table-policy
   WebSocket publication remain intact.
7. The branch is not pushed until the file/behavior inventory is closed and the
   required Builder plus shared-scheduler test matrix is green.
8. The branch is not merged without explicit approval.

## Target architecture

- `PlatformJob` remains the only durable orchestration system.
- The old dedicated trusted Builder coordinator is not restored.
- Coding and build execution use a provider-neutral sandbox contract:
  Cloudflare Sandbox is the recommended managed provider and a local sandbox is
  the supported development/self-hosted provider.
- Native Builder, CLI, and external MCP harnesses use one target-bound Solution
  authoring capability contract rather than parallel Builder-only mutations.
- Generated application URLs remain transparent `/apps/{slug}` routes. Existing
  V1/V2 applications remain compatible.
- Builder projects become collaborative source projects with explicit viewer,
  editor, reviewer, approver, and publisher capabilities; deployed Solutions
  remain pinned releases/installs.

## Source inventory summary

The initial tree comparison found 322 divergent paths between corrected main
and the Builder backup: 118 Builder-side additions, 200 shared modifications,
four corrected-main artifacts absent from the backup, and no renames.

### Corrected-main artifacts that must remain

| Path | Disposition | Reason |
| --- | --- | --- |
| `api/alembic/versions/20260807_withdraw_unfinished_builder.py` | preserve | Migration continuity and deterministic seed cleanup |
| `api/tests/e2e/platform/test_withdrawn_builder_migrations.py` | preserve and extend | Proves tombstones and upgrade compatibility |
| `api/tests/unit/jobs/test_solution_deploy_platform_job.py` | preserve | Canonical corrected PlatformJob behavior |
| `api/tests/unit/routers/test_tables_policy_publish.py` | preserve | Post-commit WebSocket publication regression |

### Explicitly obsolete implementation

| Path or behavior | Disposition | Replacement |
| --- | --- | --- |
| `api/src/builder/coordinator.py` | do not restore | PlatformJob handler plus sandbox provider |
| `api/src/builder/main.py` | do not restore | Existing scheduler replicas |
| `k8s/builder/deployment.yaml` | do not restore | No trusted Builder coordinator deployment |
| Old scheduler/merge commit hunks | do not replay | Corrected main |
| Old Builder migration bodies | do not restore | New forward-only migrations |
| Public second-port/app-origin setup | redesign | Internal transparent `/apps/*` routing |

### Builder-owned behavior to recover and evolve

- immutable source revisions, sessions, turns, diffs, restore, and review pins;
- full Solution authoring and validation;
- Agent Skill upload, browsing, `SKILL.md` instruction projection, and assets;
- isolated build protocol and artifact validation;
- private preview, scoped app runtime, launch renewal, and WebSockets;
- promotion review and exact-revision publication;
- Build workbench, Preview/Code/Changes, restoration transitions, and mobile UX;
- administrator readiness, provider configuration, quotas, usage, and health;
- provider/customer collaboration, My work / All work / Needs review, and audit;
- external MCP authoring parity with native Builder and CLI.

## Reconstruction waves

| Wave | Scope | State |
| --- | --- | --- |
| 0 | Backup, recovery worktree, inventory, and migration/shared-file audits | complete |
| 1 | New forward schema plus Builder domain models/contracts | in progress |
| 2 | Durable authoring workspace and shared REST/CLI/MCP capability layer | pending |
| 3 | PlatformJob Builder handlers and Cloudflare/local sandbox providers | pending |
| 4 | Agent Skills, full Solution authoring, preview runtime, and publication | pending |
| 5 | Collaboration, RBAC, catalog organization, favorites, and MSP support | pending |
| 6 | Admin readiness, cost ledger, budgets, metering, and provider health | pending |
| 7 | Restored and refined Builder UI with live UX/accessibility review | pending |
| 8 | Inventory closure, complete verification, conformance, and push decision | pending |

## Required conformance fixtures

### Full-Solution authoring parity

Both native Builder and an external MCP harness must build, validate, preview,
publish, and install the same portable Solution containing an app, Python
workflow, form, Agent Skill bundle, table/policy, config or integration
requirement, runtime file location, and correct access relationships.

### Scale and tenancy

Seed at least 100 customer organizations, 1,000 Builder projects/apps, and a
larger revision/activity history. Verify server-side pagination and filtering,
ordinary-user My work isolation, provider All work discovery, and zero
cross-customer disclosure.

### Recovery and failure handling

Exercise scheduler restart, sandbox loss, stale leases, cancellation, provider
429/outage, budget exhaustion, tampered artifacts, expired app sessions, and
returning to an earlier Builder conversation/revision.

## Verification ledger

No post-reconstruction result is recorded yet. Historical green results on the
backup are evidence of intended behavior, not proof of this integration branch.

Before push, record:

- API Pyright and Ruff;
- generated OpenAPI/CLI/SDK contracts and contract-version decision;
- complete backend unit and E2E suites;
- complete client Vitest and Playwright suites;
- focused Builder, PlatformJob, scheduler diagnostics, migration-upgrade, and
  table/Solution-deploy regression matrices;
- Compose and Kubernetes rendering for supported providers;
- desktop/mobile UX screenshots and accessibility interaction checks;
- inventory disposition for all 322 initial paths;
- final independent conformance review against this ledger and the original
  Builder requirements.
