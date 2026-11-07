# Deployed incoming-disconnect qualification

The actual Cloudflare parent-only positive control now observes an incoming
disconnect with enable_request_signal, unlike the previous local cf dev path.
The Python I/O case also records parent_abort_received. The CPU case records
before, but neither parent_abort_received nor a later child effect in the
three-second window. **CPU physical termination is still unproved.** No general
workflow handler, canonical dispatch or untrusted execution admission was enabled.

The [official Request API](https://developers.cloudflare.com/workers/runtime-apis/request/)
requires the flag at compatibility date 2026-10-30. The signal is explicitly
passed in fetch's second argument, rather than relying on Request signal
passthrough. The flag was applied only for this bounded isolated experiment and
removed during cleanup; it is not enabled in production.

## Actual observations

| Case | Native client abort | Independently read BiFrost API messages after preflight | Interpretation |
| --- | --- | --- | --- |
| Parent JS completion | No | before, after | Positive callback/result control |
| Parent JS disconnect | 188.677 ms after request start | before, parent_abort_received | Incoming delivery gate passes before any Python case |
| Python I/O disconnect | 2,037.421 ms after request start | before, parent_abort_received | Incoming delivery observed; no later effect in window |
| Python catch-capable CPU disconnect | 1,450.186 ms after request start | before | Incoming receipt/physical stop remain unproved for this case |

Each abort was requested only after the native controller read the matching
before event from the actual internal API, then waited ten milliseconds. Native
HTTP task cancellation closes the request; it is not an executor stop receipt.
The controller kept all run identities live for three seconds afterward and read
the independent log streams. No repeated invocation on timeout/error occurred.
If the parent JS gate had failed, both Python cases would have been skipped.

The parent JS delayed for two seconds; Python I/O used the exact previously
qualified 250 ms sleep. The CPU case used a finite 20-million-addition loop with
a BaseException catch. No ABI result returned from either aborted Python request;
this experiment does not independently remeasure the managed Python ABI. Its
before events demonstrate actual Python execution, not just imports. The earlier
engine/SDK/ABI proofs remain separate artifacts.

The I/O and parent JS cases did not emit after, but this bounded absence cannot
establish that all execution ceased. The CPU case lacks even the parent abort
receipt, so its absence of effects must not become physical-stop acceptance.
The separately proved caught CpuLimitExceeded/after effects remain unchanged.

## Source, resources and bounds

Base 57ca6e57403ecaad80d99df372490d2b189876af plus the eight source hashes in
results/request-signal-cloud/source.json. The wrapper reuses the exact qualified
backend/request-signal-probe.mjs implementation. Children receive only a fixed
effect capability, globalOutbound=null, cpuMs=1000 and four subrequests. Parent
CPU remains 1000 ms. Run-scoped protocol-2 JWTs are minted by actual upstream
authorization in the isolated dev API and remain parent-only; no platform key
is passed to a child. Each case has a distinct synthetic tenant/run identity.

Eight guard/forwarding unit tests and both Python-file Ruff checks passed before
deploy. An initial test invocation used the wrong working-directory prefix and
found no files; it was corrected before the actual eight-test pass. cf deploy
--dry-run succeeded using the existing Wrangler dependency junction. No package
install or upgrade. Account operations used cf CLI 1.0.0-beta.22 and Wrangler
4.156.0; no alternate identity, access grant or production target.

Open version 2224a36e-d6fb-4147-b742-52aaa798b701. Provider version/module readback
confirmed enable_request_signal before controller invocation and retained the
actual deployed entrypoint. Its hash matches the local build. The generated
local config alone was not used as proof of provider configuration.

The exclusive fresh window expired at 1793673568937, at most 30 minutes, capped
at $0.10 incremental usage, six reserved POSTs and two fresh Python children.
The ledger records unsupported canonical request, invalid identity, completion,
parent abort, I/O abort, CPU abort. One additional retired-guard GET verifies
closure. Each reservation precedes sending and uncertain requests remain charged.
Actual billed cost, cloud CPU and peak memory are unavailable, not zero.

Controller command inside CT128:
`timeout 210 python3 /opt/bifrost-tests/pyodide-ddf316c1/dev-deploy-2037ac0/request-signal/run_guest.py`.
It completed in 18.902 seconds, one CPU / 768 MiB / no additional swap / 128 PIDs,
on the existing isolated dev network. CT128 stayed 2 CPU / 8 GiB with about
15 GiB free disk. Exact command/image are in control.json. No laptop Docker.

## Cleanup and remaining contract

Unsupported canonical request returned 422; invalid identity returned API 401
before a child could launch. Cross-tenant log read returned 403. Four synthetic
run log streams were removed; all four access and refresh probes returned 401
after fixture deletion. Independent SQL for the exact four execution IDs returned
zero rows. Only the pre-existing dev stack/tunnel remain; no controller remains.
Volumes were preserved.

Immediately after the controller returned, cf deploy restored the prior closed
configuration from dev/cpu-placement: expiry zero, workers.dev disabled, and no
temporary signal flag. DEV_AUTH was deleted. Final version
1ff3251d-6947-4f5e-a8bf-fd7c6fd7db19, deployment
f5c7b0e7-8084-46c5-a6cf-0a59fa305a70. Provider readback confirms restoration,
the secret list is empty, and retired-guard GET returned 404. The exhausted
private profile is marked retired and must not be reused.

```sh
node --test spikes/cloudflare-pyodide/dev/request-signal/policy.test.mjs spikes/cloudflare-pyodide/backend/request-signal.test.mjs
node spikes/cloudflare-pyodide/dev/request-signal/verify.mjs
```

Next useful experiment: the documented WorkerCode.tails loopback can provide
provider-generated request completion information without a new Tail Worker
resource: https://developers.cloudflare.com/dynamic-workers/api-reference/#tails.
Qualify its lifecycle semantics and correlate it to a fresh run before treating
any outcome as evidence of noncooperative work ceasing. A terminal callback alone
must not be relabeled physical quiescence. Canonical late-result fencing,
duplicate/reconnect semantics and cleanup remain separate requirements. Do not
enable the backend from this diagnostic result.
