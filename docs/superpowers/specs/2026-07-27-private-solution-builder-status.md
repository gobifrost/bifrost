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
- `builder-runner/runner.py` bytecode compilation and `git diff --check`: clean.

Live-stack proof restored an accumulated conversation, compacted its context,
repaired a real Tailwind v4 compiler failure, invoked `validate_solution` and
`test_solution_build`, completed `solution.build` on the existing Worker, and
automatically deployed the resulting private preview. No transcript or source
state was discarded during resume.

## Remaining acceptance gates

- Hierarchical platform → organization → user → Solution quota enforcement is
  not part of this version. The shared ledger records provider tokens, cache
  tokens, media usage, reported cost, user, organization, and Solution; Builder
  currently enforces only its per-turn request/token ceilings.
- The supplied Cloudflare Workers token successfully authenticated to the
  configured account. Provisioning logic, image mirroring, Workflow deployment,
  and callbacks have automated coverage, and the production image builds and
  probes locally. A live remote Cloudflare container run requires the current
  `bifrost-build` candidate to exist in GHCR, which cannot happen until this
  dirty worktree is committed and pushed for CI. The default local Worker path
  has the complete live resume/build/deploy proof.
- The branch is intentionally still uncommitted and unpushed. Commit, candidate
  publication, live Cloudflare acceptance, and merge each require Jack's
  explicit approval.

No commit, push, or merge is authorized until the final focused aggregate is
green and Jack explicitly approves the branch.
