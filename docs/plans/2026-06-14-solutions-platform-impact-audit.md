# Solutions Platform-Impact Audit (adversarial, four-lens)

**Date:** 2026-06-14
**Branch:** `solutions/connection-references` (worktree `solutions-success-criteria`), draft PR #347
**Method:** Five grounded fan-out agents, code-verified against HEAD vs `main`. Every finding cites `file:line`. No code changed in this pass.
**Triage order:** This audit runs AFTER desloppify (Jack's sequencing). Nothing here is fixed yet.

This is an adversarial sweep of the **pre-Solutions feature surface** for things Solutions
impacted, plus a user-perspective completeness/coverage/UX pass. Four lenses:

1. **Regression** — what Solutions broke or changed in existing features.
2. **Completeness** — install / update / manage lifecycle from a user's POV: expected vs actual.
3. **Coverage** — platform capabilities that should hook into Solutions but don't (storage was Jack's prime suspect).
4. **UX-surfacing** — state the backend knows but the UI doesn't show.

---

## What Solutions changed (the blast radius)

- A `solution_id` column on portable entities: **workflows, forms, agents, applications, tables, config, custom_claims** (verified via `grep -rl solution_id api/src/models/orm/`). NOT on Integration/IntegrationMapping, EventSource/Subscription, ScheduleSource, KnowledgeStore, MCPServer/Connection, OAuthToken.
- An **always-on read-only guard** (`api/src/services/solutions/guard.py`): routers call `assert_not_solution_managed` / `assert_entity_id_not_solution_managed` (clean 409); a `before_flush` ORM backstop raises `SolutionManagedWriteError` for any dirty/deleted managed ORM object. **The backstop only sees ORM objects — Core `insert/update/delete` statements bypass it** (this is the recurring trap; see H1).
- `solution_id` threaded onto the **ExecutionContext** so a solution's workflow resolves its own solution-scoped data plane (the "F2 fix": SDK appends `?solution=`, resolver does own-first off `ctx.solution_id OR ctx.app_id`). **Only the server engine re-resolves it from the DB workflow row.**

---

## Resolution status (2026-06-14, autonomous fix run)

Fixed this run (before desloppify, so desloppify reviews final code). All TDD, all green:
- **H1** — git-sync sweep now spares `solution_id IS NOT NULL` centrally in `_bulk_delete`/`_bulk_deactivate`. Verified by unit test + 81 git-sync e2e. (commit e5b57f30)
- **U1** — managed-table Edit/Delete gated + 409 translated in Tables.tsx. (ad841494)
- **F1/F2** — `bifrost run` + `solution start` resolve the install id into the ExecutionContext (`resolve_install_id_for_workspace`, defensively None). **Verified LIVE**: `bifrost run` of a deployed solution's workflow read its own table (total:1) where the pre-fix path 404s. (22abb239)
- **F3** — CORRECTION: not a bug. Solution config *values* are instance-owned (key,org) rows resolved by the normal cascade; they carry `origin_solution_id`, NOT `solution_id`. There is no install-scoped config row for own-first to prefer (unlike tables). The tables/config asymmetry is by design. No change.
- **M-MCP** — early `is_solution_managed` guard on delete_agent, update_table, delete_table, update_app, update_app_dependencies (clean 409 not backstop 500). (ea8e2324)
- **M-ROLE** — role-delete 409 now names the owning install(s); block scope unchanged (correct). (df6c8dfc)
- **U-prev / U-prov / CM3** — install-preview integrations badge, repo provenance line, diff in git Update-now dialog. (c6a8551d)
- **Knowledge coverage (partial)** — install preview now surfaces bundled agents' knowledge namespaces as a non-blocking note (no backend change). Events/schedules NOT surfaced — no bundle signal exists (their triggers aren't captured at all); needs full H4. (1830ab06)

Also CORRECTED from the original audit: Config does NOT carry a `solution_id` column
(the earlier agent grep false-matched `origin_solution_id`). The entities with a real
`solution_id`: workflows, forms, agents, applications, tables, custom_claims.

Still DEFERRED as product/design work (NOT built): H2 (knowledge corpus deploy),
H3 (file-upload storage), H4 (event/schedule trigger plane), H5 (private-repo auth),
H6 (rollback), H7 (publish/discovery), CM2/CM4 (provenance author field, per-solution
integration reconnect), X-AUTH (non-admin operator access).

## TL;DR — ranked

