# Private Solution Builder — Reconstructed Status

**Updated:** 2026-08-17
**Integration branch:** `codex/code-builder-pydantic-integration-20260816`
**Base:** main at `16e317e628d57ff4a4e57d179ff71d370445f6b5`
**Source of truth for recovered work:** immutable backup `1696d8693`

## Outcome

Code Builder has been reconstructed on current main without replaying the
withdrawn scheduler implementation or creating another AI harness. It now uses
the shared Pydantic AI runtime merged with Chat V3, the existing Bifrost Worker,
the canonical `PlatformJob` control plane, and the same Solution/CLI/MCP
contracts used by workstation-based builders.

The normal deployment adds no permanent runtime container. The Scheduler owns
durable orchestration, existing Workers run native Builder turns and app
compilation, and the API serves isolated previews behind a transparent
same-origin path. Cloudflare is an optional external execution backend for the
same two runner envelopes and the same `ghcr.io/gobifrost/bifrost-build` image.

The branch is not approved for merge yet. Verification and delivery QA are
recorded below and must be complete before approval is requested.

The recovered foundation is complete, but full CLI/MCP/native Builder parity
and revision-bound Agent Skill reference-file hydration remain active work.
Their execution order and acceptance gates are defined in
[`docs/plans/2026-08-17-builder-capability-parity-execution.md`](../../plans/2026-08-17-builder-capability-parity-execution.md).

## User experience

### Build catalog

- Build is hidden from ordinary users until AI and a sandbox runner are enabled
  and connected.
- An administrator who opens Build early sees an actionable setup state and a
  direct route to Settings → Builder.
- The prompt-led new-app surface creates a private Solution and session, shows
  staged workspace/Agent/opening animation, and transitions directly into the
  workbench.
- The default library is **My work**: owned builds and explicitly shared builds.
- Users with `organization.impersonation` can deliberately switch to **All
  customer work**, then filter by organization, owner, and search text without
  mixing every customer's work into their normal view.
- Platform administrators also receive a fenced **Global Workspace** for
  proposing, validating, applying, and rolling back reviewed `_repo` changes.

### Builder workbench

- A resizable desktop split places the shared Chat/Agent experience beside
  Preview, Code, and Changes.
- Mobile uses explicit Agent, Preview, Code, and Changes panes; the selected
  pane, preview route/device, session, and desktop split are restored locally.
- Sessions and conversations are durable. Reopening a build restores the
  transcript and current/deployed revisions; a queued or running PlatformJob is
  rediscovered and its live progress resumes.
- A failed or cancelled turn may retain an inert workspace checkpoint. **Resume
  saved work** starts a new fenced attempt from that checkpoint; starting a new
  request uses the current immutable Solution revision instead.
- Before a turn may finalize, the Builder validates the Solution and runs the
  real production app compiler. Compiler failures return a bounded actionable
  log to the model, which repairs the source and repeats validation. A green
  staged artifact is retained and reused by deployment.
- Preview shows launch, loading, building, stale, failed, and not-yet-deployed
  states. A restored deployed revision does not rebuild just because the user
  reopened the page.
- Code provides a searchable source tree and file viewer. Changes provides
  immutable revision history, real diffs, download, and history-preserving undo.
- Owners may share view/edit access. Support access can review and edit without
  pretending to be the owner; durable rows retain requester/author identity and
  the platform audit log records privileged actions.

### Agent Skills

- For a bundle-backed Agent, `SKILL.md` is the canonical instruction source and
  becomes the Agent system instructions. The generic prompt field is read-only
  so a second instruction source cannot drift.
- A normal Agent without a Skill continues to use its editable TipTap Markdown
  instruction field. The restored Edit/Preview control uses the shared editor.
- A Solution manifest stores `bundle_path` relative to the Solution root, for
  example `skills/expense-tracker`. It is never relative to `.bifrost/`.
- The Agent UI accepts a `.skill` or `.zip` bundle by drag-and-drop, validates
  its shape and limits, stores a direct upload under Agent-owned storage rather
  than `_repo`, and exposes a file explorer, Markdown/source preview, download,
  and detach action.
- Solution-managed Agent Skills are browseable and exportable but not directly
  editable. Their files change through the owning Solution revision.
- The hidden `read_skill_asset` runtime tool is injected only for the exact
  bundle-backed Agent. Builder visibly identifies the `bifrost-build` Skill so
  users can tell why and when Skill instructions are active.

