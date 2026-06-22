# Reusable Named Policy Rules — Design

**Date:** 2026-06-22
**Status:** Design (approved in brainstorming; pending written-spec review)
**Branch:** `codex/files-sdk-policies`
**Supersedes:** the "Reusable / named policy templates" section at the tail of
`docs/superpowers/plans/2026-06-22-files-explorer-redesign.md` (this spec resolves its open
questions and diverges on granularity + missing-ref behavior — see "Divergences" below).

## Problem

There is no notion of a *named, reusable* policy rule. The same rule (e.g. `admin_bypass`,
`everyone_read`) is **copied inline** into every file-prefix policy and every table policy that
needs it. The just-shipped "Insert template…" dropdown
(`client/src/components/{tables,files}/*-policy-templates.ts`) only deep-copies a rule into the
editor buffer — a "poor man's template." Editing the canonical rule does not propagate; drift is
invisible and unfixable without hand-editing every copy.

Jack wants to **define a rule once and reference it across many targets** (many file prefixes,
many tables) so that editing the canonical rule updates everywhere it is used.

## Decisions (locked in brainstorming)

| Question | Decision |
|----------|----------|
| Scope of mechanism | **Shared across file + table policies** — one entity, one resolver, one CRUD surface |
| Granularity | **Individual rules**, not whole-policy bundles — a policy's rule list may mix inline rules and references |
| Reference semantics | **Live reference** — resolved fresh at every evaluation; editing the rule changes all referencing policies |
| Rule org scope | **Cascade org→global**, same arm as the policies themselves |
| Missing/unresolvable ref | **Hard-fail the policy load** (raise), consistent with the evaluator's existing structural errors |
| Rename | **Server-side cascade** — renaming a rule rewrites every referencing policy in the same transaction |
| Delete while referenced | **Blocked** — would create a dangling ref (which hard-fails on load) |
| Portability | **First-class manifest entity** (`ManifestPolicyRule`); policies export with `{"$ref": name}` preserved |
| Where it lives | **Approach A** — shared module + one ORM table; both policy services call the same resolver |

## Background: the current policy shape (verified)

File and table policy rules are **structurally identical** — both are a `name` + `description` +
`actions` + a `when` JSON-AST expression. The only difference is the action vocabulary and the
namespaces available inside `when`.

`api/src/models/contracts/policies.py`:

```python
# File rule
class FilePolicyRule(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str | None = None
    actions: list[FileAction] = Field(min_length=1)   # read | write | delete | list
    when: FileExpr | None = None                       # {"user": ...} / {"file": ...} AST

class FilePolicies(BaseModel):
    policies: list[FilePolicyRule] = Field(default_factory=list)

# Table rule (same shape, different action enum + namespace)
class Policy(BaseModel):
    name: str; description: str | None
    actions: list[Action]        # read | create | update | delete
    when: Expr | None            # {"user": ...} / {"row": ...} AST

class TablePolicies(BaseModel):
    policies: list[Policy] = Field(default_factory=list)
```

**Storage:** one JSONB document per policy target.
- File: `FilePolicy.policies` JSONB, keyed `(organization_id, location, path-prefix)`
  (`api/src/models/orm/file_metadata.py`).
- Table: `Table.access` JSONB (contract field `policies`).

**Evaluator (shared, pure):** `api/shared/policies/evaluate.py` evaluates the `when` AST. The
domain wrappers (`api/shared/file_policies.py::evaluate_file_action`, the table equivalent) OR
across rules whose `actions` include the requested action; **default deny**. The table `list`
path additionally **compiles the same AST to a SQL WHERE clause** — so any reference must be
**inlined before the evaluator/compiler runs**. This is the load-bearing constraint of the whole
design: resolution happens *before* evaluation, never inside it.

**Existing error model (the consistency anchor for missing-ref):** the evaluator treats *missing
runtime data* softly (a null `{file: ...}`/`{row: ...}` field → comparison returns `False`) but
treats a *malformed structure* hard — `unknown operator`, `unevaluatable node` → `raise
ValueError`. A missing `$ref` is a malformed policy document, not missing data, so **hard-fail is
the consistent behavior.**

## Architecture (Approach A)

A single shared **named-rule library** that both policy domains reference.

### Data model

New ORM `PolicyRule` (`api/src/models/orm/policy_rule.py`), org-scoped with the same
cascade arm as policies:

```
PolicyRule
  id              UUID  pk
  organization_id UUID | NULL          # NULL = global; org row overrides global of same name
  name            str   (<=100)        # the reference key
  description     str | NULL
  body            JSONB                # { "actions": [...], "when": {...} | null }
  created_by      UUID | NULL
  created_at / updated_at
  UNIQUE (organization_id, name)
```

