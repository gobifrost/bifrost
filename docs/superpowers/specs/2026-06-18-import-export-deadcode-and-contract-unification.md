# Import/Export Dead-Code Map + Write-Contract Unification

**Date:** 2026-06-18
**Branch investigated:** `origin/main` @ `9a76c95c` (Solutions PR #347 merged 2026-06-17)
**Status:** Deep-dive findings + proposed design — awaiting approval before any refactor

> ⚠️ Scope note: the primary `bifrost` checkout was 29 commits behind `origin/main` and
> predated the Solutions merge. All findings here are verified against a fresh worktree off
> `origin/main`. Anything claimed about "old main" elsewhere is stale.

---

## 1. The question

After the Solutions work merged, Jack expected the old import/export system to be
deprecated, but noticed (a) manifest/Solutions writes don't follow the same API contract
rules as the REST routers (POST-vs-PATCH decided by bespoke logic), and (b) concrete field
drops — agent **tool description** not carried forward, and agent **name** behaving
differently in Solutions than elsewhere. Before unifying the write contracts, he wants to
know **what is actually dead** so we don't refactor unused code.

---

## 2. Dead-code map (verified, with evidence)

### 2.1 Already deleted (clean — no orphan references)

| Item | Status | Evidence |
|---|---|---|
| `api/bifrost/commands/export.py` (`bifrost export`) | **GONE** | File absent; no importer anywhere |
| `api/bifrost/commands/import_cmd.py` (`bifrost import`) | **GONE** | File absent; no importer |
| `api/bifrost/portable.py` (scrub pipeline) | **GONE** | File absent; no importer |

The portable CLI bundle story (`bifrost export --portable` / `bifrost import`) is **already
removed**. Don't resurrect or refactor it.

### 2.2 Live — keep

| Item | Status | Live caller |
|---|---|---|
| `manifest_generator.py` (`generate_manifest`, `serialize_*`) | **LIVE** | git-sync, `entity_change_hook` (CLI watch), `solutions/capture.py` |
| `manifest_import.py` (`import_manifest_from_repo`, `ManifestResolver`, `_*_content_from_manifest`) | **LIVE** | git-sync reconciliation; **Solutions deploy** reuses the content-builder helpers |
| `github_sync.py` | **LIVE** | scheduler background sync jobs |
| `AgentIndexer` / `FormIndexer` | **LIVE** | git-sync **and** Solutions deploy both delegate here |
| `solutions/*` (deploy, capture, guard, export, git_sync, integration_template) | **LIVE** | the Solutions feature |

### 2.3 Live UI feature — separate system, NOT the manifest path

`api/src/routers/export_import.py` → `/api/export-import/*` (export/import of **knowledge,
configs, tables, integrations, "all"-zip**). **LIVE**, called from `client/src/services/exportImport.ts`
(Knowledge, Config, Tables, Integrations, Maintenance pages + ImportDialog).

This is a **third** system, distinct from both the manifest path and Solutions. It is a
per-entity JSON/ZIP backup-and-restore for the *operator's own* environment, with its own
secret re-encryption (`encrypt_secret`/`decrypt_with_key`) and org rebinding.

**Redundancy evaluation (Jack asked for this):**

- **Overlap with Solutions is substantial.** Solutions capture/export already handles **tables
  incl. row data** (`capture.py` `include_data`, per-table row cap at `capture.py:78`,
  `Document` rows), **configs incl. secrets** (decrypt → `secrets.enc` blob via
  `secrets_blob.py`), and **integrations** (secret-scrubbed templates). So
  `/api/export-import/{tables,configs,integrations}` re-implements capabilities Solutions now
  has — including a **second independent copy of the secret-encrypt + org-rebind logic**.
- **Knowledge is NOT covered by Solutions.** Solutions only carries agent `knowledge_sources`
  *references*, never `KnowledgeStore` contents. `/api/export-import/knowledge` has **no
  Solutions equivalent** and must survive regardless.
- **They are not wired together.** `solutions.py` imports only `solutions.export.build_workspace_zip`;
  it never touches the `export_import` router. Parallel implementations, not shared code.
  > `[corrected 2026-06-19]` "second independent copy of the secret-encrypt logic" **overstates
  > it.** They are *parallel re-implementations sharing only `src.core.security` primitives*, not
  > a literal copy: export-import re-encrypts source-key→dest-key (`export_import.py:729-730`),
  > Solutions uses a scrypt password envelope (`secrets_blob.py`). Overlap is on *entity
  > coverage*, not orchestration. (Verified §7.)
- **Verdict:** `/api/export-import` is a *fourth* parallel write/field-mapping system and a
  *second* secret-handling implementation. Tables/configs/integrations there are largely
  redundant with Solutions for *distribution*, but the router still serves an *operator
  self-backup* use case (export your own env to JSON, re-import later) that Solutions'
  package-centric model doesn't directly replace — and Knowledge pins it alive. Recommendation:
  **keep for now, do not fold into this effort**, but flag it as the next consolidation
  candidate once contract-unification (below) proves the pattern — at minimum its
  tables/configs/integrations field mapping and secret handling should eventually route through
  the same source of truth rather than a fifth bespoke copy.

### 2.4 Likely-orphaned endpoint (flag, don't delete yet)

`POST /api/files/manifest/import` (`files.py:457`): now that the CLI `import` command is
deleted, **no external caller remains** — it appears only in generated `client/src/lib/v1.d.ts`,
with no `client/src` fetch and no `api/bifrost` caller. The only non-test caller is the router
handler itself, which calls `import_manifest_from_repo` (`files.py:493`). So the *endpoint
wrapper* is orphaned even though `GET /api/files/manifest` is still used (CLI watch / git-sync
state export). **Classification: unclear-leaning-dead; verify with a request-log check before
removing.**

> `[corrected 2026-06-19]` Earlier this section said "git-sync calls `import_manifest_from_repo`
> directly." **That is wrong.** git-sync bypasses *both* the HTTP wrapper **and**
> `import_manifest_from_repo` — it instantiates `ManifestResolver` directly
> (`github_sync.py:54-56,279`) and drives `_diff_and_collect` (`:1146`). The two import paths
> share only the `ManifestResolver` class, not that function. The endpoint is still orphaned —
> just for a different reason than originally stated. (Verified §7.)

---

## 3. How many "decide POST vs PATCH" systems exist

This is the heart of Jack's concern. Counting distinct create-vs-update mechanisms across the
codebase for the **overlapping** entities (agents, forms, tables, configs, apps, integrations,
workflows, events):

1. **REST routers** — HTTP verb decides. `POST` = insert (new UUID), `PUT/PATCH` = update by
   id. Pydantic DTO validation up front (`AgentCreate.name` is `min_length=1`, etc.). Writes
   via ORM constructor / setters (agents, forms) or a repository (tables, configs, apps).
2. **Manifest import — `Upsert(match_on="id")`** (`sync_ops.py`). Used for agents (metadata),
   forms (metadata), apps, configs, workflows, claims, config-schemas. SELECT-by-id →
   UPDATE-or-INSERT.
3. **Manifest import — natural-key two-pass** (`_resolve_table`, `_resolve_custom_claim`,
   `_resolve_workflow`, `_resolve_integration`): try `(name|path, org)` → realign id → else id
   → else insert. Exists because portable bundles carry different UUIDs across environments.
4. **Direct `INSERT ... ON CONFLICT`** — event sources/subscriptions, table insert, integration
   nested rows (config schema, oauth provider, mappings). Executed immediately, not deferred.
5. **Indexer `ON CONFLICT DO UPDATE`** — `AgentIndexer.index_agent`, `FormIndexer.index_form`.
   This is where agents/forms *content* (description, channels, tools, schema) actually lands,
   for **both** git-sync and Solutions deploy.
6. **Solutions deploy** adds two *installation-level* gates on top of #2/#5: per-install
   `uuid5` id remap + an **ownership guard** (`guard.py` / `_guard_owner`: a row's `solution_id`
   must be NULL or this install) + scoped full-replace deletion (`WHERE solution_id = sid AND
   id NOT IN bundle`). These are legitimately Solutions-specific and should **not** be unified
   away — they enforce the one-writer-per-install invariant git-sync doesn't need.

**So: ~5 create-vs-update implementations for the same entities** (6 counting Solutions'
overlay), three of which (#2, #3, #5) live inside `manifest_import.py` alone. The REST contract
(#1) is a separate sixth that the others were supposed to mirror but drifted from.

> `[corrected 2026-06-19]` The verified count is **8 mechanisms, not ~5-6** — this section
> undercounted by missing two surfaces:
> - **MCP tools** (`mcp_server/tools/agents.py:322`, `forms.py:308`, `tables.py:288`,
>   `apps.py:188`) write the ORM **directly** with hand-rolled name validation and — critically
>   — **drop the role-sync side-effect** (`sync_agent_roles_to_workflows` /
>   `sync_form_roles_to_workflows`) that the REST routers run. agents/forms/tables/apps are
>   *deliberately exempt* from the thin-wrapper parity test (`test_mcp_thin_wrapper.py`
>   `PARITY_HANDLERS`). **This contradicts the spec's later assumption that "CLI/MCP/manifest all
>   read from the same DTOs" — MCP does not, for these four entities.** This must be in scope.
> - **`/api/export-import`** uses its own natural-key `db.add` upsert (mechanism #7).
>
> Also note the inventory splits manifest_import's writers more finely than this section did
> (Upsert, natural-key two-pass, `ON CONFLICT DO UPDATE`, *and* in-place attribute-mutation are
> distinct). The file-write/watch path is **not** a create mechanism — `WorkflowIndexer` is
> update-only and cannot insert. Full 8-mechanism table in §7.

---

## 4. The actual field divergences (Jack's two bugs, root-caused)

The earlier sub-agent reports disagreed; I read the code to settle it. **Both** the
"description is lost" claim and the "no divergence at all" claim were wrong. Reality:

Agents flow through **two write stages** in the manifest/Solutions path:
`ManifestAgent` → `_agent_content_from_manifest()` (YAML) → `AgentIndexer.index_agent()`
(content: name, description, system_prompt, channels, knowledge_sources, system_tools,
llm_model, llm_max_tokens, tools, delegations) → then deploy stamps scope + `max_iterations` /
`max_token_budget` / `mcp_connection_ids`. So description/channels/tools **do** round-trip.
The `_resolve_agent` `Upsert` is metadata-only and runs *alongside* the indexer, not instead
of it — that's what confused the static reads.

### 4.1 Tool description — REAL end-to-end drop (Jack's bug #1) ✅ confirmed

- A tool's LLM-facing description is `Workflow.tool_description` (`workflows.py:91`), **not** an
  agent field. The agent only stores `tool_ids` (workflow UUID bindings).
- `tool_description` is set **only** via the REST/UI workflow path (`workflows.py:1581-1582`).
  The workflow indexer comment is explicit: *"tool_description ... are API/UI-only — never set
  from code."* (`indexers/workflow.py:190`).
- **`tool_description` is absent from `ManifestWorkflow`, from `manifest_generator` (export),
  from `manifest_import` (import), and from Solutions deploy.** Grep returns zero hits in all
  four.
- **Consequence:** capture a solution → deploy it → every tool reverts to the workflow's plain
  `description`; the curated `tool_description` is silently gone. This is a true contract
  divergence: REST carries the field, the portable/manifest/Solutions chain drops it across the
  board. It is *not* a deploy-vs-REST bug — it's a missing field in the manifest model and its
  serializer/deserializer.

### 4.2 Agent name — REAL behavioral divergence (Jack's bug #2) ✅ confirmed

- REST: `AgentCreate.name` / `AgentUpdate.name` are `Field(min_length=1, max_length=255)` —
  empty/missing name → **422, hard fail** (`contracts/agents.py:56,85`).
- Manifest/Solutions: `_agent_content_from_manifest` emits `name = magent.name or ""`
  (manifest_import.py:295); `AgentIndexer.index_agent` then **silently returns `False` (no DB
  write at all) if name is missing** (`agent.py:70-72`), and otherwise will happily write an
  empty/odd name with **no length/format validation**. The indexer also accepts `tools` as an
  alias for `tool_ids` (`agent.py:153`) — another input shape the REST DTO doesn't have.
- **Consequence:** the same logical agent validates and errors differently depending on which
  door it comes through. That's the "name doesn't work the same in Solutions" symptom.

### 4.3 Other drifts found (same root cause: bespoke writers, not the DTO)

- **Forms (manifest `_resolve_form` metadata Upsert):** sets only name/is_active/created_by/
  org/access_level; relies on `FormIndexer` for description/workflow_id/schema. Workflow
  binding correctness therefore depends on the indexer + inline content being complete, not on
  the `FormCreate` contract.
- **Apps:** manifest defaults `app_model` to `inline_v1`; REST defaults to `standalone_v2`.
  `created_by` "git-sync" vs `user.email`. `icon` not carried by manifest.
- **created_by / timestamps:** manifest paths stamp synthetic `git-sync` / `file_sync` /
  `solution-deploy` and bypass the router's `now`. Mostly cosmetic, but it means audit columns
  mean different things depending on the writer.

---

## 5. What this implies (and what it does *not*)

- The portable-CLI half of the "old system" is **already dead and removed** — no refactor debt
  there.
- The manifest/git-sync core and the `/api/export-import` UI feature are both **live**; neither
  is safe to delete.
- The genuine problem is exactly as Jack framed it: **the write paths don't share the API
  contract.** Multiple bespoke create-vs-update implementations (#2–#5) each re-decide field
  mapping, so fields the REST DTO carries (`tool_description`, name validation, `icon`, form
  workflow binding) drift or drop. Unifying these reduces six decision points toward one source
  of truth and makes the two reported bugs structurally impossible rather than individually
  patched.

---

## 6. Proposed direction (contract unification) — to discuss before planning

**Goal:** all entity writes — REST, git-sync/manifest import, Solutions deploy — agree on
*what fields an entity has* and *how create-vs-update is decided*, by routing through one
contract layer, while preserving the legitimately-distinct concerns (portability id-remap,
Solutions ownership/scope guards).

Three candidate shapes, in increasing ambition:

- **A — Close the specific gaps only (minimal).** Add `tool_description` to `ManifestWorkflow` +
  generator + importer; make `AgentIndexer` validate name like the DTO (reject empty, enforce
  length) instead of silent no-op; align app defaults. Fixes both reported bugs and the worst
  drifts. Does **not** unify the mechanisms — the N writers remain, just better aligned. Lowest
  risk, leaves the structural problem.

- **B — One field-mapping source of truth (recommended).** Make the manifest models and the
  indexers derive their field set from the same `XxxCreate/XxxUpdate` DTOs (the project already
  has `dto_flags.py` enforcing CLI/MCP parity against these DTOs — extend that discipline to
  manifest + indexer). Keep the *upsert mechanics* per-context (REST verb, manifest id-upsert,
  Solutions overlay) but make them all populate the **same validated field set**. A parity test
  fails when a DTO field isn't carried by the manifest/indexer path. This kills the whole class
  of "field X drops in distribution" bugs without collapsing the upsert strategies that
  legitimately differ.

- **C — Single write-service per entity (most ambitious).** Every door (router, manifest,
  deploy) calls one `EntityService.upsert(dto, *, mode)` that owns validation + field mapping +
  insert/update, with mode-specific hooks for id-remap/ownership. Cleanest end state, largest
  blast radius, touches the most live code.

**Decided with Jack (2026-06-18):** Approach **B with A folded in first**, scoped to the live
overlapping entities (agents, forms, workflows, apps, tables, configs, integrations). The two
reported bugs (§4.1, §4.2) ship as the first increment so they're fixed immediately and a
parity test prevents regression while the broader unification lands. Solutions'
ownership/scope guards (`guard.py`) stay exactly as they are. `/api/export-import` is **out of
scope** for this effort (operator-backup use case + Knowledge pins it alive) but recorded in
§2.3 as the next consolidation candidate.

**Remaining open question (one):**
- OK to remove the orphaned `POST /api/files/manifest/import` endpoint wrapper (keeping the
  live `import_manifest_from_repo` service function), pending a prod request-log confirmation
  that nothing external hits it?


---

# 7. VERIFIED end-to-end findings (multi-agent workflow, 2026-06-19)

> The section below is the output of an 18-agent trace+adversarial-verify workflow run
> against this worktree. It **supersedes** the first-pass code-read claims in §2–§6 where
> they conflict. Corrections that change §2–§6 are also patched inline above and flagged
> `[corrected 2026-06-19]`. Both reported bugs are CONFIRMED at code level; a third
> (blank-name agent FK-orphan) was discovered. Live repro of the two runtime-visible bugs
> follows in §8.

# How These Surfaces Are Actually Used

Worktree: `/home/jack/GitHub/bifrost/.claude/worktrees/solutions-deadcode-audit`. Actual HEAD: `377fd522` (the task framing said `9a76c95c`; the code at the present HEAD substantiates every claim below). All file:line citations are from this checkout.

## End-to-end usage map

### Surface 1 — REST API write paths (contract baseline, 7 entities)

| Field | Detail |
|---|---|
| Live? | LIVE |
| UI entry | Agents: `AgentSettingsTab.tsx` (useCreateAgent) + `AgentDetailPage.tsx:106` (useUpdateAgent). Forms: `FormBuilder.tsx:165` (update) / `:193` (create). Tables: `TableDialog.tsx:147/:158`. Configs: `useConfig.ts:52,74`. Apps: `AppInfoDialog.tsx:237/:260`. Integrations: `IntegrationDetail.tsx:114`. Workflows: `WorkflowEditDialog.tsx:259` (update only) |
| Endpoint | `POST/PUT /api/agents[/{id}]` (`useAgents.ts:114,134`), `POST/PATCH /api/forms[/{id}]` (`useForms.ts:173,195`), `POST/PATCH /api/tables` (`services/tables.ts:113,129`), `POST/PUT /api/config[/{id}]` (`useConfig.ts:55,77`), `POST/PATCH /api/applications` (`useApplications.ts:96,119`), `POST/PUT /api/integrations` (`services/integrations.ts:53,68`), `PATCH /api/workflows/{id}` + `POST /api/workflows/register` |
| Service | Agents/forms/workflows write the ORM **inline in the router** then call `sync_agent_roles_to_workflows` (`routers/agents.py:509,838`) / `sync_form_roles_to_workflows` (`routers/forms.py:417,622`). Tables/configs/apps/integrations delegate to `TableRepository`/`ConfigRepository`/`ApplicationRepository`/`IntegrationsRepository` |
| DB write | Agents create `db.add(agent)` `routers/agents.py:392-412`, `db.flush()` `:492`; update in-place setters `:699-727`, `:821`. Forms create `db.add(form)` `forms.py:379-395`; update setters `:554-589`. Workflows register `db.add(new_wf)` `workflows.py:1325-1336` or reactivate `:1311-1321`; update setters `:1437-1618` incl. `workflow.tool_description=request.tool_description` `:1581-1582` |
| Entities | Agent(+AgentTool/Delegation/Role/MCPConnection), Form(+FormField/FormRole), Table, Config, Application, Integration(+ConfigSchema/Mapping), Workflow(+WorkflowRole) |
| Create vs update | Decided by HTTP verb + route, never a payload flag. POST collection → new `uuid4()` row; PUT/PATCH item → load-by-id + mutate. **Config special:** `POST /api/config` UPSERTS by (key, org) in `ConfigRepository.set_config`. **Workflow special:** no generic create — `POST /register` creates from a `.py` source or reactivates an inactive row (409 if already active); `PATCH /{id}` updates |

### Surface 2 — Solutions install/deploy → `SolutionDeployer.deploy`

| Field | Detail |
|---|---|
| Live? | LIVE |
| UI entry | `CreateEditSolution.tsx:910` (installSolution), `:1303` (installSolutionFromRepo), `:873` (preview); `SolutionDetail.tsx:1186` (export), `:1226` (sync). No UI wrapper for async `POST /{id}/deploy` (CLI/server-internal) |
| Endpoint | `POST /api/solutions/install` (`solutions.py:1524`), `/install/from-repo` `:1400`, `/install/preview`, `/deploy` (async job, body at `:927`) `:996`, `/sync` `:1191`, `/{id}/capture` `:1087`, `/{id}/export` `:257` |
| Service | `services/solutions/deploy.py:309 SolutionDeployer.deploy`; per-install remap `_remapped_bundle:493` (uuid5(install_id, manifest_id)); reuses `FormIndexer`/`AgentIndexer` via `_upsert_forms:1229`/`_upsert_agents:1278`; `_form/_agent_content_from_manifest` (`manifest_import.py:275,293`); zip entry `zip_install.py:478` |
| DB write | Per entity: Workflow `Upsert(match_on="id")` `deploy.py:799` (values `:778-795`, **no tool_description**); Table `:959`; Claim `:1004`; App `:1130`; Form `FormIndexer.index_form()` `:1253` then Core `update(Form)` scope `:1268`; Agent `AgentIndexer.index_agent()` `:1300` (`insert(Agent).on_conflict_do_update` agent.py:119-149) then Core `update(Agent)` `:1323`; config-schema Upsert `:1376`; events Core insert/update `:1571/:1577`; integration shells ORM add `:1406-1446` (only if name absent globally); connection decls Core insert/update `:1489/:1498`; role junctions delete-all+add `:651-659`; deletions `_reconcile_one` `:1760` |
| Entities | workflows, tables, apps, forms, agents, events (Event/Schedule/Webhook/Subscription), claims, config_schemas, connection_declarations, integration shells, role junctions, agent_mcp_connections |
| Create vs update | Decided by id presence at the install-remapped uuid5 id. `Upsert(match_on="id")` + indexers' ON CONFLICT. Redeploy reproduces same id → UPDATE; identical bundle in two installs → distinct ids → two INSERTs. `_guard_owner:1674` raises `SolutionDeployConflict` (409) if id exists with a different solution_id. Full-replace: `_reconcile_deletions` sweeps this install's rows absent from the bundle |

### Surface 3 — Git-sync / manifest write path

| Field | Detail |
|---|---|
| Live? | LIVE (git-sync); the standalone `/api/files/manifest/import` is live-but-orphaned (see Surface 3b) |
| UI entry | `SourceControlPanel.tsx:596` (handleSync), `:473` (commit), `:450` (fetch), `:672` (resolve), `:648` (abort), `:774` (discard) → hooks in `useGitHub.ts:302 gitPost` |
| Endpoint | `POST /api/github/{sync,commit,fetch,resolve,abort-merge,discard,changes,diff}` (`github.py:638-839`), all `CurrentSuperuser`, each enqueues a job via `publish_git_operation` (e.g. `:708-715`), returns `GitJobResponse`; results over WebSocket |
| Service | Scheduler consumes the op (`scheduler/main.py:438-542`), builds `GitHubSyncService` (`github_sync.py:260`), which imports `ManifestResolver` directly (`github_sync.py:54-56`, instantiated `:279`). `_import_all_entities` (`github_sync.py:1132`) runs `_resolver._index_agents_from_manifest` `:1232` (and form/workflow indexers) as side-effects |
| DB write | (1) `SyncOp.Upsert` (`sync_ops.py:71-127`): UPDATE WHERE id (or name); rowcount==0 → INSERT — used by `_resolve_form` `manifest_import.py:2886`, `_resolve_agent` `:2937`, `_resolve_config` `:2245/2264`, `_resolve_app` `:2315/2322`. (2) Direct Core: `_resolve_table` `:2407/2429/2443`, integration config-schema `:2078/2072-2076/2091`, OAuthProvider `:2111`. Content via indexers: `AgentIndexer` `agent.py:119` `insert(Agent).on_conflict_do_update` (set_ `:133-147`) |
| Entities | Form, Agent, Config, Application, Table, Integration(+ConfigSchema/Mapping), OAuthProvider, Workflow, Organization, Role/UserRole, CustomClaim, EventSource, MCPServer/Connection, role junctions |
| Create vs update | Form/Agent/Config/App: `SyncOp.Upsert` by UPDATE-WHERE-id rowcount (`sync_ops.py:108-115`). Config pre-checks a natural-key cache (`:2212,2223`) + realigns id; SECRET configs with non-null value skipped (`:2227`). App by slug (`:2298`). Table two-pass natural key (`:2400` → realign; else by-id `:2428`; else `insert().on_conflict_do_nothing` `:2443`). Indexers ON CONFLICT on id PK |

### Surface 3b — `POST /api/files/manifest/import` (orphaned wrapper)

| Field | Detail |
|---|---|
| Live? | LIVE-but-orphaned (no external caller) |
| UI entry | NONE — `v1.d.ts:2696/2709` are generated types only; no client fetch |
| Endpoint | `POST /api/files/manifest/import` (`routers/files.py:457`, `CurrentSuperuser`) |
| Service | Calls `import_manifest_from_repo` (`manifest_import.py:524`, instantiates `ManifestResolver` `:645`) at `files.py:493`, commits `:509` |
| Note | No CLI caller (`api/bifrost/` grep empty). Only non-test caller is the router handler. git-sync does **not** route through this; it uses `ManifestResolver` directly. `test_watch_regression_disappearing_entity.py:253-255` asserts watch must never call it |

### Surface 4 — `/api/export-import/*` (operator backup)

| Field | Detail |
|---|---|
| Live? | LIVE |
| UI entry | Export: `Knowledge.tsx:295`, `Config.tsx:169`, `Tables.tsx:172`, `Integrations.tsx:135`, `Maintenance.tsx:242`. Import: `ImportDialog.tsx:367,380,387` (rendered from all five pages) |
| Endpoint | `POST /api/export-import/export/{type|all}` + `/import/{type|all}` (`exportImport.ts:31,59,99,131`), all `CurrentSuperuser` |
| Service | Self-contained in `export_import.py` (no service/repository layer). Builders `_build_*_export:165/194/226/271`; helpers `_resolve_org_id:87`, `_parse_target_org:145`. Crypto from `src.core.security` |
| DB write | Direct ORM, bypasses OrgScopedRepository: Knowledge `db.add(KnowledgeStore)` `:568` (embedding `[0.0]*1536`); Tables `db.add(Table, access=make_seed_admin_bypass())` `:663`; Configs `db.add(Config)` `:781`; Integrations `db.add(Integration/ConfigSchema/Mapping/OAuthProvider)` `:868/890/929/1192` |
| Entities | KnowledgeStore, Table, Document, Config, Integration(+ConfigSchema/Mapping), OAuthProvider |
| Create vs update | Natural-key lookup → update-if-found / insert-if-absent. Knowledge (namespace,key,org) `:545`; Tables (name,org) `:620`; Configs (key,org[,integ]) `:755`; Integrations (name, not-deleted) `:835`. `import_all` is delete-free upsert |

### Surface 5 (additional, gap chase) — MCP tools (direct-ORM for 4 of 7 entities)

| Field | Detail |
|---|---|
| Live? | LIVE — all 16 modules registered (`mcp_server/tools/__init__.py:26-49`) |
| UI entry | MCP client (agent tool calls), not the web UI |
| Endpoint | MCP tools, not HTTP routes. Split: **direct-ORM** (agents/forms/tables/apps create+update) vs **thin REST** (`call_rest`) for configs/integrations/workflow |
| Service | Direct: `agents.create_agent` `db.add(agent)` `agents.py:322`, `db.add(AgentTool)` `:339`, `db.add(AgentDelegation)` `:362`; update setters `:504-522`. `forms.create_form` `db.add(form)` `:308` + `db.add(field)` `:314`. `tables.py:288-289`. `apps.create_application` `db.add(app)` `:188` + `:238 commit`. Thin: configs `:132/181/214`, integrations `:187/247/306/371`, workflow `:505/548/600/646`, `apps.replace_app` `:519` |
| DB write | Direct `db.add(...)` / inline ORM setters (above). **Drops the `sync_*_roles_to_workflows` side-effect** the REST routers run |
| Entities | Agent(+junctions), Form(+FormField), Table, Application — plus configs/integrations/workflow via REST |
| Create vs update | Hand-rolled. `create_agent` validates name itself: `agents.py:260 if not name`, `:264 if len(name)>255` — a reimplementation of the `AgentCreate` Pydantic 422 that can drift |

### Surface 6 (additional, gap chase) — file-write / watch indexing (UPDATE-ONLY)

| Field | Detail |
|---|---|
| Live? | LIVE via `POST /api/files/write` + `PUT /api/files/editor/content` / watch push |
| UI entry | CLI `bifrost files write` / SDK `files.write` (`bifrost/files.py:137-140`); editor content PUT |
| Endpoint | `POST /api/files/write` (`routers/files.py:215 backend.write`) → `S3Backend.write` (`file_backend.py:178`, only when `location=="workspace"`) → `FileStorageService.write_file` → `_index_python_file_full` → `index_python_file` (`service.py:450,473`); `PUT /api/files/editor/content` `files.py:763` |
| Service | `WorkflowIndexer.index_python_file` (`indexers/workflow.py`) |
| DB write | **UPDATE-ONLY.** Every branch looks up `(path, function_name, solution_id IS NULL)` and skips if absent: `@workflow` `:161-168` (`if not existing_workflow: continue` "Use register_workflow() to register"), `@data_provider` `:281-287`. Mutations are `update(Workflow).where(...)` `:228-233,327-329,632-643`. **No `db.add`/`insert(Workflow)` anywhere in the file.** On file delete, soft-deactivate (`delete_workflows_for_file` `:613-648`, UPDATE not DELETE) |
| Entities | Workflow (update/enrich + soft-delete only) |
| Create vs update | UPDATE only — never creates. Excludes `tool_description` (`:187-190`, "API/UI-only — never set from code"); name/description set only when DB field is NULL (`:207-212`). Gated by `needs_indexing`, set only for `.py` paths (`file_ops.py:215,223,244`; signal at `files.py:858`) |

### Surface 7 (additional, gap chase) — app dependencies / publish

| Field | Detail |
|---|---|
| Live? | LIVE |
| UI entry | App builder; MCP `update_app_dependencies`; CLI `bifrost apps set-deps` (`commands/apps.py:17`) |
| Endpoint | `PUT /api/applications/{id}/dependencies` (`app_code_files.py:862-908`, **not** `applications.py`); draft/publish: `applications.py:485` save_draft, `:528` publish, `:629` swap_slugs, `:897` rollback |
| DB write | `put_dependencies` `app.dependencies = deps if deps else None` `:901`, `commit` `:902`, render-cache invalidate `:905-906`; solution guard `:880` |
| Entities | Application (dependencies + compiled-preview/draft state) |
| Create vs update | Update of an existing Application row |

### Surface 8 (additional, gap chase) — CLI

| Field | Detail |
|---|---|
| Live? | LIVE, but pure REST wrapper |
| UI entry | CLI commands |
| Endpoint | Each command maps to a REST verb (`commands/apps.py:6-19`: `create → POST /api/applications`, `set-deps → PUT .../dependencies`; `bifrost/files.py:137 → POST /api/files/write`) |
| DB write | None independent — subsumed by REST contract baseline |
| Create vs update | Same as REST. `bifrost apps create` is a two-step (`POST /api/applications` then optional `PUT .../dependencies`), both REST |

## Spec claims — adjudicated

| Claim | Verdict | Evidence | Correction |
|---|---|---|---|
| REST `AgentCreate.name` `min_length=1` → empty/missing returns 422 | **CONFIRMED** | `contracts/agents.py:54-56` `name: str = Field(..., min_length=1, max_length=255)`; bound at `routers/agents.py:338-340` | — |
| REST agent path does not set `Workflow.tool_description`; set only via workflow path `workflows.py:1581-1582` | **CONFIRMED** | `routers/agents.py` has only two read-only refs (`:542`, `:1015`); contracts file has zero `tool_description`; only write is `update_workflow` `workflows.py:1580-1582` | — |
| Solutions deploy carries agent description/channels/tools/knowledge_sources via AgentIndexer (NOT lost) | **CONFIRMED** | Emitted `manifest_import.py:296-307`; persisted `agent.py:122/136` (desc), `:124/138` (channels), `:156-174` (tool FKs), `:125/139` (knowledge_sources); `ManifestAgent` fields `manifest.py:160-165` | — |
| Solutions deploy/capture drops `Workflow.tool_description` end-to-end | **CONFIRMED** | Column exists `orm/workflows.py:91` but absent from capture `capture.py:392-407`, `ManifestWorkflow` `manifest.py:81-100`, importer (grep zero), deploy values `deploy.py:778-797` | — |
| Solutions agent name routes through AgentIndexer which silently no-ops on missing name with no length validation (unlike REST 422) | **CONFIRMED** | `agent.py:69-72` `if not name: logger.warning(...); return False` (no exception, no row); no length check in `:119-149`; REST enforces via `contracts/agents.py:56,85` | — |
| `POST /api/files/manifest/import` has no remaining external caller (CLI deleted; github_sync calls `import_manifest_from_repo` directly) | **PARTIAL** | No-caller part CONFIRMED (`v1.d.ts:2696/2709` types only; CLI grep empty; only caller is the router handler `files.py:457-458` → `:493`). Reason REFUTED: github_sync imports `ManifestResolver` directly (`github_sync.py:54-56,279`) + `_diff_and_collect` `:1146`; it never calls `import_manifest_from_repo` | **Fix the spec's parenthetical:** git-sync bypasses BOTH the wrapper AND `import_manifest_from_repo`; the two import paths share only the `ManifestResolver` class, not that function |
| Manifest/git-sync agent path DOES round-trip `description` (earlier "description is lost" claim WRONG) | **CONFIRMED** | `manifest_import.py:293-297` → `_resolve_agent_content:343-353` → `_index_agents_from_manifest:1252-1270` → `agent.py:122/136`; git-sync reaches it via `github_sync.py:1132,1232`; column `orm/agents.py:34`. Caveat: the `_resolve_agent` metadata SyncOp (`:2906`) does NOT write description — it flows only through the indexer side-effect | — |
| ~5-6 distinct create-vs-update impls across REST + manifest_import + Solutions | **CONFIRMED** | Six mechanisms verified (see inventory below): SyncOp.Upsert (`sync_ops.py:103-115`), natural-key two-pass (`manifest_import.py:2390-2452`), insert().on_conflict_do_update (`agent.py:119-148`, `manifest_import.py:2111`), in-place attr-mutation (`manifest_import.py:2069-2087`), REST inline ORM (`agents.py:339`), Solutions overlay (`deploy.py:1268-1325`) | — |
| `/api/export-import` is LIVE across Knowledge/Config/Tables/Integrations/Maintenance/ImportDialog | **CONFIRMED** | `exportImport.ts:31,59,99,131`; export callers `Knowledge.tsx:295`, `Config.tsx:169`, `Tables.tsx:172`, `Integrations.tsx:135`, `Maintenance.tsx:242`; ImportDialog `:367,380,387`; router registered `main.py:595`, all `CurrentSuperuser` | — |
| `/api/export-import` re-implements secret encryption + org rebinding independently of Solutions (a second copy) | **PARTIAL** | Independence CONFIRMED (own crypto orchestration `export_import.py:729-730,1164-1173`; own org rebind `_resolve_org_id:87`, `_parse_target_org:145`; no cross-imports either way). "Second copy" OVERSTATED: Solutions uses a structurally different secret path (scrypt envelope `secrets_blob.py:9-26`) and different org binding (`Solution.organization_id` `zip_install.py:367,415`); they share only `src.core.security` primitives | **Fix the spec:** these are **parallel re-implementations, not literal copies** — overlapping concern, no shared orchestration |
| Solutions covers tables/configs/integrations but has NO Knowledge equivalent, so `/api/export-import/knowledge` pins the router alive | **CONFIRMED** | Solutions covers Table `capture.py:131`, configs `:136`, integrations `:354/641`; no `KnowledgeStore` anywhere under `services/solutions/` (the only "knowledge" hit is `agent.knowledge_sources` `capture.py:604`, source NAMES not document rows). export_import is unique knowledge path `export_import.py:32,419,168`, live via `Knowledge.tsx:295` | Minor: actual HEAD is `377fd522`, not `9a76c95c` — does not affect the verdict |
| MCP tools write the ORM directly (second create/update impl) for agents/forms/tables/apps, violating the thin-wrapper rule | **CONFIRMED** | Direct ORM `agents.py:322,339,362`, `forms.py:308,314`, `tables.py:288-289`, `apps.py:188,238`; `test_mcp_thin_wrapper.py` only asserts over `PARITY_HANDLERS:51-78` (roles/configs/claims/orgs/integrations/workflow) — agents/forms/tables/apps **deliberately exempt**. MCP path **drops** `sync_agent_roles_to_workflows`/`sync_form_roles_to_workflows` that REST runs (`routers/agents.py:509,838`; `routers/forms.py:417,622`) | — |
| Writing a workflow `.py` via file-write upserts a Workflow row — a FOURTH workflow CREATE mechanism | **REFUTED (create-claim)** | Surface is real (`service.py:450,473` via `POST /api/files/write`), but `indexers/workflow.py` is UPDATE-ONLY: skips unregistered functions `:161-168,281-287`, all mutations are `update(...)`, no `db.add`/`insert(Workflow)` in the file; delete is soft-deactivate `:613-648`. Docstring `:104` "Use register_workflow() to create" | **Fix the spec/critique:** this is an **enrich/update + soft-delete** surface, NOT a fourth create path. It excludes `tool_description` `:187-190` and never overwrites non-NULL name/description `:207-212` |
| `PUT /api/applications/{id}/dependencies` is an untraced Application-row write surface | **CONFIRMED** (relocated) | Route lives in `app_code_files.py:862-908` (not `applications.py`): `app.dependencies = ...` `:901`, `commit` `:902`, solution guard `:880`. Plus draft/publish/swap/rollback `applications.py:485,528,629,897` | Spec's "next-check" looked in `applications.py`; the dependencies route is in `app_code_files.py` |
| CLI is genuinely thin (subsumed by REST) | **CONFIRMED** | `commands/apps.py:6-19` maps every verb to a REST endpoint; `bifrost/files.py:137 → POST /api/files/write`. No independent ORM write path | — |

## Complete inventory of create-vs-update mechanisms

Eight distinct mechanisms span the seven surfaces. The "~5-6" spec estimate undercounts once MCP and file-write are folded in.

| # | Mechanism | Decides create-vs-update by | Surfaces / entities |
|---|---|---|---|
| 1 | **HTTP verb + route** (REST inline-ORM) | POST collection = new `uuid4()`; PUT/PATCH item = load-by-id + mutate | REST routers — Agent (`agents.py:392,699`), Form (`forms.py:379,554`), Workflow update (`workflows.py:1437`) |
| 2 | **Repository upsert/CRUD method** | repo method semantics (set_config UPSERTS by key; others by id) | REST via `ConfigRepository`/`TableRepository`/`ApplicationRepository`/`IntegrationsRepository` |
| 3 | **`SyncOp.Upsert(match_on="id"\|"name")`** | UPDATE WHERE match; rowcount==0 → INSERT (`sync_ops.py:103-115`) | git-sync `_resolve_form/agent/config/app` (`manifest_import.py:2886/2937/2245/2315`); Solutions Workflow/Table/Claim/App/config-schema (`deploy.py:799…`) |
| 4 | **Natural-key two-pass + id-realign (raw Core)** | match by (name/slug, org) → UPDATE+realign id; else by id; else `insert().on_conflict_do_nothing` | git-sync `_resolve_table` (`manifest_import.py:2390-2452`), `_resolve_custom_claim`; Solutions `SolutionConnectionSchema`/`EventSource` (`deploy.py:1489-1510,1566-1578`) |
| 5 | **`insert().on_conflict_do_update` (Postgres UPSERT on PK/constraint)** | INSERT, on id/constraint conflict UPDATE | Indexers `AgentIndexer`/`FormIndexer` (`agent.py:119-148`); git-sync OAuthProvider (`manifest_import.py:2111`) |
| 6 | **In-place ORM attribute-mutation upsert** | `if key in existing: mutate; else insert(...)` | git-sync integration config-schema + mappings (`manifest_import.py:2069-2087,2158-2174`) |
| 7 | **Natural-key direct-ORM `db.add` upsert** (export-import) | natural-key lookup → mutate-existing / `db.add(new)`; gated by `replace_existing` | `/api/export-import` Knowledge/Table/Config/Integration (`export_import.py:545/620/755/835`) |
| 8 | **Direct `db.add` + inline setters** (MCP) | hand-rolled; create tool vs update tool | MCP agents/forms/tables/apps (`mcp_server/tools/agents.py:322`, `forms.py:308`, etc.) |

Notes on the watch/file-write surface: it is **not** a create-vs-update mechanism at all — `WorkflowIndexer.index_python_file` is UPDATE-only and cannot insert. CLI adds no mechanism (delegates to #1/#2 via REST).

## Corrections to the spec

- **`POST /api/files/manifest/import` reason is wrong.** The spec/verdict says github_sync "calls `import_manifest_from_repo` directly." It does not — github_sync imports/instantiates `ManifestResolver` (`github_sync.py:54-56,279`) and uses `_diff_and_collect` (`:1146`). The git-sync path bypasses both the HTTP wrapper and `import_manifest_from_repo`; the two import paths share only the `ManifestResolver` class. The endpoint is genuinely orphaned (no CLI/client caller), but for that reason, not the stated one.
- **File-write/watch is not a fourth workflow CREATE path.** The completeness critique's headline ("writing a workflow `.py` upserts a Workflow ORM row … FOURTH create mechanism") is REFUTED. `indexers/workflow.py` skips any function with no pre-existing registered row (`:161-168,281-287`), performs only `update(...)`, and soft-deactivates on delete (`:613-648`). It is an enrich/update surface bounded to `.py` files via `needs_indexing` (`file_ops.py:215,223,244`).
- **"export_import is a second copy of Solutions' secret/org logic" overstates it.** They are independent parallel re-implementations sharing only `src.core.security` primitives. export_import uses a source-key→dest-key re-encrypt model (`:729-730,1164-1173`) + `_resolve_org_id`/`_parse_target_org`; Solutions uses a scrypt password envelope (`secrets_blob.py`) + install-row `Solution.organization_id`. Overlap is on entity coverage (tables/configs/integrations), not code.
- **The app-dependencies route is mislocated in the spec's next-check.** `PUT /api/applications/{id}/dependencies` is in `app_code_files.py:862-908`, not `applications.py`; a router-file-scoped grep on `applications.py` misses it.
- **MCP write paths are entirely absent from the contract baseline.** The spec asserts "CLI/MCP/manifest all read from the same DTOs," but agents/forms/tables/apps MCP create+update write the ORM directly (`agents.py:322`, `forms.py:308`, `tables.py:288`, `apps.py:188`), are exempt from `test_mcp_thin_wrapper.py` (`PARITY_HANDLERS:51-78`), hand-roll name validation (`agents.py:260,264`), and drop the REST role-sync side-effect. This is a real divergence, not covered by the DTO-parity assumption.
- **HEAD mismatch (cosmetic):** the task framing cites `9a76c95c`; the worktree HEAD is `377fd522`. No verdict is affected.

## Still requires live repro

Two findings are real bugs whose *runtime consequence* cannot be fully settled by code reading alone:

1. **Silent empty-name agent deploy (FK orphan / lying count).** Code confirms `AgentIndexer.index_agent` returns `False` and writes no row on blank name/system_prompt (`agent.py:69-77`), yet deploy then runs `update(Agent).where(id==...)` (0 rows, silent), adds role/MCP junction rows whose FK target may not exist, and `DeployResult.agents_upserted` counts `len(bundle.agents)` regardless (`deploy.py:476`). Whether the junction inserts actually raise a FK violation at COMMIT (vs. silently succeed against a non-existent agent id) depends on DB constraint enforcement at runtime — needs a live deploy of a bundle with a blank-named agent to observe whether it 500s, half-commits, or reports a false-positive count.

2. **`Workflow.tool_description` round-trip loss on capture→deploy.** Code confirms the field is omitted from capture, the manifest model, the importer, and the deploy values dict, while the column exists and is user-settable via the workflow PATCH. The end-user-visible impact — a `type='tool'` workflow's MCP/agent-facing description silently reverting to NULL after a capture-then-redeploy cycle — needs a live capture → install → inspect to confirm the column is actually cleared (vs. left untouched on an update that omits the key).

---

# 8. LIVE REPRO — both bugs reproduced end-to-end (2026-06-19)

Driven via the API-matched CLI against a fresh netbird dev stack (`bifrost-debug-be6ad6da`),
solution `tooldesc-repro` install `58a5f7ec…`. Not code-reading — observed behavior.

## Bug 1 — `tool_description` dropped on export/capture — CONFIRMED LIVE
- Set `workflows.tool_description = 'CURATED-TOOLDESC-DO-NOT-LOSE-12345'` on the deployed
  solution workflow `hello` (direct DB write, since the API blocks it — see below).
- `bifrost solution export tooldesc-repro --mode shareable` → unzipped bundle.
- The exported `.bifrost/workflows.yaml` carries `type, endpoint_enabled, public_endpoint,
  timeout_seconds, category, tags, access_level, roles` — **but no `tool_description`**.
- `grep -r CURATED-TOOLDESC… bundle/` → **NOT FOUND ANYWHERE**. The curated description is gone
  the moment the solution is exported; any reinstall loses it.
- **Stronger finding (the guard makes it unfixable):** trying to set it the supported way —
  `PATCH /api/workflows/{id} {"tool_description": …}` on a solution-managed workflow — returns
  `403 "Solution-managed entities can only be managed by deployment methods."` So for a
  solution tool: the API path is **blocked by the read-only guard**, and the deployment path
  **doesn't carry the field**. There is **no supported path** to give a solution's tool a
  curated description. (Only a `_repo`/global workflow can have one — and capture drops it.)

## Bug 2 — blank-name agent silently swallowed + lying success count — CONFIRMED LIVE
- Added an agent with `name: ''` + a `tool_ids` binding to `.bifrost/agents.yaml`, deployed.
- CLI printed `found … 1 agent(s)` and `Deploy complete.` with **no error**.
- DB after: **0 agents**, **0 orphan agent_tools**. The agent never materialized —
  `AgentIndexer.index_agent` hit `if not name: return False` (`agent.py:69-72`) and no-op'd.
- The deploy job result row (`c1ffaaf9…`) recorded `"status":"succeeded"` and
  **`"agents_upserted": 1`** — a **false-positive count**: it claims it upserted an agent that
  does not exist. No 500, no surfaced warning. Worst-case diagnostic: a solution author ships a
  package, sees green, and the agent is silently missing on every install.

Both bugs are structural consequences of the manifest/indexer path NOT going through the REST
contract (which would 422 the blank name and carry `tool_description`). This is the empirical
backing for the §6 unification work.
