# Attempt-scoped native request collection

`workflow_request_mux.py` holds accepted futures, collection tasks and results
by execution ID. The fixed qualification transport now delegates its streamed
workflow request to this component. Each trusted collector is bound before
dispatch; unbound actions/runs are refused. Identical duplicate requests reuse
the original collection, while changed native request metadata is refused.
Request snapshots are taken before suspension. No credential issuer, claim loop,
business queue, terminal authority or retry policy is added.

The session generation is checked before lookup, before the collector starts,
and after waiting for acceptance. Retained requests cannot be rebound to a newer
session. Cancellation of an HTTP/native request waiter leaves its independently
owned collection running and retained; it does not assert physical cancellation.
Native journal/result consumer semantics remain responsible for terminal ACKs.

Five focused request-collection tests cover two concurrent slots completing in
reverse order, duplicate delivery, independent failure, immutable credential
snapshots, cancelled request waiters and owner rotation. Four HTTP component
tests separately cover sessions/framing. These are native synthetic interleaving
tests, not actual concurrent engine/SDK execution or deployed isolation proof.

## Fail-first evidence

Two newly added assertions failed against the first component: a replacement
generation could read an old retained ACK, and an already-scheduled collector
could start after ownership rotated. The fix stores admission generation per
slot and checks it again at collection start and acceptance return. The original
helper, original four-case tests, five-case exposing regression and observed
failure summary are retained under [results/request-mux](../results/request-mux/).

An intermediate nine-case run passed assertions but emitted an unobserved-task
warning: the owner-rotation test did not retrieve its independently retained
collection exception. The final test explicitly awaits and checks that failure.
That fixture repair does not cancel the collection or discard its error.
The final harness also avoids changing its dispatch context before the mux
validates a duplicate request. Earlier single-run receipts and source remain
preserved; the final source receives a separate real lifecycle qualification.

## Current boundary

The canonical fixture exercises this component with a real engine/SDK run and
the existing late finite-settlement scenario. Final V4 passes four phases:
one SDK upsert of 70, canonical Timeout/401 while the invocation remains held,
actual late Success, lost ACK and native replay without a second launch. The
provider remains running/no OOM until fixture cleanup; the owned project has no
running containers afterwards. Supervisor 102.210 seconds includes setup,
deliberate 40-second async work and cleanup, not startup/CPU/billing. Raw receipt
[V4](../results/request-mux/v4.json) pins source/workspace/image hashes. Nine
focused native tests, Ruff and Pyright pass. It still creates a native pool
per fixed fixture and its workflow validator uses singleton credential/evidence
fields. Consequently this change is preparation for a shared native relay, not
acceptance of multiple real engine runs through one pool/owner session.
The [shared-pool follow-on](SHARED-NATIVE-POOL.md) moves those fields into
per-attempt slots in a separate dedicated fixture and qualifies two actual
run-scoped identities through one native pool. Guarded mux retirement now
follows actual canonical journal retirement there. The earlier single-run
fixture and its exact historical receipt remain preserved; a reusable admitted
deployment adapter is still outstanding.

Same CT128 2 CPU/8 GiB allocation and existing cached pins. No cloud deployment,
invocation, new credentials, runtime setting or admission change. Deployed CPU
cancellation remains unqualified. Normal finite completion requires awaited work,
terminal stream completion and canonical reconciliation, not isolate destruction.

```sh
timeout 430 python3 /opt/bifrost-tests/pyodide-ddf316c1/inbox/request-mux-v4/ct128-canonical-failure.py --request-mux --revision 4
node spikes/cloudflare-pyodide/backend/verify-workflow-request-mux.mjs
```
