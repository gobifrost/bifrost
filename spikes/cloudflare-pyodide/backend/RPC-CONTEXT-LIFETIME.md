# Caller-context qualification: two preserved local failures

Neither attempt reached cancellation qualification. Admission remains disabled.
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

Next experiment: construct the Python Worker inside the forwarding invocation,
and observe progress through a synthetic parent capability rather than using a
request-owned child handle from another context. Include a normal-completion
control and independently observed post-context gate/effect ordering. Do not
replace this with killing the Linux harness or treating silence as physical stop.

The official [loader API](https://developers.cloudflare.com/dynamic-workers/api-reference/)
documents load as a fresh Worker and get as an ID cache with no guarantee that
two requests reach the same isolate. Using get and assuming shared Python globals
would therefore invalidate an isolation/cancellation conclusion. Normal successful
completion does not require destroying the isolate.

Actual commands: cached cf CLI dev through
`ct128-sdk-probe.py --rpc-context --revision 1` and `--revision 2`, each under
`timeout -k 5 120`. Existing CT128, 2 CPU/8 GiB/no swap; provider 1 CPU/2 GiB,
network none, globalOutbound null. Runtime bundle SHA 3c3fd5a4..., Pyodide
314.0.6/CPython 3.14.2 from the existing qualified cache; neither failure measures
ABI anew. v2 cleanup receipt elapsed time is not startup latency. No cloud calls,
deployment, spending, new package or runtime/security setting change.
