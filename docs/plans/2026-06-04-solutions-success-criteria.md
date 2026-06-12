# Solutions — Success Criteria & Intent

Status: intent locked, feeds spec + goal-driven implementation
Date: 2026-06-04
Supersedes terminology in: `2026-06-03-capability-source-model.md` ("capabilities" → "Solutions")
Grounded by: `2026-06-03-capability-source-model-VIABILITY-REVIEW.md`

> **Purpose of this document.** This is the end-to-end *success story* — the intent, the locked
> decisions, and the falsifiable "done" criteria. It is NOT the spec. The spec will be written
> next and reconciled against this. The goal-driven implementation chases the success criteria here.

---

## 1. The Plot (one paragraph)

Bifrost keeps its single global ad-hoc workspace (`_repo/`) exactly as it is today — full git
diff/commit/status drift workflow, fast in-platform editing, shared library, broad agent context.
**On top of that** we add **Solutions**: scoped, deployable units of Bifrost functionality. **A
Solution is an installable surface, not a git repo.** It is the deployable unit installed onto a
platform instance. It *may be sourced from* a git repo (which then becomes the writer), or deployed
manually, or developed locally — but the repo is one possible *source*, not the Solution's identity.
Solution-managed entities are **read-only on the platform**: there is **exactly one writer per
install**, which eliminates drift by construction instead of by merge. Solutions are self-contained
worlds (their own import root), install per scope (one org, or global), and can be installed multiple
times. **Solutions are invisible to users** — users only ever see the entities a Solution deploys,
and from the user's perspective nothing changes. React apps inside a Solution are first-class and
must feel like normal React.

---

## 2. The Two Tiers (the core mental model)

| | `_repo/` (ad-hoc workspace) | Solutions (installable surface) |
|---|---|---|
| What it is | An editable workspace | A deployable unit installed onto an instance |
| Source of truth | The workspace itself, git-synced w/ drift workflow | The install on the platform; its *source* may be a connected git repo |
| Platform editing | Editable; drift tracked via diff/commit | **Read-only** for solution-managed entities |
| Writer | In-platform edits + git commit | **Exactly one per install:** `bifrost deploy` **OR** git-connected auto-pull |
| Scope | Global / shared library | One org **or** global (see §3.3) |
| Runtime source (Python) | `_repo/` | `_solutions/{solution_id}/` |
| Built app artifact (React) | `_apps/` | `_apps/` (same prefix; dist only) |
| Import root | `_repo/` | `solutions/{solution_id}/`, fallback to `_repo/` only if "global repo access" on |
| Purpose | Shared code, one-offs, fast iteration | Deployable client/product units |
| Status | **Unchanged. Preserved as-is.** | New |

Over time `_repo/` can become "the shared library" and Solutions become "the deployable units" —
but that evolution is optional and not forced.

---

## 3. Locked Decisions

### 3.1 Terminology & nature
- The unit is a **Solution**: an **installable surface**, not a repo.
- An installed Solution is an **install**, identified by `solution_id` (a UUID per install).
- A Solution **may be connected to** a single git repo as its source (§3.9). The repo is a *source*,
  not the Solution itself.

### 3.2 Source-of-truth contract (the drift solution)
- Entities (**workflows, apps, forms, agents, tables + table policies**) gain a nullable `solution_id`.
- If `solution_id IS NOT NULL`, the entity is **solution-managed**:
  - Every mutation path **outside deployment** returns an HTTP error:
    *"Solution-managed entities can only be managed by deployment methods."*
  - The platform UI renders these entities **read-only**.
- **Exactly one writer per install.** No per-save git ops. No merge. No drift, by construction.
- **Instance still owns (editable even on solution-managed entities):** only what cannot be
  portable — **OAuth token mappings** and **secret config values**. Everything else that *can* be
  portable *is* portable and is locked.

