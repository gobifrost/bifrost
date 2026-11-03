# Canonical cancellation request with effect revocation and natural completion

Prototype0.1.0, real local engine/SDK/API/PostgreSQL/Redis qualification. This
does **not** qualify physical cancellation or a Cancelled terminal outcome.

Upstream's authenticated POST /api/executions/{id}/cancel checks current caller
reach and ownership. A running execution becomes Cancelling, not Cancelled.
Cancelling is in LIVE_EXECUTION_STATUSES, so its run credential remains live.
The API publishes its existing Redis flag/event. Native Python-profile physical
termination remains refused. Separately, the authoritative result writer permits
natural Success while the claimed row is Running or Cancelling. These upstream
semantics are preserved; no companion writes a replacement business status.

## Actual experiment

Two independently scoped real upstream workflows execute SDK config/table
operations and controlled synthetic HTTPS. The target holds a vendor POST; the
peer waits independently. The live peer's genuine run credential cannot cancel
the target (403). After peer Success, its same ended credential gets401.

The target's own genuine run identity calls the actual cancellation endpoint
twice: both200/Cancelling. Direct SDK access remains200, as upstream requires.
The actual Redis flag is1. The fixture explicitly calls the native cancellation
hook and verifies its existing Physical cancellation is not qualified error.
It does not claim a started native pubsub listener or signed-control delivery.

The companion then closes both SDK and vendor gates under the actual owner
fence, using a bound execution/runtime-handle control descriptor. The resulting
ACK reports physical_stopped=false/execution_settled=false. Independent SQL
checks Cancelling, no completion timestamp, active_runs1 and the original
memory reservation; the real collector remains pending. The destination has
received the held request but has not committed it.

Only then is the destination released. It commits the pre-revocation write.
The actual Python workflow observes vendor410 and a refused SDK write, handles
those responses and returns finite normal success. Actual upstream terminal
consumption records Success, not a fabricated Cancelled. Both rows independently
read Success, access tokens401, SDK close returned, resource accounting settles,
and fixture/worker/collector cleanup completes. Busy counts[2,1,1,0]; one launch
each; exactly five allowed vendor effects, including the held commit and no
post-revocation vendor write. The target's before-cancellation SDK document value
remains70. This is the cancellation-request/natural-completion race, not proof
that cancellation can stop arbitrary Python.

## Source and reproduction

API1d33150, enginef49d4ba, SDK139596497, staged bundle61d818ae, cached image33cd92be.
The existing cf CLI local dev/workerd path is used. Readback hashes of the
actual cancellation router, result repository, live-status enum and authority
reader are checked against the native API commit. Existing CT1282CPU/8GiB,
provider1CPU/2GiB/128PIDs. No resource expansion, cloud invocation, credentials/
grants, runtime flags, production effect or shared remote-pool edit.

```sh
# Assigned guest; v1 was preparation only, v2 corrects the diagnostic log label:
timeout -k 5 430 python3 /opt/bifrost-tests/pyodide-ddf316c1/inbox/canonical-cancel-v2/ct128-canonical-failure.py --https-cancel --revision 2
# Repository:
node --test spikes/cloudflare-pyodide/backend/prepare-canonical-https.test.mjs spikes/cloudflare-pyodide/backend/fixture-https-policy.test.mjs
node spikes/cloudflare-pyodide/backend/verify-canonical-cancel.mjs
```

One actual qualification, exit0,74.04274500720203seconds whole lifecycle, not
startup latency. Ruff/Pyright and three focused Node cases pass. All owned
test-project containers stopped; volumes and separate dev resources preserved.
The fixture explicitly removes only its own synthetic cancel flag and confirms
absence; this is not a claim that upstream automatically removes that flag.
Raw receipt, generated local entry and authority hashes are in results/canonical-cancel.
No new CPU, peak-memory or billed-cost measurement is added.

## Remaining contract gap

The extra HTTP revoke path is fixed private test infrastructure, not production
control authentication. The public Python profile remains unadmitted. The real
API cancellation authorization is qualified here, but complete native cancel
subscription, authenticated provider control, a Cancelled result and physical
resource enforcement are not. The prior incoming-disconnect failure remains.

Next: qualify a cooperative async cancellation result through the actual engine,
with full cleanup and resource retention. Do not acknowledge physical CPU stop
or map capability revocation into upstream Linux termination success.
