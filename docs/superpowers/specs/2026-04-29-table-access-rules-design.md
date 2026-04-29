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

## In-scope deliverables

Three deliverables ship together:

1. **REST access rules** — the `Table.access` block, the checker, and the
   relaxed REST endpoints (covered in the rest of this spec).
2. **Web SDK** — a TypeScript `tables` client mirroring the Python workflow
   SDK's surface (`get`, `insert`, `update`, `delete`, `upsert`, `query`,
   `count`, `insert_batch`, `upsert_batch`, `delete_batch`). Exposed to apps
   via the existing platform scope (`client/src/lib/app-code-runtime.ts`).
   Authoritatively backed by the same REST endpoints; access enforced
   server-side.
3. **Websocket subscriptions** — apps can subscribe to a table and receive
   push events on insert/update/delete. Plugs into the existing
   `/ws/connect` channel-based subscription system
   (`api/src/routers/websocket.py`) and Redis pub/sub fanout
   (`api/src/core/pubsub.py`). Subscription authorization runs the same
   `TableAccessChecker` against `read`.

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
  Configuring Creator with `read=true` but `create=false` is a valid and
  useful pattern — users see only rows that workflows created on their
  behalf, but cannot insert new rows themselves.

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
- **Workflow SDK insert**: `created_by` is **resolved automatically from the
  execution context** (`context.user_id` —
  `api/bifrost/_execution_context.py:84`). The execution context already
  carries the originating user for every trigger path:
  - Manual run / form submission / user-scheduled run → the human user's
    UUID.
  - Webhook / event system / autonomous trigger →
    `SYSTEM_USER_ID = "00000000-0000-0000-0000-000000000001"`
    (`api/src/core/constants.py`), the existing system-user sentinel.
  - Legacy executions with no recorded user → `NULL`.

  An optional `created_by=` argument on the SDK lets a workflow override the
  attribution explicitly. Default behavior pulls from the context so existing
  call sites get correct attribution for free. The system-user sentinel is a
  real `User` row, so Creator grants will apply to system-inserted rows
  (rare, generally undesirable — table authors who want system inserts to
  *not* match Creator grants should leave Creator scope empty for that
  table).

`Document.updated_by` follows the same rule: REST writes set it to the
session user; SDK writes resolve from `context.user_id`, with an optional
`updated_by=` override.

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

It returns `Allow` or `Deny`. **Platform admins (superusers) and workflow
callers always return `Allow`** — they bypass the access block entirely, the
same way they bypass other entity ACLs in Bifrost. Admin access is unconditional
across every scope and action; admins do not need to be listed in any role.

For a non-admin logged-in user the checker walks the three scopes in order —
Everyone, Role, Creator — and returns `Allow` on the first grant. The check is
pure and synchronous (no DB I/O); the caller is responsible for loading
`Table.access`, the user's role IDs, and the row's `created_by` before
invoking it.

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
6. **Platform admin bypass**: superusers (platform admins) continue to bypass
   the checker — same as they bypass other access checks today. The existing
   admin Tables UI keeps working without changes. Admins can read/write every
   table regardless of `access` configuration.

The table-level admin endpoints (POST `/api/tables`, PATCH `/api/tables/{id}`,
DELETE `/api/tables/{id}`) keep `CurrentSuperuser`. Only the document-level
endpoints relax to `Context`.

### Workflow SDK

