# File Policies — RLS-style policies for direct file reads/writes from apps

## Status

**Experimental. Spec, awaiting plan.**

Tracked at jackmusick/bifrost#170. Dependent on the in-flight `feat/table-access`
branch (table policies) and on the merged unified file-path resolution
(jackmusick/bifrost#155). This is a deliberate parallel to table-policies; many
sections below mirror or reference
`docs/superpowers/specs/2026-04-30-table-policies-design.md` on that branch.

This spec is being shipped knowing it represents significant additional
complexity for the platform. It is the right move because the alternative —
apps continuing to proxy file reads through workflows — caps what apps can
practically do (galleries, document viewers, attachment libraries). It is also
acknowledged as work that may exceed what an OSS project can sustainably
maintain. The design has explicit fallback positions called out in §10.

## Problem

Apps cannot read or write files directly from the browser. The CLI uses the
files endpoints under `/api/files/*` but those require `CurrentSuperuser`. To
display a file in an app, or to upload from the browser, the app currently
either (a) has the workflow engine proxy the read, adding queue + worker
round-trips, or (b) goes through the form-upload flow, which is purpose-built
for that one use case.

Tables faced the same problem and the resolution there was table-policies: a
JSON-AST policy language with an evaluator (per-row check) and a compiler
(SQL pushdown). We adopt the same skeleton for files, with a deliberately
reduced operator set for v1 and a smaller reference resolver.

## Non-goals

- **Reaching parity with table-policies' operator set in v1.** Six AST node
  types are sufficient to express Everyone / Role / Creator and combinations.
  Additional operators (`lt`, `gt`, `in`, `is_null`, etc.) are additive — they
  do not change the data shape and are added when concrete use cases land.
- **Arbitrary file metadata predicates.** Content-type, size, S3 tags, and
  custom metadata are out of v1. The `{file: ...}` reference set is small and
  deliberate. Anything beyond `created_by`, `created_at`, `path`, and
  `location` requires a sidecar-schema decision and is out of scope.
- **SQL pushdown.** Tables push predicates into Postgres. Files do not have a
  SQL surface — list operations are prefix-bound and return everything under
  the prefix. The evaluator filters per-row in Python after the list. This is
  acceptable at v1 scale (hundreds to low-thousands of files per folder) and
  is revisited if a tenant hits list latency.
- **Cross-prefix queries.** Mirroring the S3 list semantics, policies match
  on `(location, path)` and lists are prefix-bound. No `WHERE location IN
  (...)`-style queries.
- **Migration of legacy unscoped uploads.** PR #155 established that
  pre-scoped form uploads at `uploads/{form_id}/...` (no scope segment) are
  unreachable through the resolver. File policies inherit that decision;
  legacy keys remain accessible only via direct S3 (admin) or via workflows
  that hit the bucket directly.

## Dependencies

### Hard dependencies

1. **`feat/table-access` (table policies, in-flight).** Provides the policy
   engine in `api/shared/policies/`: AST models, evaluator, SQL compiler,
   function registry, probe, and the websocket subscribe path. Provides the
   admin editor pattern (Monaco JSON + templates + reference panel), the
   `useTable` hook design we mirror as `useFiles`, the manifest+CLI plumbing,
   and the role-cache.
2. **Unified file-path resolution (#155, merged).** Provides `resolve_s3_key`
   and the `(location, scope, path)` decomposition. File policies operate on
   the user-meaningful `(location, path)` identity; scope is a structural
   segment owned by the resolver and **never appears in policy ASTs**.

### Soft dependency: shared engine extraction

When file policies land, we **also** refactor `api/shared/policies/` so the
engine is domain-agnostic and the table-specific bits move to a separate
binding. This refactor is a precondition for file policies, not an aside.
See §6.

## Model

A `FilePolicy` is a row keyed by `(location, path)` plus a list of named
rules, each with `actions` and `when`:

```yaml
location: shared
path: finance
policies:
  - name: admin_bypass
    actions: [read, write, delete, list]
    when: { user: is_platform_admin }

  - name: finance_writes
    actions: [read, write, delete, list]
    when: { call: has_role, args: [finance] }

  - name: own_uploads
    actions: [read, write, delete]
    when:
      eq: [{ file: created_by }, { user: user_id }]
```

Resolution within a policy is **additive OR**: if any rule allows the action,
the action is allowed. Default deny.

Resolution across policies on different paths is **longest-prefix wins**:
when a request comes in for `(location=shared, path=finance/2026-Q1.pdf)`,
the resolver finds the policy whose `(location, path)` is the longest prefix
of `shared/finance/2026-Q1.pdf` and evaluates only that policy. There is no
inheritance or union across policies at different paths.

This matches the SharePoint / Google Drive / filesystem mental model: each
folder has its own access, and the most-specific folder's rules apply. It is
strictly simpler than the additive-across-rules model and makes the audit
question ("why does Alice have this access?") have one answer per request.

### Default deny

A new file location with no policies is workflow-only. Brand-new folders
inherit the longest-prefix policy from above; if no policy exists at any
prefix, default deny. The seeded `admin_bypass` rule is a non-removable
template that ensures admins can always edit.

### Admin bypass

Platform admins (superusers) bypass via a seeded `admin_bypass` rule on
every policy, identical to the table-policies pattern. Admin bypass is not
special-cased in the evaluator — it is just rule #1, evaluated like any
other. This keeps the evaluator pure and makes admin access auditable in
the rule list.

## Expression model

JSON ASTs. No string DSL, no parser, no injection surface.

### Operators (v1)

Six total. Every operator must have a per-row evaluator form. SQL/storage
pushdown is **not** required for files (see Non-goals).

| Op | Shape | Semantics |
|---|---|---|
| `and` | `{ and: [Expr, ...] }` | Boolean AND, short-circuit |
| `or` | `{ or: [Expr, ...] }` | Boolean OR, short-circuit |
| `not` | `{ not: Expr }` | Boolean NOT |
| `eq` | `{ eq: [a, b] }` | Equality (NULL == NULL is false) |
| `call` | `{ call: <fn>, args: [...] }` | Function call (allow-listed) |
| literal | `true \| false \| <string> \| <number> \| null` | Literal |

Operands are nested expressions, references (`{file: ...}`, `{user: ...}`),
or literals. A bare scalar in an operand position is a literal.

### Forward compatibility

Adding `neq`, `lt`, `lte`, `gt`, `gte`, `in`, `is_null` is purely additive —
the AST shape does not change, only the set of recognized node types. We
add these on demand. The first request from a real use case (e.g., "files
where `created_at > ?` for retention") motivates `gt`. Until then, six
node types is enough.

### `{user: ...}` references

Identical to table-policies. Resolves against the calling user's principal.

| Field | Type | Source |
|---|---|---|
| `user.user_id` | UUID | `ctx.user.user_id` |
| `user.email` | string | `ctx.user.email` |
| `user.organization_id` | UUID \| null | `ctx.user.organization_id` |
| `user.is_platform_admin` | bool | `ctx.user.is_superuser` |
| `user.role_ids` | list[UUID] | `ctx.user.roles` |
| `user.role_names` | list[str] | derived once per request |

### `{file: ...}` references

Deliberately small. Resolves against the file's metadata (sidecar) and the
user-meaningful `(location, path)`.

| Field | Type | Source |
|---|---|---|
| `file.created_by` | UUID \| null | `file_index` sidecar |
| `file.created_at` | ISO datetime | `file_index` sidecar |
| `file.path` | string | the user-meaningful path (no scope segment) |
| `file.location` | string | the location component |

### Functions

Identical to table-policies. v1 = `has_role`. Function calls are restricted
to the registered allow-list. The registry is shared between tables and
files (see §6).

## Storage

### `FilePolicy` ORM table

```
file_policies
├── id              UUID  PK
├── organization_id UUID  NULL  FK organizations.id   -- null = global policy
├── location        TEXT  NOT NULL
├── path            TEXT  NOT NULL                    -- user-meaningful, no scope
├── policies        JSONB NOT NULL                    -- list of rules
├── created_by      UUID  NOT NULL  FK users.id
├── created_at      TIMESTAMPTZ
└── updated_at      TIMESTAMPTZ

UNIQUE INDEX ON (organization_id, location, path)
```

`policies` is a strict-shape JSONB list of rules:

```json
[
  {"name": "admin_bypass", "actions": ["read", "write", "delete", "list"], "when": {"user": "is_platform_admin"}},
  {"name": "finance_writes", "actions": ["read", "write", "delete", "list"], "when": {"call": "has_role", "args": ["finance"]}}
]
```

Validated on write by a Pydantic model (`FilePolicyDocument`).

### `file_index` sidecar (extension)

The existing `file_index` table is extended (or a sibling table is added —
decided in plan-writing) to track:

- `created_by` (UUID, the user that wrote the file)
- `created_at` (TIMESTAMPTZ)
- `(location, scope, path)` decomposition

Every write path that lands a file in S3 must populate this row:

- Form uploads (`api/src/routers/forms.py`)
- SDK uploads (`bifrost.files.write`, `bifrost.files.write_bytes`)
- Workflow writes (`sdk.files.write`)
- Direct admin writes via the file browser

The sidecar is the source of truth for `{file: ...}` references and for
list filtering. Without it, Creator-scope policies cannot list-filter
correctly. The plan must enumerate every write path and confirm the
sidecar is populated.

### Migration

One Alembic migration creates `file_policies`. Sidecar extensions to
`file_index` are a separate migration in the same change. No backfill of
existing files (we do not retroactively guess `created_by`); existing files
without a sidecar row are accessible only to admins until they are
re-written.

## Engine: shared with tables

`api/shared/policies/` is refactored to make the engine domain-agnostic.
Per-domain bindings move to separate modules.

### Target layout

```
api/shared/policies/
├── ast.py              # Pydantic AST models. Shared.
├── evaluate.py         # Generic walker. Takes a Resolver. Shared.
├── compile.py          # Generic predicate compiler. Takes a Binding. Shared.
├── functions.py        # has_role, has_permission, ... — domain-agnostic. Shared.
├── probe.py            # Static analysis (extracted referenced fields). Shared.
└── subscription.py     # Websocket subscribe filtering. Shared at the AST level.

api/shared/table_policies.py   # RowResolver + JSONB binding for tables.
api/shared/file_policies.py    # FileResolver + (file_index) binding for files.
```

### What is genuinely shared

- **AST node models.** `Expr`, `And`, `Or`, `Not`, `Eq`, `Call`, references,
  literals. Same Pydantic types. Tables and files use the same parser.
- **Evaluator skeleton.** The walker is a pure function over the AST that
  delegates `{row: ...}` / `{file: ...}` to a `Resolver` interface. Two
  resolvers (`RowResolver`, `FileResolver`); one walker.
- **Compiler skeleton.** The walker emits predicates against a `Binding`
  that knows how to project AST references onto the underlying storage.
  Two bindings (Postgres rows + JSONB for tables; `file_index` rows for
  files); one walker.
- **Function registry.** `has_role` lives in `functions.py`. New functions
  default to the shared registry unless they are inherently domain-specific
  (e.g., `path_starts_with` for files; not in v1).

### What stays per-domain

- The resolver (how `{row: ...}` or `{file: ...}` references are resolved
  to values).
- The binding (how references project onto the underlying storage for
  query rewriting).
- The wiring into the REST handlers, the SDK adapters, and the websocket
  subscribe path.

### Sequencing

The shared-engine refactor lands as the first task of this work, *after*
table-policies merges. The diff is mostly mechanical: move table-specific
resolver code out of `evaluate.py` behind an interface; same for compiler.
Both engines (table and file) consume the refactored core thereafter.

## REST surface

The existing `/api/files/*` endpoints (today gated by `CurrentSuperuser`)
relax to `Context`. The handler resolves the `(location, path)`, looks up
the longest-prefix `FilePolicy`, hydrates the caller principal, and runs
the evaluator.

Endpoint-by-endpoint:

| Endpoint | Action | Notes |
|---|---|---|
| `POST /api/files/read` | `read` | Per-file check |
| `POST /api/files/write` | `write` | Sidecar row written or updated |
| `POST /api/files/delete` | `delete` | Sidecar row deleted |
| `POST /api/files/list` | `list` | Filter results by per-row `read` check |
| `POST /api/files/exists` | `read` | Existence-non-leak: 403 for both deny and not-found |
| `POST /api/files/signed-url` | `read` (GET) / `write` (PUT) | Single signing |
| `POST /api/files/signed-urls` (NEW) | `read` / `write` | Batch signing |

The batch signed-URL endpoint is new and is the immediate unblocker for
gallery use cases. It accepts `paths: list[str]` and returns
`[{path, url, expires_in}]` for allowed paths and `[{path, error}]` for
denied paths. One auth check, N HMACs.

## Web SDK

`client/src/lib/app-sdk/files.ts`. Mirrors the Python files SDK:

```ts
files.read(path)
files.write(path, content)
files.delete(path)
files.list(prefix)
files.exists(path)
files.signedUrl(path, method?)
files.signedUrls(paths, method?)
files.upload(path, blob)        // signed-PUT helper for browser file inputs
files.download(path)            // signed-GET helper
```

Plus a `useFiles(prefix)` hook (mirroring `useTable`) that returns a live
listing — backed by REST list + websocket invalidation events when files
land/move/delete in the prefix.

## CLI / MCP / Manifest

Per CLAUDE.md "Keeping CLI, MCP, and manifest in sync":

- **CLI** (`bifrost files policies ...`): `set`, `get`, `list`, `delete`.
  Set takes a JSON file or `--policies <inline-json>`. The exact surface is
  decided in plan-writing.
- **MCP**: `api/src/services/mcp_server/tools/files.py` follows the
  thin-HTTP-wrapper rule for any new tools (per `_http_bridge.py`). Existing
  files MCP tools, if any divergent ones exist, are reconciled separately
  per `docs/plans/2026-04-18-mcp-router-reconciliation.md`.
- **Manifest** (`api/bifrost/manifest.py`, `manifest_generator.py`,
  `github_sync.py`): a new `ManifestFilePolicy` is added, with role-name
  rewriting on portable export to match the table-policies pattern. Per-org
  policies are scrubbed on portable export; global policies (with
  `organization_id IS NULL`) round-trip unchanged.
- **DTO parity** (`api/bifrost/dto_flags.py`): the new `FilePolicyCreate` /
  `FilePolicyUpdate` DTOs are exposed via CLI; UI-only fields go in
  `DTO_EXCLUDES` with a comment.

## Frontend admin surfaces

Three new surfaces. All three share a tree-view component family.

### File browser

Tree of `(location, path)` rooted at each location. Scope segment is hidden
by default — what the author sees is `shared/finance/2026-Q1.pdf`, not
`shared/<scope>/finance/2026-Q1.pdf`. An admin-mode toggle ("Show raw
scopes") exposes the full S3 key for incident response.

Right-click on a folder → menu with **Manage rules**, **Rename**,
**Delete**, **Test access here…**. Right-click on a file → **Open**,
**Download**, **Copy signed URL**, **Test access here…**, **Delete**.

The OrgSelect at the top scopes the tree (single-org default). Admin-mode
"Show raw scopes" turns OrgSelect into a filter and shows all scope
segments inline.

### Rule editor

Same Monaco-JSON editor + templates + reference panel as table-policies.
The reference panel shows the v1 `{file: ...}` and `{user: ...}` field
sets. Templates seed common cases:

- "Everyone reads, role X writes"
- "Only the file's creator"
- "Admin bypass" (always present, non-removable on first save)

### Effective access tester

The safety-net surface. Picks a user (or synthesizes a hypothetical user
with extra roles), picks a path, picks an action. Calls a backend endpoint
that runs the **real** evaluator and returns the resolution trail:

- which policy matched (longest-prefix lookup result)
- which rule fired (or "no rule allowed")
- the AST evaluation trace (what each `eq` / `call` resolved to)
- the resolved S3 key (for sanity-checking scope substitution)

The hypothetical-user feature ("test as Alice + Role Y, even though Alice
doesn't have Role Y") is the most valuable bit — it lets authors verify a
new rule before granting anyone the role.

Available from two entry points: a button in the file browser ("Test
access here…", with the path pre-filled), and a standalone page under
Settings → Files → Access (no path pre-fill, for support cases).

### Renames as a UI primitive

S3 doesn't rename — it copies and deletes. A folder rename in the UI is a
single user action that does:

1. Rewrites every key under the old prefix to the new prefix (S3 copy +
   delete; per-key, with progress feedback).
2. Updates `file_index` sidecar rows for those keys.
3. Updates every `FilePolicy` row whose `(location, path)` falls under the
   renamed prefix.

The rename dialog surfaces the secondary effect: "this will move 47 files
and update 3 policies." Confirmation does all three. Without this, every
folder rename silently breaks access.

The DB-side rule update is one transaction. The S3-side copy is best-effort
(S3 isn't transactional); rules update first, so the worst-case interleaving
is "rules point at the new path before the files have finished copying" —
which means the new prefix is briefly empty, never that access is mismatched.

## Subscriptions

The websocket subscribe path is mirrored from table-policies. A new
channel kind:

- **Channel name:** `files:{location}:{path}` (per-prefix, with the user-
  meaningful path).
- **Subscribe handshake:** runs the evaluator with `action=list` against
  the subscribing user. Reject if denied.
- **Server-side fanout:** every write path (`files.write`, signed-PUT
  completion, form uploads, deletes, renames) calls a
  `publish_file_change(location, path, action, file_meta)` helper that
  broadcasts to all subscribed prefixes.
- **Per-recipient filtering:** for subscribers whose effective `read` is
  Creator-only, the per-connection send-path drops events whose
  `created_by != subscriber.user_id`. Same machinery as table-policies'
  Creator filter.
- **Invalidation on policy changes:** `publish_file_policy_changed` fires
  when a `FilePolicy` row changes; subscribed clients recompute. Revoked
  read = subscription closed with `subscription_revoked`.
- **Reconnect semantics:** no replay. Same as table subscriptions.

## Testing

Mirrors the table-policies coverage shape.

**Backend unit:**
- `tests/unit/test_file_policies_evaluator.py` — every operator × every
  reference; default-deny; admin-bypass; longest-prefix match.
- `tests/unit/test_file_policies_resolver.py` — `{file: ...}` resolution
  against synthetic sidecar rows.
- `tests/unit/test_manifest.py` — `ManifestFilePolicy` round-trip with
  role-name rewriting.
- `tests/unit/test_dto_flags.py` — DTO parity for new file-policy DTOs.
- `tests/unit/test_pubsub_file_changes.py` — `publish_file_change` envelope
  shape; `publish_file_policy_changed` invalidation.

**Backend e2e:**
- `tests/e2e/platform/test_file_policies_rest.py` — REST matrix: 3 users
  (admin, role-holder, plain) × 4 actions × representative policy configs.
  Asserts 200/403 + the Creator-filter case on list (Bob sees only his
  files).
- `tests/e2e/platform/test_files.py` — updated to assert default-deny: a
  non-superuser hitting a file with no governing policy gets 403.
- `tests/e2e/platform/test_file_subscriptions.py` — websocket flow:
  subscribe accepted/rejected, receive write/delete events, Creator-only
  filtering, access revocation.
- `tests/e2e/platform/test_file_signed_urls_batch.py` — batch signing
  with mixed allowed/denied paths.

**Client unit (vitest):**
- `client/src/lib/app-sdk/files.test.ts` — every method in the web SDK,
  shape parity with the Python SDK.
- `client/src/lib/app-sdk/use-files.test.tsx` — happy path, reconnect,
  cleanup, access-denied, `subscription_revoked`.
- `client/src/components/files/FilePolicyEditor.test.tsx` — Monaco editor
  + templates + reference panel.
- `client/src/components/files/EffectiveAccessTester.test.tsx` —
  user/path/action picker + resolution trail rendering.
- `client/src/components/files/FileBrowser.test.tsx` — tree view, scope
  hiding, right-click menu.

**Client e2e (Playwright):**
- `client/e2e/files-app-direct.spec.ts` — embedded test app uses the web
  SDK to upload + read a file via signed URLs; assert no workflow
  execution is created.
- `client/e2e/files-rename.spec.ts` — rename a folder in the admin UI;
  assert files moved, sidecar updated, policy updated, dialog warned.
- `client/e2e/files-effective-access.spec.ts` — open the tester, pick a
  user + path, see the resolution trail.

## Rollout

1. Shared-engine refactor lands first. Table-policies and (eventually)
   file-policies both consume the refactored core.
2. `FilePolicy` schema migration + sidecar extension migration.
3. REST endpoints relax from `CurrentSuperuser` to `Context`. Default deny
   means yesterday's non-superuser callers still cannot call these
   endpoints today — only the error code changes (401 → 403 in some paths)
   plus the opportunity to opt in.
4. Web SDK ships with the batch signed-URL endpoint as the primary
   unblocker.
5. Admin UI ships: file browser → rule editor → effective-access tester.
6. Apps that want direct file access opt their files in per-folder.
7. Subscriptions ship after the REST + SDK + UI surfaces are stable.

No flag day. No data migration of existing files (we do not retroactively
populate `created_by`; existing files are admin-only until rewritten).

## Open questions for plan-writing

1. **Sidecar table structure.** Extend `file_index` or add a sibling
   table? The existing `file_index` is search-focused and has different
   write semantics; sidecar metadata may want its own table.
2. **Rename progress feedback.** S3 prefix copy is per-key; for large
   folders this can take seconds-to-minutes. Streaming progress to the
   browser vs. background job + notification — UX call.
3. **CLI ergonomics.** Inline JSON vs. file-based vs. flag-style for
   policy edits. Same question table-policies answered; we pick the same
   answer.
4. **Hypothetical-user UX in the tester.** "Test as Alice + Role Y" needs
   to be obvious without making it look like Alice has Role Y in reality.

## Fallback positions (if the work outgrows us)

Called out so we know where to stop if we have to:

1. **Reduced operator set is a viable shipping subset.** If the full
   policy editor + manifest + websocket plumbing is too much, ship just
   the v1 operator set with a JSON textarea editor and no live
   subscriptions. The static-three-scope model (Everyone / Role /
   Creator) is expressible as a small set of templates inside that
   subset; a UI that emits only those templates is a third fallback if
   even Monaco-JSON is too much.
2. **Shared-engine refactor is independently valuable for tables.** If
   file policies stall, the engine refactor still cleans up the
   table-policies code and pays for itself.
3. **Batch signed-URL endpoint stands alone.** Even without policy
   evaluation, the batch endpoint solves the gallery use case for
   admin-only file access — a useful intermediate state if the policy
   work has to slip.
