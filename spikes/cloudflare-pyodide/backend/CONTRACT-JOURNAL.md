# Host transport journal experiment

`attempt_journal.py` is a bounded companion experiment for a trusted API-side
supervisor. It is not deployed, not a dispatcher, and not an alternative queue.
The Linux launcher and shared `remote_pool.py` are unchanged. Only the Docker
test runner gains a read-only mount of this single companion module.

The upstream contract remains authoritative:

* `ownership.owns_remote_session` checks the persisted owner generation.
* `fencing.remote_owner_context` carries that generation into canonical writes.
* `fencing.fence_remote_owner` holds the PostgreSQL row lock through the write.
* `repositories.executions.update_execution` rejects missing/wrong claims and
  terminal overwrites. Full consumer completion still owns logs/events/metrics.

The journal reserves a physical route before launching it. Concurrent duplicates
receive the same task. A reused route with a different execution is rejected.
Reconnect can adopt its inventory and deliver its retained result without
relaunching. Failed/rejected acceptance retains the result. ACK requires both an
explicit canonical acceptance and a still-current owner after acceptance.
Completed routes retain tombstones until whole-launcher retirement, preventing
late duplicate commands from creating new effects. Retirement refuses pending
ACKs and permanently closes the journal; capacity exhaustion fails closed.

This is process-memory transport state, not durable recovery. After host process
loss, canonical recovery must decide the outcome; no local entry can recreate a
business run. The caller must authenticate dispatch and validate its canonical
attempt claim before using this component. No arbitrary public ingress exists.
The preflight session check does not replace transactional attempt authorization.

## Qualification

From the repository worktree, use its Docker tools environment to run:

```bash
./test.sh tests/unit/dev/test_pyodide_attempt_journal.py tests/unit/worker/remote/test_ownership.py tests/unit/worker/remote/test_fencing.py -q
./test.sh quality api
```

The new tests use real PostgreSQL owner rotation and claim-fenced repository
writes. Physical launch callbacks are deterministic fixtures, **not new Pyodide
or cloud acceptance evidence**. They exercise duplicate dispatch, identity
conflict, full-journal rejection, stale session delivery, uncertain terminal
acceptance, explicit rejection, replay, retirement, owner rotation before commit
and before ACK, and terminal overwrite prevention. Existing engine/SDK proof at
08f149c99 remains distinct.

## Full consumer reconciliation increment

`reconcile_workflow` now delegates to the actual upstream workflow consumer and
uses `ExecutionInfo.attach_transport_metadata` for parent-owned sync metadata.
It requires a matching consumer worker and execution identity and a canonical
result envelope. It does not translate raw engine output or invent a result
status. Upstream's normal-return callback contract means **terminal consumed**,
including its deliberately ignored stale replay; it does not mean the replay
wrote a new result. This mirrors the unchanged remote bridge's ACK behavior.
Exceptions and owner replacement before ACK retain the journal entry.

The ten-test local run passed in 12.41 seconds, including one real full-consumer
qualification with PostgreSQL and Redis. Its transport fails after the callback
completes but before journal ACK. The execution is durably Success with value 70,
its real Redis log stream has been staged into one PostgreSQL log row and
acknowledged, and its active cache entry is removed. Reconnect adopts the same
task and replays the result through the same upstream consumer: no second launch,
no second log row and no second completion-metrics call. A guest sync flag cannot
override the parent's metadata. Three negative cases reject mismatched worker,
mismatched execution, and noncanonical result success type before effects.

Raw evidence: `../results/consumer-reconciliation-junit.xml`. The physical launch
remains a deterministic fixture. This is not fresh engine/cloud execution proof.
Claims are fixture-created and deleted in teardown, **not canonically acquired
or released by a working executor**. Pub/sub and built-in event functions run
normally, but no event subscriber or external delivery receipt is asserted.

## Next safe implementation

Acquire and release the dispatch using upstream `worker_pool.claims` and its
canonical supervisor, then qualify interruption before launch and after claim.
Extract/reuse the pure result-envelope construction currently embedded in
`simple_worker.py` before connecting actual Pyodide output: do not maintain a
second envelope codec. Whole-host loss and interrupted post-commit follow-up
work still need recovery qualification. This increment injects disconnect after
the full callback, not at every individual post-commit instruction.

The initial targeted run passed all nine tests in 8.23 seconds; raw JUnit is
`../results/attempt-journal-junit.xml`. This comprises six new journal contracts
and three existing owner/fence contracts. Fixture rows are deleted in teardown;
the journal cleanup test additionally proves that retirement refuses unfinished
work and cannot be reopened after its tombstones are cleared.

There is deliberately no `cancel` method. Cancelling an asyncio task or request
does not establish physical termination of an isolate. The observed catchable
cloud CPU failure remains a material blocker for arbitrary untrusted workflows.
No cloud invocation, endpoint reopening, credentials, spending, persistent access
or production platform mutation is part of this increment.
