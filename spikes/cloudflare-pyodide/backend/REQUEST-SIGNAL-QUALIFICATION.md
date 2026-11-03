# Incoming cancellation delivery is a separate acceptance gate

Current documentation exposed a missing distinction in the historical abort
experiments. With compatibility date 2026-10-30, incoming client disconnection
events require enable_request_signal. Passing a Request object alone is also
distinct from explicitly supplying RequestInit.signal to the downstream fetch.
See the official [Request API](https://developers.cloudflare.com/workers/runtime-apis/request/)
and [November 2 changelog](https://developers.cloudflare.com/changelog/post/2026-11-02-request-signal-default/).
The announced default date is November 17, not our current date. This does not
change the independently proved deployed quota-exception continuation result.

Hypothesis: qualify incoming disconnect delivery and explicit downstream signal
forwarding before attributing later CPU effects to a failed child cancellation.
New fixed local fixtures retain an independent native parent_abort_received
event, separate from the native client's abort_requested timestamp. The child
can report only before/caught/after through its restricted effect capability;
it cannot forge a parent receipt. No Bifrost credentials or arbitrary code.

## Actual guest observations

Command: `timeout 300 python3 /opt/bifrost-tests/pyodide-ddf316c1/inbox/request-signal-v1/ct128-request-signal.py`
via existing pve-t340 pct exec access. The helper serially ran cf dev with the
flag absent and then present in the task-owned local configuration. It explicitly
passes `{signal: request.signal}` as fetch's second argument. No deployed
configuration or account resource was changed.

| Configured local flag | Completion | I/O abort | Catch-capable CPU abort | Parent disconnect receipt |
| --- | --- | --- | --- | --- |
| Absent | Python ABI and before/after pass | Later after effect | Later after effect | None observed |
| enable_request_signal | Same control passes | Later after effect | Later after effect | None observed |

The external Node client received AbortError for the abort cases, but cf dev's
HTTP logs show the Worker requests completed 200. The parent signal listener
emitted no independent receipt. Thus **incoming_abort_not_observed**, not a
qualified executor cancellation failure. The current local cf dev/proxy path did
not supply the required delivery positive control. Source config and environment
requested the flag; no generated runtime flag readback was available after cf
dev exited. Do not infer that the Python child received an abort from these data.

Both actual runtimes reported Python 3.14.2 / Pyodide 314.0.6 /
cpython-314-wasm32-emscripten. Each local case used fresh LOADER.load and
globalOutbound=null. The CPU loop is finite, 20 million additions, with a
BaseException catch. The native observer retains every case across later cases,
records monotonic times and bounds events per case; after each request it waited
2.5 seconds. Absence in that interval cannot certify termination. Parent recording
uses waitUntil only to retain its diagnostic callback after client disconnection.

Disabled/enabled supervisors completed in 20.332/20.113 seconds. CT128 stayed
2 CPU / 8 GiB, no swap. One container at a time: one CPU, 1 GiB / no additional
swap, 256 PIDs, network=none, 45-second readiness and 90-second driver bounds,
5 GiB free-disk guard. Both containers stopped, no OOM, no volume deletion. The
existing dev stack remained running. No cloud invocation, deployment, spending,
credential, grant, security policy or production effect. Wrangler's Request.cf
metadata DNS lookup failed under network=none; this expected local placeholder
warning was retained in the runtime logs rather than routed around the boundary.

The existing publisher-source bundle/cache wrapper and dependency image hashes
were verified before each run. Native commands use cf 1.0.0-beta.26 and existing
Wrangler 4.156.0. Source base cceccf3abe81af72820991a07fd00f7263fd455a plus the
exact runtime packet hashes in results/request-signal/{disabled,enabled}.json.
All six runtime/config files match current source. The packet's original two-test
unit file is preserved as original-test.txt; three classifier tests were added
after runtime observation, in a separate offline module, without changing the
executed runtime module. Twelve selected offline tests and helper Ruff passed.

```sh
node --test spikes/cloudflare-pyodide/backend/request-signal.test.mjs spikes/cloudflare-pyodide/backend/termination-evidence.test.mjs spikes/cloudflare-pyodide/backend/timed-termination.test.mjs
node spikes/cloudflare-pyodide/backend/verify-request-signal.mjs
```

## Consequence and next experiment

Historical raw abort receipts remain intact. An after effect relative to the
client abort timestamp establishes continuation after client disconnection; it
does not establish that a forwarded signal or RPC execution-context cancellation
was actually delivered. Earlier descriptions of those local results as physical
stop counterexamples must be read with this limitation. The separate deployed
CpuLimitExceeded/caught/after result remains a valid quota-termination
counterexample, because that interruption was directly caught by Python.

Next: a separately bounded guarded provider experiment should first prove a
parent-only JavaScript incoming-disconnect positive control, with the explicit
flag on the isolated test Worker, then I/O and noncooperative catch-capable CPU
cases with the same independent parent and child API log receipts. Keep tokens
live through the observation, then revoke/clean up. If incoming delivery is not
observed, stop and classify the transport gap; do not call it an executor stop
test. A parent signal receipt plus absent effects still does not itself prove
physical quiescence. Canonical result fencing and cleanup remain separate gates.
The existing public executor stays closed and untrusted admission disabled.
