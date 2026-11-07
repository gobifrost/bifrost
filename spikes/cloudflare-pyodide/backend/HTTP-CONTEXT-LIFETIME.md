# Local in-flight HTTP cancellation has an observable connection close

The request-owned forwarding fixture now uses actual Python pyfetch, an allowlisted
egress service, and a Node HTTP observer on container loopback. No in-memory gate
promise crosses Worker request contexts. The observer receives and holds the HTTP
response before the calling Worker is allowed to return.

## Measured v2 ordering

* Awaited control: release, started, gate, after, response EOF; value 7 and tail ok.
* Retained waitUntil: started, gate, EOF; one pending connection, no tail before
  release. Release then produces after, clears the connection, and tail is ok.
* Early response: started, gate, EOF, gate_connection_closed. At the pre-release
  snapshot the gate is still closed but the connection has already closed and
  pending connections are zero. Subsequent release produces no after within the
  explicit 500 ms observation window; caller tail is ok.

This is positive evidence of local cancellation of the tested in-flight network
operation, combined with a bounded absence of its downstream effect. It is not
proof that CPU execution stopped, that no future effect is possible, that an
isolate was destroyed, or that deployed Cloudflare behaves identically. Resource
timing fields remain zero and are not CPU measurements. The Linux container is
stopped only after the observations, so killing the harness is not the signal.

v1 is retained: it waited for started rather than gate receipt, and the early
response case never issued gate. It is a valid between-operation observation but
does not qualify in-flight HTTP cancellation. v2 changes that causal barrier;
neither result is silently reclassified or rerun until green.

## Scope and reproduction

`ct128-sdk-probe.py --rpc-http-context --revision 2`, under `timeout -k 5 120`,
starts the existing cached cf CLI in CT128 and executes `http-context-probe.mjs`
inside its owned provider. The observer accepts only three synthetic UUIDs and
100 requests on 127.0.0.1:8820; outer fetch is bounded to 45 seconds and closes all
observer connections in finally. Worker observer fetches have 10-second bounds.
The fixture deliberately omits awaiting one RPC as a negative control, never as
the production execution pattern. Normal work must be awaited and responses
consumed; waitUntil is only bounded invocation retention.

Same CT128 2 CPU/8 GiB/no swap, provider 1 CPU/2 GiB/128 PIDs/network none,
cached cf 1.0.0-beta.26, workerd 1.20261023.1 and integrity-pinned runtime bundle
3c3fd5a4.... No new packages or dependency substitution. Python ABI is inherited
from the existing qualified cache, not remeasured by this fixture. This test does
not execute the upstream SDK/engine; their receipt fields remain false.

Receipts, exact v1 sources and bounded v2 log are in `../results/http-context/`.
Run `node backend/verify-http-context.mjs` for source/receipt/order assertions.
Both owned containers are stopped; no volumes were removed. No cloud invocation,
deployment, credentials, spending, runtime-security flag or admission change.
Node syntax checks and all three focused lifecycle receipt verifiers pass. The
changed Python harness passes Ruff and Pyright in a network-disabled bounded
container on CT128. No broad platform suite was rerun for this fixture-only change.

The design follows the supported parent-forwarding and invocation lifecycle
boundaries in the official [loader API](https://developers.cloudflare.com/dynamic-workers/api-reference/)
and [RPC lifecycle](https://developers.cloudflare.com/workers/runtime-apis/rpc/lifecycle/).
Next independent gate is CPU/deadline enforcement with an external progress/effect
observer, then deployed qualification when authorized. Canonical upstream fencing
and credential revocation remain necessary even when network cancellation works.
