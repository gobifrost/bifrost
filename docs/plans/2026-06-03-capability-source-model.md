# Capability Source Model: Git-Owned Capabilities, Runtime `_repo`, and Agent-Friendly Hydration

Status: design spike for review

Audience: Claude/Codex reviewers and Bifrost maintainers

Purpose: ground a possible source-model evolution in the current Bifrost implementation before any schema or runtime changes are attempted.

## Executive Summary

Bifrost currently has a single runtime workspace rooted at S3 `_repo/`. Workflows, modules, app source, and split manifest files live there. The database stores runtime registrations and environment bindings. Workflow execution is not repo-aware: it resolves a workflow UUID to a DB row, reads `path` and `function_name`, then the worker loads the Python file from Redis/S3 using that path. App builds are similar: the app DB row stores `repo_path`, the bundler materializes that path from `_repo/`, builds it, and writes artifacts under `_apps/`.

That current design gives agents a major advantage: everything is visible as one workspace. But it also makes ownership, Git review, branching, client-specific work, shared-code reuse, and environment cloning hard. The goal should not be to make the execution engine dynamically execute from many Git repos. The safer target is:

```text
Git source repos -> install/sync layer -> S3 `_repo/` runtime tree -> DB registry -> existing engine
```

The engine can remain path-based. The new system should make Git repos, capabilities, shared dependencies, environment locks, and hydrated workspaces first-class above the runtime tree.

The smallest credible direction is not "just better folders." It is:

1. Add a real `Capability` concept.
2. Keep `_repo/` as the installed runtime tree.
3. Treat Git repos as authoring/review sources, not runtime imports.
4. Add an environment lockfile that records which source refs are installed where.
5. Add a hydrate command that assembles one coherent local workspace for humans and agents.
6. Gradually move apps toward normal Vite/React project shape while preserving current app bundler compatibility.

## Non-Goals

- Do not make workflow execution choose a Git repo or branch at runtime as the first step.
- Do not copy shared code into every capability.
- Do not make S3 `_repo/` the long-term collaboration source of truth for Git-gated work.
- Do not split every one-off workflow into its own repo.
- Do not make agents reason across disconnected folders without a generated, coherent workspace.
- Do not change tenant secrets, OAuth tokens, or environment-specific values into portable source artifacts.

## Current System: Grounding in Existing Code

### Runtime Source Tree

`api/src/services/repo_storage.py` defines `RepoStorage`, a thin S3 wrapper scoped to `_repo/`.

Known behavior:

- Callers pass paths relative to `_repo/`, such as `workflows/test.py`.
- `RepoStorage._repo_key()` prefixes paths with `_repo/`.
- `read`, `write`, `delete`, `list`, `list_with_metadata`, `list_directory`, and `exists` operate on S3 objects.
- `write` returns a SHA-256 hash.

Current implication:

```text
S3 object key: _repo/workflows/test.py
Runtime path: workflows/test.py
```

This is the canonical runtime file namespace. It is not currently multi-repo. It is one installed tree.

### File Index

The repo instructions and codebase indicate `file_index` is a text/search index, not a universal content source of truth.

Current responsibilities:

- Text writes are indexed for search and metadata.
- Binary and app source reads should go to S3 through `RepoStorage`.
- Python module reads for workflow execution use Redis/S3 module cache, not `file_index`.

Current implication:

Any source-model redesign must not assume `file_index` can replace `_repo/`. It is an index over `_repo/`, not the installed source tree.

### Workflow Registry

`api/src/models/orm/workflows.py` stores all executable user code in the `workflows` table.

Relevant fields:

- `id`: workflow UUID.
- `name`, `display_name`, `description`, `category`, `tags`.
- `function_name`: Python function name.
- `type`: `workflow`, `tool`, or `data_provider`.
- `organization_id`: `NULL` means global.
- `path`: relative path from workspace root.
- `parameters_schema`.
- endpoint, API key, timeout, access, role, and ROI metadata.

Important constraint:

```text
UniqueConstraint("path", "function_name", name="workflows_path_function_key")
```

Current implication:

The natural source identity for a workflow is already:

```text
path::function_name
```

This is the migration bridge. A capability-owned workflow does not need a new runtime call mechanism. It can still be:

```text
capabilities/halo_ticketing/workflows/triage.py::triage_ticket
```

### Workflow Execution

`api/src/services/execution/service.py:get_workflow_for_execution()` looks up the DB row by workflow UUID and returns metadata only:

- `name`
- `function_name`
- `path`
- `timeout_seconds`
- `time_saved`
- `value`
- `execution_mode`
- `organization_id`
- `type`
- `cache_ttl_seconds`

