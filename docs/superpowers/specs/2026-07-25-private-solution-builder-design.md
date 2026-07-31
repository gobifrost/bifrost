# Private Solution Builder

**Status:** Revised 2026-07-27 — builder is a skill-bundle agent; generated Python is permanently inert

**Date:** 2026-07-25

**Branch:** `codex/ai-solution-builder-spec`

**Worktree:** `.worktrees/ai-solution-builder-spec`

## Summary

Bifrost should provide a Lovable-style builder without giving every user a
long-lived npm development server and without running npm builds in the API
container.

The product unit is a **private Solution**, not an app draft:

- The user receives a real Solution that can own apps, tables, files, workflows,
  forms, agents, configs, policies, and connection declarations.
- The Solution is private to its owner and sealed from `_repo`, organization,
  and global data by default.
- The builder persists immutable source revisions in object storage. Temporary
  directories are disposable.
- Multiple builder chats can exist, but only one mutating turn may operate on a
  Solution at a time.
- Agent turns can run concurrently across Solutions. Expensive builds use a
  separate bounded FIFO queue, with a default concurrency of one.
- The builder is an Agent record whose `system_prompt` is the `bifrost-build`
  `SKILL.md` body and whose `bundle_path` points at that skill directory. It
  runs through the existing `AgentExecutor`, not a parallel agent loop.
- A separate, secretless runner performs builds. The API never runs npm.
- Generated Python is authored and deployed inert. Execution requires human
  review and binding as a workflow tool.
- Generated apps run on a separate app origin with a Solution-bound renewable
  session, not the viewer's full Bifrost token.
- An admin promotes the same Solution from Private to Company or Global. There
  is no second install and no synthetic owner role.

This is the same broad shape used by mature hosted builders: a control plane,
persistent source snapshots, disposable workspaces, bounded build workers,
static artifacts, and an isolated preview/runtime. It is not an always-on
container per user.

## Why this replaces the current mental model

The current server-side V2 build path calls `npm install` and `vite build`
inside the API process and returns every built file in memory. The callers are
direct Solution deploy, which returns 202 and uses FastAPI `BackgroundTasks` in
the API process, git-connected sync, install-from-repo, and zip install. There
is no browser-builder caller today. A large or hostile build can exhaust the
API container even when the caller presents the work as asynchronous.

Git-connected sync (`POST /api/solutions/{id}/sync`) is the worst offender: in
`api/src/routers/solutions.py:1858-1895` it currently runs clone, npm, and the
Vite build inline in the request handler, not even as a background job, and
must move to the build-job protocol.

There is a second in-process build pipeline: the esbuild/tailwind compiler for
legacy inline, non-`standalone_v2` apps in
`api/src/services/app_bundler/` and `api/src/services/app_compiler/`, invoked
lazily on first view from `app_code_files.py`. It is outside this program
because it compiles administrator-authored trusted apps and is lower risk, but
it is a tracked follow-up because it has the same API-stability exposure.

The current standalone V2 runtime also mounts app code into the same browser
document and passes it the viewer's full bearer token. That is appropriate for
trusted administrator-deployed apps, but it is not an isolation boundary for
generated code. `global_repo_access=false` cannot stop app JavaScript from
ignoring the SDK and using the full user token against another API route.

Moving only npm into another container would fix API stability, but it would
not make user-generated apps private or safe. The builder therefore needs four
boundaries together:

1. private Solution ownership;
2. immutable source revisions;
3. secretless build execution;
4. a Solution-bound browser runtime identity.

## Recovered decisions

The following decisions were recovered from the earlier builder discussion,
the role-scope worktree, existing superpowers specs, and the Obsidian
External-Agent Platform Research note:

- The Solution remains the scope. Do not introduce another project/data scope.
- Private ownership is a parent gate inherited by every Solution-owned entity.
- `global_repo_access=false` is mandatory for user-created private Solutions.
- A builder must never receive a Platform Admin credential.
- Promotion is a privileged Bifrost control-plane operation.
- Source control may be added later, but Git is not required to persist builder
  work.
- Generated Python is never dispatched with the current global engine token.
- Existing Solution deployment is the canonical reconciliation mechanism.
- The current `bifrost solution start` proxy and V2 app lifecycle are useful
  development references, but a hosted builder does not keep Vite alive between
  turns.
- The role-scope program separates action permission, organization reach, and
  resource/data policy. Builder authorization follows the same separation.

## Goals

### POC goal

A permitted internal user can:

1. create a private Solution;
2. ask the builder to make a working app backed by a Solution table and file
   location;
3. watch the turn, build, and deploy statuses;
4. preview and use the app without exposing their normal Bifrost token;
5. edit it through multiple chat turns;
6. inspect the changed files and revision history;
7. undo a turn;
8. download the current Solution workspace;
9. request Company or Global promotion.

The API must remain healthy if the runner times out, crashes, or is OOM-killed.

### Complete product goal

