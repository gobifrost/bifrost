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

Twelve total. Every operator must have a SQL form (this is a constraint, not a guideline).

| Op | Shape | Semantics |
|---|---|---|
| `and` | `{ and: [Expr, ...] }` | Boolean AND, short-circuit |
| `or` | `{ or: [Expr, ...] }` | Boolean OR, short-circuit |
| `not` | `{ not: Expr }` | Boolean NOT |
| `eq` | `{ eq: [a, b] }` | Equality (NULL == NULL is false; matches SQL semantics) |
| `neq` | `{ neq: [a, b] }` | Inequality |
| `lt` / `lte` / `gt` / `gte` | `{ lt: [a, b] }` | Comparison (numbers, strings, ISO dates) |
| `in` | `{ in: [a, [v1, v2, ...]] }` | Set membership; right side is a literal list |
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

One function for v1: `has_role(name_or_uuid: str) -> bool`. Implemented as membership in `user.role_ids` or `user.role_names`.

Function calls are intentionally restricted to a registered allow-list. Adding `manages()` or anything that touches the DB during evaluation requires an explicit design — for now, denormalize relationships into row fields instead.

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

Admin bypass is **not** a built-in — the spec example above includes an explicit `admin_bypass` rule. This is intentional: by making it visible in policies, an org can choose to forbid even superusers from updating finalized records, or whatever audit constraint they need. The migration that ships with this feature seeds an admin-bypass rule on every table with no existing policies; tables created via UI/CLI/manifest get the same default in their generated stub.

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

Three commits on the current branch encode contract + UI for the old `TableAccess` shape (`939982b8`, `0358aa0f`, `61d34287`). All of them are reverted before this feature lands.

Kept from the current branch:
- Migration `5024a64c` (adds `Table.access` JSONB column) — reused unchanged
- ORM, manifest scaffolding, web SDK structure, websocket `table:` channel, REST endpoints, batch endpoints, CSRF wiring, platform-scope SDK injection
- Workflow SDK auto-attribution (`38c9d17e`)
- E2E test infrastructure (the `alice_user`/`bob_user` fixtures, the websocket fixture pattern, the Playwright app fixture for tables)

Reset and rebuilt:
- `TableAccessChecker` → `evaluate_policy` (pure function over expressions)
- The handler integration in `api/src/routers/tables.py` — the call shape stays similar (load table, check, optionally compile read filter), but it calls a new evaluator
- Pydantic contract (`TableAccess` family removed, `TablePolicies`/`Policy`/`Expr` added)
- Manifest serialization
- The CLI `--access` flag (renamed `--policies`, same JSON-or-@file mechanics)
- The admin editor UI (rebuilt around a policy list — see UI section)
- All tests for the access matrix

The reset preserves the SDK, websocket, batch endpoints, and manifest plumbing. We don't lose the week's work; we replace the contract layer.

## Admin editor UI (v1)

The current `TableAccessEditor` (compact grid) is replaced by a **policy list editor**. Each policy is a row with:

- Name (string input)
- Description (optional, smaller input)
- Actions checkboxes (Read / Create / Update / Delete)
- A "When" field — for v1, **a JSON textarea with live AST validation** (uses the same Pydantic validator). Below it, a small read-only preview pane that pretty-prints the expression in pseudocode for sanity.
- A delete button per row
- An "Add policy" button at the bottom that inserts a stub: `{name: "new_policy", actions: ["read"], when: null}`

Two helpers below the textarea:
- A "Templates" dropdown that inserts common patterns (own-row, own-org, role-gated, admin-bypass) as starting points
- A "Reference" link that opens a dialog showing available `row.*` and `user.*` references and the `has_role()` function

This is intentionally a **textarea, not a visual builder**, for v1. Visual expression builders are a separate piece of work and the user research for what to build needs the textarea version in production first to inform design.

## CLI

`bifrost tables create --policies <json-or-@file>` and `bifrost tables update --policies <json-or-@file>`. Replaces `--access`. Same JSON-or-@file mechanic.