The docstring says workers load code through Redis cache with S3 `_repo/` fallback.

`api/src/jobs/consumers/workflow_execution.py` reads the workflow metadata, resolves workflow org scoping, and passes `file_path` plus `function_name` in worker context.

`api/src/services/execution/worker.py` then:

1. Reads `function_name` and `file_path` from context.
2. Calls `get_module_sync(file_path)`.
3. Calls `load_workflow_from_db(code=loaded_code, path=file_path, function_name=function_name)`.

Despite some older comments saying "DB-first", the active execution path is:

```text
workflow UUID
  -> DB row
  -> path/function_name
  -> Redis module cache
  -> S3 `_repo/{path}` fallback
  -> exec module
  -> find decorated function by function_name
```

Current implication:

The engine does not care which Git repository produced the file. It only needs a stable installed path and a DB registration.

### Python Virtual Imports

`api/src/services/execution/virtual_import.py` installs a `MetaPathFinder` in worker processes.

Key behavior:

- It skips known stdlib and third-party top-level module names.
- It converts import names to possible file paths.
- Example conversion:

```text
shared.halopsa -> shared/halopsa.py
shared.halopsa -> shared/halopsa/__init__.py
```

- It fetches each candidate path through `get_module_sync`.
- If the module index shows children under a path, it creates a namespace package.

`api/src/core/module_cache_sync.py` provides the sync Redis/S3 path:

```text
get_module_sync(path)
  -> Redis key bifrost:module:{path}
  -> S3 _repo/{path}
  -> cache back to Redis
```

Current implication:

Shared code can already be installed once under stable runtime paths and imported normally:

```python
from shared.halo.client import HaloClient
from capabilities.halo_ticketing.modules.parsers import parse_ticket
```

The loader does not need to know whether `shared/halo/client.py` came from a shared Git repo or was written in the in-app editor.

### App Registry and Bundling

`api/src/models/orm/applications.py` stores app metadata.

Relevant fields:

- `id`
- `name`
- `slug`
- `repo_path`
- `organization_id`
- `published_snapshot`
- `access_level`
- `description`
- `dependencies`

The model docstring says app files are stored in S3 at `_repo/{repo_path}/` and indexed in `file_index`.

`api/src/services/app_bundler/__init__.py` builds apps by:

1. Taking `app_id`, `repo_prefix`, `mode`, and `dependencies`.
2. Materializing source from `_repo/{repo_prefix}` into a temporary directory.
3. Running Tailwind processing.
4. Synthesizing `_entry.tsx`.
5. Synthesizing `node_modules/bifrost/index.js` for platform exports and component re-exports.
6. Running esbuild.
7. Writing artifacts to app storage under preview/live paths.

Current implication:

Apps are also runtime-path driven:

```text
Application.repo_path = apps/my-app
Bundler reads _repo/apps/my-app/*
```

A capability-owned app can be represented without changing this:

```text
Application.repo_path = capabilities/halo_ticketing/apps/console
Bundler reads _repo/capabilities/halo_ticketing/apps/console/*
```

The larger app-platform question is separate: the current bundler hides a lot of React/Vite shape behind Bifrost conventions. That may be one reason agents do worse than in a plain Vite project. The source model can support a future "normal Vite app" layout, but it does not solve that by itself.

### Manifest Models

`api/bifrost/manifest.py` defines portable-ish manifest models.

Current workflow manifest:

```yaml
workflows:
  <uuid>:
    id: <uuid>
    name: ...
    path: workflows/onboard.py
    function_name: onboard_user
    type: workflow
    organization_id: ...
    roles: [...]
    role_names: [...]
    access_level: ...
    endpoint_enabled: ...
```

Current app manifest:

```yaml
apps:
  <uuid>:
    id: <uuid>
    path: apps/my-dashboard
    slug: my-dashboard
    name: My Dashboard
    dependencies:
      package: version
```

Current form and agent manifests already carry portable content inline, with environment-specific fields kept alongside but conceptually separate.

Current integration, config, table, event, MCP, organization, and role manifests are also represented in the same split manifest system.

Current implication:

Manifests already know enough to register source-backed entities from paths. They do not currently know which capability owns a group of entities or which Git source produced a path.

### Manifest Import

`api/src/services/manifest_import.py` is the key import surface.

Known behavior:

- It prefetches existing entities in bulk.
- Workflows are cached by natural key `(path, function_name)` plus ID.
- Apps are cached by slug.
- Tables are cached by `(name, organization_id)`.
- Integrations are cached by name.
- Configs are cached by `(key, integration_id, organization_id)`.
- Role names can be resolved to role UUIDs for portable imports.
- `target_organization_id` can rewrite `organization_id` for cross-environment import.

