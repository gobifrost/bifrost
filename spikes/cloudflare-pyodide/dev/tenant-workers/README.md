# Per-tenant persistent Python workers (prototype)

Pivot from **fresh isolate per attempt** (`LOADER.load` per attempt) to **one long-lived generic
worker per tenant** (`LOADER.get(tenantWorkerId, genericScript)`), with attempts per tenant
serialized. Decision record and cost model: [`backend/ADR-PER-TENANT.md`](../../backend/ADR-PER-TENANT.md).
Measured isolation postures: [`backend/ISOLATION-POSTURES.md`](../../backend/ISOLATION-POSTURES.md).
Cloud acceptance (deployed + metered): [`backend/CLOUD-TENANT-ACCEPTANCE.md`](../../backend/CLOUD-TENANT-ACCEPTANCE.md).

## Why

| | Per-attempt (current executor) | Per-tenant (this prototype) |
|---|---|---|
| Cross-tenant isolation | full | full (different workers never share an isolate) |
| Within-tenant isolation | full | **none** (attempts share builtins/modules/env/fs) |
| Billed creation | 1 Dynamic Worker **per attempt** | 1 per tenant per day (0 on reuse) |
| Attempt latency | ~2.1 s cold every time (local) | ~1.2 s once, then **~51 ms** (cloud round trip) |

The creation meter dominates cost at volume (`LOADER.load` with no id bills one worker per
invocation), so per-tenant trades attempt purity for roughly two orders of magnitude less
creation cost and no per-attempt boot. Both directions are **measured in the billed account**,
not modelled — see the meter table in the cloud acceptance doc.

## Files

| File | Purpose |
|---|---|
| `tenant-registry.mjs` | deterministic `tenant id -> worker id` (`tenant-py-<sha256[:32]>`); reused across code changes because the script is constant |
| `generic-worker-code.mjs` | the CONSTANT Python script plus `genericWorkerCode()` and a digest; workflow source never appears here (path-keyed `_PathImporter` records storage paths) |
| `tenant-gateway.mjs` | in-supervisor module gateway: serves the attempt store pin-verified via `GET /api/sdk/modules/<path>` + `GET /api/sdk/modules-resolve` (mirrors `dev/executor`); holds the run token parent-side, callers present `DUMMY_CREDENTIAL` |
| `tenant-entry.mjs` | supervisor: auth/expiry gate (cloud), capability + egress gates before any loader effect, gateway resolve/fetch preflight, per-tenant serialization chain, output budget, import audit/deny; `legacy_load` action is the per-attempt meter control |
| `wrangler.tenant-local.jsonc` | local `workerd` config (LOADER binding only; the gateway is in-supervisor, no extra binding) |
| `cloudflare.config.ts` / `package.json` | cloud config for `cf deploy` (`workersDev`, LOADER binding, expiry var) |
| `tenant-isolation.test.mjs` | local `workerd` integration suite |
| `cloud-acceptance.mjs` | deployed acceptance suite (auth, isolation, serialization, cold/warm, gates, path audit) |
| `meter-readback.mjs` / `meter-delta.mjs` | Dynamic Worker creation meter readback and controlled delta experiment |
| `*.test.mjs` | 28 unit tests |

## Run locally

```sh
cd spikes/cloudflare-pyodide/dev/tenant-workers
node --test tenant-registry.test.mjs generic-code.test.mjs tenant-gateway.test.mjs tenant-gates.test.mjs
npx wrangler dev --config wrangler.tenant-local.jsonc --port 8811 --ip 127.0.0.1 &
node tenant-isolation.test.mjs
```

## Run in cloud

```powershell
# one-time: create the auth secret (value never printed)
$path = "$env:TEMP\bifrost-pyodide-tenant-ddf316c1-secrets.json"
# ... write {"SPIKE_AUTH": "<32 random bytes, base64>"} to $path ...
cf deploy --secrets-file $path
node cloud-acceptance.mjs        # 20 invocations
node meter-readback.mjs          # account creation/request meter for today
node meter-delta.mjs --mode get --only reuse --tag <existing-tag>   # expect +0 creations
node meter-delta.mjs --mode load --tag <new-tag>                    # expect +count creations
```

The worker requires `X-Spike-Auth` plus an unexpired `SPIKE_EXPIRES_AT`; everything
else is rejected before any tenant work. Teardown: `cf workers delete bifrost-pyodide-tenant-ddf316c1`.

## Proven locally (workerd)

- Cross-tenant: tenant A's planted `builtins`/`sys.modules` markers are invisible to tenant B.
- Within-tenant residue is **expected and documented**: same-tenant read-after-plant sees the
  marker. Mutually distrusting workloads need separate tenants.
- Serialization: two concurrent same-tenant attempts do not interleave (`queue_wait_ms` records
  the wait); two different tenants run in parallel (525 ms wall for 2 × 500 ms work).
- Cold vs warm for one tenant: **2617–3401 ms cold / 11 ms warm** (`worker_reused` proves reuse).
- Gates still reject before effects: capability (missing path), egress (unlisted host),
  undeclared-import terminal naming storage paths, and the child holds no
  credentials. Capability rejection provably precedes any gateway module fetch
  (`api_calls` is empty on the 422).

## Proven in cloud (deployed + metered)

- 22/22 acceptance checks on the deployed worker, 20 invocations; cross-tenant PASS,
  within-tenant residue FAIL as predicted.
- Cold 1,247 ms / warm 53 ms; second run 1,152 / 51 ms (round trip, not guest CPU).
- Two concurrent same-tenant attempts serialized (second waited 1,413 ms including cold
  boot); two tenants ran in parallel (546 ms wall for 2 × 500 ms).
- Path-keyed audit end to end: `recorded: ["workflows/wf.py"]`, undeclared terminal names
  `workflows/helper.py`, capability reject with `api_calls: []`.
- Creation meter: stable ids **+0** workers for 3 invocations; `LOADER.load` **+3** for 3
  invocations, repeated **+3** again.

## Not established

Guest CPU-ms per workflow (parent-level only), memory accounting, token refresh through a
real gateway, physical termination, tenant-id rotation drill, and adversarial containment
beyond the canary surface. `ISOLATION-POSTURES.md` canary 11 remains unproven.

## Known prototype gap

**Closed:** path-level attribution. Module source is PATH-keyed (`workflows/x.py`), served to
the child only as pin-verified `TenantGateway` responses, and `_PathImporter` records REAL
storage paths for the production `reconcileImports`/`deniedTerminal` audit.

**Remains:** the gateway serves the attempt-supplied store, not Redis/S3 via a real API (no
upstream `api/` changes in this prototype). The run token is present parent-side rather than
minted by a real gateway, so token refresh and revocation timing are unproven.
