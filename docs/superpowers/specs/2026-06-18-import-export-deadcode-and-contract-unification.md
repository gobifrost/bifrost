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
with no `client/src` fetch and no `api/bifrost` caller. git-sync calls
`import_manifest_from_repo` **directly**, not via HTTP. So the *endpoint wrapper* is likely
dead even though the *service function* is very much alive. `GET /api/files/manifest` is still
used (CLI watch / git-sync state export). **Classification: unclear-leaning-dead; verify with a
request-log check before removing.**

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