### 3.3 Scope model (two distinct meanings of "global")
A Solution installs at a **scope**, using Bifrost's existing scoping system:
- **Org scope:** entities are visible to that one org; everything under the install inherits the org.
  No per-entity `organization_id` rewrite — scope is a single property of the install.
- **Global scope:** entities are **globally available to people on the Bifrost tenant** (same as any
  global-scoped entity today). This is purely about **who can see/use the deployed entities**.

**"Global scope" is NOT "global repo access".** They are orthogonal:
- *Global scope* (above) = visibility of deployed entities.
- *Global repo access* (§3.5) = whether the Solution's **code** may import shared modules from `_repo/`.

A Solution can be global-scoped without global repo access (self-contained code, visible to all), or
org-scoped with global repo access (private entities, code reaches `_repo/` shared library), or any
combination. **Genuinely-shared importable code still lives in `_repo/`, not in a Solution.**

### 3.4 Multiple installs
- One Solution definition can be **installed multiple times** (e.g. ship `halo-ticketing` once,
  install it for N client orgs). Each install is independent, keyed by `solution_id`.
- Install identity is unique per **(solution, scope)** — no scope overlap between installs.
- Deploy resolves *which install* it targets by solution-identity + scope; `--solution {uuid}` is the
  explicit override when ambiguous. (On upload, a matching solution name MAY update an existing install.)

### 3.5 Runtime / imports (resolves the flat-namespace problem)
- Execution always starts by running a workflow. If the workflow has a `solution_id`:
  - Import root is `solutions/{solution_id}/` — `from modules.x import y` resolves to
    `_solutions/{solution_id}/modules/x.py` (imports work from the solution root, exactly like
    running locally from the directory root).
  - Falls back to `_repo/` **only if "Enable global repository access" is on** (off by default).
- Solutions are **self-contained worlds**: `solutions/A/modules/x` and `solutions/B/modules/x` are
  different files at different roots and never collide. Per-execution scoping is what makes
  multi-version solution-local code safe (no global `sys.modules` shadowing).

### 3.6 Storage, artifacts & reconcile
- **Python (workflows, modules) installs as source** to `_solutions/{solution_id}/` — it is *executed*
  as source by the virtual importer, not built.
- **React app `src/` is build-input only and is NOT installed.** On deploy, the build runs
  (`npm run build` or equivalent) and **only the `dist/` output is copied to the `_apps/` prefix**
  (the existing app-artifact location). `_solutions/` does not contain app source — no more than it
  contains the YAML manifest files. (See §6.3 for *where* the build runs.)
- **Deploy = full replace, scoped strictly to `solution_id`:** upsert everything in the bundle;
  delete entities previously under this `solution_id` that are absent from the new bundle.
  - **Never touches `_repo/` or any other install.** `_repo/` is out of scope for all Solution ops.
  - Requires the viability study's deletion-sweep gating fix, **re-scoped to `solution_id`** (delete
    when "absent from THIS solution's bundle", not the current destructive global path-existence check).

### 3.7 Tables
- Solution owns table **schema + policies** (RLS-like — an app defines its own data-access rules).
- **Row data is runtime state; deploy never writes or wipes it.** A redeploy with a changed schema
  migrates structure (add/alter) and **preserves rows**. Mirrors the existing app-source vs app-data split.

### 3.8 Descriptor & manifest structure
- A Solution workspace is marked by a root descriptor file: **`bifrost.solution.yaml`**.
  - Holds Solution-level **identity + config**: id, slug, name, scope (org/global), global-repo-access
    flag, declared shared deps.
  - This is how tooling (`bifrost run`, deploy, export) detects it is operating against a Solution
    (vs the ad-hoc `_repo/` workspace) and knows to target `_solutions/{id}/` and stamp `solution_id`.
- **Per-entity content stays in the existing split `.bifrost/*.yaml` manifests** (`workflows.yaml`,
  `apps.yaml`, `forms.yaml`, etc.). The descriptor **indexes** them. This reuses the generator +
  importer that already round-trip — a Solution is "the import/export system promoted to a first-class,
  enforced, git-first lifecycle." A Solution = `bifrost.solution.yaml` + `.bifrost/*.yaml` + Python
  source + app `src/` (build input).

