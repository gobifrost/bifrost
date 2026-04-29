# Table Access Rules — Design

**Date:** 2026-04-29
**Status:** Spec, awaiting plan

## Problem

Data-heavy apps cannot read or write tables directly from the browser. The
`/api/tables/*` endpoints exist and the frontend services already wrap them
(`client/src/services/tables.ts`), but every endpoint requires `CurrentSuperuser`
(`api/src/routers/tables.py:52-57`). To read a table from an app, the app has to
invoke a workflow that proxies the read — adding queue latency, an execution
record, and a worker round-trip to what should be a single SQL query.

The goal is to let apps (and other authenticated users) call the existing tables
REST endpoints directly, **without weakening the default**: tables that don't
opt in should remain workflow-only, exactly as they are today.

## Non-goals

- **Row-level access rules.** No per-row roles, no filter expressions, no
  computed predicates. If row scoping is needed, callers do it inside a
  workflow. Row-level scoping can be added later without breaking this design.
- **Table CRUD permissions.** Creating, renaming, and deleting *tables*
  themselves is unchanged: superuser/admin only via the existing endpoints.
  This spec is about row-level CRUD (insert/select/update/delete on
  `Document`), gated by table-level access rules.
- **Reworking the workflow path.** Workflows continue to use the SDK; the SDK
  bypasses the access-rule check (workflow context is implicitly trusted).

## Model

A new `access` block on each `Table`. Three scopes (Everyone, Role, Creator),
each carrying four boolean CRUD flags. Resolution is **additive** (union of
grants); default is deny.

```yaml
table:
  organization_id: <uuid|null>     # existing org scoping, unchanged
  access:
    everyone:            # any authenticated user with access to the table's scope
      read: bool
      create: bool
      update: bool
      delete: bool
    role:
      roles: [role_id, ...]
      read: bool
      create: bool
      update: bool
      delete: bool
    creator:             # the user who created the row (Document.created_by)
      read: bool
      create: bool
      update: bool
      delete: bool
```

### Scope semantics

- **Everyone** — any authenticated user who already qualifies for the table's
  org scope. For an org-scoped table, that means members of that org. For a
  global table (`organization_id IS NULL`), that means any authenticated user.
  Mirrors the SharePoint "Everyone except External Users" mental model.
- **Role** — users holding any of the role IDs listed in `access.role.roles`.
  Role membership is checked via the existing `UserRole` junction
  (`api/src/models/orm/users.py:128-142`).
- **Creator** — applies per-row: the user whose ID is stored in
  `Document.created_by`. The row's creator gets the CRUD actions enabled in
  this block; `create` here means "the logged-in user can insert rows" (which
  trivially makes them the creator of the row they just inserted).

### Resolution: additive (union)

A user may perform action `X` on a row if **any** scope they qualify for
grants `X`. Examples for a single table:

| Config | Alice (member of org, no roles) | Bob (member of org, has Role A) | Bob acting on a row he created |
|---|---|---|---|
| `everyone.read=true` | read ✓ | read ✓ | read ✓ |
| `role.roles=[A], role.update=true` | — | update ✓ | update ✓ |
| `creator.delete=true` | — | — | delete ✓ |

Default for a brand-new table: every flag false, no roles listed →
**workflow-only**, byte-for-byte equivalent to current behavior.

### `created_by` semantics

`Document.created_by` already exists as a nullable `String(255)` column
(`api/src/models/orm/tables.py:103`); no migration needed for the field
itself, only for population.

- **REST insert** (logged-in user): `created_by = <session user id>`,
  unconditionally.