| # | Lens | Sev | One-liner |
|---|------|-----|-----------|
| **H1** | Regression | **HIGH** | Git-sync stale-entity sweep hard-deletes solution-managed entities via Core, bypassing the guard → silent data loss. **Verified by direct read.** |
| **H2** | Coverage | HIGH | Knowledge/RAG corpus is reference-only — solution ships an agent with an empty knowledge base, no error, wrong answers. |
| **H3** | Coverage | HIGH | Form/table file uploads (S3 `uploads/`) never captured/deployed — "full backup" silently lossy; restored rows dangle file refs. |
| **H4** | Coverage | HIGH | Event sources/webhooks AND schedules/cron can't be shipped — a triggered/scheduled solution installs the workflow but not the thing that runs it; not even surfaced as an unmet need. |
| **H5** | Completeness | HIGH | Private-repo install is unauthenticated — the saved GitHub token isn't threaded into the solution clone; private marketplace repos can't be installed. |
| **H6** | Completeness | HIGH | No rollback — deploy/sync are destructive full-replace with no retained prior artifact; a bad "Update now" is unrecoverable. |
| **H7** | Completeness | HIGH | No publish/discovery — "marketplace" is manually-shared URLs; no publish-to-repo action, no registry/catalog. |
| **F1** | Regression | HIGH | `bifrost run` (local) never sets `solution_id` → a solution workflow run locally resolves the `_repo/` data plane, not its own tables/configs. |
| **F2** | Regression | HIGH | `bifrost solution start` local function host — same omission as F1; the flagship local-dev command can't resolve its own solution's tables. |
| **U1** | UX | HIGH | Solution-managed **table delete** reaches a raw 409 — Tables.tsx doesn't gate the Delete button or catch the error (every other entity surface does). |
| **F3** | Regression | MED | SDK `config` has NO own-first solution resolution on ANY path (tables got `?solution=`, config didn't) — verify how solution config values are stored before sizing. |
| **M-MCP** | Regression | MED | 5 legacy MCP tools (delete_agent, update/delete_table, update_app, update_app_dependencies) lack the early guard → ugly 500 from the backstop instead of clean 409. |
| **M-ROLE** | Regression | MED | A shared role bound to any one solution-managed entity becomes undeletable, even if many non-managed entities use it. |
| **C-MCP** | Coverage | MED | MCP servers/connections are reference-only — agent's `mcp_connection_ids` FK-dangle on a fresh env (bound by raw UUID, not remapped by name like roles). |
| **C-ROLE** | Coverage | MED | Roles are bound by name but never created — role-gated entities install ungated/broken with no unmet-need signal if the role is missing. |
| **CM1** | Completeness | MED | From-repo install: no org targeting + config values are declare-only (read-only) → always "dead until you set configs after." |
| **CM2** | Completeness | MED | No "mine vs external" provenance — no author/origin field; can't tell self-authored from third-party (directly contradicts the user's mental model). |
| **CM3** | Completeness | MED | "Update now" (git sync) shows no diff before a destructive full-replace; no changelog concept. |
| **CM4** | Completeness | MED | No per-solution integration reconnect/disconnect — only a deep-link to the global Integrations page. |
| **U-prev** | UX | MED | Declared connections/integrations omitted from the install preview — required integrations invisible until post-install Setup tab. |
| **U-prov** | UX | MED | Repo URL / subpath / ref shown only inside the Edit dialog — no at-a-glance provenance line on the detail header. |
| **X-AUTH** | Cross-cut | (note) | Every Solutions REST endpoint is `CurrentSuperuser`. If non-admin org operators are in scope, the whole feature is locked to platform admins. |

Plus a set of confirmed-SAFE areas and Low/polish items detailed below.

---

## Lens 1 — Regression (what Solutions broke)

### H1 — Git-sync stale-entity sweep deletes solution-managed entities (VERIFIED, data loss)
`api/src/services/manifest_import.py` `_resolve_deletions` / `_bulk_delete` (1690–1832), called from the
git auto-pull (`github_sync.py:873`, `:1296`) and `POST /api/manifest/import` with
`delete_removed_entities=True` (`files.py:493`).

- None of the bulk-delete base filters exclude managed rows. Verified directly:
  - Workflow `[is_active==True, path.isnot(None)]` (1755)
  - Integration `[is_deleted==False]` (1763)
  - Form `[is_active==True]` (1823); Agent `[]` (1829); App `[]` (1832); CustomClaim `[]` (1812)
  - Config inline sweep filters only `config_schema_id IS NULL` (1770–1772) — NOT `solution_id`
