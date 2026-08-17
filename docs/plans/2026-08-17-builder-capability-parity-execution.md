# Builder capability parity and Agent Skill hydration

**Owner:** Jack Musick / Bifrost engineering
**Created:** 2026-08-17
**Status:** Ready for implementation after the recovered-Builder checkpoint is
approved
**Execution branch:** `codex/code-builder-pydantic-integration-20260816`
**Base:** `origin/main@16e317e62`
**Depends on:** the reconstructed Builder and shared Pydantic runtime described
in `2026-08-16-code-builder-pydantic-integration.md`

## Purpose

Finish the portability contract that lets a person or coding model build the
same Bifrost resources from the native Builder, MCP, or the CLI without three
sets of product behavior or three handwritten instruction sets.

REST handlers and their domain services remain the canonical behavior and
authorization boundary. A stable operation catalog describes that behavior.
The CLI, MCP, and native Builder are generated or thin adapters over the same
operations. The `bifrost-build` Skill teaches one transport-neutral build
method and progressively loads generated bindings when a harness needs exact
syntax.

This plan also completes portable Agent Skill hydration. `bifrost_get_agent`
must return the canonical `SKILL.md` projection plus a revision-bound file
inventory, and every supported harness must be able to read referenced bundle
files on demand.

## Product outcomes

1. A Builder with permission to change a resource can do everything the
   corresponding CLI or MCP surface can do, including Solution-owned resources
   and an explicitly selected Workspace target.
2. The same operation has one stable identity, one request/response contract,
   one authorization path, one audit path, and recognizable bindings across
   REST, CLI, MCP, and native Builder.
3. Bifrost platform MCP tools use `bifrost_<verb>_<noun>` names where possible.
   The CLI mirrors the resource and verb, for example:
   `agents.create` -> `bifrost_create_agent` -> `bifrost agents create`.
4. A coding model receives only capabilities allowed by its profile, caller,
   target, and policy. Merely having access to an Agent never grants all tools
   statically attached to that Agent.
5. An Agent backed by a Skill can follow `SKILL.md` references from native
   execution, the dynamic MCP gateway, Builder, or a local CLI harness without
   embedding the entire bundle in model context.
6. The normal deployment adds no container. Existing Workers run local coding
   turns and app compilation; Cloudflare remains an optional backend for the
   same runner envelope and image.

## Architectural contract

```text
                         bifrost-build/SKILL.md
                                  |
                         stable operation intent
                                  |
                       canonical operation catalog
                                  |
                  REST handler + domain authorization
                    /             |              \
              CLI adapter    MCP adapter     native Builder
                                                |
                              existing Worker or Cloudflare
```

### Stable operation identity

The internal identifier is transport-neutral and never derived from a Python
function name or FastAPI's generated `operationId`:

```text
agents.create
agents.get
agents.skill_files.read
solutions.build
workspace.files.patch
```

Each catalog entry owns:

- stable operation ID;
- REST method and path;
- request and response Pydantic models;
- public MCP name;
- CLI command path;
- required action scope(s);
- target and resource-access resolver;
- audit event and required side effects;
- synchronous, execution-worker, or `PlatformJob` behavior;
- supported surfaces and a required reason for every exclusion.

Explicit catalog metadata becomes the source for OpenAPI operation identity,
generated Skill references, parity tests, and capability discovery. We do not
use FastAPI's handler-derived IDs as durable identities.

### Public naming rules

- Bifrost-owned MCP tools: `bifrost_<verb>_<noun>` using a singular noun for
  one resource and a plural noun for collections, such as
  `bifrost_create_agent`, `bifrost_get_agent`, and `bifrost_list_agents`.
- CLI: `bifrost <plural-resource> <verb>`, such as
  `bifrost agents create`, `bifrost agents get`, and `bifrost agents list`.
- Internal catalog: `<resource>[.<subresource>].<verb>`.
- Gateway/bootstrap operations remain descriptive exceptions:
  `bifrost_search_capabilities`, `bifrost_execute_tool`, and
  `bifrost_get_execution`.
- User workflows and third-party MCP tools keep their authored/remote names;
  they are not Bifrost platform operations.
- CLI-only lifecycle commands such as login, local watch, and Skill
  installation are recorded as intentional surface exclusions.

Naming migration must update persisted Bifrost system-tool IDs, generated
references, tests, and the CLI contract version together. Do not retain an
unrequested alias/fallback layer. If inventory shows a shipped name cannot be
changed atomically, stop at that specific name for an explicit compatibility
decision.

### Agent Skill descriptor and hydration

`bifrost_get_agent` and dynamic capability discovery return a canonical Skill
descriptor rather than only the materialized `system_prompt`:

```json
{
  "entrypoint": "SKILL.md",
  "instructions": "...canonical SKILL.md...",
  "revision": "sha256:...",
  "files": ["SKILL.md", "references/policies.md"],
  "skill_ref": "opaque revision-bound reference"
}
```

Rules:

- Bundled Agents use the actual `SKILL.md`; `system_prompt` remains only the
  compatibility materialization required by existing execution code.
- Inline Agents receive the existing portable `SKILL.md` projection and a
  one-file inventory.
- `skill_ref` is opaque, revision-bound, and never substitutes for
  authorization. Every file read rechecks current Agent and parent-Solution
  access.
- Storage locations (`_repo`, `_agent_skills`, or Solution storage) are never
  exposed to MCP callers.
- `bifrost_read_agent_skill_file` reads one safe relative path with the same
  size, traversal, and binary-encoding limits on every surface.
- `bifrost_download_agent_skill` exports the deterministic `.skill` archive;
  when used through Chat/MCP it returns the canonical user-facing `ArtifactRef`.
- The file inventory enables progressive disclosure. Reference files are read
  only when `SKILL.md` directs the harness to them.
- A changed revision invalidates the old `skill_ref` with an actionable stale
  response rather than silently mixing bundle versions during one run.

## Execution sequence

No later phase starts until the preceding gate is green. Implementation may be
split into small reviewable commits, but the catalog and authorization
contracts must land before adding more Builder-specific tools.

### Phase 0 — Freeze and checkpoint the recovered foundation

- [ ] Re-run the Builder inventory against immutable backup `1696d8693` and
      confirm every recovered, generalized, and retired path remains accounted
      for after Chat V3 integration.
- [ ] Ensure the worktree contains no unrelated changes or untracked artifacts.
- [ ] Run the existing focused Builder/shared-runtime gates recorded in the
      reconstructed status document.
- [ ] Commit the recovered foundation as one reviewable checkpoint and push the
      candidate branch only after Jack explicitly authorizes commit/push.
- [ ] Publish `ghcr.io/gobifrost/bifrost-build` through CI and complete the
      first live Cloudflare acceptance run before treating Cloudflare as
      delivery-proven.

**Gate:** immutable remote checkpoint, green local Worker path, green candidate
image probe, and no merge action.

### Phase 1 — Canonical operation catalog and parity inventory

- [ ] Add the catalog model and explicit route metadata in shared backend code.
- [ ] Inventory every REST route, CLI command, registered Bifrost MCP tool, SDK
      operation, native Builder operation, and manifest-owned mutation.
- [ ] Classify each operation as exact parity, missing surface, divergent
      behavior, transport-only, or intentionally unsupported.
- [ ] Record target kind, authorization resolver, audit event, side effects,
      async policy, and DTOs for every catalog entry.
- [ ] Establish the `bifrost_<verb>_<noun>` and CLI mapping rules above.
- [ ] Generate a compact operation reference for `bifrost-build` and retain the
      existing detailed CLI reference as generated material.
- [ ] Add CI tripwires for duplicate IDs/names, missing REST routes, unaccounted
      CLI/MCP verbs, stale generated docs, and exclusions without reasons.

**Gate:** every existing surface is accounted for and a representative Agent
operation is generated end to end from one catalog entry.

### Phase 2 — Make REST behavior canonical

- [ ] Convert legacy direct-ORM MCP implementations to thin HTTP wrappers using
      `_http_bridge.py`; do not create a second service path for MCP.
- [ ] Reconcile known drift in Agents, Forms, Tables, Apps, and Events:
      authorization, role propagation, cache invalidation, RepoSyncWriter,
      scheduler wiring, audit registration, validation, and partial-success
      behavior.
- [ ] Use hard structured REST errors for invalid mutations; do not preserve
      silent warn-and-continue behavior.
- [ ] Move reusable business logic out of handlers only where the REST handler
      itself is not already thin.
- [ ] Delete the duplicated ORM/scoping/validation implementations as each
      entity moves. No dead alternate path remains.
- [ ] Generate or validate CLI and MCP request fields from the same DTOs and
      run both DTO and contract-version tripwires.

Suggested vertical-slice order is Agent first (because Skill hydration and the
coding profile depend on it), then Forms, Tables, Apps, Events, followed by the
remaining inventory gaps.

**Gate:** no Bifrost platform MCP mutation bypasses its REST behavior, and each
operation produces the same success, failure, side effects, and audit result
from CLI, MCP, and REST.

### Phase 3 — Finish Agent Skill portability

- [ ] Add the revision/digest field and forward-only migration required for a
      durable Agent Skill revision. Populate it on inline projection, direct
      upload, Solution deploy, and Solution sync.