Current limitation: agent-scoped native execution can read companion Skill
files, but generic dynamic MCP discovery still returns materialized Agent
instructions without a revision-bound bundle descriptor. The parity execution
plan replaces this private dialect with canonical Skill discovery and file-read
operations across native Builder, MCP, and CLI.

## Execution architecture

| Durable operation | PlatformJob responsibility | Compute location |
| --- | --- | --- |
| `solution.builder.turn` | authorization context, lease, retries, cancellation, progress, terminal result | existing Worker by default; optional Cloudflare sandbox |
| `solution.build` | immutable input hash, dispatch, progress, fencing, verified output manifest | existing Worker by default; optional Cloudflare sandbox |
| `solution.deploy` | durable deploy transaction, source/revision attribution, result projection | Scheduler job child process |
| `sandbox.runner.provision` | durable provider setup, diagnostics, retry/error state | Scheduler job child process plus Cloudflare API when selected |

`PlatformJob` is the durable control plane, not the coding harness and not a
new worker. The Scheduler claims and fences work. Long-running AI and npm/Vite
compute is dispatched to the existing Worker or Cloudflare so Scheduler slots
do not sit occupied for the duration.

Native Chat, autonomous Agents, local Builder, and Cloudflare Builder construct
the model loop through `AgentRuntimeRunner`. Builder adds a constrained
workspace tool profile and Solution finalization; it does not duplicate model
streaming, tool events, compaction, attachments, artifacts, or usage recording.
Provider requests update the durable phase to **AI is working** for both local
and Cloudflare execution, so the restored UI never leaves a stale tool name
onscreen during a long model response.

### Context, attachments, and generated artifacts

- Conversation messages, timestamps, tool activity, and attachments use the
  shared Chat persistence and streaming contract.
- Uploaded and generated user files use canonical opaque `ArtifactRef` values
  and the conversation artifact workspace, including SDK and MCP boundaries.
- Immutable Solution source revisions, resumable internal checkpoints, staged
  build inputs, and compiled `dist/` output are internal build artifacts rather
  than user-facing `ArtifactRef` objects.
- Context compaction is the shared provider-neutral Pydantic capability. It
  targets a 24k active context, first clamps oversized messages and clears old
  tool results, then retains a sliding tail while preserving the first user
  message. The UI reports compaction as ordinary Agent activity.
- Builder currently enforces a per-turn ceiling of 80 model requests and two
  million total tokens, with a reserved tool-free wind-down response. Complete
  platform → organization → user → Solution quota policy is not implemented
  yet; provider/cache token and provider-reported cost data are recorded in the
  shared AI usage ledger when the provider supplies them.

## Preview and publication

- Builder previews use an API-mounted `/api/builder-runtime` sub-application.
  There is no second public hostname, port, DNS record, or app-host container.
- A one-time opaque launch code becomes an HttpOnly cookie scoped to one
  Solution/app path. The sandboxed document then receives a short-lived
  `solution_app` actor token bound to the exact viewer, organization, Solution,
  app, and session JTI.
- Generated documents receive an opaque browser origin through CSP/iframe
  sandboxing. The runtime exposes only a reviewed SDK allowlist; actor tokens
  are rejected by ordinary API, MCP, and normal WebSocket authentication.
- Existing V1 and V2 apps remain on the trusted runtime. A promoted release may
  be marked isolated or trusted without rebuilding the reviewed bytes.
- A build is an immutable compiled artifact. Deployment applies that reviewed
  source/build to one private preview or published release, so the same build
  lineage can be deployed or promoted deliberately while the private source and
  conversation remain intact.
- Build inputs are byte-deterministic across Worker and Scheduler processes.
  Identical source, dependencies, SDK, and toolchain reuse the same verified
  staged output rather than compiling twice.

## Authorization and MSP support model

- Scope names follow lowercase Graph-inspired
  `<resource>[.<subresource>].<action>[.all]`; `solutions.build` is the human
  authorization to use Builder, not a runner execution permission.
- Platform Admin is an immutable built-in wildcard role. Platform Operator is
  a sticky compatibility assignment during legacy-check deprecation and grants
  `organization.impersonation`; Builder access remains an independent role
  assignment.
- Platform Builder is a mutable default role containing `solutions.build`.
  Administrators may assign it or define their own role with the same scope.
- Private Solution access is parent-gated across Builder, normal repositories,
  MCP discovery, Agents, apps, forms, tables, and workflows. Ownership,
  collaborator grants, and support authority remain separate from action scope.
