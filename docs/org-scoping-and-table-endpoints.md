# Org Scoping + Table-Document Endpoints

Reference doc: how organization scoping works in Bifrost today, where the implementation diverges from intent, and the table-document endpoint consolidation that brings web UI and SDK callers onto a single path.

## Scope of changes

This document describes both **the system as it exists** (the scoping section) and **a focused refactor** (the table-document consolidation). The two are written together because the consolidation is the only thing that requires any code change today; everything else in the scoping section is reference material.

**What changes:**

- Ten CLI table-document handlers (`/api/cli/tables/documents/*`) get deleted. The Python SDK is repointed at the existing REST endpoints (`/api/tables/{id}/documents/*`).
- Two small additions to the REST endpoints to absorb features the CLI had: optional `created_by`/`updated_by` body fields on document writes (accepted only when the caller is the engine or a platform admin; 403 otherwise), and an explicit `POST /api/tables/{id}/documents/upsert` verb.
- `Table.application_id` column and the `app` lookup parameter that flowed through it are removed entirely (separate migration). The column was used only by the CLI handlers being deleted plus a few REST table-CRUD references; no live consumers in the SDK or web UI depend on it.
- Auto-create-on-insert moves from the CLI handler into the SDK as a 404→create→retry pattern.
- `_get_cli_org_id` gets UUID validation. One-paragraph fix.

