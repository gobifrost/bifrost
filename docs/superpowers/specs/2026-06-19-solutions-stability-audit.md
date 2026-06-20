# Solutions Stability Audit (2026-06-19)

> Adversarial multi-agent code-verified audit (31 agents). Output file truncated at 18KB; this captures the verdict + all critical/high findings + the field-parity table. The medium/low tail was cut off mid-sentence — re-run or read agent transcripts for the full medium list.

## Verdict

**(B) Stable after identified fixes.**

Solutions as-shipped is fundamentally sound â the deploy/capture/manifest machinery round-trips correctly for the standard `bifrost export` â install path, and the two false alarms confirm the dangerous-looking edges (portable form workflow refs, table-data name divergence) are not reachable. But there is **one CONFIRMED critical data-loss defect** (git-connected installs sweep all event/schedule/webhook triggers) and **a cluster of CONFIRMED high-severity defects** (EventSubscription PK collision on second install, agent-delegation order dependence, table orphan cross-solution reattach, blank-name silent no-op, `auto_fill` drop) that must be fixed before Solutions can be called stable for the multi-tenant + git-connected + community-bundle use cases it advertises. None are systemic architecture failures â each is a localized contract gap â which is why this is (B), not (C).

Counts of CONFIRMED/PARTIAL-REAL defects: **critical: 1, high: 6, medium: 6** (plus 6 low). The adversarial re-verification downgraded several originally-"high" findings to medium/low (noted inline); the table below uses the re-verified severity.

## Critical & High findings

