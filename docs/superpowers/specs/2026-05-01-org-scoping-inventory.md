# Org Scoping Inventory (2026-05-01)

> **Reference material.** This is a snapshot of how organization scoping is implemented across the Bifrost codebase as of `feat/table-access` (commit `be252086`). It is the input to the caller-identity design doc (`2026-05-02-workflow-caller-identity-design.md`). It is not a plan and does not propose changes; it documents the current state so the design can address it concretely.

## Executive summary

The Bifrost codebase implements organization scoping with a layered enforcement model:

1. **Helper layer** — `resolve_target_org()` (REST writes), `resolve_org_filter()` (REST reads/lists), and `resolve_scope()` / `set_scope()` (Python SDK) gate scope requests.
2. **Repository layer** — `OrgScopedRepository` enforces cascade scoping (org + global) for lookup/listing.
3. **Endpoint layer** — `CurrentSuperuser` dependency protects admin-only routes.

The intended rule is: **provider-org members** (`organization.is_provider == True`, well-known UUID `00000000-0000-0000-0000-000000000002`) **can target any org; non-provider users can only access their own org or global resources** (`organization_id IS NULL`).

**Key finding:** `UserPrincipal` does NOT carry `is_provider`. Provider membership is determined by:

- The Python SDK's runtime context: `ExecutionContext.organization.is_provider` (looked up from DB at workflow start).
- The server-side gate today: indirectly via `is_superuser` (the platform-admin role bit), which *correlates* with provider-org membership only because of the migration history (PLATFORM users were moved into the provider org). The role bit is the wrong concept and can drift.

The result is **three different rules across three layers**:

| Surface | Cross-org gate | Concept used | Status |
|---|---|---|---|
| `ExecutionContext.set_scope()` (workflow runtime, `api/bifrost/_execution_context.py:148`) | `org.is_provider` | The intended rule | Correct |
| `resolve_target_org` (REST, `api/src/core/org_filter.py:91`) | `is_superuser` | Wrong proxy | Works in practice but conceptually wrong |
| `_get_cli_org_id` (CLI/Python SDK transport, `api/src/routers/cli.py:357`) | None | Broken | Active vulnerability |

---

## 1. Concept layer

### `UserPrincipal` (`api/src/core/auth.py:30-79`)

Auth model documented at line 38-41:

- `is_superuser=true, org_id=UUID`: Platform admin in an org
- `is_superuser=false, org_id=UUID`: Regular org user
- `is_superuser=true, org_id=None`: System account (global scope)
- `is_superuser=false, org_id=None`: INVALID (rejected at token parsing)

Fields relevant to scoping:

- `user_id: UUID`
- `email: str`
- `organization_id: UUID | None`
- `is_superuser: bool` — global platform-admin flag
- `roles: list[str]`, `role_ids: list[UUID]`, `role_names: list[str]`
- `embed: bool` — embed session token
- `app_id: str | None`, `form_id: str | None` — for embed tokens

Computed:

- `is_platform_admin` (line 63): alias for `is_superuser` (no separate concept)
- `is_system_account` (line 68): `is_superuser and organization_id is None`

**Absent:** `is_provider`. The token does not carry it; the principal does not derive it.

### `Organization.is_provider`

- Column on `organizations` table (`api/src/models/orm/organizations.py:39`).
- Well-known provider org UUID: `PROVIDER_ORG_ID = '00000000-0000-0000-0000-000000000002'` (defined in migration `api/alembic/versions/20260107_022300_add_provider_org.py:26`).
- Migration moved all PLATFORM-type users into the provider org and made `users.organization_id` non-nullable.
- Exactly one provider org by design; cannot be deleted.

### JWT claims

Token construction sites: `api/src/routers/auth.py:623-629`, `api/src/routers/oauth_sso.py:403-405`, `api/src/routers/mfa.py:228-232`, `api/src/routers/embed.py:81-85,159-163`.

Standard payload:

```json
{
  "sub": "user-uuid",
  "email": "...",
  "name": "...",
  "is_superuser": true|false,
  "org_id": "uuid|null",
  "roles": [...],
  "embed": false|true,
  "jti": "...",
  "app_id": "...",
  "form_id": "...",
  "verified_params": {...},
  "exp": <ts>,
  "type": "access|refresh|embed",
  "iss": "...",
  "aud": "..."
}
```

**Absent:** `is_provider`. To know whether a user is in the provider org, you must look up `Organization.is_provider` in the database.

---

## 2. Resolver helpers