- **Workflow SDK insert**: `created_by` defaults to `NULL`. A new optional
  argument lets the workflow attribute the row to a user (e.g. a form
  submission's submitter). Workflows that don't pass it leave the row with
  `created_by = NULL`, which means **no Creator-scope grant ever applies** to
  that row — exactly the safe behavior for system-inserted data.

`Document.updated_by` already exists too; same rules — REST writes set it to
the session user; SDK writes can pass it explicitly, default `NULL`.

## Storage

### `Table.access` column

New JSONB column on `tables`, nullable, default `NULL`.

`NULL` means "no access rules configured" → workflow-only (the default).
A non-null value is a strict-shape JSON object validated by a Pydantic model:

```json
{
  "everyone": {"read": false, "create": false, "update": false, "delete": false},
  "role":     {"roles": [], "read": false, "create": false, "update": false, "delete": false},
  "creator":  {"read": false, "create": false, "update": false, "delete": false}
}
```

The shape is fixed; missing sub-blocks are treated as all-false at evaluation
time. This avoids partial-update gotchas and keeps the JSON cheap to inspect.

### Migration

One Alembic migration:

1. `ALTER TABLE tables ADD COLUMN access JSONB DEFAULT NULL`
2. No backfill — every existing table stays workflow-only.

`Document.created_by` and `Document.updated_by` already exist; no schema
change. New code paths populate them; existing rows with `NULL` are simply
unaffected by the Creator scope.

## Enforcement

### Where the check lives

A single `TableAccessChecker` helper module
(`api/shared/table_access.py`), called from both the REST router and the SDK
adapter. The checker takes:

- the `Table` (with `access` loaded),
- the `Action` (read/create/update/delete),
- the `Caller` (a normalized struct: user id, org id, role ids, or a
  `WorkflowCaller` sentinel),
- and, for read/update/delete, the row's `created_by` (or `None` for create).

It returns `Allow` or `Deny`. Workflow callers always return `Allow`.

For a logged-in user it walks the three scopes in order — Everyone, Role,
Creator — and returns `Allow` on the first grant. The check is pure and
synchronous (no DB I/O); the caller is responsible for loading `Table.access`,
the user's role IDs, and the row's `created_by` before invoking it.

### REST endpoints (`api/src/routers/tables.py`)

For each document endpoint:

1. Replace `CurrentSuperuser` with `Context` (the standard
   logged-in-user dependency).
2. Resolve the `Table` (existing org scoping unchanged).
3. **For list/query**: if no scope grants `read`, return 403. If Everyone or
   Role grants `read`, return rows unfiltered (broader grant wins under
   additive resolution). If **only** the Creator scope grants `read`, filter
   rows by `created_by = <user>` at the SQL level. (This is the one place a
   Creator grant produces a filter rather than a per-row check, because the
   alternative is loading every row and discarding most of them.)
4. **For get/update/delete**: load the row, then run the checker against
   `(action, table, caller, row.created_by)`. 403 on deny, 404 on not-found.
   Order matters: check existence first to avoid leaking presence to
   unauthorized callers — return 403 for both "exists but no access" and
   "doesn't exist" so callers can't probe.
5. **For create**: run the checker with `created_by=None` (the row doesn't
   exist yet); the Creator scope's `create` flag, if set, is sufficient.
6. **Superuser bypass**: superusers continue to bypass the checker — same as
   they bypass other access checks today. The existing admin Tables UI keeps
   working without changes.

The table-level admin endpoints (POST `/api/tables`, PATCH `/api/tables/{id}`,
DELETE `/api/tables/{id}`) keep `CurrentSuperuser`. Only the document-level
endpoints relax to `Context`.

### Workflow SDK

The SDK's `tables` client gets an internal `WorkflowCaller` marker; the
checker short-circuits to `Allow` for it. The workflow context already runs
with system trust (it's how reads work today); this just makes that explicit.

The SDK's insert/update methods gain an optional `created_by` / `updated_by`
parameter:

```python
sdk.tables.get("tickets").insert({"title": "..."}, created_by=submitter_user_id)
```

Default is `None` for both. Existing call sites are unaffected.

## API surface

### Pydantic contracts (`api/src/models/contracts/tables.py`)

New models:

```python
class TableAccessScopeCRUD(BaseModel):
    read: bool = False
    create: bool = False
    update: bool = False
    delete: bool = False

class TableAccessRoleScope(TableAccessScopeCRUD):
    roles: list[UUID] = []

class TableAccess(BaseModel):
    everyone: TableAccessScopeCRUD = Field(default_factory=TableAccessScopeCRUD)
    role:     TableAccessRoleScope = Field(default_factory=TableAccessRoleScope)
    creator:  TableAccessScopeCRUD = Field(default_factory=TableAccessScopeCRUD)
```

`TablePublic`, `TableCreate`, `TableUpdate` gain `access: TableAccess | None`.

### CLI / MCP / Manifest

Per CLAUDE.md "Keeping CLI, MCP, and manifest in sync":

- **CLI** (`bifrost tables update`): gains `--access <json>` or, more
  ergonomically, `--allow everyone.read --allow role.update --role <id>` —
  exact CLI surface chosen during plan-writing.
- **MCP** (`api/src/services/mcp_server/tools/tables.py`): per CLAUDE.md, new
  MCP tools must be **thin HTTP wrappers**, not direct ORM. The existing
  tables MCP tool is already on the drift list
  (`docs/plans/2026-04-18-mcp-router-reconciliation.md`); rather than extend
  the divergent ORM-direct tool with `access` plumbing, the access-rule
  surface is added during MCP reconciliation. For this spec, the MCP tool's
  current behavior (superuser-only, bypasses access rules) is preserved
  unchanged.
- **Manifest** (`api/bifrost/manifest.py`, `manifest_generator.py`,
  `github_sync.py`): `ManifestTable` gains an `access` field. Round-trip
  test added to `tests/unit/test_manifest.py`. Role IDs in the manifest are
  rewritten by name during import (same pattern as `FormRole` /
  `AppRole` today) so exports are portable.
- **DTO parity** (`api/bifrost/dto_flags.py`): `access` is added to
  `TableUpdate` exposure or, if it's UI-managed only, added to
  `DTO_EXCLUDES` with a comment. Decided during plan-writing.

### Frontend

The existing `client/src/services/tables.ts` continues to work unchanged for
admins. For app-author UX:

- The Tables admin page gains an "Access" tab/section per table — three
  collapsible cards (Everyone, Role, Creator), each with four checkboxes;
  the Role card includes a role multi-select.
- Apps that read tables in the browser use the existing `$api.useQuery`
  hooks against `/api/tables/*`. No new client surface.
- A Vitest test covers the access editor component
  (`TableAccessEditor.test.tsx`).

## Testing

- **Unit, `tests/unit/test_table_access.py`**: pure-function checker tests
  for every scope × action × caller combination, including the Creator
  filter behavior for list/query.
- **Unit, `tests/unit/test_manifest.py`**: round-trip a table with a fully
  populated `access` block including role IDs.
- **E2E, `tests/e2e/platform/test_table_access.py`**: matrix of three users
  (admin, role-holder, plain) hitting list/get/insert/update/delete on a
  table with a representative access config; assertions on 200/403 and on
  the Creator-filter case (Bob sees only his rows).
- **E2E, existing tests**: the existing `tests/e2e/platform/test_tables.py`
  is updated to assert the default-deny behavior (a non-superuser hitting a
  table with no `access` block gets 403).
- **Client unit, `client/src/services/tables.test.ts`**: hooks unchanged;
  no new test required unless the access-editor component is added.
- **Client e2e**: a Playwright spec exercises the admin access editor.

## Rollout & migration

1. Schema migration adds `access` column, default `NULL`. Existing tables
   are workflow-only — same as today.
2. REST endpoints relax from `CurrentSuperuser` to `Context`; the access
   checker enforces deny when `access IS NULL`. **A non-superuser who could
   not call these endpoints yesterday still cannot call them today** — the
   only change is the error code (401 → 403 in some paths) and the
   opportunity to opt in.
3. SDK behavior is unchanged for existing call sites; new `created_by` /
   `updated_by` params default to `NULL`.
4. Admin UI ships the access editor.
5. Apps that want direct table access have their tables configured
   per-table; they migrate off the workflow-proxy reads at their own pace.

There is no flag day and no data migration. Existing apps that proxy reads
through workflows keep working until their authors choose to switch.

## Open question for plan-writing

**CLI ergonomics** — `--access <json>` is unambiguous but ugly; flag-style
(`--allow everyone.read --role-id <id> --allow role.update`) is nicer but
requires careful parsing. Resolved during plan-writing; doesn't affect the
core design.

## Future work (explicitly out of scope)

- **Row-level rules.** A natural extension is a per-table predicate (JSONB
  filter expression evaluated at read time) layered on top of the table-
  level `access` block. The current design's Allow/Deny return type and
  separate row-aware path for the Creator scope leave a clean seam for it.
- **MCP tool reconciliation.** Aligning the tables MCP tool with the REST
  endpoint behavior (including access-rule enforcement) is tracked in
  `docs/plans/2026-04-18-mcp-router-reconciliation.md`.
- **Audit log.** Per-row access decisions are not currently logged.