| Title | Severity | Entity | Symptom | Evidence (file:line) | Repro |
|---|---|---|---|---|---|
| Git-connected auto-pull deletes ALL of an install's event/schedule/webhook triggers on every sync | **critical** | git_sync deploy / EventSource reconcile | Any solution installed via git connection loses every event source, cron schedule, and webhook on first sync and every commit after; scheduled workflows silently stop, webhooks go dead. Zip-install path is unaffected. | `git_sync.py:131-151` builds `SolutionBundle` with **no** `events=` (verified: workflows/tables/apps/forms/agents/claims/config_schemas/connection_schemas passed, events omitted); `SolutionBundle.events` defaults `[]` (`deploy.py:283`); `deploy()` runs `_upsert_events(â¦, [])` (no-op, `deploy.py:380`) then `_reconcile_one(EventSource, sid, set())` (`deploy.py:1751-1752`) â `stale = ALL existing EventSources for sid` deleted (`deploy.py:1765-1775`); children cascade (`events.py:135/186/249`). CLI collects events (`solution.py:1420`); zip collects events (`zip_install.py:202`). | Git-install a solution whose `.bifrost/events.yaml` declares a schedule+webhook; query `event_sources WHERE solution_id=<id>` â 0 rows; cron never fires. Same repo as zip â rows present. |
| EventSubscription PK reused verbatim across installs â 2nd install fails with duplicate-key 500 | **high** | deploy `_upsert_events` / `_remapped_bundle` | Installing the same bundle (with event subscriptions) into a 2nd org raises a duplicate-PK IntegrityError â 500, rolls back the whole 2nd install. Breaks "install the same solution into two customers." Same-install redeploy is fine. | `deploy.py:1623` inserts with raw `id=UUID(str(msub["id"]))`. Remap pass rewrites only `workflow_id`/`agent_id` (`deploy.py:575`), never `msub["id"]`; EventSource own id IS remapped (`deploy.py:540` + pass-1), so delete-by-`event_source_id` (`deploy.py:1541`) keys on the remapped source id and can't clear install #1's sub on a fresh install. PK default uuid4 (`events.py:247`), captured verbatim (`manifest_generator.py:357`). | Author a solution with a schedule + subscription; install into org A (succeeds), install byte-identical bundle into org B â UniqueViolation on `event_subscriptions_pkey` â 500. Fix: remap `msub["id"]` through `solution_entity_id`/`id_map` like the source id. |
| Agentâagent delegation silently dropped when parent is ordered before child in the bundle | **high** | deploy `_upsert_agents` + AgentIndexer delegation sync | A solution with agents AâB installs/redeploys with the AâB delegation missing; non-deterministic across DB row orderings; not healed by redeploy. | `deploy.py:1295-1338` indexes each agent fully (row+delegations) one-by-one, no deferred pass; `agent.py:188-200` writes delegation only if child row already exists else `logger.warning`; capture `_agent_entries` has no ORDER BY (`capture.py:590-593`). Child id IS remapped (`deploy.py:561-565`), so the *only* failure mode is forward ordering. (Same single-pass exists in canonical `manifest_import.py:1255-1270` â not Solutions-specific, but Solutions ships it.) | Bundle with A.delegated_agent_ids=[B] where A sorts before B â no `agent_delegations` row for (A,B); inverting order fixes it. Fix: second pass wiring all delegations after all agent rows exist. |
| Table orphan adoption reattaches a prior unrelated solution's table+documents on slug+name collision in an org | **high** | deploy `_upsert_tables` orphan adoption | A newly-installed unrelated solution silently inherits a previous solution's table rows (cross-solution data bleed). | `deploy.py:894-905` adopts orphan filtering on `orphaned_at IS NOT NULL, origin_solution_slug==slug, name==name, org_pred` only â **not** solution id; keeps orphan id + Documents (`deploy.py:908-922`). `delete_solution` stamps `origin_solution_slug=sol.slug` (`solutions.py:771-781`). Slug is author-controlled (`zip_install.py:180`), unique only per (slug,org) install scope (`solutions.py:51-65`), no global registry. ORM comment confirms slug is the intended reattach key (`tables.py:61-62`). | Org X: install sol-A (slug 'foo', table 'data', add docs); uninstall; install unrelated sol-B (slug 'foo', table 'data') â sol-B's 'data' carries sol-A's Documents. |
| Blank-name forms/agents silently fail to deploy; deploy reports success | **high** [KNOWN] | deploy `_upsert_forms`/`_upsert_agents` | A hand-authored/community bundle whose form (or agent w/o system_prompt) omits `name` installs "successfully" (`forms_upserted`/`agents_upserted` counts it) but no row is written; with roles it FK-500s instead. | `form.py:92-95` / `agent.py:74-77` return `False` (no insert) on blank name; `deploy.py:1253`/`1300` discard the bool; `update(Form/Agent).where(id==â¦)` matches 0 rows (`deploy.py:1268-1270`/`1323-1325`); `DeployResult` counts bundle entries (`deploy.py:475-477`). Manifest defaults name="" (`manifest.py:114/146`). **Note:** AgentCreate enforces `min_length=1` so the *agent* case is only reachable via hand-authored bundles, not capture; forms have no min_length (`contracts/forms.py:262`). | Zip with a form entry `{id, workflow_id}` and no `name` â 200, `forms_upserted=1`, no Form row; add `roles` â FK-violation 500. |
| `FormField.auto_fill` dropped on every Solution deploy / git-sync / file-sync (FormIndexer never writes the column) | **high** | form field (deploy via FormIndexer) | A solution/git-synced form using field auto-fill loses every auto_fill mapping on deploy â the cross-field pre-population silently disappears for installed customers. REST keeps it. | Capture serializes it (`capture.py:986`); it rides in the opaque `form_schema` dict (`manifest.py:133`) and is written to YAML (`manifest_import.py:288-289`); but the indexer deletes all fields (`form.py:197-199`) then re-inserts via `FormFieldORM(â¦)` (`form.py:212-235`) that sets nameâ¦allow_as_query_param but **never `auto_fill=`** â falls to default None (`orm/forms.py:71`). REST persists it (`routers/forms.py:103`). | Create a form where field A.auto_fill maps to B; export; install; `SELECT auto_fill FROM form_fields` â NULL for all. |