`body` is exactly the portable half of a rule (`actions` + `when`) — the same content a
`FilePolicyRule`/`Policy` carries minus its own `name`/`description` (those live on the row).

> **Identity vs. cascade.** `PolicyRule` carries `organization_id` and resolves org→global with
> override, so per `api/src/repositories/README.md` it is a **cascade entity**. But its
> resolution key is `(org, name)` exact-match (not longest-prefix like file policies), which
> `OrgScopedRepository.get(name=...)` expresses directly. **Decision:** resolve `PolicyRule`
> through `OrgScopedRepository` (the canonical cascade primitive), *not* a hand-rolled
> `OR organization_id IS NULL` query. This is the one place this spec touches the cascade — use
> the canonical version.

### Reference shape

A rule entry inside any `policies` list becomes a **union** of an inline rule or a reference:

```python
class PolicyRuleRef(BaseModel):
    ref: str = Field(alias="$ref", min_length=1, max_length=100)
    model_config = ConfigDict(populate_by_name=True)

# File:
class FilePolicies(BaseModel):
    policies: list[FilePolicyRule | PolicyRuleRef] = Field(default_factory=list)

# Table:
class TablePolicies(BaseModel):
    policies: list[Policy | PolicyRuleRef] = Field(default_factory=list)
```

A reference is `{"$ref": "admin_bypass"}`. Pydantic discriminates structurally (a ref has only
`$ref`; an inline rule has `actions`). The union sits in BOTH domains' policy documents — the
ref shape itself is domain-agnostic.

### Resolution (the load-time pre-pass)

New shared module `api/shared/policy_rules.py`:

```python
async def resolve_policy_refs(
    policies: list[InlineRule | PolicyRuleRef],
    *,
    org_id: UUID | None,
    repo: OrgScopedRepository,         # PolicyRule repo, cascade-resolving
    action_domain: Literal["file", "table"],
) -> list[InlineRule]:
    """Replace each {$ref} with the resolved rule body, inlined.

    Raises PolicyRuleNotFound (→ 422 on save / 500 on stored-policy load) if a
    referenced rule does not resolve in (org → global).
    Raises PolicyRuleDomainMismatch if the resolved rule's actions are not valid
    for action_domain (e.g. a {read,write,delete,list} file rule referenced from a
    table policy whose vocabulary is {read,create,update,delete}).
    """
```

The two domain wrappers call it immediately before evaluating / compiling:

- `FilePolicyService` (`api/src/services/file_policy_service.py`): after
  `FilePolicies.model_validate(row.policies)`, call `resolve_policy_refs(..., "file")`, then
  `evaluate_file_action`.
- Table policy service: same, before `evaluate` **and before AST→SQL compilation** for `list`.

Resolution is **fresh every load**, which is what makes references *live*. No caching beyond the
request.

**Domain validation.** A rule's `actions` define which domain it is valid in. `resolve_policy_refs`
rejects a cross-domain reference (`PolicyRuleDomainMismatch`). This replaces the plan's proposed
`kind: file|table|both` tag — referencing *individual rules* lets the action vocabulary be the
discriminator instead of a redundant tag. A rule whose actions are a subset valid in both domains
(none today — the enums are disjoint beyond `read`/`delete`) would be referenceable from both; we
don't special-case it.

**Cycle guard.** Rules cannot themselves contain references (a `PolicyRule.body` is a single
`{actions, when}`, never a list), so cycles are structurally impossible. No depth limit needed.

### Integrity guarantees

1. **Save-time validation.** Saving a file/table policy whose `$ref` does not resolve →
   `422` with a `PolicyValidationError` pointing at the offending list index. The same
   `resolve_policy_refs` call backs both save-validation and load.
2. **Hard-fail on load.** A stored policy whose ref later becomes unresolvable raises on load
   (consistent with `unknown operator`). In normal operation this is unreachable because of (3)
   and the rename cascade — it's the loud backstop, not an expected path.
3. **Delete-while-referenced is blocked.** `DELETE /api/policy-rules/{name}` first runs the
   where-used query (below); if any file or table policy references it, returns `409` with the
   list of referencing targets. (A global rule is "referenced" if any org's policy references it.)
4. **Rename cascade (server-side).** Renaming a rule (`PUT` with a new `name`) finds every file
   policy AND table policy whose `policies` list contains `{"$ref": old}` and rewrites them to
   `{"$ref": new}` **in the same transaction**. Authoritative regardless of trigger
   (CLI / MCP / UI) — no orphans possible. Reuses the where-used query.

### Where-used query

A single helper scans both JSONB policy columns for a `$ref` to a given name:

```sql
-- file policies
SELECT id, location, path FROM file_policies
WHERE policies -> 'policies' @> :ref_json;     -- :ref_json = '[{"$ref":"<name>"}]'
-- table policies
SELECT id, name FROM tables
WHERE access -> 'policies' @> :ref_json;
```

Org-scoped to match the rule's reach (a global rule's where-used spans all orgs; an org rule's
spans that org). Backs both delete-guard and rename-cascade, and powers the **blast-radius UX**
("this rule is used by N file prefixes and M tables") shown before saving a rule edit.

