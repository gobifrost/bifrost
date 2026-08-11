# Code Builder recovery ledger

**Recovery branch:** `codex/code-builder-recovery-20260807`

**Corrected-main starting point:** `ae0833d402bfccb5b4d634c9ed6f711db705fbb2`

**Builder source of truth:** `1696d8693632c21c34be9469e0cbdf5502d0fee9`

**Remote immutable backup:** `backup/code-builder-1696d8693-20260807`

**Updated:** 2026-08-10

## Recovery contract

The scheduler correction intentionally withdrew the unfinished private
Solution Builder from public `main`; it did not make the product work
disposable. This branch reconstructs Builder on corrected PlatformJob and
scheduler code without rewriting published migration history.

The reconstruction preserves these invariants:

1. The original `code-builder` branch remains unchanged at `1696d8693`.
2. The remote backup is an exact immutable ref to that commit.
3. Corrected `main` is authoritative for scheduler leadership, PlatformJob
   claiming/execution, diagnostics, fixtures, and shared debug/test
   infrastructure.
4. Scheduler and merge commits `68e97dd35`, `9dde18dc0`, `451ac4c0b`, and
   `352274cf3` are not replayed.
5. Withdrawn Builder revisions remain tombstones. All restored Builder schema
   is introduced through new forward-only revisions after
   `20260807_withdraw_unfinished_builder.py`.
6. Corrected Solution deploy cleanup, table-policy publication, scheduler
   diagnostics, and debug/test behavior remain intact.
7. The reconstructed branch is not merged without explicit approval.

## Current architecture

- `PlatformJob` is the only durable Builder job system. Existing scheduler
  replicas coordinate turns, builds, provisioning, progress, retry,
  cancellation, and recovery.
- Cloudflare Workflows plus Sandbox Containers are the recommended external
  execution provider. The provider-neutral local runner is optional for
  development or deliberate self-hosting. There is no automatic fallback.
- Production gains no permanently running Bifrost Builder, runner, or app-host
  container. Cloudflare pulls a version-matched runner image only for active
  work. The isolated app runtime is mounted as a narrow sub-application inside
  the existing API process.
- Generated app routes remain on the normal Bifrost origin. No extra public
  port, DNS record, or hostname is required.
- Native Builder and external MCP harnesses share the same target-bound
  Solution workspace tools and canonical `bifrost-build` Skill.
- Publishing creates or updates a separate shared release/install. The private
  source project and its complete history remain editable.

## Recovered and evolved behavior

| Area | State | Evidence boundary |
| --- | --- | --- |
| Forward-only Builder schema and tombstone continuity | implemented | migration upgrade/E2E coverage |
| Immutable revisions, sessions, turns, diffs, undo, deployed pins | implemented | unit, API E2E, workbench tests |
| PlatformJob turn/build lifecycle and scheduler handoff | implemented | job, scheduler, callback tests |
| Cloudflare provisioning and external callback contract | implemented | JS, provisioning, callback tests; one live Cloudflare checkpoint completed |
| Optional authenticated local runner service | implemented | runner-service tests plus durable provision/dispatch/build/deploy E2E |
| OpenCode harness, persisted state, compaction, and turn resume | implemented | JS integration and runner tests |
| Agent Skill upload/browse/export and `SKILL.md` instruction source | implemented | API/CLI/MCP/component coverage |
| Native/external MCP Solution workspace parity | implemented for the shared filesystem/validation surface | MCP gateway and workspace tests |
| Same-origin isolated preview and attenuated SDK scopes | implemented | runtime API/WebSocket/E2E coverage |
| Provider/customer collaboration and support discovery | implemented | access E2E and Build UI tests |
| Separate customer/global release publication | implemented | promotion E2E/component coverage |
| Admin Global Workspace proposal/validate/apply/rollback | implemented | unit, live API E2E, browser coverage |
| Admin setup/readiness wizard and live provisioning progress | implemented | service/component/Playwright coverage |
| Per-turn call/token budgets and user/org AI-cost attribution | implemented | proxy tests, workbench meters, usage report link |
| Per-org or per-user monthly Builder quota policies | not implemented | requires a platform-wide quota policy design |
| Cloudflare invoice/charge ingestion into Bifrost | not implemented | run identity and duration are stored; billing remains in Cloudflare |
| Favorites, review comments, and live presence | not implemented | useful catalog/collaboration refinements, not authorization substitutes |

