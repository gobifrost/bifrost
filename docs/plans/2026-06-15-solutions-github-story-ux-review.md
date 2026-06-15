# Solutions "GitHub story" UX review — install / update / publish / DR

**Date:** 2026-06-15 (overnight, autonomous)
**Method:** drive-it-don't-just-test (memory `feedback_drive_dont_just_test`). Driven via the CLI + REST API against the live debug stack (`http://localhost:37791`, port mode). **Browser-UI portions are NOT driven** — Chrome MCP can't get localhost site-permission on this host autonomously; those steps are flagged `[NEEDS-BROWSER]` for a manual pass.
**Bar:** a fully-kitted Microsoft CSP app (multiple shared modules, two integrations, in-depth setup) installs from scratch without the platform being the reason it's hard.

> **Scope honesty (read first).** The fully-autonomous slice is the **CLI/API install → update-signal → export → DR round-trip** arc, driven below with a real solution. The **CSP-from-scratch bar** additionally requires a v1→v2 migration of the CSP inline app (the `bifrost:migrate` arc — already battle-tested separately per `Solutions Migration (v1 to v2)`), and the **install/publish *experience*** (does it spark joy?) is partly a browser-UI judgment. Those are scoped at the end as `[NEEDS-BROWSER]` / `[NEEDS-MIGRATION-DRIVE]` follow-ups, not silently skipped.

---

## Executive summary (what I drove + the verdict)

Drove the full **install → update-signal → export → DR** arc via CLI/API against the live stack, using `rtm-portal` (a real, complete v2 solution workspace) as the vehicle, plus a fresh "DR Target" org for the recovery leg. **The core machinery is sound and the DR story works end-to-end** (export a full backup → install into a brand-new org → all 16 workflows + 1 app + 3 forms + 11 tables + 2 claims restored faithfully). The `--org` standard reads clean across `deploy`/`export`/`install`. Provider cross-org access works at the API layer.

**The one HIGH-value friction is F2:** role-based solutions require every referenced role to **pre-exist by name**, and a missing role fails *mid-deploy* with a 422 (one at a time), invisible to the install preview — the single thing most likely to make a first-time CSP install feel like the platform is fighting you. Roles are global so it's a one-time setup (milder than I first thought), but the preview should surface the whole set up front and `deploy`/`install` should behave the same way.

