# Backend acceptance ledger

Reconciled against source/evidence through `b9043a08b` on 2026-10-31.
The full backend objective is **not achieved**. The useful trusted subset is
experimental YELLOW; first-class arbitrary untrusted execution is RED under the
tested physical-termination contract. A source PR, an approved research review,
green CI or an HTTP 200 envelope cannot substitute for runtime acceptance.

| Required guarantee / artifact | Current authoritative evidence | Status and missing proof |
|---|---|---|
| Deployable Worker and exact setup | Wrangler configs, README, historical cloud version receipts in IMPLEMENTATION.md | Prototype exists; production provider is not enabled. |
| Current upstream engine and SDK, genuine run identity | `results/canonical-local.json`; `results/canonical-isolation-admission.json` | Passes actual **local** engine/SDK path, not deployed canonical execution. |
| Parameters, async execution, workspace imports, logs/errors | Current-engine cloud fixtures and local canonical receipts | Cloud fixtures use synthetic context; credentialed fixtures are local. |
| Actual canonical error and output channels | backend/WORKFLOW-FAILURE.md; failure-terminal receipt | Genuine ValueError and redacted structured logs persist/reconcile; stdout is absent in shared engine path. Runtime qualification deliberately still fails that assertion. |
| Integration/config reads and table writes | SDK benchmark and canonical tenant receipts with independent API readback | Local success; no authenticated cloud-to-BiFrost end-to-end proof. |
| Dependency closure and pinned provenance | Runtime manifest/source hashes, packaged closure and module-response tests | Tested closure exists. Broad package compatibility and missing-wheel builds are not implied. |
| Runtime versus resolver identity | backend/RUNTIME-PROVENANCE.md; actual full-closure fingerprint and bootstrap guard | Local runtime/ABI pin enforced before upstream import; cloud runtime still requires separate qualification. |
| Native dependency qualification | dependency-qualification.md | Procedure specifies native-first build/test/deploy. No native build or third-party upstream package-suite claim is made for the C++ startup failure: no Python package causes the minimal reproduction. |
| Dynamic workspace/source integrity | Actual helper import; module/resolution integrity checks | Fixed loose-workspace path passes. Solution/artifact-workspace remapping is rejected. Trusted production artifact selection is not integrated. |
| Canonical dispatch ownership/fencing | Actual stage/claim/consumer tests; journal PostgreSQL tests | Local pass. Transport journal survives reconnect only, not host loss; it is not a second queue. |
| Eligibility before claim | `results/preclaim-eligibility.xml`; backend/PRECLAIM-ELIGIBILITY.md | Existing handler non-advertisement blocks unqualified providers. Selective runtime requirements are not persisted/integrated. |
| Unsupported rejection before effects | Fixture admission tests and local 422 duration observation | Fixed fixture only; not arbitrary-code classification or authorization. |
| Run/tenant credential scope and revocation | Two-tenant SDK 403s, terminal 401s, no parent signing secret | Local actual API proof. Cloud credential transport/reachability untested. |
| SDK refresh lifecycle | SDK-REFRESH, REFRESH-CHALLENGE, CONCURRENT-REFRESH and REFRESH-INTERRUPTION under backend/ | Local cold challenge/replay, mixed convergence and SDK waiter cancellation after actual API issuance pass. Terminal access/refresh revoked. Whole-execution/isolate interruption and cloud remain unqualified. |
| Fresh security boundary and contamination | Fresh cloud state probes; local simultaneous builtins/env/module/tmp canaries | Positive fixed-fixture evidence, not complete hostile isolation acceptance. |
| Duplicate physical dispatch | Journal single-launch checks, transport-waiter JUnit and backend/TRANSPORT-INTERRUPTION.md | Actual local engine/SDK collection survives in-flight waiter detachment with one launch and canonical reconciliation. A dropped provider socket and whole-host recovery remain unqualified. |
| Terminal idempotency/stale rejection | Canonical claim/consumer PostgreSQL tests | Durable terminal state preserved. Mid-callback notification/cache/accounting cleanup is not repaired by stale replay. |
| Upstream retained terminal-event/ACK reuse | backend/RETAINED-RESULTS.md; protocol JUnit and canonical-refresh-retained-response.json | Actual local Worker/SDK result survives downstream response loss and lost ACK using upstream retention; one launch/log row, Success/value 70, access/refresh revoked. Control link remains simulated; live authentication, upstream-link loss and whole-host recovery unqualified. |
| Reconnect/reconciliation without duplicate effects | Lost-ACK local runs; postcommit fault matrix; RESPONSE-LOSS and RETAINED-RESULTS | Without a retained provider result, loss stays Running. The retained gateway path below recovers locally. Whole-host recovery and timely interrupted-run revocation remain unqualified; mid-callback cache/log boundaries persist. |
| I/O cancellation | Local/cloud before/after probes | Later observed I/O effect suppressed in bounded probes. No universal physical-termination receipt. |
| CPU cancellation and physical termination | `cpu-catch-cloud-fresh.json`; external CPU-abort evidence | **Contradicted**: later effects occur after abort/limit handling. Required physical stop is not established. |
| Deadlines/provider CPU failure mapping | Outcome-mapping PostgreSQL fault matrix; actual cloud CPU exception | Mapping of reported outcome is tested; trusted physical deadline enforcement is not. |
| Worker interruption and cleanup | Local termination fixture, canonical fixture cleanup, cloud retirement readbacks | Full interrupted-attempt provider recovery and physical-handle cleanup remain incomplete. Retired cloud test allowance cannot be reused. |
| Oversized payload/memory exhaustion | Bounded transport tests; historical local/cloud memory artifacts | Negative probes exist. No measured safe per-run peak or capacity admission for current engine; synthetic 1 GiB fixture budget is not isolate capacity. |
| Cold/warm latency and concurrency | Historical benchmarks; warm tenant receipt; minimal cold reproducer | Warm local pair passes. Cold local pair fails in native workerd without BiFrost or packages. No production p95/p99 claim. |
| CPU/peak memory/bundle/SDK overhead/cost | Historical raw benchmark/metric artifacts and bundle receipts | Measurements and estimates separated. Full Linux pool comparison, current peak/CPU and complete per-1,000 execution economics remain missing. |
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
