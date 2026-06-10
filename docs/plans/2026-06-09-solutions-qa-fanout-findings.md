# Solutions Adversarial QA Fan-Out — Findings Backlog

Date: 2026-06-09
Branch: worktree-solutions-success-criteria
Scope: adversarial QA across six axes (scope-isolation, lifecycle, readonly-enforcement, global-repo-data-fallback, ui-ux, cli-docs-literalism). Every CONFIRMED finding below was independently reproduced by a second verifier on a fresh port-mode stack.

> ## ⚠️ SUPERSEDED (2026-06-09, same day): "0 high open" did NOT hold for the branch
>
> A full-branch static review (7 finder angles over the ~31k-line diff, every candidate
> independently verified) found **7 high-severity bugs this fan-out missed**, plus ~10
> medium/low. The fan-out drove the feature from the front door; the misses lived in
> side doors it never constructed: name/slug COEXISTENCE states (same-name `_repo/`
> table → uninstall stuck; multi-install slugs → `embed.py`/`websocket.py`/MCP crashes
> on endpoints the branch never touched), cross-org admin execution (forms resolved in
> the CALLER's org), git-connected sync (config schemas wiped every pull), Windows
> (`npm.cmd`), and npm-dev SDK mode (transport raced child effects; ws subscribe never
> authenticated). Lesson for the next fan-out: axes must include *pre-existing-state
> collisions* and *unmodified endpoints whose invariants the branch relaxed*.
>
> **All findings fixed on this branch** — plan + finding↔commit map:
> `docs/superpowers/plans/2026-06-09-solutions-review-fixes.md` (commits `a530f41b`..,
> 19 fix tasks + the versioning/upgrade scope change, each spec- and quality-reviewed).
> The M1/low backlog below remains valid where not covered by those fixes.

## STATUS (CONFIRMED only)

- critical: 0
- high: 0
- medium: 1
- low: 6
- TOTAL CONFIRMED (bugs to fix): 7
- DEPRECATION (not a bug): 1 — export --portable / import (superseded by Solutions; see below)

REFUTED: 4 (do not re-investigate) — includes the original H1 (config/secret fallback), RETRACTED on review as intended org→global cascade behavior, not a bug. BLOCKED axes: 0 (all six axes booted and were driven).

**Post-run triage outcome:** of the run's 2 initial HIGHs, one (config/secret fallback) was RETRACTED as intended behavior and the other (export --portable scrub) was reclassified as a DEPRECATION of a pre-Solutions command — so **zero HIGH bugs remain**. The actionable backlog is 1 medium + 6 low + one deprecation chore.

> **Post-run triage correction (2026-06-09):** the original **H1** ("sealed cross-org install reads decrypted global secrets") was downgraded from CONFIRMED/HIGH to REFUTED. The reported behavior is the **intended org→global config cascade**, not a cross-tenant leak. `merged_for_sdk()` (`api/src/repositories/config.py:148-186`) unions exactly two scopes — `organization_id IS NULL` (global) and `organization_id == caller's own org` — and has **no branch that reads any other org's configs**. A "global" (NULL-org) config is a deliberately operator-published value, not "another org's secret"; reading it from any org's cascade is the same resolver every config read uses. The consumer is the server-side **workflow** (engine sentinel), not a user, within the trust boundary of "whoever can deploy a workflow into this org." `global_repo_access` is a `_repo/` **code-import** seal, not a secret ACL — the code/data asymmetry is documented and deliberate. Org-*scoped* secrets remain non-reachable cross-org (scope-isolation axis confirmed: OrgB forcing OrgA scope → 403). See REFUTED #4.

---

## CONFIRMED

> No CONFIRMED HIGH findings remain. The original H1 (config/secret fallback) was RETRACTED as intended behavior (REFUTED #4); the renumbered H1 (export --portable scrub) was reclassified as a DEPRECATION (below), not a bug to fix.

### DEPRECATION (was H1 / H2) — `bifrost export --portable` / `bifrost import` is legacy; do not fix its scrub, deprecate the commands
- **surface:** export-import
- **finding (factually true, but NOT a bug to fix):** `bifrost export --portable <dir>` does not scrub org IDs/names — portable and non-portable `.bifrost/organizations.yaml` are byte-identical (same md5), and `bundle.meta.yaml` records `portable: true` but `scrubbed: []` + `source_env: localhost:<port>`. So a "shareable" bundle embeds the source-env org roster + host.
- **disposition (decided 2026-06-09 with Jack — RESOLVED: REMOVED):** `export --portable` / `import` predates Solutions (Apr 18; Solutions landed Jun 4) and was the generic pre-Solutions cross-environment workspace-sharing path. **Solutions supersedes it** for packaged distribution: `bifrost solution deploy` / `solution install <zip>` is the install-scoped, lifecycle-aware, provenance-tracking replacement (`export.py` didn't even know solutions exist — that's why REFUTED #2's solution round-trip was a silent no-op). The only thing export/import still nominally covered is bare `_repo/` workspace migration across environments, which git sync + Solutions now handle. **Decision: rather than fix the scrub bug, the `bifrost export` / `bifrost import` commands were REMOVED outright** (BREAKING CHANGE) — deleting `api/bifrost/commands/export.py`, `import_cmd.py`, `portable.py`, and their tests; dispatcher/help/docs updated; guidance in CLAUDE.md / AGENTS.md / llm.txt now points at Solutions. The broken scrub is gone with the command.
- **status:** ✅ DONE in this branch.

### MEDIUM

#### M1 — Scaffolded app README documents a `npm run dev` local-dev path that diverges from `solution start` and breaks the sample's no-deploy promise
- **surface:** cli (scaffold template / docs)
- **did:** After `bifrost solution scaffold-app dashboard`, read `apps/dashboard/README.md` + `vite.config.ts`. README leads with `npm run dev` (`http://localhost:5173 — already authenticated`); scaffold CLI output + `docs/llm.txt` instead document `bifrost solution start` (single origin, in-process `@workflow` functions, no deploy). Replayed the sample button's exact `/api/workflows/execute` payload against a fresh pre-deploy workspace.
- **observed:** Under `npm run dev` there is NO single-origin proxy and NO in-process FunctionHost; workflow calls hit the dev API directly. On a fresh workspace the sample button (`useWorkflow('functions/hello.py::main')`) has no deployed target → `HTTP 404 Workflow 'functions/hello.py::main' not found`. README never mentions `solution start`, so a docs-literal first-run user gets a non-working sample button.
- **expected:** Scaffolded README should lead with `bifrost solution start` for first-run local dev (sample runs in-process, no deploy); present standalone `npm run dev` as the post-deploy / app-only path with that caveat.
- **code_ref:** `apps/dashboard/README.md` (scaffold template) + `.env.example` vs `api/bifrost/commands/solution.py` `start_cmd` + `solution_dev/proxy.py`.
- **proposed fix shape:** Rewrite the scaffolded README/`.env.example` "Local dev" section to lead with `bifrost solution start`, and demote `npm run dev` to a labeled post-deploy/app-only note.

### LOW

#### L1 — Cross-org existence/managed-status oracle: solution-managed app guard runs before authorization
- **surface:** apps-ui
- **did:** As an OrgB non-superuser, `PATCH /api/applications/<id>` against (a) an OrgA solution-managed app, (b) an OrgA plain app, (c) a random UUID.
- **observed:** (a) → 409 `Solution-managed entities can only be managed by deployment methods.`; (b) → 404; (c) → 404. A cross-org low-privilege user distinguishes "solution-managed app in another org" (409) from "plain/nonexistent" (404), disclosing existence + managed status across org boundary. Same pre-authz ordering on delete/publish/repoint/rollback/logo.
- **expected:** Cross-org user gets 404 for any inaccessible app regardless of managed status; the org-access (404) gate runs before the solution-managed (409) guard.
- **code_ref:** `api/src/routers/applications.py:333` (`assert_entity_id_not_solution_managed` before `get_application_by_id_or_404` at :361); guard does an org-unscoped raw lookup in `api/src/services/solutions/guard.py:89-106`. Same ordering at applications.py:388,455,498,553,821,861,925.
- **proposed fix shape:** In `applications.py` mutation routes, resolve+authorize via the org-scoped repo (404 on miss) BEFORE calling the solution-managed guard; or have the guard accept the org-scoped entity already fetched.

#### L2 — Orphaned-table provenance UUID (`origin_solution_id`) is written to the DB but dropped from the table read DTO
- **surface:** forms-ui (tables contract)
- **did:** After uninstall, `GET /api/tables/<id>` vs the Postgres row.
- **observed:** DB row has `origin_solution_id=<uuid>`; API returns the field entirely absent (`'origin_solution_id' in d == False`). `origin_solution_slug` IS surfaced (the documented reattach key), so reattach + the "from <slug>" badge are unaffected. Pure contract/docstring mismatch — `delete_solution` docstring claims the orphan carries `origin_solution_id`.
- **expected:** Either surface `origin_solution_id` in the DTO to match the delete-handler docstring, or document it as DB-internal only.
- **code_ref:** `api/src/models/contracts/tables.py:85-102` (`TablePublic` exposes `orphaned_at` + `origin_solution_slug`, no `origin_solution_id`); `api/src/routers/solutions.py:335` (sets it), `:261` (docstring claim).
- **proposed fix shape:** Add `origin_solution_id: UUID | None` to `TablePublic` and the serializer, OR amend the docstring to say slug-only is surfaced.

#### L3 — Hand-authored `.bifrost/configs.yaml` with non-UUID config ids fails install with a cryptic "badly formed hexadecimal UUID string"
- **surface:** export-import
- **did:** Hand-authored a workspace with a config keyed by key string and no explicit `id`, zipped, ran `bifrost solution install`.
- **observed:** `422 Invalid solution zip: badly formed hexadecimal UUID string`. `_collect_config_schemas` defaults `id` to the map key (e.g. `GREETING`); the deployer does `UUID(c['id'])` which throws. Scaffold/export always write UUID ids, so this only bites hand-authored/external workspaces. Adding explicit UUID ids fixed it.
- **expected:** Generate/normalize a UUID for config-schema entries lacking one, or return a clear "config schema id must be a UUID" validation message.
- **code_ref:** `api/bifrost/commands/solution.py:510-535` (`"id": body.get("id", key)` at :526) + `api/src/services/solutions/deploy.py:1150` (`UUID(c['id'])`).
- **proposed fix shape:** In `_collect_config_schemas`, when no `id` is present generate a deterministic UUID (e.g. uuid5 from key) instead of defaulting to the key string; or validate-and-message early at zip ingest.

#### L4 — Deploy summary line only reports workflow count, hiding forms/agents/tables that were upserted
- **surface:** cli
- **did:** `bifrost solution deploy` on a workspace declaring 1 form, 1 agent, 1 table, 1 workflow.
- **observed:** Output: `... 1 workflow(s) upserted, 0 deleted.` The endpoint returns full counts (`SolutionDeployResponse`: workflows/tables/forms/agents `_upserted`), all four entities created, but the CLI prints only the workflow count.
- **expected:** Summary reflects all entity types upserted (forms, agents, tables, workflows, apps).
- **code_ref:** `api/bifrost/commands/solution.py:782-786` (formats only `workflows_upserted`/`workflows_deleted`); contract `api/src/models/contracts/solutions.py:172-183`.
- **proposed fix shape:** Build the summary from every `*_upserted`/`*_deleted` field present in the response, skipping zero-count types.

#### L5 — `bifrost solution deploy` does not reconcile `global_repo_access` on an existing install (set only at create)
- **surface:** cli
- **did:** Flipped `global_repo_access` in `bifrost.solution.yaml` and re-ran `bifrost solution deploy` against an existing install.
- **observed:** Descriptor's `global_repo_access` is sent only on the CREATE branch (`POST /api/solutions`); redeploy of an existing install does NOT PATCH the flag, with no warning. Had to `PATCH /api/solutions/{id}` explicitly. `SolutionDeployRequest` has no `global_repo_access` field; `deploy_solution` never reads/writes it.
- **expected:** Deploy reconciles the descriptor's `global_repo_access`, or warns it is create-only.
- **code_ref:** `api/bifrost/commands/solution.py:736` (create branch only; redeploy at :769 has no flag); `api/src/models/contracts/solutions.py:128-151`; `api/src/routers/solutions.py:451`.
- **proposed fix shape:** Either add `global_repo_access` to `SolutionDeployRequest` + reconcile in `deploy_solution`, or emit a CLI warning on redeploy when the descriptor's flag differs from the live install.

#### L6 — Solutions list cards omit entity-summary chips (counts) shown in the install dialog and detail page
- **surface:** solutions-page
- **did:** Installed a solution, opened `/solutions`, compared list cards vs install dialog + detail page.
- **observed:** List cards show only name, slug, Provider/Manual badges, trash icon — no count chips. `GET /api/solutions` list payload carries no entity arrays/counts, so the card has no data to render. Chips render correctly in the dialog (`EntitySummary`) and on the detail page.
- **expected:** If "entity-summary chips render correctly" is meant to cover the list page, each card surfaces at-a-glance counts. Minor UX gap, not a defect — chips work where present.
- **code_ref:** `client/src/pages/Solutions.tsx` (list-card render at ~lines 384-436; `EntitySummary` used only in install dialog ~line 488).
- **proposed fix shape:** Add count fields to the `/api/solutions` list DTO and render an `EntitySummary` chip row on each list card (or explicitly scope the success criterion to dialog+detail).

---

## REFUTED (do not re-investigate)

1. **DATA fallback (tables) is UNGATED by `global_repo_access` but own-first prevents wrong-data.** — The headline behavior reproduced (a sealed install with no own table reads a GLOBAL `_repo/` table by name), but this is **by-design shared-global-tier semantics**: own-first (`_resolve_solution_table_by_name`, `tables.py:625-673`) deterministically shadows the `_repo/` row when the install owns the name, so there is no wrong-data outcome. No bug to confirm. (The config/secret path has no own-first either, but it is also not a bug — it's the standard org→global cascade; see RETRACTED #4.)

2. **Documented `export --portable` → `import --org` flow does NOT round-trip a Solution (silent no-op).** — BLOCKED, not reproduced. Verifier reproduced preconditions but `bifrost deploy` never completed due to a parallel-QA stack teardown collision on the shared debug project name / `/tmp`; never reached the export→import steps. Zero empirical observation; defaulted to confirmed=false. (Note: H1 — the renumbered export-scrub finding — confirms a *separate, real* portable-scrub defect; this round-trip claim is the unverified one.)

3. **`bifrost solution start` dumps a raw Python traceback on port-in-use.** — BLOCKED, not reproduced. Verifier could not boot a healthy port-mode stack (daemon contention from parallel QA stacks). Static read of `solution.py:961` (`await site.start()` with no surrounding `OSError` handler; `handle_solution()` :976-994 catches only Click exceptions) is *consistent* with the claim, but this is code-reading only — left unconfirmed.

4. **(RETRACTED, originally H1) "Sealed cross-org solution install reads any GLOBAL `_repo/` config including decrypted secrets, regardless of `global_repo_access`."** — The behavior reproduces, but it is **intended, not a bug**. `merged_for_sdk()` (`api/src/repositories/config.py:148-186`) unions exactly the global tier (`organization_id IS NULL`) and the caller's OWN org (`organization_id == self.org_id`); there is **no branch reading any other org's configs**. `_resolve_sdk_org_id` (`cli.py:313`) resolves a non-bypass caller only to their own org (other-org scope → 403), so this is the standard org→global cascade every config read uses — not cross-tenant. A NULL-org config is a deliberately *global, operator-published* value, not "another org's secret." The consumer is the server-side **workflow** (engine sentinel `is_superuser=True`), not a user; a workflow author who can deploy into an org is already inside that org's runtime trust boundary, and the platform intentionally publishes global configs/secrets to all orgs' runtimes. `global_repo_access` is a `_repo/` **code-import** seal (module loader), NOT a secret/data ACL — the code-vs-data asymmetry is documented and deliberate (the data tier is ungated by design). Org-*scoped* secrets remain non-reachable cross-org (scope-isolation axis: OrgB forcing OrgA scope → 403). **No follow-up; the deferred "data-tier gate" is NOT needed for configs.** (If the surprising code/data asymmetry warrants anything, it's a one-line docs clarification, not a security fix.)

---

## DATA-FALLBACK VERDICT

Did the ungated `_repo/` data fallback actually bite? **NO — neither tables nor configs/secrets are a defect.** (This reverses the run's initial verdict after triage — see REFUTED #4.)

- **Tables:** ungated but SAFE. Own-first shadowing means an install-owned table name always wins; the `_repo/` table is reachable by name only when the install does not own that name (the same shared-global-tier semantics used platform-wide). No wrong-data outcome.
- **Configs/secrets:** ungated and **also intended**. The SDK config path returns the standard org→global cascade (own org + global NULL-org tier), never another org's data. A "global" config/secret is an operator-published, deliberately-global value; the server-side workflow (not a user) consuming it is within the org's runtime trust boundary. `global_repo_access` gates `_repo/` **code imports**, not data — a documented, deliberate asymmetry.

**Decision:** The deferred "extend `global_repo_access` gating to the data tier" follow-up is **NOT needed**. The only optional action is a one-line docs note clarifying that `global_repo_access` seals code imports, not global config/secret reads, so the asymmetry isn't surprising. No security fix.

---

## COVERAGE / GAPS

**scope-isolation** — Drove all 5 probes with a real OrgB non-superuser + positive controls. Core isolation CLEAN: OrgB cannot resolve/execute OrgA's workflow (by name/UUID/forged app_id/forged `X-Bifrost-App` header — all 404), cannot read OrgA's table (name/UUID 404; forged header read+write 404), cannot read OrgA config (`/api/sdk/config/get` forcing OrgA scope → 403), S3 app draft/export/render isolated (404). One real defect: the cross-org app existence oracle (L1). **Not reached:** events/agents/forms update endpoints for the same guard-vs-authz ordering (forms/agents likely share the `applications.py` CurrentUser pattern — warrants a follow-up ordering check).

**lifecycle** — Covered all 5 criteria: install round-trip (CLI + UI drag-drop), seed→uninstall→orphan (orphaned_at/solution_id NULL/slug stamped, "Show orphaned" toggle + badge)→reinstall reattach (rows survive), redeploy with schema change (rows preserved), same-slug Provider+Global independence, config values instance-owned + runtime resolution. Two low findings (L2, L3). **Not reached:** git-connected install lifecycle (deploy/uninstall refusal — code-read only), app (standalone_v2) install/redeploy + app-dist S3 sweep on uninstall, concurrent-deploy write-lock 409 contention, forms/agents in the bundle.

**readonly-enforcement** — Tested CLI + direct REST PATCH/PUT/DELETE/promote/deactivate/replace/recreate on managed form/agent/table/workflow (all 409); `/remap` at managed bindings (200 but updated:0, bindings intact); role bulk-assign + role DELETE backstop (409, bindings preserved); colliding-UUID create (new UUID minted); `before_flush` ORM backstop fired in-container; runtime carve-outs allowed (doc insert 201, configs set OK). Zero DB corruption. **Not reached:** (1) UI builder read-only banner/disabled-save NOT driven visually (Chrome extension disconnected — verified via API flags + client source only); (2) MCP tools NOT exercised over the wire (FastMCP endpoint 404 in dev stack, `tools_count:0`) — enforcement confirmed by static read + in-container `before_flush` repro; (3) App managed-entity mutations not exercised empirically (no app in fixture); (4) MCP `update_table` has no explicit solution guard, relies solely on the global backstop — worth an explicit guard for defense-in-depth.

**global-repo-data-fallback** — Drove all 3 criteria on two installs (Provider + CustOrg). CODE fallback VERIFIED GATED (sealed→ModuleNotFoundError; flip flag→resolves; flip back→re-seals). TABLE data fallback VERIFIED ungated-but-safe (own-first). CONFIG/SECRET fallback initially flagged as a cross-org leak but RETRACTED on triage — it is the intended org→global cascade (own org + global tier only, never another org's data), not a defect (see RETRACTED #4). Cross-org isolation of *org-specific* values is correct. **Not reached:** UI drive of the management page (runtime output was stronger evidence), export/import round-trip of `global_repo_access` (L5 covers redeploy), non-superuser-vs-superuser distinction on `/api/sdk/config/get` (SDK reader runs as engine sentinel `is_superuser=True`, so the cross-org global read is expected for any install).

**ui-ux** — Drove all 5 criteria with Playwright + screenshots. PASSED: solution-managed form rendered+submitted running its OWN workflow (own-scope config+table resolution); standalone_v2 app mounted at `/apps/<slug>` with working in-app `useWorkflow`; **BifrostHeader standalone via `solution start` rendered fully STYLED (D3 regression FIXED)**; install dialog (drag-drop, entity-count chips, scope selector, masked secret field, clear slug-conflict 409); uninstall type-to-confirm + non-destructive orphan toast + "Show orphaned" Config-page badge (value masked `[SECRET]`). One low finding (L6). **Not reached:** Tables-page "Show orphaned" (only Config-page tested), role_based form access, multi-org install scoping in UI (single Provider org in dev stack).

**cli-docs-literalism** — Booted healthy PORT-mode stack, API-matched CLI, password-grant — all per CLAUDE.md, verbatim. VERIFIED: `solution init`→`scaffold-app`→`deploy` first-run (workflow not dropped, runs); `bifrost watch` refuses in a solution workspace (exit 1, points at `solution start`); `solution start` local dev (one origin, in-process workflow, /api proxy, clean SIGINT teardown); `solution install <zip> --org` round-trip; stale-pattern checks (`sync --push`, `clone`, `import --roles`, bogus command — all correct non-zero exits, helpful messages). Two findings (M1, L5). **Not reached:** real browser UI button clicks (extension not connected — curl + code inspection substituted), drag-drop UI install path (only CLI), `--set KEY=VALUE` on install (covered under lifecycle).

---

## BLOCKED

None. All six axes booted a stack and were driven. (Two REFUTED items above were individually blocked by parallel-QA stack-teardown collisions on the shared debug project name / `/tmp` — an infra contention artifact of running many QA stacks at once on the same branch HEAD, not a product issue. Re-run those two in isolation if re-investigation is desired.)