**Verdict against the bar** ("installs from scratch without the platform being the reason it's hard"): **close, gated on F2.** Fix the role-preview gap (F2 #1+#2) and the deploy-summary undercount (F3) and the CLI install story clears the bar. The *experience* questions (does it spark joy? the publish-your-repo flow; the browser install/update UI) are partly `[NEEDS-BROWSER]` — see Scope at the bottom.

**Ranked fix-list (actionable):**
1. **F2 #2 (cheap, do now):** collect ALL missing roles and fail once with the full list, instead of raising on the first. Removes the deploy-time whack-a-mole.
2. **F2 #1 (real fix):** add roles to the install/deploy **unmet-needs preview** (parity with integrations/configs) so the operator sees the set before committing.
3. **F3 (cheap):** deploy summary should count every upserted kind (tables/forms/apps), not just workflows/claims.
4. **F2 deploy-vs-install parity:** make `solution install` and `solution deploy` resolve roles identically (verify install's behavior on a genuinely-absent role).
5. **F1 (optional polish):** one-line info when a descriptor carries the dead `scope:` key.
6. **F2 #3 (judgment call):** optional `--create-missing-roles` on install/deploy. **Needs Jack's call** (auto-creating identity entities may be more magic than wanted).

None of these are blockers I fixed inline — they're design calls (warn-vs-create, preview parity) that want Jack's input rather than an overnight unilateral change to install/deploy semantics. F4/F-DR/F-PROVIDER are verified-working (no action). F5/F1-as-bug dissolved on reproduction.

---

## Findings (ranked)

> Filled in as the drive proceeds. Each: severity · what · evidence · fix.

### F1 — [LOW / already-handled] Legacy `scope:` key in older descriptors
- **What I suspected:** `rtm-portal/bifrost.solution.yaml` still declares `scope: org` (removed from the descriptor by the `--org` work, `f54a78ab`) — a possible break or silent contradiction on deploy.
- **What's actually true (reproduced in code):** **already handled, deliberately.** `SolutionDescriptor` (`api/bifrost/solution_descriptor.py:42`) sets `model_config = ConfigDict(extra="ignore")` *specifically* so "old descriptors keep loading after scope was removed" (its own comment, lines 33/40-42). The legacy `scope:` key is parsed-and-ignored; install kind comes from `--org`/`--global`. `global_repo_access` is a *separate, still-live* field (correctly read at `solution.py:1302/1314`). So rtm-portal's descriptor deploys fine; no contradiction.
- **Residual (LOW, optional):** a user editing an old descriptor gets no signal that `scope:` is now dead — it just silently does nothing. Optional polish: `solution deploy` could emit a one-line info ("ignoring legacy `scope:` key — install kind comes from `--org`/`--global`") the first time it sees one. Not a bug; a discoverability nicety. **No action needed unless Jack wants the nudge.**
- **Lesson:** reproduce-before-fixing earned its keep again — this looked like a MED migration gap and is actually a clean, intentional design.

### F2 — [HIGH for the "from scratch" bar] Missing roles fail mid-deploy, NOT surfaced by the install preview
- **What (driven live):** deploying rtm-portal (`solution deploy --org Provider`) into a fresh stack fails with `422 unknown role: RTM Portal Admin — create it first in the target env.` A solution that declares role-based access on any entity (`role_names:` in the manifest) requires the operator to **hand-create every referenced role, by exact display name, before the install/deploy will succeed.**
- **CORRECTION (after driving the full arc + checking the role model):** roles are a **GLOBAL identity entity** (`roles.py:171` "roles are global, no org_id needed"; `bifrost roles create` has no `--org` *by design*). So the operator creates each referenced role **once, globally**, and it satisfies EVERY org's installs forever — not per-org. The DR install into a *different* fresh org (below) then "just worked" with no role error, because the global roles I'd created for the first deploy already existed. So F2 is real but **milder than per-org whack-a-mole**: it's a one-time global setup step, and only on `deploy` (see the deploy-vs-install note below).
- **Why it still matters (this is THE bar):** the bar is "installs from scratch WITHOUT the platform being the reason it's hard." Even one-time, the operator hits a mid-`deploy` 422 per *unknown* role with no up-front list — for a first-time CSP install with several roles that's still deploy → 422 → create → deploy → 422 → … until all roles exist. The preview should just tell them the set once.
- **deploy vs install asymmetry (noted):** `solution deploy` hard-fails on a missing role (422, mid-flight). `solution install <zip>` succeeded into a role-less fresh org *only because the roles were already global*; whether install would also 422 on a genuinely-absent role (or handle it more gracefully) is a `[NEEDS-FOLLOWUP]` — the two paths should behave identically.
- **Root cause (reproduced in code):** `_resolve_role_names` (`manifest_import.py:382`) raises `ValueError` on the first unknown name, mid-deploy. The up-front **unmet-needs engine does NOT cover roles** — `check_install_needs` / `SolutionDependencyWalker` (`dependency_walker.py`) surface unmet **modules, tables, configs, integrations**, but there is no role check. So roles are the one declared dependency class that's invisible to the preview and only fails at deploy time.
- **Fix candidates (ranked):**
  1. **Add roles to the unmet-needs preview** — collect every `role_names` across the manifest, diff against existing roles in the target org, and list the missing ones in the install/deploy preview (alongside integrations/configs). Then the operator sees the full list ONCE, before committing. (Best — matches how integrations are already surfaced.)
  2. **Collect-all-then-report at deploy** — instead of raising on the first unknown role, gather all missing names and raise once with the complete list (so it's one round-trip, not N).
  3. **Offer auto-create** — a `--create-missing-roles` flag (or a preview affordance) that creates the declared roles (empty, to be populated) as part of install. Roles are identity entities, cheap to create; the solution already names them.
- **Recommendation:** do #2 immediately (cheap, removes the whack-a-mole) and #1 for the real fix (preview parity). #3 is a judgment call (auto-creating identity entities on install may be more magic than Jack wants). **Needs Jack's call on #3.**
- **STATUS:** verified live (the 422) + root-caused in code (the raise + the preview gap). High-value finding — exactly the kind the drive was meant to catch.

### F3 — [MED] Deploy summary line undercounts (omits tables/forms/apps)
- **What (driven live):** after creating the roles, `solution deploy --org Provider` succeeded with `Deployed install …: 16 workflow(s) upserted, 2 claim(s) upserted, 0 deleted.` But the install actually contains (verified via `/entities`): **16 workflows, 1 app, 3 forms, 11 tables, 2 claims.** So 15 entities (app + forms + tables) deployed but the summary reported none of them.
- **Why it matters (UX):** an operator reads "16 workflows, 2 claims" and reasonably concludes their tables/forms/app did NOT ship — when they did. Erodes trust in the deploy output; may trigger a needless re-deploy or a support question. (This was a known platform-note from the build-skill validation loop; now confirmed in a real install drive.)
- **Fix:** include every upserted entity kind in the summary (`16 workflows, 1 app, 3 forms, 11 tables, 2 claims`). Cheap — the deploy already knows the counts (it returns them via `/entities`).
- **STATUS:** verified live.

### F4 — [verified WORKING] Full-mode export (encrypted secrets + data) — DR/backup path
- **What (driven live):** `solution export rtm-portal --mode full --password …` → `rtm-portal-0.9.0.zip` (96KB). Zip carries `bifrost.solution.yaml`, `functions/`, app source, and the manifest yamls (workflows/tables/forms/claims/apps). **No `secrets.enc` and no table-data blob** — which at first looked like a full-mode bug.
- **Reproduced in code → CORRECT, not a bug:** `build_workspace_zip` (`export.py:200`) writes `.bifrost/secrets.enc` only when `password AND (config_values OR table_data)`. The deployed rtm-portal install had **0 config values and empty (just-deployed) tables** — nothing sensitive to encrypt — so the blob is correctly omitted and the password is a no-op for this install. Shareable mode never includes it. Design is sound (defensive: no blob unless there's something to protect).
- **Residual to fully exercise:** the secrets round-trip itself (set a config value + insert table rows → re-export → confirm `secrets.enc` present → re-install with password → values restored). The MECHANISM is verified (export runs, code path correct); the populated round-trip is a `[NEEDS-DATA-SETUP]` follow-up. The export e2e (`test_solution_zip_install_e2e.py`) already covers the populated case per the memory.
- **STATUS:** export path verified working; empty-blob is correct for an empty install.

### F-DR — [verified WORKING] Full disaster-recovery round-trip (export → install into a fresh org)
- **Driven live, end-to-end:**
  1. `solution deploy --org Provider` → install `81730530…` (16 wf, 1 app, 3 forms, 11 tables, 2 claims).
  2. `solution export rtm-portal --mode full --password …` → `rtm-portal-0.9.0.zip`.
  3. Created a brand-new org "DR Target …" (`b9d77399…`).
  4. `solution install rtm-portal-0.9.0.zip --org <DR-org>` → **`Installed solution 7c2da174…`**.
  5. Verified the DR install via `/entities`: **16 workflows, 1 app, 3 forms, 11 tables, 2 claims** — a complete, faithful round-trip into a different org.
- **Verdict:** the core DR claim ("a user can export an install and recover it into a fresh environment via the CLI") **works**. The `--org` standard is clean on both `export` (ref arg) and `install` (`--org/--organization/--scope/--global`, `--set`, `--password`, `--replace-secrets`, `--replace-data` — well-documented `--help`).
- **STATUS:** verified live. The only friction in the DR path was F2 (roles must pre-exist) — which is one-time-global and was already satisfied here.

### F-PROVIDER — [verified WORKING at API layer] Provider-org cross-org access (folded-in Phase 2c-item-2)
- **Driven live:** as `dev@gobifrost.com` (Provider org `…0002`), against the DR install `7c2da174…` which lives in a *different* org (DR Target `b9d77399…`):
  - `GET /api/solutions` lists the cross-org install ✅
  - `GET /api/solutions/{id}` → 200 ✅
  - `GET /api/solutions/{id}/entities` returns its full entity set incl. the app metadata ✅
- **Verdict:** the provider bypass (`is_platform_admin OR is_provider_org`, per `repositories/README.md`) works at the solution + entities + app-metadata layer for a cross-org install. Confirms the previously-untested 2c-item-2 at the API level.
- **Residual:** the live **app render/mount AS the provider** (serving the v2 app's HTML for a cross-org install) is a browser-path test — v2 apps mount same-document at `/apps/{slug}` (memory `project_solutions_v2_sdk_shape`), not `/api/apps/{id}` (404, wrong path). `[NEEDS-BROWSER]`.

### Side observation — a `covi-csp` solution already exists on the stack
- The install list shows **`covi-csp`** installed in two orgs (`011c9ac2…` and Provider `…0002`). So a CSP-shaped solution has already been built/installed at least once (closer to the review's "fully-kitted CSP" bar than rtm-portal). Worth using `covi-csp` as the CSP-bar vehicle for the `[NEEDS-MIGRATION-DRIVE]`/`[NEEDS-BROWSER]` follow-up rather than migrating the raw `apps/microsoft-csp` inline app from scratch. (Also: the stack has ~30 leftover installs from validation runs — a cleanup candidate, not a finding.)

### F5 — [dissolved / non-issue] `roles create` has no `--org`
- Looked like an `--org`-standard gap (every other write verb takes `--org`). **Reproduced in code → intentional:** roles are a GLOBAL identity entity (`roles.py:156-172`, no `organization_id` on `RoleORM`; CLAUDE.md: identity entities don't follow the org-cascade). So `roles create` correctly omits `--org`. No action. (Reproduce-before-fixing again separated a real gap from an intentional design.)

---

## Scope honestly NOT covered overnight (needs follow-up)

These need either browser access (Chrome MCP can't get localhost permission autonomously on this host) or a multi-hour migration drive. Listed so they're not silently skipped:

- **`[NEEDS-BROWSER]` The install/update *experience* ("does it spark joy?").** The whole point of step 1 is the felt quality of the browser install flow (drag-drop a zip / pick a repo → preview → config → done) and the update-now dialog with its diff. Driven at the API layer (works); the human-feel of the UI is unevaluated. The Phase-3 redesign (SolutionDetail 9→3 tabs) is also `[NEEDS-BROWSER]` for a screenshot.
- **`[NEEDS-BROWSER]` Live app render AS the provider** for a cross-org install (F-PROVIDER residual) — v2 apps mount same-document at `/apps/{slug}`.
- **`[NEEDS-MIGRATION-DRIVE]` The CSP-from-scratch bar.** The literal bar (take `apps/microsoft-csp` inline v1 → migrate to v2 → install with its 2 integrations + shared modules + setup) is the `bifrost:migrate` arc (already battle-tested separately per `Solutions Migration (v1 to v2)`). A **`covi-csp` solution already exists installed on the stack** — use THAT as the CSP-bar vehicle for the experience review rather than re-migrating from scratch.
- **`[NEEDS-DATA-SETUP]` Populated secrets/data round-trip** (F4 residual): set a config value + insert table rows → re-export → confirm `secrets.enc` present → re-install with password → values restored. Mechanism verified; the populated path is covered by `test_solution_zip_install_e2e.py` per memory but wasn't re-driven live.
- **`[NEEDS-REPO]` Publish-your-own-repo-as-a-solution** (step 3) and the **6h update-check poller** (step 2): need a git-connected install pointing at a real repo. The mechanism exists (`update_check.py` + `solution_update_check.py` scheduler; marketplace build per `project_solutions_github_marketplace`) and the UI signal (badge + Update-now) is unit-tested (Phase 3). Not re-driven against a live repo.

## Drive artifacts (for re-runs)
- Scratch CLI (matches server): `/tmp/bifrost-formschema-check/.venv/bin/bifrost` (logged in dev@gobifrost).
- Deployed install (Provider): `81730530-765e-4858-bacc-73052144b8e2`.
- DR target org: `b9d77399-de25-4906-9bb3-ae16a2c770c3`; DR install: `7c2da174-281e-4746-bf60-c51a482321b0`.
- Export zip: `/tmp/rtm-drive-export/rtm-portal-0.9.0.zip`.
- Global roles created (one-time): `RTM Portal Admin`, `RTM Portal Contributor`.