| Helper | File:line | Concept it gates on | Returns | Used by |
|---|---|---|---|---|
| `resolve_target_org()` | `api/src/core/org_filter.py:91-139` | `is_superuser` | Target org UUID or None (global) | REST writes (tables create, etc.) |
| `resolve_org_filter()` | `api/src/core/org_filter.py:28-89` | `is_superuser` | `(filter_type, org_id)` for `OrgScopedRepository` | REST reads/lists (workflows, forms, agents, apps, executions, users, knowledge sources) |
| `_resolve_target_org_safe()` | `api/src/routers/tables.py:580-589` | wraps `resolve_target_org` with 422 on invalid scope | UUID or None | `tables.create_table`, `get_table_or_404` |
| `_get_cli_org_id()` | `api/src/routers/cli.py:357-392` | **none** | scope as-given (no UUID validation), or `DeveloperContext.default_org_id` | every CLI handler that takes scope |
| `ExecutionContext.set_scope()` | `api/bifrost/_execution_context.py:148-167` | `org.is_provider` | sets `_scope_override` or raises `PermissionError` | workflow code: `context.set_scope(...)` |
| `resolve_scope()` | `api/bifrost/_context.py:128-147` | `context.organization.is_provider` (if context exists; else passes through for CLI mode) | resolved scope or raises `PermissionError` | every Python SDK table method before HTTP request |

### `resolve_target_org()` logic

```
if user.is_superuser:
    if scope is None: return default_org_id
    if scope == "global": return None
    return UUID(scope)  # raises ValueError on invalid
else:
    return user.organization_id  # scope ignored
```

### `resolve_org_filter()` logic

```
superuser + scope=None  → ALL          (no WHERE clause)
superuser + scope="global" → GLOBAL_ONLY (org_id IS NULL)
superuser + scope=<uuid>  → ORG_ONLY   (org_id = uuid, no global fallback)
non-superuser (any scope) → ORG_PLUS_GLOBAL (org_id = user_org OR org_id IS NULL)
```

### `_get_cli_org_id()` logic

```
if scope == "global": return None
if scope: return scope                    # NO validation, NO permission check
# fall back to user's DeveloperContext.default_org_id
```

### `set_scope()` logic (SDK runtime)

```
if org_id is None: clear override
if org_id == original_org_id: no-op
if not context.organization.is_provider:
    raise PermissionError
set _scope_override = org_id
```

### `resolve_scope()` logic (SDK call-time)

```
if scope is None: return default
if scope == default: return scope
if no execution context (CLI mode): return scope  # JWT auth handles it
if context.organization.is_provider: return scope
raise PermissionError
```

---

## 3. `OrgScopedRepository`

**Location:** `api/src/repositories/org_scoped.py`

Generic base for multi-tenant repository logic. Enforces:

1. Cascade scoping — for org-scoped entities, returns org-specific + global records.
2. Role-based access control — for entities with role tables.

Constructor:

```python
def __init__(
    self,
    session: AsyncSession,
    org_id: UUID | None,
    user_id: UUID | None = None,
    is_superuser: bool = False,
):
```

The `is_superuser` flag here means **"trust the scope, skip role checks"** — it is *not* the same as `UserPrincipal.is_superuser`, even though most call sites pass it through directly.

### Access rules

**ID lookup (`get(id=...)`)** — globally unique; no cascade.

- Superuser: returns entity unconditionally (line 145-147).
- Regular user: returns only if `entity.organization_id IN (user_org, NULL)` (line 149-154).

This is the line the recent table-access work hardened. Cross-org UUID probes are a 404 for non-superusers.

**Name lookup (`get(name=...)`)** — cascade scoping applies even for superusers.

- Try `org_id`-scoped first.
- Fall back to global (`organization_id IS NULL`).
- For non-superusers, role check applies to the matched entity.

This ensures correct entity resolution when the same name exists in multiple orgs (the org-scoped one wins for users in that org).

### Call-site audit

Most call sites pass `ctx.user.is_superuser` through faithfully. A few pass `is_superuser=True` unconditionally — those are pre-authorized handlers (e.g. endpoints that already require `CurrentSuperuser`):

- `api/src/routers/tables.py:659` — `create_table` (handler is `@user = CurrentSuperuser`)
- `api/src/routers/tables.py:693, 735, 767` — list/sync handlers, all admin-gated upstream

These are not bypasses *if* the upstream gate is correct.

---

## 4. Scope-accepting endpoints

### REST (`?scope=` query param)

