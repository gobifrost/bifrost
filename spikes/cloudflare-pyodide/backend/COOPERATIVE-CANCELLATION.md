# Cooperative cancellation qualification

This candidate is not wired into the engine or public executor admission.
Its receipt describes only the tracked asyncio task. It deliberately contains
no BiFrost terminal status, and reports execution_settled=false and
physical_stopped=false. Upstream engine CancelledError cleanup propagates the
exception; this helper must not fabricate an engine-produced result.

On 2026-11-03, CT128 (bifrost-pyodide-test-01) ran eight native unittest cases
using cached image sha256:33cd92be03ec518c7b10e6b6abe5fbe19cb46fbf548fb93bee80f17967f54a1b.
The final packet is /opt/bifrost-tests/pyodide-ddf316c1/inbox/cooperative-native-v2.
The container used network=none, 0.5 CPU, 256 MiB memory and swap ceiling,
64 PIDs, a read-only packet mount, and a 30-second external bound:

```sh
timeout 30 docker run --rm --name pyodide-ddf316c1-cooperative-native-v2 \
  --network none --cpus 0.5 --memory 256m --memory-swap 256m --pids-limit 64 \
  -e PYTHONDONTWRITEBYTECODE=1 \
  -v /opt/bifrost-tests/pyodide-ddf316c1/inbox/cooperative-native-v2:/qualification:ro \
  -w /qualification bifrost-test-api-dev:bifrost-test-7ddfac36 \
  python -m unittest -v test_cooperative_execution
```

Exit 0: eight cases passed. The negative control actually permits a spawned task
to perform an effect after the main task was canceled. That evidence motivated
removing the initial candidate's synthetic Cancelled envelope. The v1 packet is
preserved; it passed its original eight cases but its envelope was too strong.
The v2 tests require the weaker receipt and absence of a terminal status.
Other cases preserve suppressed cancellation success, cleanup exceptions,
user-raised cancellation, wrong-run refusal, duplicate request behavior,
single-run admission and finally-block completion.

Local `node --test spikes/cloudflare-pyodide/backend/stage-cooperative-code.test.mjs`
passes: packaging preserves exact source and refuses overwrite. This is packaging
evidence only. No host Python or laptop Docker test was run.

## Actual local Pyodide RPC

The next experiment executed, using existing cf CLI dev, workerd 1.20261023.1,
Pyodide 314.0.6 / Python 3.14.2, ABI cpython-314-wasm32-emscripten. The pinned
runtime bundle and wrapper guards are enforced by unchanged ct128-sdk-probe.py.
Its historical --approved-https mode is only a reusable launcher here; the
observation's scope is raw cooperative RPC, with no HTTP egress or SDK claim.
The synthetic coroutine waits on an asyncio.Event, not a real vendor request.

First attempt v9 returned HTTP500: after concurrent RPC methods, reading the
child HTTP response body failed with "Cannot perform I/O on behalf of a different
request" (ReadableStreamSource). The exact entry is preserved in
cooperative-http-failed-entry.mjs; the guest retains the full failed observation.
cooperative-rpc-v9.json records source hashes, 10.381-second launcher lifecycle,
no OOM and stopped provider. This is a real transport-integration finding.

The bounded workaround v10 uses run_execution(payload) returning a JSON string
via RPC, avoiding the response stream. Three sequential fresh Dynamic Workers
pass normal completion, requested cancellation and suppressed cancellation.
Each finally block runs and tracked slots return to zero. Wrong-run and late
requests return false; suppressed cancellation stays Success. The cancel case
returns only the coroutine receipt, with physical_stopped/execution_settled false.
cooperative-rpc-v10.json preserves actual outcomes, ABI, source hashes, lifecycle
duration and provider cleanup. Both tests used network-disabled 1 CPU / 2 GiB /
128-PID containers. No runtime flags, security settings or bundle pins changed.

```sh
python3 /opt/bifrost-tests/pyodide-ddf316c1/inbox/cooperative-rpc-v9/ct128-sdk-probe.py --approved-https --revision 10
```

Packet preparation copies the existing approved-https-v3 project into a new
approved-https-v10 packet, overlays cooperative-probe-entry.mjs under the launcher's
approved-https-entry.mjs name and cooperative-probe.mjs under its probe name,
and stages cooperative-code.mjs with stage-cooperative-code.mjs. V9/v10 packets
and stopped providers remain preserved; do not overwrite/relaunch these names.

Next: qualify actual I/O ownership and engine cleanup using the RPC-string route
before considering integration. Event cancellation does not prove vendor I/O
settlement, spawned-task cessation, CPU stop or canonical terminal reporting.
No Cloudflare deployment/invocation was performed. Public admission remains off.

## Held HTTP qualification: incomplete

