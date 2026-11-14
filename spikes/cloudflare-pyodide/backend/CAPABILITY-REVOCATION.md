# Explicit capability revocation is separate from physical cancellation

Prototype0.1.0, companion preparation only. Upstream's Python-profile
_request_terminate still raises Physical cancellation is not qualified for this
runtime. No shared remote-pool/protocol/host behavior or public admission changes.

AttemptCapabilityRevoker binds a trusted close callback once to an existing
journal route and opaque runtime handle. It checks the original worker/launcher,
uses upstream remote_owner_context and fence_remote_owner, and holds the
PostgreSQL shared owner-row lock through bounded callback completion, a final
fence check and transaction commit. Owner replacement requires an exclusive lock.
The callback has a five-second bound and must only close physical capabilities;
it must not request an exclusive lock on the same worker row. This can delay
rotation/heartbeats briefly and is not an independently accepted upstream API.

Only an exact matching capability-only ACK is accepted. The validated response
is copied before another await. Successful replay by the current owner of the
same physical launcher uses the retained ACK without another physical request.
Errors, lost ACKs, expiry or failed commit stay uncertain and are never retried
automatically. No canonical result is fabricated. Revocation does not cancel
the collection task, acknowledge its terminal, settle execution, free memory or
certify physical stop. ACK fields explicitly say physical_stopped=false and
execution_settled=false. Tombstones are host-local, not a second durable queue.

## Actual PostgreSQL and Pyodide qualification

The disposable native test stack creates only a synthetic execution-worker
record, not a business execution. Its current owner launches a fixed fresh
Python network fixture through the real HttpInvocation/InvocationStream path.
No SDK calls are mocked; **no engine/SDK or canonical business execution is
claimed by this increment**. Those remain separate prior receipts.

The destination holds a request. Real native ownership acquisition rotates the
owner once; the stale session cannot reach the close callback. While revocation
holds the real fence, a further real rotation is independently observed waiting
on a PostgreSQL Lock via pg_stat_activity. Then one actual control request closes
the parent gate, the fence commits and rotation completes. Current-owner replay
sends no second close. The journal remains pending/unacknowledged and refuses
retirement. The destination is still uncommitted at that point.
The liveness context is wrapped only to record the real backend PID; ownership
SQL, commit behavior and lock acquisition are not mocked in this live probe.

After parent release, the already-dispatched request commits and the actual
Python fixture returns statuses[200,200,410]: its new request is denied. The
independent server sees only /before and /hold, never /after. Only after genuine
terminal/EOF collection does a fixture transport-acceptance callback acknowledge
and retire the journal. That callback is not upstream business-result reporting.

Final v6 exits0 in58.388168489094824seconds whole stack lifecycle, not startup.
Six native source regressions,14 focused Node cases, Ruff and Pyright pass.
Native owner/fencing source hashes are independently read back and checked
against API1d33150. Existing CT128 2CPU/8GiB, provider1CPU/2GiB/128PIDs, cached
dependency image33cd92be and original runtime-cache pins. No expansion, new
credential/grant, cloud invocation or runtime/security-setting change. All owned
test-project containers stopped; volumes and separate dev resources preserved.

## Experiment history

* v1: five native regressions pass; dependency staging review adds the actual
  gate/stream files before any platform run.
* v2: preflight Ruff finds two multi-statement lines; corrected before execution.
* v3: real probe fails terminal identity validation. The primitive fixture omitted
  evidence.runtime_handle; the native parser correctly refuses it. Parser unchanged.
* v4: fixed envelope passes real PostgreSQL/Pyodide in62.68294697906822seconds.
* v5: review regression mutates a shared callback ACK during the post-fence
  await; one native case fails True is not False. This is source-level callback
  mutability evidence, not an observed remote escalation.
* v6: copy validated ACK before awaiting; all six native cases and the final
  real probe pass. The old source and real-run receipts remain preserved.

```sh
# Assigned CT128, after staging the packet with existing pinned fixture inputs:
timeout -k 5 430 python3 /opt/bifrost-tests/pyodide-ddf316c1/inbox/capability-revocation-v6/ct128-canonical-failure.py --capability-revocation --revision 6
# In the network-disabled cached dependency container:
python -m unittest test_capability_revocation
# Repository evidence:
node spikes/cloudflare-pyodide/backend/verify-capability-revocation.mjs
```

Receipts and prior ACK source are in results/capability-revocation. Exact commands,
source hashes and resource readback belong to each receipt. Inherited staged
engine/SDK artifact metadata in the generic supervisor is not execution proof;
the actual primitive probe explicitly reports canonical_execution_verified=false.
No new CPU/peak-memory/cost measurement or fresh ABI claim is added.

## Remaining gates

The explicit HTTP control path is an isolated fixture, **not public control
authentication**. The child gateway cannot access it. Production needs the
existing authenticated upstream control session, authoritative cancellation
consumption and provider reconciliation, qualified together. The earlier failed
incoming-disconnect evidence remains unchanged and is not repaired by this
explicit close. Actual CPU stop, provider resource limits, memory isolation and
deployed control acceptance remain unqualified. Keep admission disabled.

Next: prepare the canonical cancellation integration while preserving upstream's
physical-stop guard and native accounting; never reinterpret capability-only
ACKs as Linux termination success.
