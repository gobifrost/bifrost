# Custom Claims — query-resolved facts about the caller, usable in table policies

## Status

Extension to `2026-04-30-table-policies-design.md`. Additive: existing policies keep working unchanged. Targets the "user × tag × scope" access patterns (multi-campus client portals, per-project, per-tenant) that cannot be expressed today because the principal carries only fixed fields.

## Why this exists

Table policies today can reference fixed principal fields: `user.user_id`, `user.email`, `user.organization_id`, `user.role_ids`, `user.role_names`, `user.is_platform_admin`. That's enough for "own org," "own row," "has role X" — but not for the realistic case where access depends on **lists** the platform doesn't know about a priori:

- "User can read documents whose `campus_id` is in the set of campuses they're a member of."
- "User can read records whose `doc_type_id` is in the set of doc types granted to any group they belong to."
- "User can update tasks assigned to any project they're on."

Each of these requires resolving a per-user list from one or more tables, then folding that list into a policy predicate. There is no way to express the resolution today.

The minimum that makes this expressible cleanly is one new primitive: a **claim** — a named, query-resolved list (or scalar) computed for the calling user, referenceable from any policy in the same org.

This is the same conceptual move Firestore custom claims make: facts about the principal that an app defines, that policies consume as literals. Difference from Firestore: ours don't ride a JWT — they resolve server-side from Bifrost tables the app already owns.

## High-level shape

A new org-scoped resource: **Custom Claims**, edited under a new tab on the Tables admin page. Each claim is:

```yaml
- name: allowed_campus_ids
  description: Campuses the user is a member of.
  type: list             # or `scalar`
  query:
    table: user_campus_access
    where:
      eq: [{ row: user_id }, { user: user_id }]
    select: campus_id
```

Policies on any table in the same org reference claims via a new reference root, `{claims: <name>}`:

```yaml
policies:
  - name: scoped_read
    actions: [read]
    when:
      and:
        - { in: [{ row: campus_id   }, { claims: allowed_campus_ids   }] }
        - { in: [{ row: doc_type_id }, { claims: allowed_doc_type_ids }] }
```

That's it. The whole multi-campus portal access model is four lines.

## Reference root: `{claims: <name>}`

Claims get their own reference root alongside `{row: ...}` and `{user: ...}`. They are **not** namespaced under `user`. Two reasons:

1. **Collision-free.** `{user: email}` is a built-in principal field. If a claim were ever named `email`, `{user: email}` would be ambiguous. Separate roots, no overlap, validator rejects ambiguity by construction.
2. **Telegraphs semantic difference.** `{user: ...}` is a fixed fact resolved at session boundary. `{claims: ...}` is a query-resolved list with a different cost and invalidation model. Different root makes that visible in every policy that uses one.

In the YAML the field name on the storage model is `claims` (lowercase, plural). The user-facing label is **Custom Claims**.

## Data shape

```python
# api/src/models/contracts/claims.py
class ClaimQuery(BaseModel):
    table: str                              # source table name (org-scoped lookup)
    where: Expr | None = None               # same Expr AST policies use
    select: str                             # column or JSON field on the source table
    # Future: limit, distinct — deferred until needed.

class CustomClaim(BaseModel):
    name: str = Field(min_length=1, max_length=100, pattern=r"^[a-z][a-z0-9_]*$")
    description: str | None = None
    type: Literal["list", "scalar"] = "list"
    query: ClaimQuery
```

Storage: new `custom_claims` table, `(organization_id, name)` unique. Rationale for a dedicated table vs. a JSONB column on `organizations`:

- Claims are inspected by name from policy validators across many table-save operations. A `(org_id, name)` index is faster than scanning a JSONB blob.
- The admin UI lists / filters / paginates claims; a real table is easier.
- Manifest round-trip per claim is cleaner with discrete rows.

## Inside `query.where` and `query.select`

The same Expr AST that powers policies. Two reference roots are valid inside a claim's `where`:

| Reference | Resolves to |
|---|---|
| `{ row: <field> }` | The row of the **source table** (`user_campus_access`, in the example). NOT the row of the table where the claim is referenced. |
| `{ user: <field> }` | The calling user's principal — same fixed fields as in policies. |

`{ claims: <other_claim> }` inside a claim's `where` is **allowed**, with cycle detection (see "Validation"). This lets one claim depend on another (`allowed_doc_type_ids` filtering on `{ claims: allowed_group_ids }`).

`select` is a string — either a top-level column on the source table or a JSON path within `data` (`metadata.priority`). Returns a list of values (one per matching row) for `type: list`, or a single value for `type: scalar` (validator enforces the source query returns ≤1 row in that case at evaluation time, not at save time — depending on the data is unavoidable here).

## Resolution: pre-resolved per request

**Not at login.** Login is hot path and most claims won't be used in any given session. Login-time resolution also doesn't gain us anything: we don't sign a JWT with the claims baked in, we're running Python with a DB connection.

**Lazy, request-scoped:**

