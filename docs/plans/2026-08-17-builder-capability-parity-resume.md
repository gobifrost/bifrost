# Code Builder capability-parity resume handoff

**Prepared:** 2026-08-17
**Last updated:** 2026-08-18 (platform-job slice committed as `ce67707cb`,
local-only; not yet pushed)
**Status:** Safe checkpoint; continue implementation, but do not open a PR or
merge without Jack's explicit approval.

## Resume location

- Worktree:
  `/home/jack/GitHub/bifrost/.worktrees/code-builder-pydantic-integration-20260816`
- Branch: `codex/code-builder-pydantic-integration-20260816`
- Remote: `origin/codex/code-builder-pydantic-integration-20260816`
- Execution plan:
  `docs/plans/2026-08-17-builder-capability-parity-execution.md`
- Full product/status ledger:
  `docs/superpowers/specs/2026-07-27-private-solution-builder-status.md`
- Reconstructed Builder source-of-truth backup remains preserved separately;
  do not rebase or mutate the old `code-builder` branch.

Start a new session with:

```bash
cd /home/jack/GitHub/bifrost/.worktrees/code-builder-pydantic-integration-20260816
git status --short --branch
git log -5 --oneline --decorate
```

The expected commits after `b4ecf2242` are the Role canonicalization
(`f248aa32d`) and the durable platform-job slice (`ce67707cb`). Resolve exact
hashes from `git log`; do not assume or rewrite history merely to match a
recorded hash.

`ce67707cb` is committed locally but **not pushed** — the remote branch still
points at `f248aa32d`. Push it before relying on the remote as the checkpoint.

## Non-negotiable architecture decisions

- REST handlers and shared domain services are the single behavior and
  authorization boundary.
- MCP tools are thin REST adapters. New MCP names follow
  `bifrost_<verb>_<noun>`; CLI mirrors `bifrost <resource> <verb>`.
- The native Builder, MCP, and CLI must converge on one stable operation
  catalog and one generated `bifrost-build` Skill reference. Do not maintain
  three instruction sets.
- Builder coding turns run through the shared Pydantic AI harness on the
  existing execution Worker. Cloudflare is an optional backend for the same
  runner envelope and `gobifrost/bifrost-build` image, not a second harness.
- App compilation/build work belongs to that execution runtime. Durable
  deploy/publish/lifecycle orchestration remains a `PlatformJob` where the
  shared job contract is needed.
- Builder worktrees/filesystems map to the shared conversation artifact
  workspace and opaque `ArtifactRef` contract; do not recreate attachment,
  progress, artifact, or usage systems.
- Persisted MCP tool IDs must move with forward-only Alembic migrations.
  Never restore the bodies of the withdrawn Builder migrations.
- No compatibility aliases or speculative fallbacks. Any name that cannot be
  migrated atomically requires an explicit decision.
- No new container is required for the normal local deployment.

## Completed and pushed foundation

Phases 0 and 1 are complete: operation inventory/catalog, shared harness
integration, recovered Builder UI/source/runner/tests, optional Cloudflare
execution, shared artifact/usage/progress plumbing, and the no-new-container
local Worker path.

Phase 2 has canonicalized these public vertical slices:

- Agents, Forms, Tables, Apps, Events, Workflows
- Organizations and Integrations
- Workspace Files
- Workflow Execution History
- Knowledge Search
- Roles
- Durable platform-job status (committed locally, not yet pushed)

The latest generated inventory reports:

- 660 REST method/path pairs
- 140 CLI leaves
- 101 MCP tools
- 10 currently implemented native Builder primitives
- 16 manifest entities
- 19 application-SDK bindings
- 73 canonical catalog operations
- 28 uncatalogued MCP tools

The Role slice adds stable `roles.list/get/create/update/delete` identities,
OpenAPI metadata, generated Skill bindings, and canonical
`bifrost_*_role(s)` MCP registrations. It keeps REST ownership of admin
authorization, built-in/Solution guards, audit, cache invalidation, cascades,
and manifest behavior.

