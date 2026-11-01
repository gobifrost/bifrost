# Actual refresh waiter cancellation

The local canonical workflow cancels its SDK refresh waiter **after the actual
API has returned HTTP 200 with a replacement**, but before the SDK receives that
response. The refresh core survives cancellation, completes credential adoption
when released, and the next SDK call adopts the replacement without another API
refresh. Pending refresh work is zero before the document write. Independent
readback is 70; canonical single-launch, lost-ACK reconciliation, redaction,
terminal access/refresh 401s and fixture cleanup pass.

This qualifies cancellation of an SDK waiter while its workflow continues.
It is not whole-workflow cancellation, HTTP disconnect, Worker termination,
CPU preemption or recovery from a lost isolate. Those guarantees remain open.

## Deterministic transport instrumentation

The local TestApi capability fetches the real `/auth/refresh` endpoint, records
its HTTP 200, and holds the unmodified response before forwarding it. A gate
stores only phase promises/events, never credential bodies. The child waits for
the readiness event, cancels/awaits its waiter, confirms the core task remains,
releases the response, waits for core completion and asks the real SDK to adopt
the replacement. No SDK result, token, authorization response or API operation
is mocked or synthesized.

The gate is created only for the fixed local `refresh_interruption` fixture
mode. Unknown modes fail admission before runtime effects. Its two synchronization
paths are handled only for the same attempt's bound capability and are never
forwarded to the API; ordinary API-route admission rejects them. Cleanup releases
the gate idempotently, closes readiness with a false result if no successful
response arrived, and removes its map entry. A late response cannot leave either
gate promise pending. There is no new durable state,
queue, public endpoint, credential grant or Cloudflare resource.

Observed phases are `server_response_held`, `ready_observed`, `release_requested`,
`response_forwarded`. The source workflow checks actual CancelledError and one
pending core task before release, a distinct replacement afterward, and zero
pending tasks before continuing. The API trace contains exactly one refresh.

## Evidence and reproduction

`results/canonical-refresh-interruption-cleanup.json` records the actual scoped API,
engine/SDK, gate phases, canonical result and exact companion source digests.
It reuses the qualified cooperative bundle from `concurrent-runtime-manifest.json`;
the new gate is parent-side test instrumentation, not an SDK/package change.
The earlier successful `canonical-refresh-interruption.json` and initial
gate/entry snapshots remain intact. Final cleanup semantics are requalified in
the separately named receipt rather than rewriting that result.

With the isolated API healthy and authorized-local Wrangler supervisor attached
to its test network, from the spike directory:

```sh
node backend/canonical-refresh.mjs --run canonical-refresh-interruption-cleanup interruption concurrent-runtime-manifest.json
node backend/verify-refresh-interruption.mjs
node --test backend/refresh-gate.test.mjs backend/fixture-admission.test.mjs backend/test-api-route.test.mjs
```

Observers refuse existing receipts before effects. Verification is offline.
Gate/admission/route tests cover held-response sequencing, cleanup release,
unsupported mode rejection and fixed API route isolation. Focused Ruff checks
the changed Python fixture; JavaScript syntax checks pass. No broad API/client
suite, deployed run or CPU/peak-memory measurement is claimed.

Earlier mixed-call evidence remains pinned to its tested source:

```sh
node backend/verify-concurrent-refresh.mjs --commit 8cf1ded85e342642808215adb363dc9e0336d212
```

Own supervisor/network/API state is restored after this local qualification.
No cloud invocation, production change or shared execution-placement edit occurred.
