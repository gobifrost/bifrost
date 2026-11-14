# Eligibility routing and workspace census

Two small, dependency-free modules:

| File | Purpose |
|---|---|
| `eligibility.mjs` | `decideBackend(attempt)` — pure, fail-closed decision. Returns `{backend: 'cloudflare' \| 'linux', reasons}`. Never dispatches, enqueues, or calls an API. Also owns the curated allowlist and import→distribution normalization. |
| `census.mjs` | Runs that same rule over real files: discovers workspaces (`.bifrost/workflows.yaml`) or scans a repo, estimates subrequests per workflow function, and emits JSON + a summary. |
| `census-gate.mjs` | CI gate: fails when the eligible share drops below a threshold, or when a workflow eligible in the recorded baseline becomes ineligible. |
| `workspace-census.workflow.yml` | Reference GitHub Actions job for the workspace repo (copy into `.github/workflows/`). |

## Run

```sh
node --test routing.test.mjs

# workspace mode: real workflow entries from .bifrost manifests
node census.mjs --workspace ../../../.. --allowance 12 --out out.json

# repo mode: every workflow-shaped Python file (context only, not a workload mix)
node census.mjs --repo ../../../.. --allowance 12 --out out.json
```

`--allowance` is the dispatcher's configured subrequest budget (default **50**; 12 was
the qualification fixture). `--cpu` sets the declared CPU budget (default `1000ms`). Both
are policy inputs, not platform constants.

## What the census does and does not do

It classifies imports (stdlib / allowlisted / provided / workspace-local),
resolves local imports against the workspace on disk, scans the named workflow
function for known Pyodide traps, and estimates subrequests from call density.

It does **not** infer egress (a dispatch-time declaration), see dynamic imports,
measure real request counts, or execute anything. Treat the output as a routing
preview, not a qualification.

Measured results and limits: [`../../backend/WORKSPACE-CENSUS.md`](../../backend/WORKSPACE-CENSUS.md).