The platform-job slice corrected a misleading identity rather than adding a
surface. `bifrost_get_app_publish_status` read the shared
`GET /api/platform-jobs/{job_id}` route under an app-specific name; it is now
`platform.jobs.get`, registered only as `bifrost_get_platform_job` in its own
thin-wrapper module, with a matching `bifrost platform-jobs get` CLI leaf.
`bifrost apps publish` still polls the same job inline, so no second status
contract exists. The catalog naming rule now normalizes hyphenated CLI
resources — no earlier slice exercised that, and `platform-jobs/get` would
otherwise derive an invalid hyphenated MCP name. Alembic head is
`20260817_platform_job_names`.

## Current verification evidence

Platform-job checkpoint (`ce67707cb`) checks completed successfully:

- 108/108 focused operation-catalog, thin-wrapper, and forward-migration unit
  checks
- 67/67 generated-inventory freshness, DTO-parity, and contract-version
  tripwires
- 2/2 live MCP checks: a real publish job read back through
  `bifrost_get_platform_job`, and an unknown job surfacing the REST 404 rather
  than a synthetic success
- live CLI drive against the debug stack: `bifrost platform-jobs get` returned
  a real completed publish job (`files_published: 5`) and the REST 404 for an
  unknown job
- API Pyright and Ruff: clean
- operation catalog and Skill truth regenerated
- live debug database current/head: `20260817_platform_job_names`
- `git diff --check`: clean before checkpointing

Not yet run for this checkpoint: the full `tests/e2e/mcp/` suite (started twice;
the first run was killed by session teardown before reporting), client
`npm run tsc`/lint, and `./test.sh pre-pr`. This slice changed no client source,
but the OpenAPI schema did gain the new operation identity, so a type regen is
still worth confirming before any PR.

Role checkpoint checks completed successfully:

- 173/173 operation-catalog, thin-wrapper, DTO, contract-version, and forward
  migration unit checks
- 14/14 live MCP/REST Role lifecycle, authorization denial,
  Solution-management guard, and manifest-import checks
- 67/67 generated inventory, Skill freshness, tool-access, and Agent tool
  loading checks
- API Pyright and Ruff: clean
- client `npm run tsc`: clean after regenerating OpenAPI types
- operation catalog and Skill truth regenerated
- live debug database current/head:
  `20260817_role_mcp_names`
- `git diff --check`: clean before checkpointing

The complete backend, Vitest, and Playwright suites were not rerun for this
small backend contract slice. Earlier slice-specific UI, Worker, Cloudflare,
Scheduler, artifact, and Builder journey evidence is recorded in the status
ledger.

## Remaining Phase 2 public parity gaps

The 29 uncatalogued MCP tools split cleanly into 18 public operations and 11
intentional runtime/Builder-local tools.

Public slices still to canonicalize:

1. Configs: list/get/create/update/delete.
2. Claims: list/get/create/update/delete.
3. File policies: list/get/set/delete.
4. Policy rules: list/create/delete.

The durable platform-job status read is done: it became the canonical
`platform.jobs.get` operation (`bifrost_get_platform_job` /
`bifrost platform-jobs get`) rather than staying an app-scoped name over a
shared route. Alembic head is now `20260817_platform_job_names`.

Note for the Configs slice: there is no per-ID REST GET for configs, so the
current MCP `get_config` resolves a ref and filters the list payload
client-side. That divergence needs an explicit decision (add the REST route or
catalog the read as list-derived) before the slice can claim parity.

For each slice: inventory existing REST/CLI/MCP behavior, make MCP a thin REST
adapter, add catalog definitions and `operation_route` metadata, align the CLI
without aliases, preserve side effects/audit/guards, add a forward persisted
tool-ID migration when names change, regenerate references/types, run scoped
unit plus live boundary tests, then commit and push a checkpoint.

Do not mistakenly catalog these 11 internal tools as public resource
operations during Phase 2:

```text
apply_patch
delete_file
get_docs
list_files
make_directory
read_file
read_skill_asset
search_text
test_solution_build
validate_solution
write_file
```

