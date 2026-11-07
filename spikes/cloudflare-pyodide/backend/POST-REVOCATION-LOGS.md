# Post-revocation logging boundary

Actual engine cancellation receipt v4 contains four captured log entries but
only two PostgreSQL entries. Two runtime log POSTs get 410 after capability
closure. The two later messages are present in the engine result; absence from
PostgreSQL is not an engine capture failure.

Pinned API 1d33150's WorkflowExecutionConsumer stages logs from the existing Redis
stream, not the result's log list. RuntimeLogRequest forbids extra fields and
has no client event/idempotency ID. SDK 1395964 sends level/message and receives
a server-generated Redis entry ID. Replaying captured messages cannot safely
distinguish already accepted rows, repeated messages or lost acknowledgments.
This assessment does not assert that upstream promises lossless logging after
cancellation; its engine explicitly treats streaming failures as non-fatal.

The companion now reports bounded log_delivery metadata on unsuccessful runtime
log responses. It includes counts, never log text or credentials, and explicitly
sets persistence_verified=false and automatic_replay_safe=false. A successful
HTTP response alone is not a persistence receipt. No diagnostic on the normal
path means only that no unsuccessful response was observed. Missing responses
and server-side silent failures are outside this projection's proof.

## Actual qualification

```sh
node --test spikes/cloudflare-pyodide/backend/log-delivery-evidence.test.mjs
node spikes/cloudflare-pyodide/backend/verify-post-revocation-logs.mjs
python3 /opt/bifrost-tests/pyodide-ddf316c1/inbox/canonical-cancel-v7/ct128-canonical-failure.py --https-cancel --revision 7
```

The --post-revocation-log generator variant deliberately logs the genuine scoped
test run token after revocation. Actual engine/SDK execution redacts it in the
captured result. Four logs remain captured, two POSTs fail 410, and only the two
pre-revocation entries persist. The new metadata is present in the retained
provider evidence. Both workflows preserve canonical Success and owned SDK
close; cancel authorization, accounting and cleanup checks remain intact.

post-revocation-logs-v7.json binds exact commands, source/packet hashes and pinned
engine f49d4ba / SDK 1395964 / API 1d33150. Exit 0, 89.839-second whole lifecycle. This is
local workerd/Pyodide evidence. Provider and disposable stack stopped, volumes
preserved; no cloud invocation, capability-policy change or credential grant.

## Contract implication

Capture/redaction and observability are qualified here; persistence repair is
not. No direct database write, Redis replay, new log store or post-revocation
authorization exception was added. The ordinary upstream result semantics stay
authoritative.

A possible upstream extension is a stable per-attempt log-event identity with
idempotent acceptance/receipt through the existing log stream, plus an explicitly
authorized terminal reconciliation path under current execution ownership.
That is a proposal, not an existing upstream API. It requires review of late-log
authorization, stale-owner fencing, ordering, redaction, limits and retention.
Without it, this companion reports the gap instead of claiming exactly-once
log delivery or silently replaying messages. Physical termination and deployed
resource enforcement remain independent admission blockers.
