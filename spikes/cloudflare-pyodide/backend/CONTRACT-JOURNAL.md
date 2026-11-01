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
receive the same awaitable result receipt. Each wait shields the retained
collection task: cancelling a transport waiter cannot cancel collection or
erase the result needed by a replacement owner. The receipt exposes no cancel
method. A reused route with a different execution is rejected.
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

The subsequent claim/envelope increment now uses the actual `claim_next` path
in a local PostgreSQL test: one dispatch/run claim, no duplicate acquisition,
current-owner pre-launch `release_workflow_run`, preserved queue timestamp,
removed claim, and successful reclaim. A rejected transactional reservation
rolls back assignment, claim and PENDING-to-RUNNING together. It does not start
a physical child or assert completed-run claim cleanup. This is qualification
of the upstream helpers, not a new scheduler or a provider admission policy.

`result_envelope.execution_result_envelope` extracts the existing pure mapping
from `simple_worker`; Linux still owns its PSS/RSS measurements and exception
path. The companion full-consumer test now uses the same envelope builder.
Unknown provider metrics remain unknown. No OS dependency is imported by the
mapping itself. Actual packaging and execution of this new helper inside
Pyodide have not yet been qualified; no new wheel/build claim follows.

This increment's exact local command was:

```bash
./test.sh tests/unit/execution/test_result_envelope.py tests/unit/dev/test_pyodide_attempt_journal.py tests/unit/test_solution_module_isolation.py -q
```

All 22 tests passed in 19.98 seconds. `../results/claim-envelope-junit.xml` and
`../results/claim-envelope.json` preserve the raw receipt and source hashes.

The [combined canonical local qualification](CANONICAL-LOCAL.md) subsequently
connected actual Pyodide output through the shared builder and canonical
claim/consumer path. It also verified upstream's intended completed-claim
retention and fixture executor deletion cascade; there is no per-run completed
claim-release API to invent. Production retention policy, stale/competing-owner
handback, whole-host loss and interrupted post-commit follow-up work still need
qualification. Current-owner pre-launch release is not proof of any of those.
Runtime security changes remain outside the current authorization. The prior
disconnect test fails after the full callback, not at every individual
post-commit instruction.

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

## Transport waiter interruption

The original journal returned its raw asyncio task. A cancelled caller awaiting
that task propagated cancellation into result collection; the route appeared
terminal but had no result to reconcile. The new regression reproduced this
before the fix (`../results/transport-waiter-before.xml`). Dispatch now returns
one shared `AttemptResult` receipt that shields each waiter independently while
the journal retains the collection task. Reconnect still returns the identical
receipt and cannot launch again. Canonical acceptance and retirement are
unchanged.

The regression uses actual PostgreSQL owner rotation, an explicitly gated local
launch callback, cancellation of its transport waiter, result adoption, terminal
acceptance and cleanup. It is not an actual isolate interruption experiment,
host-loss recovery, physical-stop proof or new deployed acceptance. A provider
collection task that itself fails remains an unresolved provider failure; this
change does not synthesize a terminal workflow result or retry its effects.

After the fix, all 29 journal, owner and fencing cases passed in 15.75 seconds:

```bash
./test.sh tests/unit/dev/test_pyodide_attempt_journal.py tests/unit/worker/remote/test_ownership.py tests/unit/worker/remote/test_fencing.py -q
```

`../results/transport-waiter-after.xml` retains the raw result. The initial
quality invocation was refused by the existing stack lock while tests ran;
quality was then started sequentially after test completion. No lock control
was altered or bypassed.

`./test.sh quality api` subsequently passed with zero Pyright errors/warnings
and clean Ruff (`../results/transport-waiter-quality.txt`). A focused Docker
Ruff check also passed for the companion module and regression test. No broader
suite or actual provider invocation was run for this host transport change.

## Collection state is not physical terminal state

The old internal `terminal: task.done()` field labelled a failed or cancelled
collector as terminal, despite having no result or physical-stop receipt. The
two new regressions reproduced that exact output before the correction
(`../results/collection-inventory-before.xml`). The companion inventory now uses:

| collection_state | Meaning |
|---|---|
| pending | Collector has not completed. |
| result_available | Collector returned a result; canonical acceptance is separate. |
| failed | Collector raised; only its exception type is reported. |
| cancelled | Collector was cancelled; no workflow/physical outcome is inferred. |

Every entry separately carries `acknowledged` and
`physical_termination_verified: false`. The latter means no physical-stop proof,
not an assertion that the isolate is still running. This is a companion internal
shape, **not** a change to upstream's remote wire schema. Its sole runtime
fixture consumer is updated atomically; no compatibility alias remains.

The 31-case PostgreSQL journal/owner/fence run passed in 13.98 seconds; raw result
is `../results/collection-inventory-after.xml`. The new failed/cancelled cases
prove owner-reconnect adoption returns the same receipt, no second launch,
canonical acceptance is never called, retirement/full-capacity refusal retain
the uncertain route, and exception messages do not enter inventory. Existing
pending/result-available cases check the new fields without a physical-stop
claim. The command was:

```sh
./test.sh tests/unit/dev/test_pyodide_attempt_journal.py tests/unit/worker/remote/test_ownership.py tests/unit/worker/remote/test_fencing.py -q
```

This run uses deterministic host callbacks, not new Pyodide/cloud execution.
The actual engine/SDK before/after routing receipts remain pinned to 815ae7dad;
their offline verifier accepts that exact historical commit. Unknown-result
recovery, timely run revocation and physical CPU termination remain unresolved.

Current upstream reuse points were inspected, not changed: Launcher.send_terminal
in api/src/worker/remote/launcher.py retains a terminal event and event ID before
network send; its session receiver removes the retained event/inventory only on
ACK. The host's _deliver_terminal in host.py schedules canonical delivery outside
the receiver and ACKs only after delivery returns. Those retention/ACK semantics
are the target. The same Launcher also owns Linux children, process sampling and
physical supervision; importing its full OS closure into Pyodide or cloning its
authoritative scheduling is not this increment. No shared launcher/host/remote
pool or Containers source was edited.

`./test.sh quality api` passed with zero Pyright errors/warnings and clean Ruff;
the raw quality output is `../results/collection-inventory-quality.txt`. Focused
Docker Ruff on the companion/fixture and JS syntax/historical provenance checks
also passed. `../results/collection-inventory.json` binds source and evidence
hashes and explicitly records zero actual Worker/cloud invocations in this
increment. The broader backend suite was not run. The own test API is stopped
and the Worker tools container remains on its bridge network only.