- `present_*_uuids` come from the committed `.bifrost/` manifest, which `generate_manifest` **excludes
  solution-managed rows from** → managed entities are never "present" → all match `id.notin_(present)`
  → swept.
- Deletion is Core `sa_delete` (1716, 1793) → **bypasses the `before_flush` guard** (guard.py:77-84
  only inspects ORM `session.dirty`/`session.deleted`).
- Tables are spared (reported as "keep", 1796-1807) and schema-linked configs are spared. Everything
  else (workflows, forms, agents, apps, claims, schema-less configs) is hard-deleted.
- **User impact:** an operator with a git-connected `_repo/` workspace who runs sync (and confirms the
  deletion prompt, which lists managed entities as generic "removed" with no managed indicator) wipes
  every installed solution's code entities.
- **Fix shape:** add `model.solution_id.is_(None)` to every `_bulk_delete` base filter and the inline
  Config/Table queries. The read/upsert side already excludes managed rows (`github_sync.py:1199`);
  the delete side is the asymmetry.

### F1 — `bifrost run` (local) never sets `solution_id`
`api/bifrost/cli.py` `_run_direct` (~1158-1168) builds `ExecutionContext(...)` with no `solution_id`.
Downstream `api/bifrost/tables.py:38-40` only appends `?solution=` when `ctx.solution_id` is truthy →
omitted → server resolves the plain `_repo/` cascade (own-first branch `tables.py:656-660` never fires).
`cli.py:1278-1291` already detects the solution root for `sys.path` but does **not** read the
descriptor's install id into the context. **Result:** a solution workflow run locally reads/writes the
wrong table (or auto-creates a `_repo/` one). Defeats the "offline dev loop with live data plane" criterion.

### F2 — `bifrost solution start` local function host — same omission
`api/bifrost/solution_dev/function_host.py:93-104` builds the dev ExecutionContext with no
`solution_id` (docstring even says it "mirrors `bifrost run`'s setup" — including the bug). The
flagship Solutions local-dev command can't resolve its own solution's tables by name.

**Root cause for F1/F2:** the solution descriptor's install id is never read into the locally-built
context. The server path is correct because the consumer re-resolves `solution_id` from the DB workflow
row (`workflow_execution.py:570`, `service.py:194`, `engine.py:296`, `worker.py:297`); local paths have
no DB row and don't read the descriptor.

### F3 — SDK `config` has no own-first solution resolution on ANY path (MED)
`api/bifrost/config.py` (63-67, 113-123, 157-162) sends only `scope`, never a solution param. Server
`cli.py` config endpoints (401-560) + `ConfigRepository.merged_for_sdk()` (`repositories/config.py:149`)
are a plain org+global cascade with zero solution awareness. `request.solution` exists ONLY on the
integrations endpoint (424 escalation), not config. Unlike tables, a solution workflow's
`config.get("key")` can never resolve the install's own config value first — **on every path including
the server engine**. Verify how deployed solution config values are stored (if all org-tier, impact is
lower) before sizing.

### M-MCP — 5 legacy MCP tools lack the early guard (MED, ugly-500 not bypass)
SELECT via `apply_mcp_org_scope` (no `solution_id` filter) → loads the managed row → mutates ORM object
→ backstop raises (ugly error_result, leaves shared session dirty) instead of a clean 409:
`delete_agent` (agents.py:674), `update_table` (tables.py:345), `delete_table` (tables.py:438),
`update_app` (apps.py:373), `update_app_dependencies` (apps.py:1140). Siblings (`update_agent`,
`update_form`, `publish_app`, `push_files`) DO guard early. `test_mcp_thin_wrapper.py` doesn't cover
these legacy tools.

### M-ROLE — shared role becomes undeletable (MED)
`assert_role_not_bound_to_solution_managed` (guard.py:134-153, called roles.py:305) 409s if the role is
bound to ANY managed entity, regardless of non-managed usage. A shared "Technician" role referenced by
one installed solution can't be deleted; the error says "redeploy the solution without it" without
saying which. Likely the intended Codex-R4 tradeoff (FK CASCADE would silently strip deploy-owned
bindings) but a real operator foot-gun in the multi-solution case.

### Confirmed SAFE (no regression)
- **OAuth/integration connect:** Integration/IntegrationMapping aren't solution-managed; connect path
  only mutates IntegrationMapping and reads the Integration row. Setting `oauth_token_id` on a
  solution-declared integration works (mappings/tokens are instance-owned by design).