Workflow import:

```text
if manifest workflow file exists:
  resolve workflow
  upsert by natural key or ID
```

App import:

```text
resolve app
upsert app row
sync/compile preview from mapp.path
```

Integration import has non-destructive upsert behavior for config schema and mappings. This preserves user-set config values and OAuth tokens where possible.

Current implication:

The import layer is the right place for repo/source awareness. It already translates source files and manifests into DB runtime registrations. Runtime execution does not need to become multi-repo aware to install source from multiple repos.

### CLI Sync and Watch

The CLI already has path/prefix mechanics around sync and watch. From code search:

- `_sync_files` and related helpers collect local files.
- `repo_prefix` can map local paths to server paths.
- `watch` processes local changes and incoming server file updates.
- App repo paths are detected from pushed files.
- `.bifrost/` is treated specially.

Current implication:

There is already a CLI concept of pushing a local directory into a path prefix. Capability hydration can build on this instead of inventing all file movement from scratch.

## Current Source-of-Truth Matrix

| Area | Current durable source | Runtime read path | Important note |
| --- | --- | --- | --- |
| Python workflow source | S3 `_repo/` | Redis module cache -> S3 `_repo/` | DB row stores `path` and `function_name`. |
| Python shared modules | S3 `_repo/` | Virtual import -> Redis/S3 | Module name maps to path. |
| App source | S3 `_repo/{Application.repo_path}` | Bundler materializes from S3 | App metadata lives in DB. |
| App artifacts | S3 `_apps/{id}/...` | App shell/storage | Built output, not source. |
| Searchable text | `file_index` DB | Search/index only | Not canonical source content. |
| Workflow registration | DB `workflows` | Execution service | Natural key is `(path, function_name)`. |
| App registration | DB `applications` | App routes/bundler | `repo_path` points into `_repo/`. |
| Forms | DB + manifest inline content | DB | Path is deprecated for form YAML. |
| Agents | DB + manifest inline content | DB | Path is deprecated for agent YAML. |
| Tables | DB + manifest | DB | Definitions/policies are portable-ish; data is runtime state. |
| Integrations | DB + manifest | DB | Config schema portable; OAuth/token values are environment state. |
| Config values | DB + manifest | DB/cache | Secrets are not portable values. |
| OAuth tokens | DB | DB | Environment-owned. |
| Roles/orgs | DB + manifest | DB | Cross-env role-name resolution exists. |

## Problem Statement

Bifrost is "almost too embedded":

1. The current single `_repo/` workspace gives agents broad context and simple imports.
2. The same single workspace makes Git ownership and review boundaries unclear.
3. Client-specific and one-off functionality lives near shared functionality, increasing duplication risk.
4. Smaller repos would improve ownership, but agents lose the ability to search the full useful world.
5. App authoring does not feel as reliable as a plain Vite/React project, likely because the platform conventions are too implicit and too unlike standard React projects.
6. Cloning an environment is not just cloning source. It involves source, DB registrations, tables, integration schemas, config keys, OAuth mappings, role/org bindings, and secrets.

The proposed direction must preserve the "single coherent workspace" advantage for agents while adding Git review boundaries and deployment provenance.

## Key Distinction

Do not conflate these three concepts:

1. Source repo: where humans and agents commit reviewed code.
2. Runtime tree: what Bifrost installs into `_repo/` and executes/builds from.
3. Hydrated workspace: what a developer/agent sees locally while working.

Today these are too close to the same thing.

Target:

```text
Source repos:
  github.com/company/bifrost-shared
  github.com/company/halo-ticketing
  github.com/company/client-a-automation

Runtime tree:
  _repo/shared/...
  _repo/capabilities/halo_ticketing/...
  _repo/capabilities/client_a_automation/...

Hydrated workspace:
  /tmp/bifrost-env-client-a/
    shared/
    capabilities/halo_ticketing/
    capabilities/client_a_automation/
    .bifrost/
    bifrost.env.lock.yaml
    generated/
```

The runtime engine remains path-based. The install/hydrate tooling becomes source-aware.

## Proposed New Concepts

### Capability

A capability is a deployable group of Bifrost functionality.

It can own:

- workflows
- tools
- data providers
- apps
- forms
- agents
- tables
- integration definitions
- config schema requirements
- event sources/subscriptions
- capability-local modules
- tests
- docs

Proposed conceptual model:

```yaml
id: cap_halo_ticketing
slug: halo-ticketing
name: Halo Ticketing
version: 0.4.1
source:
  type: git
  url: git@github.com:company/halo-ticketing.git
  ref: 9ac31fd
runtime_root: capabilities/halo_ticketing
dependencies:
  shared-halo:
    source: git@github.com:company/bifrost-shared-halo.git
    ref: v1.4.2
    runtime_root: shared/halo
```

The slug can be URL/display friendly (`halo-ticketing`), but runtime roots that contain Python modules should be import-safe (`halo_ticketing`). Otherwise `from capabilities.halo_ticketing...` cannot map to the installed path through the current virtual import hook.

Runtime paths under that capability might be:

```text
capabilities/halo_ticketing/workflows/triage.py
capabilities/halo_ticketing/modules/normalization.py
capabilities/halo_ticketing/apps/console/
```

This is not just a folder convention. The system must know:

- which installed paths are owned by the capability
- which DB entities are owned by the capability
- which source repo/ref produced them
- which dependencies were resolved
- which environment bindings are required

### Source Install

A source install records one checked-out Git source installed into one runtime path.

Example:

```yaml
source_installs:
  - name: shared
    source_url: git@github.com:company/bifrost-shared.git
    ref: 712be44
    runtime_root: shared
    kind: shared

  - name: halo-ticketing
    source_url: git@github.com:company/halo-ticketing.git
    ref: 9ac31fd
    runtime_root: capabilities/halo_ticketing
    kind: capability
```

This gives S3 `_repo/` provenance without making workflow execution repo-aware.

### Environment Lockfile

An environment lockfile records exactly what source refs and bindings define an environment.

Example:

```yaml
schema_version: 1
environment:
  id: client-a-prod
  api_url: https://...

sources:
  shared:
    repo: git@github.com:company/bifrost-shared.git
    ref: 712be44
    runtime_root: shared
    checkout_path: shared

  halo-ticketing:
    repo: git@github.com:company/halo-ticketing.git
    ref: 9ac31fd
    runtime_root: capabilities/halo_ticketing
    checkout_path: capabilities/halo_ticketing

  client-a-custom:
    repo: git@github.com:company/client-a-custom.git
    ref: 0b821aa
    runtime_root: capabilities/client_a_custom
    checkout_path: capabilities/client_a_custom

bindings:
  organizations:
    client-a:
      id: 25b...
      name: Client A

  roles:
    dispatcher:
      id: 91c...
      name: Dispatcher

  integrations:
    halo:
      integration_id: 18c...
      mappings:
        client-a:
          organization: client-a
          entity_id: client-a-tenant-id
          oauth_token: env-owned

  configs:
    halo_base_url:
      scope: client-a
      value: env-owned
    halo_client_id:
      scope: client-a
      value: env-owned
    halo_client_secret:
      scope: client-a
      value: secret/env-owned
```

The lockfile should distinguish:

- source refs that can be committed and reviewed
- runtime roots installed into `_repo/`
- environment bindings that cannot be portable source
- secret/token placeholders that must be configured per environment

### Hydrated Workspace

A hydrated workspace is a local, generated, agent-friendly view of an environment or capability.

It is not necessarily one Git repository. It is one local folder containing multiple checkouts plus generated context.

Example:

```text
/work/client-a-prod/
  bifrost.env.lock.yaml
  .bifrost/
    workflows.yaml
    apps.yaml
    forms.yaml
    agents.yaml
    tables.yaml
    integrations.yaml
    configs.yaml
  shared/
    .git/
    halo/
      client.py
  capabilities/
    halo-ticketing/
      .git/
      bifrost.capability.yaml
      workflows/
        triage.py
      modules/
        normalization.py
      apps/
        console/
          package.json
          src/
    client-a-custom/
      .git/
      workflows/
  generated/
    llms.txt
    openapi.json
    client-types/
    dependency-graph.json
    env-summary.md
```

The purpose is to let a developer or coding agent work in one coherent folder:

- search across target capability and shared dependencies
- inspect manifests and DB registrations
- see table schemas and integration config schemas
- run tests
- edit source in real Git repos
- commit and push to the correct source repo

Hydration is an assembled view. It is not the runtime tree and does not have to be committed as a whole.

## How Workflow Calls Work in the Target Model

Current:

```text
call workflow UUID
  -> DB workflows row
  -> path = workflows/foo.py
  -> function_name = run
  -> load _repo/workflows/foo.py
  -> execute run
```

Target:

```text
call workflow UUID
  -> DB workflows row
  -> path = capabilities/halo_ticketing/workflows/foo.py
  -> function_name = run
  -> load _repo/capabilities/halo_ticketing/workflows/foo.py
  -> execute run
```

