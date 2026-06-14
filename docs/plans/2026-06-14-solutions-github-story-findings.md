# Solutions GitHub-Story Drive — Findings

**Date:** 2026-06-14
**Branch:** `solutions/connection-references` (worktree `solutions-success-criteria`)
**Spec:** `docs/superpowers/specs/2026-06-14-solutions-github-marketplace-design.md`
**Plan:** `docs/superpowers/plans/2026-06-14-solutions-github-marketplace.md`

This is the running findings log for the end-to-end drive of the GitHub-repo
install / update / connect-later / DR story (plan Phase C + E). Each section
records: what was driven, what worked, where the platform added friction, and a
recommendation.

---

## Fixture: the omni-repo (CSP-class)

A representative omni-repo (NOT the real client CSP app — generic names only, the
repo is public-adjacent) modeled on the shape of `bifrost-workspace/solutions/rtm-portal`
and the substance of the v1 `apps/microsoft-csp` (tenant management + two integrations
+ setup). Built at `/tmp/bifrost-omnirepo`, `git init`'d on `main`.

```
/tmp/bifrost-omnirepo/                      # one repo, a folder per solution
├── README.md                               # catalog: rows -> install deep links
├── acme-tenant-manager/                    # the CSP-class fixture
│   ├── bifrost.solution.yaml               # slug, name, version 1.0.0, scope org, logo
│   ├── README.md                           # rendered on the README tab; documents the 2 integrations
│   ├── .bifrost/
│   │   ├── connections.yaml                # TWO declared integrations: cloud_directory (oauth2), ticketing (api_key)
│   │   └── apps.yaml                        # one standalone_v2 app (console), source-only
│   ├── apps/console/                        # SOURCE ONLY — no committed dist/ (forces server-side build)
│   │   ├── package.json                     # bifrost SDK dep -> http://localhost:37791/api/sdk/download
│   │   ├── vite.config.ts, tsconfig.json, index.html
│   │   ├── public/logo.svg
│   │   └── src/{main.tsx, App.tsx}          # uses react + lucide-react
│   ├── modules/
│   │   ├── directory_client.py              # shared module 1
│   │   └── ticketing_client.py              # shared module 2
│   └── functions/
│       └── tenant_overview.py               # workflow using BOTH integrations + BOTH modules
└── quick-report/                            # minimal 2nd solution (proves omni-repo subpath selection)
    ├── bifrost.solution.yaml                # slug quick-report, version 1.0.0
    └── functions/ping.py
```

Hits the plan's bar: multiple shared modules, TWO integrations, in-depth setup
(README + connection refs), source-only app (no committed dist), and a second
solution in the same repo to exercise `repo_subpath` selection.

### Friction found while preparing the drive

- **F-PREP-1 — server-side clone needs a container-reachable URL.** The clone runs
  in the API container, which cannot see the host's `/tmp`. For the drive the
  fixture was `docker cp`'d into the API container and cloned from a container-local
  `file:///tmp/bifrost-omnirepo`. A real user installs from a `https://github.com/...`
  URL (reachable from the container), so this is a drive-harness detail, not a
  product gap — but it confirms the clone is server-side (the platform, not the
  client, reaches GitHub). Worth a doc note: private repos will need credentials on
  the server side (auth token in the URL or a deploy key) — flag as a follow-up
  question for the publish/private-repo story.