### 3.9 Source modes (keeps "one writer" true)
- **Disconnected install:** `bifrost deploy` is the only writer.
- **Git-connected install:** the Solution is connected to a single git repo. The platform
  polls/webhooks `main` and **auto-deploys** on new commits (pulling changes in as if deployed).
  **`bifrost deploy` is disabled** for this install.
- Monorepo (multiple Solutions in one repo) is **out of scope** until a real need appears (YAGNI);
  the contract is one repo per Solution.
- Either way: **exactly one writer**, read-only in the UI. Connected mode reuses the existing git
  pull machinery (`github_sync.py`) and disables the commit/deploy half.

### 3.10 Dev loop (no `watch`)
- **`watch` does not belong to the Solution paradigm.** Watch exists to accelerate the *drift* loop
  (edit → push to `_repo/` → see live → reconcile via diff/commit). Solutions are deploy-driven,
  one-writer, read-only — there is no drift loop to accelerate. Watch stays a `_repo/`-tier concept.
- **The Solution dev loop:**
  - *Disconnected:* iterate locally → `bifrost deploy` to a **dev install** (fast).
  - *Connected:* iterate locally → merge to `main` → **auto-pull** deploys to the connected install.
- **Local / offline = local execution, live data-plane:** Python workflows execute locally
  (`bifrost run` against local Solution files) and the React app runs locally (`npm run dev`, the SDK
  routes workflow calls to local). **Data-plane SDKs — tables, integrations, OAuth — still call a
  real dev instance.** You write and run code fully offline; data and external calls hit a live
  backend. (Avoids reimplementing the platform locally; full-offline is out of scope — §7.)

### 3.11 React apps (first-class success criterion)
- A Solution's React app must feel like a **normal React project**: standard `src/`, `npm run dev`
  local loop, the SDK as ordinary imports, and a build that produces a `dist/` (deployed to `_apps/`).
  Not the current synthesized-bundle experience. (The viability study found this is orthogonal to the
  scope/source model and structurally blocked today by inline-render context inheritance; making it
  first-class is in scope here — but may warrant its own sub-spec, see §6.3.)

---

## 4. Success Criteria (falsifiable "done")

