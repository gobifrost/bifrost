# Solutions V1 Coverage — Knowledge, Triggers, Storage

**Date:** 2026-06-14
**Status:** Design (approved scope; not yet planned/implemented)
**Context:** Follow-on from the platform-impact audit (`docs/plans/2026-06-14-solutions-platform-impact-audit.md`).
The audit found three coverage gaps where a Solution references something stored outside the
portable bundle: **knowledge/RAG corpus**, **event/schedule triggers**, and **file/object
storage**. This doc records the V1 decisions and the grounded mechanics behind them.

## Decision summary

| Gap | V1? | Shape |
|-----|-----|-------|
| **Knowledge / RAG** | **YES** | Add `solution_id` to `KnowledgeStore`; own-first **additive** resolution leg (own → org → global), mirroring tables. |
| **Event / schedule triggers** | **YES** | Treat as a **manifest section**: add `events`/`schedules` to `.bifrost/` + bundle, give the ORMs `solution_id`, deploy/guard them like forms. The workflow the trigger points at already travels in the solution. |
| **File / object storage** | **NO (post-V1)** | Files have no tracked DB record, so orphan/reattach safety can't extend to them without real design. Document current behavior; defer. |

The unifying principle (and why storage is the odd one out): every entity Solutions manages
safely is a **DB row with a `solution_id` column**, so install/uninstall/orphan/reattach all
run through one machinery. Knowledge and triggers ARE rows — they just lack the column.
**Files are not rows** — S3 keys are bytes at a path with no record — so they can't ride that
machinery without first being given one.

---

## 1. Knowledge / RAG — own-first additive leg (V1)

### Current mechanics (grounded)
- `KnowledgeStore` is org-scoped with a nullable `organization_id` (`api/src/models/orm/knowledge.py:55-57`); `org_id IS NULL` == **global**. This is a real data-model tier, not convention.
- Query resolution already **cascades org → global** when `fallback=True`: `KnowledgeRepository.search` ORs `organization_id == target_org` with `organization_id IS NULL` (`api/src/repositories/knowledge.py:178-181`).
- A namespace is keyed `(namespace, organization_id, key, chunk_index)` (`knowledge.py:111-115`) — per-(namespace, org), NULL-org = global.
- **No `solution_id` column. No solution leg.** `ctx.solution_id` is never threaded into the search route (`cli_knowledge_search`, `api/src/routers/cli.py:2315-2349` — only `scope`/org flows).
- Agents reference namespaces via `agent.knowledge_sources: list[str]` (already captured in the bundle, `capture.py:482`). At runtime `AgentExecutor._execute_knowledge_search` searches with `fallback=True` (org+global) using the agent's org (`agent_executor.py:1499-1556`). UI namespace assignment comes from **role grants** (`/api/agents/accessible-knowledge`, `agents.py:540-567`), not a namespace scan.

### Design
Mirror the tables own-first pattern (`_resolve_solution_table_by_name`, `api/src/routers/tables.py:638-689`) exactly:

1. **Schema:** add `solution_id` (nullable FK) to `KnowledgeStore`. Migration + the standard read-only guard coverage (it's a managed entity when set).
2. **Resolution becomes a 3-leg additive cascade:** **own-install → org → global.** The install leg is ADDITIVE — global is never lost, it gains a higher-priority leg above it. A solution agent searching its namespace gets the install's docs first, then org, then global.
3. **Plumbing:** thread `ctx.solution_id` into `cli_knowledge_search` + the knowledge SDK (today only `scope` flows), exactly as the tables SDK appends `?solution=`. Add the own-first branch before the existing org/global cascade in `KnowledgeRepository.search`.
4. **The "how does global work then?" question (resolved):** the table precedent answers it — global is the bottom leg, unchanged. A solution author names namespaces within their own world; those resolve own-first. A global grant remains a deliberate, separate thing (role-based), not something the solution leg overrides.
5. **Deploy:** the corpus *documents* are the open question — do we ship them? Two sub-options to settle at plan time:
   - (a) **Declaration-only** (cheap): deploy the namespace binding + `solution_id`; the operator/an ingest workflow populates docs. Pairs with the install-preview knowledge note already shipped (audit run).
   - (b) **Carry documents** (heavier): capture `KnowledgeStore` rows (embeddings included) into the bundle. Larger bundles, embedding-config portability risk (see the embedding-space-drift memory). Likely post-V1 even if the column/resolution lands in V1.
   - **V1 recommendation:** ship the column + own-first resolution + declaration-only (a). Document carry (b) as a follow-up.

### Agents "seeing" namespaces
For a solution-managed agent, its `knowledge_sources` are part of the portable bundle already.
Within the solution's own world there's no global ambiguity at assignment time — the author picks
names in their own install scope. The existing role-grant path for *global* namespaces is untouched.

---

## 2. Event / schedule triggers — manifest section (V1)

### The pivot
An `EventSource`/`ScheduleSource` is **almost nothing**: a definition that says "trigger workflow X
on schedule/event Y." Workflow X **already travels in the solution**. So this is NOT a coverage gap
needing new infrastructure — it is a **missing manifest section**, the same deploy/`solution_id`/guard
pattern already built five times (workflows, forms, agents, tables, claims).

### Current mechanics (grounded)
- `EventSource` / `EventSubscription` / `ScheduleSource` have **no `solution_id`**, are not in the dependency walker, capture, deploy, or any `.bifrost/*.yaml` section.
- Execution of a managed workflow by a schedule/event already works (the scheduler re-resolves `solution_id` from the workflow's DB row; the consumer is the single propagation point). What's missing is **shipping the trigger row**, not running it.

### Design
1. **Schema:** add `solution_id` to `EventSource`, `EventSubscription`, `ScheduleSource` (the rows a solution would own). Read-only guard coverage when set.
2. **Manifest:** add `events`/`schedules` sections to `.bifrost/` + the bundle (capture, `manifest_generator`, `github_sync` resolve, `dependency_walker`). Follow the "adding a field to manifest models" checklist in the root CLAUDE.md.
3. **Deploy/guard:** upsert-by-natural-key + stale-sweep scoped to `solution_id` (the canonical deploy reconcile). The H1 fix already spares managed rows in the git-sync sweep — extend the same `solution_id IS NULL` exclusion if these get bulk-swept anywhere.
4. **Instance-specific scrub:** an `EventSource`/webhook may carry instance bits (webhook secret, inbound URL, per-install token). Apply the **integration-template scrub** pattern (`build_integration_template`, secret-scrubbed skeleton) so the portable content excludes them and the instance supplies them at/after install — exactly how connection declarations already work. Validate which fields are env-specific before serializing (don't carry secrets into the portable bundle).
5. **UI:** events/schedules owned by a solution render **read-only** with the managed badge, like every other managed entity. (Originally considered read-only-UI as the *whole* V1 answer; the pivot makes full deploy the right V1 scope, with read-only UI as the natural consequence.)

### Why this is V1-sized
It's the established pattern applied to two more tables. The only genuinely new work is the
instance-specific scrub for webhook/secret fields, which already has a template to copy.

---

## 3. File / object storage — punt to post-V1 (with honest docs)

### Current mechanics (grounded)
- Key layout is `{location}/{scope}/{path}` via `resolve_s3_key` (`api/shared/file_paths.py:72`).
  Reserved: `workspace` → `_repo/{path}` (unscoped), `uploads` → `uploads/{scope}/{path}`, `temp` → `_tmp/{scope}/{path}`. Blocked raw names: `_repo`, `_tmp`, `_apps`.
- A **non-reserved** location like `widgets` resolves to `widgets/{scope}/{path}` (`file_paths.py:97`), where `scope == org_id` bound client-side by `resolve_scope` (`api/bifrost/_context.py:128`).
- **Zero solution-awareness.** `ctx.solution_id` is on `ExecutionContext` but the files SDK never reads it — only `org_id` flows. A solution's files are just org files.

### Why it's NOT a quick win (the two killers)
1. **Inconsistent model.** Tables/knowledge do own-first *additive* (install leg layers on top of org/global). The tempting storage shortcut — make `scope = install` when `solution_id` is set — **replaces** org-scope instead of layering. It's the one place the install leg would substitute rather than add. Special-case that rots.
2. **No tracked record → no safety net.** Tables/knowledge/triggers are DB rows; `solution_id` drives uninstall/orphan/reattach. **Files have no row.** If solution files live at an install-partitioned path, uninstall has nothing to drive the orphan sweep — data is silently deleted or silently stranded. **The existing install/reinstall safety mechanisms do NOT cover files** and can't, without first giving files a tracked record.

### V1 position (documented, not built)
- **Current behavior, stated plainly in docs:** files a solution's workflow/app writes are **org-scoped** and **survive uninstall as ordinary org data**. Solutions do **not** partition or lifecycle-manage storage in V1.
- This is safe (nothing is silently deleted) and honest (we don't claim "Solutions manage storage").
- **Post-V1 design:** give solution-written files a **tracked record** (a `FileIndex`-like row carrying `solution_id` + provenance) so orphan/reattach extends to storage the same way it does for tables. Only then introduce a partitioned layout. The `solutions/{solution_id}/{org_id}/...` prefix is the fallback shape if a tracked record lands; install-scope-replaces-org-scope is **rejected** (loses the org leg, system loses track).

---

## Build order (when these get planned)
1. **Events/schedules** first — pure reuse of the deploy/manifest/guard machinery; smallest risk; unblocks the most common automation-bundle shape (scheduled/triggered workflow).
2. **Knowledge** — column + own-first resolution + declaration-only deploy; document-carry deferred.
3. **Storage** — post-V1; needs the tracked-record design before any layout change.

Each gets its own spec → plan → implementation cycle. This doc is the umbrella decision record.