## Source inventory

The initial corrected-main versus backup comparison found 322 divergent paths:
118 Builder additions, 200 shared modifications, and four corrected-main
artifacts absent from the backup. The later integration comparison grew as
`main` and Builder evolved; inventory closure must therefore be behavioral as
well as file-by-file.

### Corrected-main artifacts preserved

| Path | Disposition |
| --- | --- |
| `api/alembic/versions/20260807_withdraw_unfinished_builder.py` | preserved as migration continuity tombstone |
| `api/tests/e2e/platform/test_withdrawn_builder_migrations.py` | preserved and extended |
| `api/tests/unit/jobs/test_solution_deploy_platform_job.py` | preserved as canonical deploy-job behavior |
| `api/tests/unit/routers/test_tables_policy_publish.py` | preserved as post-commit publication regression |

### Deliberate omissions from `1696d8693`

| Old path or behavior | Disposition | Replacement |
| --- | --- | --- |
| `api/src/builder/coordinator.py` and `api/src/builder/main.py` | omitted | existing scheduler plus PlatformJob handlers |
| dedicated Builder coordinator deployment | omitted | no trusted Builder coordinator service |
| `builder_runner/` fixed-Vite HTTP service | removed | provider-neutral `builder-runner/` with OpenCode and app build modes |
| public app-host process/port and app-origin configuration | omitted | API-mounted narrow runtime under the normal origin |
| old Builder migration bodies | never restored | forward-only reinstate/collaboration/runtime/checkpoint/release/global migrations |
| scheduler hunks from withdrawn merge commits | not replayed | corrected `main` implementation |

## Reconstruction waves

| Wave | Scope | State |
| --- | --- | --- |
| 0 | Backup, recovery worktree, migration/shared-file audits | complete |
| 1 | Forward schema and Builder domain contracts | complete |
| 2 | Durable authoring and REST/CLI/MCP capability surface | complete |
| 3 | PlatformJob handlers and Cloudflare/local providers | complete |
| 4 | Agent Skills, full Solution authoring, preview, publication | complete |
| 5 | Collaboration, RBAC, My work/All customer work, support filters | complete; favorites/comments/presence deferred |
| 6 | Admin readiness, AI metering, hard turn limits, provider health | complete for turn-level controls; aggregate quotas and Cloudflare billing deferred |
| 7 | Restored Builder UI, Global Workspace, live responsive UX review | complete |
| 8 | Final inventory, current-main reconciliation, complete verification, approval handoff | in progress |

## Required final verification

Before push or merge approval:

- reconcile current `origin/main` deliberately, keeping corrected shared
  scheduler/debug behavior and regenerating contracts as necessary;
- account for every file/behavior difference against `1696d8693`;
- run API Pyright and Ruff;
- run Builder, PlatformJob, scheduler diagnostics, migration, Solution deploy,
  and table-policy regression matrices;
- run runner Python and OpenCode/Cloudflare JavaScript suites;
- run focused and broad client Vitest plus Builder Playwright at desktop/mobile;
- render supported Compose/Kubernetes configuration where applicable;
- run adversarial delivery QA and report any known limitation;
- do not push or merge until this ledger is closed and approval is explicit.

## Evidence recorded on this branch

Current green checkpoints before the final `main` reconciliation include:

- API quality: Pyright `0 errors`; Ruff passed;
- focused Global Workspace unit/E2E path, including validation, apply, and
  rollback;
- separate-release promotion E2E;
- focused Builder frontend suite, including turn restoration, progress,
  cancellation, and percentage-based hard-limit meters;
- Builder administrator Playwright: five desktop/mobile scenarios passed,
  including setup guidance, private creation, Global Workspace, and responsive
  workbench panes;
- canonical runner Python suite: 41 tests passed;
- real build plane: durable Local-provider provisioning, authenticated runner
  dispatch, fixed-toolchain compilation, callback artifacts, and live deploy
  passed end to end. The Local runner service used by this test is an opt-in
  test profile only; production Compose and Kubernetes gain no permanent
  Builder container.

These checkpoints are not the final release claim. The complete post-merge
matrix and exact inventory remain the active Wave 8 gate.
