# Deployed CPU limit placement: continuation after quota exception

**Both documented CPU limit placements fail to provide trusted physical
termination for arbitrary Python.** This deployed Cloudflare Python Worker
counterexample uses independently recorded BiFrost API effects. No execution
admission or production routing was enabled. First-class untrusted backend
acceptance remains incomplete.

Cloudflare documents WorkerCode limits and additional
`getEntrypoint(null, {limits: {cpuMs, subRequests}})` limits, using the lower of
the two: https://developers.cloudflare.com/dynamic-workers/usage/limits/.
The documented quota exception is not an uncatchable isolate stop.

## Experiment

Hypothesis: the additional entrypoint limit might prevent the continuation
observed historically. The fixed finite Python diagnostic catches BaseException
around a 200-million-iteration loop, then sequentially calls caught and after on
its parent effect capability. The parent sends only fixed tags to the existing
`/api/sdk/runtime/logs` route using an actual protocol-2 run-scoped identity.
The child receives no JWT, endpoint override, or general egress; globalOutbound
is null. This diagnostic does not claim upstream engine or canonical dispatch
acceptance.

| Fresh child | Result | Independently read API events | Native round trip |
| --- | --- | --- | --- |
| Completion control | completed=true | before, after | 1,828.972 ms |
| WorkerCode cpuMs=1000 | caught=CpuLimitExceeded | before, caught, after | 4,421.730 ms |
| Same code plus entrypoint cpuMs=1000 | caught=CpuLimitExceeded | before, caught, after | 4,330.556 ms |

The native CT128 controller read each execution's log stream through the actual
internal API after the cloud response and a one-second observation interval.
Each stream also has separate controller-control and parent-preflight messages.
These are actual authorized effects after the exception, not child-reported
effects or a transient Worker Map. The classifier conservatively labels them
effect_after_caught_exception; the fixed result separately identifies the type.
No absence of later effects could certify cessation.

All children reported Python 3.14.2, Pyodide 314.0.6 and SOABI
cpython-314-wasm32-emscripten. Every invocation uses fresh LOADER.load, never get.
Code and entrypoint budgets remain 1,000 ms / four subrequests; parent CPU stays
1,000 ms. No quota, memory, runtime security, observability or access setting was
increased. Round trips include startup/network/API overhead; cloud CPU, peak
memory and billed cost remain unknown.

## Source, bounds and cleanup

Base 0b0b9440acf4575694c10cec03c8e9733cc2a60c plus the eight-file overlay manifest
in results/cpu-placement-cloud/source.json. The existing approved account and
existing bifrost-pyodide-spike-ddf316c1 Worker were used; no new service or queue.
Open version cd139623-a0a5-4374-b70f-176058397f50, created
2026-11-03T01:21:51.518592Z. Its provider-read-back 6,720-byte entry module has
SHA256 c4879ec8ad44ccfbf37e8ba0ab2bcbe333921e13c7f7deaa24e6d6318a691a99,
identical to the local cf build. The module is retained as deployed-entry.txt;
open-version.json contains metadata/hashes without secret values. Gzip upload
was approximately 2.46 KiB; this diagnostic excludes the full engine bundle.

Account actions used cf CLI 1.0.0-beta.22 and its existing Wrangler 4.156.0 build
backend. Dry-run failures required declaring Wrangler and resolving its existing
installation; a local junction to the same-version executor node_modules fixed
discovery without installation or upgrade. An unsupported version-readback
option was corrected to positional version ID / --worker-id. No denied access
or uncertain cloud invocation was retried.

One fresh temporary guard window, maximum 30 minutes, expiry 1793670698143,
incremental cap $0.10. Five POSTs were reserved with exclusive locking/fsync
before sending: unsupported dispatch, invalid identity, then the three cases.
Only the final three created Python children. One additional GET verified public
closure. No automatic retries or old window/ledger reuse. The CT128 controller
completed in 16.848 s using one CPU, 768 MiB / no additional swap, 128 PIDs and
the existing isolated dev network. CT128 stayed 2 CPU / 8 GiB; the exact image and
command are in control.json. Heavy laptop Docker remained off.

Unsupported dispatch returned 422 before effects; invalid identity returned
observer-auth/401 before child creation. Cross-tenant log read returned 403.
Three synthetic runs and streams were removed; all access and refresh probes
returned 401 afterward. Independent SQL for the exact execution UUIDs returned
count 0; cleanup.sql preserves the query. Existing dev volumes were preserved.

Immediately after the controller returned, cf deploy closed workers.dev and set
expiry zero, then cf CLI deleted DEV_AUTH. Final version
93fba79d-254a-4111-a8fb-f0e7cd921fbc, deployment
cdb1261c-65c8-490d-adca-4b92c92a00f2. Provider readback shows expiry zero, no URLs,
and no DEV_AUTH binding; the secret list is empty. Retired-guard GET returned
404. No controller remains running. Profiles/receipts are retired and exhausted.

The DEV_API binding's provider environment label production denotes the default
Cloudflare service environment of the isolated dev Worker; its target is only
bifrost-pyodide-dev-ui-ddf316c1, not production BiFrost.

## Checks and remaining boundary

```sh
node --test spikes/cloudflare-pyodide/dev/cpu-placement/policy.test.mjs spikes/cloudflare-pyodide/backend/cpu-placement.test.mjs spikes/cloudflare-pyodide/backend/termination-evidence.test.mjs spikes/cloudflare-pyodide/backend/termination-observer.test.mjs spikes/cloudflare-pyodide/backend/timed-termination.test.mjs
node spikes/cloudflare-pyodide/dev/cpu-placement/verify.mjs
```

22 offline tests passed; both Python files passed Ruff in the pinned,
network-disabled guest image. Verifier success means the counterexample and
cleanup evidence match; it explicitly emits untrusted_workload_isolation_accepted=false. Broader
suites were not rerun: no platform engine, scheduler, auth, SDK, DB model or
frontend changed.

Guest command: `timeout 210 python3 /opt/bifrost-tests/pyodide-ddf316c1/dev-deploy-2037ac0/cpu-placement/run_guest.py`
via existing pve-t340 pct exec access. Reproduction requires a new exclusive
approved bounded window; never rerun the exhausted v1 profile. The config
defaults closed. Controller source, guard tests, raw report, ledger, exact
deployment source and cleanup evidence are retained.

The physical runtime boundary still needs a trusted stop/quiescence guarantee
independent of Python exception handling, alongside canonical late-result
fencing and cleanup. Neither tested quota placement supplies it. Logical cancel,
credential revocation, stale-result rejection and killing the external harness
cannot substitute for physical stop.

Next: express the unsupported capability at canonical executor admission and
continue local protocol conformance without enabling this backend. A supported
uncatchable provider stop primitive would need independent deployed acceptance.
Repeating the quota probe or weakening the contract will not close this gap.
Trusted fixed I/O workflows remain a separate qualified subset; the first-class
arbitrary untrusted target remains RED.