> **Autonomy bar (overarching).** This work will be executed by a goal-driven agent running
> **autonomously, end-to-end**. "Done" means working, fully tested, and verified against a live
> stack — not "code written." The implementing agent is **empowered to make whatever changes are
> needed to operate autonomously**, including fixing tooling that assumes an interactive session
> (e.g. making the CLI runnable **non-interactively / without a TUI**, adding `--yes`/`--json`/
> non-tty flags, scriptable auth). It is expected to **iterate until every criterion below passes**,
> however tedious, re-running the full verification sequence rather than declaring success early.
> Non-interactive operability of any tool it depends on is itself a success criterion (#17).

The end-to-end proof uses the **real `bifrost-workspace`** (`gocovi/bifrost-workspace`) as source
material — take a real slice (e.g. `clients/mna` or `braytel`), turn it into a Solution, and confirm:

1. **No regression:** existing ad-hoc `_repo/` functionality works **untouched** alongside Solutions.
2. **Side-by-side deploy:** the Solution deploys and runs concurrently with `_repo/` functionality.
3. **Solution-local imports:** a workflow in the Solution imports its own `modules/*` from the
   solution root and runs.
4. **Global-repo-access fallback:** with the flag ON, the Solution imports a `shared.*` module from
   `_repo/`; with it OFF, that import does **not** resolve (no silent fallback).
5. **Vendored shared deps:** export-with-shared-scan produces a self-contained Solution that installs
   on a *fresh* instance (no `_repo/` shared deps present) and its imports resolve to vendored copies.
6. **Read-only enforcement:** solution-managed entities are read-only in the UI **and** every
   non-deploy mutation API returns the "Solution-managed…" error.
7. **Editable carve-out:** OAuth token mappings and secret config values remain editable on a
   solution-managed entity's install.
8. **Scope inheritance:** all entities in an install carry the install's scope (org or global); no
   per-entity scope binding step.
9. **Multiple installs:** the same Solution installs for two different scopes as two independent
   installs with no scope overlap.
10. **Full-replace reconcile:** redeploying a Solution with an entity removed deletes that entity for
    **this install only**, and never affects `_repo/` or other installs.
11. **Table data preserved:** redeploying a Solution with a changed table schema migrates structure and
    preserves existing rows.
12. **App artifact:** a Solution's React app builds to `dist/` and is served from `_apps/`; the
    Solution surface contains **no** app `src/`. The app runs like a normal React app, with an
    `npm run dev` local loop.
13. **Git-connected one-writer:** a connected install auto-deploys on a push to `main`, and
    `bifrost deploy` is **refused** for that install (one-writer invariant holds).
14. **Solution context detection:** `bifrost run`/deploy correctly detect a Solution workspace via
    `bifrost.solution.yaml` and target `_solutions/{id}/`, vs the ad-hoc `_repo/` workspace.
15. **Offline dev:** `bifrost run` executes local Solution workflows offline while tables/integrations
    resolve against a live dev instance.
16. **Invisible to users:** end users see only the deployed entities (unchanged from their view); the
    Solution itself is not user-visible.
17. **Non-interactive operability:** every CLI/tooling path the deploy/dev/test loop depends on runs
    **headless** — no TUI, no interactive prompt — so the whole flow (create → deploy → run → verify)
    can execute unattended in a script or CI. Where a tool only had an interactive mode, a
    non-interactive path is added.
18. **Verified on a live stack, all tests green:** the full pre-completion verification (pyright,
    ruff, tsc, lint, `./test.sh all`, client unit, relevant client e2e) passes, and criteria 1–17 are
    demonstrated against a running debug stack — not asserted from code inspection.

---

## 5. Prerequisite Fixes (from the viability study — required before/with this work)

- **Deletion-sweep gating fix**, re-scoped to `solution_id` (without it, deploy/reconcile is silently
  destructive). Highest leverage.
- **MCP `service_oauth_token_id`** added to the portable scrub + test (it currently leaks a live
  service-token FK in "portable" exports — relevant because Export Solution rides the same boundary).
- **Scope-aware manifest generation** (`generate_manifest` currently dumps all orgs — a per-scope
  Solution export must not cross-contaminate tenants).

---

## 6. Open Questions (resolve in spec, not story)

1. **Connected-mode dev loop:** intended path is to iterate against a *disconnected dev install* and
   ship to the *connected prod install* via merge-to-main → auto-pull. Confirm this is the only fast
   path, or whether a guarded preview-deploy is wanted (leaning no — re-opens a drift window).
2. **`bifrost deploy` ↔ git-sync refactor:** how much of the existing git experience to refactor while
   maintaining the ad-hoc `_repo/` version. Scope undefined.
3. **React app first-class mechanics (likely its own sub-spec):** the concrete approach to standard-React
   parity (de-magic the esbuild pipeline vs. real Vite + context-delivery rework) **and where the
   build runs** — in CI / `bifrost deploy` client-side (ship dist), or on the platform at deploy
   (ship src, build server-side). Leaning client-side build (ship dist; `_solutions/` stays
   source-free for React).
4. **Offline app SDK boundary:** exactly which web SDKs can run local vs. must hit the live data-plane
   (workflows local; tables/integrations/OAuth live is the starting line — confirm edge cases).
5. **`bifrost.solution.yaml` schema:** precise fields, and whether install metadata is DB-backed or
   manifest-only first.

---

## 7. Explicitly Out of Scope

- Monorepo-of-solutions (one repo per Solution until proven otherwise).
- Fully-offline dev (local data-plane / local stand-in backend). Offline = local exec + live data-plane.
- `watch` for Solutions (it is a `_repo/`-tier drift concept; Solutions are deploy-driven).
- Installing React app **source** onto the Solution surface (ship built `dist/` to `_apps/` instead).
- Multi-version *global* shared code (solution-local multi-version is solved by per-execution roots;
  genuinely shared importable code stays single-version in `_repo/`).
- Replacing or deprecating the ad-hoc `_repo/` git-sync workflow.
- Exporting table row data via deploy.
- Making Solutions themselves user-visible (only deployed entities are visible).

---

## 8. REAL STATUS (2026-06-05) — corrected after full Codex spec review

> An initial pass claimed all 18 criteria pass. A subsequent **full adversarial Codex
> review against this spec** found several criteria met only *superficially* — the
> mechanisms work in isolation but the spec's full intent (entity set, multi-install,
> connected mode, entity visibility) was not satisfied. PR #347 is **draft/experimental**
> until these clear.

