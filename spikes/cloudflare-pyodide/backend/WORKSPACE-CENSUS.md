# Workspace census: what share of normal workloads is eligible?

> **Superseded as the headline measurement** by
> [`WORKSPACE-NEXT-CENSUS.md`](WORKSPACE-NEXT-CENSUS.md): the real workspace
> (`MTG-Thomas/bifrost-workspace-next`) measures **295/317 workflows (93.1 %)
> eligible at allowance 12, 303/317 (95.6 %) at 50**. The example-workspace
> result below stands as the first, smaller sample.

Measured 2026-11-13 with `dev/routing/census.mjs`, which drives the dispatcher's
own `decideBackend` rule (`dev/routing/eligibility.mjs`) over real files. Static
analysis only — no workspace was executed, no API was called.

## Result

| Workspace | Workflows | Eligible at allowance 12 | Estimated subrequests |
|---|---|---|---|
| `examples/rewst-migration` | 8 | **8 / 8** | 6–7 per workflow |
| `examples/workspace-import-kitchen-sink` | 3 | **3 / 3** | 2 per workflow |
| **total** | **11** | **11 / 11 (100 %)** | 2–7 |

No hard runtime traps were found in either workspace: no `time.process_time()`,
`subprocess`, `multiprocessing`, `fcntl`, `os.fork`, `ctypes`, raw sockets or
POSIX signals.

The Rewst migration solution is a genuine MSP automation (GraphQL reads, table
writes, file artifacts, plan state) and it is **dependency-clean**: stdlib +
`bifrost` SDK + solution-local modules, nothing outside the curated allowlist.
That is the workload shape we actually care about.

## Repo-wide census (context, not a workload mix)

115 workflow-shaped Python files (imports `bifrost` and defines `async def`;
test files excluded):

| Role | Eligible at 12 |
|---|---|
| SDK (`api/bifrost/**`) | 10 / 21 |
| CLI (`api/bifrost/commands/**`) | 0 / 24 |
| Platform (`api/src`, `api/shared`) | 15 / 41 |
| Spike fixtures | 13 / 24 |
| Docs | 2 / 2 |
| Example workspace | 1 / 1 |
| Other | 0 / 2 |
| **total** | **41 / 115 (35.7 %)** |

Allowance sensitivity: **41** at 12, **48** at 50, **48** at 200. So seven files
are merely call-dense, and the remaining 67 are dependency blocks.

Top blocking imports: `sqlalchemy` 29, `click` 27, `fastapi` 6, `pathspec` 5,
`textual` 4, `packaging` 4, `aiohttp` 3, `websockets` 3. **Every one is platform
or CLI code** — none is a workflow dependency. That is why this number must not
be read as a customer-workload split.

## What actually binds

1. **Dependencies, not Python.** Both real workspaces are clean. The cliff is
   native/uncurated packages (`cryptography`, `pandas`, `azure-*`, `boto3`),
   and it is a per-package routing decision, not a runtime limitation.
2. **The subrequest estimate**, not wall time. The real solution sits at 6–7
   estimated subrequests per workflow, comfortably inside the 12 the
   qualification fixture used.
3. **Lifecycle**, which this census cannot see: no daemons, no persistent
   services, no durable sleep. A workspace can be 100 % dependency-eligible and
   still contain a workflow that must stay on Linux.

## Two defects the census found in the router

Fixed in this change, both of which would have mis-routed real workloads:

- **Distribution vs import names.** The allowlist is keyed by distribution
  (`pyyaml`, `pyjwt`, `python-dotenv`), but workflow code imports `yaml`, `jwt`,
  `dotenv`. Before the fix, `import yaml` read as an allowlist miss — 19 repo
  files were falsely rejected. `IMPORT_TO_DISTRIBUTION` now normalizes both.
- **Test-file contamination** in the repo pass (Windows path separators skipped
  the `tests/` filter), which inflated the blocked count with `pytest`.

## Limits of this measurement

- **Sample: 2 workspaces, 11 workflows.** This is the repo's example content,
  not a customer fleet. It establishes that the shape is eligible; it does not
  establish a fleet percentage.
- **Egress is not modelled.** It is a dispatch-time declaration; the census
  assumes none required, so it cannot detect a workflow that needs an unlisted
  host.
- **The subrequest figure is a heuristic**: SDK-looking call sites in the named
  workflow function + entry resolve + entry load + local module count. It is
  not a measured request count.
- **Dynamic imports are invisible** (`importlib`, `__import__`).
- **Stdlib presence is not stdlib function**: only known hard traps are
  scanned; the full Pyodide stdlib gap is broader.
- Per-workflow slicing is textual (top-level `def` boundaries), so nested
  definitions are approximated.

## Next measurement that would settle rollout

Run the same tool against a real tenant workspace payload:

```sh
node spikes/cloudflare-pyodide/dev/routing/census.mjs \
  --workspace <path-to-workspace> --allowance 12 --out census.json
```

That produces per-workflow eligibility with reasons, which is the number a
rollout decision needs. The two-workspace result says the shape is eligible; it
does not say how many of your workflows are.

## Receipts

- [`results/workspace-census.json`](../results/workspace-census.json)
- [`results/repo-census.json`](../results/repo-census.json)
- `dev/routing/census.mjs`, `dev/routing/eligibility.mjs`, `dev/routing/routing.test.mjs` (9 tests)
