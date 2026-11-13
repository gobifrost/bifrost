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
| `generic-worker-code.mjs` | the CONSTANT Python script plus `genericWorkerCode()` and a digest; workflow source never appears here |
| `tenant-entry.mjs` | supervisor: capability + egress gates before any loader effect, per-tenant serialization chain, output budget, import audit/deny |
| `wrangler.tenant-local.jsonc` | local `workerd` config |
| `*.test.mjs` | 17 unit tests plus a `workerd` integration suite |

## Run

```sh
cd spikes/cloudflare-pyodide/dev/tenant-workers
node --test tenant-registry.test.mjs generic-code.test.mjs tenant-gates.test.mjs
npx wrangler dev --config wrangler.tenant-local.jsonc --port 8811 --ip 127.0.0.1 &
node tenant-isolation.test.mjs
```

## Proven locally (workerd)

- Cross-tenant: tenant A's planted `builtins`/`sys.modules` markers are invisible to tenant B.
- Within-tenant residue is **expected and documented**: same-tenant read-after-plant sees the
  marker. Mutually distrusting workloads need separate tenants.
- Serialization: two concurrent same-tenant attempts do not interleave (`queue_wait_ms` records
  the wait); two different tenants run in parallel (518 ms wall for 2 × 500 ms work).
- Cold vs warm for one tenant: **2363 ms cold / 9 ms warm** (`worker_reused` flag proves reuse).
- Gates still reject before effects: capability (missing module), egress (unlisted host),
  undeclared-import terminal, and the child holds no credentials.

## Proven nothing (explicit)

Cloud deploy/billing, token refresh through the gateway, CPU termination, and adversarial
containment beyond the canary surface are **not** established here. All timings are local
`workerd`, not cloud-metered.

## Known prototype gap

The data importer records **module names** while the production import audit reconciles
**storage paths** (`workflows/x.py`). The prototype therefore proves the gate ordering and the
terminal mapping, not path-level attribution fidelity. Production must feed the gateway-fetched
module source with its real paths (as `dev/executor` does) rather than a name-keyed data map.