> **Indexing.** The `@>` containment scan runs on every rename/delete and every blast-radius read.
> Add a **GIN index** on `file_policies.policies` and `tables.access` (the JSONB policy columns) in
> the migration so these stay index-backed rather than sequential scans as policy counts grow.

### CRUD surface (three parallel surfaces — CLAUDE.md rule)

`PolicyRule` is an entity mutation, so it needs all three surfaces fed from one DTO pair
(`PolicyRuleCreate` / `PolicyRuleUpdate`):

- **REST** (`api/src/routers/policy_rules.py`): `POST/GET/PUT/DELETE /api/policy-rules`,
  `GET /api/policy-rules/{name}/usages` (where-used / blast radius). Business logic in
  `api/src/services/policy_rule_service.py`; router stays thin.
- **CLI**: `bifrost policy-rule {create,list,get,update,delete,usages}` — a new top-level group
  (named rules are global/org library objects, not bound to a single file location or table, so
  they get their own group rather than living under `files`/`tables`).
- **MCP**: thin HTTP wrapper over the REST endpoints (per `_http_bridge.py` pattern; enforced by
  `test_mcp_thin_wrapper.py`). No ORM access.

DTO parity (`test_dto_flags.py`) and the contract-version tripwire (`test_contract_version.py`)
apply — adding `PolicyRuleCreate`/`Update` to the CLI/SDK contract surface requires a fingerprint
refresh (additive ⇒ refresh only; no `CONTRACT_VERSION` bump unless a CLI-parsed shape changes).

### CLI for the policy documents themselves (not just the named rules)

Two surfaces edit the *policies* that contain rules. The named-rules feature must keep both able
to round-trip a `{"$ref": ...}` entry.

**File policies — already exist, must accept refs.**
`bifrost files policies {list,get,set,delete}` (`api/bifrost/commands/files.py`,
`policies_group`). `set` loads a policy document (JSON/YAML literal or file) and PUTs it.
- *Change:* none to the command shape — it already passes the document through verbatim. The
  server-side `resolve_policy_refs` save-validation (above) is what makes a `{"$ref": ...}` entry
  in that document valid or `422`. Add a CLI e2e asserting a referenced rule round-trips through
  `files policies set` / `get`.

**Table policies — partial today, add a dedicated subgroup.**
`bifrost tables {create,update} --policies <json|file>` embeds the whole `access` document
(`api/bifrost/commands/tables.py`). There is **no** `tables policies` subgroup mirroring
`files policies`.
- *Change:* add `bifrost tables policies {get,set}` for symmetry with `files policies` (get/set
  the `access` document of one table by name), so a user can edit a table's policy — including a
  `{"$ref": ...}` entry — without re-sending the whole table create/update payload. `--policies`
  on create/update stays as the bulk form. Same server-side ref validation applies.

> **Why a `tables policies` subgroup and not just `--policies`:** today editing a table's access
> means re-supplying it through `tables update`, which is fine for scripted bulk edits but
> awkward for "tweak the policy on this one table." `files` already drew this line (a `files
> policies` group distinct from `files write`); `tables` should match so the two policy surfaces
> are consistent. This is in scope because referencing a named rule is precisely the "tweak one
> policy" motion we're optimizing for.

### Portability (manifest / Solutions)

- New first-class `ManifestPolicyRule` (`api/bifrost/manifest.py`), sibling of
  `ManifestFilePolicy`: `{ name, description, body: {actions, when}, organization_id? }` with the
  same env-specific scrub rules (`api/bifrost/portable.py`).
- Serialization in `manifest_generator.py` (DB → manifest); import in `github_sync.py`
  `_resolve_policy_rule` (manifest → DB, upsert by `(organization_id, name)`).
- File/table policies export **with `{"$ref": name}` preserved** (not flattened) — the reference
  relationship survives a round-trip. A Solution can ship a rule library plus policies that
  reference it.
- **Install ordering:** rules import **before** policies (a policy's ref must resolve at import
  validation). If a Solution's policy references a rule the bundle doesn't carry and the target
  env lacks, import **fails closed** (hard-fail, consistent with load).

