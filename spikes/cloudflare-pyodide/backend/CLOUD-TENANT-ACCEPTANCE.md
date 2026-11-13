# Cloud acceptance: per-tenant persistent Python workers

Measured 2026-11-13 against the billed Cloudflare account, spike branch
`t3/cloudflare-pyodide-workflow-spike`. This closes three of the five acceptance
items in `ADR-PER-TENANT.md` §6. It does **not** close the CPU-accounting or
rotation-drill items, and it does not upgrade any local-only claim.

## What was deployed

| | |
|---|---|
| Worker | `bifrost-pyodide-tenant-ddf316c1` (`*.midtown-technology-group.workers.dev`) |
| Versions | `a233223e-7d5e-4617-8894-14a6c52a1316` (first acceptance), `f1ea5952-46b1-4d8e-9799-cd424b591d95` (re-run + meter control) |
| Upload | 25.18 KiB / 7.20 KiB gzip, startup 1 ms |
| Bindings | `LOADER` (worker-loader), `SPIKE_EXPIRES_AT`, `SPIKE_AUTH` (secret) |
| Auth | `X-Spike-Auth` + unexpired window; every other request returns 403 before any tenant work |
| Child | constant generic Python script, `env: {}`, sha256 `3260cfa7…c99bbe6965` |

The supervisor is the same code the local `workerd` suite exercises
(`dev/tenant-workers/tenant-entry.mjs`); only the config differs
(`cloudflare.config.ts` instead of `wrangler.tenant-local.jsonc`) and the auth
branch replaces `LOCAL_PROBE`.

## Isolation and behaviour: 22/22 checks, 20 invocations

| Check | Result |
|---|---|
| `auth-required` | 403 without the header |
| `a-cross-tenant-builtins` | tenant B reads `null` after A plants |
| `a-cross-tenant-module` | A does not see B's `sys.modules` marker |
| `a-tenant-A-unaffected` | A still sees only its own marker |
| `a-tenant-worker-id-deterministic` | `tenant-py-95854314…` = sha256(tenant)[:32] |
| `b-same-tenant-residue-documented` | residue visible inside one tenant (documented limit) |
| `b-serialized-no-interleave` | two concurrent same-tenant attempts never overlap |
| `b-queue-wait-recorded` | second waiter queued 1,413 ms (includes cold boot) |
| `b-cross-tenant-parallel` | 546 ms wall for 2 × 500 ms across tenants |
| `c-warm-faster-than-cold` | cold 1,247 ms / warm 53 ms; second run 1,152 / 51 ms |
| `d-capability-rejects-before-effects` | 422, `missing_modules` names the path |
| `d-capability-before-any-module-fetch` | `api_calls: []` — no gateway fetch |
| `d-rejected-workflow-never-ran` | next check shows clean state |
| `d-egress-rejects-before-effects` | 422 `egress-rejected` |
| `d-undeclared-import-terminal` | 502, `undeclared_modules: ["workflows/helper.py"]` |
| `d-undeclared-terminal-names-paths` | paths, not bare module names |
| `d-declared-run-succeeds` | `{"ok": true}` |
| `d-declared-path-recorded-reconciles-clean` | `recorded: ["workflows/wf.py"]`, both gateway calls 200 |
| `d-child-holds-no-credentials` | no token-shaped env, dummy never lands in env |
| `code-digest-exposed` | constant script digest, tenant-independent |

Receipt: [`results/tenant-cloud-acceptance.json`](../results/tenant-cloud-acceptance.json).
Runner: [`dev/tenant-workers/cloud-acceptance.mjs`](../dev/tenant-workers/cloud-acceptance.mjs).

Cold-start is materially better in cloud than local `workerd`: ~1.2 s versus
~2.4–3.4 s. Warm attempts are ~51–53 ms round trip, not the ~2–11 ms local
figure — the difference is network + parent overhead, and neither number is a
guest CPU measurement.

## Creation-meter readback: the cost claim, measured

Meter: GraphQL `workersInvocationsByOwnerAndScriptGroups { uniq { distinctDynamicWorkerCount } }`
for the account, UTC day 2026-11-13. Analytics lags ~2–3 minutes, so every read
waits 150 s.

| Phase | Worker creations | Requests |
|---|---|---|
| Baseline before control | 25 | 51 |
| 3 × `get(stableId, code)` on **existing** ids | **+0** (25 → 25) | +3 |
| 3 × `load(code)` (per-attempt posture) | **+3** (25 → 28) | +3 |
| 3 more × `load(code)` | **+3** (28 → 31) | +3 |

Receipts: [`tenant-meter-get-reuse-meterget1.json`](../results/tenant-meter-get-reuse-meterget1.json),
[`tenant-meter-load-all-meterload1.json`](../results/tenant-meter-load-all-meterload1.json).

This is the rule the ADR's cost model rests on, now confirmed in the billed
account rather than modelled:

- stable `(tenant id, code)` pairs cost **0** creations on repeat invocation;
- `LOADER.load` with no id costs **1 creation per invocation**.

At the ADR's worked volume (20 tenants × 500 runs = 10,000 runs/month) that is
≈$18 of net creation charge for per-attempt versus $0 for per-tenant while ids
are stable.

Caveats, stated plainly:

- The absolute baseline (25) is account-level. A portion predates the controlled
  phases and is not fully attributed; only the deltas above are controlled.
- `distinctDynamicWorkerCount` counts `(id, code)`. Changing the constant
  generic script therefore books one new worker per existing tenant id — the
  rotation behaviour described in `ISOLATION-POSTURES.md` §"cached name".
- The acceptance runner mints a fresh tenant-id suffix per run, so each full
  suite re-run legitimately adds ~7 creations by design. Steady-state traffic on
  fixed ids adds none, as measured above.

## Still not established

1. **CPU-ms per workflow.** Creation, request, and warm/cold latency are
   measured; guest CPU is not. `time.process_time()` raises in Pyodide and the
   provider exposes CPU only at the parent-invocation level (single sample
   1,757 ms in `CPU-TELEMETRY.md`). The §3 CPU lines remain estimate-based.
2. **Memory.** Fixed 128 MiB, never billed, never measurable per execution.
3. **Rotation drill.** Not run. The generic script makes code updates
   id-stable, but a change to the script itself is a new `(id, code)` and must
   be measured as a rotation event.
4. **Token refresh, physical termination, and adversarial containment beyond
   the canary surface.** Unchanged from the local record.
5. **Multi-tenant parent.** All tenants shared one supervisor isolate; that is
   the intended design and the child isolation is per worker id. A parent
   compromise is out of scope and unchanged from the executor posture.

## Teardown

```powershell
cf workers delete bifrost-pyodide-tenant-ddf316c1
```

The deployed worker also fails closed for everything after
`SPIKE_EXPIRES_AT` (2026-11-13T09:50Z), so an undeleted worker goes inert
without a code change.
