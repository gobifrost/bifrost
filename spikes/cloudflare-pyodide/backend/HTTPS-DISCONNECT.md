# Local HTTP body-cancellation qualification failed

Prototype 0.1.0. Actual client response-body cancellation did not deliver the
stream cleanup needed to close the parent HTTPS capability before a follow-up
effect. This remains a cancellation blocker. Passing direct stream unit tests
do not waive it. No deployed behavior or delivered Python abort is inferred.

The fixed fixture reuses invocationStream and createApprovedHttpsCandidate. A
fresh Python child performs before/hold/after HTTP requests through the gateway.
An independent Node loopback destination holds the second request. The normal
control releases it, observes all200, consumes the terminal frame and EOF, and
observes cleanup. The cancellation case reads the accepted frame, waits until
the destination receives hold, then calls the actual Node HTTP response body's
reader.cancel(). This is neither a direct source-level stream cancellation nor
an injected SDK response.

## Preserved failure

v7 fails the bounded 15-second parent-gate-close condition. Its supervisor
retains hashes and cleanup, but the driver did not retain structured failure
observations. v8 changes evidence capture only, keeps the same assertion/bound,
and also exits1. It records:

* Control while held: closed=false, execute_returned=false. After release:
  terminal statuses[200,200,200], EOF, closed=true and execute_returned=true.
* Cancellation: reader.cancel() returned; hold received but not committed;
  parent remained closed=false/execute_returned=false throughout the bound.
* Failure cleanup releases the independent destination. Within500ms, it records
  hold commit and /after; parent then reports closed=true/execute_returned=true.
  The canceled caller did not collect a terminal/EOF receipt.

Thus this cf dev/workerd HTTP/proxy path did not reliably deliver incoming body
cancellation to the gate cleanup callback. It does not prove Python received or
ignored an abort, infinite execution, or deployed Cloudflare behavior. The
separate REQUEST-SIGNAL-QUALIFICATION.md already preserves absent incoming-abort
receipts with both local flag settings. No flag was changed and that old abort
experiment was not repeated here. Runtime logs contain Network connection lost;
that message alone is not a delivered parent/child cancellation receipt.

This is a runtime event-delivery/transport boundary, not a missing package,
PyEmscripten wheel or compilable extension. No package build can supply the
missing incoming event contract. The direct stream cancellation test still
passes and has narrower scope.

## Reproduction and scope

Existing CT128 2CPU/8GiB allocation, one provider at a time,1CPU/2GiB/128PIDs,
network disabled and loopback-only destination. Pinned image33cd92be, workerd
bundle3c3fd5a4 and wrapper9f9ad4a4 are checked by the unchanged supervisor;
cf CLI1.0.0-beta.26/ Wrangler4.156.0. No new ABI measurement is claimed.

Use approved-https-fixture configs plus the candidate/deadline and actual
invocation-stream modules. Stage https-disconnect-entry.mjs as
project/backend/approved-https-entry.mjs, https-disconnect-probe.mjs as
project/backend/approved-https-probe.mjs, then reuse the original supervisor:

```sh
timeout -k 5 120 python3 /opt/bifrost-tests/pyodide-ddf316c1/inbox/approved-https-v8/ct128-sdk-probe.py --approved-https --revision 8
# Current qualification exits1. The verifier below verifies failure facts only:
node spikes/cloudflare-pyodide/backend/verify-https-disconnect.mjs
```

Whole supervisor lifecycles: v7 26.533548712730408seconds, v8
28.21137470845133seconds; neither is startup latency. Both providers stopped,
no OOM, volumes and separate dev stack preserved. Receipts, v8 observation and
v7 driver snapshot are in results/https-disconnect. 14 focused Node component/
policy/deadline tests pass; they do not override this failed real qualification.
No Python source changed, so no new native Python lint/type claim is added.

No BiFrost credentials, real engine/SDK execution, canonical recovery, cloud
invocation, new credentials/grants, runtime/security changes or spending.

Next: prepare explicit owner-fenced capability revocation through the existing
control contract rather than relying on disconnect. Never acknowledge physical
termination or release uncertain capacity because a client canceled its body.
The tested canonical deadline gate remains separate. Public/untrusted admission
stays disabled.
