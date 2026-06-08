# Solutions scope/cascade/read-only/UI shakeout — findings (fan-out, 2026-06-08)

## STATUS (2026-06-08): all 5 reproduced backend bugs FIXED + verified live; UI/UX deferred

Fixed, each TDD + driven live, committed on `worktree-solutions-success-criteria`:
- **#2 (CRIT)** export crash (`from src` in `bifrost/manifest.py`) → self-contained `ClaimQuery`
  in the manifest; CLI-import guard broadened. Export produces a bundle live.
- **#3 (HIGH)** deploy dropped `functions/` Python → `_collect_python_files` layout-agnostic
  (git_sync reuses it); scaffold indexes its sample in `workflows.yaml`. Deploy→execute live.
- **#5 (HIGH)** `_repo` table couldn't share a name with a solution table → `get_by_name_strict`
  opt-in `repo_namespace_only` on the create check.
- **#4 (HIGH)** `/remap` rewrote solution-managed forms/agents → guard excludes managed rows from
  WHERE + count (form + agent-tool paths).
- **#1 (CRIT)/F2** solution workflow couldn't read its own table → `solution_id` rides the
  ExecutionContext (no new header); SDK appends `?solution=`; resolver own-first off
  `ctx.solution_id` OR `ctx.app_id`. Verified live (own row resolves; `_repo` caller gets 0).

**Still OPEN / deferred:** UI/UX findings (see UI section) + the **global-fallback gate
follow-up** — tables/configs should honor `global_repo_access` like the virtual module loader
(`module_cache_sync._candidate_storage_paths`); the flag is already in context
(`solution_global_repo_access`), carry it like `solution_id`. Critic gaps (embed-token app_id
path, forms/agents as resolution sources, org-scoped global_repo_access, a live-browser UI pass)
also remain.

---

6 parallel agents drove the live stack across 6 risk axes + a completeness critic. **19 reproduced non-OK** of 46 total. Severity: {'critical': 2, 'high': 3, 'medium': 6, 'low': 8, 'info': 27}.

