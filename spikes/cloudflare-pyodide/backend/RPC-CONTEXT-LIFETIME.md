# Caller-context qualification: request-owned child and retained control

The first two attempts did not reach cancellation qualification. Admission remains disabled.
The fixed synthetic fixture uses no workflow, credentials, or outbound network.

* v1 transfers a dynamically loaded Python entrypoint into a calling JavaScript
  Worker. The runtime rejects this with DataCloneError: dynamic entrypoints cannot
  be transferred; it recommends a parent entrypoint that constructs and forwards.
* v2 supplies a reloadable parent ContextBridge. Transfer succeeds, but its use of
  the Python handle created in the outer request fails with "Cannot perform I/O
  on behalf of a different request" (SubrequestChannel). The caller has an
  exception tail. The fixture does not reach gate release or state observation.

These are harness/runtime-boundary failures, not evidence that cancellation works
or fails. The HTTP 500 body is non-JSON; the harness JSON decoder consequently
exits nonzero and its finally block preserves the receipt and stops the provider.
Both receipts lack an observation, have cloud_invocations=0, and record the owned
container stopped. Raw bounded synthetic logs and exact source snapshots are in
`../results/rpc-context/`. No result was invented from a missing response.

## v3/v4: actual request-owned child

The parent bridge now constructs and consumes its Python child within the same
RPC invocation. A separate parent capability records fixed synthetic events and
holds the gate; no child handle crosses request contexts. v3 executes successfully.
v4 adds a positive control retaining the calling event with waitUntil:

| Case | Before gate release | After release, 300 ms observation |
| --- | --- | --- |
| Awaited control, gate already open | Python returns 7, effect recorded, tail ok | Completed |
| Retained waitUntil | started, response EOF; no caller tail yet | effect, bridge return, caller tail ok |
| Early response, no waitUntil | started, response EOF, caller tail ok | no effect or bridge return observed |

The gate is released only after caller tail observation or a bounded 2.5 second
tail wait. The retained case proves this gate/effect mechanism can resume Python
and report completion after response EOF. All event ordering is from the same
synthetic parent observer; it is independent of Python state but not an external
durable service. Global maps are strictly fixture state, never scheduling authority.

This distinguishes invocation settlement from a returned response. It supports
bounded local I/O context behavior only: missing effects within 300 ms do not prove
physical CPU cessation, absence of all future effects, isolate destruction, or
deployed enforcement. Tail cpuTime/wallTime are zero on this emulator and provide
no usable CPU measurement. The deliberately floating RPC in the early-response
case is a negative control, not a production pattern. All returned HTTP bodies
are consumed; no returned RpcTarget requires disposal in this primitive-result path.

The emulator explicitly warns that a promise resolved into a completed/canceled
request context had its continuations canceled. Preserve `v4-log.txt`: the gate
itself is a cross-request parent promise, so this observation must not be generalized
to arbitrary external HTTP responses. The suggested compatibility flag was not
enabled; changing enforcement to make the fixture pass would invalidate the test.

Next: qualify supported deadline/CPU enforcement separately and connect effect
revocation/fencing to actual upstream authorization. No caught exception, tail,
silence, or Linux harness kill is sufficient physical-stop evidence.

The official [loader API](https://developers.cloudflare.com/dynamic-workers/api-reference/)
documents load as a fresh Worker and get as an ID cache with no guarantee that
two requests reach the same isolate. Using get and assuming shared Python globals
would therefore invalidate an isolation/cancellation conclusion. Normal successful
completion does not require destroying the isolate.

Actual commands: cached cf CLI dev through
`ct128-sdk-probe.py --rpc-context --revision N` for N=1,2,3,4, each under
`timeout -k 5 120`. Existing CT128, 2 CPU/8 GiB/no swap; provider 1 CPU/2 GiB,
network none, globalOutbound null. Runtime bundle SHA 3c3fd5a4..., Pyodide
314.0.6/CPython 3.14.2 from the existing qualified cache; neither failure measures
ABI anew. v2 cleanup receipt elapsed time is not startup latency. No cloud calls,
deployment, spending, new package or runtime/security setting change.