No execution-engine repo lookup is needed.

The source repo is only relevant when installing or updating that file:

```text
git@github.com:company/halo-ticketing.git
  workflows/foo.py
    -> installed to _repo/capabilities/halo_ticketing/workflows/foo.py
    -> manifest upserts Workflow(path, function_name)
```

## Shared Code Without Duplication

Shared code should be installed once at a canonical runtime path.

Example runtime tree:

```text
_repo/shared/halo/client.py
_repo/shared/connectwise/client.py
_repo/capabilities/client_a_ticketing/workflows/sync.py
_repo/capabilities/client_b_ticketing/workflows/sync.py
```

Both workflows import:

```python
from shared.halo.client import HaloClient
```

Dependency declarations:

```yaml
dependencies:
  shared-halo:
    source: git@github.com:company/bifrost-shared-halo.git
    ref: v1.4.2
    runtime_root: shared/halo
```

Resolution rules:

1. If two capabilities require the same shared source/ref or compatible version, install it once.
2. If two capabilities require incompatible versions, fail the install by default.
3. Only allow parallel installs with explicit namespacing:

```text
shared/halo_v1/
shared/halo_v2/
```

4. Never silently vendor shared code into a capability.

This keeps dedupe visible and reviewable.

Longer term, stable shared code should become real Python packages or SDK modules. But workspace shared source can remain useful for client/project code that is not ready to be packaged.

## How Git Gating Works

The source repos are real Git repositories:

```text
github.com/company/bifrost-shared
github.com/company/halo-ticketing
github.com/company/client-a-custom
```

Each can have:

- branches
- commits
- PRs
- CI
- code owners
- release tags
- reviews

The environment lock records which refs are installed.

Development flow:

```bash
bifrost env hydrate client-a-prod
cd client-a-prod/capabilities/halo_ticketing
git checkout -b fix-triage-routing
# edit workflows/app/tests
git commit -am "fix triage routing"
git push
gh pr create
```

After merge:

```bash
bifrost env install-source client-a-prod \
  --source halo-ticketing \
  --ref 9ac31fd
```

Install then:

1. Checks out the source ref.
2. Resolves shared dependencies.
3. Copies/syncs source into `_repo/{runtime_root}`.
4. Updates `file_index` for text files.
5. Refreshes Redis module cache for Python files.
6. Imports split manifests.
7. Upserts workflows/apps/forms/agents/tables/integrations/config schemas.
8. Builds app previews.
9. Updates the environment lock.

This gives Git review without making the execution engine branch-aware.

## How a Developer Clones an Environment

The hard part is not cloning source. The hard part is cloning enough environment context that the code is meaningful, without copying secrets or live tenant state unsafely.

Proposed command:

```bash
bifrost env hydrate client-a-prod --target ./client-a-prod
```

Hydration steps:

1. Fetch environment lock from Bifrost.
2. Clone each source repo at the locked ref.
3. Place repos under the checkout paths in the lockfile.
4. Export current split manifests to `.bifrost/`.
5. Generate or fetch OpenAPI/types.
6. Generate `generated/env-summary.md`.
7. Generate dependency graph and entity reference map.
8. Write local metadata describing which files push to which runtime roots.
9. Optionally create local `.env.example` with non-secret environment placeholders.

The workspace should include enough DB metadata for reasoning:

- workflow IDs, names, paths, functions, parameters
- app IDs, slugs, repo paths, dependencies
- table names, schema, policies, scope
- integration names, config schema, mapping names
- config keys and secret placeholders
- forms and agents with inline content
- event subscriptions
- role names and organization names

It should not include:

- OAuth access/refresh tokens
- secret config values
- live table row data by default
- tenant-private uploads
- app embed secrets

For debugging with data, provide explicit opt-in snapshots:

```bash
bifrost env hydrate client-a-prod --include-sample-data tables:tickets,assets
```

That should export sanitized/sample rows, not raw production data unless an admin explicitly requests it.

## What a Capability Repo Looks Like

Example repo:

```text
halo-ticketing/
  bifrost.capability.yaml
  .bifrost/
    workflows.yaml
    apps.yaml
    forms.yaml
    agents.yaml
    tables.yaml
    integrations.yaml
    configs.yaml
  workflows/
    triage.py
    sync_tickets.py
  modules/
    normalization.py
    formatting.py
  apps/
    console/
      package.json
      src/
        App.tsx
        services/
  tests/
    test_triage.py
  README.md
```

Installed runtime tree:

```text
_repo/capabilities/halo_ticketing/.bifrost/workflows.yaml
_repo/capabilities/halo_ticketing/workflows/triage.py
_repo/capabilities/halo_ticketing/modules/normalization.py
_repo/capabilities/halo_ticketing/apps/console/package.json
```