They must receive explicit transport-only/Builder-local dispositions. In
particular, `read_skill_asset` belongs to revision-bound Agent Skill hydration,
and the local file/edit/validation tools belong to the coding harness rather
than duplicating Workspace REST operations.

## Decisions and pitfalls recorded 2026-08-18

- **Platform-job identity.** Jack chose renaming
  `bifrost_get_app_publish_status` to `bifrost_get_platform_job` over keeping
  the app-scoped name or carrying both. Rationale: the tool reads a shared
  durable-job route, so an app-scoped name would have mislabeled it and left
  every future queued operation without a canonical status read. No alias was
  retained, consistent with the no-compatibility-alias rule.
- **Operation IDs cannot contain underscores in the resource segment.** The
  contract pattern is `^[a-z][a-z0-9]*(?:\.[a-z][a-z0-9_]*)+$`, so
  `platform_jobs.get` fails validation at import time and takes the whole API
  down on startup. Use dotted segments (`platform.jobs.get`) as
  `apps.dependencies.get` already does.
- **The naming validator derives the MCP name from the CLI path** and now
  normalizes hyphens in the resource, not just the verb. Before this slice a
  hyphenated CLI resource produced an invalid hyphenated MCP name.
- **`PlatformJobPublic` has a real `error` field** that is `None` on success.
  An MCP test asserting `"error" not in payload` therefore fails on a healthy
  read; assert `payload.get("error") is None` instead. The tool's own failure
  convention also puts `error` in `structured_content`, so the two collide by
  name — the live tests pin both directions.
- **`./test.sh` resolves its Compose project from the current directory.**
  Running it after the shell's cwd has reset to the primary checkout targets a
  different worktree's stack and reports `Status: DOWN`. Always run it from the
  worktree path.
- **`./test.sh e2e <path>` deselects everything**; pass the test path directly
  (`./test.sh tests/e2e/mcp/... -k ...`) instead.
- **Editable CLI installs can silently serve a stale copy.** After
  `pip install -e <worktree>/api`, confirm with
  `python -c "import bifrost.commands as c; print(c.__file__)"` that the path
  points into the worktree before concluding a new command is missing.

## Remaining phases after public parity

1. Native Builder dispatch parity: generate/derive the Builder operation set
   from the same catalog, then replace the current gap where catalog entries
   say `native_builder: true` but only 10 native primitives are observed.
2. Agent Skill hydration: make `bifrost_get_agent` return the canonical
   `SKILL.md` projection, immutable revision, file inventory, and dependent
   references; make `read_skill_asset` revision-bound and authorized across
   native, MCP, Builder, and CLI harnesses.
3. Maintained coding profile: an enable/disable platform coding Agent/profile
   that receives only the operations allowed by caller, target, and policy.
   Access to an Agent must not imply access to every attached tool.
4. Complete Solution/Workspace target parity, including admin-only global
   Workspace building and ordinary-user Solution boundaries.
5. Final delivery QA: full Builder behavior inventory against the preserved
   backup, migration upgrade coverage, shared Scheduler/Worker/PlatformJob
   matrix, browser UX and accessibility pass, Cloudflare/local equivalence,
   generated references, and broad test gates.

## Running stacks

- Debug URL:
  `https://bifrost-70494e39-qshz.eu1.netbird.services`
- Debug Compose project: `bifrost-debug-70494e39`
- Test Compose project: `bifrost-test-70494e39`

Treat these as ephemeral. Confirm them with `./debug.sh status` and
`./test.sh stack status` before reuse. Use `./test.sh` for tests; do not run
pytest on the host.

## Source-control boundary

**Immediate next action:** `ce67707cb` is committed but unpushed. Push it to
`origin/codex/code-builder-pydantic-integration-20260816` so the remote is
again the recoverable checkpoint.

Checkpoint commits and pushes to the integration branch are authorized.
Opening a PR, enabling auto-merge, or merging is not authorized. Before any
eventual PR, update the Builder file/behavior inventory against the preserved
backup and account explicitly for every omission.
