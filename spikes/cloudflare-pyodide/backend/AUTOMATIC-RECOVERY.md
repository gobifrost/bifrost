# Existing API recovery after real relay silence

The existing API-hosted dispatch reconciler automatically ends a silent static
executor and its claimed workflow. This now passes with the real opaque pool,
accepted stream, upstream engine/SDK and an interrupted local provider. The
fixture does not call end_runs_of_lost_worker or announce_lost_runs in this mode.
It starts no second reconciler and does not backdate heartbeats, alter clocks,
shorten upstream constants or create another recovery queue.

## Two different observed failures

| Condition after provider loss | Observation |
| --- | --- |
| Relay continues genuine heartbeat/owner renewal | Canonical execution stays Running for 46.269 s; relay stays ready. This is a negative control, not successful automatic provider-failure handling. |
| Fixture stops only its heartbeat/owner-renewal task | The already-running API control replica marks the worker lost/heartbeat_lost and the workflow Failed/ExecutorLost. Observed 30.182 s after task shutdown; PostgreSQL heartbeat age 33.586 s. |

The heartbeat-loss threshold is the unchanged 30 seconds. Its database
reachability grace and 15-second reconciliation sweep are also unchanged. The
negative control lasts longer than a full heartbeat-loss window plus a sweep.
Timing is one observed sample, not a latency guarantee or provider CPU measure.

The SDK write remains 70 and launch count remains one. A post-recovery duplicate
under the expired owner is rejected with CredentialRouteChanged, rather than
reused under stale authorization. Access and refresh return 401. The redacted
integration log is recovered once; a repeat drain flushes zero. No terminal
result was received or ACKed. The actual native invocation retains its original
identity/deadline and busy obligation after canonical failure. Logical loss,
revocation and worker retirement do not certify Cloudflare physical settlement.

The Linux provider container was deliberately stopped after an independent
authenticated SDK-document read: exited 143/PID 0/no OOM. That is local process
failure injection, not deployed Dynamic Worker per-isolate cancellation proof.
Owned test containers stop; volumes and the separate dev stack remain.

## Scope and reproducibility

This fixed qualification supplies the real claim, pool route/result paths and
API control service. It does not call ThinRemoteProcessPool.start or enable the
public experimental profile. The live-relay negative control characterizes this
adapter, not every fully supervised upstream Linux executor. Runtime negotiation,
startup/admission, per-invocation failure/deadline handling and deployed physical
enforcement/accounting remain incomplete.

```sh
timeout 430 python3 /opt/bifrost-tests/pyodide-ddf316c1/inbox/automatic-loss-v1/ct128-canonical-failure.py --automatic-loss
node spikes/cloudflare-pyodide/backend/verify-automatic-loss.mjs
```

CT128 remains 2 CPU/8 GiB/no swap. Same owned project bifrost-test-5dc8d94e;
provider 1 CPU/2 GiB/128 PIDs, 360-second internal bound. Uses the previously pinned
cf CLI/workerd/bundle/image and unchanged packaged upstream engine. No dependency
or native shared-source changes in this increment. All four runtime phases exit 0;
supervisor 139.114 s includes setup, deliberate observation windows and cleanup.

[Raw receipt](../results/automatic-loss/v1.json) records commands, packet and
workspace digests, native pool/protocol overlay hashes, and provider readback.
[Post-run control-source readback](../results/automatic-loss/control-source-readback.json)
matches all five API startup/control/liveness/recovery source files to exact
base 1d33150eb376b71f8a81b427920eda1b0447f829 Git objects. Container mount readback
confirms the API used the assigned checkout's /app/src bind mount.
Ruff and Pyright pass in network-disabled disposable checker containers, using
the already qualified 512 MiB/1 GiB respective bounds; no diagnostic suppression.

## Consequence for the execution contract

Executor health, canonical workflow outcome and physical provider settlement are
three separate facts. Reusing upstream worker-loss recovery is viable; treating
one lost invocation as a dead multi-invocation relay would end unrelated work.
The remaining adapter needs per-invocation uncertain-outcome/deadline handling
while preserving authorization, no-redispatch semantics and resource obligations.
See the updated [contract assessment](CONTRACT.md). This evidence authorizes no
cloud invocation, admission change, spending, credentials or production rollout.
