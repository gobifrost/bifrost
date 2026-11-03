# Owned Automatic Terminal Delivery

`PrivateHttpWorkflowPool` now owns one terminal-delivery task for every reserved
attempt. It waits for the retained physical result, then traverses the existing
native pool's result handler and upstream canonical consumer under current
session fencing. Successful normal completion no longer needs a caller to invoke
`reconcile()`. It does not require destroying the isolate.

`attempt.delivery` is a shielded receipt. Cancelling its waiter does not cancel
collection or reporting. A failed stream or canonical callback remains a failed
receipt; the task's exception is observed without logging secrets or replacing
it with success. There is no automatic retry or re-execution. Explicit
current-owner reconciliation remains the recovery operation after uncertain ACKs.

Retirement refuses pending owned delivery, then requires journal ACKs and settled
physical collection before forgetting tombstones. This is process-local state,
not a new durable queue: losing the parent still requires upstream recovery.
Post-callback owner fencing and canonical idempotency remain necessary because a
callback can commit before its acknowledgment is lost.

## Qualification

CT128 v1 passed: 27 targeted native tests, Ruff and Pyright (zero diagnostics),
then the actual upstream engine/SDK fixture. Its successful peer returned 78
and reached canonical Success through automatic delivery. The other tenant's
ValueError was canonically committed before the injected ACK loss; reconnect
reconciliation preserved its terminal state/log IDs, rejected the stale owner,
and did not launch either workflow again. Native and PostgreSQL accounting
followed 2, 2, 1, 0; both terminal credentials returned 401. Provider remained
running without OOM before explicit harness cleanup; owned running containers
after cleanup were empty. Supervisor wall time was 74.955 seconds including
setup, credential-free prewarm, fixture barriers and cleanup, not pure startup.

Guest allocation remains 2 CPU/8 GiB; provider container 1 CPU/2 GiB. Native API
source `1d33150eb376b71f8a81b427920eda1b0447f829`, engine source
`f49d4ba99cebd24aa497f1244833b940e640885f`, pinned dependency image
`sha256:33cd92be03ec518c7b10e6b6abe5fbe19cb46fbf548fb93bee80f17967f54a1b`.
Runtime is the previously qualified Python 3.14.2/Pyodide 314.0.6 bundle;
no package, runtime, resource or security setting changed for this increment.

The unit invocation is:

```sh
python -m unittest -v test_private_http_pool test_attempt_delivery test_workflow_request_mux test_http_invocation test_workspace_prepare
```

Run within the bounded Docker configuration in
`../results/auto-delivery/pyodide-auto-delivery-v1.sh`, using the existing CT128
checkout and dependency image. The actual engine/SDK qualification command is:

```sh
timeout 430 python3 /opt/bifrost-tests/pyodide-ddf316c1/inbox/auto-delivery-v1/ct128-canonical-failure.py --auto-delivery --revision 1
```

The fixture holds two real tenant workflows after SDK writes, automatically
delivers a workflow exception with an injected post-commit ACK loss, reconnects
and reconciles that retained result without replaying effects, then releases the
peer. The peer must reach the canonical terminal state through automatic
delivery, without explicit reconciliation. It separately checks tenant token
revocation, persisted results/logs, duplicate dispatch, native/PG accounting and
cleanup. Raw results and exact source hashes accompany the qualification.

This does not add a deadline supervisor, public startup registration or cloud
deployment. Lost collection remains uncertain and holds native capacity;
automatic result reporting does not establish physical cancellation. No
untrusted admission or deployed-isolation acceptance follows from these tests.
