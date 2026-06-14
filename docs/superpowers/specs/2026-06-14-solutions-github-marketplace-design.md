# Solutions GitHub Marketplace — Design

**Date:** 2026-06-14
**Branch:** `solutions/connection-references` (worktree `solutions-success-criteria`)
**Status:** Approved design → ready for implementation plan
**Predecessors:** [[2026-06-14-solution-connection-references-design]] (connection refs — declare integrations + template shells + Setup wizard), [[2026-06-14-solution-export-import-portability-design]] (export/import + encrypted secrets + table data round-trip)

This is "Phase 8" of the Solutions arc — the GitHub install/update/publish/DR story. It is a **build** (with an end-to-end drive against a real CSP solution), not just a findings exercise.

---

## The core insight

There are exactly **three ways content gets written into a Solution install**: a CLI `deploy`, a zip install, and a git pull. "Manual create" produces only an **empty shell** that one of those three must then populate — it is a degenerate prefix, not a real authoring path. The create UX today treats it as a peer, which is wrong.

A second insight unifies the install UX: **`/api/solutions/install/preview` already does "parse → show a read-only plan (entities + declared configs) → detect an existing install for upgrade-vs-install → confirm"** — but only for an uploaded zip. The marketplace "resolve a repo and prefill everything read-only as a confirmation" flow is the *same pipeline* with a clone+subpath step replacing the unzip step.

So the design collapses to: **make the existing preview→confirm→install pipeline accept a repo (+ subpath) as a source, drop empty-shell create, and lean on the server-side app build that already exists.**

### Grounding (verified against current code, 2026-06-14)

- `Solution.git_connected` + `git_repo_url` exist (`models/orm/solutions.py:107-110`). git-connected ⇒ deploy refused (`routers/solutions.py:830`), auto-pull is the only writer.
- `git_sync.sync()` clones the **whole repo**, expects `bifrost.solution.yaml` at the **repo root**, full-replace deploys. No subpath concept.
- The CLI `bifrost solution deploy` **already resolves-or-creates the install by `(slug, scope)`** (`commands/solution.py:1074-1102`) — create-on-deploy already exists. It has **no `--org`**; scope comes from the caller's default org only.
- `/api/solutions/install/preview` parses a zip and returns `SolutionInstallPreview` (entities + declared configs + existing-install diff) with **no DB write** (`routers/solutions.py:1025`).
- Apps build from source server-side on deploy via `_compile_app_dists` (npm install + vite build); a committed `dist/` is an optional fast-path, not a hard requirement.

---

## Components

### 1. `repo_subpath` — the omni-repo primitive

New nullable column `Solution.repo_subpath: str | None` (`None`/empty ⇒ repo root, fully backward compatible).

Thread it through `git_sync`: after the existing clone, load the descriptor + run the deploy from `<clone>/<repo_subpath>` instead of `<clone>`. The descriptor-locate (`_DESCRIPTOR_FILENAME` at `git_sync.py:40`) and the deploy workspace root are the only two points that change.

**Unlocks both shapes with one field:**
- **Maintainer omni-repo:** one repo `gobifrost/solutions` with `microsoft-csp/`, `rtm-portal/`, … each a folder containing its own `bifrost.solution.yaml`. Each install carries `repo_subpath="microsoft-csp"`.
- **Community single-repo:** one repo, descriptor at root, `repo_subpath` empty.

`repo_subpath` is also added to `SolutionCreate` and the descriptor model so create-on-deploy / git-connect can set it.

### 2. Install-from-repo preview

New `POST /api/solutions/install/preview-repo`, sibling of the zip preview.

- Body: `{ repo_url, repo_subpath?, ref? }`.
- Shallow-clone (the `git_sync` clone path, off the event loop) into a temp dir → locate descriptor at `<clone>/<repo_subpath>` → run the **same preview logic** the zip path runs.
- **Refactor:** `preview_zip(data)` is split so the parse/plan core accepts a **workspace directory** (`preview_workspace(dir)`), and `preview_zip` becomes "unzip to temp dir → `preview_workspace`". The repo path is "clone to temp dir → `preview_workspace`". One plan-builder, two front-ends. Returns the identical `SolutionInstallPreview` (entities, declared configs, existing-install diff for upgrade routing).
- Parse-only: no DB write, no S3, no build (same contract as the zip preview).

### 3. New-install UX reshape — drop empty-shell create

The "New install" entry point offers exactly two sources, both routed through preview→confirm→install:

