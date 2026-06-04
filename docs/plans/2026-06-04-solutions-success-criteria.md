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
**On top of that** we add **Solutions**: org-scoped, git-owned, deployable units of Bifrost
functionality. A Solution is a normal git repo with normal CI; when CI passes it is deployed to a
platform instance — conceptually like `docker compose up` stands up an instance, but for a unit of
Bifrost functionality. Solution-managed entities are **read-only on the platform**: git (or
`bifrost deploy`) is the only writer, which eliminates drift by construction instead of by merge.
Solutions are self-contained worlds (their own import root), install per-org, and can be installed
multiple times. React apps inside a Solution are first-class and must feel like normal React.

---

## 2. The Two Tiers (the core mental model)

| | `_repo/` (ad-hoc, global) | `_solutions/{solution_id}/` (Solutions) |
|---|---|---|
| Source of truth | Git repo w/ drift workflow | Git repo, write-only to platform |
| Platform editing | Editable; drift tracked via diff/commit | **Read-only** for solution-managed entities |
| Writer | In-platform edits + git commit | `bifrost deploy` **or** git-connected pull (exactly one per install) |
| Scope | Global / shared library | One org per install |
| Import root | `_repo/` | `solutions/{solution_id}/`, fallback to `_repo/` only if "global access" enabled |
| Purpose | Shared code, one-offs, fast iteration | Deployable client/product units |
| Status | **Unchanged. Preserved as-is.** | New |

Over time `_repo/` can become "the shared library" and Solutions become "the deployable units" —
but that evolution is optional and not forced.

---

## 3. Locked Decisions

### 3.1 Terminology
- The unit is a **Solution**. (Not "capability", not "bundle", not "module".)
- An installed Solution is an **install**, identified by `solution_id` (a UUID per install).

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

### 3.3 Org model
- A Solution installs for **exactly one org**; every entity under it **inherits** that org. No
  per-entity `organization_id` rewrite — org is a single property of the install.
- **Global/shared functionality is NOT a Solution.** It lives in `_repo/`. Solutions reach it via
  an opt-in **"Enable global repository access"** checkbox on the Solution (**off by default**).
- Install identity is unique per **(solution, org)** — no scope overlap between installs.

### 3.4 Multiple installs
- One Solution definition in git can be **installed multiple times** (e.g. ship `halo-ticketing`
  once, install it for N client orgs). Each install is independent, keyed by `solution_id`.
- Deploy resolves *which install* it targets by solution-identity + org; `--solution {uuid}` is the
  explicit override when ambiguous. (On upload, matching solution name MAY update an existing install.)

### 3.5 Runtime / imports (resolves the flat-namespace problem)
- Execution always starts by running a workflow. If the workflow has a `solution_id`:
  - Import root is `solutions/{solution_id}/` — `from modules.x import y` resolves to
    `_solutions/{solution_id}/modules/x.py` (imports work from the solution root, exactly like
    running locally from the directory root).
  - Falls back to `_repo/` **only if "Enable global repository access" is on**.
- Solutions are **self-contained worlds**: `solutions/A/modules/x` and `solutions/B/modules/x` are
  different files at different roots and never collide. The per-execution scoping is what makes
  multi-version solution-local code safe (no global `sys.modules` shadowing).
- New S3 prefix `_solutions/` parallel to `_repo/`.

### 3.6 Storage & reconcile
- Solution source lives under S3 `_solutions/{solution_id}/`, parallel to `_repo/`.
- **Deploy = full replace, scoped strictly to `solution_id`:** upsert everything in the bundle;
  delete entities previously under this `solution_id` that are absent from the new bundle.
  - **Never touches `_repo/` or any other install.** `_repo/` is out of scope for all Solution ops.
  - Requires the viability study's deletion-sweep gating fix, **re-scoped to `solution_id`** (delete
    when "absent from THIS solution's bundle", not the current destructive global path-existence check).

### 3.7 Tables
- Solution owns table **schema + policies** (RLS-like — an app defines its own data-access rules).
- **Row data is runtime state; deploy never writes or wipes it.** A redeploy with a changed schema
  migrates structure (add/alter) and **preserves rows**. Mirrors the existing app-source vs app-data split.

### 3.8 Export / portability
- "Export Solution" **scans the Solution's Python** for shared-module usage and offers to **vendor
  those shared modules into the Solution**, so the export is genuinely self-contained and installable
  on another instance.
- Vendored shared modules compose cleanly with import resolution: a Solution's own copy wins because
  resolution starts at the solution root — no copy into `_repo/`, no collision, no global shadowing.
- Tables export schema + policies; data is never exported by deploy.