| Path | Handler | Resolver |
|---|---|---|
| `POST /api/tables` | `tables.create_table` | `_resolve_target_org_safe` |
| `GET /api/tables` | `tables.list_tables` | `resolve_org_filter` |
| `POST /api/tables/{id}/documents` (and all doc verbs) | `tables.*_document` | `_resolve_target_org_safe` via `get_table_or_404` |
| Various others | various | `resolve_org_filter` for lists |

All REST endpoints flow through one of the two correct (modulo `is_superuser`-vs-`is_provider`) helpers.

### CLI (request body `scope` field)

Every CLI handler that touches tenant data uses `_get_cli_org_id`, which has **no permission check**. List of affected paths:

```
/api/cli/config/{get,set,list,delete}
/api/cli/integrations/{get,upsert_mapping,get_mapping,list_mappings,delete_mapping}
/api/cli/tables/{create,list,documents/insert,documents/upsert,documents/get,documents/update,documents/delete,documents/insert/batch,documents/upsert/batch,documents/delete/batch,documents/query,documents/count}
/api/cli/knowledge/{store,store-many,search,get,delete,namespaces,namespace/{ns}}
/api/cli/ai/{complete,stream,info}
```

A non-superuser calling these endpoints (e.g. via the Python SDK after authenticating as themselves) can pass an arbitrary `scope` and have it honored. The Python SDK's `resolve_scope()` would catch this in workflow runtime, but a developer running the SDK from a CLI shell hits the wire directly with no runtime gate.

---

## 5. Raw SELECT bypasses

Endpoints that filter by `organization_id` directly with `.where()`, bypassing `OrgScopedRepository`. Most are downstream of a correct resolver call (e.g. `resolve_org_filter`), so they're correct *given* a correct resolver. They lack defense-in-depth at the repo layer.

| File:line | Concern level | Notes |
|---|---|---|
| `api/src/routers/cli.py:2637` | Low | After `_get_cli_org_id` (which is broken — but the symptom would be observable elsewhere first) |
| `api/src/routers/workflows.py:398, 531, 584, 605` | Medium | List queries; downstream of `resolve_org_filter` |
| `api/src/routers/knowledge_sources.py:221, 365` | Medium | Same shape |
| `api/src/routers/roi_reports.py:123, 233, 438` | Medium-low | Read-only metrics |
| `api/src/routers/metrics.py:234` | Medium-low | Read-only metrics |
| `api/src/routers/usage_reports.py:182, 223, 263` | Medium-low | Read-only AI usage |
| `api/src/routers/executions.py:79` | Medium | Sensitive execution history |
| `api/src/routers/users.py:87, 90` | Medium | User listings |
| `api/src/routers/export_import.py:551, 622, 757, 918, 1117` | High | Bulk operations; if `org_id` source is request body and not validated, this is a write-side bypass |

---

## 6. The execution engine identity

**Location:** `api/src/core/security.py:405-449` — `authenticate_engine()`.

Worker processes mint a synthetic, long-lived (30-day) superuser JWT at the start of each execution:

- `sub = ENGINE_USER_ID = "00000000-0000-0000-0000-000000000001"`
- `email = "engine@bifrost.internal"`
- `name = "Bifrost Engine"`
- `is_superuser = True`
- `org_id` not set → system account form

This token is saved to `~/.bifrost/credentials.json`. The Bifrost Python SDK reads it via `BifrostClient.get_instance()` and uses it as the bearer token on every HTTP call back to the API.

**Result:** every SDK call from inside a workflow arrives at the API with engine-superuser credentials. The original caller (the user who triggered the workflow, or the webhook payload, or the cron schedule) is preserved separately in `ExecutionContext.caller` (see `api/src/jobs/consumers/workflow_execution.py:657-666`) but **the API server never sees the caller principal** — it only sees the engine principal.

This is why CLI document endpoints (`api/src/routers/cli.py:2818-3346`) don't apply table policies: they evaluate as the engine, who is an unconditional superuser, so policy checks would always pass anyway. The handlers were written without policy checks because policy was meaningless at that layer.

The caller is preserved in the *workflow runtime context* (`ExecutionContext.organization`, `ExecutionContext.caller`), and the SDK's `set_scope()` and `resolve_scope()` enforce the provider rule against it. But that's runtime enforcement only, in the SDK, against the workflow's organization. The **API itself enforces nothing about the caller** because the engine token is what hits the wire.

This is the load-bearing observation for the design doc: fixing the scope helpers to use `is_provider` is downstream of fixing the broader problem that **the API is unaware of the caller's identity** during workflow execution.

---

## 7. Web SDK / client-side scope

`client/src/lib/app-sdk/tables.ts:17-27` — `withScope(path, scope?)` appends `?scope=<encoded>` if provided. Comment notes this mirrors the Python SDK and "provider admins can target a specific org; other callers omit it and the server defaults to the caller's org."

