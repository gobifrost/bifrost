# Explicit retained-result recovery

`AttemptJournal.reconcile_retained_workflow` consumes a result retained by
upstream's physical launcher after its collector failed or was cancelled. It
takes the existing `event: result`, `route_id`, `_event_id` and canonical
envelope. It never reconstructs an outcome from an exception or reexecutes code.

This is trusted-parent code, **not** an HTTP API or credential grant. Only an
authenticated upstream transport may supply events in a real adapter. It checks
worker/consumer identity, route/execution binding, event/envelope shape and
current PostgreSQL session ownership. It defers active collection and refuses an
already available local result. The normal fenced workflow consumer owns the
canonical mutation. Ownership is checked again before consumption is marked.
The caller ACKs upstream only after True; replay then avoids duplicate effects.

## Tested upstream reuse

The protocol/DB test uses actual unchanged upstream implementations:

* `Launcher.send_terminal` retains the event before a simulated send failure;
  `Launcher.inventory` exposes the retained event ID and route.
* The companion delivers it through the real WorkflowExecutionConsumer with
  actual PostgreSQL claims/fencing and Redis log recovery.
* `Bridge.acknowledge` creates the real ACK envelope. Its first simulated send
  fails; the actual launcher keeps its event. Replay repeats no completion.
* The upstream `_receive_control_commands` processes the later ACK and removes
  both retained payload and inventory entry.

Assertions prove one deterministic launch callback, one completion metrics
invocation, PostgreSQL value 70 and one persisted log row. Collection still
reports failed, while the separately retained result is consumed. Physical stop
remains unverified. Both Linux template PIDs stay None; no child is started.
The test ends only its control receiver, not a real execution runtime.

The initial scoped run passed 33 tests. Seven negative cases were then added;
the final 40 passed in 14.74 seconds:

```sh
./test.sh tests/unit/dev/test_pyodide_attempt_journal.py tests/unit/worker/remote/test_launcher.py tests/unit/worker/remote/test_ownership.py tests/unit/worker/remote/test_fencing.py -q
```

Raw evidence: `../results/retained-result-initial.xml` and
`../results/retained-result-final.xml`. Negative cases cover missing event ID,
wrong route/execution/worker, invalid success type, active collection and an
available local result. No rejection calls the consumer or ACKs. A stale owner
is also rejected in the recovery test.

`./test.sh quality api` passed with zero Pyright errors/warnings and clean Ruff.
Focused companion Docker Ruff and diff checks passed too. Raw quality output is
`../results/retained-result-quality.txt`; `../results/retained-result.json` binds
source/evidence hashes and explicitly marks synthetic transport/result and zero
Worker/cloud invocations. The broader backend suite was not run. The own test
API is stopped; the Worker tools container remains on its bridge network only.

## Limits and next integration

These are **unit/protocol tests with simulated sockets and a synthetic result**,
not new engine/SDK, Pyodide, WebSocket-authentication or cloud acceptance. Real
local engine/SDK evidence remains separately pinned to 815ae7dad. Next, place
upstream retention on the provider/supervisor side of the dropped response and
consume an actual Python Worker result through this path. If that retainer
never received the result, this method cannot recover it. No second business
queue or automatic retry is justified.

The live adapter still needs authenticated negotiation, inventory adoption and
bounded retention/cleanup tied to its physical handle. In-memory retention does
not survive whole-host loss; canonical recovery stays authoritative. Physical
CPU termination, timely interrupted-run revocation, stdout and deployed
acceptance remain open. The full backend is incomplete.

Upstream Launcher imports Linux process supervision and OS sampling. The test
uses its actual journal **in the Linux parent**, not Pyodide. Its fork/subprocess/
PID contract cannot be solved by a missing wheel build; no such build or Pyodide
import compatibility is claimed. No shared launcher, host, remote pool,
Containers, production code, endpoint or runtime security setting changed.