### Authorization & audit

**Who may write a rule.** There is a single admin role today, so no new per-role gating is
introduced. Creating/editing/deleting a `PolicyRule` requires admin — the existing bypass check
(`is_platform_admin OR is_provider_org`, per `api/src/repositories/README.md`). A **global**
rule (`organization_id = NULL`) reaches every org, so writing one is a bypass-gated operation
exactly like writing any other global cascade entity; no special concept is added. (Org-scoped
rules follow the same write path as any org entity.)

**Audit logging.** Rule create / edit / delete / rename are security-sensitive (a single edit can
change access across many policies and orgs), so each writes an `AuditLog` entry through the
existing audit path — including, for edits/renames, the **where-used count** at the time of the
change so the blast radius is captured in the record.

### Built-in `admin_bypass` rule (no migration)

Today `FilePolicyService` seeds an **inline** `admin_bypass` rule on first policy create
(`shared/file_policies_seed.py`). **Decision:**
- Seed a **built-in, read-only global `PolicyRule` named `admin_bypass`** once (idempotent). It is
  flagged read-only (not editable/deletable through the CRUD surface) — it is the platform's
  bypass primitive, not a user-tunable rule.
- Have the first-policy-create seed insert a **`{"$ref": "admin_bypass"}`** instead of the inline
  rule, so newly created policies reference the built-in.
- **Do NOT migrate existing inline `admin_bypass` rows.** Policies already created keep their
  inline copy. Consequence — intended and explicitly accepted: editing the built-in `admin_bypass`
  is moot anyway (it is read-only), and the inline copies on old rows continue to behave exactly as
  before. No data migration, no fail-closed window, no behavior change for existing policies.
- The existing "revoke admin_bypass on this prefix" still works by removing the rule (inline or
  ref) from that one policy's list.

> **Why read-only built-in, not an editable shared rule.** Making `admin_bypass` editable would
> mean "one edit removes admin bypass everywhere," which is a large, dangerous foot-gun with no
> requested use case. Read-only keeps the reference ergonomics (new policies point at one canonical
> rule) without the blast radius. Other named rules authored by admins are fully editable — only
> the built-in is locked.

## Components & boundaries

| Unit | Responsibility | Depends on |
|------|----------------|------------|
| `PolicyRule` ORM | persistence + `(org,name)` uniqueness | Base |
| `PolicyRuleRef` contract | the `{$ref}` union member | — |
| `api/shared/policy_rules.py::resolve_policy_refs` | ref → inline body, domain-validate, raise on miss | `OrgScopedRepository`, evaluator contracts |
| where-used helper | scan both JSONB columns for a ref | DB session |
| `PolicyRuleService` | CRUD + rename-cascade + delete-guard (txn) | ORM, where-used helper |
| `policy_rules.py` router | thin REST surface | service |
| CLI `policy-rule` group + MCP wrapper | parallel surfaces for the named rules | REST DTOs |
| CLI `tables policies {get,set}` | per-table policy-document edit (mirrors `files policies`) | REST |
| `ManifestPolicyRule` + generator/sync | portability round-trip | manifest layer |
| Client: reference mode in Tables + Files policy editors | author `{$ref}` in-context | REST |
| Client: in-context policy-rules manager (list/edit/where-used) | manage shared rules from those views | REST |

The two existing policy services change minimally: each adds **one `await resolve_policy_refs(...)`
call** between validate and evaluate/compile. The evaluator and the SQL compiler are **unchanged**
(they only ever see inlined rules).

## Frontend

The named-rules UI lives **inside the existing Tables and Files policy-editing surfaces** — not as
a separate, disconnected admin page. A user editing a table's or a file prefix's policy is exactly
where they reach for "apply the shared `admin_bypass`," so authoring and managing named rules
happens in that same flow.

**1. Editing policies in the Tables and Files views (the primary ask).**
Both views already edit a policy document through the shared **`JsonYamlEditor`**:
- **Files:** the policy editor in the Files explorer (`PolicyEditorModal` / Policies tab,
  `client/src/components/files/`).
- **Tables:** the table policy editor (`client/src/components/tables/`, the `--policies`/`access`
  document surface).