**Scope leak (the #1 fear) verified CLEAN empirically** — cross-org isolation, same-slug independence, own-first determinism, S3 isolation, forged X-Bifrost-App org-gate, superuser-override org-pin all reproduced safe.


## CRITICAL

### [lifecycle-roundtrip/correctness] Solution workflow cannot resolve its OWN solution-managed table by name (insert 404s loudly; query/count 404 silently into empty)
- **did:** Installed solution 'lab-sol' (slug) to OrgA (237c62c0-80f8-4031-a7ab-1fe48268c812) via `bifrost solution install /tmp/lab-sol.zip --org <OrgA>`. It owns a solution-managed table lab_widgets (id 778cbea6-..., solution_id set). Ran the solution's own workflow as OrgA: `bifrost workflows execute lab_seed --org <OrgA> --params '{"name":"gamma"}'` -> Failed: '404 Not Found: Table lab_widgets not found'. Ran `lab_count` -> Success but count=0 (empty). Isolated with curl as superuser scope=OrgA: POST /api/tables/lab_widgets/documents -> 404; POST /api/tables/778cbea6.../documents (by ID) -> 201; POST /api/tables/lab_widgets/documents/query -> 404. Contrast: the SAME workflow code imported into OrgB as plain _repo entities resolves lab_widgets BY NAME and works (seed count=1, query returns the row).
- **observed:** A workflow that belongs to a solution install cannot read or write its own solution-managed table by NAME. tables.insert() returns HTTP 404 (table not found) and fails the run; tables.query()/count() also get 404 but the SDK swallows 404 into an empty DocumentList (tables.py query: `if response.status_code==404: return DocumentList(documents=[],total=0,...)`), so a uninstall-shaped empty result is silently returned. Only insert/query BY ID works. Root cause: get_table_or_404 (api/src/routers/tables.py:567) resolves solution tables only via _resolve_solution_table_by_name (line 620), which keys off ctx.app_id (the X-Bifrost-App header). That header is set ONLY by solution v2 APPS, never by workflow execution. The engine knows solution_id (api/src/jobs/consumers/workflow_execution.py:570) but ExecutionContext (api/bifrost/_execution_context.py) has no solution_id/app_id field and the SDK client (api/bifrost/client.py) sends no solution/app header, so the table router falls to repo.get_by_name which excludes solution_id IS NOT NULL rows (api/src/repositories/org_scoped.py:221). config.get works because Config is resolved by org+key (not solution_id-excluded unless orphaned).
- **expected:** A solution workflow referencing its own solution table by name should resolve solution-first to the table owned by that install (the same way solution apps do via the app header). This is the F2 'centralize solution-first resolution' item from project memory. Tables are runtime-writable (criterion 7), so insert/query by name from a solution workflow must hit the install's table.
- **code:** api/src/routers/tables.py:567-657; api/bifrost/_execution_context.py:74; api/src/jobs/consumers/workflow_execution.py:570  ·  reproduced=True

### [lifecycle-roundtrip/correctness] bifrost export --portable crashes: ModuleNotFoundError: No module named 'src' (entire export half of round-trip unusable)
- **did:** From a solution workspace (/tmp/sol-lab with bifrost.solution.yaml + .bifrost/*.yaml + workflows/) ran `bifrost export --portable /tmp/exp-bundle` using the API-matched installed CLI (/tmp/bifrost-shakeout/.venv/bin/bifrost, v0.9.1-268-g68b8c44b). Also minimal repro: `python -c 'from bifrost.manifest import MANIFEST_FILES'`.
- **observed:** Export aborts with a traceback ending: File bifrost/manifest.py line 23 `from src.models.contracts.claims import ClaimQuery` -> ModuleNotFoundError: No module named 'src'. export.py:_parse_manifest_files imports bifrost.manifest lazily, so export crashes the moment a bundle contains .bifrost manifest files. The packaged CLI has no `src` on sys.path (the known 'Solutions CLI src.* import pitfall'). bifrost import survives because import_cmd.py does not import bifrost.manifest.
- **expected:** `bifrost export --portable` should produce a scrubbed bundle. It is the documented first half of the export/import round-trip; right now a real user cannot export a workspace/solution at all from the installed CLI. bifrost/manifest.py must not do a top-level `from src...` import (move it behind a function or vendor ClaimQuery into the bifrost package).
- **code:** api/bifrost/manifest.py:23; api/bifrost/commands/export.py:63  ·  reproduced=True


## HIGH

### [global-repo-access/correctness] bifrost deploy never bundles the scaffold's own sample workflow (functions/ dir not collected)
- **did:** First deploy attempt placed the workflow at functions/probe.py (the SAME location the scaffold writes its sample: _SAMPLE_WORKFLOW_PATH='functions/hello.py', solution.py:44, and App.tsx defaults to useWorkflow('functions/hello.py::main')). Deployed and listed _solutions/{id}/ in S3.
- **observed:** Deploy reported '1 workflow(s) upserted' (the Workflow ROW was created) but ZERO Python source was written to S3 — _solutions/{id}/ was empty. _collect_python_files only scans _PY_SOURCE_DIRS=('workflows','modules','shared') (solution.py:36,398-408), so functions/*.py is silently dropped. A deployed install whose app calls the scaffold's default 'functions/hello.py::main' has a workflow row with no resolvable code → the worker's get_module_sync returns None → WorkflowLoadError at run time. Moving the file under workflows/ fixed it. This is tangential to the global-repo axis but is a real first-run breakage of the documented scaffold path.
- **expected:** The scaffold's sample workflow location (functions/) must be collected by deploy, OR the scaffold must not write/reference functions/. The deploy collection roots and the scaffold's sample-workflow path must agree.
- **code:** api/bifrost/commands/solution.py:36,44-45,398-408  ·  reproduced=True

### [readonly-enforcement/enforcement-gap] POST /api/workflows/{id}/remap silently rewrites a solution-managed FORM's workflow_id outside deploy (no guard at all)
- **did:** Pointed the solution-managed form 98f2e2b1... at the managed source workflow 0e1eb84f... (deploy-state simulation). As the dev superuser: curl POST /api/workflows/0e1eb84f.../remap -d '{"target_workflow_id":"a894ad64-..."}'. Then queried forms.workflow_id.
- **observed:** 200 OK, response {"updated":{"forms":1,...}}. The solution-managed form's workflow_id was rewritten from the source to the target workflow while solution_id stayed set. remap_workflow_references (api/src/services/workflow_orphan.py:418) has ZERO solution guards and mutates Form via Core update() (bypasses before_flush) and AgentTool.workflow_id via junction-ORM (also uncaught). The endpoint api/src/routers/workflows.py:1790 has no assert_not_solution_managed.
- **expected:** Repointing references of/under a solution-managed entity (form/agent that the deploy bound to a workflow) must be refused with the locked 409, or skip managed rows. Deploy must remain the sole writer of a managed entity's portable workflow binding.
- **code:** api/src/routers/workflows.py:1790 remap_workflow_references; api/src/services/workflow_orphan.py:418 (0 guards), :450 Core update(Form), _remap_agent_tool_references  ·  reproduced=True

### [lifecycle-roundtrip/enforcement-gap] Cannot create a _repo table whose name matches an existing solution-managed table (409) — contradicts the partial-unique-index design that permits coexistence
- **did:** With solution-managed lab_widgets present in OrgA (no _repo version), ran `curl -X POST /api/tables?scope=<OrgA> -d '{"name":"lab_widgets",...}'` -> 409 {'detail':"Table 'lab_widgets' already exists"}. Confirmed the reverse order DOES coexist: created _repo coexist_tbl FIRST, then installed a solution shipping coexist_tbl -> install succeeded, both tables exist independently (ids fc171335 solution_id=None and 1a3807c9 solution_id set).
- **observed:** create_table's existence check (api/src/repositories/tables.py:67 -> get_by_name_strict line 58) filters only by name + organization_id, WITHOUT excluding solution-managed rows (no `solution_id IS NULL`). So when a solution table of a name exists, a normal _repo table of that name is rejected 409 — even though migration 20260606_table_name_solution_scope explicitly created separate partial unique indexes (`WHERE solution_id IS NULL` vs `WHERE solution_id IS NOT NULL`) precisely so the two namespaces coexist. Coexistence therefore only works if the _repo table is created first; it is blocked if the solution table lands first. Asymmetric and surprising.
- **expected:** Per the migration's stated intent ('two solutions may each ship a users table'; _repo and solution namespaces are separate), get_by_name_strict in create_table should exclude solution-managed rows (solution_id IS NULL) so a _repo table can always be created regardless of solution tables of the same name. The DB would accept it; only the app-layer check blocks it.
- **code:** api/src/repositories/tables.py:58-75; api/alembic/versions/20260606_table_name_solution_scope.py  ·  reproduced=True


## MEDIUM

### [cascade-matrix/correctness] CONFIG values cannot disambiguate between two solutions installed in the same org — sibling installs sharing a config key COLLIDE on the org-level unique index
- **did:** With an OrgA config value already present for key 'cas_key' (origin_solution_id=SOL_OWN), attempted to INSERT a second OrgA Config row for the same key with origin_solution_id=SOL_SIB (simulating a second/sibling solution in the same org declaring the same key). Also inspected the schema: configs unique index is (COALESCE(integration_id), COALESCE(organization_id), key); solution_config_schema unique index is (solution_id, key).
- **observed:** INSERT failed: duplicate key value violates unique constraint ix_configs_integration_org_key (integration_id, organization_id, key). Config SCHEMA declarations are per-install (solution_config_schema keyed by solution_id,key, no collision), but the resolved config VALUE lives in shared org space keyed only by (integration_id, organization_id, key) — there is no solution dimension at the value layer. Two sibling solutions in one org declaring the same key share ONE value with no per-solution disambiguation; whichever deploys second to set a value hits the constraint.
- **expected:** This is a real design asymmetry vs workflows/tables, which DO disambiguate sibling installs by solution_id. For configs, sibling installs in the same org sharing a key are conflated. Deploy only writes schema declarations (not values), so a fresh deploy won't crash, but: (a) operator-set values are shared/ambiguous across sibling solutions, and (b) any path that writes a Config value for solution B's key when solution A already owns one in the org will fail the unique constraint. Worth an explicit product decision: either namespace solution config keys, or document that config keys are org-global and sibling solutions must not collide.
- **code:** api/src/models/orm migration: ix_configs_integration_org_key; api/src/services/solutions/deploy.py:1016-1042  ·  reproduced=True

### [readonly-enforcement/enforcement-gap] before_flush backstop does NOT cover collection/junction writes — a role binding to a solution-managed agent commits successfully
- **did:** In-container (real get_db_context session, backstop installed): (a) loaded the managed agent, db.refresh(agent,['roles']), agent.roles.append(role), commit; (b) db.add(AgentRole(agent_id=<managed agent>, role_id=..., assigned_by='attacker')), commit.
- **observed:** (a) The INSERT into agent_roles was issued to the DB (backstop did NOT fire); it only rolled back on an unrelated NOT-NULL on assigned_by. (b) With assigned_by set, the junction INSERT COMMITTED — a role binding was added to a solution-managed agent with no SolutionManagedWriteError. before_flush uses session.is_modified(obj, include_collections=False), so a pure collection append doesn't mark the managed parent dirty, and the junction ORM object carries no solution_id.
- **expected:** The 'global' backstop should reject any flush that binds/alters a deploy-owned property of a managed entity, including junction rows. Its docstring claims it catches writes 'even if a mutation surface forgets the explicit guard'.
- **code:** api/src/services/solutions/guard.py:79-84  ·  reproduced=True

### [ui-walkthrough/inconsistency] Uninstall uses a bespoke type-to-confirm Dialog instead of the app's AlertDialog destructive pattern
- **did:** Compared the uninstall flow (Dialog + Input type-to-confirm + destructive Button) in Solutions.tsx:622-702 / SolutionDetail.tsx:658-731 to the canonical destructive flow in Roles.tsx:287-306 (AlertDialog/AlertDialogAction).
- **observed:** Every other destructive action in the app (delete role, delete table doc, etc.) uses shadcn AlertDialog with AlertDialogAction styled `bg-destructive`. Solutions reinvents this with a plain Dialog plus a hand-rolled type-the-name gate. The type-to-confirm itself is reasonable for a heavyweight uninstall, but it is a one-off pattern not used anywhere else, so it looks and behaves differently (no AlertDialog semantics/focus trap conventions, different button styling path).
- **expected:** Either adopt AlertDialog for visual/behavioral consistency, or if type-to-confirm is desired, factor it into a shared confirm component so it is recognizably part of the system rather than a Solutions-only divergence.
- **code:** client/src/pages/Solutions.tsx:622-702  ·  reproduced=True

### [ui-walkthrough/ux] Managed entities give non-admins zero indication why edit controls vanished
- **did:** Read SolutionManagedBadge.tsx:21-22 (returns null unless isPlatformAdmin) together with Forms.tsx:420-425, Workflows.tsx:475-480, Applications.tsx:289-293, FleetPage.tsx:484 (edit controls gated on `!is_solution_managed`).
- **observed:** On a solution-managed entity, the edit/delete buttons are hidden for everyone, but the 'Managed' lock badge that explains why renders only for platform admins (`if (!isPlatformAdmin ...) return null`). A non-admin who could otherwise manage forms/apps sees the entity with no action buttons and no badge, label, tooltip, or banner explaining the entity is solution-owned.
- **expected:** The read-only affordance (some visible lock/label or tooltip) should be shown to any user who would otherwise have management rights, not only platform admins. Otherwise managed entities look like a permissions bug to a non-admin operator.
- **code:** client/src/components/solutions/SolutionManagedBadge.tsx:21-22  ·  reproduced=True

### [ui-walkthrough/correctness] 'Managed' badge is a nested <Link> inside list rows that are themselves links/clickable
- **did:** Read SolutionManagedBadge.tsx (renders react-router <Link>) and its mount sites: Forms.tsx:420 (inside a form card with a Launch <Link>/clickable region), Workflows.tsx:475, Applications.tsx:289-292, FleetPage.tsx:484/710.
- **observed:** The badge renders an `<a>` (react-router Link to /solutions/{id}). Several mount contexts place it inside an already-clickable/anchor row. Nested interactive anchors are invalid HTML and produce inconsistent hydration/click behavior; the component leans on `onClick={(e)=>e.stopPropagation()}` to paper over it, which stops bubbling but does not fix the nested-anchor DOM nesting.
- **expected:** The badge should not be an <a> when it can land inside another <a>. Use a button-with-navigate or render the lock as a non-interactive chip plus a separate explicit link, to avoid invalid nested-anchor markup.
- **code:** client/src/components/solutions/SolutionManagedBadge.tsx:23-34  ·  reproduced=False

### [ui-walkthrough/ux] Install dialog scope defaults to Global with no warning, and required-config gate is a soft yellow note that does not block install
- **did:** Read Solutions.tsx install dialog: previewMutation.onSuccess sets scopeOrgId='__global__' (line 184), Install button disabled only on `!preview || pending` (605-609), required-missing renders a yellow 'Required — you can still install and set this later.' note (574-580).
- **observed:** Two things: (1) Scope silently defaults to Global (cross-org) every time, so a hurried operator installs platform-wide by default with no confirmation. (2) A required config can be left blank and Install stays enabled; the only signal is a small yellow note. Combined, the happy path lets you one-click a Global install that cannot actually run (required config unset) with no hard stop or summary of consequences.
- **expected:** Either make Scope an explicit required choice (no pre-selected Global), or surface a clear 'installing globally / to N orgs' confirmation. The required-config behavior is defensible by design but should at least be summarized in the footer ('1 required value still unset') rather than only inline.
- **code:** client/src/pages/Solutions.tsx:183-184, client/src/pages/Solutions.tsx:603-609  ·  reproduced=True


## LOW

### [scope-leak/correctness] Ambiguous solution-only path::fn resolves non-deterministically for a system/no-app caller (rows[0], DB order)
- **did:** With TWO same-org solution installs sharing workflows/main.py::scope_probe and NO _repo row for that path, POST /api/workflows/execute with the bare path and NO app_id (system/non-app caller, superuser, org_id=OrgA).
- **observed:** It resolved to rows[0] = sib-a's 8b763aaa — an arbitrary pick by DB order. With sib-a and sib-b both matching and no _repo row, the resolver's final fallback (workflows.py:151 `return repo_rows[0] if repo_rows else rows[0]`) returns the first solution row.
- **expected:** This is NOT a cross-org leak (both rows are in-scope OrgA, both readable by this caller) and the supported solution-app path always carries app_id->solution_scope (deterministic). But a non-app caller using a bare path that two solution installs share gets an arbitrary install — worth a deterministic tiebreak or an explicit error rather than silent rows[0].
- **code:** api/src/repositories/workflows.py:147-151  ·  reproduced=True

### [cascade-matrix/inconsistency] WORKFLOW resolver: a no-install (system) caller CAN resolve a solution-owned workflow when no global _repo/ row shares the path — inconsistent with the TABLE resolver, which 404s
- **did:** Path 'workflows/sibonly.py::run' had ONLY one solution-managed row (solution_id=SOL_SIB, org=OrgA) and no global/_repo row. Resolved as a no-install system caller scoped to OrgA: WorkflowRepository(org_id=OrgA, ...).resolve('workflows/sibonly.py::run', solution_scope=None). Then ran the parallel TABLE case (name 'sibtbl', only a SOL_SIB row) via the real HTTP endpoint POST /api/tables/sibtbl/documents/query?scope=OrgA with no X-Bifrost-App header.
- **observed:** WORKFLOW: no-install caller scoped to OrgA resolved the lone solution row (returned 'sibonly') via the final fallback `return ... rows[0]` at workflows.py:151. TABLE: the analogous no-app caller returned 404 ('Table sibtbl not found') because _resolve_solution_table_by_name only fires for an app caller and repo.get_by_name restricts to solution_id IS NULL.
- **expected:** The two solution-aware resolvers should agree on whether a non-solution caller may reach a solution-only entity by shared path/name. Today workflows say yes (in-scope, so not a cross-org leak — but it lets a system/_repo caller execute a solution's workflow), tables say no. The workflow docstring at :147-151 claims it 'prefers the _repo/ row' and only falls back 'when there is no _repo/ row' — which is exactly this branch, so it is intended-by-comment, but it diverges from the table behavior. Worth aligning or documenting the divergence. Not a scope leak: org cascade still confines it to the caller's own org (OrgB/global callers got None).
- **code:** api/src/repositories/workflows.py:151 vs api/src/routers/tables.py:606-657  ·  reproduced=True

### [ui-walkthrough/inconsistency] h1 sizing is inconsistent across Solutions surfaces and vs. the rest of the app
- **did:** Read Solutions.tsx:324, SolutionDetail.tsx:509, and compared to Roles.tsx:158 (the house style for a list page).
- **observed:** Solutions list h1 = `text-3xl font-extrabold tracking-tight sm:text-4xl`. SolutionDetail h1 = `text-3xl font-extrabold tracking-tight` (no responsive bump). Roles/Apps house style h1 = `text-4xl font-extrabold tracking-tight`. Three different header sizes across two Solutions pages and the rest of the app.
- **expected:** Page titles should match the app-wide convention (`text-4xl font-extrabold tracking-tight`). Solutions list responsively shrinks to 3xl on mobile while every other top-level page does not; the detail page is permanently a size smaller than its own list page.
- **code:** client/src/pages/Solutions.tsx:324, client/src/pages/SolutionDetail.tsx:509  ·  reproduced=True

### [ui-walkthrough/inconsistency] Solutions list has no search, sort, or refresh — diverges from every other list surface
- **did:** Compared Solutions.tsx header (lines 322-335) to Roles.tsx (SearchBox + sort + Refresh button) and Tables/Config (OrganizationSelect filter + Show-orphaned).
- **observed:** Solutions list offers only an 'Install Solution' button. No search box, no sort, no org/scope filter, no manual refresh. Roles, Tables, Config, Forms, Workflows, Apps all provide at least search and most provide a scope filter and refresh.
- **expected:** Even as a card grid, the list should match house affordances: a search box and (given installs are org-scoped) a scope filter. With many installs this page becomes an unfilterable wall of cards.
- **code:** client/src/pages/Solutions.tsx:322-335  ·  reproduced=True

### [ui-walkthrough/inconsistency] Delete-confirmation copy differs between list card and detail page (orphaned-data hint dropped)
- **did:** Diffed the two uninstall DialogDescriptions: Solutions.tsx:636-653 vs SolutionDetail.tsx:670-687.
- **observed:** The list-card dialog tells the user orphaned tables/configs 'remain visible via "Show orphaned" on the Tables and Configs pages.' The detail-page dialog omits that entire clause ('— they will be reattached if you reinstall this Solution.' and stops). Same destructive action, two different explanations of where the kept data goes.
- **expected:** Identical copy for the same operation. The 'Show orphaned' discoverability hint is the most useful sentence and it is missing from the detail-page path that a user is more likely to use.
- **code:** client/src/pages/SolutionDetail.tsx:676-683  ·  reproduced=True

### [ui-walkthrough/ux] Entity-tab row 'open' affordance is a back-chevron rotated 180° instead of a chevron-right
- **did:** Read SolutionDetail.tsx EntityTabContent (lines 138-151): `<ChevronLeft className="... rotate-180" />`.
- **observed:** Each entity row's trailing 'go to entity' indicator imports ChevronLeft and CSS-rotates it 180° to fake a right chevron. ChevronRight exists in lucide and is used elsewhere. This is a code smell that signals the icon set wasn't curated, and the rotated glyph can have subtly different optical alignment than the real ChevronRight.
- **expected:** Use ChevronRight directly.
- **code:** client/src/pages/SolutionDetail.tsx:147  ·  reproduced=True

### [ui-walkthrough/ux] Edit Solution dialog exposes raw 'Git connected' toggle + free-text repo URL with no validation or coupling
- **did:** Read EditSolutionDialog in SolutionDetail.tsx:344-382.
- **observed:** The dialog has an independent 'Git connected' switch and a separate free-text 'Git repository URL' input. Nothing couples them: you can toggle Git-connected ON with an empty URL, or fill a URL while the toggle is OFF, and there is no URL format validation (placeholder is the only hint). 'Global repo access' is a third raw switch with a one-line description. This reads as a settings dump rather than a guided form.
- **expected:** Couple the toggle and URL (URL required/enabled only when connected, basic URL validation), or hide the URL field when not git-connected. As-is the operator can save a self-contradictory state.
- **code:** client/src/pages/SolutionDetail.tsx:360-382  ·  reproduced=True

### [ui-walkthrough/ux] Solution list cards are missing description, entity-count summary, and status — sparse compared to the install preview
- **did:** Read the card body in Solutions.tsx:384-436 and compared to the rich EntitySummary chips shown in the install preview (Solutions.tsx:96-136) and to Roles rows (which show description + consumer chips).
- **observed:** An installed-solution card shows only name, slug, a scope badge, and a Git/Manual badge. It does not show how many workflows/apps/forms/etc the install contains, nor any required-config-unset warning, even though the detail page and the install preview both compute exactly those. The user must open each card to learn whether an install is even runnable.
- **expected:** Surface the entity-count chips (reuse EntitySummary) and a 'needs config' indicator on the card, mirroring the preview, so the list is informative at a glance like Roles' consumer chips.
- **code:** client/src/pages/Solutions.tsx:384-436  ·  reproduced=True


## Completeness gaps (critic — untested combinations)

- **[high]** Embed-token (JWT-baked app_id) path for BOTH workflow and table solution-scoping was never driven — every agent used only the X-Bifrost-App header — Solution-first resolution for workflows is keyed off request.app_id with an unauthenticated app→solution_id lookup (no org/ownership gate at workflows.py:764). The scope-leak agent's forged-header test exercised the table path, not this workflow JWT/body path. solution_scope only narrows WITHIN rows already org-cascade-filtered, so a leak requires a foreign solution row to appear in `rows` first — that exact interaction (foreign app_id supplied by an OrgB caller against an OrgA-owned app) was never reproduced.
- **[high]** Solution-managed FORMS and AGENTS as resolution sources (not just managed targets) were never deployed and exercised end-to-end across the solution->org->global cascade — The form->workflow and agent-tool->workflow resolution paths under solution scope are completely undriven. readonly's HIGH /remap finding shows AgentTool.workflow_id and Form.workflow_id are exactly the fields that get rewritten — yet their RESOLUTION under the own>global>never-sibling rule was never tested for a managed form/agent.
- **[high]** global_repo_access was tested ON and OFF, and toggled, but ONLY for global-scoped installs — never for an ORG-scoped install, and never mid-flight with a warm/concurrent worker — The 'disable global doesn't actually work' fear was only validated against global-scoped installs with serial executions. An org-scoped install (the common real case) plus the concurrent/warm-worker race (two executions of the same install in flight while the flag is PATCHed) were never reproduced. The one-shot fork claim rests on serial runs only.
- **[medium]** Package (__init__.py) imports and PEP-420 namespace packages under global_repo_access OFF were not distinguished — Only the flat dynamic-import case was driven. A solution that vendors a real Python package (with __init__.py) sharing a top-level name with a _repo/ package was never tested under global OFF — a plausible place for the path-prefix gate to leak a _repo/ submodule.
- **[medium]** Integration-scoped configs (integration_id NOT NULL) under solution cascade were never tested — only instance configs — Config cascade was validated only for the integration_id=NULL branch. Whether a solution-shipped integration config collides/cascades correctly across solution->org->global with two installs is unknown.
- **[medium]** Orphan -> reattach cycle was never re-checked for a NAME-RESOLUTION leak; and reattach was only driven for configs/tables, not forms/agents — The window where a row is orphaned (solution_id NULL but still org-scoped) is exactly when the solution-row exclusion from the name cascade no longer applies. No agent drove a get(name=...) from a sibling install or cross-org caller against an orphaned-but-not-yet-reattached row.
- **[medium]** The entire web UI was assessed from source only — zero live browser interaction; install->scope->config->deploy and badge->owner navigation never rendered — Directly addresses the user's stated 'low confidence in the web UI alignment/completeness' fear, and that confidence gap is UNRESOLVED — the highest-signal UI checks (nested <Link> hydration error in list rows, the install dialog's config-value validation/gate actually blocking or not, scope-default-Global warning, back-nav ?from) are all source-only. Note: this stack runs in PORT mode (localhost:37791), so per MEMORY the netbird Chrome hang does NOT apply — a browser walkthrough is actually possible here and simply wasn't done.
- **[medium]** MCP tool surface for solution-managed entities was barely touched — only the in-process tables tool backstop; no JSON-RPC handshake, no MCP creation of solution-colliding entities — MCP authenticates as the user directly (per CLAUDE.md, it does NOT follow the engine-sentinel pattern), so it is the surface most likely to bypass solution scoping — yet it was the least-driven. No agent created/queried an entity via MCP that collides by name with a solution-managed row, nor confirmed MCP read-resolution respects the solution exclusion in the name cascade.
- **[high]** Solution standalone_v2 APP reading its own solution table via X-Bifrost-App was never driven — the one path lifecycle suspects DOES resolve solution-first, against its CRITICAL workflow finding — There is an asymmetry the audit left half-open: the app path carries solution_scope (tables.py:633) and likely resolves solution-first, while the workflow path (no app_id) apparently does not (lifecycle CRITICAL). Neither the positive app case nor the contrast was reproduced, so the blast radius of the CRITICAL finding (is it workflows-only, or all callers?) is unknown.
- **[medium]** deploy never bundles functions/ Python (global-repo-access HIGH) means several agents' resolution tests ran against codeless workflows — execution-after-resolution is largely unverified — Because deploy drops functions/ code, the cascade-matrix and parts of scope-leak validated RESOLUTION (which row is picked) but not LOADING+EXECUTION (does the picked row's code actually run from the right import root). The solution->org->global combination is therefore only half-tested end to end for workflows that carry real code.