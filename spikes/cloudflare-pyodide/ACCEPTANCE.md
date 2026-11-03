# Backend acceptance ledger

Reconciled against preserved source/evidence on 2026-11-02, including the deployed
engine/SDK dev-stack receipts in [dev/README.md](dev/README.md) and the exact
publisher build and actual-workerd ABI/timed-cancellation receipts in SOURCE-RUNTIME-QUALIFICATION.md.
Matched Linux/workerd workloads are in WORKERD-BENCHMARK.md; the explicit
lost-worker/stale-result/log-drain boundary is in backend/WORKER-LOSS-RECOVERY.md.
Actual local provider-process interruption is in backend/PROVIDER-INTERRUPTION.md;
its explicit recovery is not automatic detection or per-isolate stop acceptance.
The full backend objective is **not achieved**. The useful trusted subset is
experimental YELLOW; first-class arbitrary untrusted execution is RED under the
tested physical-termination contract. A source PR, an approved research review,
green CI or an HTTP 200 envelope cannot substitute for runtime acceptance.

| Required guarantee / artifact | Current authoritative evidence | Status and missing proof |
|---|---|---|
| Deployable Worker and exact setup | Wrangler configs, README, historical cloud version receipts in IMPLEMENTATION.md | Prototype exists; production provider is not enabled. |
| Current upstream engine and SDK, genuine run identity | WORKERD-SDK-QUALIFICATION.md; dev/cloud-sdk-success.json; dev/PUBLIC-URL.md; dev/STREAM-LOGS.md | Original four cloud runs plus separately pinned public-URL and stream-log runs pass with genuine run-scoped identity against CT128 dev API. No deployed canonical claim/terminal execution or complete signed-dispatch integration. |
| Parameters, async execution, workspace imports, logs/errors | Current-engine cloud fixtures, local canonical receipts and dev/cloud-sdk-success.json | Deployed credentialed async workflows load pinned workspace helpers, return values and capture redacted structured logs. Canonical cloud error/terminal persistence remains unqualified. |
| Public execution URL versus SDK transport | dev/PUBLIC-URL.md; results/public-url local and cloud receipts | Explicit trusted metadata differs from the restricted virtual SDK endpoint. Local legacy/explicit controls pass; deployed genuine run-scoped SDK integration read/table write passes with attacker URL overrides ignored. Ended access/refresh401; executor closed and secret removed. Canonical cloud dispatch and physical termination remain unqualified. |
| Actual canonical error and output channels | backend/WORKFLOW-FAILURE.md; failure-terminal receipt | Historical pinned-engine run persists/reconciles genuine ValueError and redacted structured logs, but lacks stdout. That runtime qualification remains failed; the new shared-engine repair below has not been substituted into its receipt. |
| Shared engine stdout/stderr repair | backend/STREAM-CAPTURE.md; dev/STREAM-LOGS.md; results/stream-cloud | Four baseline failures repaired;37 native tests, guest Pyodide and actual deployed engine/SDK succeed. Redacted stdout/stderr persist in PostgreSQL using existing log delivery; repeat recovery flushes0. Historical canonical failure/stdout fixture remains failed until rerun with the new engine pin. |
| Integration/config reads and table writes | WORKERD-SDK-QUALIFICATION.md; dev/cloud-sdk-success.json | Actual deployed SDK config/integration reads and table writes pass against the isolated dev API, including encrypted synthetic secret decryption/redaction, peer403 and independent table70. OAuth and canonical end-to-end cloud dispatch remain unqualified. |
| Dependency closure and pinned provenance | Runtime manifest/source hashes, packaged closure and module-response tests | Tested closure exists. Broad package compatibility and missing-wheel builds are not implied. |
| Runtime versus resolver identity | backend/RUNTIME-PROVENANCE.md; SOURCE-RUNTIME-QUALIFICATION.md; dev runtime hash and bootstrap guard | Exact source-built bundle and ABI/native validation pass locally; the pinned engine closure also passes the runtime guard during actual deployed SDK execution. Broad deployed package-suite acceptance remains separate. |
| Native dependency qualification | backend/NATIVE-PACKAGE-QUALIFICATION.md; native-package-resume raw results; dev cloud receipts | Existing matching native Pydantic/PyYAML WASM wheels execute under official guest Pyodide: 32 selected upstream pydantic-core tests and 261 selected PyYAML native cases pass. No compilation was needed. The packaged closure executes the real engine/SDK on Cloudflare; those package suites have not been rerun inside deployed Workers. |
| Dynamic workspace/source integrity | Actual helper import; module/resolution integrity checks | Fixed loose-workspace path passes. Solution/artifact-workspace remapping is rejected. Trusted production artifact selection is not integrated. |
| Canonical dispatch ownership/fencing | Actual stage/claim/consumer tests; journal PostgreSQL tests | Local pass. Transport journal survives reconnect only, not host loss; it is not a second queue. |
| Retained physical-route principal | backend/ROUTE-OWNERSHIP.md; results/route-ownership | Companion routes now bind original worker/launcher before returning results or ACKing, including retained-result and acknowledged replay. Five baseline failures repaired; six focused cases and38 PostgreSQL/Redis journal regressions pass. Same-launcher owner/session rotation remains allowed. Not deployed canonical cloud routing. |
| Eligibility before claim | `results/preclaim-eligibility.xml`; backend/PRECLAIM-ELIGIBILITY.md | Existing handler non-advertisement blocks unqualified providers. Selective runtime requirements are not persisted/integrated. |
| Unsupported rejection before effects | Fixture admission tests and local 422 duration observation | Fixed fixture only; not arbitrary-code classification or authorization. |
| Run/tenant credential scope and revocation | Two-tenant local SDK403s; dev cloud SDK receipts | Actual cloud-to-dev transport passes with run credentials, peer403, invalid token401 and ended fixture401. Parent signing key is absent. Canonical cloud dispatch credential issuance/revocation timing remains incomplete. |
| Live control-session authentication | backend/CONTROL-AUTH.md; canonical-refresh-control-auth.json | Real signed hello/reconnect and invalid-proof/replay/stale-socket rejection pass on isolated API. Empty handlers and no inventory kept execution unadmitted; this does not qualify physical-route adoption. |
| Signed control to isolate dispatch | backend/REMOTE-HANDLE-BOUNDARY.md; five ACK/callback characterization tests | Current remote pool requires Linux PID ACK; opaque ACK is not supported and is rejected after send. Combined signed-control/isolate dispatch remains incomplete. Shared control files were not changed without a coordinated handoff. |
| SDK refresh lifecycle | SDK-REFRESH, REFRESH-CHALLENGE, CONCURRENT-REFRESH and REFRESH-INTERRUPTION under backend/ | Local cold challenge/replay, mixed convergence and SDK waiter cancellation after actual API issuance pass. Terminal access/refresh revoked. Whole-execution/isolate interruption and cloud remain unqualified. |
| Fresh security boundary and contamination | Fresh cloud state probes; local simultaneous canaries; three deployed credentialed fresh-state runs | Builtins/env/module/tmp markers are absent before effects across actual fresh credentialed cloud runs. Positive fixed-fixture evidence, not complete hostile isolation or physical-termination acceptance. |
| Duplicate physical dispatch | Journal single-launch checks, transport-waiter JUnit, backend/TRANSPORT-INTERRUPTION.md and backend/PROVIDER-INTERRUPTION.md | Actual SDK waiter detachment and provider-process interruption retain one launch. Real interruption produces RemoteProtocolError/no result; explicit upstream recovery fails the run without redispatch. Whole-host recovery/detection remain unqualified. |
| Terminal idempotency/stale rejection | Canonical claim/consumer PostgreSQL tests | Durable terminal state preserved. Mid-callback notification/cache/accounting cleanup is not repaired by stale replay. |
| Parent-owned terminal execution identity | backend/RESULT-IDENTITY.md; before/after/canonical JUnit | Native and remote callbacks now bind execution_id and sync from the existing parent ExecutionInfo. Four baseline failures reproduced; 65 focused tests plus one actual canonical two-run PostgreSQL regression pass, Ruff/Pyright clean. A forged B identity on route A completes only A; B's running state/claim survives duplicate delivery. Not deployed canonical cloud acceptance. |
| Real successful result after declared worker loss | backend/WORKER-LOSS-RECOVERY.md; actual workerd SDK and canonical recovery service | Failed state/timestamp preserved across late success/lost ACK/reconnect. Access/refresh are 401 before delivery; one launch/upsert, no redispatch. Explicit upstream terminal-log drain persists two logs; repeat drains zero. Automatic loss detection and interrupted-host recovery are not proved. |
| Upstream retained terminal-event/ACK reuse | backend/RETAINED-RESULTS.md; protocol JUnit and canonical-refresh-retained-response.json | Actual local Worker/SDK result survives downstream response loss and lost ACK using upstream retention; one launch/log row, Success/value 70, access/refresh revoked. Control link remains simulated; live authentication, upstream-link loss and whole-host recovery unqualified. |
| Reconnect/reconciliation without duplicate effects | Lost-ACK local runs; postcommit fault matrix; RESPONSE-LOSS and RETAINED-RESULTS | Without a retained provider result, loss stays Running. The retained gateway path below recovers locally. Whole-host recovery and timely interrupted-run revocation remain unqualified; mid-callback cache/log boundaries persist. |
| I/O cancellation | Local/cloud before/after probes | Later observed I/O effect suppressed in bounded probes. No universal physical-termination receipt. |
| CPU cancellation and physical termination | Historical cloud catch; SOURCE-RUNTIME-QUALIFICATION.md and backend/RPC-TERMINATION-QUALIFICATION.md | Actual-workerd independent observers record later CPU effects after both fetch abort (1672.304 ms) and RPC-context cancellation (1666.335 ms). Local catch loop completes without quota exception; cloud catch remains separate evidence. No trusted physical-stop receipt exists. |
| Explicit runtime disposal alternative | backend/DISPOSAL-BOUNDARY.md; actual cf CLI local runtime/ABI receipt | WorkerStub and its entrypoint expose neither dispose symbol in the pinned local runtime. Python ABI positive control passes; no unsupported method or CPU-stop claim is substituted. This route cannot currently exercise physical termination. |
| Deadlines/provider CPU failure mapping | Outcome-mapping PostgreSQL fault matrix; actual cloud CPU exception | Mapping of reported outcome is tested; trusted physical deadline enforcement is not. |
| Worker interruption and cleanup | backend/PROVIDER-INTERRUPTION.md; canonical fixture cleanup and cloud retirement readbacks | Actual owned provider-container stop after SDK effect, explicit canonical loss recovery, credential rejection and log drain pass locally. Automatic/whole-host/deployed recovery and per-isolate physical-handle cleanup remain incomplete. Retired cloud allowance cannot be reused. |
| Oversized payload/memory exhaustion | Bounded transport tests; historical local/cloud memory artifacts | Negative probes exist. No measured safe per-run peak or capacity admission for current engine; synthetic 1 GiB fixture budget is not isolate capacity. |
| Cold/warm latency and concurrency | WORKERD-SDK-QUALIFICATION.md; historical warm pair/minimal cold-pair reproducer | New single cold and warm actual-workerd canonical SDK runs pass at 2 GiB process cap; 1 GiB fails OOM. Historical cold concurrent pair remains failed. No production latency or concurrency acceptance follows. |
| CPU/peak memory/bundle/SDK overhead/cost | Historical artifacts; NODE-MEMORY-QUALIFICATION.md and actual SDK memory receipt | Guest observed WASM capacity 43.25 MiB after real SDK execution, separate Node snapshots. Total Cloudflare memory, resident peak/CPU, full Linux pool comparison and complete per-1,000 economics remain missing. |
| Five-workload local Linux/workerd reference | WORKERD-BENCHMARK.md; both raw benchmark receipts | Byte-identical helper passes 15 samples per backend; Linux uses real signed control/fork. Operation wall times only, three samples each; no general speed claim, production cold/warm/CPU/cost or matching memory comparison. |
| Python↔JS marshaling overhead | backend/INTEROP.md and raw interop-local.json | Bounded local FFI/JSON microbenchmark now measured; null conversion mismatch explicit. Full SDK/cloud contribution remains unmeasured. |
| Linux compatibility | Focused engine/consumer tests and ordinary CI | Default Linux seams retained; every new candidate still requires its normal CI/review. |
| Current upstream reconciliation | backend/UPSTREAM-RECONCILIATION.md; results/upstream-reconcile | Main025f6c019 merged;129 native protocol/claim/capacity/result/log contracts pass. Packaged tracked Python:73 identical modules, six existing namespace adapters, no unexpected drift. External capacity is not an isolate execution API; live dev API remains2037ac0. |
| Reviewable upstream contribution | Public personal fork; draft PR #1161 | Published experimental source. Maintainer research review at f8cc75e does not accept later heads or the backend. |