1. Before policy evaluation for a request, the platform pre-resolves every claim referenced by the table policies. Each referenced claim runs its `query` against the claim's org-scoped source table, plucks `claim.query.select`, and returns the list or scalar.
2. Results are cached on the request-scoped principal as `principal.claims[foo]`.
3. Subsequent references in the same request hit the cache.
4. Next request: fresh resolution.

Cost: one extra source-table query per *unique claim referenced* per request. A policy that references three claims pays three queries before evaluation, zero thereafter for that request.

**Websocket cache:** not shipped in v1. REST document reads/writes/batches pre-resolve claims at the router boundary. Subscription-time cache invalidation remains deferred.

**Resolution context:** the claim's `where` expression is evaluated with the **calling user's principal**, but the source-table read is an internal org-scoped lookup, not a nested policy-gated `tables.query`. This avoids recursive policy dependencies while still letting the claim filter on `{user: ...}` and other pre-resolved claims.

If the user can't see any rows in the source table that match the `where`, the claim resolves to `[]` (for `list`) or `null` (for `scalar`). The policy that references the empty list naturally denies (`{in: [x, []]}` is always false).

## AST integration

Two narrow changes to the existing expression model.

**1. New reference root `{claims: <name>}`.** Validator at policy-save time checks:
- The name exists in the org's `custom_claims` registry.
- The referencing context allows it (claim resolution itself can reference other claims; see cycle check).

Resolution at request time produces a list (or scalar) literal. The SQL compiler folds it the same way it folds `user.role_ids` — as parameterized literals, no joins to user tables.

**2. Relax `in`'s RHS.** Today `{ in: [a, [v1, v2, ...]] }` requires a literal list. Extend to also accept `{ claims: <list-typed-claim> }`:

```yaml
{ in: [{ row: campus_id }, { claims: allowed_campus_ids }] }
```

SQL: `data->>'campus_id' = ANY(ARRAY['c1','c2',...])` where the array is the resolved claim. Indexable, parameterized, identical to the existing literal-list case once the claim is resolved.

Per-row evaluator: same — checks membership against the in-memory list.

**Nothing else changes.** `eq`/`neq`/`lt`/etc. with `{claims: <scalar-claim>}` already work — a scalar claim resolves to a value and compares like any other. `intersects` is deferred until there's a concrete second use case beyond the portal pattern.

## Validation

At claim save:

- Name matches `^[a-z][a-z0-9_]*$`, unique within org.
- `query.table` resolves to a table in the same org.
- `query.where` validates as an Expr-shaped AST.
- `query.select` is a column name or JSON path on the source table. The platform extracts it at resolution time; missing paths resolve to `null`.
- No cycle: `claims_referenced(claim) ∩ {claim.name} = ∅` transitively. Implementation: build a directed graph of claim-to-claim references for the org, run a DFS at save time, reject save with 422 if the new/edited claim would introduce a cycle.

At policy save (table policy referencing a claim):

- Every `{ claims: X }` in the policy resolves to an existing claim in the org.
- Type-aware claim usage validation is deferred. At runtime, list claims work naturally on the RHS of `in`, and scalar claims compare like ordinary values.

At claim delete:

- If any policy in the org references the claim, reject with `409 Conflict` listing the dependent tables. Force the admin to remove the references first. (Cascade-delete is wrong here — silently breaking access rules is worse than a noisy refusal.)

## Org scoping

Claims are **org-scoped**. Same boundary as tables, workflows, forms, policies.

- A policy on a table in org X can only reference claims defined in org X. The validator enforces this.
- Custom Claim REST mutations are restricted to superusers and require the caller to have org context. Regular org users cannot create/edit/delete claims.
- No global / platform-wide claims for v1. If a future feature needs "every org has `allowed_internal_doc_ids`," we'd add a `scope: platform` field. Not needed now.

This lets a client org say "these are our claims" alongside its tables and policies — same shape as every other Bifrost resource.

## Admin UI

A new **Custom Claims** tab on the Tables admin page.

The tab shows:

- A list of existing claims for the current org: name, type, source table, and selected field.
- An "Add claim" button.
- Per-row edit / delete.