### Confirmed P1 gaps (criteria NOT actually met)

| # | Gap | Criterion(s) | Where |
|---|-----|--------------|-------|
| G1 | **Deployed entities hidden from users** — `_apply_cascade_scope` filters `solution_id IS NULL` in `list()`, so deployed entities are invisible in normal list views. | **16** inverted | `api/src/repositories/org_scoped.py:321` |
| G2 | **Forms + agents cannot be deployed** — deploy/bundle/reconcile only handle workflows/tables/apps. | **6,10** | `contracts/solutions.py`, `deploy.py`, `git_sync.py`, CLI |
| G3 | **Multi-install collides for apps** — global unique index on app slug + repo_path. | **9** | `applications` unique index + `deploy.py:293` |
| G4 | **Git-connected sync drops apps** — `read_workspace_bundle` collects only workflows+tables; reconcile DELETES the app. | **13,12** | `git_sync.py:31` |
| G5 | **Ambiguous org-scoped deploy** — matches any same-slug non-null-org install, full-replaces the first. | **4** | `bifrost/commands/solution.py:204` |
| G6 | **Module isolation can bleed** — per-execution root doesn't namespace `sys.modules` (partly mitigated by content-hash clear). | **3** | `core/module_cache_sync.py` |
| G7 (P2) | **v2 app served at `/api/.../dist/` not `/apps/{slug}`** — routing/deep-links differ from spec. | **12** | `BundledAppShell.tsx:399` |

### Loop to "QA-ready"
Fix G1→G7 (TDD each), local-verify, re-run full Codex spec review, repeat until Codex
finds no P1/P2 spec gaps. Only then is this worth human QA.

---

## 8. REAL STATUS (2026-06-05) — corrected after full Codex spec review

> An initial pass claimed all 18 criteria pass. A subsequent **full adversarial Codex
> review against this spec** found that several criteria were met only *superficially* —
> the mechanisms work in isolation but the spec's full intent (entity set, multi-install,
> connected mode, entity visibility) was not satisfied. This section is the corrected,
> honest status. PR #347 is **draft/experimental** until these clear.

### Confirmed P1 gaps (criteria NOT actually met)