The follow-up adds an independent loopback destination, actual pyfetch and a
parent-owned fixed-origin capability gate. V11 fails HTTP500. V12 changes only
diagnostic capture, retaining the same assertions and time bounds; it also fails.
Their source hashes and stopped-provider evidence are in cooperative-http-v11.json
and cooperative-http-v12.json. Full v11 HTML error and source remain on CT128
under runs/approved-https-v11. The structured v12 observation is preserved in
cooperative-http-v12-observation.json. V12 lifecycle is 14.382 seconds, not startup
latency. Resources and ABI match the preceding local RPC experiment.

* Normal: destination receives held POST, parent releases it, Python consumes
  its HTTP200 response and returns Success.
* Cancel: after independently observed receipt and before destination commit,
  owner closes the gate and requests cancellation. Python returns the coroutine
  receipt after finally, with zero tracked slots. The destination subsequently
  observes connection closure and still commits the accepted write on release.
* Suppression: Python catches CancelledError and awaits a new pyfetch to the
  closed gate. The runtime detects a hung invocation while the parent awaits
  its execution result. There is no successful suppression result or /after
  destination effect. Absence of that effect is not physical-stop evidence.

This is a failed overall qualification, despite the successful cancel subcase.
Do not integrate it as a complete engine cancellation mechanism. The driver
retains strict expectations and fails on HTTP500; no retry, timeout increase or
skip masks suppression failure. Parent finally releases held accepted writes,
closes gates and removes map entries. Driver failure cleanup also releases them;
provider cleanup is independently recorded as stopped.

Reproduce with a new exclusive packet/revision, overlaying cooperative-probe-entry.mjs
and cooperative-http-probe.mjs under the launcher filenames described above.
Both runs used unchanged ct128-sdk-probe.py --approved-https --revision 11/12.
No production or shared-source edits occurred.

Next bounded hypothesis: direct Task.cancel from a separate RPC may resume Python
work with an unsuitable I/O context. Test original-invocation-owned cancellation
scheduling against the same suppression and accepted-write controls. This is a
hypothesis, not an established cause; preserve current failures.

## Original-invocation scheduler: held HTTP passes locally

V13 tests that hypothesis with an explicit owner_scheduled mode. The control RPC
records a request only. A monitor created by execute_cooperatively in the original
execution invocation checks every 10 ms and calls Task.cancel there. The monitor
is canceled and awaited in finally; this is a cooperative timer, not CPU enforcement.
The direct mode remains the recorded negative-control mechanism, not a production
fallback. Both modes remain companion experimental code, outside upstream engine.

The unchanged held-HTTP assertions now pass all three cases in actual Pyodide:
normal HTTP200; main-task cancellation with cleanup before destination release;
and suppressed cancellation returning Success after the subsequent request gets410
from the closed gate. Independent destination snapshots show no /after effects,
and confirm accepted held writes still commit after release. Wrong-run and late
cancel requests remain false. This qualifies this local mechanism, not physical
stop, canonical cancellation, background-task cleanup or deployed enforcement.

cooperative-http-v13.json records exact packet hashes, Python3.14.2/
cpython-314-wasm32-emscripten, 14.375-second whole-launcher lifecycle and stopped
provider (no OOM). Command is unchanged except --revision 13. Packet:
/opt/bifrost-tests/pyodide-ddf316c1/inbox/approved-https-v13. Same network-disabled
1 CPU/2 GiB/128-PID provider and cached publisher runtime. No cloud activity.

The revised Python source also passes ten native unittest cases in a network-none
0.5 CPU/256 MiB/64-PID container, and Ruff. Native packet is inbox/cooperative-owner-v13;
command matches the earlier native invocation with that packet and container name
pyodide-ddf316c1-cooperative-native-v13. Packaging test and JavaScript syntax pass.
The new native cases exercise owner-scheduled cleanup and suppression followed
by another await. Earlier raw RPC evidence refers to source at c2066797e; earlier
held-HTTP failures refer to source at 95852fd3d. They are not relabeled as successes.

Next integration boundary: expose a separate candidate RPC entry that invokes
actual run_authorized_attempt under this scheduler and retains owned SDK close.
It must preserve original success/error envelopes and report coroutine-only
cancellation separately; no synthetic canonical Cancelled result or capacity
release is authorized by this mechanism alone.

## Engine candidate composition (not executed yet)

cooperative-runtime.mjs now composes a candidate from the actual runtimeCode
object. It preserves the existing run_authorized_attempt call, original result
envelope mapping and close_sdk finally block. It consumes the response inside
the execution invocation and returns a JSON string through RPC. Direct fetch
is refused. Control RPC requests only the bound coroutine cancellation; the
owner-scheduled monitor remains responsible for delivery. The candidate reports
actual SDK-close completion separately from its result/coroutine receipt.
It does not manufacture a canonical Cancelled envelope or release capacity.

