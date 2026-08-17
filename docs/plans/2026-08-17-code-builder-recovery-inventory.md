# Code Builder recovery inventory

**Compared source:** immutable backup `1696d8693`
**Reconstruction base:** `origin/main@16e317e62`
**Integration branch:** `codex/code-builder-pydantic-integration-20260816`

## Inventory result

The backup changed 377 paths relative to its merge base with main. Of those,
339 paths exist in the reconstructed worktree. They are either recovered
Builder implementation, deliberately generalized shared implementation, or
corrected scheduler/PlatformJob/diagnostic files already supplied by canonical
main. The reconstruction did not replay the backup's scheduler merge commits.

The remaining 38 paths are absent by design. Every absent path and its
replacement is accounted for below.

## Replaced runtime services (3)

| Backup path | Disposition |
| --- | --- |
| `api/src/builder/coordinator.py` | Retired. Durable orchestration is registered `PlatformJob` work; Builder turns and app compilation execute on existing Workers or the configured Cloudflare runner. |
| `api/src/builder/main.py` | Retired with the permanent Builder service. `api/src/builder/` now contains only the Worker-consumed prompt/Skill assets. |
| `api/src/routers/internal_builder.py` | Replaced by capability-bound runner dispatch/callback routes in `sandbox_jobs.py` and the provider-neutral runner protocol. |

## Reorganized tests (19)

These files were not copied under their old names because the architecture or
test layout changed. Their behaviors remain covered by the listed current
surfaces.

| Backup path(s) | Current coverage |
| --- | --- |
| `api/tests/e2e/platform/test_build_plane.py` | `test_solution_build_plane.py` proves Scheduler claim, existing-Worker npm/Vite execution, staged output verification, dist finalization, and artifact reuse. |
| `api/tests/e2e/platform/test_solution_app_host.py` | `test_solution_app_runtime.py` proves the same-origin isolated runtime, launch-cookie exchange, actor token, and policy boundary. |
| `api/tests/unit/services/mcp_server/test_builder_workspace.py` | `services/test_builder_mcp_harness.py`, `services/test_builder_workspace_tool_runtime.py`, and `services/mcp_server/test_gateway.py`. |
| `api/tests/unit/services/test_agent_skill_import.py`, `api/tests/unit/services/test_agent_skills.py` | `services/mcp_server/test_skill_assets.py`, Agent Skill REST E2E coverage, manifest codec tests, and the client Skill bundle/panel tests. |
| `api/tests/unit/test_build_plane.py` | `jobs/test_solution_build_platform_job.py`, `services/test_local_app_build.py`, `services/test_sandbox_runners.py`, and the real-Worker E2E. |
| `api/tests/unit/test_builder_agent_turns.py` | `services/test_builder_agent_turns.py`, `jobs/test_solution_builder_turn_consumer.py`, and `jobs/test_solution_builder_turn_platform_job.py`. |
| `api/tests/unit/test_builder_app_session.py` | `services/test_builder_app_session.py` and `services/test_builder_conversation_access.py`. |
| `api/tests/unit/test_builder_authz.py` | `services/test_solution_access_service.py`, `test_authorization_scopes.py`, private-Solution E2E, and parent-gate repository tests. |
| `api/tests/unit/test_builder_capabilities.py` | Runner-envelope authorization is covered by `services/test_sandbox_runners.py`, sandbox-job router tests, and external-claim/token-gate tests. |
| `api/tests/unit/test_builder_coordinator.py` | Superseded by `services/test_sandbox_runner_provisioning.py`, `services/test_sandbox_runners.py`, and registered PlatformJob tests; there is no permanent coordinator. |
| `api/tests/unit/test_builder_fs_tools.py` | `services/test_builder_fs_tools.py` plus `services/test_builder_workspace_tool_runtime.py`. |
| `api/tests/unit/test_builder_orm_models.py` | Forward-migration E2E, withdrawn-migration tests, and the Builder session/turn/service tests exercise the restored schema. |
| `api/tests/unit/test_builder_turns.py` | Split across `services/test_builder_scaffold.py`, `services/test_builder_agent_turns.py`, `services/test_builder_deploy_sync.py`, and revision UI/service coverage. |
| `api/tests/unit/test_revision_inspection.py` | Builder Code/Changes service and component tests cover tree, file, diff, download, and undo contracts. |
| `api/tests/unit/test_runner_protocol.py` | `services/test_sandbox_runners.py`, `services/test_local_app_build.py`, `test_build_input.py`, and Cloudflare runner tests under `builder-runner/`. |
| `api/tests/unit/test_solution_access_service.py` | Reorganized to `api/tests/unit/services/test_solution_access_service.py`. |
| `api/tests/unit/test_solution_scope_rehome.py` | Scope movement is covered by `services/test_builder_promotion.py`, `services/test_solution_access_service.py`, and promotion/router tests. |
| `api/tests/unit/test_staged_artifacts.py` | Staging is exercised by build PlatformJob, local build, build-input, deploy-sync, and real-Worker E2E tests. |

