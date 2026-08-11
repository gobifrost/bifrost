# Builder backup file and behavior inventory

**Compared source:** `1696d8693632c21c34be9469e0cbdf5502d0fee9`

**Compared target:** working tree of `codex/code-builder-recovery-20260807`

**Updated:** 2026-08-10

## Method

The file inventory selects every path in the backup whose path contains
`builder`, `Builder`, `solution_builder`, or `private_solution`. Each backup
blob is compared to the current working-tree file hash, including uncommitted
recovery work. Shared files without those names are covered by the behavioral
inventory and final test matrix because a path-name filter cannot identify
authorization, manifest, scheduler, application-runtime, or Solution deploy
integration reliably.

Result: **76 backup paths = 35 exact + 31 evolved + 10 intentionally omitted.**

## Intentionally omitted paths

Every absent backup path is accounted for below.

| Backup path | Disposition | Current replacement |
| --- | --- | --- |
| `api/src/builder/coordinator.py` | omitted | registered `solution.builder.turn`/`solution.build` PlatformJob handlers in the existing scheduler |
| `api/src/builder/main.py` | omitted | existing scheduler replicas; no Builder coordinator process |
| `api/src/routers/internal_builder.py` | omitted | job-bound `/api/internal/sandbox/jobs/{job_id}/*` callback contract |
| `api/tests/unit/test_builder_coordinator.py` | omitted | PlatformJob handler, external dispatch, callback, runner, and provisioning suites |
| `builder_runner/Dockerfile` | removed | version-matched provider-neutral `builder-runner/Dockerfile` |
| `builder_runner/server.py` | removed | one-shot `builder-runner/runner.py` plus optional authenticated `runner_service.py` |
| `k8s/builder/deployment.yaml` | omitted | no trusted Builder deployment; scheduler coordinates jobs |
| `k8s/builder-runner/deployment.yaml` | omitted | Cloudflare pulls the release runner image per active sandbox; optional local runner is hoster-operated |
| `k8s/builder-runner/network-policy.yaml` | omitted | no in-cluster runner in the default architecture; sandbox capability and Cloudflare isolation replace this deployment boundary |
| `k8s/builder-runner/service.yaml` | omitted | Cloudflare Workflow/Container binding; optional local runner endpoint is explicit configuration |

## Exact backup blobs

These paths are byte-for-byte equal to the backup:

- `api/alembic/versions/20260105_000000_split_app_builder_schema.py`
- `api/alembic/versions/20260106_151003_app_builder_versioning.py`
- `api/alembic/versions/20260117_230000_add_jsx_app_builder_schema.py`
- `api/alembic/versions/20260118_183636_consolidate_app_builder_remove_.py`
- `api/shared/builder_package_catalog.json`
- `api/src/builder/__init__.py`
- `api/src/services/app_builder/README.md`
- `api/src/services/builder/__init__.py`
- `api/src/services/builder/app_scope.py`
- `api/src/services/builder/app_session.py`
- `api/src/services/builder/claim.py`
- `api/src/services/builder/fs_tools.py`
- `api/src/services/builder/revision_inspection.py`
- `api/src/services/mcp_server/tools/builder_workspace.py`
- `api/tests/unit/services/mcp_server/test_builder_workspace.py`
- `api/tests/unit/test_builder_app_session.py`
- `api/tests/unit/test_builder_fs_tools.py`
- `client/src/components/app-builder/AppInfoDialog.tsx`
- `client/src/components/app-builder/AppUpdateIndicator.tsx`
- `client/src/components/app-builder/EmbedSettingsDialog.tsx`
- `client/src/components/app-builder/NewVersionBanner.tsx`
- `client/src/components/app-builder/index.ts`
- `client/src/components/builder/BuilderChangesPanel.test.tsx`
- `client/src/components/builder/BuilderChangesPanel.tsx`
- `client/src/components/builder/BuilderCodePanel.test.tsx`
- `client/src/components/builder/BuilderCodePanel.tsx`
- `client/src/components/builder/NewWithAIButton.tsx`
- `client/src/components/builder/RevisionList.test.tsx`
- `client/src/components/builder/RevisionList.tsx`
- `client/src/lib/builder-workbench-state.test.ts`
- `client/src/lib/builder-workbench-state.ts`
- `client/src/pages/FormBuilder.tsx`
- `client/src/stores/app-builder-editor.store.ts`
- `client/src/stores/app-builder.store.ts`
- `docs/superpowers/specs/2026-07-25-private-solution-builder-design.md`