- Promotion creates a separate published Solution pinned to the exact green
  revision. It does not mutate the private source project into the published
  installation.

## Setup and operations

- Settings → Builder offers Local and Cloudflare providers.
- Local needs no callback URL or credentials; it provisions the existing
  Worker path and verifies connectivity.
- Cloudflare asks only for Account ID and a constrained API token. Script,
  Workflow, callback address, and stable resource names are server-derived.
- Provisioning is durable and the UI displays saved, provisioned, connected,
  enabled, and blocker states. Builder remains unavailable to normal users
  until all checks pass.
- Diagnostics → Builder exposes provider configuration, connectivity, recent
  provisioning/build/deploy failures, and actionable recovery guidance.
- The external runner image is `ghcr.io/gobifrost/bifrost-build`, consistent
  with the existing `bifrost-*` image names. The image uses the same canonical
  `bifrost-build` Skill and Solution/CLI contracts as workstation builders.

## Recovery inventory against `1696d8693`

The path-by-path inventory is recorded in
[`docs/plans/2026-08-17-code-builder-recovery-inventory.md`](../../plans/2026-08-17-code-builder-recovery-inventory.md).
All 377 backup-changed paths were examined: 339 exist in the reconstructed
worktree, and all 38 absent paths have an explicit replacement or retirement
disposition.

### Reused or generalized

- Private Solution, revision, session, turn, promotion, global workspace,
  runtime-token, and Skill bundle models/services were recovered.
- The Builder-specific model loop was replaced by the shared Pydantic runtime.
- The permanent coordinator was replaced by registered PlatformJobs and the
  existing Scheduler/Worker topology.
- The separate app-host service was replaced by the same-origin API-mounted
  isolated runtime.
- The old runner directory was replaced by the provider-neutral
  `builder-runner` image and Cloudflare Workflow control plane.

### Intentionally retired

- OpenCode-specific Node loop and Builder-only transcript compaction archives.
- Builder-specific streaming, attachment, artifact, and token-accounting paths.
- Permanent Builder coordinator, runner, and app-host deployments.
- A second public app origin, public port, DNS requirement, and configurable
  worker/callback names.
- External app-build mode as a separate architecture; app builds and coding
  turns now share one runner image and dispatch contract.
- Bodies of withdrawn Builder Alembic revisions. Those revision IDs remain
  tombstones; all restored schema is forward-only after the withdrawal.

## Verification status

Current-branch evidence collected in this integration worktree includes:

- fresh-database forward migration: 1 passed;
- Agent Skill REST projection/upload/browse/download/detach: 4 passed;
- Solution deployment and Skill materialization: 40 passed;
- MCP Builder gateway: 1 passed;
- scope, OAuth, actor rejection, contract, and DTO parity: 67 passed;
- PlatformJob/cancellation/scheduler service: 25 passed;
- restored app-session, token-gate, and filesystem/archive safety: 77 passed;
- final focused Builder/shared runtime unit aggregate: 228 passed;
- complete backend unit suite after recovery integration repairs: 5,902 passed,
  3 skipped, and 20 deselected;
- final Builder integration aggregate (forward migrations, private/support
  access, Skills, same-origin runtime, MCP, real Worker build): 57 passed;
- real Scheduler → Worker → npm/Vite build, dist finalization, and exact-input
  artifact reuse is included in that green aggregate;
- final focused client component/service aggregate: 190 passed;
- focused Builder Playwright journey: 4 passed;
- API Pyright and Ruff: clean;
- client TypeScript: clean; ESLint: zero errors and one pre-existing React
  Compiler warning in unchanged `FormRenderer.tsx`;
- production `bifrost-build` Docker image build and exact CI `--probe`: green
  (`schema_version: 1`, `harness: pydantic-ai`);
- exact candidate `684f700ac` published by CI, independently pulled and probed,
  mirrored into Cloudflare's account registry, deployed as the managed
  Workflow/Container runtime, and booted successfully for a live self-test;
- Settings → Builder now reports the Cloudflare provider configured,
  provisioned, connected, enabled, and ready with no setup blockers;
- `builder-runner/runner.py` bytecode compilation and `git diff --check`: clean.
- canonical operation inventory currently accounts for 660 REST pairs, 139
  CLI leaves, 101 MCP tools, 10 native Builder primitives, 16 manifest fields,
  and 19 app-SDK bindings; 72 Agent, Form, Table, App, Event, Workflow,
  Organization, Integration, Workspace Files, Execution History, and Knowledge
  Search, and Role
  operations now supply stable OpenAPI, CLI, MCP, scope, authorization,
  side-effect, and generated Skill metadata;