- **Scheduling execution:** schedule rows are non-managed and freely creatable against managed
  workflows; the cron scheduler never loads/mutates the Workflow ORM object, so the backstop can't fire
  and managed workflows aren't hidden from scheduling. (But solutions can't *ship* a schedule — see H4.)
- **Events CRUD, Roles grant/revoke on non-managed entities:** open and correct.
- **Server + all scheduled/event execution paths:** `solution_id` propagation is correct and complete
  (single DB re-resolution point in the consumer).
- **Table ROW (data) writes:** correctly NOT guarded (only `Table` carries `solution_id`, not
  `Document`) — runtime row writes by a solution workflow are allowed. The `_run_direct` post-back path
  can't trip the guard (`Execution` has no `solution_id`).

---

## Lens 2 — Completeness (install / update / manage / publish)

**Strong:** install-from-zip/repo/subfolder, preview, unmet-dependency blocking, capture, export
(shareable vs full+password vs include-data), setup wizard, clean uninstall with data-orphaning,
orphan re-adopt, update-available detection (6h poll), upgrade entity-diff on the zip/repo path.

**Holes:**
- **H5 — private-repo install unauthenticated.** `clone_repo_to_dir` (git_sync.py:235) does a bare
  `GitRepo.clone_from` with no token injection; the saved GitHub token is used only for repo *creation*.
  Real MSP solution repos are private → install-from-repo silently fails on them.
- **H6 — no rollback.** No rollback endpoint, no version-history, no retained prior bundle.
  `upgraded_from_version` is an informational string only. Deploy is destructive full-replace.
