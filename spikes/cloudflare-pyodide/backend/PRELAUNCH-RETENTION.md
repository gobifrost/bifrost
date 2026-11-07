# Known pre-launch refusal and canonical retirement

The physical-handoff fence now rejects stale/lost owners before deadline setup.
The next exact cleanup boundary is covered by
`test_prelaunch_refusal_keeps_failed_receipt_until_canonical_reconciliation`.
It drives actual ThinRemoteProcessPool routing with mocked authority/network
boundaries, not a real database or Worker invocation.

Observed on CT128: initial parent authority succeeds, physical authority rejects;
the request sends zero HTTP POSTs, starts no deadline/collection task, and releases
the native route (busy0) through upstream's Redis cleanup path. The original
CredentialRouteChanged survives on route, collection receipt and delivery receipt.
A duplicate reuses that refusal without invoking a new child. No terminal callback
was called. The companion journal therefore retains an unacknowledged failure and
refuses retirement. This is fail-closed evidence, not completed cleanup or a
first-class executor acceptance result.

Command in cached, network-disabled .5CPU/1GiB/64PID image:

```sh
python -m unittest -v test_private_http_pool.PrivatePoolTests.test_prelaunch_refusal_keeps_failed_receipt_until_canonical_reconciliation
```

Exit0, one test,0.009s. Ruff/Pyright pass for the changed test file. No engine,
adapter or upstream behavior changed in this increment; the preceding real
engine/SDK control remains the evidence for valid dispatch. No baseline rerun or
cloud call was needed. Guest packet:
`/opt/bifrost-tests/pyodide-ddf316c1/inbox/prelaunch-retention-v1`.

## Proposed shared contract, not an existing API

1. Keep known refusal distinct from uncertain dispatch. Zero sends is trusted
   physical-adapter evidence; a generic exception, disconnect or timeout cannot
   supply it.
2. The canonical owner must consume a refusal through upstream's setup-failure
   or recovery semantics, including attempt/owner fencing and credential/accounting
   obligations. A local journal must not decide durable business-job status.
3. Retire the local refusal receipt only after that canonical acknowledgment.
   Stale ownership, failed acknowledgment or uncertain dispatch retains evidence;
   no automatic rerun, fabricated result or forced tombstone deletion.
4. Do not confuse this with physical cancellation: no child was invoked here.

Implementation needs agreement on the shared upstream acknowledgment/recovery
boundary. Current companion lacks this explicit no-launch reconciliation route.
No shared `remote_pool`, placement, host-admission or Containers-owner file was
edited. Startup and public/untrusted admission remain gated.

## Coordination receipt

Existing passive caller was queried for capability, then a single question was
submitted to Eddie about shared ownership and an existing refusal/retirement
contract. Request3495c4ae-4324-4b3a-bd74-df7b7a252bd7 returned
`source_run_not_active`, `safe_to_resend:false`, backend0.11.0. No automatic retry,
alternate caller/host/credential or new thread was used. This is a denied passive
exchange, not an execution approval denial and not a completed handoff.

Required prerequisite: reviewer reconciliation of the existing caller/native-run
receipt, then shared-interface ownership evidence through that same approved
route. It does not require a new credential, grant or production change.

Follow-on: [setup failure acknowledgement probe](SETUP-REFUSAL-CONTRACT.md)
reproduces accepted/refused persistence both returning normally and publishing
failure. Generic setup completion cannot yet serve as the companion refusal ACK.

Follow-on REFUSAL-ACK.md implements a private explicit no-send acknowledgement
and retirement path, with canonical consumption/owner checks. It does not change
this earlier retained-failure observation or create a public upstream ACK API.