Manifest paths must be written relative to the runtime tree, not merely relative to the repo root:

```yaml
workflows:
  55d...:
    name: Triage Ticket
    path: capabilities/halo_ticketing/workflows/triage.py
    function_name: triage_ticket

apps:
  71a...:
    name: Ticket Console
    slug: ticket-console
    path: capabilities/halo_ticketing/apps/console
```

Alternative: allow repo-local manifests and have install rewrite paths to runtime-root-prefixed paths. That is friendlier for authors:

```yaml
# repo-local
path: workflows/triage.py

# installed/imported
path: capabilities/halo_ticketing/workflows/triage.py
```

This rewrite must be deterministic and visible in dry-run output.

## App Platform Direction

This design does not by itself fix app authoring quality. It creates a source model that can support a better app authoring model.

Current app bundler:

- Reads app source from `_repo/{repo_path}`.
- Synthesizes an entry file.
- Synthesizes a `bifrost` module.
- Uses esbuild with platform externals.
- Stores dependencies in `Application.dependencies`.

The long-term authoring target should be closer to standard Vite/React:

```text
apps/console/
  package.json
  vite.config.ts
  index.html
  src/
    App.tsx
    main.tsx
```

Bifrost-specific APIs should look like ordinary imports:

```ts
import { useWorkflowQuery, tables } from "@bifrost/client";
import { Button } from "@bifrost/ui";
```

Compatibility path:

1. Keep current esbuild bundler for existing apps.
2. Add a new app mode or manifest flag for standard Vite apps.
3. Teach the app installer/bundler to detect `package.json` and build through Vite or a Vite-compatible adapter.
4. Keep runtime deployment artifacts under `_apps/{id}/`.
5. Make `bifrost app dev` run an ordinary Vite dev server with Bifrost auth/context injection.

Agent benefit:

Agents already know how to build Vite apps. The less Bifrost-specific magic in app source, the better.

## Agent Experience Requirements

This architecture only helps if agents see one coherent project.

Hydrated workspaces must include:

- target source repos
- shared dependency source repos
- generated OpenAPI/types
- split manifests
- environment lock
- table schemas
- integration config schemas
- readable entity-reference map
- examples from similar capabilities
- current `llms.txt`
- local tests and test commands

The workspace should make references easy to follow:

```text
generated/entity-map.md

Workflow: Triage Ticket
  id: 55d...
  ref: capabilities/halo_ticketing/workflows/triage.py::triage_ticket
  file: capabilities/halo_ticketing/workflows/triage.py
  forms: Ticket Intake
  agents: Ticket Router
  tables: tickets, ticket_events

App: Ticket Console
  id: 71a...
  slug: ticket-console
  source: capabilities/halo_ticketing/apps/console
  workflows used:
    - Triage Ticket
```

The agent should not have to infer:

- which repo owns a file
- which runtime path a local file installs to
- which workflow UUID corresponds to a path/function
- which config keys are secrets
- which tables are global vs org-scoped
- which shared dependency version is installed

## Proposed CLI Surface

Initial commands:

```bash
bifrost capability init <slug>
bifrost capability export <slug> ./out
bifrost capability import ./capability --runtime-root capabilities/<slug>
bifrost capability install <slug> --repo <url> --ref <ref>
bifrost capability hydrate <slug> --env <env> --target ./workspace

bifrost env lock export <env> ./bifrost.env.lock.yaml
bifrost env hydrate <env> --target ./workspace
bifrost env install-source <env> --source <name> --ref <ref>
bifrost env diff <env> ./workspace
```

Important behavior:

- `hydrate` is read-mostly. It creates local checkouts and generated context.
- `install` writes to `_repo/`, imports manifests, and updates DB runtime registrations.
- `diff` compares local source/manifests against installed `_repo/` and DB metadata.
- `push/watch` can still exist for fast iteration, but should know the runtime root mapping from the lockfile.

## Data Model Sketch

Possible new DB tables:

```text
capabilities
  id uuid pk
  slug text unique
  name text
  description text null
  runtime_root text unique
  source_install_id uuid null
  version text null
  created_at
  updated_at

source_installs
  id uuid pk
  name text
  kind text -- shared | capability | environment
  source_url text
  source_type text -- git
  ref text
  resolved_commit text
  runtime_root text unique
  lock_data jsonb
  installed_at

capability_entities
  capability_id uuid
  entity_type text -- workflow | app | form | agent | table | integration | config | event | mcp_server
  entity_id uuid
  source_path text null
  primary key (capability_id, entity_type, entity_id)

source_install_files
  source_install_id uuid
  path text
  content_hash text
  primary key (source_install_id, path)

environment_locks
  id uuid pk
  environment_key text
  lock_data jsonb
  created_at
  updated_at
```