- [ ] Generalize the existing Agent Skill REST projection/file/download routes
      around the canonical Skill descriptor.
- [ ] Return that descriptor from `bifrost_get_agent` and dynamic capability
      discovery.
- [ ] Replace the private `read_skill_asset` dialect with the canonical
      `bifrost_read_agent_skill_file` binding in native Agent, agent-scoped MCP,
      generic dynamic MCP, and Builder execution.
- [ ] Ensure the dynamic gateway classifies and exposes the file reader for a
      bundle-backed Agent and binds it to the selected Agent/revision.
- [ ] Add deterministic `.skill` export through `ArtifactRef` without exposing
      object-storage paths.
- [ ] Update the Agent UI file explorer and Skill-use indicator to consume the
      same descriptor rather than reconstructing bundle state.

**Gate:** an external harness can discover an Agent, load `SKILL.md`, follow a
relative reference, and execute an allowed capability; inline, uploaded, and
Solution-managed Agents all pass the same contract tests.

### Phase 4 — One transport-neutral `bifrost-build` Skill

- [ ] Rewrite authored Skill guidance around stable operation intent, target
      selection, build lifecycle, safety, and verification.
- [ ] Remove handwritten lists that teach separate CLI/MCP/Builder dialects.
- [ ] Retain only a small bootstrap rule: use dynamic capability discovery when
      present; otherwise load the generated CLI binding.
- [ ] Generate operation and CLI appendices from the catalog and keep CI
      freshness enforcement.
- [ ] Verify `bifrost skill update` installs the same Skill and references for
      Codex, Claude, Copilot, Cursor, Gemini, and other Agent-Skills-compatible
      harnesses.

**Gate:** one authored instruction source, generated bindings only, and no
transport-specific product behavior hidden in prose.

### Phase 5 — Maintained coding profile and dynamic authorization

- [ ] Implement the Bifrost coding experience as a versioned built-in profile,
      not a normal Agent with a permanently copied list of privileged tools.
- [ ] Provide platform enable/disable and policy controls while keeping the
      profile's Skill and required baseline capabilities maintained by Bifrost.
- [ ] Resolve available operations at discovery and again at execution as the
      intersection of:
      1. coding-profile capabilities;
      2. caller action scopes;
      3. resource and parent-Solution access;
      4. selected build target;
      5. platform and organization policy.
- [ ] Keep `solutions.build` as permission to enter/use Builder; every actual
      mutation still requires its resource action scope.
- [ ] Preserve requester, effective actor, owner, and last-updated-by identity.
      Support access never rewrites ownership or hides the assisting operator.
- [ ] Ensure capability search does not disclose forbidden resources or
      schemas and execution cannot rely on stale discovery authorization.

**Gate:** negative authorization tests prove that Agent access, Builder access,
support authority, and resource mutation authority are independent.

### Phase 6 — Native Builder target parity

- [ ] Expose explicit Solution and Workspace target modes in the native Builder
      without making the experience app-only.
- [ ] Give Solution mode complete access to apps, Agents/Skills, workflows,
      modules, forms, tables/policies, configs, claims, files, manifests, build,
      preview, promotion, and deployment when authorized.
- [ ] Give Workspace mode the same accessible entity operations as CLI/MCP.
      Mutations occur in an isolated Builder changeset with validation, diff,
      attribution, explicit apply, and rollback rather than silent live writes.
- [ ] Keep the admin Global Workspace fence for `_repo`; ordinary users cannot
      obtain it through target selection or direct operation execution.
- [ ] Route native Builder capability calls through the same catalog/dispatcher
      as MCP instead of adding Builder-only entity tools.
- [ ] Keep conversation restoration, shared Pydantic compaction, attachments,
      activity history, generated artifacts, and usage accounting on the Chat
      V3 contracts.
- [ ] Run the same coding profile and operation envelope on existing Workers or
      Cloudflare. Local execution remains fully supported and requires no new
      container or public endpoint.

**Gate:** the same representative full Solution and Workspace change can be
completed through CLI, dynamic MCP, and native Builder with equivalent final
state and audit evidence.

### Phase 7 — Builder UX, MSP operations, and governance

- [ ] Rename app-only entry points and copy so the product communicates full
      Solution/Workspace building.
- [ ] Show the active Skill/profile and make Skill reference-file reads visible
      in the collapsed activity history without exposing internal storage.
- [ ] Preserve My work as the default and All customer work as an explicit
      support/admin view with owner, organization, access, status, and updated-by
      filters.
- [ ] Preserve setup gating: hidden for unconfigured ordinary users; actionable
      Settings guidance and live connectivity for administrators.