| # | Gap | Criterion(s) broken | Where |
|---|-----|---------------------|-------|
| G1 | **Deployed entities hidden from users** — `_apply_cascade_scope` filters `solution_id IS NULL` in `list()`, so deployed solution entities are invisible in normal list views. | **16** (inverted), usability of all | `api/src/repositories/org_scoped.py:321` |
| G2 | **Forms + agents cannot be deployed** — deploy/bundle/reconcile only handle workflows/tables/apps. | **6,10** (forms/agents read-only is superficial) | `api/src/models/contracts/solutions.py`, `deploy.py`, `git_sync.py`, CLI |
| G3 | **Multi-install collides for apps** — global unique index on app slug + repo_path; a 2nd install of an app-bearing solution violates it. | **9** | `applications` unique index (migration 20260220) + `deploy.py:293` |
| G4 | **Git-connected sync drops apps** — `read_workspace_bundle` collects only workflows+tables; auto-pull reconcile DELETES a connected install's app. | **13, 12** for connected | `api/src/services/solutions/git_sync.py:31-40` |
| G5 | **Ambiguous org-scoped deploy** — matches any same-slug non-null-org install, full-replaces the first; can hit the wrong client. | **4 (multi-install safety)** | `api/bifrost/commands/solution.py:204` |
| G6 | **Module isolation can bleed** — per-execution thread-local root doesn't namespace `sys.modules`; same-name modules across installs can collide (partially mitigated by content-hash clear in `_clear_workspace_modules`). | **3 (self-contained worlds)** | `api/src/core/module_cache_sync.py` |
| G7 (P2) | **v2 app served at `/api/.../dist/` not `/apps/{slug}`** — iframe document location differs from the spec scaffold; routing/deep-links don't behave like a normal app at the app route. | **12** | `client/src/components/jsx-app/BundledAppShell.tsx:399` |

### What IS genuinely solid
- Read-only enforcement on REST routers + the `before_flush` backstop + MCP (for ORM mutations) — proven by exhaustive e2e returning 409.
- Scoped full-replace reconcile for workflows/tables (never touches `_repo/`); table row data preserved.
- v2 app build → dist → `_apps/` serving; offline `bifrost run` with solution-local imports; vendoring; the read-only UI affordance (card/table/editor).
- CLI packaged correctly (no `src.*` imports; regression-guarded).

### Loop to "QA-ready"
Fix G1→G7 (TDD each), local-verify, re-run full Codex spec review, repeat until Codex finds no P1/P2 spec gaps. Only then is this worth human QA.

---

## 9. REAL STATUS (2026-06-12) — current

The two §8 sections above are the 2026-06-05 snapshot, kept for history. As of
2026-06-12, **G1–G7 are all closed** (entity visibility, forms/agents deploy,
multi-install app collision, git-sync dropping apps, ambiguous deploy resolution,
module isolation content-hash + per-install roots, same-document v2 mount), the
Solutions versioning/upgrade flow, the management UI redesign (CreateEditSolution,
table view, standard Organization selector, solution-level icon), and the RTM Portal
live drive are shipped on this branch.

### Remaining spec gaps

| Gap | Criterion | Detail |
|-----|-----------|--------|
| Auto-pull trigger missing | 13 (half) | One-writer holds (deploy/zip refused for connected installs) and the sync core works, but it is only reachable via manual `POST /api/solutions/{id}/sync`. No GitHub webhook / poller fires it on a push to main. |
| `global_repo_access` gates imports only | 4 (intent) | Solution executions resolve tables/configs own-first → `_repo/`; the global fallback is NOT gated by the flag, so a self-contained install can silently resolve a global-tier table/config by name. |

### Known follow-ups (not criteria)

- SDK `tables.check_access(...)` so workflows stop re-implementing table policy
  (the RTM staff-lockout class of bug; friction log #13).
- SDK tarball cache keyed on source mtime/hash, killing the docker-exec +
  api-restart dev dance (friction log #14).

### Shipped 2026-06-12 (post-merge of the main UI overhaul)

- **Export** (feedback "still no export button"): every write persists the
  install's workspace zip (`_solution_exports/{id}.zip`, pre-remap ids, byte-
  deterministic); `GET /api/solutions/{id}/export` + Export button on the
  solution page. The export is directly re-installable — proven e2e
  (install → export → preview matches the same install → re-install no-op).
- **/solutions paradigm**: verified against the merged Rhea overhaul — list
  and detail pages inherit the new conventions cleanly (search, view toggle,
  org filter, RoleDetail-shaped detail).
- **Moving things TO Solutions**: design proposal written —
  `2026-06-12-solution-capture-design.md` (capture = adopt-in-place + export;
  identity-preserving remap exception; dependency walker; open decisions for
  Jack). Not implemented.

---

## 10. RTM Portal punch list + Grant Templates (2026-06-12, in-flight)

RTM Portal lives in `~/GitHub/bifrost-workspace/solutions/rtm-portal`; deploys to
THIS worktree's debug stack (port mode, `http://localhost:37791`, also
`http://development.netbird.cloud:37791`). CLI scratch venv:
`/tmp/bifrost-cli-rtm-port`. Drive scripts: `/tmp/rtm-drive/`, shots `/tmp/rtm-shots/`.
Demo logins: `dee@rtm-demo.com` / `RtmDemo2026x` (RTM admin), architect@/jim.wagner@
same password (externals), `dev@gobifrost.com` / `password` (platform).