The editor (modal or side panel — match the existing table policy editor's pattern):

- **Name** field (validated against the regex).
- **Description** field (optional).
- **Type** selector: list / scalar.
- **Query** editor — Monaco-based, JSON/YAML toggleable (see "Reusable components"). The shared editor accepts a schema prop, but Monaco schema registration / autocomplete is still deferred.
- **Help slide-out** (see "Reusable components") with examples for common patterns: own-membership lookup, group-fanout lookup, scalar lookup.

### Reusable components (extracted from the existing policy editor)

The existing `TablePolicyEditor` already has two patterns we want here and elsewhere. Both should be **extracted as shared components** before / as part of this work:

**`<JsonYamlEditor>`** — Monaco editor with a JSON/YAML toggle and schema-driven validation. Props: `schema`, `value`, `onChange`, `defaultFormat`. Used by:
- `TablePolicyEditor` (today, in-line)
- `CustomClaimEditor` (this spec)
- Future schema-driven config editors (workflow inputs, form schemas, app config blocks, etc.)

**`<HelpSlideout>`** — help-icon button that opens a slide-out panel with documentation (markdown or rendered children). Props: `title`, `children` (or `content`), open state. Used by:
- `TablePolicyEditor` reference panel (today)
- `CustomClaimEditor` examples (this spec)
- Any future admin form where inline reference docs help.

The extraction is ~half a day of work on top of the rest. It pays back immediately and on every future admin editor. Spec calls this out explicitly so the team doesn't build a parallel one-off.

## Manifest round-trip

`ManifestCustomClaim` mirrors `CustomClaim` 1:1, in a new file `.bifrost/claims.yaml`:

```yaml
claims:
  5e313f6a-01f0-4e46-b1d7-3a1232d78c22:
    id: 5e313f6a-01f0-4e46-b1d7-3a1232d78c22
    name: allowed_campus_ids
    description: Campuses the user is a member of.
    organization_id: 0fce3cc0-c9a0-45e6-b8f0-a9948a89e033
    type: list
    query:
      table: user_campus_access
      where:
        eq: [{ row: user_id }, { user: user_id }]
      select: campus_id
```

Cross-env portability:

- `query.table` is referenced by **name**, not UUID. Already correct — table names are stable across envs.
- Org references in the claim's persistence (`organization_id`) are scrubbed on export (same pattern as table policies).
- On import, the `organization_id` is set from the target org. If the source table doesn't exist in the target env, import fails fast with a clear error.

`manifest_import.py` follows the same non-destructive upsert pattern as integrations: query existing by `(org_id, name)`, update matching, insert new, delete removed.

## CLI

`bifrost claims create|update|delete|get|list`. JSON-or-@file for `query`. Same mechanics as `bifrost tables --policies`.

`bifrost claims get <name>` returns the same JSON shape as the REST endpoint.

## MCP

Thin HTTP wrappers in `api/src/services/mcp_server/tools/claims.py` per the existing MCP discipline (`api/src/services/mcp_server/tools/_http_bridge.py` pattern). No direct ORM access, no repository imports. Covered by the existing `test_mcp_thin_wrapper.py` enforcement test.

## Testing strategy

1. **Pure resolver unit tests** (~20 cases): each claim type, with/without `where`, list and scalar, empty result, claim-references-claim, cycle rejection at save.
2. **AST integration unit tests** (~15 cases): `in` with `{claims: list_claim}` RHS, `eq` with `{claims: scalar_claim}`, validator rejects wrong type, validator rejects unknown claim name, SQL pushdown emits the expected `= ANY(ARRAY[...])`.
3. **Resolution path unit tests**: pre-resolution at the REST boundary, cache hit on second reference in same request, fresh resolution on next request.
4. **REST e2e**: CRUD, unknown source table rejection, scoped-read with claims, deleting a referenced claim is rejected, editing a claim's `query` is reflected on next request.
5. **Manifest round-trip** (1 case): create claims in env A → export → import to env B → claims resolve identically against equivalent membership data.

## Migration

Additive. No existing policies break. New `custom_claims` table added via a normal Alembic migration. The existing `Table.access` JSONB column is unchanged; only the `in` validator and the AST reference set are extended.

Tables that currently have policies referencing only `{user: ...}` and `{row: ...}` are untouched.

## What's deferred

- **`intersects` operator** — when both sides are lists. Not needed for the portal pattern (one-side-list `in` covers it). Add when there's a concrete second use case.
- **`exists_in` / subquery operator** — the spec already defers this (policies design doc, open questions). Claims don't change that calculus.
- **Real-time invalidation on membership change** — for now, claims refresh on next request (REST) or next reconnect (websocket). A future enhancement can emit `claims_invalidated` events from membership-table writes and clear the cache mid-connection.
- **Platform-scope claims** — `scope: platform` for claims that apply across all orgs. Add if/when a real use case appears.
- **Computed scalars across multiple rows** — a claim like "the user's most-senior role" (an aggregate). Today `type: scalar` works for single-row lookups; aggregates need extra primitives we don't have yet.

## Scope

~4–5 working days, broken down:

- Pydantic contract + ORM + migration + storage (~0.5d)
- REST-boundary pre-resolution + per-request cache (~1d)
- AST relaxation on `in` + new `{claims: ...}` reference root + validator (~0.5d)
- Component extraction (`<JsonYamlEditor>`, `<HelpSlideout>`) (~0.5d)
- Admin UI (Custom Claims tab + editor) (~1d)
- Manifest + CLI + MCP wrappers (~0.5d)
- Tests (resolver, AST, e2e, manifest round-trip) (~1d)

Fits in a single implementation plan. The reusable-component extraction is the only piece with cross-cutting impact; everything else is additive and well-bounded.

## What this isn't

- Not a general query layer — `query.table` is a single source table, no joins. Multi-table claims work by chaining (claim A references claim B).
- Not a workflow replacement — claims resolve facts about the user, not arbitrary computed values about the row or the world. State-transition guards still belong in workflows.
- Not real-time — request-scoped cache only. Real-time invalidation / subscription cache behavior is a separate feature.
- Not a SQL escape hatch — the only query language is the Expr AST that policies already use. No new injection surface.
