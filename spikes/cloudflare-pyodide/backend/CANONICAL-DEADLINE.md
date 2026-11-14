# Canonical cutoff and response uncertainty

Follow-on: [retained result reconciliation across the original cutoff](DEADLINE-RETAINED.md)
now passes locally, including owner rotation, lost ACK replay and no relaunch.
The uncertain-only experiment below remains preserved against its tested source.

Canonical-mode companion admission now requires the upstream workflow_deadline
and preserves that absolute cutoff. It no longer grants a fresh timeout_seconds
window on receipt. Actual guest engine/SDK execution passes with the field.
The new live negative control confirms that a workflow credential can still
authorize a native API write after this cutoff while its canonical run is live.
The capability gate and canonical recovery remain necessary separate controls.

## Existing upstream contract

At the checked-out source, `RemoteProcessPool.route_execution()` stamps
workflow_deadline from its UTC started_at plus timeout before the authenticated
transport request. `ProcessPool._dispatch_to_child()` does the equivalent for
Linux. Redis context writing precedes this stamp; do not assume rereading the
context alone preserves the original cutoff. Reconciliation must retain the
original started_at/timeout/deadline rather than granting another window.

`authenticate_runtime_token()` checks epoch and run liveness on the primary.
`read_authority_state()` uses Execution.status for workflow liveness; its `now`
comparison applies to service-attempt leases. A workflow deadline is not an
automatic credential wall-clock revocation. No platform auth code was changed.

The local fixture now stamps deadline using its existing ExecutionInfo start
and timeout, matching that dispatch metadata. Its LocalPool remains a trusted
fixture, not signed remote control or negotiated opaque-handle support.

The companion accepts explicit UTC ISO strings (including upstream microseconds,
conservatively truncated to milliseconds), refuses malformed/expired/extended
cutoffs before creating API/runtime capabilities, and uses the supplied cutoff
for canonical egress. The fixed fixture requires its cutoff within the admitted
duration on the parent clock; production clock relationships still need
qualification. Direct API fixtures retain their separate local duration cap;
missing canonical metadata never falls back to that path.

## Actual negative qualification

Command in assigned CT128:

```sh
timeout 430 python3 /opt/bifrost-tests/pyodide-ddf316c1/inbox/deadline-response-loss-v1/ct128-canonical-failure.py --deadline-response-loss
```

One bounded run, exit 0, supervisor 94.071 s:

1. Genuine canonical claim and run-scoped credential execute the packaged real
   upstream engine/SDK. Its table upsert succeeds once; independent API read is 70.
2. The native proxy receives the completed provider result, retains no credential
   material in the report, and waits past the original absolute deadline.
3. The **trusted native observer deliberately bypasses the child egress gate**,
   writes a separate synthetic `late-proof` document with the same run credential,
   and independently reads value 71. Both API requests succeed. This is not a
   demonstrated child escape or a new SDK compatibility claim for the late write.
4. The proxy discards the completed provider response. Collection raises the
   actual RemoteProtocolError; the canonical row remains Running with no result
   or fabricated failure/success, and launch count is one.
5. Existing fixture cleanup removes its run/worker/claims and the credential
   subsequently receives 401. This is fixture cleanup, **not accepted timeout
   reporting or production recovery**. The underlying tests intentionally reject
   success acceptance on a lost response.

The qualification result is a verified boundary/counterexample, not successful
executor acceptance. It does not test a server committing an SDK write that was
still in flight at the cutoff; the original SDK effect completed before expiry,
while its provider result remained uncertain. The prior deterministic unit
counterexample for an in-flight commit remains separate.

## Provenance, resources and reproduction

Source base b6366ae2444f210f8aab319675e001e57e2464f9 plus exact root/project packet
hashes in `results/deadline-response-loss/v1.json`. API source remains
1d33150eb376b71f8a81b427920eda1b0447f829 with previously qualified owned overlays;
packaged engine f49d4ba99cebd24aa497f1244833b940e640885f and runtime closure
bc4eae48a21ab788f99bf3a554ca16f676a93549ba96bcdb99f433ed46e6daf0 are unchanged.
The new admission/capability, canonical fixture, response-loss probe and runner
hashes are checked against current source by the verifier. Earlier verifiers
retain their exact historical source pins rather than rewriting receipts.

Existing CT128 was rechecked: bifrost-pyodide-test-01, 2 CPU / 8 GiB / no swap,
about 6.5 GiB memory available and 15 GiB free disk. Provider container one CPU /
2 GiB / no extra swap / 128 PIDs, existing isolated test network; guarded Compose
and runtime/image/cache pins unchanged. No OOM. Owned tests stopped afterward;
volumes and dev stack preserved. cf CLI beta.26 delegated local execution to
Wrangler 4.156.0 and cached workerd. No runtime package/ABI change or compilation
was needed; the previously qualified Python 3.14.2/Pyodide 314.0.6 closure is used.
No new peak-memory, cloud CPU or cost measurement is claimed.

```sh
node --test spikes/cloudflare-pyodide/backend/attempt-egress.test.mjs spikes/cloudflare-pyodide/backend/fixture-admission.test.mjs spikes/cloudflare-pyodide/backend/refresh-gate.test.mjs
node spikes/cloudflare-pyodide/backend/verify-deadline-response-loss.mjs
node spikes/cloudflare-pyodide/backend/verify-egress-gate.mjs
node spikes/cloudflare-pyodide/backend/verify-canonical-failure-repaired.mjs
```

30 focused tests pass; guest Ruff passes for the three changed Python fixtures.
Full platform/UI suites were not rerun. No cloud invocation/deployment, new
credential/grant, security setting, spending, resource expansion or production
effect. No laptop Docker. Untrusted admission stays disabled.

Next: reconcile the retained uncertain result across the original cutoff using
the existing canonical ownership/terminal path, with no redispatch or renewed
deadline. Preserve the separate resource obligation: this result-loss experiment
does not qualify cancellation or provider CPU enforcement.
