# Real Engine/SDK Retention Across Collector ACK Loss

The existing CT128 fixture now exercises companion retained-terminal adoption
using an actual upstream engine/SDK response, rather than synthetic terminal
evidence. Both tenant workflows execute their independently observed SDK table
writes using genuine run-scoped identities and API-derived pinned source.

For one workflow, `RetainedHttpFault` delegates to a real HTTPX transport and
retains the original local provider response. `InvocationStream` validates its
accepted/result frames through EOF. The original collector instead receives an
injected `ReadError` before seeing that accepted frame. Its client closes while
the retainer still owns the actual connection. No SDK/API response is mocked in
this real run; only the original collector's read boundary is faulted.

This is not a real TCP cut to the provider: its connection deliberately remains
owned by the retainer. It demonstrates collector failure with surviving retention,
not Cloudflare disconnect semantics, provider-side durable retention, cross-host
authentication or recovery after the retainer process dies. The evidence source
is the original fixed provider response inside the isolated test network; no new
public retained-result endpoint or invented launcher event ID is introduced.

## Observed Contract

CT128 v1 passed:

- Original collector accepted handle and timestamp remain absent. Its collection
  and delivery retain `ReadError`; native physical capacity remains held.
- Retainer reaches actual response EOF, and adoption traverses the native handler
  into the real canonical consumer. The first callback commits workflow failure
  but loses its ACK. Busy count remains two while the successful peer is live.
- A real PostgreSQL owner reconnect rejects stale delivery and accepts unchanged
  retained evidence without rerunning the child or duplicating terminal state,
  completion timestamp or log IDs. Exactly one physical POST targets that child.
- The peer's credential remains usable (200) while the failed run's is revoked
  (401). The normal peer then returns 78 through automatic delivery, and both
  terminal credentials return 401. Both workflows launch once.
- Native/PG accounting follows 2,2,1,0. Journal/mux retirement passes despite the
  preserved failed original receipt, after trusted terminal acceptance. Fixture
  ownership cleanup passes and owned running containers after cleanup are empty.
  Provider is alive/no OOM immediately before harness cleanup.

These are real local Pyodide engine/SDK and canonical-control results. The control
reconnect is the existing direct fixture using real ownership functions, not a
qualified signed public WebSocket/session startup path. Public admission remains
disabled. No physical isolate destruction or abnormal CPU termination is proved.

## Reproduction and Evidence

Guest: CT128, unchanged 2 CPU/8 GiB. Native API checkout
`1d33150eb376b71f8a81b427920eda1b0447f829`, engine source
`f49d4ba99cebd24aa497f1244833b940e640885f`; pinned dependency image
`sha256:33cd92be03ec518c7b10e6b6abe5fbe19cb46fbf548fb93bee80f17967f54a1b`.
Provider uses cached cf CLI `dev`, 1 CPU/2 GiB/128 PIDs and the previously pinned
runtime. Unit/quality containers: network none, 0.5 CPU/1 GiB/64 PIDs.

Stage the four new/changed fixture files over `retained-contract-v1` using the
checked-in script in `../results/retained-http/`. It runs 49 native unit cases
(2.393 seconds) and Ruff/Pyright, all passing with zero diagnostics. Its ownership
test verifies original client close leaves the retained stream open until EOF
and refuses a second POST. Then run:

```sh
timeout -k 5 430 python3 /opt/bifrost-tests/pyodide-ddf316c1/inbox/retained-http-v1/ct128-canonical-failure.py --retained-http --revision 1
node spikes/cloudflare-pyodide/backend/verify-retained-http.mjs
```

Raw receipt includes exact commands, source hashes, real SDK observations and
canonical results. Total supervisor wall time 73.608 seconds includes setup,
credential-free prewarm, fixture waits and cleanup; it is not pure startup or CPU
time. CPU, peak isolate memory and billing were not measured. No cloud invocation,
deployment, spending, credentials/grants, package or runtime/security-setting
changes. No heavy laptop Docker or shared upstream source edits.

Next: qualify uncertain deadline callback recovery and reject oversized context
before native bookkeeping. Supported startup/registration, deployed retention
and abnormal CPU/isolation enforcement remain explicit acceptance gaps.
