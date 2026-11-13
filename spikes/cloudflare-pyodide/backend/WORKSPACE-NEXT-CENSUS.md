# Workspace census: `bifrost-workspace-next`

Measured 2026-11-13 against **`MTG-Thomas/bifrost-workspace-next` @ `37581f6`**
(private), the real workspace repo: 35 deployable Solution workspaces, 317
workflow entries. Static analysis with `dev/routing/census.mjs`, which drives the
dispatcher's own `decideBackend`. Nothing was executed.

An earlier run against the upstream repo's two example workspaces (11/11
eligible) is in [`WORKSPACE-CENSUS.md`](WORKSPACE-CENSUS.md); this supersedes it
as the measurement that matters.

## Headline

| Subrequest allowance | Eligible | Share |
|---|---|---|
| **12** (qualification fixture) | **295 / 317** | **93.1 %** |
| 50 | 303 / 317 | 95.6 % |
| 200 | 303 / 317 | 95.6 % |

`reference/community` was excluded: its manifest points at community files that
are not present in this repo (85 entries, 31 of them missing on disk). It is
reference material, not a deployable workspace.

## What the 22 ineligible workflows are

| Cause | Workflows | Detail |
|---|---|---|
| Unlisted package | 14 | `openrouter`, `pypdf`, `mammoth`, `bs4`, `reportlab` |
| Call density only | 8 | estimated 13–21 subrequests vs allowance 12 |
| (of which both) | 5 | package miss *and* over budget |

By solution: `bifrost-grc` 19, `bifrost-dmarc` 1, `bifrost-docs` 1,
`wondernote` 1. Every other solution is 100 % eligible.

Package-miss files: `grc_questionnaires.py` (7 entries, `pypdf` + `openrouter`),
`grc_recommendations.py` (3, `openrouter`), `grc_source_documents.py` (4,
`mammoth` + `bs4` + `reportlab`).

## Three findings that change the position

1. **Zero native-extension blocks across 317 workflows.** The entire
   third-party surface outside the allowlist is five pure-Python packages. That
   is strategy-1 territory (`DEPENDENCY-CENSUS.md`): bundle + measure each one,
   not route to Linux forever. The `cryptography`/`azure-*`/`pandas` cliff that
   dominated the theoretical discussion does not appear in this workspace.
2. **The allowance, not the code, is the second blocker.** Only eight workflows
   exceed 12 estimated subrequests (max 21). A dispatcher configured at 50 clears
   all eight with no workspace change. Twelve was a qualification fixture, never
   a platform ceiling.
3. **Failures cluster, they don't scatter.** 19 of 22 are in one solution
   (`bifrost-grc`, which does document extraction and LLM calls). Four solutions
   out of 35 carry every miss — a rollout can start with the other 31 today.

## Census defects this run forced out

Each was a real false verdict on real code:

- **Shared-module resolution.** Solutions import repo-root `modules/`,
  `helpers/` and `features/`; resolving only inside the solution root produced
  false `unresolved module` failures.
- **Namespace packages.** `functions/` directories without `__init__.py` are
  importable in Python 3 and by the engine's importer. Six dmarc workflows were
  wrongly rejected until namespaces resolved.
- **`reference/` manifests.** 85 entries, 31 absent on disk — now skipped
  explicitly rather than counted as failures.
- **Distribution vs import names** (`yaml`/`pyyaml`, `jwt`/`pyjwt`) — fixed
  earlier; would have false-rejected 19 files.
- **Test-file contamination** from Windows path separators — fixed earlier.

## Limits

- **Eligibility ≠ qualification.** 93.1 % means "would be routed to Workers",
  not "has run there". The per-tenant runtime is cloud-accepted
  (`CLOUD-TENANT-ACCEPTANCE.md`); these specific workflows are not.
- **Egress is not modelled** (a dispatch-time declaration), so a workflow
  needing an unlisted host is not detected here.
- **The subrequest figure is a heuristic**: SDK-looking call sites in the named
  workflow function + entry resolve + entry load + resolved local modules.
- **Dynamic imports are invisible**; stdlib traps are only the known hard set.
- **Lifecycle is invisible**: no daemons, durable sleep, or threading
  requirements are assessed.

## Recommendation

1. **Configure the Cloudflare allowance at 50, not 12.** 95.6 % with no code
   changes; 12 was a fixture value that was never a platform bound.
2. **Bundle the five pure-Python packages** behind per-package measured import
   tests (`requests`/`msal` show the method: +13 % for a bundle with ~47 MiB
   headroom). That closes the remaining 14.
3. **Pilot on the 31 fully-eligible solutions**, leave `bifrost-grc` on Linux
   until its package line is tested.
4. **Run the census in CI** on workspace changes so the routing split cannot
   silently rot.

## Receipts

- [`results/workspace-next-census-12.json`](../results/workspace-next-census-12.json)
- [`results/workspace-next-census-50.json`](../results/workspace-next-census-50.json)
- [`results/workspace-next-census-200.json`](../results/workspace-next-census-200.json)
- Tool: `dev/routing/census.mjs` (9 routing tests pass)