Both gain:
- A **reference mode** on the existing "Insert template…" dropdown: inserts `{"$ref": name}` into
  the editor buffer instead of deep-copying the rule body. The dropdown is sourced from the
  server (`/api/policy-rules`, filtered to the domain by the rule's actions). Copy mode stays for
  intentional one-offs; reference is the default for named rules.
- Inline **resolve-on-save validation feedback**: a `{"$ref": ...}` to a non-existent rule
  surfaces the server `422`/`PolicyValidationError` at the offending list index (same channel the
  editors already use for malformed `when` ASTs).

**2. Managing the named rules themselves (reachable from those same views).**
A **policy-rules manager** — list / create / edit / delete + where-used — promoted from the
client `*-policy-templates.ts` catalogs to a server-backed list. It is reachable **from the
Tables and Files policy editors** (e.g. a "Manage rules…" affordance beside the reference
dropdown) so the author never leaves the policy-editing context to define a new shared rule. Whether
it renders as a slideout/modal from those views or also gets a standalone route is an
implementation detail; the requirement is that it is reachable in-context from both the Tables and
Files policy surfaces.
- Before saving a rule edit, show the **blast-radius count** from
  `/api/policy-rules/{name}/usages` ("used by N file prefixes and M tables") — editing a live rule
  changes every referencing policy.
- Rename and delete go through the server (cascade / 409-guard); the UI surfaces the where-used
  list when a delete is blocked.

- Types via `npm run generate:types` (no hand-written types).

## Testing

- **Evaluator/compiler unchanged** — existing pure tests stay green (proof refs never leak into
  evaluation).
- **`resolve_policy_refs` unit tests:** inline-only passthrough; single ref resolves; org overrides
  global; missing ref raises; cross-domain ref raises; mixed inline+ref order preserved.
- **Service tests:** rename cascade rewrites file AND table policies in one txn; delete-while-
  referenced returns 409 with targets; delete of unreferenced succeeds; where-used spans orgs for a
  global rule; the built-in `admin_bypass` rule rejects edit/delete (read-only) and is seeded
  idempotently; create/edit/delete/rename each write an `AuditLog` row (with where-used count on
  edit/rename); writing a **global** rule requires the bypass check (a non-bypass caller is denied);
  existing inline `admin_bypass` rows are untouched by the feature (no migration).
- **REST e2e:** create rule → reference it in a file policy and a table policy → evaluate (allow) →
  edit rule body → evaluate reflects the edit (live) → rename → both policies repointed → attempt
  delete (409).
- **Contract:** `test_dto_flags.py` parity, `test_contract_version.py` fingerprint refresh,
  `test_mcp_thin_wrapper.py` for the MCP tool.
- **CLI:** a referenced rule round-trips through `bifrost files policies set`/`get`; new
  `bifrost tables policies get`/`set` round-trips a policy doc including a `{"$ref": ...}` entry;
  `bifrost policy-rule` group create/list/get/update/delete/usages.
- **Manifest round-trip:** `test_manifest.py` for `ManifestPolicyRule`; `test_git_sync_local.py`
  for rule-before-policy import ordering and ref preservation; install fails closed on an
  unresolvable ref.
- **Client:** vitest for the reference-mode insert (Tables editor + Files editor) and the
  in-context policy-rules manager (list/edit/where-used/blast-radius); Playwright admin spec
  covering "insert ref in the Files policy editor" and "insert ref in the Tables policy editor"
  (note: `*.admin.spec.ts` is local-only, not in CI).

## Divergences from the plan's earlier "templates" section

1. **Granularity — individual rules, not bundles.** The plan modeled `PolicyTemplate` as a *bundle*
   (`rules: [...]`). This spec references **one rule at a time** (`PolicyRule.body` is a single
   `{actions, when}`). Finer reuse, structurally cycle-free, and it lets the action vocabulary be
   the domain discriminator — dropping the plan's `kind: file|table|both` tag.
2. **Missing ref — hard-fail, not fail-closed-skip.** The plan said drop-and-deny. This spec raises
   on an unresolvable ref, matching the evaluator's existing structural-error handling
   (`unknown operator` raises). Paired with delete-guard + rename-cascade so a dangling ref is
   unreachable in normal operation. Settled on the "most consistent/expected" criterion: a missing
   ref is a malformed document, not missing data.

Everything else (org→global cascade scope, inline `$ref` reference form, fail-closed install,
blast-radius UX, three-surface CRUD, seed-as-reference) **agrees with** the plan's recommended
shape.

## Out of scope

- Per-rule versioning / history (the plan floated `version?`). Not needed for "edit once, applies
  everywhere"; revisit if audit demands it.
- A dedicated cross-org *shared file pool* (separate known limitation — see
  `project-files-sdk-status`).
- Copy-mode removal — leaving inline copy available for intentional one-offs.
- Migrating existing inline `admin_bypass` rows to references (explicitly declined above — old
  rows stay inline, no migration).
- Per-role authorization for rule writes — single admin role today; admin (bypass check) gates all
  rule writes.