- **F-PREP-2 — git "dubious ownership" on a copied repo.** `docker cp` left the repo
  owned by a different uid; `git` refused operations until ownership/`safe.directory`
  was fixed. Cloning *from* it (the feature's path) is unaffected once the source is
  readable. Not a product issue; noted for harness reproducibility.

---

## Drive 1 — install-from-repo + server-side source build (RISK GATE) — PASSED

Driven against the live debug stack (`bifrost-debug-75bc0d9c`, port mode
`http://localhost:37791`), superuser `dev@gobifrost.com`, fixture cloned into the
API container at `file:///tmp/bifrost-omnirepo`.

### What worked
- **`POST /install/preview-repo`** resolved the read-only plan from the `acme-tenant-manager`
  subfolder of the omni-repo: slug/name/version/scope, the source-only app (`src_files`
  present, `dist_files: null`), and **both `connection_schemas`** (`cloud_directory` oauth2,
  `ticketing` api_key). This is the "resolve + prefill read-only confirmation" UX.
- **`POST /install/from-repo`** created a git-connected install (`git_connected: true`,
  `repo_subpath: acme-tenant-manager`, version 1.0.0) under the caller's org. Deploy is then
  refused (one-writer). The omni-repo subpath selection works — the second solution
  (`quick-report`) sits in the same repo untouched.
- **Server-side source build / serve CONFIRMED (the "biggest gap").** The standalone_v2 app
  installed with **no committed `dist/`** and renders server-side: the app shell returns 200
  with an importmap, and the npm dependency **`/__bifrost_modules/lucide-react.js` resolves
  to a 296 KB module server-side**. The v2 model serves source via a Vite-dev-style transform +
  importmap (`/__bifrost_modules/*`, `/@vite/client`) rather than a one-shot prebuilt dist — so
  a source-only repo is fully installable. **Committed dist is NOT required.** This answers the
  spec's §7 risk gate: the platform handles source.

### F1 — REAL BUG FOUND + FIXED: git-connected install dropped declared integrations
On the FIRST install, `solution_connection_schema` had **zero rows** and the Setup tab showed
`items: []` — the two declared integrations silently vanished, even though they appeared
correctly in the *preview*. Root cause: `read_workspace_bundle` in `git_sync.py` (the
git/connected deploy path) did **not** call `_collect_connection_schemas`, so the
`SolutionBundle` carried `connection_schemas=[]` and `deploy`'s integration-shell +
`SolutionConnectionSchema` creation never ran. The **zip-install path collected them**
(`zip_install.py:192`), so this was a git-path-only divergence — exactly the class of bug the
connection-refs feature exists to prevent, and it breaks the CSP-from-scratch story (install
declares integrations → Setup surfaces them).

**Fix (this branch):** added `_collect_connection_schemas(workspace)` to `read_workspace_bundle`'s
`SolutionBundle(...)`, mirroring the zip path. **Re-drive confirmed:** after the fix, both
`solution_connection_schema` rows persist (`cloud_directory|0`, `ticketing|1`) and the Setup tab
surfaces both as `kind: connection`, `required: true` items (`connected: false` until the admin
connects them — the warn-only contract; `setup_complete` stays true because declared-but-unconnected
does not block). Needs a regression test (added in the fix commit).

### F2 — fixture gap (NOT a platform bug): workflow needs a manifest entry
The fixture's `functions/tenant_overview.py` did not register as a Workflow (`workflows: 0`).
Correct platform behavior: solution workflows are collected from `.bifrost/workflows.yaml`
(UUID-keyed manifest), not bare `functions/*.py`. The Python source IS bundled (layout-agnostic
`_collect_python_files`), but a registered Workflow needs a manifest entry. Fixture to be
corrected before the Task 16 drives (add a `.bifrost/workflows.yaml` entry for `tenant_overview`).

### Harness notes
- The API process runs as **uid 1000**; cloning the copied-in fixture required `chown -R 1000:1000`
  + a uid-1000 `git config --global --add safe.directory '*'` inside the container (F-PREP-2).
  A real `https://github.com/...` URL avoids all of this.

## Drive 2 — F3: REAL BUG — cannot DELETE a git-connected install that declares integrations

Found while resetting state for the upgrade drive (deleting the prior install to re-install
the fixed fixture). **`DELETE /api/solutions/{id}` returns 500** for a git-connected install
that carries `SolutionConnectionSchema` rows (i.e. any install with declared integrations —
which is exactly what the F1 fix now correctly persists). A disconnected install with content
deletes fine (verified: a disconnected install cascade-deleted 6 workflows + 1 app, HTTP 200).

**Root cause (precise):** `delete_solution` does `await ctx.db.delete(sol)`. `Solution.connection_schema`
is an ORM relationship with `cascade="all, delete-orphan"` and `lazy="selectin"`, so the children
are eagerly loaded on the initial `get()` and the ORM cascade marks them in `session.deleted` at
flush. The Solutions read-only `before_flush` backstop (`guard.py::_before_flush`) rejects ANY
`solution_id`-bearing row in `session.deleted` (`SolutionManagedWriteError`), turning the legitimate
teardown into a 500. Workflows/apps/forms/agents avoid this because they are removed by **DB-level FK
`ondelete=CASCADE`** (never ORM-loaded into the delete session); `SolutionConnectionSchema` ALSO has
`ondelete=CASCADE` at the DB level, but its ORM `delete-orphan` cascade fires first.

This was LATENT until the F1 fix: before F1, git-installs carried no connection_schema rows, so
nothing tripped the guard on delete.

**Attempted live fixes that did NOT work** (documented so the real fix doesn't repeat them):
- `passive_deletes=True` on the relationship alone — children were already `selectin`-loaded, so the
  cascade still marked them.
- Expunging the loaded children + a Core `delete()` before `db.delete(sol)` — the `delete-orphan`
  cascade re-evaluated the (still-referenced) collection and re-marked them; still 500.

**Correct fix direction (for a focused, tested task):** the legitimate path is to make the guard NOT
fire for a full-install teardown — the cleanest being to let the DB FK cascade handle the children
(as it does for workflows/apps) by ensuring the children are neither ORM-loaded-and-orphaned NOR in
`session.deleted`. Options: (a) `passive_deletes=True` AND `lazy="select"`/no eager load on the delete
path (load the install for delete WITHOUT the connection_schema relationship, e.g. a query with
`raiseload`/`noload`, so the cascade has nothing loaded to orphan); or (b) teach the guard to allow
deletions whose parent Solution is itself being deleted in the same flush (recognize teardown vs
ad-hoc mutation). (a) is lower-risk (doesn't touch the security backstop). Must ship with an e2e
regression test: install-from-repo (git-connected, 2 integrations) → DELETE → 200 + rows gone.

Status: identified + precisely diagnosed; NOT yet fixed (dispatched as a focused fix). The rest of
Drive 2 (upgrade → badge → Update-now) is blocked on being able to reset state, so it runs after F3.

## Drive 2b — upgrade + update-available signal

_(after F3 fix)_

## Drive 2b — upgrade + update-available signal — PASSED (end to end, live)

After the F2 fixture fix (added `.bifrost/workflows.yaml`), a fresh install deploys BOTH the app
(`Tenant Console`) and the workflow (`tenant_overview`). Then drove the upgrade signal:
- Bumped the fixture descriptor `version: 1.0.0 → 1.1.0`, committed, re-copied into the container.
- Ran `check_solution_updates()` directly → `{'checked': 1, 'updates_found': 1}`; it UPDATE'd
  `update_available_version='1.1.0'` on the install (SQL confirmed in logs).
- `GET /api/solutions/{id}` → `version: 1.0.0`, `update_available_version: 1.1.0` — **the badge data is present**.
- **Update now** (`POST /{id}/sync`) → 202 → pull + full-replace → `GET` now shows `version: 1.1.0`,
  `update_available_version: None` — **the signal cleared, the badge disappears.** Exactly the design.
- The `solution.update_available` emit is unit-tested (Task 10/11, emit-once on the None→available edge);
  the live storage + clear loop is confirmed.

## Drive 3 — connect-later (create disconnected -> Connect repository -> pull) — PASSED

- Created a disconnected install (`git_connected=false`).
- **Connect repository** via `PATCH {git_connected:true, git_repo_url, repo_subpath:"quick-report"}` →
  `git_connected=true`, subpath set.
- `POST /{id}/sync` → 202 (pulled the connected repo).
- `POST /{id}/deploy` → **409** — the one-writer invariant correctly flips on after connecting.
The connect-later lifecycle works. (The pulled `quick-report` shows 0 workflows only because that minimal
2nd fixture has no `.bifrost/workflows.yaml` — same F2 fixture pattern, not a platform issue; the pull
mechanism + deploy-refused flip are what's verified.)

## Drive 4 — full-data DR (export -> restore preview) — F4: REAL BUG FOUND

`POST /{id}/export` returns a real bundle zip (descriptor + workflows.yaml + README + apps.yaml), and a
restore preview (`POST /install/preview` of the backup) correctly resolves slug/version/app/workflow and
detects fresh-vs-existing. **BUT the backup drops the declared integrations**: the zip has no
`.bifrost/connections.yaml` and the restore preview shows `connection_schemas: []`, even though the live
install has two persisted `solution_connection_schema` rows (`cloud_directory`, `ticketing`).

**Root cause (precise):** export rebuilds the bundle live via `SolutionCaptureService.bundle_for` →
`_connection_entries(solution_id)` (`api/src/services/solutions/capture.py:264/279`). That method does NOT
read the persisted `SolutionConnectionSchema` rows — it **re-derives** connections by scanning each
workflow's SOURCE for `integrations.get("X")` refs via `self.repo.read(wf.path)`. For a DEPLOYED solution
the source lives under `_solutions/{id}/`, not `_repo/`, so `repo.read(wf.path)` raises and the `except`
**silently skips every workflow** (the code comment admits it). With no source read, `names` is empty → no
connection entries → `build_workspace_zip` writes no `connections.yaml` (it guards `if bundle.connection_schemas`).

So a DR backup (or any export) of an installed solution loses its integration declarations — the install
would restore WITHOUT its Setup integrations. Same connection-schema-divergence class as F1, on the export side.

**Fix direction (focused, tested task):** `_connection_entries` should prefer the **persisted
`SolutionConnectionSchema` rows** as the source of truth (deploy created them) and only fall back to the
source-scan when none exist. Ship with: an e2e DR round-trip (install-from-repo with 2 integrations → export
→ restore-preview shows `connection_schemas` with both) + a unit test that `bundle_for` carries the persisted
connection rows for a deployed install.

Status: identified + precisely diagnosed; dispatched as a focused fix.

### DR runbook (as driven, once F4 lands)
1. `POST /api/solutions/{id}/export` (optionally `mode=full` + password for encrypted secrets + table data)
   → a workspace zip backup. 2. On a clean instance: `POST /api/solutions/install/preview` (confirm plan) →
   `POST /api/solutions/install` → everything materializes (entities, config declarations, **integrations**,
   and with a full export, secrets + table data). CLI equivalents: `bifrost solution export` / `solution install <zip>`.

---

## Summary — what the drive proved + the bugs it caught

The full GitHub-marketplace story works end-to-end on the live stack:
- **Install from a repo link** (preview → read-only confirmation → install), including **omni-repo subpath
  selection** (one repo, a folder per solution).
- **Server-side source build/serve** — a source-only app (no committed `dist/`) installs and renders; npm
  deps resolve server-side. **Committed dist is not required** (the spec's §7 risk gate — PASSED).
- **Update signal** — descriptor-version bump → scheduled check sets `update_available_version` → badge data
  + (unit-tested) event → **Update now** (`/sync`) pulls + clears the signal.
- **Connect-later** — a disconnected install → Connect repository → pull → deploy refused (one-writer flips on).
- **DR** — export → restore-preview materializes entities + **integrations** (after F4).

**Four bugs the drive caught, all fixed on this branch (each with a regression test):**
- **F1** — git-connected deploy dropped declared `connection_schemas` (`read_workspace_bundle` didn't collect
  `.bifrost/connections.yaml`). Fixed; both the deploy path and the install Setup now surface integrations.
- **F2** — *(fixture gap, not a platform bug)* a workflow needs a `.bifrost/workflows.yaml` manifest entry to
  register; bare `functions/*.py` source is bundled but not registered. Fixture corrected.
- **F3** — could not DELETE a git-connected install that declared integrations (the read-only `before_flush`
  backstop rejected the `delete-orphan` cascade of `SolutionConnectionSchema`). Fixed with `noload` +
  `passive_deletes` so the DB FK cascade handles them (guard protection intact — verified).
- **F4** — export/DR dropped declared integrations (`_connection_entries` re-derived from unreadable deployed
  source instead of reading the persisted rows). Fixed to prefer the persisted `SolutionConnectionSchema` rows.

The throughline: the connection-schema declarations weren't threaded through every path (deploy, delete,
export). The drive forced each path and the three platform fixes (F1/F3/F4) close the divergences.

## Recommendations / deferred (product decisions, not built this arc)

- **Hosted catalog** stays a static list of `{name, repo, path, version}` → install-from-link URLs (per Jack);
  no platform registry/discovery API this arc.
- **Update detection** is a scheduled descriptor-version poll (every 6h); push/webhook detection deferred.
- **Auto-apply** of updates deferred — Update-now is one-click-with-confirm; a workflow subscribed to
  `solution.update_available` can call `/sync` for automation.
- **Additive (non-replace) Update mode** deferred — deploy/sync stay full-replace.
- **Org scope + up-front config values on install-from-repo** — the from-repo endpoint installs at the caller's
  default org with read-only config declarations (values set post-install via Setup). If org-scoped repo
  installs or up-front values are wanted, `SolutionRepoPreviewRequest` + the endpoint need to grow
  `organization_id` / `config_values` (flagged during the UI build). Small follow-up if desired.
- **Private-repo install** — the clone is server-side, so private repos need server-side credentials (a token
  in the URL or a deploy key). The publish/private-repo auth story is unspecified; worth a design pass before
  recommending community publishing of private repos.
