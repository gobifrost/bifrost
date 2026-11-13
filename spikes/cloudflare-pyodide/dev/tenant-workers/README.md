# Per-tenant persistent Python workers (prototype)

Pivot from **fresh isolate per attempt** (`LOADER.load` per attempt) to **one long-lived generic
worker per tenant** (`LOADER.get(tenantWorkerId, genericScript)`), with attempts per tenant
serialized. Decision record and cost model: [`backend/ADR-PER-TENANT.md`](../../backend/ADR-PER-TENANT.md).
Measured isolation postures: [`backend/ISOLATION-POSTURES.md`](../../backend/ISOLATION-POSTURES.md).

## Why

| | Per-attempt (current executor) | Per-tenant (this prototype) |
|---|---|---|
| Cross-tenant isolation | full | full (different workers never share an isolate) |
| Within-tenant isolation | full | **none** (attempts share builtins/modules/env/fs) |
| Billed creation | 1 Dynamic Worker **per attempt** | 1 per tenant **per day** |
| Attempt latency | ~2.1 s cold every time | ~2.1 s once, then **~2 ms warm** |

The creation meter dominates cost at volume (`LOADER.load` with no id bills one worker per
invocation), so per-tenant trades attempt purity for roughly two orders of magnitude less
creation cost and no per-attempt boot.

## Files

| File | Purpose |
|---|---|
| `tenant-registry.mjs` | deterministic `tenant id -> worker id` (`tenant-py-<sha256[:32]>`); reused across code changes because the script is constant |
| `generic-worker-code.mjs` | the CONSTANT Python script plus `genericWorkerCode()` and a digest; workflow source never appears here (path-keyed `_PathImporter` records storage paths) |
| `tenant-gateway.mjs` | in-supervisor module gateway: serves the attempt store pin-verified via `GET /api/sdk/modules/<path>` + `GET /api/sdk/modules-resolve` (mirrors `dev/executor`); holds the run token parent-side, callers present `DUMMY_CREDENTIAL` |
| `tenant-entry.mjs` | supervisor: capability + egress gates before any loader effect, gateway resolve/fetch preflight, per-tenant serialization chain, output budget, import audit/deny |
| `wrangler.tenant-local.jsonc` | local `workerd` config (LOADER binding only; the gateway is in-supervisor, no extra binding) |
| `*.test.mjs` | 27 unit tests plus a `workerd` integration suite |

## Run

```sh
cd spikes/cloudflare-pyodide/dev/tenant-workers
node --test tenant-registry.test.mjs generic-code.test.mjs tenant-gateway.test.mjs tenant-gates.test.mjs
npx wrangler dev --config wrangler.tenant-local.jsonc --port 8811 --ip 127.0.0.1 &
node tenant-isolation.test.mjs
```

## Proven locally (workerd)

- Cross-tenant: tenant A's planted `builtins`/`sys.modules` markers are invisible to tenant B.
- Within-tenant residue is **expected and documented**: same-tenant read-after-plant sees the
  marker. Mutually distrusting workloads need separate tenants.
- Serialization: two concurrent same-tenant attempts do not interleave (`queue_wait_ms` records
  the wait); two different tenants run in parallel (518 ms wall for 2 × 500 ms work).
- Cold vs warm for one tenant: **2617–3180 ms cold / 11 ms warm** (`worker_reused` flag proves reuse).
- Gates still reject before effects: capability (missing path), egress (unlisted host),
  undeclared-import terminal naming storage paths, and the child holds no
  credentials. Capability rejection provably precedes any gateway module fetch
  (`api_calls` is empty on the 422).

## Proven nothing (explicit)

Cloud deploy/billing, token refresh through the gateway, CPU termination, and adversarial
containment beyond the canary surface are **not** established here. All timings are local
`workerd`, not cloud-metered.

## Known prototype gap

**Closed (this change):** path-level attribution. The name-keyed data map is
gone: module source is PATH-keyed (`workflows/x.py`), served to the child only
as pin-verified `TenantGateway` responses to `/api/sdk/modules/<path>` after
`/api/sdk/modules-resolve` resolves the entry — the same two endpoints
`dev/executor` uses. The child's `_PathImporter` records REAL storage paths,
so the production `reconcileImports`/`deniedTerminal` audit reconciles paths
and the undeclared-import terminal names paths. The workerd suite proves: (a)
a declared path that loads appears in recorded paths and reconciles clean; (b)
an undeclared-but-loadable path maps to the terminal with paths named; (c)
capability rejection happens before any gateway module fetch (`api_calls: []`
on the 422 plus loader untouched); (d) cross-tenant isolation unchanged; (e)
same-tenant residue unchanged/documented. The generic script stays constant
(no per-tenant bake) and the child stays credential-free (gateway injects the
run token parent-side; the child presents only `DUMMY_CREDENTIAL`).

**Remains:** the gateway serves the attempt-supplied store, not Redis/S3 via a
real API (no upstream `api/` changes in this prototype); cloud deploy/billing,
token refresh through the gateway, CPU termination, rotation of tenant ids on
engine-code change, and adversarial containment beyond the canary surface are
**not** established here. All timings are local `workerd`, not cloud-metered.