The SDK's `tables` client gets an internal `WorkflowCaller` marker; the
checker short-circuits to `Allow` for it. The workflow context already runs
with system trust (it's how reads work today); this just makes that explicit.

The SDK's insert and update methods automatically resolve `created_by` /
`updated_by` from `context.user_id` — covering form submitters, manual
runners, user-scheduled runs (the originating human), and webhook / event /
autonomous triggers (the system-user sentinel). An optional override is
available for workflows that need to attribute on behalf of a different
user:

```python
# Default: row attributed to context.user_id (form submitter, etc.)
sdk.tables.get("tickets").insert({"title": "..."})

# Override (rare — e.g., a workflow ingesting an external system's event)
sdk.tables.get("tickets").insert({"title": "..."}, created_by=external_user_id)
```

Existing call sites get correct attribution automatically; no code changes
required for the default path. The override is the escape hatch.

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
admins. New surfaces:

- **Admin access editor.** The Tables admin page gains an "Access"
  tab/section per table — three collapsible cards (Everyone, Role,
  Creator), each with four checkboxes; the Role card includes a role
  multi-select. Vitest covers the editor (`TableAccessEditor.test.tsx`).
- **Web SDK for apps** (`client/src/lib/app-sdk/tables.ts`). A new
  TypeScript module mirroring the Python workflow SDK's `tables` surface
  one-for-one. Methods: `get`, `insert`, `update`, `delete`, `upsert`,
  `query`, `count`, `insert_batch`, `upsert_batch`, `delete_batch`,
  `subscribe`. All async, all backed by the REST endpoints (and `/ws` for
  `subscribe`). Exposed to apps via the existing platform scope
  (`client/src/lib/app-code-runtime.ts`); apps import as
  `import { tables } from "bifrost"`. Vitest covers every method.
- **Subscription hook**
  (`client/src/lib/app-sdk/use-table-subscription.ts`). React hook wrapping
  `tables.subscribe` for the common case. Vitest covers reconnect, cleanup,
  and access-denied paths.

### Websocket subscriptions

A new channel kind is added to the existing `/ws/connect` router
(`api/src/routers/websocket.py`):

- **Channel name:** `table:{table_id}`. One channel per table.
- **Subscribe handshake:** when a client subscribes, the server loads the
  `Table`, runs `TableAccessChecker(action=read, ...)` against the
  websocket-authenticated user, and rejects the subscription if denied.
  Subscriptions on tables with `access IS NULL` are denied (workflow-only).
- **Server-side fanout:** `DocumentRepository.insert/update/delete` calls a
  new `publish_document_change(table_id, action, doc)` helper in
  `api/src/core/pubsub.py`, which broadcasts a small JSON envelope to the
  channel:

  ```json
  {
    "type": "document_change",
    "table_id": "...",
    "action": "insert" | "update" | "delete",
    "id": "...",
    "created_by": "<uuid|null>",
    "data": { ... }
  }
  ```

- **Per-recipient Creator-scope filtering.** This is the one non-trivial
  bit. Tables that grant `read` only via the Creator scope can't broadcast
  every change to every subscriber — that would leak rows that don't belong
  to the recipient. Two-step filter:

  1. The broadcast envelope always includes `created_by` (cheap, already
     loaded).
  2. The websocket router's per-connection send path checks: if the
     subscriber's effective read grant is *Creator-only*, drop the event
     unless `created_by == subscriber.user_id`. If Everyone or Role grants
     read, send unfiltered.

  The per-connection check uses cached access-rule + role data computed at
  subscribe time (refreshed if the table's `access` column changes — see
  invalidation below).

- **Invalidation on access changes.** When `Table.access` is updated via
  the admin UI, the API publishes a `table_access_changed:{table_id}`
  envelope on the same channel. Each connected client recomputes its
  effective grants — or, if the change revokes read, the server closes the
  subscription with a `subscription_revoked` event. (Server enforces;
  client is informed.)

- **Reconnect semantics.** No replay. Clients that disconnect lose events
  in the gap; on reconnect they should re-fetch state via REST. The
  envelope is fire-and-forget. (This matches how `execution:*` channels
  behave today.)

- **What the SDK exposes:** `tables.subscribe(name, callback, options?)`
  returns an `unsubscribe()` function. The SDK handles the ws connection
  (or reuses the runtime's existing connection), the handshake, and the
  envelope-to-callback dispatch. Callers see typed events:

  ```ts
  tables.subscribe("tickets", (evt) => {
    // evt: { action: "insert"|"update"|"delete", id, data, created_by }
  });
  ```

  React users use the `useTableSubscription` hook wrapper.

## Testing

The combinatorial space is large; coverage is structured so the unit layer
exhaustively walks scope × action × caller, and the e2e layer proves the
end-to-end wiring works on a representative slice.

**Backend unit:**
- **`tests/unit/test_table_access.py`** — pure-function checker. Every
  combination of: 3 scopes × 4 actions × {admin, role-holder, plain user,
  workflow caller} × {row-with-creator, row-without-creator}. Plus the
  list/query Creator-filter rule: Everyone-read and Role-read return
  unfiltered; Creator-read-only returns SQL-filtered.
- **`tests/unit/test_manifest.py`** — `ManifestTable` round-trip with a
  fully populated `access` block including role IDs and role-name
  rewriting on portable export.
- **`tests/unit/test_pubsub_table_changes.py`** — `publish_document_change`
  emits the right envelope shape; `table_access_changed` invalidation
  envelope is correct.
- **`tests/unit/test_dto_flags.py`** — DTO parity test passes with the new
  `access` field on `TableUpdate` (or with an explicit `DTO_EXCLUDES`
  entry, decided in plan-writing).

**Backend e2e:**
- **`tests/e2e/platform/test_table_access.py`** — REST matrix: 3 users
  (admin, role-holder, plain) × 4 actions × representative access configs.
  Assertions on 200/403 and on the Creator-filter case (Bob sees only his
  rows in list/query).
- **`tests/e2e/platform/test_tables.py`** — updated to assert default-deny:
  a non-superuser hitting a table with `access IS NULL` gets 403.
- **`tests/e2e/platform/test_table_subscriptions.py`** — websocket flow:
  subscribe with read access (accepted), without read access (rejected),
  receive insert event, receive update event, receive delete event,
  Creator-only filtering (Bob's subscription receives only his rows even
  when Alice inserts), access revocation (admin removes Bob's role,
  server emits `subscription_revoked`).

**Client unit (vitest):**
- **`client/src/lib/app-sdk/tables.test.ts`** — every SDK method:
  `get`/`insert`/`update`/`delete`/`upsert`/`query`/`count`/
  `insert_batch`/`upsert_batch`/`delete_batch`. Mocks `apiClient`; asserts
  shape parity with the Python SDK.
- **`client/src/lib/app-sdk/use-table-subscription.test.tsx`** — happy
  path, reconnect, cleanup-on-unmount, access-denied propagation,
  `subscription_revoked` cleanup.
- **`client/src/components/tables/TableAccessEditor.test.tsx`** — the
  admin access editor.

**Client e2e (Playwright):**
- **`client/e2e/tables-app-direct.spec.ts`** — an embedded test app uses
  the web SDK to insert + read a row; assert it round-trips without a
  workflow execution being created.
- **`client/e2e/tables-subscription.spec.ts`** — embedded test app
  subscribes to a table; assert it receives a push event when a separate
  REST call inserts a row.

## Rollout & migration

1. Schema migration adds `access` column, default `NULL`. Existing tables
   are workflow-only — same as today.
2. REST endpoints relax from `CurrentSuperuser` to `Context`; the access
   checker enforces deny when `access IS NULL`. **A non-superuser who could
   not call these endpoints yesterday still cannot call them today** — the
   only change is the error code (401 → 403 in some paths) and the
   opportunity to opt in.
3. SDK behavior is unchanged for existing call sites by default — but row
   inserts now record `created_by` from `context.user_id` automatically.
   This is strictly additive (existing rows untouched, existing reads
   unaffected); the only observable difference is that newly inserted rows
   carry the originating user.
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
- **Replay / event log for subscriptions.** Subscriptions are
  fire-and-forget; clients that miss events during a disconnect re-fetch
  via REST. A durable event log per table (so clients can replay since a
  cursor) is a future addition if real-time apps need exactly-once
  semantics.