- **H7 — no publish/discovery.** No "publish my installed solution to a repo" action, no registry/
  catalog API. "Marketplace" = word-of-mouth URLs (explicit design decision, but contradicts the
  user's marketplace mental model).
- **CM1 — from-repo install:** no org selector in `RepoBody`, and config values are declare-only
  (read-only) → always dead-until-configured-after.
- **CM2 — no "mine vs external" provenance.** No author/publisher/origin field on `Solution`. UI shows
  Git vs Manual, not authored-by-me vs external. Directly contradicts the user's mental model.
- **CM3 — "Update now" (git sync) shows no diff** before full-replace; no changelog.
- **CM4 — no per-solution integration reconnect/disconnect** (only a deep-link to global Integrations;
  the UI "Reconnect" is about the *git repo*, not OAuth).
- **Pin-to-version is partial:** `git_ref` pins a branch/tag, but update detection reads the descriptor
  `version:` at the ref HEAD, so you can't "pin to v1.2.3 and ignore newer."
- **CLI gaps:** no dedicated `bifrost solution update` or `uninstall`/`delete` (UI-only delete; updates
  via re-`install`).

**X-AUTH (cross-cutting):** every `routers/solutions.py` endpoint is `CurrentSuperuser` (78, 112, 118,
…). If non-admin org operators should manage their own org's solutions, the whole feature is locked out.

---

## Lens 3 — Coverage (what doesn't hook into Solutions but should)

Coverage matrix (cols: has `solution_id` / captured / deployed / round-trips / clean uninstall / guarded):

| Capability | Status |
|---|---|
| Workflows, Python source/`modules/`, Tables(schema), Apps(v2), Forms, Agents, Custom claims, Config declarations, Integration declarations | **Full** — owned, deployed, round-trip, clean uninstall, guarded. The definitional core is solid. |
| Table DATA (rows) | Partial — full-backup/include-data only, 50k-row cap with silent WARNING (capture.py:72,709). |
| Config VALUES | Instance-owned, secrets blob only — coherent split. |
| **Knowledge / RAG corpus** | **NONE** — `Agent.knowledge_sources` is a `list[str]` of names; the `KnowledgeStore` rows are never captured/deployed/uninstalled; no `solution_id`. |
| **Form/table file uploads (S3 `uploads/`)** | **NONE** — `include_data` copies row JSON verbatim but doesn't follow file-key refs into S3. |
| **Event sources / webhooks / subscriptions** | **NONE** — no `solution_id`, not in walker/capture/deploy. |
| **Schedules / cron** | **NONE** — same. |
| MCP servers / connections | Reference-only — agent `mcp_connection_ids` re-bound by raw UUID, never created; FK-dangle on fresh env. |
| Roles | Bound by name, never created; silent ungating on missing role. |

**Storage (Jack's prime suspect) is confirmed as a real gap on TWO axes:**
- **H2 — vector store (RAG):** a knowledge-backed agent installs pointing at an empty namespace → no
  error, empty retrieval, wrong answers (worst failure mode). No lifecycle (uninstall leaves the
  corpus; redeploy can't manage it).
- **H3 — object storage (uploads):** "full backup" with file-bearing tables restores rows whose file
  keys point at objects that don't exist in the target → attachments 404. Lossy with no warning.

**H4 — trigger plane (events + schedules):** the single most common automation shape — a scheduled or
webhook-triggered workflow — cannot be shipped as a self-contained Solution. The workflow deploys; the
trigger doesn't; and unlike integrations it isn't even surfaced as an unmet need, so the install looks
complete but is inert.

**The pattern to copy:** integration declarations (`SolutionConnectionSchema` + empty-shell creation +
unmet-need surfacing in the walker) are the coherent model. Knowledge, events, schedules, and MCP all
lack this equivalent.

---

## Lens 4 — UX-surfacing (state the backend knows but the UI hides)

Good news first: `solution_id` / `is_solution_managed` ARE returned on every entity contract
(forms.py:321, agents.py:152, tables.py:99, applications.py:155, workflows.py:77), and managed
badges/lock banners + disabled Save render across Forms/Agents/Apps/Tables/Claims/Workflows. The app
editor even proactively translates the 409 (`AppCodeEditorLayout.tsx:106-121`) — the gold standard.

**Gaps:**
- **U1 (HIGH) — managed table delete → raw 409.** `Tables.tsx` Edit/Delete buttons (414-435) aren't
  gated on `is_solution_managed` and `handleConfirmDelete` (119-128) has no try/catch. The badge is
  already rendered two columns over (370). Gate the Delete + translate the 409.
- **U-prev (MED) — declared connections omitted from install preview.** `SolutionInstallPreview.connection_schemas`
  (solutions.py:355) exists; `CreateEditSolution.tsx` `EntitySummary` (161-193) lists everything but
  connections. Required integrations invisible until the post-install Setup tab.
- **CM3/M2 (MED) — no diff in the git "Update now" flow.** `SolutionUpgradeDiff` is computed only for
  the zip/repo path (`UpgradeDiffView`); the git-sync confirm (SolutionDetail.tsx:1388) just says
  "replaces the installed content."
- **U-prov (MED) — provenance only in the Edit dialog.** `git_repo_url`/`repo_subpath`/`git_ref`
  (solutions.py:90-94) aren't shown as an at-a-glance "tracking github.com/org/repo @ main /subpath"
  line on the detail header.
- **Low/polish:** capture dialog hardcodes "Static scan" instead of reading `scan_is_static`
  (SolutionCaptureDialog.tsx:381); uninstall toast omits `claims_deleted` / `config_declarations_deleted`
  counts; `WorkflowListSurface.tsx` "Open in editor" (326) not gated for managed workflows (relies on
  editor's own read-only handling).

---

## Suggested fix sequencing (post-desloppify)

1. **H1 first — it's data loss.** One-file fix: add `solution_id IS NULL` to the manifest_import
   deletion filters. Smallest blast radius, biggest risk reduction. Add a guard-installed e2e test that
   git-syncs a `_repo/` workspace with an installed solution and asserts the managed entities survive.
2. **U1 — managed table delete raw 409.** Tiny client fix, user-visible wall.
3. **F1/F2/F3 — local-execution data-plane parity.** Read the solution descriptor's install id into the
   locally-built ExecutionContext (`bifrost run` + `solution start`), and give config the same own-first
   `?solution=` treatment tables got. These three share a root cause.
4. **Coverage decisions (product calls, not bugs):** H2 (knowledge), H3 (upload storage), H4 (trigger
   plane) each need a design — likely following the `SolutionConnectionSchema` declaration+shell+unmet-need
   pattern. Decide scope before building; at minimum, surface them as unmet needs so installs don't look
   complete when they're inert.
5. **Completeness holes (H5 private-repo auth, H6 rollback, H7 publish/discovery)** are the
   already-recorded marketplace follow-ups; H5 is the most user-blocking of the three.

## Verification notes
- H1 and the F1/F2/F3 propagation table were verified by direct file reads in this session; the rest
  carry agent-cited `file:line` evidence and should be spot-checked at fix time.
- Several findings (F1/F2 user-visible symptom, F3 config storage shape, M-ROLE multi-solution case)
  are flagged "needs live verification" — drive them on the debug stack before committing fixes.
