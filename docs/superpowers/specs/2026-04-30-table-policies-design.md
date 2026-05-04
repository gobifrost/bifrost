# Table Policies — RLS-style row policies for Bifrost tables

## Status

Replaces the in-flight `TableAccess` design (`2026-04-29-table-access-rules-design.md`) before merge. The branch `feat/table-access` will be reset to keep migration + SDK + websocket scaffolding and rebuild the access model on top.

## Why this exists

The shipped-but-unmerged `TableAccess` shape (`{everyone, roles, creator}`) handles "who can do what to a table." It does not handle row-state-aware rules ("can update only while not finalized"), row-relationship rules ("manager can read rows where `manager_user_id == caller.user_id`"), or any combination thereof. Without those, the realistic apps we want to build — perf-review, customer-onboarding with per-customer access, anything resembling Firestore-style direct reads — cannot be expressed without bouncing through workflows.

We don't want to ship a half-feature that gets used and then constrains us. The path forward is a **policy/rule** model close to Postgres RLS or Firestore Security Rules, with a JSON AST expression language and SQL pushdown for queries.

## High-level shape

Each table has a `policies` block: a list of named rules. Each rule grants a set of actions when its predicate evaluates true. Resolution is **additive OR**: if any rule allows the action, the action is allowed. Default deny.

```yaml
policies:
  - name: admin_bypass
    actions: [read, create, update, delete]
    when: { user: is_platform_admin }

  - name: own_org_rows
    actions: [read]
    when:
      eq: [{ row: organization_id }, { user: organization_id }]

  - name: employee_owns_responses
    actions: [read, update]
    when:
      and:
        - eq: [{ row: user_id }, { user: user_id }]
        - not: { eq: [{ row: finalized }, true] }

  - name: manager_reads_reports
    actions: [read]
    when:
      eq: [{ row: manager_user_id }, { user: user_id }]

  - name: hr_can_finalize
    actions: [update]
    when: { call: has_role, args: [hr_admin] }
```

Apps and workflows do not call a different endpoint. The same `/api/tables/{name}/documents/*` REST surface and the same web SDK / workflow SDK call into the policy evaluator. Default-deny means a table with no policies is workflow-only (admin-bypass aside) — same default as today.

## The expression model

Expressions are JSON ASTs. No string DSL, no parser, no injection surface. The AST stores cleanly in JSONB and is machine-renderable in an editor.

### Operators

Thirteen total. Every operator must have a SQL form (this is a constraint, not a guideline).

| Op | Shape | Semantics |
|---|---|---|
| `and` | `{ and: [Expr, ...] }` | Boolean AND, short-circuit |
| `or` | `{ or: [Expr, ...] }` | Boolean OR, short-circuit |
| `not` | `{ not: Expr }` | Boolean NOT |
| `eq` | `{ eq: [a, b] }` | Equality (NULL == NULL is false; matches SQL semantics) |
| `neq` | `{ neq: [a, b] }` | Inequality |
| `lt` / `lte` / `gt` / `gte` | `{ lt: [a, b] }` | Comparison (numbers, strings, ISO dates) |
| `in` | `{ in: [a, [v1, v2, ...]] }` | Set membership; right side is a literal list |
| `is_null` | `{ is_null: a }` | True iff the operand resolves to null/missing. `{not: {is_null: ...}}` covers "is set". |
| `call` | `{ call: <fn>, args: [...] }` | Function call (extension point) |

Operands are either nested expressions, references (`{row: ...}`, `{user: ...}`), or literals (string / number / bool / null / list). A bare scalar in an operand position is a literal.

### References

`{ row: "<field>" }` resolves against the row being checked. Top-level columns (`row.id`, `row.organization_id`, `row.created_by`, `row.updated_by`, `row.created_at`, `row.updated_at`) come from the document table's columns. Anything else — `row.user_id`, `row.manager_user_id`, `row.finalized`, etc. — comes from `documents.data->>'<field>'`.

Field paths use simple dot notation (`row.metadata.priority`). Implementation uses JSONB path access (`data #>> '{metadata,priority}'`) for nested fields.

`{ user: "<field>" }` resolves against the calling user's principal. Available fields:

| Field | Type | Source |
|---|---|---|
| `user.user_id` | UUID | `ctx.user.user_id` |
| `user.email` | string | `ctx.user.email` |
| `user.organization_id` | UUID \| null | `ctx.user.organization_id` |
| `user.is_platform_admin` | bool | `ctx.user.is_superuser` |
| `user.role_ids` | list[UUID] | `ctx.user.roles` |
| `user.role_names` | list[str] | derived once per request |

### Functions

One function for v1: `has_role(name_or_uuid: str) -> bool`. Returns true iff the argument is in `user.role_names` or matches a UUID in `user.role_ids` (string-compared).

Function calls are restricted to a **registered allow-list**. Each entry in the registry provides BOTH forms — the per-row evaluator and the SQL compiler — and they are registered together so they can't drift:

```python
FUNCTIONS = {
    "has_role": FunctionDef(
        evaluate=lambda args, user, row: (
            args[0] in user.role_names
            or args[0] in [str(r) for r in user.role_ids]
        ),
        compile=lambda args, user, row_ctx: sql_literal(
            args[0] in user.role_names
            or args[0] in [str(r) for r in user.role_ids]
        ),
        arg_types=[str],  # validator enforces this at table-create time
    ),
}
```

The validator at table create/update rejects any `call` whose target is not in the registry, with a 422 listing the available functions. A function that cannot be SQL-compiled (because it requires DB lookup at evaluation time, network calls, etc.) cannot be registered — the registration signature requires both forms. This is the gate that prevents `manages()` from sneaking in without an explicit design (it would require a `manager_relationships` table, an indexed lookup, and a join in the compile path — all of which need a separate spec).

For now, denormalize relationships into row fields. `ROW.manager_user_id` is preferable to `manages(ROW.user_id)` for both correctness and performance.

### Type semantics

- Comparisons coerce JSONB-extracted strings: `eq: [{row: user_id}, {user: user_id}]` compares as strings; UUID values are stringified by callers (the SDK already does this for `created_by`).
- Numeric comparisons require both sides to be numeric; mixed types compare false (matches PG `data->>'x' = '5'` behavior — string-equal but not numeric-equal).
- `null` propagates: `{eq: [{row: missing_field}, anything]}` evaluates false. Use `{not: {eq: [{row: x}, null]}}` for "is set" checks.
- Boolean fields stored in JSON come out as `true`/`false`. `{eq: [{row: finalized}, true]}` works.

### Validation

Pydantic validates the AST shape on table create/update. Invalid expressions reject with 422 at the boundary — never run an unvalidated AST. The validator covers:

- Operator shape (right number / type of operands)
- Reference paths use only known top-level columns or are valid JSONB paths
- `call` targets are in the function allow-list
- Literal lists for `in` are non-empty

## How execution works

Two paths from the same AST.

### Path 1: per-row decision

For inserts, updates, deletes, and per-message websocket filtering, the evaluator takes a single row and the caller and returns boolean. Pure function:

```python
def evaluate(expr: Expr, row: dict, user: Principal) -> bool: ...
```