- [ ] Keep Local/Cloudflare readiness, build, deployment, and callback failures
      in Diagnostics with actionable recovery copy.
- [ ] Register create/edit/apply/build/deploy/promote/support actions in the
      existing audit log.
- [ ] Complete hierarchical usage policy. Portable enforcement dimensions are
      requests, input/output/cache tokens when reported, wall/runtime, and
      sandbox resource duration. Provider-reported dollars remain an observed
      metric rather than the sole portable enforcement unit.
- [ ] Use the most-specific configured per-run allowance (Solution, then user,
      organization, platform default) while still enforcing aggregate ceilings
      at every owning level. Show consumption and remaining allowance to users
      and support staff with permission.

**Gate:** first-class loading, empty, disabled, permission-denied, stale,
conflict, retry, cancellation, resume, build-failure, and deployment-failure
states are visually and behaviorally verified.

### Phase 8 — Verification, candidate, and acceptance

- [ ] Fresh migration from an empty database and upgrade from databases that
      observed the withdrawn Builder tombstones.
- [ ] Operation-catalog, naming, DTO, OpenAPI, CLI, MCP-wrapper, contract-version,
      manifest, Skill generation, and generated-doc tripwires.
- [ ] Agent Skill tests for inline/upload/Solution storage, revision races,
      permission changes, parent access, path traversal, missing files, binary
      files, size limits, deterministic export, and ArtifactRef authorization.
- [ ] Coding-profile tests for every authorization intersection, including
      external users and cross-tenant support access.
- [ ] Targeted backend unit/E2E tests for every changed entity and side effect.
- [ ] Client unit/type/lint checks plus focused Playwright journeys for normal
      builder, support operator, platform admin, setup-blocked user, resume, and
      full Solution/Workspace changes.
- [ ] Shared Scheduler, PlatformJob, Worker, Chat V3, app runtime, Solution
      deploy, SDK, and MCP regression selection.
- [ ] Live local Worker build/preview/deploy/resume proof.
- [ ] Live Cloudflare coding turn and app-build proof using the published
      `ghcr.io/gobifrost/bifrost-build` candidate.
- [ ] Update the recovery inventory and status ledger with exact commands,
      results, unrun broad suites, and any known failure disposition.
- [ ] Request explicit approval before merge.

**Gate:** all acceptance criteria below are evidenced; no known failure is
waived as unrelated or flaky.

## Required migrations

1. A new forward-only Agent Skill revision/digest column and backfill. Do not
   edit or restore any withdrawn Builder migration body.
2. A forward migration for renamed persisted Bifrost system-tool identifiers,
   if the inventory confirms those IDs are stored in Agent configuration.
3. Seed/config changes for the built-in coding profile and scopes must use the
   existing immutable-role/config framework rather than a parallel permission
   table.
4. Quota configuration/ledger changes, if required by Phase 7, extend the
   shared AI usage and policy models; they do not create Builder-only usage
   accounting.

## Acceptance criteria

- Every REST/CLI/MCP/Builder operation is catalogued or intentionally excluded
  with a reason.
- Every Bifrost platform MCP tool follows the approved naming convention or has
  an explicit documented exception.
- No MCP entity mutation contains direct ORM business logic.
- CLI, MCP, and Builder produce equivalent authorization, validation, side
  effects, audit events, and final state for the same operation.
- `bifrost_get_agent` returns canonical Skill instructions, file inventory,
  revision, and an opaque Skill reference.
- A reference file can be progressively loaded through every supported runtime
  without exposing a raw bundle path.
- The `bifrost-build` Skill has one authored workflow; exact transport syntax is
  generated or dynamically discovered.
- The coding profile can be enabled/disabled and never grants a capability the
  caller could not invoke directly.
- Native Builder can target complete Solutions and permitted Workspace
  resources rather than only apps.
- Existing V1/V2 apps, autonomous Agents, Chat, workflows, SDK artifacts, and
  trusted deployments retain their current behavior.
- Local Worker and Cloudflare execute the same versioned runner envelope and
  Skill.
- No new permanent container, public port, DNS record, bespoke job system,
  attachment system, artifact contract, or model loop is introduced.
- Merge occurs only after Jack reviews the evidence and explicitly approves it.

## Superseded and supporting documents

- This plan supersedes the deferred sequencing in
  `2026-04-18-mcp-router-reconciliation.md`; its drift inventory remains useful
  evidence.
- `2026-08-16-code-builder-pydantic-integration.md` remains the architecture and
  recovery record for the already reconstructed foundation.
- `2026-08-17-code-builder-recovery-inventory.md` remains the path-level backup
  accounting ledger.
- `2026-07-27-private-solution-builder-status.md` remains the implementation and
  verification ledger and must be updated as each gate is completed.
