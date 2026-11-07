# Terminal consumption is not complete post-commit recovery

The combined real local Worker proof at a00ae73e9 injected disconnect **after
the full consumer callback returned**. Its one-launch/one-log result remains
valid for that window. It does not establish exactly-once logs or recovery of
every individual post-commit operation.

This qualification uses the actual upstream workflow consumer, PostgreSQL,
Redis, journal fencing and native `recover_terminal_execution_logs`. Physical
launch is a deterministic fixture callback. No Worker, cloud resource, runtime
security setting or production implementation changed.

## Observed fault windows

| Observation | After full callback | After durable commit, before notification |
|---|---|---|
| Durable execution outcome | Preserved | Preserved |
| Launch callbacks | 1 | 1 |
| Log rows immediately after replay | 1 | 1 |
| Log rows after native recovery | 1 | 2 |
| Log stream after native recovery | Empty | Empty |
| Terminal notifications during replay | 0 (already handled) | 0 (not repaired) |
| Active cache after replay/recovery | Absent | Still present in the fixture |
| Completion metrics callback | Once | Not reached |
| Journal terminal event | Consumed/ACKed | Consumed/ACKed |

The earlier interruption occurs when `publish_execution_update` is called,
after the consumer has committed its result and staged log. The injected
exception prevents delivery and skips subsequent log ACK, derived work and
cache cleanup. Reconnect then consumes a stale terminal result: the repository
refuses another terminal write and the consumer returns normally. This permits
the transport ACK but does not resume the skipped follow-up operations.

Native log recovery subsequently reads the unacknowledged stream entry and
appends it with a new sequence number, then acknowledges Redis. This matches
the explicit at-least-once contract in `stage_logs_to_postgres`; no duplicate
workflow invocation or SDK effect is required to produce duplicate log rows.
The active metadata key remains immediately after this sequence; the fixture
gave it a 60-second TTL and deletes it during teardown. Expiry timing itself
was not measured. No notification subscriber or delivery receipt was asserted.

## Outcome mapping

Both windows were exercised for Success, reported RuntimeError, TimeoutError,
CancelledError and CpuLimitExceeded. The actual consumer persisted the expected
Success, Failed, Timeout, Cancelled and Failed statuses, respectively, preserving
the result/error and unknown CPU metrics as null. These are **reported outcome
fixtures**: they do not prove physical timeout, CPU failure classification at
the provider boundary, or cancellation of a running isolate.

The first success-only run passed 13 tests; its raw receipt is preserved as
`results/postcommit-recovery-success-junit.xml`. The expanded final run passed
21 tests in 9.07 seconds (JUnit suite duration 9.094 seconds). The ten fault
cases are recorded as JUnit properties, including observed log counts and
notification/cache state:

* `results/postcommit-recovery-junit.xml`
* `results/postcommit-recovery.json` (parsed observations and source hashes)

Reproduce from this worktree's isolated tools environment:

```bash
./test.sh tests/unit/dev/test_pyodide_attempt_journal.py -q
```

Focused Docker Ruff and diff checks pass. Broader suites/type checking were not
rerun locally for this test-only increment; normal CI remains the verifier.

## Consequence for the physical runtime boundary

Preserve upstream's meanings: durable result, terminal event consumption and
completed follow-up delivery are different facts. Do not replay a workflow to
repair its notification, invent an exactly-once log promise, or introduce a
provider-owned completion queue. The current prototype can retain upstream's
durable outcome without claiming stronger post-commit recovery than upstream.

These characterization assertions record a limitation, not a waiver or a reason
to preserve it. If upstream strengthens recovery, qualify the stronger behavior
and update these fixtures/evidence. Any repair belongs in the canonical platform
path and ordinary maintainer review, not a second BiFrost in the provider adapter.

Next independent local work: concurrent tenant/attempt isolation and malformed
or unsupported dispatch rejection before effects. Whole-host interruption and
the intermediate commit/notification gap remain unqualified as complete recovery.
The actual cloud physical-termination incompatibility remains unchanged, and
arbitrary untrusted workloads remain unsupported.
