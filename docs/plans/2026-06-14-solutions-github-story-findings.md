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

## Drive 2 — upgrade + update-available signal

_(to be filled by Task 16)_

## Drive 3 — connect-later (CLI deploy -> Connect repository)

_(to be filled by Task 16)_

## Drive 4 — full-data DR (export -> install into clean instance)

_(to be filled by Task 16)_

---

## Recommendations / deferred

_(to be filled as the drive surfaces findings)_