The row dict is built once at the call site: top-level columns merged with `data` (top-level wins on key collision; `data` doesn't contain reserved keys per the existing schema).

For inserts: there is no row yet. The "candidate row" is the request body's `data` plus the caller's stamped `created_by`/`updated_by`/`organization_id`. Insert-time policies are checked against the candidate. This catches "user tries to insert a row claiming someone else as `manager_user_id`" — if the policy says `eq: [{row: user_id}, {user: user_id}]` for inserts, the user can only create rows attributed to themselves.

### Path 2: query pushdown

For list / query operations, the evaluator compiles to a SQL `WHERE` fragment that filters the documents table at the DB. This is non-negotiable for correctness: a 100k-row table cannot list every row to Python and filter in memory.

Compilation is mechanical:

| AST | SQL |
|---|---|
| `{eq: [{row: x}, "v"]}` | `data->>'x' = 'v'` |
| `{eq: [{row: x}, {user: user_id}]}` | `data->>'x' = '<resolved-uuid>'` (parameterized) |
| `{eq: [{row: organization_id}, ...]}` | `organization_id = '<uuid>'` (column, not JSONB) |
| `{and: [A, B]}` | `(<A>) AND (<B>)` |
| `{or: [A, B]}` | `(<A>) OR (<B>)` |
| `{not: A}` | `NOT (<A>)` |
| `{in: [{row: x}, ["a","b"]]}` | `data->>'x' = ANY(ARRAY['a','b'])` |
| `{is_null: {row: x}}` | `data->>'x' IS NULL` |
| `{is_null: {row: organization_id}}` | `organization_id IS NULL` (column form) |
| `{call: has_role, args: ["mgr"]}` | `TRUE` or `FALSE` (resolved at request time from `user.role_names`) |
| `{user: is_platform_admin}` in any operand | resolved at request time, becomes `TRUE`/`FALSE` literal |

Multiple `read` policies combine with `OR`: the final WHERE is `(<policy1>) OR (<policy2>) OR ...`.

The compiler returns a SQLAlchemy `BinaryExpression`-or-equivalent that the existing `DocumentRepository.list/query` methods AND together with their existing filters.

User-side facts (`user.role_ids`, `is_platform_admin`, etc.) are resolved at compile time, not at SQL execution time. This means: the SQL has no joins to user tables; it has parameterized literals derived from the principal. Cheap, indexable, no auxiliary queries during list.

## Resolution rules

For action `A` against row `R` (or candidate row, on insert) by caller `C`:

1. If `policies` is empty or omitted → deny (default).
2. For each rule in `policies` whose `actions` includes `A`:
   - If `when` is omitted, the rule allows.
   - Else evaluate `when(R, C)`. If true, the rule allows.
3. If any rule allows, the action is allowed. Otherwise denied.

Admin bypass is **not** a built-in — the spec example above includes an explicit `admin_bypass` rule. The evaluator has no implicit "if user.is_platform_admin: return True" branch; admins are subject to the same `policies` list as everyone else.

To preserve the "admins can do anything" default, every table is **seeded** with an `admin_bypass` rule when it's created with no policies set. The seeded rule is an ordinary entry in the `policies` list — visible in the editor, exported in the manifest, editable via PATCH or CLI. An org that needs stricter audit constraints (e.g., "even superusers cannot update rows where status=archived") can edit or remove it, and the change takes effect via the normal policy evaluation path.

Two consequences worth naming:

- A table with `policies: []` (explicitly empty) denies all access including admins. To restore admin access, add the rule back. This is intentional — "I deleted my admin bypass" should not be a silent no-op.
- The seed only fires when no policies are set. Once a table has any policy, the seed never re-fires. Removing the bypass is durable.

Tables created via UI / CLI / manifest get the seed automatically. Tables created in tests or fixtures get the seed unless they pass an explicit `policies` field.

## Per-action notes

| Action | Path | Notes |
|---|---|---|
| `read` (list/query) | Pushdown | `OR` across all read-allowing rules |
| `read` (single doc by id) | Per-row | Same set of rules, evaluated against the loaded doc |
| `create` | Per-row (candidate) | Evaluator gets the candidate row; no DB row exists yet |
| `update` | Per-row | Evaluator runs against the **pre-update** row state. The new state isn't checked here — if a workflow validates business rules (e.g., can't unfinalize), that's a separate layer. |
| `delete` | Per-row | Evaluator runs against the loaded row |
| Websocket subscribe | Per-row at handshake (test row = empty) + per-message at fanout (test row = the changed row) | Mirrors the existing creator-filter pattern, generalized |

The "update checks pre-update state" choice is intentional. If the policy is `not: {eq: [{row: finalized}, true]}` and the row is currently `finalized=false`, the user can submit an update that sets `finalized=true`. This matches Firestore semantics. To prevent state transitions ("can't move from finalized=true to finalized=false"), an app must use a workflow.

## SDK surface alignment

The REST + SDK methods established on the in-flight branch are kept, with one realignment to match Supabase's "arrays as first-class" batch convention:

| Method | Signature | Notes |
|---|---|---|
| `get(table, id)` | `→ DocumentPublic \| null` | 404/403 collapsed to null; same as today |
| `insert(table, data \| data[])` | `→ DocumentPublic \| DocumentPublic[]` | Single-or-array; was `insert` + `insert_batch`, merged |
| `upsert(table, item \| item[])` | `→ DocumentPublic \| DocumentPublic[]` | Same merge |
| `update(table, id, data)` | `→ DocumentPublic \| null` | By-id only — no filter-driven update; if the caller needs multi-row mutation, they `query` then loop |
| `delete(table, id \| id[])` | `→ boolean \| {deleted: number}` | Single-or-array |
| `query(table, q?)` | `→ DocumentListResponse` | Filters via JSON AST, optional sort/limit/offset |
| `count(table)` | `→ number` | Convenience wrapper around the count endpoint |
| `subscribe(tableId, filter, onEvent)` | `→ () => void` | Lower-level non-React primitive; `useTable` builds on it |

What changed from the in-flight branch:

- `insert_batch`, `upsert_batch`, `delete_batch` are removed. The single methods accept arrays.
- Single-input → single-output, array-input → array-output. Predictable for callers.

What didn't change:

- Filter-driven update/delete (Supabase-style) is **not** added. We stay by-id for mutations because Postgres + PostgREST gives Supabase its filter-driven shape cheaply; for a JSONB document store, by-id is the simpler primitive and `query` already covers "find by predicate."
- `count` stays as a method (vs. Supabase's count-as-flag-on-select). Less ceremony for the common "how many rows can I see" question.
- Method names track Firestore/Convex conventions. We don't go all-in on PostgREST.

### Batch behavior under policies

When `insert([row1, row2, row3])` is called:

1. The server validates the batch shape (Pydantic).
2. For each row, the server builds the candidate row and runs the `create` policies.
3. If ALL rows pass, the server inserts them in a single transaction and returns `[doc1, doc2, doc3]`.
4. If ANY row fails, the server inserts NONE and returns 403 with a body identifying the row index that failed but **not** the policy name (denials must not leak policy details to callers; a malicious caller could otherwise probe policies row-by-row).

Same pattern for `upsert(array)` and `delete(array)`.

The "all-or-nothing" choice is deliberate. The in-flight branch's `*_batch` returned `{inserted, errors[]}` (partial success). Under policies, partial success creates a probing surface. Transactional all-or-nothing is the correct default; callers who genuinely want partial success can submit single calls in a loop and decide what to do per-row.

## Data shape

```python
# api/src/models/contracts/tables.py
class Expr(RootModel[dict]): ...   # JSON AST validated by a custom validator

class Policy(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str | None = None
    actions: list[Literal["read", "create", "update", "delete"]] = Field(min_length=1)
    when: Expr | None = None  # None = always-true (rule fires for any row)

class TablePolicies(BaseModel):
    policies: list[Policy] = Field(default_factory=list)
```

Stored in the existing `Table.access` JSONB column. A non-empty `policies` list completely supersedes the old shape — the column is the policies block, full stop.

The OpenAPI schema names the field `policies` on `TableCreate`/`TableUpdate`/`TablePublic`. The DB column stays named `access` for migration cleanliness; the contract field name communicates the new model.

## Migration from current branch

The plan is to revert the contract / UI / handler / test commits while keeping the column migration intact. Net result: **a single migration ships** (the original `Table.access` JSONB column add) and the multi-role data migration (`20260429b_migrate_table_access_role_to_roles.py`) is deleted before merge — there is no production data with the old shape, so no migration is needed for the rebuild.

Kept from the current branch:
- Migration that adds `Table.access` JSONB column — reused unchanged. Column name stays `access` for migration cleanliness; the contract field name in the API is `policies`.
- ORM stub for the `access` column on `Table`
- Manifest scaffolding (the round-trip plumbing — the inner shape changes)
- Web SDK structure (`tables.{get, insert, update, upsert, delete, query, count, *_batch, subscribe}` — surface unchanged)
- Websocket `table:` channel + per-message filter scaffolding (the filter logic generalizes)
- REST endpoints, batch endpoints (server-side; merged with singles on the SDK surface), CSRF wiring
- Platform-scope SDK injection (`tables` exported to apps; `useTable` replaces the old `useTableSubscription` export)
- Workflow SDK auto-attribution (`created_by`/`updated_by` resolved from execution context)
- E2E test infrastructure: `alice_user` / `bob_user` fixtures, the websocket fixture pattern, the Playwright app fixture for tables

Reset and rebuilt:
- `TableAccessChecker` → `evaluate_policy` (pure function over expressions)
- The handler integration in `api/src/routers/tables.py` — the call shape stays similar (load table, check, optionally compile read filter), but it calls the new evaluator and the new SQL compiler
- Pydantic contract: `TableAccess` family deleted, `TablePolicies` / `Policy` / `Expr` added
- Manifest models for the new shape
- The CLI `--access` flag — renamed `--policies`, same JSON-or-@file mechanics
- The admin editor UI — rebuilt around the policy list with Monaco
- All tests for the access matrix
- The data migration `20260429b_migrate_table_access_role_to_roles.py` — deleted

The reset preserves the SDK, websocket, batch endpoints, and manifest infrastructure. The contract layer and policy editor are the only meaningful rewrites.

## Admin editor UI (v1)

The current `TableAccessEditor` (compact grid) is replaced by a **policy list editor**. Each policy is a row with:

- Name (string input)
- Description (optional, smaller input)
- Actions checkboxes (Read / Create / Update / Delete)
- A "When" expression editor — a **Monaco** editor in `json` mode, schema-driven (autocomplete + inline validation derived from the `Expr` Pydantic schema)
- A delete button per row
- An "Add policy" button at the bottom that inserts a stub: `{name: "new_policy", actions: ["read"], when: null}`

The Monaco editor uses the same JSON Schema we already publish via OpenAPI for the `Expr` model. That gives autocomplete for operator keys (`and`, `or`, `eq`, `is_null`, etc.), the `row.<field>` and `user.<field>` reference shapes, and the `call` target list (only registered functions). Inline validation runs in two layers: client-side via the schema (instant feedback while typing) and server-side via Pydantic on save (the source of truth, catches anything the schema can't express).

Helpers around the editor:

- A "Templates" dropdown that inserts common patterns as starting points (own-row, own-org, role-gated, admin-bypass)
- A "Reference" link that opens a side panel listing every `row.*` / `user.*` reference and every registered function with arg types

This is the v1 surface. A visual / drag-and-drop builder is a separate piece of work and benefits from seeing what users actually configure in production before being designed.

## CLI

`bifrost tables create --policies <json-or-@file>` and `bifrost tables update --policies <json-or-@file>`. Replaces `--access`. Same JSON-or-@file mechanic.

`bifrost tables get` shows the policy list as `name (actions): summary` lines, with the `when` expression formatted as compact pseudocode.

## Workflow SDK

No surface change. Workflows continue to:
- Auto-resolve `created_by` / `updated_by` from `context.user_id`
- Call `tables.{insert,update,upsert,query,delete}` etc.

The policy evaluator runs server-side regardless of caller (browser SDK or workflow). Workflows hit the same REST endpoints; the same evaluator decides. A workflow running as the user ID of the original requester gets the same permissions that user would have — no escalation by virtue of being a workflow.

If an app/workflow needs to bypass policy (e.g., a backfill workflow), it must run as a platform-admin user, and the table must have an `admin_bypass`-style rule. There is no separate "system" caller class; this keeps audit and behavior aligned.

## Subscriptions

The websocket `table:{id}` channel exists already (per the in-flight branch). Two extensions are needed for the policy model: **visibility-change fanout** and **server-side query filtering**.

### Subscribe protocol

The client may pass an optional `filter` expression at subscribe time. The expression is the same JSON AST as policies (same operators, same `row.*` references, same validator). Same registered functions, including `has_role`.

```jsonc
// Client → server
{ "type": "subscribe", "channels": [
  { "name": "table:00000000-0000-0000-0000-000000000000",
    "filter": { "eq": [{ "row": "user_id" }, { "user": "user_id" }] }
  }
] }
```

If `filter` is omitted, the subscription receives every change the policy lets the user see.

The server validates the filter at subscribe time. Invalid expressions return an `error` ack and the channel is not subscribed.

### Visibility-change fanout

When a row changes, the publisher has both the **pre-mutation** state and the **post-mutation** state (for update events) or one of them (for insert/delete). For each subscriber on that table, the server computes:

```
old_visible = policy_read(old_row, user) AND filter(old_row, user)   // false if no old_row
new_visible = policy_read(new_row, user) AND filter(new_row, user)   // false if no new_row
```

And emits exactly one of:

| `old_visible` | `new_visible` | Emit | Body |
|---|---|---|---|
| F | F | nothing | — |
| F | T | `insert` | full new row |
| T | F | `delete` | row id only |
| T | T | `update` | full new row |

This means a row that mutates from "I can't see it" to "I can see it" surfaces as an insert in the client, even though it's not a real DB insert. Same the other way around: a row that mutates out of visibility surfaces as a delete. App code never has to think about this; the hook just receives `insert`/`update`/`delete` and reconciles its local list.

`policy_read` here is the OR of all `read`-allowing policies, evaluated per-row by the same evaluator the REST path uses.

### Initial snapshot

The hook (`useTable`) is responsible for the snapshot. On mount it does a `tables.query(name, query)` REST call to fetch the current matching rows, then opens the subscription with the same filter. The REST `query` and the subscription `filter` use the same expression shape, so they return / fanout the same set. This avoids designing a "fetch-and-subscribe" combined endpoint for v1.

There is a tiny race window: rows that change between the snapshot and the subscription open won't fire events for that change. The hook handles this by re-querying on websocket reconnect (a no-op in the steady state, a recovery path on flap). Out of scope for v1: full at-least-once semantics with sequence numbers.

### Re-evaluation on policy edit

When a table's `policies` are updated, the publisher emits a `table_access_changed` event on the channel. The connection re-checks every active subscription on that table:

```
for each active subscription:
    can_still_subscribe = any read-allowing policy matches against
                          a probe row (the table-id row only)
    if not can_still_subscribe:
        emit subscription_revoked, close subscription
```

The probe-row check is conservative: it asks "could this user receive ANY message on this table now?" If the new policies are row-data-aware (e.g., creator-only) the probe-row check returns true (some rows could match) and the subscription stays open. The per-message filter then drops irrelevant rows individually. This matches the existing creator-filter behavior in the in-flight branch — the policy version generalizes the same logic.

### SDK surface

Only **one** React-level entry point for live table data. `useTable` is the primary surface; `useTableSubscription` from the in-flight branch is removed before merge.

```ts
useTable(name: string, query?: { where?: Expr; limit?: number; offset?: number }): {
  rows: Row[]
  loading: boolean
  error: Error | null
}
```

The hook:
1. Fetches the initial snapshot via `tables.query(name, query)`
2. Opens a websocket subscription with `query.where` as the server-side filter
3. Reconciles insert/update/delete events from the visibility-change fanout into a local Map keyed by row id
4. Returns reactive `rows` (sorted by `updated_at desc` by default; future enhancement for explicit sort), `loading`, `error`

For the single-row case, `useTable` with an `eq[row.id, ...]` filter and `[0]` works fine:

```ts
const { rows, loading } = useTable("reviews", { where: { eq: [{ row: "id" }, reviewId] } });
const review = rows[0] ?? null;
```

A dedicated single-row hook is not part of v1. We follow the Firestore/Supabase/Convex pattern of one query surface; apps that want a helper can wrap `useTable` themselves.

The lower-level non-React primitive stays available:

```ts
tables.subscribe(
  tableId: string,
  filter: Expr | null,
  onEvent: (evt: TableChangeEvent) => void,
): () => void  // unsubscribe
```

Useful for:
- Non-component code (workers, services, ad-hoc subscriptions outside React lifecycle)
- The rare "raw events" use case (analytics counters, toast on someone else's change) — call from a `useEffect`

`tables.subscribe` is what `useTable` is built on. Apps that need raw events can use it directly; apps that need a reactive list use `useTable`.

### What's deferred

- Pagination across the websocket boundary (re-querying when the user pages). For v1, `useTable` re-runs `tables.query` when offset/limit change.
- At-least-once delivery / message replay after reconnect. For v1, reconnect = re-query.
- Subscriptions to multiple tables joined together. Out of scope.

## Web SDK

No surface change to the REST methods on `client/src/lib/app-sdk/tables.ts`. Same `get/insert/update/upsert/delete/query/count/*_batch` signatures and return types. Behavior changes only in *what gets allowed*: REST calls return whatever the policy evaluator allows.

The `subscribe()` method gains an optional `filter: Expr | null` parameter (forwarded to the websocket protocol).

The `useTableSubscription` hook from the in-flight branch is **removed** before merge and replaced by `useTable`. Apps that need raw event streams call `tables.subscribe` directly from a `useEffect`. This keeps the React surface to a single `useTable` hook — the same shape every comparable provider (Firestore `onSnapshot`, Supabase `useQuery`, Convex `useQuery`, PowerSync `usePowerSyncWatchedQuery`) settled on.

## Manifest round-trip

`ManifestTable.policies: list[ManifestPolicy] | None`. Each `ManifestPolicy` mirrors `Policy` 1:1. Role-name rewrite for `has_role` arguments: in portable export, role UUIDs in `has_role` `args` are rewritten to role names; the inverse rewrite happens at import. Table-level `policies` is part of the portable artifact — sharing across environments is a goal.

## Testing strategy

Three test surfaces, in this order:

1. **Pure evaluator unit tests** (60+ cases): every operator, edge cases (null propagation, type coercion, short-circuit), realistic policies (own-row, own-org, role-gated, manager-reads-reports, finalized-state, combinations).

2. **SQL pushdown unit tests** (40+ cases): every operator's SQL output, parameterization, role-name resolution at compile time, plus a "round-trip" test that runs the same policy through both paths against the same fixtures and asserts the results match.

3. **REST + websocket e2e** (20+ cases, mirrors the access-matrix tests but expanded): admin bypass (seeded rule + custom edit + deletion), own-org filter, manager visibility (with `manager_user_id` denormalized into rows), state-locked guard on update, websocket subscription filtering with the four-way visibility-change fanout (insert/update/delete/visibility-gain/visibility-loss), policy-edit revoking an active subscription, server-side filter validation.

Plus the existing Playwright app-fixture specs continue to work — they exercise the SDK end-to-end. We add a spec covering a multi-policy scenario (two roles + state-aware predicate + denormalized manager field) and a spec exercising `useTable` end-to-end (initial snapshot + insert + update + delete + visibility-gain via row mutation).

## Open questions

- **`call` extensibility**: only `has_role` for v1. If we add `manages`-style functions later, they need either a registered relation table (e.g., `manager_relationships(employee_user_id, manager_user_id)`) with explicit pushdown rules, or a flat denormalized field on the row. Deferred.
- **Performance ceiling**: a single table with 50 read policies × OR-fanout against a 1M-row JSONB table will not be fast. We assume realistic policy counts (1-10 per table). If a user manages to hit pathological complexity, they'll see slow queries; we'll add a complexity validator only if it becomes a real problem.
- **Audit logging**: not in scope for v1. Each denial returns 403 with the policy name that came closest (debug aid). True per-row audit logs are a separate feature.
- **Field-level access** ("manager can read row but not the `salary` field"): not in scope. The shape is broader than what we ship; if needed later, we'd add a `redact` block per policy listing fields to null-out in responses. Designed for, not built.

## What this isn't

- Not a general-purpose rules engine. Thirteen operators by design.
- Not a workflow replacement. State-transition guards (can't move a row from done→open after the fact) belong in workflows. Pre-update evaluation means a user CAN cause their row to enter a locked state (because the row was unlocked when they touched it); they CANNOT touch it again afterwards.
- Not free. The admin editor is Monaco with schema-driven autocomplete; a visual / drag-and-drop builder is a future feature once we see what users actually configure.
- Not column-level. Row-level only.

## Scope

A single implementation plan, sized at 4-6 working days. The migration is small (column already exists), the contract change is well-bounded, the integration points are the same handlers we already touched. Three pieces dominate the cost:

- The SQL compiler + its tests (~1.5 days)
- The visibility-change websocket fanout + the two new hooks + e2e (~1.5 days)
- The Monaco editor with schema-driven autocomplete + templates + reference panel (~1 day)

Everything else (Pydantic contract, evaluator, REST integration, manifest, CLI, llm.txt, skill update, branch reset) fits in the remaining 1-2 days.
