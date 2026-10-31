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

## Next safe implementation

Connect a test-only host driver to the full upstream workflow consumer callback
and authoritative claim acquisition, then test disconnect after durable commit
but before ACK. A boolean repository rejection cannot alone distinguish a stale
attempt from an already committed result: use the canonical consumer/recovery
path, not a guessed success ACK. This experiment deliberately retains rejected
results rather than silently discarding them. Qualify logs/event receipts and
claim release in that same test before wiring a physical Worker invocation.

The final targeted run passed all nine tests in 8.23 seconds; raw JUnit is
`../results/attempt-journal-junit.xml`. This comprises six new journal contracts
and three existing owner/fence contracts. Fixture rows are deleted in teardown;
the journal cleanup test additionally proves that retirement refuses unfinished
work and cannot be reopened after its tombstones are cleared.

There is deliberately no `cancel` method. Cancelling an asyncio task or request
does not establish physical termination of an isolate. The observed catchable
cloud CPU failure remains a material blocker for arbitrary untrusted workflows.
No cloud invocation, endpoint reopening, credentials, spending, persistent access
or production platform mutation is part of this increment.