- operation-catalog, generated-reference, Compose-harness, Skill-mirror, and
  cross-event-loop regression selection: 25 passed and 2 environment-skipped
  mirror checks; API Pyright and Ruff remain clean;
- the Event slice removes the legacy direct-ORM MCP path in favor of eleven
  canonical REST adapters, adds strict target/scope and schedule validation,
  makes audit/manifest/Solution guards consistent across REST/CLI/MCP, and
  forwards persisted tool grants through `20260817_event_mcp_names`; its
  post-generation contract selection passed 343/343, live MCP passed 25/25,
  the 45 focused REST/CLI/Solution checks are green, and shared
  Scheduler/Event processor coverage passed 39/39;
- the Workflow slice removes its direct-ORM MCP path in favor of nine canonical
  REST adapters, adds ordinary-user list/get visibility, reuses the existing
  execution worker and execution envelope, and makes audit, manifest,
  validation, cache/MCP refresh, and Solution guards consistent across
  REST/CLI/MCP. Forward migration `20260817_workflow_mcp_names` preserves
  persisted Agent grants; the focused unit selection passed 360/360, REST/MCP
  protocol and refresh coverage passed 60/60, Solution read-only coverage
  passed 2/2, and the full MCP lifecycle is green;
- the Organization slice removes all five direct-ORM MCP paths in favor of
  canonical REST adapters, aligns the public CLI at `bifrost organizations`,
  and keeps platform administration explicitly outside native coding Builder
  targets. Forward migration `20260817_organization_mcp_names` preserves
  persisted Agent grants; 374 focused unit checks, 49 live MCP/CLI/REST checks,
  and 112 contract/tool-access checks pass, with Alembic current at the sole
  head and generated references fresh;
- the Integration slice removes its remaining direct-database MCP path and
  canonicalizes all six Integration/mapping tools. Internal users discover
  only their Organization's mappings; external/embed sessions remain denied;
  schema-key deletion is confirmed at the REST boundary across browser, CLI,
  and MCP; and REST owns audit plus manifest writes. Forward migration
  `20260817_integration_mcp_names` preserves persisted Agent grants; 446
  focused unit checks, 33 live MCP/CLI/REST checks, 35 MCP protocol checks,
  the external denial, and 3 browser component checks pass;
- the Workspace Files slice removes all seven direct-storage code-editor MCP
  paths and the duplicate App `push_files` mutation in favor of eight
  canonical REST adapters. REST owns platform-admin-only `_repo` access,
  conflict-safe patching, storage/index/cache/preview side effects, and the
  Solution-managed source boundary. Forward migration
  `20260817_workspace_file_names` preserves and deduplicates persisted Agent
  grants; 196 focused architecture/unit checks, 10 live REST/MCP checks, and
  67 DTO/contract/migration checks pass. The operation inventory now contains
  64 canonical operations and 37 uncatalogued MCP tools;
- the Execution History slice removes the final two direct-ORM Workflow-run
  lookup paths in favor of canonical REST adapters. CLI and MCP expose the full
  REST filter and pagination contract with names that remain distinct from the
  dynamic gateway's asynchronous execution receipt. Forward migration
  `20260817_execution_mcp_names` preserves persisted Agent grants; 148 focused
  architecture/MCP/CLI/migration checks, 109 generated-inventory checks, the
  live Worker-backed REST/MCP lifecycle, and 64 DTO/contract checks pass. The
  operation inventory now contains 66 canonical operations and 35 uncatalogued
  MCP tools;
- the Knowledge Search slice removes the final legacy direct-database public
  MCP path. REST, native Chat, and autonomous Agent runs reuse one embedding
  and repository-search service; the MCP adapter calls canonical
  `POST /api/knowledge/search`, where REST independently verifies the Agent
  and derives its organization and namespace boundary. This preserves global
  Agent grounding for authorized organization users without granting them
  direct global-search rights. CLI adds
  `bifrost knowledge search`, and MCP is registered only as
  `bifrost_search_knowledge`. Forward migration
  `20260817_knowledge_mcp_name` preserves Agent grants and platform MCP
  allow/block decisions. The focused architecture/runtime/tool-access/
  manifest/CLI/migration/inventory aggregate passes 464/464; the combined MCP
  protocol/config, complete round-trip, DTO/contract, and context-boundary
  selection passes 129/129. Seven live embedding-backed namespace checks and
  13 Agent Settings component checks also pass. The operation inventory now
  contains 67 canonical operations and 34 uncatalogued MCP tools;