## Current provider API review

The current [Dynamic Workers API](https://developers.cloudflare.com/dynamic-workers/api-reference/)
documents fresh `load()` versus cached `get()`. Its
[custom limits](https://developers.cloudflare.com/dynamic-workers/usage/limits/)
document CPU/subrequest exceptions. Cached `@cloudflare/workers-types`
5.20261031.1 experimental definitions expose WorkerStub entrypoint and Durable
Object-class access, with CPU/subrequest limits; no physical-stop method was
found on WorkerStub. `WorkflowInstance.terminate()` and stream-controller
`terminate()` belong to different contracts. Their names do not establish a
physical-stop operation for an in-flight Python Dynamic Worker. This bounded
API review does not prove that Cloudflare can never offer such a primitive.

The actual hostile cloud fixture, rather than missing API text alone, blocks
the required untrusted cancellation guarantee. A new provider primitive must
pass that counterexample before capability advertisement can change.

## Authority and next implementation boundary

Thomas subsequently authorized the isolated pve-t340 dev stack, cf CLI deployment
to the existing account and CT128 expansion. The 2026-11-02 bounded private
qualification window is now retired after 13 reserved requests, verified public
closure and temporary-secret deletion. The bounded dev UI remains available.
Continue implementation within that dev scope, reconciling a fresh bounded
experiment and cleanup before further cloud work; never reuse a retired ledger.
This does not authorize production routing, unlimited spending, new grants or
weakening runtime/security guarantees.

The next shared implementation is the persisted runtime requirement and provider
qualification comparison described in backend/PRECLAIM-ELIGIBILITY.md. It must
be coordinated with the other execution owner before editing shared placement,
capacity and recovery interfaces. A second scheduling service or a companion
boolean supplied by workflow code is not a substitute.

No result here authorizes production routing. Keep Linux the supported default
for arbitrary Python, CPU-heavy work, persistent services and agents. Short
trusted I/O workflows remain the most promising candidate after the missing
canonical/cloud and runtime guarantees are resolved.
