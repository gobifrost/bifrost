# Execution Log Durability

Execution workers append each log line to `bifrost:logs:<execution-id>`. The
append uses Redis `XADD MAXLEN ~10000`, so the live stream is bounded and is
not an archive. PostgreSQL `execution_logs` is the durable history.

## Delivery and recovery

Completion stages parseable stream entries in the same transaction that writes
the terminal execution result. The resulting receipt identifies only the
entries staged by that transaction. Redis entries are acknowledged with `XDEL`
only after the transaction commits. A process failure after commit and before
that acknowledgement can replay an entry, so this is **at-least-once**
delivery. Readers must not assume deduplication.

Completion first wakes a synchronous caller after its authoritative commit.
Receipt acknowledgement and a one-execution terminal recovery then run outside
that response-latency path. `cleanup_stuck_executions` also performs one
bounded recovery page every five minutes; the cadence is registered in
`api/src/scheduler/registry.py`. Malformed Redis entries remain for repair,
but do not block later valid entries in the same page. Recovery never flushes
a live execution stream, which preserves the live reader's identity.

## Retention

Run retention removes streams for selected executions only after the database
deletion commits. Redis cleanup is best effort. The ordinary `run.retention`
pass also scans one bounded page for valid UUID stream keys whose execution row
is absent; it removes only those orphaned keys. It leaves malformed keys and
every stream with an extant row, including rows retained by Keep Forever and
running rows. A dry run does not reclaim Redis streams.

The orphan rule is safe only because durable acceptance commits the `PENDING`
`Execution` and its `WorkDispatch` before handing work to an executor:
`api/src/services/execution/async_executor.py::_accept` commits before
`hand_off_workflow_run`, and `api/src/services/execution/workflow_dispatch.py::stage_workflow_run`
creates the row in that transaction. A worker cannot produce its stream until
the committed dispatch is handed off. `api/tests/unit/services/execution/test_workflow_dispatch.py::test_acceptance_commits_a_pending_run_before_waking_executors`
exercises that ordering.

Orphan reclamation also relies on the execution-ID allocation contract. Normal
producer paths allocate a fresh UUID4: the public workflow request has no
`execution_id` field, `enqueue_workflow_execution` allocates one when none is
provided, and system, endpoint, form, integration, and agent callers allocate
their IDs before acceptance. `api/tests/unit/services/execution/test_async_executor.py::test_enqueue_workflow_execution_allocates_a_fresh_uuid_when_unset`
pins the queue default. The few preallocated IDs are fresh IDs made for the
same launch (for example, agent tool progress) or retries of an existing
`PENDING`/`SCHEDULED` execution row; they are not a retained-run replay. Thus,
after retention has deleted a row, normal production code does not recreate a
different execution with that former stream key. This prevents a stale
absence-scan page from deleting a new run's stream. The primary key only
protects the interval while a row exists; it is not the reason this race is
safe.

If this acceptance-before-handoff invariant changes, disable absence-based
orphan reclamation until the new producer path proves the same ordering.