## Replaced runner image (2)

| Backup path | Disposition |
| --- | --- |
| `builder_runner/Dockerfile` | Replaced by `builder-runner/Dockerfile`, producing `ghcr.io/gobifrost/bifrost-build`. |
| `builder_runner/server.py` | Replaced by the provider-neutral `builder-runner/runner.py` and Cloudflare Workflow adapter. The image runs both the shared Pydantic coding turn and canonical app compilation envelopes. |

## Reorganized client tests (3)

| Backup path | Disposition |
| --- | --- |
| `client/e2e/build.admin.spec.ts` | Renamed and expanded as `client/e2e/builder.admin.spec.ts`. |
| `client/src/components/ui/tiptap-toolbar.test.tsx` | Toolbar behavior is covered through `tiptap-editor.test.tsx` and `markdown-editor-field.test.tsx`; the toolbar remains shared production code. |
| `client/src/pages/settings/LLMConfig.test.tsx` | Builder readiness/configuration moved to the dedicated `settings/Builder.test.tsx`; shared LLM configuration retains its existing coverage. |

## Retired deployment topology (7)

| Backup path(s) | Disposition |
| --- | --- |
| `docker-compose.debug.netbird.yml` | Current main's per-worktree debug harness owns NetBird/port mode. Builder adds no special overlay or public hostname. |
| `k8s/app-host/deployment.yaml`, `k8s/app-host/service.yaml` | Retired. Preview is an API-mounted, same-origin isolated runtime. |
| `k8s/builder-runner/deployment.yaml`, `k8s/builder-runner/network-policy.yaml`, `k8s/builder-runner/service.yaml` | Retired. Local execution uses existing Worker replicas; Cloudflare runs the published image ephemerally. |
| `k8s/builder/deployment.yaml` | Retired with the permanent coordinator. Scheduler/PlatformJob is the durable control plane. |

## Superseded planning documents (4)

The following backup documents are intentionally not restored as active specs
because they prescribe the discarded coordinator/app-host/OpenCode topology:

- `docs/superpowers/plans/2026-07-27-wp3-build-plane.md`
- `docs/superpowers/specs/2026-06-17-agent-skill-bundles-and-capabilities-design.md`
- `docs/superpowers/specs/2026-06-17-code-execution-decision.md`
- `docs/superpowers/specs/2026-07-25-private-solution-builder-design.md`

Their still-valid requirements are carried forward in
`2026-08-16-code-builder-pydantic-integration.md` and
`2026-07-27-private-solution-builder-status.md`: private-by-default projects,
Solution-relative Skills, secure filesystem limits, durable recovery,
same-origin isolated previews, MSP support access, promotion, setup
diagnostics, and full CLI/MCP portability. The immutable remote backup remains
available for historical review without presenting obsolete architecture as
current guidance.

## Behavior changes relative to the backup

- OpenCode and its Builder-only compaction/transcript implementation are
  replaced by the shared Pydantic runtime used by Chat and autonomous Agents.
- Builder-specific attachments, artifact references, tool progress, streaming,
  and usage accounting are deleted in favor of the merged Chat V3 contracts.
- The scheduler never performs the long-running AI or npm/Vite computation.
  It durably claims/fences PlatformJobs and dispatches the compute envelope to
  an existing Worker or Cloudflare.
- Build validation now includes a real production compile inside the repair
  loop. Successful staged output is content-addressed and reused by deployment.
- The production SDK tarball is deterministic across processes, so an
  identical build request hashes identically in Worker and Scheduler processes.
- Preview needs no new DNS name, public port, app-host service, or permanent
  container.