## Deliberately evolved backup paths

These paths preserve their backup responsibilities and add or adapt behavior
for corrected migrations, PlatformJob/sandbox execution, collaboration,
separate releases, Global Workspace, resumption, metering, or current UX:

- `api/alembic/versions/20260725_builder_tables.py` — retained as a tombstone;
  restored schema is forward-only.
- `api/src/models/contracts/solution_builder.py`
- `api/src/models/orm/solution_builder.py`
- `api/src/routers/solution_builder.py`
- `api/src/services/builder/agent_turns.py`
- `api/src/services/builder/build_input.py`
- `api/src/services/builder/build_plane.py`
- `api/src/services/builder/build_requests.py`
- `api/src/services/builder/capabilities.py`
- `api/src/services/builder/private_solutions.py`
- `api/src/services/builder/promotion.py`
- `api/src/services/builder/revision_storage.py`
- `api/src/services/builder/scaffold.py`
- `api/src/services/builder/staged_artifacts.py`
- `api/src/services/builder/turns.py`
- `api/src/services/solutions/builder_authz.py`
- `api/tests/e2e/platform/test_private_solutions.py`
- `api/tests/unit/test_builder_agent_turns.py`
- `api/tests/unit/test_builder_authz.py`
- `api/tests/unit/test_builder_capabilities.py`
- `api/tests/unit/test_builder_orm_models.py`
- `api/tests/unit/test_builder_turns.py`
- `client/src/components/builder/NewWithAIButton.test.tsx`
- `client/src/components/builder/PreviewPane.test.tsx`
- `client/src/components/builder/PreviewPane.tsx`
- `client/src/hooks/useBuilderAccess.ts`
- `client/src/pages/SolutionBuilder.test.tsx`
- `client/src/pages/SolutionBuilder.tsx`
- `client/src/services/builder.test.ts`
- `client/src/services/builder.ts`
- `docs/superpowers/specs/2026-07-27-private-solution-builder-status.md`

## Behavioral recovery beyond path-name matching

The following recovered responsibilities live in shared or newly named files
and therefore do not appear in the 76-path name inventory:

| Backup behavior | Current disposition |
| --- | --- |
| durable build/deploy orchestration | canonical PlatformJob registry, runner policy, scheduler claim/recovery, and notification WebSocket |
| private source revisions and conversation continuity | preserved and extended with OpenCode checkpoint state and resume links |
| fixed credential-free application build | retained inside the provider-neutral one-shot runner; safe `./` and root-relative asset bases are accepted while external/protocol-relative bases are rejected |
| Builder Agent tools | retained as hidden target-bound workspace tools and bridged to external MCP harnesses |
| Agent instructions and assets | canonical Solution-root-relative `SKILL.md`, direct bundle storage, browser/editor/export, CLI, MCP, and manifests |
| private preview security | same-origin API-mounted opaque iframe runtime with exact app/Solution/org/viewer scopes |
| promotion | exact pinned review now publishes a separate target release without consuming private source |
| administrator support | My work/All customer work, organization/owner filters, centralized support authorization, collaborators |
| global `_repo` editing | immutable admin proposal, non-executing validation, explicit apply, digest fencing, rollback |
| setup/operations | admin readiness wizard, encrypted Cloudflare/local settings, live provisioning job, user enable gate |
| AI execution | job-bound Bifrost LLM proxy, user/org usage attribution, transactional call/token fences, visible percentages |
| production topology | no permanent Builder container; the canonical runner is started ephemerally by Cloudflare or explicitly by a self-hoster, while the identical image is an opt-in test profile for build-plane E2E only |

## New forward-only Builder migrations

The withdrawn revision IDs are not repopulated. Current Builder state returns
through these later migrations:

- `20260807_reinstate_builder_schema.py`
- `20260807_complete_builder_schema.py`
- `20260807_builder_collaboration.py`
- `20260807_platform_job_external_execution.py`
- `20260807_app_runtime_mode.py`
- `20260810_builder_turn_checkpoints.py`
- `20260810_builder_releases.py`
- `20260810_builder_global_workspace.py`

## Closure condition

This inventory is complete only when the final branch has been reconciled with
current `origin/main`, the comparison is rerun with the same 35/31/10 paths or
an explained successor count, the complete Builder/shared scheduler matrix is
green, and the final handoff reports the known aggregate-quota, Cloudflare
billing, favorites/comments/presence, and simultaneous-edit limitations.