- the Role slice keeps all five existing CLI/MCP operations on their shared
  REST policy boundary and registers them only as `bifrost_list_roles`,
  `bifrost_get_role`, `bifrost_create_role`, `bifrost_update_role`, and
  `bifrost_delete_role`. REST continues to own the platform-admin gate,
  built-in and Solution-management guards, audit events, cache invalidation,
  cascades, and manifest synchronization. Forward migration
  `20260817_role_mcp_names` preserves Agent grants and platform MCP
  allow/block decisions. The architecture/DTO/contract/migration selection
  passes 173/173, the live MCP/REST/authorization/Solution/manifest selection
  passes 14/14, and generated inventory/tool-access checks pass 67/67. API
  Pyright/Ruff and client TypeScript are clean. The operation inventory now
  contains 72 canonical operations and 29 uncatalogued MCP tools;
- the platform-job slice corrects a misleading identity rather than adding a
  surface. `bifrost_get_app_publish_status` read the shared
  `GET /api/platform-jobs/{job_id}` route under an app-specific name, so it is
  now the canonical `platform.jobs.get` operation registered only as
  `bifrost_get_platform_job`, with a matching `bifrost platform-jobs get` CLI
  leaf and its own thin-wrapper module. `bifrost apps publish` still polls the
  same job inline, so no second status contract exists. REST retains
  requester-or-platform-admin visibility. The catalog naming rule now
  normalizes hyphenated CLI resources, which the previous slices never
  exercised. Forward migration `20260817_platform_job_names` renames the
  persisted Agent grant and platform MCP configuration entry. The focused
  catalog/wrapper/migration selection passes 108/108, inventory/DTO/contract
  tripwires pass 67/67, and the live MCP publish-job read plus unknown-job
  denial pass 2/2. A live CLI drive read a real completed publish job and
  returned the REST 404 for an unknown job. The operation inventory now
  contains 73 canonical operations, 140 CLI leaves, and 28 uncatalogued MCP
  tools;
- the candidate CI E2E shard exposed one real test-harness defect: the global
  asyncpg queue pool retained connections across pytest's function-scoped
  event loops. Testing now uses `NullPool` while development and production
  retain configured pooling; a two-loop regression test and the real
  Scheduler → Worker Solution build E2E both pass.

Live-stack proof restored an accumulated conversation, compacted its context,
repaired a real Tailwind v4 compiler failure, invoked `validate_solution` and
`test_solution_build`, completed `solution.build` on the existing Worker, and
automatically deployed the resulting private preview. No transcript or source
state was discarded during resume.

## Remaining work and limitations

- Hierarchical platform → organization → user → Solution quota enforcement is
  not part of this version. The shared ledger records provider tokens, cache
  tokens, media usage, reported cost, user, organization, and Solution; Builder
  currently enforces only its per-turn request/token ceilings.
- Agent, Form, Table, App, Event, Workflow, Organization, Integration,
  Workspace Files, Execution History, Knowledge Search, and Roles now use
  REST-canonical MCP adapters.
  Remaining uncatalogued domains, native Builder dispatch parity,
  revision-bound Agent Skill hydration, generated transport bindings, and the
  maintained coding profile remain sequenced work in the capability-parity
  execution plan.
- The recovered foundation is committed and pushed through `014d212ea`; the
  canonical App and Event checkpoints are pushed through `3c717d110`; the
  Workflow checkpoint is pushed through `c6251e790`; the Organization
  checkpoint is pushed through `367ca7d2c`; and the Integration checkpoint is
  pushed through `85ee5b2a0`; and the Workspace Files checkpoint is pushed
  through `3b3f9a868`. The Execution History checkpoint is pushed through
  `000645e85`; the Knowledge Search checkpoint is pushed through `b4ecf2242`;
  the Role checkpoint and durable resume handoff are complete on the same
  integration branch. It remains
  an integration branch: no pull request or merge action has been taken, and
  merge still requires Jack's explicit approval after the remaining phases and
  delivery QA are complete.

No merge is authorized until the remaining execution phases and final delivery
QA are green and Jack explicitly approves the branch.