## Field-parity findings

Every CONFIRMED field that is dropped or wrong across the three surfaces (REST = full CRUD router/contract; Manifest = `.bifrost/*.yaml` round-trip; Solutions = captureâdeploy):

| Field | Entity | REST | Manifest | Solutions deploy | Impact |
|---|---|---|---|---|---|
| `auto_fill` | form field | set (`routers/forms.py:103`) | present (rides in `form_schema` dict) | **NULL** (FormIndexer omits, `form.py:212-235`) | high â cross-field auto-fill silently lost on every install/git-sync |
| `max_run_timeout` | agent | set (`orm/agents.py:76`) | **absent** (ManifestAgent has no field, `manifest.py:158-177`) | **NULL** (no writer in `_upsert_agents`/indexer) | medium â long-running agent reverts to DEFAULT_RUN_TIMEOUT (`agent_run.py:123`) |
| `tool_description` | workflow | set (`contracts/workflows.py:104`, `routers/workflows.py:126`) | **absent** (`serialize_workflow` omits, `manifest_generator.py:85-100`) | **absent** (`capture.py:392-407`, `deploy.py:778-797`) | medium â type='tool' workflows lose curated LLM-facing description, fall back to `description` (`tool_registry.py:109/166`) |
| `event_type` (source-level) | event source | n/a (engine field) | **absent** (`serialize_event_source` reads only sub-level, `manifest_generator.py:339-368`) | NULL written (`deploy.py:1559`, but manifest never carried it) | medium â topic EventSource becomes undispatchable (`repositories/events.py:172-180` matches `event_type==topic`) |
| `list_entities_data_provider_id` | integration shell | set (`orm/integrations.py:35`) | template carries it (`integration_template.py:36-40`) | **NULL** (`_upsert_integration_shells` never reads it, `deploy.py:1406-1446`) | low â fresh shell's entity-picker has no data provider wired (and the raw value would be a dangling FK across envs anyway) |
| `display_name` | workflow | set (`routers/workflows.py:108`) | **present** in regular manifest (`manifest.py:222`, `manifest_import.py:2113`) but **omitted by `serialize_workflow`** (`manifest_generator.py:85-100`) | **NULL** (`capture.py:392-407`, `deploy.py:778-797`) | low â cosmetic; no UI list renders workflow display_name (only the edit dialog), so no visible fallback |
| `max_iterations` / `max_token_budget` (clear-to-None) | agent | clearable | None â absent via `_drop_none` (`capture.py:597`) | **previous value retained** on redeploy (`deploy.py:1319-1322` stamps only when non-None) | low â clearing a budget doesn't revert; runtime coalesces None to same default (`autonomous_agent_executor.py:149-150`), so bounded. Mirrors canonical `manifest_import.py:316-319` |

## Medium / fragility findings

- **`delete_solution` S3 sweep post-commit, no retry** (`solutions.py:859-880`) â a storage blip after the DB cascade commits leaves orphaned `_solutions/{id}/` + `_apps/{id}/dist` artifacts and returns a misleading 500; re-run 404s so orphans are never swept. Deploy has `_retry_idempotent`/`SolutionFinalizeIncomplete`; delete does not (medium, REAL).
- **`install_from_repo` finalize_s3 outside try/except** (`solutions.py:1506-1519`) â `SolutionFinalizeIncomplete` propagates as a raw 500 (sibling `install_zip` returns an actionable 502, `solutions.py:1647-1654`); the committed git-connected row references S3 source never written, and dep

---

## Live-repro confirmation (2026-06-19)
The CRITICAL (git-connected sync wipes all event sources) was reproduced END-TO-END on the dev
stack: an install with 1 event source (`nightly` schedule), deployed via a `SolutionBundle` built
the way `git_sync.read_workspace_bundle` builds it (no `events=`), went from **1 → 0 event
sources** (rolled back, not persisted). Confirmed:
- `git_sync.read_workspace_bundle` (git_sync.py:131) omits `events=`; zip path passes it
  (`zip_install.py:202`).