Three local Node cases pass (composition, source-shape refusal and packaging).
They inspect the real runtime entry template without importing the generated
bundle; they do not execute the engine. A generated Python entry with SHA256
e0532bf409b6828ce1050d897c8b7459792d74d7695a4a73001f964fa4c7eb55
also passes native Python bytecode compilation in CT128's cached dependency
image, network-none, 0.5 CPU/256 MiB/64 PIDs, 30-second bound. The owned container
is removed; source remains inbox/cooperative-engine-v1.py. Compilation performs
no imports or workflow execution and establishes syntax only.

Next: wire this explicit candidate into a separate canonical fixture, keeping
ordinary runtime/dispatcher behavior unchanged, and run genuine run-credential
engine/SDK cases plus cancellation cleanup. Source composition and syntax do not
qualify engine integration, canonical terminal cancellation or deployed behavior.

## Actual engine/SDK through candidate RPC: normal/error pass

The separate prepare-canonical-cooperative.mjs composition now wraps the existing
HTTPS fixture. It adds an isolated fixture-only, execution/handle-bound control
path, closes future capabilities before requesting cancellation, and removes
its entrypoint map during cleanup. This is not public control authentication.
Non-engine coroutine receipts cause an uncertain transport failure; no canonical
Cancelled result is manufactured. The ordinary fixture/runtime is unchanged.

The normal/error candidate executed on CT128 with real PostgreSQL/Redis/API,
genuine run-scoped identity and the pinned upstream engine/SDK. Command:

```sh
python3 /opt/bifrost-tests/pyodide-ddf316c1/inbox/canonical-https-v5/ct128-canonical-failure.py --https-fixture --revision 5
```

Exit0, 73.061-second complete lifecycle. canonical-cooperative-v5.json preserves
exact commands, packet hashes, source/ABI artifact provenance and cleanup.
verify-canonical-cooperative.mjs checks those observations against current
candidate/adapter source. API1d33150, enginef49d4ba, SDK1395964, bundle61d818a;
this is not a current-main parity or deployed-Cloudflare claim.

Two actual workflows load pinned workspace helpers, read configuration, write
tables, make four independently observed synthetic HTTPS forwards and produce
canonical Failed/Success. Both owned SDK closes return. One launch each,
stale-owner refusal, duplicate dispatch reuse, lost-ACK reconciliation, terminal
obligation retirement and worker cascade cleanup pass. Ended credentials get401.
Both the local provider and owned disposable stack are stopped; volumes remain.
The existing cf CLI and resource-bounded Compose/provider are used unchanged.

This establishes engine-backed normal/error RPC execution. It does not exercise
the new fixture cancel path. Next: actual engine cancellation, owned SDK-close
behavior and canonical uncertain-outcome recovery, without inventing a terminal
result or releasing capacity on a coroutine receipt. RPC string marshaling also
needs separate oversized-result/memory qualification before admission.

## Actual engine cancellation with suppression: local pass

prepare-engine-cancel-fixture.mjs creates a separate synthetic workflow/test
variant. It leaves shared-native-pool.py unchanged, invokes the genuine cancel
API and existing owner-fenced capability revoker, then requests cooperative
cancellation through the separate local control path. The workflow catches
CancelledError at its held HTTPX request, requires later vendor410 and denied
SDK write, and returns its actual success. Success is preserved by upstream;
this is deliberately not a Cancelled terminal-result qualification.

V3 fails at __fixture_cancel with SubrequestChannel cross-request I/O ownership:
the stored entrypoint stub cannot be called from another incoming request.
engine-cooperative-cancel-v3.json preserves failed qualification/source/cleanup;
full private failure and provider logs remain on CT128. No alternate identity,
runtime flag or permission was used to work around the restriction.

V4 changes the parent control mechanism: the incoming control request sets a
bound flag only. A 10 ms monitor inside the original invocation makes the RPC
call, and is awaited during cleanup. Its acknowledgment means requested, not
delivered/stopped/settled. The Python owner monitor then delivers Task.cancel.
Actual workflow success requires observing CancelledError, independently proving
delivery beyond that acknowledgment. The runtime/engine/SDK code is unchanged.

```sh
python3 /opt/bifrost-tests/pyodide-ddf316c1/inbox/canonical-cancel-v4/ct128-canonical-failure.py --https-cancel --revision 4
```