Alternative:

Start with no new tables and store lock/install metadata as manifest files in `.bifrost/`. This is lower risk but makes querying ownership and provenance harder. A useful migration path is:

1. Add manifest-only `capabilities.yaml`.
2. Use it to prove workflow/app grouping.
3. Add DB tables once install/provenance needs are clear.

## Migration Plan

### Phase 0: Design and Audit

Deliverables:

- This doc.
- One selected real capability candidate.
- Current-state dependency graph for that candidate.
- List of source files, workflows, apps, forms, agents, tables, configs, integrations involved.

No code changes beyond docs.

Questions to answer:

- What is the smallest existing bundle that includes at least one app, one workflow, one shared module, one table, and one integration/config dependency?
- Which current paths would move?
- Which current IDs must be preserved?
- Which references are UUID-only and need portable path/name alternatives?

### Phase 1: Capability Folder Convention Inside Existing `_repo/`

Goal:

Prove the runtime can operate from capability-prefixed paths without source repo changes.

Actions:

1. Pick one non-critical capability.
2. Move or create source under:

```text
capabilities/<slug>/workflows/
capabilities/<slug>/modules/
capabilities/<slug>/apps/
```

3. Update workflow manifest paths to capability-prefixed paths.
4. Update app manifest paths to capability-prefixed paths.
5. Import manifest and verify workflow execution/app build.
6. Verify virtual imports from capability-local modules.
7. Verify imports from existing shared paths.

Runtime remains unchanged.

Expected code changes:

- Mostly tests and docs.
- Maybe path validation or editor detection if it assumes `workflows/` or `apps/`.

### Phase 2: Capability Manifest

Goal:

Make grouping explicit.

Add `.bifrost/capabilities.yaml`:

```yaml
capabilities:
  cap_halo_ticketing:
    id: cap_halo_ticketing
    slug: halo-ticketing
    name: Halo Ticketing
    runtime_root: capabilities/halo_ticketing
    entities:
      workflows:
        - 55d...
      apps:
        - 71a...
      tables:
        - 82b...
    dependencies:
      shared:
        - shared-halo
```

Actions:

1. Add manifest model.
2. Add manifest generator support.
3. Add manifest import support.
4. Add tests for round-trip.
5. Add UI or CLI list/detail later.

No multi-repo yet.

### Phase 3: Environment Lockfile and Hydration Prototype

Goal:

Let a developer clone an environment into a coherent local workspace.

Actions:

1. Add `bifrost env lock export`.
2. Add `bifrost env hydrate`.
3. Generate:

```text
bifrost.env.lock.yaml
.bifrost/*.yaml
generated/entity-map.md
generated/env-summary.md
generated/openapi.json
```

4. For Phase 3, source can still come from the single current Git repo or S3 `_repo/`.
5. Make the hydrated workspace useful to agents before adding multiple repos.

This phase validates the agent experience.

### Phase 4: Multi-Repo Source Installs

Goal:

Make capability/shared repos real Git sources.

Actions:

1. Add `source_installs` concept.
2. Add `bifrost capability install --repo --ref --runtime-root`.
3. On install:
   - checkout source
   - resolve dependencies
   - rewrite repo-local manifest paths if needed
   - sync to `_repo/{runtime_root}`
   - update `file_index`
   - refresh module cache
   - import manifests
   - compile apps
   - update lockfile
4. Add dry-run output.
5. Add rollback to previous lock ref.

Runtime remains unchanged.

### Phase 5: Shared Dependency Resolution

Goal:

Avoid duplicate shared code.

Actions:

1. Add dependency declarations to capability manifest.
2. Add resolver:
   - same dependency and compatible ref -> reuse
   - conflict -> fail loudly
   - explicit namespaced duplicates only when requested
3. Add lockfile entries for resolved dependencies.
4. Add `bifrost env deps explain`.

### Phase 6: App Authoring Upgrade

Goal:

Make Bifrost apps feel like normal React/Vite projects.

Actions:

1. Define standard app project layout.
2. Add app manifest flag for build mode.
3. Add `@bifrost/client` package or local generated adapter.
4. Add `bifrost app dev` with Vite HMR and Bifrost auth/context injection.
5. Keep existing app bundler compatibility.
6. Add migration tool for current apps.

## Testing Strategy

Phase 1 tests:

- Unit: manifest accepts capability-prefixed workflow/app paths.
- E2E: import workflow under `capabilities/<slug>/workflows/foo.py`, execute by UUID.
- E2E: workflow imports `capabilities.<slug_python>.modules.helper`.
- E2E: workflow imports `shared.<module>`.
- E2E: app with `repo_path=capabilities/<slug>/apps/<app>` builds preview.

Phase 2 tests:

- Unit: `ManifestCapability` parse/serialize deterministic.
- Unit: manifest generator writes capability ownership.
- E2E: import capability manifest preserves workflow IDs and app IDs.

Phase 3 tests:

- CLI unit: env lock export redacts secret values.
- CLI unit: hydrate creates expected directory layout.
- CLI unit: generated entity map includes workflow path/function refs.
- E2E: hydrate then push a workflow edit into `_repo` with correct runtime path.

Phase 4 tests:

- E2E: install from a local bare Git repo at a commit ref.
- E2E: install updates `_repo` files and DB workflow rows.
- E2E: rollback to previous ref restores source and registrations.
- E2E: branch/ref install does not alter runtime until install is run.

Phase 5 tests:

- Unit: dependency resolver reuses compatible shared dependency.
- Unit: dependency resolver fails incompatible shared refs.
- E2E: two capabilities import one shared module path and both workflows execute.

Phase 6 tests:

- Client/unit: Bifrost SDK adapter works in standard app.
- E2E: `bifrost app dev` opens Vite app with auth/context.
- E2E: standard Vite app builds and deploys through Bifrost app runtime.

## Risks and Hard Problems

### Environment Cloning Is Not Source Cloning

Tables, integrations, roles, orgs, configs, OAuth tokens, event sources, and MCP connections have environment-specific state. A clone command must clearly separate:

- portable definitions
- environment bindings
- secrets/tokens
- sample data
- live data

If this is fuzzy, developers and agents will make wrong assumptions.

### UUID References Can Hide Portability Problems

Forms, agents, events, and apps may refer to workflows by UUID. Current import has some portable reference handling, but a capability source repo should prefer stable refs where possible:

```text
capabilities/halo_ticketing/workflows/triage.py::triage_ticket
```

The environment importer can resolve those to UUIDs.

Review needed:

- Which current manifest fields support portable refs?
- Which still require UUIDs?
- Where should path refs be allowed or disallowed?

### Path Rewrites Can Become Confusing

Repo-local manifests are nicer:

```yaml
path: workflows/triage.py
```

Runtime manifests are explicit:

```yaml
path: capabilities/halo_ticketing/workflows/triage.py
```

If the installer rewrites paths, it must show exactly what changed and avoid committing rewritten runtime paths back into source repos unless desired.

### Shared Dependency Versioning Can Become a Package Manager

Avoid building a full package manager too soon. Start with:

- exact refs
- reuse identical refs
- fail conflicts
- explicit override when needed

### Agent Context Can Get Worse

If hydration produces a scattered folder with missing shared code, stale manifests, or unclear runtime mappings, agents will perform worse than the current monorepo. Hydration quality is not optional.

### App Platform Remains a Separate Problem

Capability source organization will not automatically make apps feel like Vite. The app runtime and dev loop need their own compatibility/migration plan.

## Review Questions for Claude

1. Does the current-system map match the code paths?
2. Is there any execution path that still reads workflow code from DB rather than Redis/S3?
3. Which editor/CLI/API routes assume top-level `workflows/` or `apps/` paths?
4. Which manifest references are UUID-only and would block portable capability repos?
5. What is the smallest existing real capability candidate for Phase 1?
6. Should `Capability` start as manifest-only or DB-backed?
7. Should repo-local manifests use local paths and rewrite on install, or require runtime-root-prefixed paths in source?
8. How should environment lockfiles be stored: DB, S3 `_repo/.bifrost`, Git, or all three?
9. Which table data, if any, should hydrate by default?
10. What security review is needed for exporting integration mappings, config keys, MCP connections, and event sources?
11. What app authoring changes are needed before standard Vite projects can be first-class?

## Initial Recommendation

Start with Phase 1 and Phase 3, not multi-repo installs.

Reason:

- Phase 1 proves the runtime path model works for grouped functionality.
- Phase 3 proves the agent/developer experience.
- Neither requires the engine to become multi-repo aware.
- Both reveal the actual blockers before adding Git source installs.

If those phases feel good, Phase 4 can introduce real multi-repo source installs with much less risk.

The architectural line to preserve is:

```text
Git is source/review.
S3 `_repo/` is installed runtime source.
DB is runtime registry and environment binding.
Hydrated workspace is the agent/developer view.
```
