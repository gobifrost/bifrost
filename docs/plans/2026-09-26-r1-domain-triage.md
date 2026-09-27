# R1 domain triage: what's inert vs. what's a new decision

**Written:** 2026-09-26, read-only research pass. No code changed in this pass — see
[`2026-09-26-rbac-program-handoff.md`](2026-09-26-rbac-program-handoff.md) for the program
context. R1a (catalog/inventory/generator framework, `operation_route` metadata only, zero
authorization change) is already committed on this branch. This document triages the 17
reference-branch "make X operations canonical" commits (plus one related fix commit) that
R1a deliberately did **not** port, so Jack can decide per domain, per item, what ships and in
which slice (R1b domain PR, R3, a standalone bugfix, or "new feature, not RBAC").

Each domain commit is split into the same five buckets:

- **(a) Catalog/route metadata** — `operation_route(...)` decorator additions. Already
  established as safe/inert by R1a's own verification; not itself an approval item here.
- **(b) MCP thin-wrapper conversion** — rewriting the domain's MCP tool implementation from
  direct ORM/repository access into a thin HTTP bridge that calls the REST endpoint. This is
  the reason R1 was split into R1a/R1b: whatever the REST endpoint enforces becomes what MCP
  enforces, which can change MCP-level authorization even when REST itself doesn't change.
- **(c) New authorization or validation decisions** — anything genuinely new: a 403/422/409
  that didn't fire before, a validation that didn't exist, a visibility change. Each item
  below cites file:line, describes today vs. proposed, and a judgment on whether R3's formal
  authorization-context work would introduce the same thing as a matter of course.
- **(d) Other new side effects** — new audit emission, manifest regeneration, external calls,
  cache/commit-ordering changes not already covered by (c).
- **(e) Tests** — what test files move, and whether the new behavior in (c)/(d) has a named
  test asserting it.

