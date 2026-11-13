# Workspace census: `bifrost-workspace-next`

Measured 2026-11-13 against **`MTG-Thomas/bifrost-workspace-next` @ `37581f6`**
(private): 35 deployable Solution workspaces, 317 workflow entries. Static
analysis with `dev/routing/census.mjs`, which drives the dispatcher's own
`decideBackend`. Nothing was executed.

## Headline

| Subrequest allowance | Eligible | Share |
|---|---|---|
| 12 (the original qualification fixture) | 304 / 317 | 95.9 % |
| **50 (configured policy)** | **317 / 317** | **100 %** |

`reference/community` was excluded: its manifest points at community files that
are not present in this repo. It is reference material, not a deployable
workspace.

## What changed since the first run

The first pass measured **295/317 at allowance 12** with five unlisted packages.
Two things closed the gap, both now done:

1. **The allowance was a fixture value, not a policy.** 12 was chosen to prove
   the estimate mechanism. 50 is a normal dispatcher setting; at 50 only 13
   workflows remained blocked, all by call density.
2. **The five packages were qualified in a real Worker** (`DEPENDENCY-CENSUS.md`
   "Result 2"): `pypdf`, `beautifulsoup4`, `mammoth`, `openrouter`, `reportlab`
   (+ `pillow`, `soupsieve`, `cobble`, `httpcore`, `jsonpath-python`). All import
   and perform their job; bundle cost is +32.5 MB uncompressed / +6.3 MB gzip.

## The remaining 13 (at allowance 12)

All are call-density, not dependencies: `grc_agent_tools` (5), `grc_questionnaires`
(5), `dmarc` (1), `docs/migration` (1), `wondernote` (1), estimated 13–21
subrequests. They are eligible at 50 with no code change.

## Findings

1. **Zero native-extension blocks across 317 workflows.** The whole non-allowlist
   surface was five pure-Python packages, now qualified. The
   `cryptography`/`azure-*`/`pandas` cliff does not appear in this workspace.
2. **The allowance is a policy decision, not a wall.** 12 → 95.9 %, 50 → 100 %.
   The dispatcher's subrequest estimate is the control, and the estimate is
   conservative (call sites + entry/module resolution).
3. **Compressed bundle size is now the binding budget.** 9.15 MiB gzip for the
   full Worker. Any further package is a budgeted decision, and `openrouter`
   alone is 1.2 MB gzip / 1,229 files. Bundle startup is unaffected (120 ms), but
   first import of the heavy set costs ~4.9 s once per isolate.

## What the census forced out

Five false verdicts on real code, all fixed:

- **Shared modules** — solutions import repo-root `modules/`, `helpers/`,
  `features/`; per-solution resolution failed them.
- **Namespace packages** — `functions/` without `__init__.py`; 6 dmarc workflows
  were wrongly rejected.
- **`reference/` manifests** — 85 entries, 31 absent on disk.
- **Distribution vs import names** — `yaml`/`pyyaml`, `jwt`/`pyjwt`; 19 files
  would have been false-rejected.
- **Test-file contamination** from Windows path separators.

## Limits

- **Eligibility ≠ qualification.** 317/317 means "would be routed to Workers",
  not "has run there". The per-tenant runtime is cloud-accepted; these specific
  workflows are not.
- **Egress is not modelled** (dispatch-time declaration).
- **The subrequest figure is a heuristic**, and it is conservative.
- **Dynamic imports and lifecycle are invisible** (daemons, durable sleep).

## Recommendation

1. **Run at allowance 50.** 100 % static eligibility for 317 workflows.
2. **Ship the five packages** — measured, functional, and paid for only by the
   solution that uses them. Watch the 9.15 MiB gzip budget for anything further.
3. **Pilot any solution** — the routing split no longer excludes one. What
   remains is runtime qualification per workflow, not routing.
4. **Gate the census in CI** so the split cannot rot (`dev/routing/census-gate.mjs`
   plus the workflow template in `dev/routing/workspace-census.workflow.yml`).

## Receipts

- [`results/workspace-next-census-12.json`](../results/workspace-next-census-12.json)
- [`results/workspace-next-census-50.json`](../results/workspace-next-census-50.json)
- `dev/routing/census.mjs`, `eligibility.mjs`, `census-gate.mjs`, 10 routing tests
- `backend/package-import/` (functional import qualification)