- `_reconcile_one(EventSource, sid, set())` (deploy.py:1751) sweeps all install event sources;
  children cascade.
Not theoretical — git-connected solutions lose every schedule/webhook on every sync.

---

## Triage by path (Jack's rule: fix Solutions-path reproducibles now; defer _repo/shared to convergence)

Verified each finding's WRITE PATH to decide fix-now vs defer:

### FIX NOW — Solutions-path, reproducible, narrow
- **CRITICAL — git-connected events wipe.** Lives in `solutions/git_sync.py:99` `read_workspace_bundle`
  (Solutions path; `github_sync.py` does NOT touch it — grep clean). Events DO ship with Solutions
  (`capture.py:137,176` capture+count them). Fix = one line: add `events=_collect_events(workspace)`
  to `read_workspace_bundle`, mirroring `zip_install.py:202`. Live-confirmed 1→0.
- **HIGH — EventSubscription PK reuse.** `deploy.py:1623` inserts `id=msub["id"]` verbatim; the remap
  pass never rewrites it. Solutions deploy, all install paths. Fix = remap `msub["id"]` through the
  id_map like the source id. Reproducible (2nd-install 500).
- **HIGH — table orphan cross-solution reattach.** `deploy.py:894` adopts on (slug,name,org) not
  solution identity. Solutions deploy. Reproducible (data bleed). Fix = tighten the adoption key.

### DEFER TO CONVERGENCE — shared/_repo path; list as "confirm the converged writer kills these"
- **HIGH — FormField.auto_fill dropped.** Root cause is the SHARED `FormIndexer` (`form.py` re-inserts
  FormFieldORM without `auto_fill=`), used by Solutions deploy AND git-sync AND file-sync. REST sets it
  (`routers/forms.py:103`). Not Solutions-specific; a field-parity gap the converged serializer/contract
  must carry. (Could hotfix the indexer line now if auto_fill is in active use — flagged as a judgement call.)
- **HIGH — agent delegation order-dependence.** Audit confirms the same single-pass exists in canonical
  `manifest_import.py:1255-1270` — SHARED, not Solutions-specific. The two-pass fix belongs in the
  converged writer (wire all delegations after all agent rows exist).
- **Field-parity gaps** (`tool_description`, `max_run_timeout`, `event_type`, `display_name`,
  max_iterations clear-to-None): all are "writer doesn't carry a field the contract has" — the EXACT
  class the annotated-contract (Axis A) eliminates. These become the convergence acceptance checklist:
  the converged writer is "done" only when each of these round-trips.

### KNOWN / already in Phase 1
- **blank-name silent no-op + false count** — Phase 1 plan already fixes (indexer raises; deploy
  surfaces 409). Note: indexer is SHARED so the raise also hardens git-sync; the count is Solutions.

### Bridge
Every DEFER item is a field/relation the converged contract+writer must carry — so they become the
convergence test suite, not lost work. Every FIX-NOW item is a Solutions-path reproducible that
shouldn't wait. This is exactly Jack's split.

---

## Triage correction (2026-06-19, post-ORM-verification)
While planning the fix, the Table ORM revealed the orphan-adoption finding is NOT a clean
fix-now: `tables.py:61-62` states `origin_solution_id` is informational and `origin_solution_slug`
is the INTENDED reattach key — because a reinstalled solution gets a NEW solution.id (old row
deleted). So matching adoption on solution.id would break the legitimate same-solution-reinstall
reattach. The cross-solution bleed is real, but the fix requires a design decision (make slug a
trusted unique identity / add a stable definition-id / opt-in adoption). MOVED from "fix now" to a
HOLD item (Phase-1 plan Task 11, not executed pending decision). Net fix-now Solutions-path list:
**Task 9 (events wipe, CRITICAL) + Task 10 (EventSub PK, HIGH)** — both clean one-spot fixes.