`bifrost tables get` shows the policy list as `name (actions): summary` lines, with the `when` expression formatted as compact pseudocode.

## Workflow SDK

No surface change. Workflows continue to:
- Auto-resolve `created_by` / `updated_by` from `context.user_id`
- Call `tables.{insert,update,upsert,query,delete}` etc.

The policy evaluator runs server-side regardless of caller (browser SDK or workflow). Workflows hit the same REST endpoints; the same evaluator decides. A workflow running as the user ID of the original requester gets the same permissions that user would have — no escalation by virtue of being a workflow.

If an app/workflow needs to bypass policy (e.g., a backfill workflow), it must run as a platform-admin user, and the table must have an `admin_bypass`-style rule. There is no separate "system" caller class; this keeps audit and behavior aligned.

## Web SDK

No surface change to `client/src/lib/app-sdk/tables.ts`. Same methods, same return types. Behavior changes only in *what gets allowed*: the SDK calls return whatever the policy evaluator allows.

The `subscribe()` method already routes through the websocket layer; the per-message filter (today checks creator-filter) becomes "evaluate the read policies against the changed row." Existing logic generalizes naturally.

## Manifest round-trip

`ManifestTable.policies: list[ManifestPolicy] | None`. Each `ManifestPolicy` mirrors `Policy` 1:1. Role-name rewrite for `has_role` arguments: in portable export, role UUIDs in `has_role` `args` are rewritten to role names; the inverse rewrite happens at import. Table-level `policies` is part of the portable artifact — sharing across environments is a goal.

## Testing strategy

Three test surfaces, in this order:

1. **Pure evaluator unit tests** (60+ cases): every operator, edge cases (null propagation, type coercion, short-circuit), realistic policies (own-row, own-org, role-gated, manager-reads-reports, finalized-state, combinations).

2. **SQL pushdown unit tests** (40+ cases): every operator's SQL output, parameterization, role-name resolution at compile time, plus a "round-trip" test that runs the same policy through both paths against the same fixtures and asserts the results match.

3. **REST + websocket e2e** (15+ cases, mirrors the access-matrix tests but expanded): admin bypass, own-org filter, manager visibility (with `manager_user_id` denormalized into rows), finalized-state guard on update, websocket subscription filtering, policy-edit revoking an active subscription.

Plus the existing Playwright app-fixture specs continue to work — they exercise the SDK end-to-end. We add one spec covering a multi-policy scenario (perf-review-shaped: two roles, finalized state, denormalized manager field).

## Open questions

- **`call` extensibility**: only `has_role` for v1. If we add `manages`-style functions later, they need either a registered relation table (e.g., `manager_relationships(employee_user_id, manager_user_id)`) with explicit pushdown rules, or a flat denormalized field on the row. Deferred.
- **Performance ceiling**: a single table with 50 read policies × OR-fanout against a 1M-row JSONB table will not be fast. We assume realistic policy counts (1-10 per table). If a user manages to hit pathological complexity, they'll see slow queries; we'll add a complexity validator only if it becomes a real problem.
- **Audit logging**: not in scope for v1. Each denial returns 403 with the policy name that came closest (debug aid). True per-row audit logs are a separate feature.
- **Field-level access** ("manager can read row but not the `salary` field"): not in scope. The shape is broader than what we ship; if needed later, we'd add a `redact` block per policy listing fields to null-out in responses. Designed for, not built.

## What this isn't

- Not a general-purpose rules engine. Twelve operators by design.
- Not a workflow replacement. State-transition guards (can't move finalized=true→false) belong in workflows.
- Not free. The admin editor is a textarea; visual expression building is a future feature when we know what users actually configure.
- Not column-level. Row-level only.

## Scope

A single implementation plan. The migration is small (column already exists), the contract change is well-bounded, the integration points are the same handlers we already touched. The biggest single piece of work is the SQL compiler with its tests — about 1.5 days. Whole feature: 4-6 working days.
