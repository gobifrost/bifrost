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

The initial evidence above is **unit/protocol testing with simulated sockets and
a synthetic result**, not Pyodide, WebSocket-authentication or cloud acceptance.
The actual local Worker integration below now covers the next boundary. If its
retainer never received the result, this method cannot recover it. No second
business queue or automatic retry is justified.

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

## Actual local Python Worker result

`provider-response-loss.py --retained-response` places upstream's actual
Launcher journal in the existing Linux test gateway. The gateway obtains the
complete response from a fresh local Python Dynamic Worker, independently reads
the SDK-written document, checks the actual envelope's execution identity,
redaction probe and workflow code hash, and retains it before closing the
downstream collector's TCP socket without response bytes.

The first fixture invocation passed (`canonical-refresh-retained-response.json`):

* The real upstream engine/SDK performed config/helper loading and one successful
  table upsert; independent readback was 70. Pinned workspace digests and actual
  execution/worker identity are retained in the report.
* The collector saw actual RemoteProtocolError; canonical state was Running.
  The gateway already held the upstream terminal event.
* A real PostgreSQL owner reconnect advanced the generation. The old owner was
  refused. The runtime credential still read its known module with 200 after
  reconnect, proving generation rotation alone did not cause revocation.
* Retained delivery through the normal consumer committed Success/value 70.
  Access and refresh then both returned 401 **before fixture purge**.
* A deliberately lost control ACK preserved the upstream event. Replay used no
  second launch or log row. The actual upstream ACK receiver then removed its
  retained payload and inventory; the one persisted log row was unchanged.

There was one local Python Worker launch. No SDK call or result was mocked in
this fixture. The separate **control transport is simulated**: actual upstream
event/ACK code is composed in process, without a live authenticated WebSocket
session. The existing parent test-stack key stays in the Linux parent and is
not a new credential/grant. No Linux template child is started.

From the spike directory, after the existing isolated local setup is ready:

```sh
node backend/canonical-refresh.mjs --run canonical-refresh-retained-response retained-response concurrent-runtime-manifest.json
node backend/verify-retained-response.mjs
```

The run refuses the existing receipt before effects. Offline verification checks
actual observations and exact prototype/upstream source hashes; `--commit <sha>`
verifies immutable historical source when the branch later changes. The cached
479-module runtime payload was checked before execution and remains
`21b82a201061b86a025265b371b057a3516bb8f549c5a701634390f0a8ca9dc9`.
Wrangler 4.156.0 ran local mode. Focused Docker Ruff, JS syntax/diff checks and
the current-source offline verifier pass. No broader API suite reran for this
fixture-only integration; the 40-test/API-quality result above remains separate.

`retained-response-local-cleanup.json` records the exact own Wrangler supervisor
SIGINT, independent port-8799 refusal, bridge-only network and stopped API. Test
row/claim cleanup also passed. Stopping the test control receiver or Wrangler is
not a physical-termination receipt for untrusted Python; that field stays false.

This proves local recovery for **gateway-to-collector response loss after the
gateway received the result**. Worker-to-gateway loss, gateway/host death,
live session authentication/admission, general routing and cloud deployment
remain unqualified. `local_recovery_verified` is true; broad
`recovery_acceptance` remains false. No new cloud call, public endpoint,
spending, persistent access, production or Containers change occurred.