Exit0, 73.639-second complete lifecycle. engine-cooperative-cancel-v4.json records
API1d33150, enginef49d4ba, SDK1395964, bundle61d818a, all packet hashes/commands,
actual run identity, independent destination snapshots and stopped owned stack.
verify-engine-cooperative-cancel.mjs checks the generated fixture against those
hashes and the preserved results. Both SDK closes returned; both canonical runs
ended Success, with ended credentials401. Wrong-tenant cancellation403, ended
peer401 and duplicate own cancellation200/Cancelling retain upstream semantics.
The native physical-cancellation guard still refuses the unqualified target.

The fixture deliberately holds canonical consumer ACK while observing accounting:
busy counts2,1,1,0 and the PostgreSQL reservation stay held until that gate is
released. The real engine result arrives before destination release; the accepted
vendor write still commits afterward. This is not automatic detection/settlement
of downstream effects. Two redacted pre-cancellation logs persist per attempt;
post-revocation log delivery is not proved, because the SDK capability is closed.
No physical stop, background-task cessation or deployed acceptance follows.

Next: unsuppressed engine cancellation must retain uncertain transport/capacity
and reconcile through existing upstream recovery. Separately qualify bounded
RPC result marshaling, log obligations after revocation and real deployed limits.
No new cloud invocation, public admission or ordinary backend behavior changed.

## Unsuppressed engine cancellation: logical recovery, physical obligation retained

The generator's explicit --unsuppressed variant re-raises CancelledError instead
of returning success. V5 exposed a harness defect: the deliberate consumer-ACK
hold also prevented its deadline callback. It timed out waiting for canonical
Timeout. engine-unsuppressed-v5.json preserves the failed run and source hashes;
no product deadline defect is inferred from that blocked fixture callback.

V6 releases that test-only hold once failed collection is observed. The actual
engine-backed collection fails with ConnectionError and supplies no engine
terminal result. The original canonical deadline then produces Timeout, access
gets401, and native busy_count stays1. Calling adapter.retire actually refuses
with "unacknowledged physical routes remain". The previously accepted held vendor
write still commits when explicitly released. No synthetic Cancelled envelope,
retry, forced ACK or automatic capacity release was introduced.

```sh
python3 /opt/bifrost-tests/pyodide-ddf316c1/inbox/canonical-cancel-v6/ct128-canonical-failure.py --https-cancel --revision 6
```

This run exits1 and qualification_passed remains false: after recording the
diagnostic it deliberately stops at unqualified retirement. It is not a passing
end-to-end cancellation qualification. engine-unsuppressed-v6.json records
100.153-second full lifecycle, exact commands/source hashes and stopped test
stack. engine-unsuppressed-v6-diagnostic.json captures the bounded observation
before fixture cleanup. verify-engine-unsuppressed.mjs verifies those facts and
the generated source, without upgrading the result. V6 source compiles through
the actual test runner; generator regression cases pass. Volumes are preserved.

Remaining settlement needs trustworthy provider/invocation termination evidence
and an owner-fenced acknowledgment of canonical recovery. Timeout/401 or a caught
exception alone cannot provide it. SDK-close completion after unsuppressed
cancellation is also not externally established by this failed transport path.
Independent local work remains: RPC numeric fidelity, marshaling budgets and log
obligations. No cloud, production or ordinary-backend change was made.

## RPC numeric fidelity repair

The candidate's ordinary JSON.parse rounded9007199254740993 to9007199254740992
before HTTP framing. A red regression in cooperative-rpc-result.test.mjs also
reproduced signed-zero/exponent changes. cooperative-rpc-result.mjs now uses the
existing parseLosslessJson; the canonical candidate invokes this helper rather
than parsing RPC results itself. Coroutine-only receipts still cannot become
canonical terminal results. Eight focused parser/composition cases pass.

Actual CT128 engine/SDK qualification also passes (cooperative-numeric-v6.json).
The workflow checks positive/negative9007199254740993 and0.1 parameters before
effects, then returns the positive integer for independent canonical persistence
verification. Negative-result, signed-zero and exponent output preservation are
unit-level proof here, not additional deployed/engine observations. The first
verifier incorrectly expected a negative result field; inspection corrected it
to the actual fixture contract without changing the runtime result.

```sh
python3 /opt/bifrost-tests/pyodide-ddf316c1/inbox/canonical-https-v6/run-cooperative-numeric.py
node spikes/cloudflare-pyodide/backend/verify-cooperative-numeric.mjs
```

The explicit wrapper combines existing controller https_fixture/numeric_result
options; no controller/upstream changes or extra queue are introduced. Exit0,
72.459-second lifecycle, one launch per run, canonical Failed/Success, real SDK
effects, four independent HTTPS forwards, reconciliation and owned SDK close.
Exact packet hashes and existing engine/SDK/bundle/image pins remain in the raw
receipt. Provider and disposable stack stopped; volumes preserved; no cloud use.
Marshaling size/memory bounds remain next, independent of numeric correctness.
