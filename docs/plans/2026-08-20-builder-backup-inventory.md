# Builder backup recovery inventory

**Source of truth:** `1696d8693` (`backup/code-builder-1696d8693-20260807-immutable-official`)
**Reconstructed worktree:** `codex/code-builder-pydantic-integration-20260816` plus its uncommitted working tree
**Updated:** 2026-08-20
**Status:** pre-push accounting; no commit, push, PR, rebase, or merge performed

This inventory accounts for Builder-owned behavior recovered from the immutable
backup. It compares every backup path whose name identifies Builder, private
Solutions, promotions, app runtime/host, Cloudflare runner, or Platform Jobs.
The comparison found 87 paths: 66 remain at the same path and the 21 omissions
below are intentional replacements, test consolidations, or superseded design
material. No omission represents an accidentally discarded product behavior.

## Replaced implementation paths

| Backup path | Current disposition |
|---|---|
| `api/src/builder/coordinator.py` | Removed. Durable Builder turns now use the existing execution Worker and shared Pydantic AI harness; Platform Jobs remain for durable non-agent operations. |
| `api/src/builder/main.py` | Removed with the standalone Builder service. There is no new Builder coordination container. |
| `api/src/routers/internal_builder.py` | Removed with the private HTTP service-to-service protocol. Worker consumers invoke the shared Builder turn service directly. |
| `api/src/services/solutions/builder_authz.py` | Replaced by central `AuthorizationContext`, Builder target authorization, private-Solution access, and runtime-authorization services. |
| `builder_runner/Dockerfile` | Removed. Local and Cloudflare execution use the same Worker/runner envelope; no permanent Builder runner container is introduced. |
| `builder_runner/server.py` | Removed with the bespoke runner HTTP contract. |
| `k8s/builder-runner/deployment.yaml` | Removed; no dedicated Builder runner deployment. |
| `k8s/builder-runner/network-policy.yaml` | Removed with that deployment. |
| `k8s/builder-runner/service.yaml` | Removed with the bespoke HTTP service. |
| `k8s/builder/deployment.yaml` | Removed; Builder agent turns scale through the existing Worker deployment. |

## Consolidated test paths

| Backup path | Current coverage |
|---|---|
| `api/tests/e2e/platform/test_solution_app_host.py` | Replaced by `test_solution_app_runtime.py`, private-Solution tests, and Builder browser journeys. |
| `api/tests/unit/services/mcp_server/test_builder_workspace.py` | Replaced by Builder MCP harness, HTTP bridge, Solution tools, runtime authorization, and workspace operation tests. |
| `api/tests/unit/test_builder_agent_turns.py` | Replaced by the shared turn consumer, runtime profile, Builder turn service, and Pydantic harness tests. |
| `api/tests/unit/test_builder_app_session.py` | Replaced by app-runtime access, app-host/router, and live runtime E2Es. |
| `api/tests/unit/test_builder_authz.py` | Replaced by central authorization, Builder target, runtime authorization, Solution access, and router authorization matrices. |
| `api/tests/unit/test_builder_capabilities.py` | Replaced by canonical authorization-scope, operation-catalog, operation-inventory, DTO parity, and native Builder discovery tests. |
| `api/tests/unit/test_builder_coordinator.py` | Obsolete with the standalone coordinator; Worker consumer and Platform Job tests cover the surviving responsibilities. |
| `api/tests/unit/test_builder_fs_tools.py` | Coverage moved into Builder scaffold, runtime authorization, MCP harness, and Solution workspace tests. |
| `api/tests/unit/test_builder_orm_models.py` | Replaced by the reinstated forward-only migration E2E plus current Builder ORM/contract/service tests. |
| `api/tests/unit/test_builder_turns.py` | Split across Builder turn consumer, shared agent loop, session/runtime profile, scaffold, artifact, and private-Solution tests. |

## Superseded design path

| Backup path | Current disposition |
|---|---|
| `docs/superpowers/specs/2026-07-25-private-solution-builder-design.md` | Intentionally not restored as a live specification. It prescribed a dedicated Builder service/runner and per-Solution Agent identity, both superseded after the scheduler correction and Chat V3 integration. Its historical content remains immutable at `1696d8693`; the current source of truth is the July 27 status specification plus the August 19 authorization/execution plan and this handoff set. |

## Behavior accounting

The reconstructed branch retains or replaces every backup behavior category:

- private Solution ownership, revisions, worktrees, locking, sessions, turns,
  resumability, preview restoration, and publication review;
- full Solution authoring, Organization workspace authoring, and reviewed
  Global repository proposals;
- shared Skill hydration, attachments, ArtifactRefs, generated artifacts,
  progress/activity, usage, compaction, and conversation history;
- local Worker and optional Cloudflare execution through one runner envelope;
- static app build/runtime hosting without a new public port or required public
  hostname;
- direct-person and Role sharing, MSP support catalogs, owner/org filtering,
  promotion destinations, audit attribution, and diagnostics;
- forward-only Builder schema after the withdrawn migration tombstones;
- CLI, MCP, and native Builder operation parity through the shared catalog and
  thin REST bridge.

Before any push, rerun `git diff --check`, generated-reference tripwires, the
scoped backend/client/browser matrix recorded in the resume document, and this
same 87-path omission query. Any new omission must be added here with an
explicit behavior disposition.