- **From a repository** — enter `repo_url` (+ optional subpath, + optional ref) → `preview-repo` resolves → **read-only confirmation card** (name, version, scope, entities, declared configs/connections, upgrade-vs-fresh) → **Install** sets `git_connected=True`, `git_repo_url`, `repo_subpath` and triggers the git sync/deploy. The install is git-connected from birth (deploy refused; pull is the writer — the existing managed-state guard).
- **From a zip** — the current drag-and-drop → `install/preview` → confirm → install.

**The empty-shell "manual create" button is removed.** CLI-populated installs appear automatically via create-on-deploy when you run `bifrost solution deploy`. The UI's role in the CLI path is to *show the command*, not to pre-create an empty row.

### 4. Install-from-link + static catalog

A deep link opens the New-install flow pre-filled in "From repository" mode:

```
/solutions/new?repo=gobifrost/solutions&path=microsoft-csp[&ref=v1.2.0]
```

The "marketplace" is a **static** catalog — a markdown/JSON list (in the omni-repo's README or on the docs site) of `{ name, repo, path, version }` rows, each rendering one install-from-link button. **No platform registry DB, no discovery endpoint** this phase. The only platform requirement is that the deep link reliably pre-fills and the preview resolves.

### 5. Install detail view — Connect / Reconnect / Disconnect

Rename the per-install **"Edit"** to **"Details"**. Within it, a repository-connection section exposes the *connect-later* lifecycle:

- **Connect repository** (on a disconnected, CLI-populated install): set `git_repo_url` + `repo_subpath`, flip `git_connected=True`. From then on pull is the writer (deploy refused).
- **Reconnect** (change repo/subpath/ref on an already-connected install).
- **Disconnect**: flip `git_connected=False` → install becomes CLI-writable again (deploy allowed).

This is the "I've been deploying via CLI and now want to commit + connect the repo" path. It mutates the existing `git_connected`/`git_repo_url` (+ new `repo_subpath`) fields via the existing `PATCH /api/solutions/{id}` (`update_solution`, router:549) — no new write semantics, just surfaced as first-class actions.

### 6. `bifrost solution deploy --org`

Add `--org <id-or-slug>` to `deploy_cmd`. When resolving-or-creating the install, target the explicit org instead of the caller's default (`_resolve_target_install` currently takes only `deployer_org_id`). Matches the `--org` convention on other CLI commands; prevents creating an install in the wrong org and having to move it later. Default (omitted) = caller's default org, unchanged.

### 7. Server-side build is the path (verification, not new code)

Git-connected installs build apps from source on deploy via the existing `_compile_app_dists`. Committing `dist/` stays an **optional fast-path**, not a requirement. The deliverable here is **verification in the drive**: a *source-only* CSP repo (no committed `dist/`) installs cleanly from a repo link, building apps server-side. If the drive surfaces a real gap (e.g. build needs a dep the platform lacks, or the fast-path/source-path branch is mis-wired for the repo source), fix it; otherwise this item is "proven + documented."

### 8. Update-available signal (scheduled check → badge/event → one-click update)

For a git-connected install, updates arrive via pull — but the user must *learn an update exists*. This fits three existing patterns: a periodic scheduler job (cf. `oauth_token_refresh.py`), the PEP-440 version compare already in `deploy.py` (`_is_downgrade` / version parse), and a builtin event (cf. `emit_integration_refresh_failed`).

**Version source — descriptor `version:`, not git tags.** The check reads the `version:` field from `<repo_subpath>/bifrost.solution.yaml` at the configured `ref`'s HEAD (a shallow fetch / single-file read — no full clone). This is the single source of truth and works **identically for single-repo and omni-repo subfolders**: a repo-wide git tag cannot version N solutions in subfolders, so we deliberately do **not** depend on releases/tags. Authors just bump `version:` — no release ceremony. Git tags remain *supported for pinning* via the install's `ref`, but are not required for detection.

- **Scheduler job** `solution_update_check` (engine): for each git-connected install, fetch the descriptor version at the ref's HEAD, compare PEP-440 to the installed `version`, and persist `Solution.update_available_version: str | None` (cleared when up to date).
- **Surface:** the stored `update_available_version` renders an "Update Available" badge on the `/solutions` catalog card + Details view, wherever cheap.
- **Event:** emit a builtin `solution.update_available` when an install **newly** flips to having an update — subscribable in the event system (wire a workflow/notification to it).
- **Apply:** a one-click **"Update now"** (with confirm) in Details / on the badge triggers the existing git pull → full-replace deploy. No new apply semantics. Auto-apply is deferred (an author who really wants it can call the pull API from a workflow subscribed to the event).

### 9. The drive + findings doc

Build a local **omni-repo fixture** with `microsoft-csp/` as a folder: a fully-kitted solution with **two integrations** (declared connection refs → template shells → Setup wizard), **multiple shared modules**, a README + in-depth setup, and **no committed dist** (forces the server-side build). Source material: the v1 `apps/microsoft-csp` in `../bifrost-workspace` (migrate/adapt) and the shape of the existing `solutions/rtm-portal`. All names/secrets generic — nothing client-specific lands in the public repo.

Drive end-to-end on the debug stack, documenting every friction point in `docs/plans/2026-06-14-solutions-github-story-findings.md`:

1. Install-from-repo via deep link → preview confirmation → server-side source build → install.
2. Setup wizard satisfies the two connection refs.
3. Reinstall / upgrade (same slug, newer version) → upgrade routing via the preview diff.
4. Update signal: bump the descriptor `version:` in the fixture repo → scheduled check flips `update_available_version` → badge + `solution.update_available` event → one-click "Update now" → pull + full-replace.
5. Connect-later: a CLI-deployed install → Connect repository → subsequent pull.
6. DR: full backup export (encrypted secrets + table data) → install into a clean instance → everything materializes. Re-verify the export/import arc holds in the DR framing; map the CLI/API-driven DR runbook.

Findings the build doesn't close (e.g. additive-update mode, update-available signal) are recorded as recommendations, not built.

---

## Scope boundary (YAGNI)

**In:** `repo_subpath` (column + threading + descriptor + create); install-from-repo preview (refactor `preview_zip` → `preview_workspace` + clone front-end); New-install UI = From-repo + From-zip, empty-shell create dropped; install-from-link deep link + static-catalog convention; Details view with Connect/Reconnect/Disconnect; `deploy --org`; server-side-build verification for source-only repos; the update-available signal (scheduled descriptor-version check + badge + `solution.update_available` event + one-click Update now); the CSP omni-repo drive + findings doc.

**Out (this phase — recorded as findings, not built):**
- Platform discovery API / registry DB (catalog stays static).
- Webhook-driven (push) update detection — the check is a scheduled poll of the descriptor version.
- Auto-apply of updates (Update now is one-click-with-confirm; auto-apply via a workflow on the event is the escape hatch).
- Additive non-replace "Update" mode (deploy stays full-replace).
- A separate manual "Check for updates" button (the scheduled check + the persisted badge cover it).

---

## Sequencing & risk

Backbone first, drive early, polish last — so the drive catches gaps before the polish is over-built:

1. **Backbone:** `repo_subpath` (1) + `preview_workspace` refactor and `preview-repo` endpoint (2). Everything hangs off these.
2. **CLI:** `deploy --org` (6) — small, independent.
3. **Drive prep + first drive:** build the CSP omni-repo fixture and drive install-from-repo + the server-side build (7, 9 steps 1-2). **This is the risk gate** — a real 2-integration source-only solution is what proves the backbone and the server-side build actually hold. Fix what it surfaces before building UI polish.
4. **UI + signal:** New-install reshape (3), deep link (4), Details/Connect-Disconnect (5), update-available check + badge + event + Update now (8).
5. **Remaining drive:** upgrade, update-signal, connect-later, DR (9 steps 3-6) → findings doc.

## Testing

- Unit: `repo_subpath` descriptor round-trip; `preview_workspace` parity with `preview_zip`; `deploy --org` scope resolution; connect/disconnect state transitions on `update_solution`; the update-check version compare (descriptor newer / equal / older / unparseable → correct `update_available_version` + event-flip-once).
- E2E: install-from-repo round-trip (a local fixture repo → preview → install → entities present); git-connected deploy-refused invariant preserved with a subpath; connect-later flips the writer; update check against a fixture whose descriptor version was bumped → badge + event.
- Vitest: New-install source picker; Details connect/disconnect actions; the read-only confirmation card; the Update Available badge + Update now action.
- Drive (manual, on the debug stack): the 6-step CSP arc above — the real proof.

## Naming (used identically across the plan)

`repo_subpath` (column / descriptor / create field); `ref` (install's pin — branch/tag, default = repo default branch HEAD); `preview_workspace(dir)` (the shared plan core) vs `preview_zip(data)` (front-end); `POST /api/solutions/install/preview-repo`; `SolutionInstallPreview` (unchanged response); "From a repository" / "From a zip" (the two New-install sources); "Details" (the renamed Edit view); "Connect repository" / "Reconnect" / "Disconnect" (the lifecycle actions); `deploy --org`; `solution_update_check` (scheduler job); `Solution.update_available_version` (stored signal); `solution.update_available` (builtin event); "Update now" (one-click pull + full-replace).