**Net effect on scope handling:** fewer scope helpers in the table-document path, not more. Today three resolvers participate (`_resolve_target_org_safe`, `_get_cli_org_id`, the SDK's `resolve_scope`); after consolidation it's two (the SDK runtime check plus the REST helper). `_get_cli_org_id` stops being part of the table-document path entirely.

**What does not change:**

- Engine identity. The synthetic engine-superuser token (`authenticate_engine`) and the engine's authority to do what workflows need to do are intentional and stay as-is.
- `is_superuser` → `is_provider` migration in the REST scope helpers. Real change in who-can-do-what; deferred to a separate effort.
- `DeveloperContext` and the `bifrost run --interactive` dev-workbench feature. Alive and used; refactor opportunity, not a bug.
- The dual scope-resolution sites in workflow dispatch (`workflows.py:807-832` and `workflow_execution.py:540-549`). Consistent today; cleanup deferred.
- The other `/api/cli/*` endpoints (configs, integrations management, knowledge mutation, AI completion). Engine-only by intent; not consolidated.
- Embed token scope handling, raw `WHERE organization_id = ...` patterns, and other audit items called out at the bottom of this doc.

## How scoping works today

### The intended rule

Every authenticated request from outside the engine gets scoped to the caller's organization, with cross-org targeting allowed only for **provider-org members** (`Organization.is_provider == True`, well-known UUID `00000000-0000-0000-0000-000000000002` from `api/alembic/versions/20260107_022300_add_provider_org.py:26`). Provider members can pass `?scope=<other-org-uuid>` or `?scope=global`; everyone else has scope ignored — they're pinned to their own org.

The intended rule is expressed correctly in two places:

- `api/bifrost/_execution_context.py:148-167` (`ExecutionContext.set_scope`) — gates on `self.organization.is_provider`.
- `api/bifrost/_context.py:128-147` (`resolve_scope`) — gates on `context.organization.is_provider`.

Both are SDK-runtime checks that fire before the SDK issues an HTTP request.

### How the API enforces it (today)

The server-side gate is `is_superuser`, not `is_provider`. Two helpers:

- `api/src/core/org_filter.py:91-139` (`resolve_target_org`) — for write paths. Superusers can target any org via scope; non-superusers' scope is ignored, pinned to `user.organization_id`.
- `api/src/core/org_filter.py:28-89` (`resolve_org_filter`) — for read/list paths. Returns a `(filter_type, org_id)` for `OrgScopedRepository` to use.

Both gate on `is_superuser`. The intended `is_provider` rule and the actual `is_superuser` rule correlate today only because of migration history: `api/alembic/versions/20260107_022300_add_provider_org.py:53-66` swept all PLATFORM-type users into the provider org. That correlation is brittle; we don't fix it here (see "Out of scope" below) but it's worth knowing the rule the helpers implement isn't the rule the SDK enforces.

### Inside the engine

Workflows run as a synthetic engine superuser (`ENGINE_USER_ID = 00000000-0000-0000-0000-000000000001`, `is_superuser=true`, no org). See `api/src/core/security.py:405-449` (`authenticate_engine`). The worker mints this token at the start of every execution and saves it to `~/.bifrost/credentials.json`; the SDK uses it as the bearer for every HTTP call back to the API.

The engine identity is intentional and stays as-is: the engine is a controlled environment, an admin wrote the workflow, and the engine has the authority to do what the workflow needs to do.

### How the resolved org is computed for a workflow

The rule is "workflow's org if set, else caller's org," and it's implemented **twice**:

- `api/src/routers/workflows.py:807-832` — the API handler computes `execution_org_id` for the queued execution. It checks (in order): explicit `request.org_id` override (admin only, line 814), workflow's `organization_id` (line 818), then caller's `ctx.org_id` (line 824) with a `DeveloperContext.default_org_id` lookup for platform admins (lines 826-832).
- `api/src/jobs/consumers/workflow_execution.py:540-549` — the worker dispatch consumer re-derives the same value when it fetches `workflow_data.get("organization_id")`. It overrides `org_id` if the workflow is org-scoped, leaves the caller's org if global.

The first computed value flows through `_publish_pending` (`api/src/services/execution/async_executor.py:33-89`) into the Redis pending blob (`pending["org_id"]`), which the worker then reads and clobbers with its own re-derivation. The duplication is wasteful but currently consistent: both sites apply the same rule. If the rule changes, both have to change in lockstep.

The worker's `org_data` (`api/src/jobs/consumers/workflow_execution.py:633-647`) is fetched via `ConfigResolver.get_organization` and **does include `is_provider`**. The API handler's intermediate `Organization(id=..., name="", is_active=True)` (`api/src/routers/workflows.py:867`) does **not** populate `is_provider`. Since the API-handler version is throwaway (the worker re-fetches), this discrepancy doesn't bite at runtime — but it's still a footgun: if someone refactors away the worker re-fetch, the `is_provider` check in `set_scope`/`resolve_scope` silently breaks.

### What the worker passes to the SDK runtime

The worker reconstructs `ExecutionContext` from the Redis pending blob plus the org-data dict, in `api/src/services/execution/worker.py:140-160`. `Organization` carries `is_provider` correctly here (line 150). `ExecutionContext.organization` is what `set_scope`/`resolve_scope` evaluate against. So inside the SDK runtime, the `is_provider` rule works correctly.

The HTTP request the SDK makes back to the API does **not** carry the caller or the resolved org. It carries the engine-superuser token. The API server therefore evaluates every workflow-driven request as engine-superuser.

This is intentional. Per the user's framing: the engine is a controlled environment where workflow authors are trusted, the engine identity is what the API sees, and the API enforces nothing about the caller for engine-attested calls. We're not changing that.

### Where the rule isn't enforced server-side

`api/src/routers/cli.py:357-392` (`_get_cli_org_id`) is the CLI-side scope resolver. It enforces nothing — whatever scope arrives on the wire is honored, with no UUID validation and no permission check. Used by every endpoint under `/api/cli/...`.

Inside workflow runtime this is invisible because the SDK gates `set_scope` and `resolve_scope` before issuing the request. But from outside the engine — a developer running `bifrost.tables.insert(scope=...)` against a deployed API after `bifrost login`, or anyone with a regular user JWT making a direct HTTP call to a `/api/cli/...` endpoint — `_get_cli_org_id` lets them target any org with no check.

This is a real bypass. It exists because most `/api/cli/...` endpoints (configs, integrations management, knowledge mutation, AI completion) were genuinely intended for engine-only use — the rule wasn't enforced because the endpoints weren't supposed to be reachable by users with motivations to exploit the gap. That assumption was always shaky and is no longer true: the table-document handlers under `/api/cli/...` (the ones we're consolidating) overlap with the REST surface, so they're effectively reachable.

## Table-document endpoint duplication

Two parallel implementations exist:

- `api/src/routers/cli.py:2818-3370` — 10 CLI handlers for `/api/cli/tables/documents/{insert, upsert, get, update, delete, insert/batch, upsert/batch, delete/batch, query, count}`.
- `api/src/routers/tables.py:782-1170` — REST handlers for `/api/tables/{table_id}/documents/{insert, get, update, delete, query, count, batch, batch-delete}`.

Side-by-side behavior:

| Behavior | CLI | REST |
|---|---|---|
| Auth gate | `_get_cli_org_id` (no check) | `_resolve_target_org_safe` via `get_table_or_404` (`is_superuser` check) |
| Table identifier | name (`request.table`), name + `app` filter | name or UUID (via `get_table_or_404`) |
| Auto-create on insert | yes — `_find_or_create_table_for_sdk` (cli.py:2657) | no — 404 if table missing |
| `application_id` lookup filter | yes — `request.app` filters by `Table.application_id` (cli.py:2625) | no — `application_id` is a column on the table, not a lookup axis |
| Policy check (`_check_action_or_403`) | **none** | yes — read/create/update/delete all gated (tables.py:798, 858, 886, 914, 953) |
| WS publish (`publish_document_change`) | **none** | yes — insert/update/delete (tables.py:828, 876, 932) |
| Read filter via `compile_read_filter` | **none** | yes — query/count return empty when no rule grants read (tables.py:855, 988) |
| Audit on policy denial | **none** | (planned; not yet shipped per `docs/superpowers/plans/2026-05-01-table-policies-hardening.md`) |
| `created_by` / `updated_by` override in body | yes — `request.created_by`, `request.updated_by` honored | no — always `ctx.user.user_id` |
| Update merge semantics | `{**doc.data, **request.data}` (cli.py:3024) | repo-level merge via `DocumentRepository.update` |
| Upsert idempotency | atomic `INSERT ... ON CONFLICT DO UPDATE` (cli.py:2918) | not exposed as a separate verb; `batch` accepts `upsert: bool` (tables.py:1014) |
| Batch policy check | no | yes — pre-flight all rows, return 403 with denied indices (tables.py:1037, 1135) |
| Returns a `table_id` for downstream subscribe | no — returns `id, table_id, data, created_at, updated_at` per doc | yes — `DocumentListResponse.table_id: UUID` in query response (tables.py:1003) |

What CLI does that REST doesn't:

1. **Auto-create on insert.** Workflow authors can call `tables.insert("foo", {...})` without an explicit create, and the table is materialized on first write with `make_seed_admin_bypass()` policies. This is real ergonomic value.
2. **`created_by` / `updated_by` body override.** CLI handlers accept these in the body and use them; REST always uses the calling user.
3. **Atomic upsert as a single verb.** REST has it via `batch` with `upsert=true`, not as a single-doc operation.
4. **`app` lookup filter** via `Table.application_id`. Same table name in different apps within the same org resolves differently. Not preserved — `Table.application_id` is being removed entirely.

What REST does that CLI doesn't:

1. **Policy enforcement.** Every action goes through `_check_action_or_403` or `compile_read_filter`. CLI doesn't invoke either.
2. **WebSocket publish.** Every mutation publishes a `document_change` event for live subscribers. CLI doesn't.
3. **All-or-nothing batch denials.** Batch endpoints pre-flight every row against policy and return 403 with the full set of denied indices. CLI batch handlers just commit.
4. **Read filter from policies.** Query and count return empty (not 403) when no rule grants read, avoiding existence leaks.

## What to consolidate

The duplication isn't justified. Reasons to keep CLI handlers:

- **Auto-create on insert** — real ergonomic; doesn't have to live on the API. Move it to the SDK as a 404→create→retry. The SDK already issues `POST /api/tables` for explicit creates (api/bifrost/tables.py:89). On `INSERT` returning 404 from the REST endpoint, post a create with the same name, then retry the insert. Race resolution via the existing 409-on-create-conflict path.

- **`app` lookup filter** — dropped. `Table.application_id` is removed in a separate migration; same-name-different-app within one org is no longer a supported model.

- **`created_by` / `updated_by` body override** — added to the REST `DocumentCreate` / `DocumentUpdate` bodies. Accepted only when `ctx.user.user_id == ENGINE_USER_ID` or `ctx.user.is_superuser`; otherwise the field's presence in the body returns 403. The SDK populates these from `ExecutionContext.caller` so workflow writes attribute to whoever triggered the workflow. Web UI users get a 403 if they try to forge attribution; if they don't send the field, the handler defaults to `ctx.user.user_id` exactly as it does today.

- **Atomic upsert as a single verb** — added as `POST /api/tables/{id}/documents/upsert`. One round trip, same `INSERT ... ON CONFLICT DO UPDATE` SQL the CLI used.

Reasons to drop CLI handlers:

- **Policy enforcement.** Not even debatable — the policy hardening branch this design lives on shipped policy support specifically because tables are now user-facing. SDK callers (workflows) get the same guarantee web UI callers get.
- **WS publish.** Visible bug today: workflow inserts don't show up live in the UI. Demo POC at `/tmp/bifrost-poc/` documented this in the prior session.
- **Audit.** Same reason — engine-attributed denials aren't useful, but only because engine-attributed everything is the bug. Once engine writes go through the policy gate, audit becomes meaningful.

The consolidation: **delete the 10 CLI handlers; point the Python SDK at `/api/tables/{name_or_id}/documents/*` (which already accepts name-or-UUID via `get_table_or_404`). Move auto-create into the SDK. Add `created_by`/`updated_by` to the REST write bodies, gated to engine + superuser. Add an explicit upsert verb. Drop `Table.application_id` and the `app` lookup parameter in a separate migration.**

After consolidation, web UI and Python SDK share one endpoint set, policy enforcement / WS publish / audit are uniform, and the no-server-gate `_get_cli_org_id` stops being part of the table-document path. (`_get_cli_org_id` itself stays for the surviving `/api/cli/*` endpoints — fixing it is a separate, smaller question handled below.)

## Other fixes in scope

These are all small and follow from the analysis:

1. **`_get_cli_org_id` validation.** Validate `scope` as UUID (or `"global"`, or null); 422 on garbage. The function currently returns garbage strings unmodified (cli.py:381) and the failure mode is a downstream PostgreSQL type error. This is a one-paragraph fix.

2. **`_get_cli_org_id` permission gate, for endpoints reachable outside the engine.** The surviving CLI endpoints fall into two categories: ones that genuinely should be engine-only (configs, integrations management, knowledge mutation, AI completion) and the table-document handlers we're consolidating away. The first category is fine as-is for now — they're behind regular bearer auth, and the practical exposure is limited. The CLI table-document handlers go away as part of consolidation. **No new gate is added in this PR.** (We deliberately don't change `_get_cli_org_id`'s no-permission-check behavior on the surviving endpoints because doing so requires knowing what they should enforce, which is a separate audit.)

3. **Dual scope-resolution in workflow dispatch.** `workflows.py:807-832` and `workflow_execution.py:540-549` both implement the "workflow's org if set, else caller's org" rule. This is real drift waiting to happen. Out of scope for this PR (the rule is consistent today, the duplication is a refactor opportunity, not a bug). Note it for a follow-up cleanup.

4. **`is_provider` not populated in the API handler's `Organization`.** `workflows.py:867` constructs `Organization(id=..., name="", is_active=True)` without `is_provider`. Currently throwaway because the worker re-fetches (`workflow_execution.py:643-647`), but a footgun. Out of scope; cleanup ticket.

## Known divergences from intent (refactor candidates)

If a future session is here to refactor scoping itself (not the table consolidation), this is the consolidated list of known issues. Each is documented above with file:line; this section is the index.

1. **Server-side gate uses `is_superuser`, not `is_provider`** (`api/src/core/org_filter.py:91-139` and `:28-89`). The intended rule is provider-org membership; the implementation uses the platform-admin role bit. They correlate today only because of migration history. Real change in who-can-do-what — a provider-org non-superuser gains cross-org access, a hypothetical non-provider superuser loses it. Needs the test matrix that doesn't exist yet to land safely.

2. **`UserPrincipal` doesn't carry `is_provider`** (`api/src/core/auth.py:30-79`). JWT doesn't carry it either. Any server-side `is_provider` check would need a DB lookup (or a JWT-claim addition + login-time population). Prerequisite for #1.

3. **Dual scope-resolution in workflow dispatch.** `api/src/routers/workflows.py:807-832` and `api/src/jobs/consumers/workflow_execution.py:540-549` both implement "workflow's org if set, else caller's org." Consistent today; if the rule changes, both must change in lockstep. The API-handler value is throwaway (the worker re-derives), so the API-handler version should probably go away — but `_publish_pending` and `create_execution` rely on it.

4. **`is_provider` not populated in the API handler's `Organization`** (`api/src/routers/workflows.py:867`). Currently throwaway because the worker re-fetches; if anyone removes the re-fetch, `set_scope`/`resolve_scope` silently break. Either populate it or document the throwaway nature in code.

5. **`_get_cli_org_id` has no permission gate** (`api/src/routers/cli.py:357-392`). Honors any scope from any caller. Mostly invisible because most consumers are engine-only by design, but the table-document handlers under it (now being consolidated away) made the bypass real. After consolidation, this is "all surviving CLI endpoints are engine-intended but lack server-side enforcement of that intent" — defense in depth would harden them.

6. **`DeveloperContext` is spread across multiple consumers.** `_get_cli_org_id` (cli.py:384), `workflows.py:826-832`, `cli.py:606+` (`bifrost run` direct path), and the `bifrost run --interactive` session machinery (`cli.py:846+`, `client/src/pages/CLI.tsx`, `/api/cli/sessions/*`, the `cli-sessions:{user.id}` WebSocket channel). The interactive path is alive. Refactoring `DeveloperContext` means deciding the future of the dev-workbench feature.

7. **Embed token scope handling** (`api/src/core/auth.py:201-211`) exempts embed tokens from the "non-superuser must have org" rule. Works in practice; not pinned down with tests; how scope flows for embed callers isn't documented.

8. **Raw `WHERE organization_id = ...` bypasses `OrgScopedRepository`** in `workflows.py`, `knowledge_sources.py`, `roi_reports.py`, `executions.py`, `users.py`, `export_import.py`. Each is correct given a correct upstream resolver, but they bypass the repo's role-check defense-in-depth.

## Approaches considered and rejected

For a future session: these were discussed during the design pass and ruled out. They might still be right for a different problem; they're not the right answer for this one.

- **Rescoping the engine token** to be a transport-only credential, with caller identity propagated separately. The user's call: engine identity is intentional, the engine is a controlled environment, an admin wrote the workflow, and the engine has the authority to do what workflows need. Don't redesign it.

- **`X-Bifrost-On-Behalf-Of` header carrying caller claims.** Rejected because it requires the engine-vs-caller split above, which we're not doing.

- **Per-workflow capability sets** (e.g. `permissions: ["integrations:write"]` declared in workflow manifest). Rejected because it adds per-workflow identity bookkeeping; the user explicitly said they don't want more identity stuff per workflow.

- **Per-workflow `bypass_caller_auth: bool`** flag. Same reason — still per-workflow identity bookkeeping.

- **New FastAPI dependency markers** (`CurrentBrowserSession`, `CurrentCallerScoped`, `CurrentEngineOnly`). Rejected because the underlying problem they solved (caller-vs-engine identity routing) was rejected. The existing dependencies (`CurrentUser`, `CurrentSuperuser`) are sufficient.

- **`CurrentOrgScoped` marker as a single new dependency.** Briefly considered as a lighter version of the four-marker scheme. Rejected because the existing scope helpers (`_resolve_target_org_safe`, `resolve_target_org`) already do the work; adding a marker would be net-additional code without solving a concrete problem.

The shape these all share: **redesigning where authorization decisions happen.** The user's preference is to leave the engine alone and address only the specific bug (CLI handlers don't enforce policies). That preference rules out anything that touches the engine token's role.

## Out of scope, captured for later

- **`is_superuser` → `is_provider` migration in `resolve_target_org` and `resolve_org_filter`.** Real change in who-can-do-what (a non-superuser provider-org member would gain cross-org access; a hypothetical superuser outside the provider org would lose it). Today's rule (`is_superuser` as the gate) continues unchanged.

- **`DeveloperContext` rationalization.** Currently spread across `_get_cli_org_id` (cli.py:384), `workflows.py:826-832`, `cli.py:606+` (the `bifrost run` direct path), and the entire `bifrost run --interactive` session machinery (`cli.py:846+`, `client/src/pages/CLI.tsx`, the `/api/cli/sessions/*` endpoints, the `cli-sessions:{user.id}` WebSocket channel). The interactive path is alive and used. Removing `DeveloperContext` is a non-trivial refactor — defer.

- **The other `/api/cli/*` endpoints** — configs, integrations management, knowledge mutation, AI. Engine-only by design; not consolidated.

- **Embed token org scoping.** `auth.py:201-211` exempts embed tokens from the "non-superuser must have org" rule. Works today; no failing tests; pinning down with explicit tests is its own pass.

- **Raw `WHERE organization_id = ...` patterns** scattered through routers (workflows.py, knowledge_sources.py, roi_reports.py, executions.py, users.py, export_import.py — see prior session's audit). Most are correct given correct upstream resolvers; full audit is a separate sweep.

## Plan stack

1. Add `created_by` / `updated_by` to `DocumentCreate` / `DocumentUpdate`. Accept only when `ctx.user.user_id == ENGINE_USER_ID` or `ctx.user.is_superuser`; otherwise 403 if the field is present in the body.
2. Add `POST /api/tables/{id}/documents/upsert` to REST. Reuse the `INSERT ... ON CONFLICT DO UPDATE` pattern from the CLI handler.
3. Move auto-create-on-insert into the Python SDK (`tables.insert` and `tables.insert_batch`): catch 404, post `/api/tables`, retry. Race resolution via the existing 409-on-create-conflict path.
4. Repoint Python SDK's `tables.documents.*` methods at the REST URLs.
5. Delete CLI table-document handlers (cli.py:2818-3370) and the helpers `_find_or_create_table_for_sdk`, `_find_table_for_sdk` if no surviving callers.
6. Drop `Table.application_id` column. Migration removes the column and the FK to `applications.id`. Strip the few REST table-CRUD references (`TableUpdate.application_id`, `_validate_application_for_table`, `tables.py:285-289`).
7. Validate `scope` as UUID/`"global"`/null in `_get_cli_org_id`. (Independent small fix.)

Each step is independently reviewable. After step 5 lands, web UI and SDK share one table-document path; policy/WS/audit happen uniformly. Step 6 is a separate pure-removal migration. Step 7 closes a small validation gap on the CLI endpoints that remain.