### DONE — v0.7.0 (committed to bifrost-workspace `64e37bc`, deployed, drive-verified)

- Documents: cascading Building + Floor filters (floor labels carry building when
  no building picked); verified excludes (2 docs → 1 on Floor 1, 0 on Floor 4).
- Document cards: click-to-view PDF, share/edit/archive icon group top-right,
  Download DWF standalone; responsive (stacked action row on phones).
- Shell: sidebar → horizontal nav strip ≤820px (`ShellStyles` in App.tsx).
- ToggleChip inline-flex fix (icon-above-text wrap); GrantMatrix at scale
  (campus filter >5, doc-type filter >10 + capped scrolling chip panel,
  searchable add-campus Combobox); grants keyed by facility_id not index.
- Groups question answered: C# Groups were NOT migrated (replaced by per-user
  grants) → Jack wants **Grant Templates** as the migration path (below).

### DONE — Grant Templates v0.8.0 (committed to bifrost-workspace, deployed, E2E drive GREEN)

Design (locked with Jack): template = named doc-type set + access level
(C# `Group` parity; `legacy_id` for GroupId migration). Reference + materialize:
`portal_access` rows get `template_id` stamp, content stays materialized so the
claims/policy chain is untouched; **editing a template re-materializes all
members** (mass maintenance). Per-user custom grants remain the option. Template
delete refused while in use. Template-first UX (CreateUser preselects first template).

Shipped: `grant_templates` table + `template_id` on portal_access (tables.yaml);
`manage_grant_template` workflow (create/update+re-materialize/delete-refuse-if-members);
`manage_portal_user` takes `template_id`; client GrantTemplates.tsx admin page,
template-first GrantEditor in Users modals, template badge on user cards,
DocTypePicker/TemplateCampusList extracted from GrantMatrix.

E2E proof (`/tmp/rtm-drive/templates-verify.mjs`, shots `/tmp/rtm-shots/templates/`):
dee created "Binders Only" (green+red), assigned to architect via the Grants
modal; architect saw 2 Riverside docs; dee added Egress Analysis to the template;
architect saw 3 — **no per-user action**. Per-campus expiry survives template
swaps (verified on tom). Delete disabled while in use. No HTTP/page errors.

Two bugs found & fixed along the way:
1. **Latent install bug:** workflows deployed `role_based` with no roles → ALL
   staff workflows (manage_portal_user, upload, every report) were
   platform-admin-only; dee got 403. Fix: `role_names:` in workflows.yaml
   (deploy resolves names→UUIDs). Admin-only: manage_portal_user,
   manage_grant_template. Staff: upload + all reports.
2. Dev-stack-only surgeries (NOT in code): architect@studio-demo.com had no
   password auth (hashed_password copied from jim + is_registered=true via
   psql, password RtmDemo2026x); tom's grants restored after a wrong-card
   drive click (template briefly assigned to him).

Migration note for the real cutover: import C# Groups → grant_templates rows
(stamp legacy_id = GroupId), then per user: campuses from CampusUser +
template_id from User.GroupId via legacy_id lookup → manage_portal_user
create/set_grants with template_id.