Research was done by four parallel sub-agents against the full commit diffs on the reference
branch (`/home/jack/GitHub/bifrost/.worktrees/code-builder`); a few sections are explicitly
flagged **unsure**/**incomplete** where the diff was too large to read exhaustively in one
pass — those need a second look before their domain's R1b PR, not just before merge.

## Cross-cutting findings (read this first)

1. **The `assignable_to_resources` role-type check is duplicated verbatim across at least
   three domains** (agents, forms, apps — see each domain's (c) below). All three add the same
   "capability role vs. resource-assignable role" 409/422 gate inline in their own router. This
   is precisely the Role-assignment-boundary distinction R2/R3 formalizes centrally. Recommend
   Jack decide once whether this ships now (copy-pasted per domain, as the reference branch
   did) or waits for R2/R3's shared evaluator — shipping it twice means reviewing it three-plus
   times either way.
2. **The "silently drop/override → 403" pattern repeats across domains** (agents fields on
   non-admin update, agents `mcp_connection_ids`, apps `organization_id` on create). Each
   instance reverses an existing, sometimes deliberately-commented, silent-drop UX decision.
   These are genuine behavior changes independent of any RBAC vocabulary — worth its own
   line-item approval per instance, not a blanket "obviously fine" pass.
3. **Two commits are not really "canonicalization" at all**: **apps** (`059474653`) bundles a
   broad, 11-call-site mutation-authorization tightening (`get_application_to_manage_or_404`)
   that is a real security-relevant change on existing endpoints, not a rename; **knowledge**
   (`e6810d6ed`) is substantially new product surface (new router, new agent-knowledge
   boundary, agent-executor wiring) with only one catalog operation riding along. Both need
   their own product/security decision, not a standard "port the MCP rename" treatment.
4. **workflows** (`8799f4c06`) contains what may be the single biggest sleeper change in the
   whole set: `list_workflows` moves from an inline org-filter switch to
   `WorkflowRepository.list()`, which (per `api/src/repositories/README.md`) also applies a
   role filter for non-superusers. That can **narrow** what a non-admin sees today. No test was
   found asserting this specific change by name — get one before this ships.
5. **Three domains are genuinely clean, rename-only R1b candidates with zero new
   authorization decisions**: **executions**, **roles**, and (mostly) **platform_jobs** /
   **claims** / **file_policies**. These are the lowest-risk starting points if Jack wants an
   easy first R1b batch to validate the review pattern before tackling agents/forms/apps.
6. **Several domains touch `client/`** (agents' `AgentSettingsTab`, apps' `AppInfoDialog`,
   tables' generated `v1.d.ts`, knowledge's `AgentSettingsTab`/`v1.d.ts`) — R1b PR scoping
   should not assume "server-only," contrary to R1a's working assumption for its own scope.
7. **configs** and **policy_rules** each add one genuinely new REST endpoint
   (`configs.get`, `policy.rules.get`) gated by the *same* pre-existing admin-only dependency
   as their siblings — no new authorization decision, but new capability. This matches R1a's
   independent finding that these two operations' REST endpoints don't exist on main yet
   (along with `workflows.get`, `events.subscriptions.get`, `knowledge.search`,
   `workspace.files.patch`, all dropped from R1a's catalog for the same reason). **configs**
   additionally carries a same-day self-correction (`3564d350c`) — whoever ships `configs.get`
   should port the corrected version directly, not replay the transient bug and then the fix.

## The 6 operations R1a dropped: what each would need to land

R1a's catalog only declares operations whose REST endpoint already exists on main. These 6
were excluded for that reason. None is a rename or a decorator add — each needs the actual
endpoint (and, for two of them, a design decision) before it can re-enter the catalog. Detail
for each lives in its domain section below; this is the consolidated "what's missing" view.

| Operation | Reference endpoint | What's missing on main | What it would take to land | Detail |
|---|---|---|---|---|
| `workflows.get` | `GET /api/workflows/{workflow_id}` | No GET-by-id route for a single Workflow at all — main has PATCH/DELETE on that path but no GET. | New router handler using `WorkflowRepository.get(id=...)` (tenant/role/Solution-visibility cascade), a catalog entry, a decorator, and a REST e2e test. No auth decision beyond "same visibility as `list_workflows` post-fix" (see workflows (c).2 below) — should land together with that visibility question, not before it. | [workflows](#workflows-8799f4c06) |
| `events.subscriptions.get` | `GET /api/events/sources/{source_id}/subscriptions/{subscription_id}` | No GET-by-id route for a single Event Subscription — only list/create/update/delete exist. | New `CurrentSuperuser`-gated handler, 404 on missing source/subscription, catalog entry + decorator + test. Pure new capability, same admin-only gate as siblings — no new authorization decision. | [events](#events-605a45724) |
| `knowledge.search` | `POST /api/knowledge/search` | Main's knowledge router lives at `/api/knowledge-sources` with no search endpoint at all; the reference branch's `/api/knowledge` router doesn't exist on main. | Not just a missing endpoint — a new router, a new agent-knowledge-boundary authorization model, and agent-executor wiring (see knowledge (c) below). This is product/design work, not a port; needs its own decision from Jack before scoping an endpoint add. | [knowledge](#knowledge-e6810d6ed) |
| `policy.rules.get` | `GET /api/policy-rules/{domain}/{name}` | No GET-by-name route — only PUT/DELETE/`.../usages` exist; the CLI `get` leaf currently lists everything and filters client-side. | New handler backed by a new public `PolicyRuleService.get()` (delegates to the existing private `_get`), same `CurrentSuperuser` gate as siblings, catalog entry + decorator + test. One design note to carry over: solution-managed rules would be readable (not just writable-blocked) through this path — a "read wider than write" default worth confirming, not assuming. | [policy_rules](#policy_rules-2b1803831) |
| `configs.get` | `GET /api/config/{config_id}` | No GET-by-id route — `bifrost configs get`, MCP `get_config`, and the secret check inside `bifrost configs delete` all currently fetch the whole list and filter client-side. | New handler, same `CurrentSuperuser` gate as siblings, catalog entry + decorator + test — **plus port the already-known fix from `3564d350c`**: don't apply the name-cascade org filter to the ID lookup (`OrgScopedRepository.get()` deliberately doesn't cascade ID lookups), or a platform admin will get a 404 reading a config their sibling PUT/DELETE routes let them write. | [configs](#configs-0ce412d71) |
| `workspace.files.patch` | `POST /api/files/patch` | No `/api/files/patch` route at all — this is a new conflict-safe unique-string-edit primitive, not a rename of an existing one. | New handler with new 404/409(version_conflict)/409(string_not_found)/400(binary) logic, catalog entry + decorator + test. Independent of this, two *existing* workspace-file endpoints (`write`, `delete`) are also missing the new solution-managed-write guard found in workspace_files (c).1 below — worth landing together since both are "workspace file endpoints bypass the ORM flush guard" fixes. | [workspace_files](#workspace_files-11fe6f128) |

---

## agents (7aa8f5a7e)

### (a) Catalog/route metadata
5 `operation_route(...)` kwargs added to `api/src/routers/agents.py` (list/get/create/update/delete) — purely additive.

### (b) MCP thin-wrapper conversion
`api/src/services/mcp_server/tools/agents.py`: 927 lines touched, shrinks substantially. Tools renamed `list_agents`/`get_agent`/`create_agent`/`update_agent`/`delete_agent` → `bifrost_list_agents`/`bifrost_get_agent`/`bifrost_create_agent`/`bifrost_update_agent`/`bifrost_delete_agent`. Old implementation did direct ORM/repository access with its own permission logic:
- old `list_agents` (~line 693-706): built an `AgentRepository(...)` directly and branched `list_all_in_scope` vs `list_agents` based on `context.is_platform_admin` — bespoke scoping now replaced by calling `GET /api/agents`.
- old `get_agent` (~line 818-836): its own SQL query with `apply_mcp_org_scope(query, Agent, context)` and manual "org-specific over global" ordering — bespoke visibility now replaced by `GET /api/agents/{id}`.
- old `update_agent` (~line 1230-1236): inline `if not context.is_platform_admin: ...` org-boundary check — superseded by, but **not identical to**, the new REST-side privileged-fields 403 below.

### (c) New authorization or validation decisions
All in `api/src/routers/agents.py` (line numbers post-commit):
1. **~line 178 (`create_agent`)**: new 403 "Only platform administrators can set Agent fields: ..." when a non-admin sets `system_tools`/`knowledge_sources`/`delegated_agent_ids`/`role_ids`/`mcp_connection_ids` on create. Today: no blanket field-level rejection exists. Proposed: hard 403. — *novel, not an obvious R3 default* (a fine-grained field-level gate, more specific than a boundary/capability check).
2. **~lines 339-345 (`update_agent`)**: same privileged-fields 403 on update, including `clear_roles`. Today: update silently drops these fields to `None` for non-admins — **no error**. Proposed: 403 instead of silent drop. — *novel*; this is a deliberate reversal of an existing, explicitly-commented silent-drop UX decision ("Silently drop the field rather than 403 so the UI can submit a single payload regardless of role") — flag to Jack by name, since a previous author chose silent-drop on purpose.
3. **~lines 355-358 (`update_agent`)**: `mcp_connection_ids` non-None from a non-admin now 403s "Only platform administrators can manage Agent MCP connections" (same silent-drop-to-403 reversal, separate code path).
4. **~lines 361-364**: new 422 if both `clear_roles` and `role_ids` are provided together. Today: no such conflict check. — *likely R3 territory* (input-shape hygiene, not access control).
5. **~lines 371-383**: new 422 "Rescoping an Agent with MCP connections requires an explicit mcp_connection_ids list" when `organization_id` changes and the agent already has MCP connections. Today: rescoping silently keeps stale cross-org connections (arguably a latent bug). — *novel, not an obvious R3 default*, but plausibly a legitimate bugfix independent of RBAC.
6. **`_validate_agent_references` ~lines 96-111**: new duplicate-reference check across `tool_ids`/`delegated_agent_ids`/`role_ids`/`mcp_connection_ids` → 422. Today: duplicates silently accepted. — *likely R3 territory* (generic input hygiene).
7. **~lines 120-136**: new `role_ids` validation — must resolve to an existing `Role` with `assignable_to_resources = True`, else 404/422. Today: unvalidated (old code silently skipped roles not found). — *this concept is core, obvious R3 territory* — the exact Role-assignment-boundary distinction R2/R3 formalizes. (See cross-cutting #1: duplicated in forms/apps too.)
8. **~lines 138-157**: new `mcp_connection_ids` validation — must belong to the same org as the target agent, else 422 (also 422 if agent is global and connections supplied). Today: cross-org connections were silently filtered out (dropped), not rejected. — *novel, not an obvious R3 default*.

### (d) Other new side effects
- `emit_audit(db, "agent.create"/"agent.update"/"agent.delete", ...)` — 3 new call sites (~lines 304-316, 484-493, 508-514). No audit trail existed before.
- `RepoSyncWriter(db).regenerate_manifest()` — 3 new call sites paired with each audit emit.

### (e) Tests
- `api/alembic/versions/20260817_agent_mcp_tool_names.py` — migration renaming persisted `agents.system_tools` entries to the `bifrost_*` names.
- `api/tests/e2e/api/test_agents.py` (+35), `api/tests/e2e/mcp/test_mcp_parity.py` (+206, new), `api/tests/e2e/mcp/test_mcp_scoped_lookups.py` (−312, old bespoke-scoping tests deleted), `api/tests/unit/test_agent_mcp_name_migration.py` (+62, new, upgrade/downgrade), `test_mcp_solution_managed.py`, `test_mcp_thin_wrapper.py` (+9).
- `client/src/components/agents/AgentSettingsTab.test.tsx` + `.tsx` — **this domain touches client**.

---

## forms (792283a33)

### (a) Catalog/route metadata
5 decorators (list/get/create/update/delete) — purely additive.

### (b) MCP thin-wrapper conversion
`api/src/services/mcp_server/tools/forms.py`: 937 lines touched. Old `list_forms` (~lines 35-61) had its own 3-way scope resolution built directly into the MCP tool (`is_platform_admin` / `org_id` / neither) — bespoke, now replaced by calling `GET /api/forms` and inheriting the REST handler's scope resolution.

### (c) New authorization or validation decisions
Both in `_replace_form_roles` (shared by create/update), `api/src/routers/forms.py` ~lines 360-378:
1. **Duplicate `role_ids` check** → 422. Today: duplicates silently accepted. — *likely R3 territory*.
2. **`assignable_to_resources` check on Form role_ids** → 409 "Capability role(s) cannot be assigned to Forms". Today: only existence was checked. — *core R3 territory*, same concept as agents #7 above — **duplicated near-verbatim across at least agents/forms/apps** (cross-cutting #1).
3. **`update_form` field-set semantics change**, ~lines 935-1027 (pre-existing code, changed here): `if request.description is not None:` → `if "description" in request.model_fields_set:` (same pattern for `workflow_id`, `launch_workflow_id`, `default_launch_params`, `allowed_query_params`). Today: an explicit `null` in a PATCH is indistinguishable from omitting the field — left unchanged. Proposed: explicit `null` now clears the field. **This is a data-semantics change, not an access-control change** — separate decision Jack should see regardless of RBAC; *not R3 territory*.

### (d) Other new side effects
- `emit_audit(db, "form.create"/"form.update"/"form.delete", ...)` — 3 new call sites (~553-566, 1015-1027, 1097-1104).
- `RepoSyncWriter(db).regenerate_manifest()` — paired with each.

### (e) Tests
- `api/alembic/versions/20260817_form_mcp_tool_names.py` (new).
- `api/tests/e2e/api/test_forms.py` (+177), `test_mcp_parity.py` (+272), `test_mcp_scoped_lookups.py` (−408, removed), `test_mcp_tools.py`, `test_dto_body_assembly.py` (+30, likely covers the null-semantics change), `test_form_mcp_name_migration.py` (new, +59), `test_mcp_solution_managed.py`, `test_mcp_thin_wrapper.py`.

---

## tables (b9847a45d)

### (a) Catalog/route metadata
5 decorators (create/list/get/update/delete) — purely additive.

### (b) MCP thin-wrapper conversion
`api/src/services/mcp_server/tools/tables.py`: 685 lines touched. Old implementation had extensive inline scoping: manual cross-org rejection (~line 233, 247-248), an inline global-rescope gate "Validate scope change permissions" `if scope == "global" and not context.is_platform_admin:` (~lines 366-367), and another inline org filter on visibility (~lines 438-440) — all replaced by delegating to the REST router.

### (c) New authorization or validation decisions
All in `api/src/routers/tables.py`:
1. **New `_validate_table_target_org` helper (~lines 513-534)**, called from `create_table` (~677) and `update_table` (~1006, when `organization_id`/`policies` changes) — 422 if the target org doesn't exist. Today: no existence check; a bogus `organization_id` fails later at the DB FK level with an opaque error. — *likely R3 territory in spirit* (target-org validation naturally pairs with boundary resolution), but implemented as ad hoc per-router logic.
2. **`update_table` now validates `organization_id` changes, not just `policies`** (~lines 979-1023). **`TableUpdate.organization_id` is a brand-new field** — tables could not be rescoped via update through this contract before this commit at all. — *novel: new functionality (org rescoping) bundled with its validation*, not a tightening of an existing decision. Flag as functionality expansion, not just auth tightening.
3. **Update-conflict error changes 422 → 409** (~lines 1023-1040): `except ValueError` → `except (IntegrityError, ValueError)`; a rename/rescope collision now returns 409 with a clean message instead of 422 with a raw exception string, plus an added `db.rollback()`. — not an authz decision; an error-shape correction a CLI/SDK consumer parsing status codes would notice.
4. **`delete_table` pre-checks existence** (~lines 1073-1080) to capture the name for the audit event — functionally equivalent 404, not a decision change.

### (d) Other new side effects
- `emit_audit(ctx.db, "table.create"/"table.update"/"table.delete", ...)` — 3 new call sites (~86-97, 1044-1051, 1094-1101).
- `RepoSyncWriter(ctx.db).regenerate_manifest()` — paired with each.
- CLI (`api/bifrost/commands/tables.py`) gains `--org`/`--is-global` on `tables update`, wiring into the new `organization_id` field — a client-visible new capability.
- `client/src/lib/v1.d.ts` regenerated — confirms the new field is contract-visible.

### (e) Tests
- `api/alembic/versions/20260817_table_mcp_tool_names.py` (new).
- `test_mcp_parity.py` (+235), `test_cli_tables.py`/`test_tables.py` (REST/CLI e2e), `test_mcp_tools.py`, `test_mcp_solution_managed.py`, `test_mcp_thin_wrapper.py`, `test_table_contract_policies.py` (new), `test_table_mcp_name_migration.py` (new, upgrade/downgrade).

---

## apps (059474653)

### (a) Catalog/route metadata
10 decorators total (`apps.create/list/get/update/delete/publish/replace/validate` in `applications.py`, plus `apps.dependencies.get/update` in `app_code_files.py`) — purely additive metadata.

### (b) MCP thin-wrapper conversion
`api/src/services/mcp_server/tools/apps.py`: 1246 lines touched, the largest of the four in this batch. Old implementation duplicated the "global vs organization scope, fall back to context.org_id" pattern seen in agents/forms/tables for slug resolution and effective-org resolution (~lines 26-44, 134-146) — replaced by REST calls.

### (c) New authorization or validation decisions — largest finding in the whole triage
All in `api/src/routers/applications.py` / `app_code_files.py`:
1. **New `get_application_to_manage_or_404` helper**, replacing plain `get_application_by_id_or_404` (a read-access check) at **11 call sites**: `list_app_files`, `read_app_file`, `put_dependencies`, `update_application`, `delete_application`, `save_draft`, `publish_application`, `replace_application_endpoint`, `swap_application_slugs`, `rollback_application`, `upload_application_logo`/`delete_application_logo`. The new helper: platform admins pass through; a non-admin may manage only a loose Application owned by their own org; a global Application or one owned by a different org now 403s "Only platform administrators can manage global Applications". **Today: every one of these 11 endpoints only required read-level access to mutate** — any user who could *see* an app (including a global, platform-owned one) could apparently update/delete/publish/replace/rollback/upload-logo on it. Proposed: mutation requires same-org ownership or platform-admin. — **This is a broad, blanket tightening across 11 endpoints in one helper, not a narrow field-level gate. This IS core R3 territory** (exactly the capability/boundary model), but it's landing here, ahead of R2/R3's formal machinery, as a plain inline check. **Strongly recommend Jack decide explicitly whether this ships now as a standalone security fix or waits for R3** — shipping it twice is wasted review effort either way, and shipping it *late* leaves a real gap open in the meantime.
2. **New `_validate_application_target`**: non-admin explicit `organization_id` on create/update now 403s "Applications may only be created in your organization"; nonexistent org 422s. Today: a non-admin's explicit `organization_id` was silently *overridden* to their own org (never rejected), no existence check at all. — same override-to-reject shift as agents #2; *novel, not an obvious R3 default* on its own (existence-check half is ordinary hygiene).
3. **New `_validate_application_roles`**: duplicate (422), missing-role (404), `assignable_to_resources` (409) — identical in shape to forms/agents role_ids validators (cross-cutting #1, third occurrence).
4. **`update_application` organization_id change gate**: non-admin setting `organization_id` on update now 403s "Only platform administrators can change Application scope". Today: no such explicit gate existed. — *likely R3 territory*.

### (d) Other new side effects
- `emit_audit(...)` — 6 new call sites (`app.create/update/delete/publish/replace/dependencies.update`).
- `RepoSyncWriter(ctx.db).regenerate_manifest()` — 5 new call sites; **`publish_application` audits but does not call `regenerate_manifest()`**, unlike its siblings — worth confirming this is deliberate (publish may trigger manifest regen elsewhere via its platform job) rather than an oversight.
- `put_dependencies`: moves `await ctx.db.commit()` to *after* the audit-emit/manifest-regen calls — a commit-ordering change worth double-checking given the codebase's own history with commit-ordering bugs.

### (e) Tests
- `api/alembic/versions/20260817_app_mcp_tool_names.py` (new).
- `test_applications.py` (+132), `test_mcp_parity.py` (+274, largest of the batch), `test_cli_apps.py`, `test_operation_catalog.py` (+64), `test_mcp_solution_managed.py` (−197, largest deletion), `test_mcp_thin_wrapper.py` (+14).
- `client/src/components/app-builder/AppInfoDialog.test.tsx`/`.tsx`, `AppInfoDialogPayload.ts`, `client/src/pages/EntityManagement.tsx` — **this domain touches client UI**, needs client review in its R1b PR.

---

## events (605a45724)

### (a) Catalog/route metadata
10 decorators (webhook_adapters.list, sources.list/create/get/update/delete, subscriptions.list/create/update/delete) — additive. `events.subscriptions.get` also gets a decorator, but on a brand-new endpoint (see (c)).

### (b) MCP thin-wrapper conversion
`api/src/services/mcp_server/tools/events.py`: 920 → 662 lines, renamed to `bifrost_` prefix. **Unconfirmed**: did not find an old tool-level permission check distinct from the router's (the old tools appear to have delegated to the same repository the router used) — flagged as unconfirmed rather than "clean" given the file's size.

### (c) New authorization or validation decisions
1. **`get_subscription` (`events.subscriptions.get`) is an entirely new endpoint** (`api/src/routers/events.py` ~lines 495-533) — `CurrentSuperuser`-gated, 404 if source/subscription missing. Confirms R1a's independent finding that this route doesn't exist on main. — 100% new surface, not a port.
2. **New Event Subscription target-validation cluster** — `_validate_target_organization`, `_validate_subscription_scope`, `_validate_subscription_target` (~lines 195-335): target must exist (404), be active/correctly-typed (422), mutually-exclusive type fields (422), and target org must equal the Event Source's org or be global (422). Today: no equivalent inline block found in the pre-image within review budget — treat as likely absent before. — *likely R3 territory* (org-boundary enforcement, landing early as ordinary validation).
3. **New webhook adapter/integration validation on `update_source`** (~lines 815-845): adapter must exist (422), referenced Integration must exist (404), adapter requiring an integration must have one (422). Today: plain field assignment, no validation — a request that silently worked before now 422s. — *not an obvious R3 default*; data-integrity validation, not an authz boundary.
4. **New `_validate_rescoped_subscriptions`** (~line 342) when an Event Source's `organization_id` changes — guards against rescoping a source out from under its subscriptions' org-scope assumptions. Today: rescoping likely just worked.

### (d) Other new side effects
- `emit_audit(...)` on source create/update/delete and subscription create/update/delete (6 new call sites) + paired `RepoSyncWriter(db).regenerate_manifest()`.
- New adapter `subscribe`/`unsubscribe` HTTP calls to the external webhook provider triggered from `update_source` when adapter/integration/config fields change (~lines 850-870) — network side effects that didn't fire on a plain config update before.

### (e) Tests
Shared `test_mcp_parity.py` plus generic catalog/inventory/thin-wrapper/CLI-surface tests. **No dedicated test found (within review budget) asserting the target-validation cluster or the adapter-swap validation by name** — needs one before this domain's R1b PR.

---

## workflows (8799f4c06)

**Caveat:** this diff is unusually large (4719 lines for the router alone); reviewed standout hunks, not every line — treat as a starting point, not exhaustive.

### (a) Catalog/route metadata
9 decorators (list/execute/validate/register/update/roles.grant/roles.revoke/delete) — plus one on the brand-new `get_workflow` endpoint (see (c)).

### (b) MCP thin-wrapper conversion
`api/src/services/mcp_server/tools/workflow.py`: 692 → 543 lines, renamed to `bifrost_` prefix. **Unconfirmed** whether old tools had independent permission logic vs. the router — not verified within time budget.

### (c) New authorization or validation decisions
1. **`get_workflow` (`workflows.get`) is an entirely new endpoint** — uses `WorkflowRepository.get(id=...)` with tenant/role/Solution-visibility cascade, 404 if not found. Confirms R1a's independent finding. — new surface, not a port.
2. **`list_workflows` rewritten from an inline org-filter switch to `WorkflowRepository.list()`**. Today: org filtering only (ALL/GLOBAL_ONLY/ORG_ONLY/ORG_PLUS_GLOBAL — no role check). Proposed: delegates to `WorkflowRepository.list()`, which per `api/src/repositories/README.md` also applies a role filter for non-superusers. **This can narrow what a non-admin sees today — a real visibility change, not cosmetic.** — *likely R3 territory* in spirit (role-based visibility is exactly what the boundary/capability model formalizes), but landing here ahead of R2/R3. **The single most consequential item found in this whole triage besides the apps mutation-authorization tightening — flag for explicit Jack sign-off, and get a named test asserting the before/after visibility set before this ships.**

### (d) Other new side effects
`emit_audit(...)` + `RepoSyncWriter(db).regenerate_manifest()` pairs at register, update, deactivate/delete, role grant, role revoke (5 locations) — consistent with every other domain's pattern; not individually verified for correctness beyond confirming they're new.

### (e) Tests
Touches `test_mcp_thin_wrapper.py`, `test_mcp_parity.py`, `test_workflow_mcp_name_migration.py` (new), several MCP gateway/middleware/tool-access unit tests, golden manifest-codec fixtures. **No test found (within review budget) asserting the `list_workflows` role-filter visibility change by name** — this is the item most needing one before R1b workflows lands.

---

## organizations (6d47c2a85)

### (a) Catalog/route metadata
5 decorators (list/create/get/update/delete), purely additive — router diff has zero other changes besides one import line.

### (b) MCP thin-wrapper conversion
`list_organizations`/`get_organization`/`create_organization` rewritten to thin wrappers — previously used the ORM directly with no visible admin-only enforcement of their own inside the tool body (relied on tool-registration gating). `update_organization`/`delete_organization` were already thin wrappers from an earlier effort; untouched here except a cosmetic `_rest_error` dedup. Net: create/list/get now rely on the REST router's `CurrentSuperuser` gate instead of having no explicit gate of their own.

### (c) New authorization or validation decisions
None found. Router diff is decorator-only.

### (d) Other new side effects
None found (organizations already had `emit_audit` before this commit).

### (e) Tests
New: `test_organization_mcp_name_migration.py`, `test_cli_orgs.py`, entries in `test_mcp_parity.py`. Touched but generic (incidental CLI-surface-count fixtures): `test_cli_base.py`, `test_tool_access.py`, `test_agent_executor_tools.py`, `test_cli_org_flags.py`, `test_cli_surface_smoke.py`, `test_mcp_thin_wrapper.py`, `test_skill_cli_claims.py`.

---

## integrations (d727af8f0)

### (a) Catalog/route metadata
23 decorators across integrations/config/mappings/oauth/test/generate_sdk — additive, but two endpoints carry real behavior changes alongside their decorator (see (c)).

### (b) MCP thin-wrapper conversion
`api/src/services/mcp_server/tools/integrations.py`: 412 → 387 lines, renamed to `bifrost_` prefix. **Important nuance**: the old `list_integrations` MCP tool (~line 62, `if context.is_platform_admin or not context.org_id:`) already independently implemented org-scoped visibility for non-admins directly via ORM — MCP was already *more permissive* than the REST router's `CurrentSuperuser`-only gate. This commit's REST change (item (c)-1) makes REST match what MCP was already doing, not a novel capability from scratch.

### (c) New authorization or validation decisions
1. **`list_integrations` REST endpoint widened from platform-admin-only to any active internal user, org-scoped**. Today: `CurrentSuperuser` — only platform admins can call `GET /api/integrations`. Proposed: any non-external, non-embed user can list integrations mapped to their own org (explicit 403 for external/embed users; `organization_id = None if is_platform_admin else ctx.org_id`). As noted in (b), this brings REST in line with MCP's pre-existing looser behavior rather than being novel — but it's still a real REST-surface widening: some caller class that can't hit this endpoint today, will be able to tomorrow. — judgment: **unsure** whether R3 would do this same widening by default (plausible given the default-roles table's "org member home access", but not automatic).
2. **New 409 Conflict guard on `update_integration` when config-schema keys would be removed** — counts affected `Config` rows and 409s (`"integration_schema_removal_requires_confirmation"`) unless `force_remove_keys=true`. Today: a shrunk `config_schema` list presumably just applied silently (deleting schema rows cascades to Config values, per CLAUDE.md's own "non-destructive upsert" note). — *not an obvious R3 default*; a destructive-change confirmation UX, arguably a bugfix matching the repo's own philosophy, but new behavior a caller could be relying on today (silent removal).

### (d) Other new side effects
`emit_audit(...)` + `RepoSyncWriter(ctx.db).regenerate_manifest()` on create_integration, update_integration, create_mapping, update_mapping (4 new pairs).

### (e) Tests
Shared `test_mcp_parity.py` plus generic catalog/inventory/thin-wrapper/CLI tests. **No dedicated test found by name asserting either the widened `list_integrations` access or the config-schema-removal 409** — both need one before this domain's R1b PR.

---

## workspace_files (11fe6f128)

### (a) Catalog/route metadata
~18 decorators across read/write/delete/list/exists/stat/patch/manifest/watch/watchers/editor.* — additive.

### (b) MCP thin-wrapper conversion
Workspace-file MCP surface lives in `api/src/services/mcp_server/tools/code_editor.py` (~1000 diff lines, the largest MCP file in the whole set) — heavily rewritten. **Unsure**: no explicit removed permission/role check found in the reviewed window (mostly UI/pending-deactivation string building), suggesting the old implementation may have already deferred file-content authorization to the workspace/file-policy layer, but this was not read exhaustively. **Needs a direct old-vs-new diff read before approving this domain's R1b PR.**

### (c) New authorization or validation decisions
1. **New `assert_workspace_path_not_solution_managed`** (`api/src/services/solutions/guard.py:159`), wired into existing `POST /api/files/write` and `POST /api/files/delete`. Today: writing/deleting a workspace file inside a Solution-managed App's `repo_path` succeeds. Proposed: 409 Conflict. Rationale (from the diff's own docstring): "Workspace file endpoints write S3 and `file_index` directly, so the ORM flush guard cannot see them." — *likely R3 territory* (extends the Solution-managed write guard to workspace file writes), but it's a real behavior change on two **existing** endpoints — needs its own approval line, not a free pass for "looking like R3's job."
2. **`patch_workspace_file` is an entirely new endpoint** (`POST /api/files/patch`) with new 404/409(version_conflict)/409(string_not_found)/400(binary) logic. Confirms R1a's decision to drop `workspace.files.patch` from the ported catalog — no approval needed for R1a.

### (d) Other new side effects
**Not exhaustively inventoried** given the diff's size (5452 lines) — flagged as incomplete coverage, not "none found," for this domain specifically.

### (e) Tests
`test_workspace_file_mcp_name_migration.py` (new), `test_solution_guard.py`, `test_mcp_solution_managed.py`, `test_mcp_thin_wrapper.py`, `test_mcp_parity.py`, `test_workspace_superuser_only.py`, `test_code_editor_tools.py`, `test_cli_files.py`. Did not confirm which test specifically asserts the new solution-managed-guard 409s.

---

## executions (1676eff45)

### (a) Catalog/route metadata
2 decorators (`executions.list`, `executions.get`) — additive, no other router changes.

### (b) MCP thin-wrapper conversion
`api/src/services/mcp_server/tools/execution.py`: ~173 → 124 lines. Old and new both route through the identical `ExecutionRepository` class defined in `api/src/routers/executions.py` (same "non-superusers can only see their own execution" check either side) — **no behavior change identified**.

### (c) New authorization or validation decisions
None found.

### (d) Other new side effects
None found.

### (e) Tests
`test_execution_tools.py` (new), `test_mcp_parity.py`, `test_tool_access.py`.

**Assessment: clean, low-risk R1b candidate.**

---

## knowledge (e6810d6ed)

### (a) Catalog/route metadata
1 decorator: `knowledge.search` on the new `POST /api/knowledge/search` route in a brand-new file (`api/src/routers/knowledge.py`). Does not touch `knowledge_sources.py` at all.

### (b) MCP thin-wrapper conversion
`api/src/services/mcp_server/tools/knowledge.py` touched but not read exhaustively — this commit's dominant content is new-feature work, not a wrapper conversion. **Needs its own review pass in R1b, not a rename-only assumption.**

### (c) New authorization or validation decisions — not primarily a canonicalization commit
This commit adds a **brand-new router and endpoint**, consistent with R1a's independent finding that it doesn't exist on main. All of the following is novel — there's no prior behavior to compare against:
1. `api/src/routers/knowledge.py:~74`: external users get a hard 403 on knowledge search.
2. `api/src/routers/knowledge.py:~50-92` (`_agent_search_boundary`): when `agent_id` is passed, namespaces are clamped to `agent.knowledge_sources`; requesting a namespace the Agent isn't granted → 403. This is genuinely new product surface (agent-bound knowledge access) — closer to a feature decision than an authorization-cutover default.
3. `api/src/routers/knowledge.py:~96-108`: direct (non-agent) search uses the canonical `resolve_effective_scope` resolver → `ScopeNotAllowed` → 403 — this piece **is** exactly the R3-style scope-resolution pattern, even though the endpoint it lives on is new.

Also wires knowledge search into agent tool execution (`agent_executor.py`, `execution/agent_helpers.py`, `execution/autonomous_agent_executor.py`) — further outside a pure canonicalization scope, not reviewed in depth here.

### (d) Other new side effects
Not reviewed in depth — flagged as incomplete given (c)'s scope already showing substantial new-feature work.

### (e) Tests
`test_knowledge_search.py`, `test_mcp_knowledge_scoping.py`, `test_cli_knowledge.py`, `test_knowledge_mcp_name_migration.py`, `test_search_budget.py`, plus several agent-executor test files. Also touches `client/src/lib/v1.d.ts` and `client/src/components/agents/AgentSettingsTab.tsx`.

**Assessment: this is not a standard R1b "port the rename" candidate. It likely needs its own product/design decision from Jack before it goes anywhere.**

---

## roles (b25d00cf8)

### (a) Catalog/route metadata
5 decorators (list/create/get/update/delete) — purely additive.

### (b) MCP thin-wrapper conversion
`api/src/services/mcp_server/tools/roles.py` was **already** a thin HTTP-bridge wrapper before this commit (the CLAUDE.md-cited canonical exemplar). This commit only renames the five tools and updates the lookup tables. No logic change, no permission-check removal — there was none to remove.

### (c) New authorization or validation decisions
None found.

### (d) Other new side effects
None found.

### (e) Tests
`test_role_mcp_name_migration.py` (new), `test_mcp_parity.py`, `test_operation_catalog.py`, `test_mcp_thin_wrapper.py` — minor updates, no new-behavior assertions.

**Assessment: clean, low-risk R1b candidate.**

---

## platform_jobs (99555955c)

### (a) Catalog/route metadata
1 decorator (`GET /api/platform-jobs/{job_id}` → `platform.jobs.get`) — purely additive.

### (b) MCP thin-wrapper conversion
The old tool `bifrost_get_app_publish_status` (in `apps.py`) was **already** a thin wrapper calling the same REST endpoint via `call_rest`. This commit just moves it into a new `platform_jobs.py` module under the canonical name `bifrost_get_platform_job` — same body, same "requester or platform admin" enforcement delegated to REST either side. No logic change.

### (c) New authorization or validation decisions
None found.

### (d) Other new side effects
None found (read-only operation; no audit, no manifest regen).

### (e) Tests
`test_mcp_parity.py` (+`TestMcpParityPlatformJobs`, 2 tests), `test_platform_job_mcp_name_migration.py` (new, upgrade/downgrade tested), `test_mcp_thin_wrapper.py`, `test_operation_catalog.py`, `test_operation_inventory.py` — no new-behavior assertions needed since there's no new behavior.

**Migration:** `20260817_platform_job_mcp_tool_names.py` (revision `20260817_platform_job_names`) renames `bifrost_get_app_publish_status` → `bifrost_get_platform_job` in `agents.system_tools` and `system_configs`. Upgrade+downgrade tested.

**Assessment: clean, low-risk R1b candidate.**

---

## claims (5c819bc6e)

### (a) Catalog/route metadata
5 decorators (list/get/create/update/delete) — purely additive.

### (b) MCP thin-wrapper conversion
The five tools were **already** thin wrappers over `/api/claims`. This commit only renames them. No logic or permission-check change — the router's `CurrentSuperuser` dependency was already the sole gate on both sides.

### (c) New authorization or validation decisions
None found. The catalog's declared `authorization_resolver="Platform-admin gate"` documents the pre-existing dependency, not a change.

### (d) Other new side effects
None found — the catalog's declared `side_effects` (source-table validation, referenced-claim validation, dependency-cycle rejection, table-policy-reference-blocks-delete) document pre-existing router/service behavior, confirmed by no matching diff hunks in `api/src/routers/claims.py` beyond decorator+import lines.

### (e) Tests
`test_mcp_parity.py` (+`TestMcpParityClaims`), `test_claim_mcp_name_migration.py` (new, upgrade/downgrade), `test_operation_catalog.py`, `test_mcp_thin_wrapper.py` — names only.

**Migration:** `20260818_claim_mcp_tool_names.py`, chained after `20260817_platform_job_names`. Renames all 5 tool names. Upgrade+downgrade tested.

**Assessment: clean, low-risk R1b candidate.**

---

## file_policies (15191f62e)

### (a) Catalog/route metadata
4 decorators (list/get/set/delete) — purely additive. `POST /api/files/policies/test` deliberately left uncatalogued (no CLI/MCP counterpart).

### (b) MCP thin-wrapper conversion
The four policy tools were **already** thin wrappers, renamed to canonical form. Only behavior-adjacent change: three tools gained a `logger.info(...)` call previously only on `list` — logging only, not authorization.

### (c) New authorization or validation decisions
None found. Two accompanying fixes are infra, not authorization: a CLI-naming-rule fix in `operation_catalog.py` (nested-resource singularization) and a route-path-matching fix in `operation_inventory.py:67-71` (`{policy_path:path}` converter handling) — both accounting fixes with no runtime effect.

### (d) Other new side effects
None found.

### (e) Tests
`test_mcp_parity.py` (+94), `test_file_policy_mcp_name_migration.py` (new, upgrade/downgrade), `test_operation_catalog.py`, `test_mcp_thin_wrapper.py`.

**Migration:** originally `20260818_file_policy_mcp_tool_names` — that revision id (35 chars) overflowed the `alembic_version` varchar(32) column and broke startup after being applied; shortened to `20260818_file_policy_names`, chained after `20260818_claim_mcp_tool_names`. **Whoever re-dates these migrations for R1b should keep the short form and note why**, since the long form is a real footgun (silently breaks startup, not a test failure). Renames 4 tool names, upgrade+downgrade tested.

**Assessment: clean, low-risk R1b candidate. Watch the revision-id-length footgun when re-dating.**

---

## configs (0ce412d71)

### (a) Catalog/route metadata
4 of 5 decorators are purely additive (list/create/update/delete). The 5th, `configs.get`, is not metadata-only — see (c).

### (b) MCP thin-wrapper conversion
Tools were already thin wrappers, renamed to `bifrost_<verb>_config`. `get_config`'s implementation changes as a consequence of (c) — reads the new by-ID endpoint instead of fetching the whole list and filtering client-side. Same end-user-visible result (platform-admin-gated single-config read); the `CurrentSuperuser` gate itself doesn't change.

### (c) New authorization or validation decisions
1. **New `get_config_by_id`** (`api/src/routers/config.py:73-96`): brand-new `GET /api/config/{config_id}` endpoint. Today: no such endpoint; three callers (`bifrost configs get`, MCP `get_config`, the secret-type check inside `bifrost configs delete`) each fetched the entire list and filtered client-side. Proposed: real single-row lookup, same `CurrentSuperuser` gate as every other config route — the authorization *decision* is unchanged, but a genuinely new *capability* is added. — judgment: **likely R3 territory** in that R3 will re-verify this route's authorization anyway, but adding the endpoint at all is new work, not a pure refactor.
2. **Same-day self-correction, `3564d350c`**: the just-added `get_config_by_id` filtered by `organization_id == org OR organization_id IS NULL` (the name-cascade shape), even though `OrgScopedRepository.get()` deliberately does not cascade ID lookups. Before the fix: a platform admin got 404 reading another org's config by ID, even though the sibling update/delete routes (ID-only, no org clause) let that same admin PUT/DELETE it. After the fix: `get` matches its siblings' semantics. — judgment: **novel, not an obvious R3 default** — a same-day self-correction of new code from the same slice; whoever ships `configs.get` for real should port the corrected version directly, not replay the bug and then the fix as two steps.

### (d) Other new side effects
Dead-code removal (`_find_config_by_id` helper, obsolete after the endpoint lands) — refactor, not a new side effect. No `emit_audit` — configs routes are documented as emitting no audit events (not a gap introduced here). Configs' manifest surface is declared as an **exclusion**, not a binding (`.bifrost/configs.yaml` is export-only, no `_resolve_config` in `github_sync.py`) — accounting, not a code change.

### (e) Tests
`api/tests/e2e/api/test_config.py` (33 tests, including secret-masking through the new endpoint and non-superuser-403). `3564d350c` adds `test_admin_can_read_any_org_config_by_id`, directly asserting the corrected behavior (verified against the bug: 404 with cascade, 200 without). `test_config_mcp_name_migration.py` (migration + revision-length coverage), `test_operation_inventory.py` (hardcoded REST count fix folded in).

**Migration:** `20260819_config_mcp_names`, chained after `20260818_file_policy_names`. Renames 5 tool identifiers.

---

## policy_rules (2b1803831)

### (a) Catalog/route metadata
5 of 6 decorators are additive (create/list/update/delete/list_usages). The 6th, `policy.rules.get`, is new capability — see (c). Also includes a breaking CLI rename (see below).

### (b) MCP thin-wrapper conversion
The 3 pre-existing tools (list/create/delete) were already thin wrappers; renamed to canonical form with a shared `_rule_url` helper factored out (refactor, no logic change). 3 **new** tools (`get`/`update`/`list_usages`) are thin wrappers from birth — no prior custom-logic version to compare, so no "old check removed" to cite; they inherit whatever the (also new, for `get`) REST handler enforces.

### (c) New authorization or validation decisions
1. **New `get_policy_rule`** (`api/src/routers/policy_rules.py:81-96`): `GET /{domain}/{name}`, genuinely new REST capability, same `CurrentSuperuser` gate as siblings. Today: no such route; the CLI `get` leaf listed every rule and filtered client-side. Backed by a new public `PolicyRuleService.get` (`api/src/services/policy_rule_service.py:110-116`) delegating to the existing private `_get`.
2. **Read-wider-than-write for solution-managed rows** (`policy_rule_service.py:114-115`, docstring): "Solution-managed rules are readable through it because the solution-managed guard blocks writes only — a caller may inspect a rule it cannot modify." Today: no read path exists to have this behavior at all (net-new). — judgment: **likely R3 territory** — "read wider than write" for solution-managed rows is a reasonable default several domains already have implicitly (e.g. claims), so R3 would probably land the same answer, but it's the first place this becomes observable behavior for policy rules.

### (d) Other new side effects
Catalog declares `audit_event` for create/update/delete, documenting **pre-existing** `emit_audit` calls (confirmed: no *new* `emit_audit`/`RepoSyncWriter` lines added to `policy_rules.py` by this commit — these were already present).

### (e) Tests
`test_mcp_parity.py` (+219, 7 new tests including the `new_name` rename mapping and (domain,name) scoping), `test_cli_policy_rules.py` (updated for CLI renames), `test_policy_rule_mcp_name_migration.py` (new — explicitly asserts the migration renames only the 3 pre-existing tool IDs and does **not** touch get/update/list_usages, since those never had a legacy name), `test_operation_catalog.py`, `test_operation_inventory.py`, `test_mcp_thin_wrapper.py`.

**User-visible breaking CLI renames** (flag per program instructions): CLI group `policy-rule` → `policy-rules`; leaf `usages` → `list-usages`. The reference-branch commit message says both land in an already-open breaking-change window (`MIN_CLI_VERSION` unreleased vs. latest tag), so `MIN_CLI_VERSION` isn't bumped further there — **re-verify against main's actual current `MIN_CLI_VERSION`/tag state before assuming that's still true when this ships.**

**Migration:** `20260819_policy_rule_names`, chained after `20260819_config_mcp_names` (single head across the whole reference-branch chain: platform_jobs → claims → file_policies → configs → policy_rules).

---

## Summary table

| Domain | New auth/validation decisions | New side effects | MCP behavior change | Client touched | Risk tier |
|---|---|---|---|---|---|
| agents | 8 items, incl. 2 silent-drop→403 reversals | audit + manifest ×3 | yes (bespoke scoping removed) | yes | High |
| forms | 2 items (1 is data-semantics, not auth) + null-PATCH-semantics change | audit + manifest ×3 | yes | no | Medium |
| tables | 3 items, incl. new rescoping capability | audit + manifest ×3, new CLI flag | yes | yes (generated types) | Medium |
| apps | 4 items, incl. an 11-site mutation-authz tightening | audit ×6, manifest ×5 (1 gap?) | yes | yes | **Highest** |
| events | 4 items, incl. new external side effects (webhook sub/unsub) | audit + manifest ×6 | unconfirmed | no | High |
| workflows | 2 items, incl. a visibility-narrowing change | audit + manifest ×5 | unconfirmed | no | High |
| organizations | none | none | yes (no-op gate change) | no | Low |
| integrations | 2 items, incl. a REST-widening | audit + manifest ×4 | yes (REST catches up to MCP) | no | Medium |
| workspace_files | 1 confirmed (solution-managed guard on 2 existing endpoints) + incomplete review | incomplete review | unsure | no | Medium (needs deeper read) |
| executions | none | none | no | no | **Low — clean** |
| knowledge | not a canonicalization commit; new product surface | not reviewed | unsure | yes | Needs its own decision |
| roles | none | none | no | no | **Low — clean** |
| platform_jobs | none | none | no | no | **Low — clean** |
| claims | none | none | no | no | **Low — clean** |
| file_policies | none | none | no (+logging) | no | **Low — clean** (watch revision-id length) |
| configs | 1 new endpoint + 1 same-day self-correction | none | no | no | Medium |
| policy_rules | 1 new endpoint + 1 read-wider-than-write default | none (pre-existing) | partial (3 new tools have nothing to compare) | no | Medium |

Suggested first R1b batch if Jack wants to validate the review pattern cheaply before the harder
domains: **executions, roles, platform_jobs, claims, file_policies** — five clean rename-only
domains, zero items in bucket (c), lowest review cost. **agents, apps, workflows, events** carry
the most consequential (c) items and deserve the most scrutiny; **knowledge** likely isn't an R1b
domain at all in the sense the others are.