### 3.9 Git-connected mode (keeps "one writer" true)
- A Solution install can be **connected to a single git repo** (one repo per Solution; monorepo is
  explicitly out of scope until a real need appears — YAGNI).
- **Connected install:** the git repo is the only writer. Platform polls/webhooks `main` and
  **auto-deploys** on new commits. `bifrost deploy` is **disabled** for this install.
- **Disconnected install:** `bifrost deploy` is the only writer.
- Either way: **exactly one writer**, read-only in the UI. Connected mode reuses the existing git
  pull machinery (`github_sync.py`) and disables the commit half.

### 3.10 React apps (now a first-class success criterion)
- A Solution's React app must feel like a **normal React project**: the local dev loop, the import
  model, and deploy must make it behave like standard React — not the current synthesized-bundle
  experience. (The viability study found this is orthogonal to the org/source model and structurally
  blocked today by inline-render context inheritance; making it first-class is in scope here.)

---

## 4. Success Criteria (falsifiable "done")

The end-to-end proof uses the **real `bifrost-workspace`** (`gocovi/bifrost-workspace`) as source
material — take a real slice (e.g. `clients/mna` or `braytel`), turn it into a Solution, and confirm:

1. **No regression:** existing ad-hoc `_repo/` functionality works **untouched** alongside Solutions.
2. **Side-by-side deploy:** the Solution deploys and runs concurrently with `_repo/` functionality.
3. **Solution-local imports:** a workflow in the Solution imports its own `modules/*` from the
   solution root and runs.
4. **Global-access fallback:** with "Enable global repository access" ON, the Solution can import a
   `shared.*` module from `_repo/`; with it OFF, that import does **not** resolve (no silent fallback).
5. **Vendored shared deps:** export-with-shared-scan produces a self-contained Solution that installs
   on a *fresh* instance (no `_repo/` shared deps present) and its imports resolve to the vendored copies.
6. **Read-only enforcement:** solution-managed entities are read-only in the UI **and** every non-deploy
   mutation API returns the "Solution-managed…" error.
7. **Editable carve-out:** OAuth token mappings and secret config values remain editable on a
   solution-managed entity's install.
8. **Org inheritance:** all entities in an install carry the install's org; no per-entity org binding step.
9. **Multiple installs:** the same Solution installs for two different orgs as two independent installs
   with no scope overlap.
10. **Full-replace reconcile:** redeploying a Solution with an entity removed deletes that entity for
    **this install only**, and never affects `_repo/` or other installs.
11. **Table data preserved:** redeploying a Solution with a changed table schema migrates structure and
    preserves existing rows.
12. **React app parity:** a Solution's React app builds and runs like a normal React project, with a
    local dev loop that feels like standard React.
13. **Git-connected:** a connected install auto-deploys on a push to `main`, and `bifrost deploy` is
    refused for that install (one-writer invariant holds).

---

## 5. Prerequisite Fixes (from the viability study — required before/with this work)

- **Deletion-sweep gating fix**, re-scoped to `solution_id` (without it, deploy/reconcile is silently
  destructive). Highest leverage.
- **MCP `service_oauth_token_id`** added to the portable scrub + test (it currently leaks a live
  service-token FK in "portable" exports — relevant because Export Solution rides the same boundary).
- **Org-scoping in manifest generation** (`generate_manifest` currently dumps all orgs — a per-org
  Solution export must not cross-contaminate tenants).

---

## 6. Open Questions (resolve in spec, not story)

1. **Connected-mode dev loop:** intended path is to iterate against a *disconnected dev install*
   (`bifrost deploy` works there) and ship to the *connected prod install* via merge-to-main. Confirm
   this is the only fast-iteration path, or whether a guarded preview-deploy is wanted (leaning no —
   re-opens a drift window).
2. **`bifrost deploy` ↔ git-sync refactor:** how much of the existing git experience to refactor while
   maintaining the ad-hoc version. Scope undefined.
3. **React app first-class mechanics:** the concrete approach to standard-React parity (de-magic the
   esbuild pipeline vs. real Vite + context-delivery rework). Big enough to be its own sub-spec.
4. **Solution definition file:** what the in-repo Solution descriptor looks like (id, slug, name,
   global-access flag, declared deps) and whether install metadata is DB-backed or manifest-only first.

---

## 7. Explicitly Out of Scope

- Monorepo-of-solutions (one repo per Solution until proven otherwise).
- Cross-org Solutions (org is single-valued per install; global = use `_repo/`).
- Multi-version *global* shared code (solution-local multi-version is solved by per-execution roots;
  genuinely shared code stays single-version in `_repo/`).
- Replacing or deprecating the ad-hoc `_repo/` git-sync workflow.
- Exporting table row data via deploy.