Anything that can be represented in a Solution workspace can be authored in the
builder. Generated Python remains inert after authoring and deploy. It becomes
runnable only after human review and binding as a workflow tool through the
normal imported-workflow review path described in
[Generated Python](#generated-python-is-inert-by-design). It never inherits the
existing superuser engine token.

## Non-goals

- A permanent npm dev server per user, chat, or Solution.
- A general terminal or shell exposed to the model.
- GitHub repository creation as a prerequisite.
- Real-time co-editing.
- User-managed organization role membership.
- Arbitrary build-time npm scripts in the first release.
- Treating source scanning as a sandbox.
- Replacing the existing Solution manifest or deploy semantics.
- Making existing trusted standalone V2 apps use the isolated runtime in the
  first migration.
- Migrating the legacy inline-app esbuild/tailwind compile path off the API
  (tracked follow-up; administrator-authored, lower risk).
- Designing the break-glass support flow (the invariant reserves it;
  specification is deferred until after the POC).

## Product model

### A builder project is a Solution

There is no separate app-project scope. Creating a builder project creates:

- one org-scoped `Solution`;
- `visibility="private"`;
- `owner_user_id=<creator>`;
- `global_repo_access=false`;
- one initial source revision containing a valid Solution workspace;
- one builder conversation;
- usually one scaffolded `standalone_v2` app.

The Solution ID is the scope for all deployed entities and runtime data.

### Skill bundles travel with the Solution

`Agent.bundle_path` is relative to the Solution root and resolves the same way
`ManifestApp.path` resolves an app source directory. It is a new field; it does
not reuse or revive the deprecated `ManifestAgent.path`, whose content moved
inline.

When `bifrost solution deploy` reads a repository containing agent bundles, the
install carries each bundle's `SKILL.md`, `references/`, `scripts/`, and
`assets/`. Bundle files use the same S3 source of truth as other Solution
source through `RepoStorage` and `SolutionStorage`; they never use
`get_module()`.

Bundled `scripts/` are inert readable assets, never an execution surface. This
is what lets a community or consultant repository ship a working
agent-plus-bundle as one installable unit.

### Private is a Solution visibility, not an entity access level

Add two explicit fields to `Solution`:

```text
owner_user_id: UUID | null
visibility: private | shared
```

Existing Solutions migrate to `visibility=shared` and retain current behavior.
Builder-created Solutions require an owner and start private.

These are access metadata on the existing install row. They do not introduce a
new Solution definition, install identity, or data scope: `Solution.id` remains
the install and `solution_id` remains the ownership and scope key everywhere.

The UI derives the three user-facing levels:

| UI level | `visibility` | `organization_id` |
| --- | --- | --- |
| Private | `private` | owner's organization |
| Company | `shared` | organization UUID |
| Global | `shared` | `NULL` |

This keeps the existing organization/global scope model intact.

### Private access invariant

For a private Solution:

- the owner can list, view, edit, build, deploy, preview, and run it;
- other non-admin users receive 404 for the Solution and every owned entity;
- external users are always denied;
- it does not appear in the ordinary administrator Solution catalog;
- administrators may see a pending promotion request and inspect its pinned
  review snapshot, but may not edit or run the private Solution;
- emergency support uses a separate, explicit, audited break-glass action;
- only a platform administrator may promote it or authorize break-glass
  cleanup;
- the owner may request promotion but may not perform it.

Only the owner ordinarily sees, edits, and runs a private Solution. Operator
access is limited to the explicit, audited promotion and break-glass paths.

### One central parent gate

Introduce one `SolutionAccessService` with actions such as:

```text
view
edit
build
run
promote
```

It is the only place that interprets Solution ownership and visibility.
Repositories and routers may call it, but may not reimplement private-owner
logic.

The gate must cover:

- Solution list, detail, export, deploy, sync, capture, and deletion;
- apps and all app asset/render routes;
- workflows and execution resolution;
- forms, agents, events, claims, and configs;
- tables, table policies, and table rows;
- files, file metadata, file policies, and file locations;
- WebSocket subscriptions and execution status;
- CLI and MCP reads that resolve Solution-owned entities.

List queries should filter private parents in SQL. Detail reads return 404 when
the parent is inaccessible.

### Private owner policy bypass

While a Solution is private, its owner must be able to build and test it even
if the generated table/file rules are incomplete.

Add an exact, server-derived authorization fact:

```text
private_solution_owner(solution_id, user_id)
```

It bypasses table/file/entity audience rules only when:

- the target resource has that exact `solution_id`;
- the Solution is still private;
- the actor is still its owner.

It does not set `is_superuser`, grant organization access, or apply to a sibling
Solution. Every bypass is auditable.

When the Solution is promoted, the bypass stops. The Solution's declared access
levels, roles, table rules, and file policies become authoritative.

### Slug identity

Current Solution uniqueness is `(slug, organization_id)`. That would prevent
two users in one company from independently creating a private `todo` Solution.

Replace it with partial uniqueness:

- private: unique `(owner_user_id, slug)`;
- shared Company: unique `(organization_id, slug)`;
- shared Global: unique `slug`.

Private app routing uses Solution ID and app ID, so private apps in the same
organization may also reuse a slug. Promotion re-runs the existing visible-app
slug collision check and requires a rename if necessary.

## Builder permission

### Canonical scope model

Builder authorization is the first production use of the role-scope model, not
a temporary permission system. Roles are bundles of validated scope keys,
effective scopes are placed in the user's access token, and principals expose
one authorization API:

```text
principal.has_scope(scope)
```

Scope keys follow the Graph-inspired Bifrost grammar:

```text
<resource>[.<subresource>].<action>[.all]
```

- keys are lowercase and dot-delimited;
- entity resources use the same stable plural noun throughout the catalog;
- a subresource is used only when the parent exposes meaningfully different
  authorization surfaces, such as table schema versus table documents;
- the action is a specific verb such as `read`, `write`, `execute`, `build`,
  or `publish`;
- the base scope performs the action only where the ordinary resource audience
  and data policy also admit the actor;
- `.all` may bypass an individual resource's audience only inside an already
  authorized effective organization;
- `.all` never means every organization;
- organization reach remains a separate context grant;
- resource ownership, Solution/app binding, and table/file policies are not
  encoded into scope names.

`platform.superuser` is the documented compatibility wildcard and
`organization.impersonation` is the documented effective-organization grant.
They are reserved system scopes rather than examples for naming ordinary
route actions. New action scopes must follow the grammar above and be registered
in the code-owned catalog before a role, token, or route may reference them.

The Builder uses the stable action scope:

```text
solutions.build
```

This name is consistent with the convention: `solutions` is the protected
resource family and `build` is the action. It means:

> May create and modify builder projects and private Solution source within the
> project/resource boundary that separately admits the current user.

It does not grant organization reach, another user's project, publication,
role management, integration grants, platform settings, or global `_repo`
access.

### Role and token control in this release

- Add `solutions.build` to the code-owned authorization scope catalog as a
  privileged, custom-role-assignable action scope.
- Store it in first-class `Role.scopes`; do not store or mirror it in the
  legacy free-form `Role.permissions` JSON.
- Resolve a user's effective scopes as the union of their assigned roles.
- Mint the validated effective scope set into every human access token through
  the shared authentication service.
- Gate every Builder entry point through:

```text
principal.has_scope("solutions.build")
```

- Keep the project/Solution access service as the independent resource gate.
  A valid action scope is necessary but never sufficient to access a specific
  project.
- Platform administrators satisfy the check through the catalog's reserved
  `platform.superuser` wildcard during the RBAC migration.
- External users remain denied by the Builder persona gate even if a role is
  misconfigured with `solutions.build`.
- Role create/update remains platform-administrator-only until dedicated role
  management scopes land.

An assignment change is reflected when a token is minted or refreshed in the
initial foundation. The role-scope program's token-version and revocation
release later makes privileged scope removal immediate without changing
Builder routes or scope names.

### Route control after the broader RBAC migration

The same catalog and `has_scope` dependency expands route family by route
family. Each non-public route must ultimately declare exactly one
authorization classification: ordinary authenticated compatibility, a
cataloged human action scope, or an explicitly named non-human actor contract.
A coverage test fails when a new protected route has no classification.

Migration uses shadow comparison before enforcement. Existing
`is_superuser`, provider-membership, role-name, and ad-hoc permission decisions
remain authoritative for an endpoint family until its parity matrix passes.
After cutover, the scope decision becomes authoritative and the legacy branch
is removed. The end state is:

```text
allowed =
  valid token/session
  AND principal.has_scope(required_action)
  AND may_target(effective_organization)
  AND resource_access_policy(resource, principal)
  AND data_policy(operation, row_or_path, principal)
```

Human, app, embed, and execution credentials use the same scope catalog.
Non-human credentials contain only an attenuated subset plus immutable
resource bindings; `actor_type` selects the credential contract but does not
itself grant API permissions.

## Roles and sharing

The builder may author access declarations and propose role names, but ordinary
builders may not create privileged organization roles or assign users to them.

For the POC:

- the owner runs a private Solution through the private-owner bypass;
- the builder can declare entity access levels and policy rules;
- an admin promotion review maps those declarations to existing roles or
  creates the reviewed roles;
- admins invite users and manage membership;
- the builder user cannot self-grant `roles.manage` through generated source.

Later, `roles.manage` carried in a Solution-bound credential could let approved
Solution owners manage runtime membership only for that bound Solution. The
resource binding supplies the narrowness; it is not encoded as an ad-hoc suffix
in the action-scope key. It is intentionally not implied by `solutions.build`.

### Private deploy cannot mutate shared control-plane resources

The current Solution deployer may materialize role rows and activate runtime
entities. A private deploy must not cause those shared side effects.

While a Solution is private:

- requested role names remain portable declarations in source;
- entity-role junctions remain empty because the private-owner gate supplies
  runtime access;
- deploy cannot create, rename, assign, or delete organization/global roles;
- deploy cannot create or read organization integration mappings, OAuth
  mappings, credentials, or connection grants;
- declared connection requirements remain unresolved;
- deploy cannot create or mutate organization/global config values;
- Solution-owned config schemas and values are available only within the exact
  Solution scope;
- custom claims are emitted only inside the bound Solution runtime;
- schedules, events, autonomous agents, and generated workflows cannot activate
  shared runtime side effects.

Config isolation is enforced by the `Config.solution_id` storage boundary and
exact-match resolution described below, not by deployer convention alone.

Promotion materializes only the administrator-reviewed role mappings and
connection grants. It does not clear the runtime-blocked state on generated
workflows, schedules, events, or autonomous agents; those follow the separate
human-reviewed workflow-tool path described below.

## Source and revision model

### Object storage remains the source of truth

Temporary directories are working copies. Persistent source is an immutable
revision archive stored through Bifrost's S3 abstraction:

```text
_solution_builder/{solution_id}/revisions/{revision_id}/source.zip
```

This works with DigitalOcean Spaces, AWS S3, MinIO, SeaweedFS, and any backend
already supported by Bifrost. The browser and builder containers never receive
S3 credentials. Internal API endpoints stream artifacts to and from storage.
Every internal artifact API, including revision download/upload, staged
build-artifact upload, and finalize copy, streams with bounded memory; the API
process never buffers a whole zip or `dist/`.

The existing:

```text
_solution_artifacts/{solution_id}/source.zip
```

continues to mean "the source that produced the last successful deployed
install." It is not the builder's draft history.

### Data model

Add:

```text
SolutionBuilderProject
  solution_id                 PK/FK
  current_revision_id
  deployed_revision_id
  promotion_status            none | requested
  created_at
  updated_at

SolutionSourceRevision
  id
  solution_id
  parent_revision_id
  restored_from_revision_id
  conversation_id
  created_by
  source_sha256
  size_bytes
  summary
  created_at

SolutionBuilderSession
  id
  solution_id
  conversation_id
  user_id
  created_at
  updated_at

SolutionBuilderTurn
  id
  session_id
  requested_by
  base_revision_id
  output_revision_id
  build_job_id
  deploy_job_id
  status
  error
  created_at
  started_at
  completed_at
```

Reuse the existing `Conversation` and `Message` records for chat history and
tool-call presentation. The builder session is the typed link between a
conversation and a Solution; do not make the generic Chat page infer behavior
from `extra_data`.

### Config value scoping

Add a nullable `solution_id` column to `Config` and a partial unique index on
`(solution_id, key) WHERE solution_id IS NOT NULL`. Private-Solution config
values are stored with the owning `solution_id` and resolved by exact match
only. Existing `(integration_id, organization_id, key)` uniqueness and
organization/global config behavior are unchanged and continue to use rows
where `solution_id IS NULL`.

### Turn semantics

Each builder turn:

1. acquires the existing per-Solution write lock before mutation;
2. downloads the current revision through the internal artifact API;
3. safely extracts it into a new 0700 temporary directory;
4. runs the agent with tools rooted to that directory;
5. validates the workspace shape;
6. stores a new immutable revision;
7. updates `current_revision_id`;
8. queues build and deploy;
9. releases and deletes the temporary directory in `finally`.

The source revision auto-applies when the agent turn succeeds. The user does
not accept each individual diff.

Build failure does not discard source:

- `current_revision_id` remains the user's latest source;
- `deployed_revision_id` remains the last successful preview;
- the UI shows that preview is behind source and displays the build error;
- the next turn can repair the failed revision.

### Undo

Undo creates a new revision whose content is restored from the selected older
revision. It records `restored_from_revision_id`, then builds and deploys like
any other turn. History stays immutable and auditable.

### Multiple chats

One user may have multiple conversations for a Solution. Every mutating turn
uses the latest `current_revision_id`, not a session-private fork.

Only one mutating turn per Solution runs at once. Different Solutions can run
agent turns concurrently. The UI tells a stale conversation when another
session changed the Solution.

Branching/collaborative merges are deferred.

## Agent coordinator

### Trust boundary

Add a trusted `builder` service. It consumes builder-turn and build jobs from
RabbitMQ and uses Redis for locks, heartbeats, cancellation, and queue state.

It does not need:

- a database URL;
- S3 credentials;
- the Bifrost JWT signing secret;
- a Platform Admin token;
- Kubernetes API access.

Each queue message carries a short-lived, one-job capability minted by the API.
That capability is bound to:

- job ID;
- Solution ID;
- requesting user;
- allowed internal builder operations;
- expiry and JWT ID (`jti`).

The builder uses internal streaming APIs for revision download/upload, agent
execution, build artifact upload, deploy handoff, and status. The capability
cannot call normal platform administration routes.

RabbitMQ and Redis connection strings still exist in the trusted parent
process. They are not exposed through any agent tool and no generated program
runs in that process.

### Agent framework

The builder does not get its own agent runtime. It is an `Agent` record whose
`system_prompt` is the body of
`plugins/bifrost/skills/bifrost-build/SKILL.md` and whose `bundle_path` points
at that skill directory. Builder turns execute through the existing
`AgentExecutor` path: `build_agent_system_prompt()` in
`api/src/services/execution/agent_helpers.py` assembles the prompt, and
`resolve_agent_tools()` resolves the tools.

This deletes `BuilderAgentRuntime` and `InternalLoopRuntime` as concepts.
Reimplementing a tool loop would fork the platform's agent behavior and
re-earn artifacts, delegation, Toolbox gating, and workspace tool intersection
that already exist. The builder contributes its tools — the root-scoped
workspace file operations — and its bundle — `bifrost-build` — not a loop.

Two prerequisites are unbuilt today and owned by this program:

- `Agent.bundle_path`: ORM, `AgentCreate`/`AgentUpdate`/`AgentPublic`
  contracts, portable `ManifestAgent` field, `manifest_generator` and
  `github_sync` round-trip, and CLI/MCP flags.
- `read_skill_asset(path)`: a bundle-root-scoped system tool registered by
  `get_system_tools()` in `api/src/services/mcp_server/server.py` and exposed
  by `resolve_agent_tools()` whenever `bundle_path` is set. It reuses the
  CodeQL-recognized realpath + `startswith` barrier; the existing
  `WorkspaceRoot._resolve` in `api/src/services/builder/fs_tools.py` is the
  reference implementation to reuse rather than reinvent.

When `bundle_path` is set, `build_agent_system_prompt()` injects the bundle
contract paragraph from Part A.4 of the agent-skill-bundles design. `SKILL.md`
is not separately prompt-stuffed: `references/` and `assets/` load on demand
through `read_skill_asset`.

### Model tools

The coding model receives only typed tools such as:

```text
list_files
read_file
search_text
write_file
apply_patch
delete_file
make_directory
validate_solution
```

These eight operations are workspace tools. `read_skill_asset(path)` joins them
as an invisible system tool whenever the builder Agent has a bundle.

There is deliberately no build tool: builds are queued automatically after the
turn commits its revision (see Turn semantics), never triggered by the model
against the mutable workspace. There is deliberately no general shell,
subprocess, environment, network, S3, Redis, RabbitMQ, database, Bifrost API,
or Kubernetes tool.

Every filesystem tool:

- accepts only relative paths;
- rejects absolute paths, `..`, NUL, and platform-specific path tricks;
- resolves the canonical parent under the session root;
- rejects symlinks, hardlinks, devices, sockets, and FIFOs;
- enforces file count, per-file, and total-workspace limits;
- returns bounded output;
- writes atomically.

Archive extraction uses the same rules and rejects duplicate/conflicting paths,
zip bombs, and oversized expansions.

### AI configuration

Add optional `builder_model` to global AI settings, rendered with the same
searchable model combobox used for agents. It resolves the model for the
builder Agent, whose `llm_model` field is the natural runtime home:

```text
Builder model: <model id> | Use platform default
```

Resolution is:

```text
builder Agent llm_model = builder_model ?? global model
```

The dev POC configures the existing OpenAI-compatible provider endpoint for
OpenRouter and loads the OpenRouter key from 1Password into the dev instance.
The key is never committed, copied into a revision, or injected into either
builder container.

This is dev-environment setup requested for the POC, not a production
dependency. Production uses the provider configured in global AI settings.

## Build system

### No npm in the API

The API must stop calling `SolutionAppBuilder.compile_dist()` locally for any
server-side build.

Deploy paths become:

- a bundle containing a valid prebuilt `dist/`: deploy directly, as today;
- a bundle needing a server build: create an app-build job and wait for its
  artifact;
- build service unavailable: return a clear 503/build-unavailable result;
- never fall back to npm in the API container.

This applies to:

- the new builder's deploys;
- direct Solution deploy;
- git-connected Solution sync;
- install-from-repo;
- zip install.

The CLI can continue to build locally and ship prebuilt `dist/`.

The migration is additive:

1. add the build worker and job protocol;
2. prove artifact parity with `SolutionAppBuilder`;
3. add the new builder on the job protocol;
4. move direct deploy, Git sync, install-from-repo, and zip-install callers;
5. remove the API subprocess implementation.

There is no executable npm fallback in the API at the end of any migrated
caller path.

### Trusted coordinator and secretless runner

Use two services in Compose and separate Deployments in Kubernetes:

```text
builder
  trusted job coordinator
  RabbitMQ + Redis
  internal API job capabilities
  existing AgentExecutor path
  bundle-backed coding-agent file tools
  never executes generated code

builder-runner
  fixed Node/Python toolchain
  no RabbitMQ, Redis, DB, S3, AI, JWT, or Kubernetes credentials
  no service-account token
  no platform network access
  executes one bounded job at a time
```

They must not be sidecars in the same Kubernetes pod because containers in a
pod share a network namespace. Separate pods allow an egress-deny NetworkPolicy
on the runner.

The coordinator sends a source archive to the runner over a narrow internal
protocol. The runner returns a validated file manifest and file streams. The
coordinator validates every output path and uploads each file through the
internal artifact API.

The runner is disposable. It purges its temp directory after every job and may
restart after each job in the first release to prevent cross-job persistence.

### Build queue

Agent turns and builds are separate resources:

- multiple LLM/chat turns may run concurrently across Solutions;
- one mutating turn per Solution;
- build concurrency defaults to one instance-wide;
- `BUILDER_MAX_CONCURRENT_BUILDS` may raise it;
- jobs run FIFO within each user, with fair scheduling across users;
- queued status is visible immediately;
- cancellation removes queued work or terminates the active runner job.

The queue protects memory without making chat globally single-threaded.

Build jobs move to the runner. Deploy jobs still require database access for
reconciliation and artifact finalize, so they stay in the API. They move off
in-process FastAPI `BackgroundTasks` onto the same RabbitMQ job protocol so
they survive pod restarts; today's deploy jobs die with the pod and depend on
orphan reconciliation. Git sync's inline clone, npm, and Vite execution moves
to the same protocol rather than running in the request handler.

### Fixed build contract

The first release does not run arbitrary package scripts or a user-supplied
Vite config.

The runner uses:

- a Bifrost-owned Vite configuration;
- a pinned toolchain image;
- `npm --ignore-scripts` semantics;
- a curated/preloaded package catalog;
- no arbitrary registry or internet access;
- fixed build and test commands;
- production source maps disabled;
- CPU, memory, PID, file, output, log, and time limits.

This supports the standard React/Vite/Bifrost SDK stack and common pure-JS UI
packages.

Packages requiring lifecycle scripts, native builds, custom Vite plugins, or
arbitrary build commands are refused with a clear unsupported-dependency
error.

Fuller npm parity later uses a trusted dependency resolver that downloads
integrity-pinned package tarballs and stores them as artifacts. The runner
still receives no general network and still ignores lifecycle scripts. Truly
arbitrary build scripts require a stronger disposable runtime and are not
silently enabled.

Source remains downloadable. A project with unsupported dependencies can
continue locally through the Bifrost CLI and deploy a prebuilt `dist/`; the
hosted builder does not pretend that standard managed Kubernetes provides an
unrestricted local shell.

### Build artifacts

Do not return all `dist/` bytes to the API in one Python dictionary.

Store staged output under:

```text
_build_artifacts/{build_job_id}/{app_id}/...
```

The build job records:

- source revision and SHA;
- toolchain version;
- app ID and dependency digest;
- status and resource outcome;
- output file manifest and hashes;
- logs, truncated to a configured limit.

After the Solution DB commit, object-storage copy operations move staged files
to `_apps/{app_id}/dist/`. Staged output is deleted after finalize or by the
cleanup sweep.

An idempotency key of source SHA + app ID + toolchain version may reuse a
successful build.

### Runner hardening

Use the existing worker hardening as a baseline:

- non-root UID/GID;
- `allowPrivilegeEscalation=false`;
- all capabilities dropped;
- read-only root filesystem;
- writable tmpfs only;
- `automountServiceAccountToken=false`;
- `RuntimeDefault` seccomp;
- no host mounts or Docker socket;
- strict CPU, memory, PID, and ephemeral-storage limits;
- ingress only from the coordinator;
- egress denied;
- no shared writable cache between users.

The fixed build contract avoids requiring nested user namespaces or
`CAP_SYS_ADMIN` on managed Kubernetes.

## Runtime and preview

### Why the generated app needs a different origin

An iframe on the same origin is visual isolation, not a security boundary. The
generated app could read the platform's local storage, reach the parent DOM,
or use the user's full token.

Builder-generated apps therefore use a configured app origin, for example:

```text
Control plane: https://bifrost.example.com
App host:      https://apps.example.com
```

Both origins may route to the same Bifrost API deployment. The browser never
receives an S3 URL. The app-host routes stream static artifacts through the
existing storage abstraction.

The app origin serves a minimal app-host shell, not the normal Bifrost SPA. It
contains no control-plane authentication UI, settings, or general application
router.

A distinct origin does not require a second domain, a new container, or
operator proxy work. The browser origin tuple is scheme, host, and port, so a
second **port** on the same host is a full origin boundary:
`https://bifrost.example.com:8443` is as isolated from
`https://bifrost.example.com` as a separate subdomain. The app host is the
same API deployment answering on that second port with the minimal app-shell
instead of the SPA.

The shipped deployment artifacts (Compose bundle and Kubernetes manifests)
preconfigure the port-based app origin: one extra listener and port mapping
that Bifrost ships, same containers, same hostname, no new DNS record. An
operator who launches the stack gets a working Builder with zero origin
setup. `BIFROST_APP_ORIGIN` is an override for deployments that prefer a
sibling subdomain (`apps.example.com`), not a setup step.

The one caveat: deployments that terminate TLS at an external proxy listening
only on 443 for the hostname (some Cloudflare/ingress configurations) must
either pass the second port through or use the subdomain override. The admin
status card explains this when the configured app origin is unreachable.

In dev/Compose, the app host uses a second localhost port
(`http://localhost:<port2>` is a genuinely distinct origin from
`http://localhost:<port1>`). `debug.sh` boots the stack with the app-origin
port preconfigured so Builder works out of the box in dev, and the Playwright
capstone drives the port-based origin. The fail-closed stance remains: if the
distinct app origin is unreachable or explicitly disabled, Builder is
unavailable rather than falling back to the control-plane origin.

### Launch and renewable session

The control-plane UI uses its normal user session to request a one-time launch
code bound to:

- user;
- Solution;
- app;
- requested path/query/hash;
- expiry of roughly 60 seconds.

It opens:

```text
https://apps.example.com/launch/{one_time_code}
```

The app host redeems the code, creates an app-host session, and redirects to
the real app path. Tokens do not appear in query strings or Referer headers.
The renewable session is held only in a host-only, `HttpOnly`, `Secure` cookie
on the app origin.

The app receives a short-lived access token with claims such as:

```text
actor_type: solution_app
actor_user_id
solution_id
app_id
organization_id
scopes
jti
exp
```

`scopes` is a validated, attenuated subset of the same code-owned catalog used
by human roles. It contains only the runtime actions approved for this app
launch; it never contains Builder or publication authority such as
`solutions.build` or `solutions.publish`. The initial runtime surface includes
only:

```text
tables.documents.read
tables.documents.write
files.content.read
files.content.write
workflows.execute
executions.read
```

`executions.read` remains constrained to executions created by the token's
JTI/session, and every other scope remains constrained to the bound Solution
and app. Adding an SDK endpoint does not add authority automatically: its scope
must exist in the catalog, be included in the app grant, and be required by the
app-runtime route.

The token is renewable through the app-host session while the user's launch
authorization remains valid. A 10-15 minute access-token lifetime limits theft;
it does not limit how long the app can stay open. Users experience it like a
normal app.

This extends the existing app-embed token pattern, but binds the session to the
real user and Solution rather than a shared HMAC embed identity.

### Runtime middleware

Add Solution-runtime enforcement analogous to `EmbedScopeMiddleware`.
Enforcement uses route dependencies and tags on the app-host router, not a
regex path allowlist; the existing middleware's regex approach can fail open
as routes evolve and must not be replicated.

A `solution_app` token:

- may load only its bound app and artifacts;
- must hold the cataloged action scope required by each runtime route;
- may access only declared tables/files/configs in its bound Solution;
- may execute only workflows owned by its bound Solution;
- may read only executions created by its JTI/session;
- cannot call users, roles, organizations, settings, generic Solutions,
  integrations, OAuth, `_repo`, or arbitrary admin APIs;
- cannot override `solution_id` or organization through headers/query params.

For `solution_app` tokens, table/file/config resolution is an exact
`solution_id` match only. There is no organization/global cascade fallback and
no `_repo` fallback. An undeclared or unmatched resource is 404 and is never
auto-created. This sealing is a property of the builder runtime path
implemented in Work Package 6, not of the `global_repo_access` flag.

Exactness matters because cascade-by-name could resolve a declared table name
onto an organization/global row the owner can read, turning a name collision
into unintended data exposure.

The seal has one designed loosening mechanism, deferred past the POC: a
per-Solution `shared_data_access` flag, default off, settable only by an
administrator as part of promotion review. When enabled it allows the
Solution's runtime to reach declared organization/global tables and files,
with the actor's normal policies as the gate. It is deliberately a separate
flag from `global_repo_access` (which gates code imports); code-reach and
data-reach are different risks and the review screen shows both. Until that
flag lands, exact-match resolution is unconditional. Its persistence,
enforcement, and review flow are specified after the POC; they are not part
of Work Packages 0-8.

The private owner's exact bypass is evaluated server-side from
`actor_user_id`, not trusted from a token boolean.

### Browser sandbox and CSP

The builder preview iframe uses:

```text
sandbox="allow-scripts allow-same-origin allow-forms allow-downloads"
```

`allow-same-origin` refers to the separate app origin, not the Bifrost control
origin. The generated app still cannot access the parent document.

Private generated apps start with a restrictive CSP:

```text
default-src 'self'
script-src 'self'
connect-src 'self'
object-src 'none'
base-uri 'none'
frame-ancestors <configured control origin>
```

Styles, images, and fonts receive the narrow additions required by the standard
scaffold. Arbitrary browser egress is not enabled by default. Future connection
grants may expand `connect-src` after admin review.

### Paths and parameters

The canonical app-host path includes unambiguous IDs:

```text
/{solution_id}/apps/{app_id}/{path...}?query#hash
```

The app's `BrowserRouter` receives the corresponding basename. Normal top-level
app navigation therefore uses real browser paths, query parameters, refresh,
back/forward, and deep links.

Inside the builder:

- the iframe owns its internal history;
- a small host bridge reports path/query/hash changes with `postMessage`;
- the builder preview toolbar shows an address field;
- editing that field navigates the iframe;
- the builder URL may persist the selected preview path;
- opening in a new tab uses the same one-time launch flow.

The bridge exposes navigation, reload, theme, logout, and optional download
requests. Generated code cannot directly manipulate the outer Bifrost shell.

### Iframe limitations

Compared with today's same-document trusted runtime:

- no direct access to Bifrost React context or parent DOM;
- no accidental inheritance of platform CSS;
- popups, top navigation, clipboard, fullscreen, and OAuth require explicit
  sandbox flags or host-bridge capabilities;
- the parent address bar does not change automatically while embedded;
- third-party cookies are not a supported auth mechanism.

Ordinary app routing, forms, file selection/upload, Web SDK calls, query
parameters, downloads, and responsive layouts continue to work.

These limitations are intentional for generated code. Existing trusted apps
remain same-document until an explicit migration.

## Generated Python is inert by design

Bifrost rejected in-house sandboxing for model-authored code in
`docs/superpowers/specs/2026-06-17-code-execution-decision.md`. Running
model-authored code with real Bifrost credentials is RCE-by-hallucination, so
generated Python never executes under the trusted model or in the trusted
workflow pool.

The builder may author and validate Python source, but private deploy stamps
generated workflows, schedules, events, and autonomous agents as
runtime-blocked — a persisted flag on the entity row, not derived metadata —
regardless of what the source declares. Every workflow, schedule, event, and
autonomous-agent dispatch path re-checks that state.

Promotion does not clear the runtime-blocked state. A generated workflow
becomes runnable only when a human reviews it and binds it as a workflow tool
through the normal imported-workflow review path. There is no builder-specific
bypass.

The gate is authorship and review, not a technical sandbox. Once a person has
reviewed a generated workflow it runs like any other workflow — in the existing
engine, with full `ExecutionContext` and SDK, under the caller's normal
permission model. Nothing about it stays second-class. What is refused is
running code on the model's say-so alone, which is why the review step cannot
be automated away.

The `external` runner is the only conceivable path for untrusted code, and it
is protocol-only, deferred, and out of scope for this program.

## Promotion

### Promotion changes visibility, not identity

The private Solution is already a real install. Promotion keeps its
`solution_id`, data, files, source history, and entity ownership.

- Private to Company: set `visibility=shared`, keep `organization_id`.
- Private to Global: set `visibility=shared`, rehome the existing Solution and
  its install-owned entities through the current scope-change path.

The existing scope-change path is `PATCH /api/solutions/{id}`
(`update_solution`). Under the per-install write lock, it re-stamps owned
entities' `organization_id`, but the current list covers only `Workflow`,
`Application`, `Form`, `Agent`, `CustomClaim`, and `Table`. Promotion to Global
must extend re-homing to every `solution_id`-bearing table, including
`file_metadata`, file policies, `policy_rule`, `event_sources`,
`event_subscriptions`, and the new builder tables.

Config values are deliberately not re-homed on scope change today because they
are instance-owned and keyed by organization. With Solution-owned config
values, promotion carries rows bearing the Solution's `solution_id` with the
install. Organization-scoped values that the Solution referenced but did not
own still require operator re-entry at the new scope, and the promotion review
screen must say so.

Private-to-shared promotion moves the Solution row between partial unique
indexes. Conflict checking is atomic across the target Company or Global slug
index and the visible-app slug check.

The promotion transaction materializes the reviewed roles and connection
grants and redeploys the pinned revision before `visibility=shared` becomes
observable. A failure leaves the Solution private and unchanged.

### Admin review

The promotion screen pins one successfully deployed revision and shows:

- source diff from the prior deployed revision;
- apps, workflows, tables, files, forms, agents, configs, and claims;
- requested entity access levels and role mappings;
- table/file policies;
- npm dependencies;
- generated Python and its inert/review state;
- declared connections/integrations and requested egress;
- `global_repo_access` state and any requested `shared_data_access`;
- build/test results;
- data/scope changes;
- artifact and source hashes.

The admin chooses Company or Global, maps/creates reviewed runtime roles, and
assigns users. Builders cannot use promotion to grant themselves organization
authority.

Promotion is a replay deploy: a distinct deployer mode re-runs the pinned
revision and materializes the previously suppressed, admin-reviewed role
creation, entity-role junctions, and connection grants inside the promotion
transaction. Runtime-blocked schedules, events, autonomous agents, and
generated workflows stay blocked. It is not a visibility-flag flip plus role
mapping.

Promotion requires:

- current revision equals last successful deployed revision;
- build and validation are green;
- no unresolved role/connection/policy requirements;
- generated Python remains runtime-blocked pending separate human review as a
  workflow tool;
- `global_repo_access` remains false unless the admin explicitly changes it.

## Builder UI

### Entry points

Provide both:

- **Solutions:** `New with AI` and `Open Builder`;
- **Apps:** `Build an app` and `Edit in Builder` for builder-owned Solution
  apps.

Both navigate to:

```text
/solutions/{solution_id}/builder
```

Do not put the workflow inside generic `/chat`.

### Workspace

Use a resizable builder layout:

```text
┌──────────────────────┬────────────────────────────────────────┐
│ Chat / sessions      │ Preview                                │
│ tool progress        │ route + reload + device controls       │
│ queued/build status  │                                        │
├──────────────────────┴────────────────────────────────────────┤
│ Files / diff / revision history drawer                        │
└───────────────────────────────────────────────────────────────┘
```

Top-level actions:

- Private badge and owner;
- build/deploy status;
- Undo;
- Download source;
- Open app;
- Request promotion.

Extract and reuse the existing chat message, composer, streaming, and tool-call
presentation components. Builder state, APIs, routes, and permissions remain
separate from ordinary conversations.

### Revision behavior

The UI distinguishes:

- **Source:** latest auto-applied revision;
- **Preview:** last successfully deployed revision.

A failed build leaves the last-good preview visible with a clear stale badge.
Selecting a revision shows its file diff and build/deploy outcome.

## Availability and admin experience

### Heartbeat

The builder service publishes a Redis heartbeat with a short TTL containing:

```text
coordinator_ready
runner_ready
agent_ready
build_ready
version
last_error_category
```

The API exposes:

- a minimal availability result to permitted users;
- detailed diagnostics to platform admins.

### Feature visibility

For ordinary users, Builder entry points appear only when:

- they have `solutions.build`;
- they are not external;
- coordinator, runner, builder Agent, and configured model are healthy;
- a distinct app origin is configured.

Admins always see a Builder setup/status card, including:

- service disconnected;
- RabbitMQ/Redis unavailable;
- runner unavailable;
- AI not configured;
- app origin missing/misconfigured;
- image/version mismatch;
- last health transition.

The feature fails closed. It does not run builds in the API when the service is
missing.

### Required connectivity

The trusted coordinator needs:

- RabbitMQ;
- Redis;
- the internal Bifrost API;
- the secretless runner service.

The runner needs only:

- coordinator-initiated ingress;
- temporary local storage.

It does not need a database, object storage, queue, AI, Bifrost, or Kubernetes
credential.

## Cleanup and retention

### Temporary work

- Per-turn directories use `TemporaryDirectory`-equivalent lifecycle and
  delete in `finally`.
- Build runner directories are UUID-named and purged after every job.
- The runner restarts after a failed/OOM/timed-out job.
- Startup and periodic sweeps remove directories not associated with a live
  lease.
- Kubernetes `emptyDir`/tmpfs handles pod replacement.
- Compose does not use a persistent workspace volume.

### Durable work

- Source revisions remain until the user/admin deletes the Solution or an
  explicit retention policy prunes them.
- Last deployed source remains in the existing source-artifact location.
- Staged build artifacts expire after successful finalize or a short failure
  retention window.
- Logs have size and time retention limits.
- Orphaned object uploads are reconciled by a periodic DB-to-storage sweep.

## Resource controls

Initial configurable defaults:

| Resource | Default |
| --- | --- |
| Private Solutions per user | 10 |
| Mutating turns per Solution | 1 |
| Concurrent builds instance-wide | 1 |
| Build timeout | 10 minutes |
| Source archive | 50 MiB |
| Expanded source | 200 MiB |
| File count | 5,000 |
| Single source file | 5 MiB |
| Build output | 100 MiB |
| Captured build log | 1 MiB |
| Runner memory | 2 GiB |
| Runner CPU | 2 cores |

Admin-configurable per-org/user quotas may add build count, AI spend, storage,
and concurrency controls. Every AI call continues to create `AIUsage` records
linked to the builder conversation and Solution.

## Failure semantics

| Failure | Result |
| --- | --- |
| Agent/model error before mutation | No new revision |
| Agent error after writes | Temp workspace discarded; no revision |
| Revision upload failure | Turn fails; current pointer unchanged |
| Validation failure | New source is not persisted unless represented as an explicit failed revision policy; POC discards |
| Build failure | Source revision remains current; preview remains last-good |
| Runner OOM/crash | Build job failed; API/coordinator remain healthy |
| Deploy DB failure | Staged artifacts retained briefly; deployed pointer unchanged |
| Artifact finalize failure | Deploy job reports finalize failure and is retryable/idempotent |
| Coordinator death | job lease expires; startup recovery marks/requeues safely |
| Builder unavailable | UI hidden for users; admin sees diagnostics; no API fallback |
| Solution promoted during turn | lock/version check aborts stale private turn |

The accepted POC sharp edge is asymmetric: build failures persist as repairable
revisions, while a validation failure discards the turn's entire output. A
failed-revision representation may be added later if this proves painful in
practice.

## Security invariants

1. A model tool cannot name or traverse outside its turn root.
2. Generated source is never executed in the trusted coordinator.
3. The build runner contains no platform or storage credential.
4. The API never launches npm for a server build.
5. A generated app never receives the viewer's normal Bifrost bearer token.
6. A private runtime token cannot name another Solution or organization.
7. Builder runtime resolution is exact-`solution_id`; there is no cascade or
   `_repo` fallback for `solution_app` tokens. The admin-set
   `shared_data_access` flag is the sole designed, post-POC exception.
   (`global_repo_access=false` continues to seal code imports.)
8. Private-owner bypass is exact to one owner and one Solution.
9. A private deploy does not create shared roles or connections and does not
   activate schedules, events, autonomous agents, or generated workflows.
10. Generated Python never executes at all under the trusted model; it is
    authored and deployed inert, and becomes runnable only after human review
    as a workflow tool.
11. Promotion pins reviewed source and artifact hashes.
12. A build crash cannot crash or OOM the API.
13. One user's temp workspace or runner state is not reused by another user.
14. Internal artifact APIs stream; no build or revision payload is buffered
    whole in API memory.

## Implementation work packages

### 0. Authorization baseline

- Rebase and land the additive #473 role/scope foundation in this worktree:
  code-owned catalog, first-class `Role.scopes`, effective-scope resolution,
  token claims, `principal.has_scope`, built-in Platform Admin compatibility,
  role API/UI, migrations, and tests.
- Register `solutions.build` in that catalog and grant it through roles.
- Remove the Builder's transitional `Role.permissions` lookup; Builder routes
  authorize from the principal's effective scopes.
- Add the canonical naming grammar and route-classification contract above to
  catalog validation and tests.
- Keep broader route enforcement and execution-token cutover independently
  staged; the Builder consumes the foundation without making unfinished RBAC
  phases a hidden prerequisite for unrelated routes.
- Make `solution_app` the first attenuated non-human consumer of the shared
  scope catalog rather than a permanent actor-type-only allowlist.
- Create the implementation issue/PR breakdown after review.

### 1. Private Solution foundation

Today the entire `/api/solutions` router surface, roughly 30 endpoints, is
gated by `CurrentSuperuser`. The private-access invariant is therefore
vacuously true because non-admins cannot reach any Solution route. This work
package's core job is to open a non-superuser Solution surface safely through
new owner-scoped private-Solution routes gated by `SolutionAccessService`,
while existing administrator routes remain superuser-only.

- Add owner/visibility schema and partial unique indexes.
- Add `Config.solution_id` and exact Solution-scoped config resolution.
- Add centralized Solution access service.
- Integrate the Work Package 0 authorization service into the private routes.
- Add private create/list/detail/update/delete routes.
- Enforce `global_repo_access=false`.
- Cover every Solution-owned entity and private-owner policy bypass.
- Suppress shared role, connection, schedule, event, agent, and workflow side
  effects while private.
- Add promotion request/admin promotion skeleton.

Exit: owner succeeds; another normal user and an external user receive 404
across Solution, app, table, file, workflow, and asset paths.

### 2. Source revisions and sessions

- Add project/revision/session/turn models.
- Add streamed revision artifact APIs.
- Scaffold a valid initial Solution.
- Add safe archive/file tool library.
- Add restore/undo and source download.
- Reuse existing conversation/message persistence.

Exit: a user can create a private project, produce immutable revisions, reopen
multiple sessions, and restore an older revision without Git.

### 3. Dedicated build plane

- Add build and deploy job schemas/protocol.
- Add trusted `builder` and secretless `builder-runner` services.
- Migrate server-side npm callers to the build job protocol in the additive
  sequence above, then remove it from `SolutionAppBuilder` in the API.
- Add staged per-file artifacts and post-commit copy.
- Route direct deploy, git sync, install-from-repo, and zip install through
  build jobs when no `dist/` is present.
- Move API-owned deploy reconcile/finalize work off FastAPI
  `BackgroundTasks` onto RabbitMQ jobs, and move Git sync out of the request
  handler onto the same protocol.
- Add queue concurrency, cancellation, timeout, and cleanup.

Exit: forcing runner OOM/timeout fails only the job; API health and a concurrent
normal request remain green. A prebuilt CLI deploy still works when the build
plane is unavailable; a server-build request returns 503 rather than running
npm.

### 4. Agent skill-bundle prerequisites

- Add `Agent.bundle_path` to the ORM, Agent create/update/public contracts,
  portable `ManifestAgent` content, `manifest_generator`/`github_sync`
  round-trip, and CLI/MCP flags.
- Keep deprecated `ManifestAgent.path` inert; do not reuse it for bundles.
- Add bundle-root-scoped `read_skill_asset(path)` to `get_system_tools()` and
  expose it through `resolve_agent_tools()` whenever `bundle_path` is set.
- Reuse the CodeQL-recognized containment barrier from
  `WorkspaceRoot._resolve`.
- Extend `bifrost solution deploy` so Solution source carries `SKILL.md`,
  `references/`, `scripts/`, and `assets/` through
  `RepoStorage`/`SolutionStorage`.
- Add manifest round-trip, deploy, tool-gating, and path-traversal tests.

Exit: a deployed Solution preserves an Agent's bundle, the prompt receives the
bundle contract, and the Agent can read only assets beneath its own bundle
root.

### 5. Builder agent execution

- Add builder job capabilities and execute turns through the existing
  `AgentExecutor`.
- Create the builder Agent record with the `bifrost-build` `SKILL.md` body as
  `system_prompt` and its skill directory as `bundle_path`.
- Resolve `builder_model` into the builder Agent's `llm_model`.
- Wire the eight root-scoped workspace tools into `resolve_agent_tools`.
- Link AI usage to Solution/session/turn.
- Persist new revision and enqueue build/deploy.
- Add per-Solution turn serialization and cross-Solution concurrency.

Exit: OpenRouter-backed builder Agent turns edit only the hydrated workspace,
create a revision, and queue a bounded build without exposing secrets or
forking the platform's agent runtime.

### 6. Isolated app host

- Add app-origin launch/session flow.
- Add Solution-bound runtime token and middleware.
- Add app-host asset/runtime routes.
- Add CSP, sandbox, and navigation bridge.
- Enforce exact-`solution_id` table/file/config resolution with no cascade or
  `_repo` fallback.
- Keep existing trusted same-document apps unchanged.

Exit: generated code cannot read the control-plane token/DOM or call a route
outside its Solution; the owner can use the app continuously through token
renewal; nested paths/query/hash survive refresh and open-in-new-tab.

### 7. Builder UI and admin settings

- Add builder route and both entry points.
- Extract reusable chat components.
- Add preview, route bar, file/diff drawer, revisions, Undo, download, and
  promotion request.
- Add builder model setting.
- Add heartbeat/status and admin setup card.
- Hide the feature for ordinary users when unavailable.

Exit: the authoring/preview slice of the browser POC can be test-driven
without CLI/Git; the full capstone completes with Work Package 8.

### 8. Promotion

- Add pinned-revision review summary.
- Add a replay-deploy mode that enables reviewed role and connection
  materialization inside the Company/Global promotion transaction.
- Extend Global re-homing to every `solution_id`-bearing table.
- Add target partial-index and visible-app slug conflict checks.
- Show which referenced organization-scoped config values require re-entry.
- Add role mapping/assignment handoff to admins.
- Enforce readiness gates and audit.

Exit: an admin can promote the exact reviewed revision; owner bypass disappears
and ordinary roles/policies take effect.

## Verification

### Backend unit and E2E

- permission, ownership, visibility, and promotion matrix;
- private partial uniqueness and promotion collisions;
- private deploy creates no shared role/mapping and starts no schedule, event,
  autonomous agent, or generated workflow;
- all parent-gated entity repositories and direct routers;
- table/file owner bypass exactness;
- exact runtime data resolution and `_repo` code sealing;
- archive traversal, symlink, hardlink, zip-bomb, and size limits;
- revision pointer atomicity and restore history;
- per-Solution lock, build-queue concurrency, and per-user fairness;
- Agent bundle manifest round-trip and Solution deploy carrying bundle files;
- `read_skill_asset` bundle-root containment and automatic system-tool
  exposure;
- build job idempotency, cancellation, recovery, timeout, and OOM;
- no API subprocess/npm assertion;
- staged artifact hash/copy/finalize;
- app-session issue, renew, revoke, and the allowed/denied route matrix;
- solution/app/execution binding and IDOR attempts;
- generated Python runtime-block persistence and dispatch-path re-checks.

### Frontend unit

- extracted chat components remain compatible with Chat;
- permission/health-derived entry visibility;
- source-vs-preview state;
- queued/running/failed/succeeded states;
- route bridge and preview address field;
- revision selection and Undo;
- model fallback display;
- promotion readiness.

### Playwright happy path

One capstone spec:

1. admin grants Alice `solutions.build`;
2. Alice creates a private Solution;
3. Alice asks for a small tracker with a table and file upload;
4. agent turn streams and creates a revision;
5. build queues, succeeds, and deploys;
6. preview loads through the separate app origin;
7. create/read/update rows and upload/read a file;
8. navigate to a parameterized route and refresh;
9. Bob cannot list/open the Solution, app, table, file, or assets;
10. generated app attempts raw cross-Solution/admin calls and receives 403/404;
11. Alice requests a change, then Undo restores the prior behavior;
12. admin promotes the pinned revision to Company;
13. owner bypass stops and the configured runtime role admits the assigned user.

### Operational/adversarial

- OOM the runner while polling API health;
- kill coordinator during every turn/build/deploy phase;
- submit malicious paths, archives, package metadata, Vite config, postinstall,
  source imports, output symlinks, and oversized logs;
- inspect runner environment and filesystem from an allowed build fixture and
  prove no platform secret exists;
- prove runner cannot reach API, object storage, queues, or Internet;
- attempt iframe parent/localStorage access;
- exfiltration attempt blocked by CSP/network policy;
- replay and cross-bind app launch/session tokens;
- run two Solutions concurrently and two turns against one Solution.

## POC completion bar

The POC is ready for Jack to test drive only when:

- the feature is role-permitted and unavailable-by-default;
- private ownership covers tables/files/assets, not only the Solution page;
- the API performs no npm build;
- source survives container restarts;
- the last-good preview survives a failed build;
- Undo works;
- the preview uses a separate origin and Solution-bound renewable token;
- OpenRouter is configured from 1Password without committing a secret;
- a runner OOM does not affect API health;
- the Playwright capstone passes;
- generated Python is visibly inert and cannot be dispatched before human
  review as a workflow tool.

## Related references

- `docs/superpowers/specs/2026-06-04-solutions-v2-app-model-design.md`
- `docs/superpowers/specs/2026-06-07-solution-start-local-dev-design.md`
- `docs/superpowers/specs/2026-06-24-solution-storage-scope-redesign.md`
- `docs/superpowers/specs/2026-06-17-agent-skill-bundles-and-capabilities-design.md`
- `docs/superpowers/specs/2026-06-17-code-execution-decision.md`
- `docs/superpowers/specs/2026-04-27-chat-v2-program-design.md`
- `docs/superpowers/specs/2026-04-27-chat-v2-sandbox-bwrap-findings.md`
- `docs/plans/2026-04-16-esbuild-app-bundler.md`
- `docs/plans/2026-07-02-solutions-file-policies-audit.md`
- `.worktrees/473-role-authorization-scopes/docs/plans/2026-07-10-role-based-authorization-scopes.md`
- `api/src/services/solutions/app_build.py`
- `api/src/services/solutions/source_artifact.py`
- `api/src/services/solutions/write_lock.py`
- `api/src/core/embed_middleware.py`
- `api/src/services/execution/template_process.py`
- `k8s/worker/deployment.yaml`
