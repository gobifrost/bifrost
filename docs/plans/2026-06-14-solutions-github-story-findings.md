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

## Drive 1 — install-from-repo + server-side source build (RISK GATE)

_(to be filled by Task 8)_

## Drive 2 — upgrade + update-available signal

_(to be filled by Task 16)_

## Drive 3 — connect-later (CLI deploy -> Connect repository)

_(to be filled by Task 16)_

## Drive 4 — full-data DR (export -> install into clean instance)

_(to be filled by Task 16)_

---

## Recommendations / deferred

_(to be filled as the drive surfaces findings)_
