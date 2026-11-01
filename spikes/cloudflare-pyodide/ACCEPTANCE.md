# Backend acceptance ledger

Reconciled against preserved source/evidence on 2026-11-01, including the exact
publisher build and actual-workerd ABI/timed-cancellation receipts in SOURCE-RUNTIME-QUALIFICATION.md.
Matched Linux/workerd workloads are in WORKERD-BENCHMARK.md; the explicit
lost-worker/stale-result/log-drain boundary is in backend/WORKER-LOSS-RECOVERY.md.
The full backend objective is **not achieved**. The useful trusted subset is
experimental YELLOW; first-class arbitrary untrusted execution is RED under the
tested physical-termination contract. A source PR, an approved research review,
green CI or an HTTP 200 envelope cannot substitute for runtime acceptance.

| Required guarantee / artifact | Current authoritative evidence | Status and missing proof |
|---|---|---|
| Deployable Worker and exact setup | Wrangler configs, README, historical cloud version receipts in IMPLEMENTATION.md | Prototype exists; production provider is not enabled. |
| Current upstream engine and SDK, genuine run identity | WORKERD-SDK-QUALIFICATION.md; preserved Node and historical local receipts | Actual engine/SDK now passes cold and warm in CT128 workerd with canonical run identity; no deployed canonical execution or complete signed-dispatch integration. |
| Parameters, async execution, workspace imports, logs/errors | Current-engine cloud fixtures and local canonical receipts | Cloud fixtures use synthetic context; credentialed fixtures are local. |
| Actual canonical error and output channels | backend/WORKFLOW-FAILURE.md; failure-terminal receipt | Genuine ValueError and redacted structured logs persist/reconcile; stdout is absent in shared engine path. Runtime qualification deliberately still fails that assertion. |
| Integration/config reads and table writes | WORKERD-SDK-QUALIFICATION.md; actual guest API 200/peer 403, independent table readback 70 | Actual workerd integration SDK and canonical consumption pass. Synthetic encrypted secret decrypts correctly and result/log probes redact; OAuth and authenticated cloud-to-BiFrost end-to-end remain unqualified. |
| Dependency closure and pinned provenance | Runtime manifest/source hashes, packaged closure and module-response tests | Tested closure exists. Broad package compatibility and missing-wheel builds are not implied. |
| Runtime versus resolver identity | backend/RUNTIME-PROVENANCE.md; SOURCE-RUNTIME-QUALIFICATION.md; full-closure fingerprint and bootstrap guard | Exact source-built bundle matches existing hash; actual network-disabled workerd ABI/native validation passes. Deployed runtime still needs separate qualification. |
| Native dependency qualification | backend/NATIVE-PACKAGE-QUALIFICATION.md; native-package-resume raw results | Existing matching native Pydantic/PyYAML WASM wheels execute under official guest Pyodide: 32 selected upstream pydantic-core tests and 261 selected PyYAML native cases pass. No wheel compilation was needed. This is neither full package-suite nor actual Cloudflare wheel acceptance. |
| Dynamic workspace/source integrity | Actual helper import; module/resolution integrity checks | Fixed loose-workspace path passes. Solution/artifact-workspace remapping is rejected. Trusted production artifact selection is not integrated. |
| Canonical dispatch ownership/fencing | Actual stage/claim/consumer tests; journal PostgreSQL tests | Local pass. Transport journal survives reconnect only, not host loss; it is not a second queue. |
| Eligibility before claim | `results/preclaim-eligibility.xml`; backend/PRECLAIM-ELIGIBILITY.md | Existing handler non-advertisement blocks unqualified providers. Selective runtime requirements are not persisted/integrated. |
| Unsupported rejection before effects | Fixture admission tests and local 422 duration observation | Fixed fixture only; not arbitrary-code classification or authorization. |
| Run/tenant credential scope and revocation | Two-tenant SDK 403s, terminal 401s, no parent signing secret | Local actual API proof. Cloud credential transport/reachability untested. |
| Live control-session authentication | backend/CONTROL-AUTH.md; canonical-refresh-control-auth.json | Real signed hello/reconnect and invalid-proof/replay/stale-socket rejection pass on isolated API. Empty handlers and no inventory kept execution unadmitted; this does not qualify physical-route adoption. |
| Signed control to isolate dispatch | backend/REMOTE-HANDLE-BOUNDARY.md; five ACK/callback characterization tests | Current remote pool requires Linux PID ACK; opaque ACK is not supported and is rejected after send. Combined signed-control/isolate dispatch remains incomplete. Shared control files were not changed without a coordinated handoff. |
| SDK refresh lifecycle | SDK-REFRESH, REFRESH-CHALLENGE, CONCURRENT-REFRESH and REFRESH-INTERRUPTION under backend/ | Local cold challenge/replay, mixed convergence and SDK waiter cancellation after actual API issuance pass. Terminal access/refresh revoked. Whole-execution/isolate interruption and cloud remain unqualified. |
| Fresh security boundary and contamination | Fresh cloud state probes; local simultaneous builtins/env/module/tmp canaries | Positive fixed-fixture evidence, not complete hostile isolation acceptance. |
| Duplicate physical dispatch | Journal single-launch checks, transport-waiter JUnit and backend/TRANSPORT-INTERRUPTION.md | Actual local engine/SDK collection survives in-flight waiter detachment with one launch and canonical reconciliation. A dropped provider socket and whole-host recovery remain unqualified. |
| Terminal idempotency/stale rejection | Canonical claim/consumer PostgreSQL tests | Durable terminal state preserved. Mid-callback notification/cache/accounting cleanup is not repaired by stale replay. |
| Real successful result after declared worker loss | backend/WORKER-LOSS-RECOVERY.md; actual workerd SDK and canonical recovery service | Failed state/timestamp preserved across late success/lost ACK/reconnect. Access/refresh are 401 before delivery; one launch/upsert, no redispatch. Explicit upstream terminal-log drain persists two logs; repeat drains zero. Automatic loss detection and interrupted-host recovery are not proved. |
| Upstream retained terminal-event/ACK reuse | backend/RETAINED-RESULTS.md; protocol JUnit and canonical-refresh-retained-response.json | Actual local Worker/SDK result survives downstream response loss and lost ACK using upstream retention; one launch/log row, Success/value 70, access/refresh revoked. Control link remains simulated; live authentication, upstream-link loss and whole-host recovery unqualified. |
| Reconnect/reconciliation without duplicate effects | Lost-ACK local runs; postcommit fault matrix; RESPONSE-LOSS and RETAINED-RESULTS | Without a retained provider result, loss stays Running. The retained gateway path below recovers locally. Whole-host recovery and timely interrupted-run revocation remain unqualified; mid-callback cache/log boundaries persist. |
| I/O cancellation | Local/cloud before/after probes | Later observed I/O effect suppressed in bounded probes. No universal physical-termination receipt. |
| CPU cancellation and physical termination | Historical cloud catch; SOURCE-RUNTIME-QUALIFICATION.md and backend/RPC-TERMINATION-QUALIFICATION.md | Actual-workerd independent observers record later CPU effects after both fetch abort (1672.304 ms) and RPC-context cancellation (1666.335 ms). Local catch loop completes without quota exception; cloud catch remains separate evidence. No trusted physical-stop receipt exists. |
| Deadlines/provider CPU failure mapping | Outcome-mapping PostgreSQL fault matrix; actual cloud CPU exception | Mapping of reported outcome is tested; trusted physical deadline enforcement is not. |
| Worker interruption and cleanup | Local termination fixture, canonical fixture cleanup, cloud retirement readbacks | Full interrupted-attempt provider recovery and physical-handle cleanup remain incomplete. Retired cloud test allowance cannot be reused. |
| Oversized payload/memory exhaustion | Bounded transport tests; historical local/cloud memory artifacts | Negative probes exist. No measured safe per-run peak or capacity admission for current engine; synthetic 1 GiB fixture budget is not isolate capacity. |
| Cold/warm latency and concurrency | WORKERD-SDK-QUALIFICATION.md; historical warm pair/minimal cold-pair reproducer | New single cold and warm actual-workerd canonical SDK runs pass at 2 GiB process cap; 1 GiB fails OOM. Historical cold concurrent pair remains failed. No production latency or concurrency acceptance follows. |
| CPU/peak memory/bundle/SDK overhead/cost | Historical artifacts; NODE-MEMORY-QUALIFICATION.md and actual SDK memory receipt | Guest observed WASM capacity 43.25 MiB after real SDK execution, separate Node snapshots. Total Cloudflare memory, resident peak/CPU, full Linux pool comparison and complete per-1,000 economics remain missing. |
| Five-workload local Linux/workerd reference | WORKERD-BENCHMARK.md; both raw benchmark receipts | Byte-identical helper passes 15 samples per backend; Linux uses real signed control/fork. Operation wall times only, three samples each; no general speed claim, production cold/warm/CPU/cost or matching memory comparison. |
| Python↔JS marshaling overhead | backend/INTEROP.md and raw interop-local.json | Bounded local FFI/JSON microbenchmark now measured; null conversion mismatch explicit. Full SDK/cloud contribution remains unmeasured. |
| Linux compatibility | Focused engine/consumer tests and ordinary CI | Default Linux seams retained; every new candidate still requires its normal CI/review. |
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

No new cloud invocation, endpoint reopening, spending, runtime security changes,
persistent credential/grant or canary extension is currently authorized. The
approved bounded cloud experiment is retired. Local work can continue; deployed
acceptance needs a separately authorized target, exact bounded experiment and
cleanup obligations. Do not reactivate the old budget or weaken the goal.

The next shared implementation is the persisted runtime requirement and provider
qualification comparison described in backend/PRECLAIM-ELIGIBILITY.md. It must
be coordinated with the other execution owner before editing shared placement,
capacity and recovery interfaces. A second scheduling service or a companion
boolean supplied by workflow code is not a substitute.

No result here authorizes production routing. Keep Linux the supported default
for arbitrary Python, CPU-heavy work, persistent services and agents. Short
trusted I/O workflows remain the most promising candidate after the missing
canonical/cloud and runtime guarantees are resolved.