`useTable(name, { scope })` (`client/src/lib/app-sdk/use-table.ts:77-100`) plumbs scope through to `tables.query` and `tables.subscribe`.

**No client-side gating.** Browser session authenticates as the actual user; the user's JWT is used as bearer; the server enforces the rule. This is correct and unaffected by the workflow caller-identity issue.

---

## 8. Python SDK scope

Every SDK table method calls `resolve_scope(scope)` from `api/bifrost/_context.py` before issuing the HTTP request. The check uses `context.organization.is_provider`.

In workflow execution, the context is constructed at workflow start with the workflow's *organization* (which may differ from the caller's org, depending on how the workflow was triggered and what scope it's running under). `set_scope()` allows further override only if the workflow's org `is_provider`.

In CLI mode (running `bifrost <command>` from a developer's laptop), there is no `ExecutionContext`. `resolve_scope()` falls through to "let the server's JWT auth decide." Today that means `_get_cli_org_id` decides, which checks nothing.

---

## 9. Tests

### Existing scope-related coverage

- `api/tests/e2e/platform/test_tables.py::TestDocumentScopeQueryParam` — REST scope parameter behavior on document endpoints (provider admin cross-org, non-superuser ignored, scope=global, name collision, invalid scope 422).
- `api/tests/unit/test_scope_override.py::TestSetScope` and `TestResolveScope` — SDK-side scope override gating; provider vs non-provider denied; CLI-mode passthrough.
- `api/tests/e2e/platform/test_policies.py` — cross-org isolation via policy (404 from org gate before policy is checked).
- `api/tests/unit/routers/test_scoped_lookups.py` — `OrgScopedRepository` cascade scoping behavior.

### Gaps

1. **CLI scope validation** — no test verifies `_get_cli_org_id` rejects invalid UUIDs (because it doesn't).
2. **Cross-org via Python SDK on the CLI path** — no E2E test for "non-superuser logs into bifrost CLI, calls `bifrost.tables.insert(scope=<other-org-uuid>)`." That bypass is currently untested.
3. **Caller-identity propagation** — no concept exists; no test exists.
4. **Embed-token scope** — embed tokens have no `org_id` in the JWT (and the auth.py:201-211 special case exempts them from the "non-superuser must have org" rule). How embedded queries scope is undocumented in the code; no test pins it down.
5. **System-triggered workflows (cron, webhook)** — no human caller; how the principal is constructed for the API to see is undefined because the API doesn't see one today.

---

## 10. Top inconsistencies

1. **`_get_cli_org_id` has no permission check.** Every CLI endpoint trusts the scope on the wire. Python SDK callers running from a developer shell can target any org.
2. **`UserPrincipal` carries `is_superuser`, not `is_provider`.** The role bit is the wrong concept; it correlates with provider membership only by migration accident.
3. **The execution engine runs everything as `is_superuser=True, org=None`.** No caller principal reaches the API during workflow SDK calls. Policy checks in CLI endpoints are absent partly because they would always pass anyway.
4. **`resolve_target_org` and `resolve_org_filter` gate on `is_superuser`.** A non-superuser member of the provider org cannot scope across orgs today, even though the intended rule allows it.
5. **Embed tokens have no documented org-scoping model.** They bypass the "non-superuser must have org" rule with a code comment but no tests.
6. **Raw `WHERE organization_id = ...` pattern is widespread.** Most uses are correct given a correct resolver, but they bypass `OrgScopedRepository`'s defense-in-depth (especially the role-check half) and hide assumptions inside individual handlers.

---

## 11. What this means

The CLI/REST consolidation that was the original goal of this branch is downstream of three larger problems:

1. **The API doesn't know the caller during workflow execution.** Engine-superuser is the only identity it sees.
2. **The cross-org gate uses the wrong concept** (`is_superuser` instead of `is_provider`), surviving in production only because of migration history.
3. **The CLI handler path has no server-side gate at all,** which is invisible in workflow runtime (the SDK gates) but exploitable from the Bifrost CLI shell.

Any consolidation of CLI doc endpoints into the REST surface is unsafe until #1 is fixed — otherwise we'd be moving SDK calls onto endpoints that would also see engine-superuser and bypass policies the same way. Fixing #2 is conceptually clean but largely cosmetic until the API actually has a real principal to evaluate the rule against (#1). #3 is the most exploitable today; it gets fixed for free as a side effect of #1 because the CLI handlers go away.

The design doc (`2026-05-02-workflow-caller-identity-design.md`) addresses these in order.
