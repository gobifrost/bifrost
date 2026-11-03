# Backend acceptance ledger

Reconciled against preserved source/evidence on 2026-11-03, including the deployed
engine/SDK dev-stack receipts in [dev/README.md](dev/README.md) and the exact
publisher build and actual-workerd ABI/timed-cancellation receipts in SOURCE-RUNTIME-QUALIFICATION.md.
Matched Linux/workerd workloads are in WORKERD-BENCHMARK.md; the explicit
lost-worker/stale-result/log-drain boundary is in backend/WORKER-LOSS-RECOVERY.md.
Actual local provider-process interruption is in backend/PROVIDER-INTERRUPTION.md;
its explicit recovery is not automatic detection or per-isolate stop acceptance.
The full backend objective is **not achieved**. The useful trusted subset is
experimental YELLOW; arbitrary untrusted execution remains RED under unresolved
isolation, abnormal cancellation and resource-accounting guarantees. A source PR, an approved research review,
green CI or an HTTP 200 envelope cannot substitute for runtime acceptance.

Normal completion is distinct from abnormal cancellation: await required work,
finish the response/stream/RPC and dispose returned RPC object stubs when done.
Destroying a physical isolate after every successful job is not an acceptance
requirement. Worker lifecycle/reuse belongs to the platform; fresh per-run
security state still needs evidence. The current HTTP path returns responses,
not returned RpcTarget objects. See Cloudflare's
[RPC lifecycle](https://developers.cloudflare.com/workers/runtime-apis/rpc/lifecycle/).
HTTP [waitUntil](https://developers.cloudflare.com/workers/runtime-apis/context/#waituntil)
is bounded to 30 seconds after response/disconnect, not a durable job owner.
Settled invocation/resource cleanup and synchronous CPU cessation are separate
proofs. No Queues/DO/D1 orchestration is introduced for this purpose.
The detailed assessment and preserved experiments remain in backend/LIFECYCLE.md.

Local HTTPS integration delta: backend/CANONICAL-HTTPS.md records two actual
engine/SDK workflows with run-scoped identity, controlled synthetic vendor
HTTPX requests and canonical Failed/Success/replay/cleanup. Independent destination
evidence contains exactly four approved forwards. This fixed local candidate is
not general external HTTPS admission or deployed canonical acceptance; the ordinary
private adapter still rejects that requirement. The transport redirect repair
is confined to the candidate. CPU, memory and cancellation gaps remain unchanged.
The named-policy follow-up in backend/HTTPS-POLICY-BINDING.md passes the same
canonical flow with an explicit HTTPS declaration and immutable trusted tenant
policy. Default configuration still rejects HTTPS; pre-dispatch binding is not
upstream preclaim eligibility or public authentication.
backend/HTTPS-DEADLINE.md adds a causal local in-flight vendor write test:
Timeout/revocation and reserved capacity precede destination commit; new writes
are refused and late success cannot overwrite Timeout. The pre-deadline effect
still commits after release, explicitly preserving the uncertainty boundary.
The pre-deadline HTTP body-cancellation qualification in backend/HTTPS-DISCONNECT.md
fails locally: cleanup is not delivered before release and a follow-up vendor
effect occurs. Direct stream-cancel unit coverage does not qualify incoming HTTP
delivery. No child-abort, physical-stop or deployed conclusion follows; this
remains a material cancellation/admission gap.
backend/CAPABILITY-REVOCATION.md independently proves an explicit companion close
under the actual PostgreSQL owner lock, including observed rotation blocking,
stale-owner refusal, no duplicate close and actual Python follow-up410. Its ACK
explicitly proves neither physical stop nor execution settlement. It is not wired
to public/native cancellation and does not repair incoming disconnect delivery.
backend/CANONICAL-CANCEL-REQUEST.md combines actual engine/SDK execution with the
authorized cancellation API: peer403, ended-token401, duplicate200/Cancelling,
live credential200, capability-only revocation and reserved capacity through
settlement. Finite natural completion becomes Success through upstream's existing
result writer. Cancelled reporting and physical termination remain unqualified.
backend/COOPERATIVE-CANCELLATION.md adds actual local Pyodide RPC controls:
normal/event cancellation/suppression pass, but suppression after canceling a
held pyfetch hangs. The successful HTTP cancel subcase settles only its tracked
coroutine; the accepted vendor write still commits after release. This failed
qualification does not enable admission or canonical Cancelled reporting.

Recent evidence delta: the common startup repair candidate now has 12 mocked,
six real-Redis and one signed Linux engine/SDK/retirement qualification; it remains
an isolated overlay, not an applied shared-source repair. Local request-context
tests now observe closure of actual in-flight HTTP and a retained positive control.
Finite CPU continuation is separately observed with a refused post-deadline effect.
These advance specific contract guarantees without qualifying deployed CPU stop,
memory/isolation or public Pyodide startup. Fresh dev loader packaging also now
includes its SDK cleanup JavaScript dependency; that is assembly evidence only.

| Required guarantee / artifact | Current authoritative evidence | Status and missing proof |
|---|---|---|
| Deployable Worker and exact setup | Wrangler configs, README, historical cloud version receipts in IMPLEMENTATION.md | Prototype exists; production provider is not enabled. |
| Current upstream engine and SDK, genuine run identity | WORKERD-SDK-QUALIFICATION.md; dev/cloud-sdk-success.json; dev/PUBLIC-URL.md; dev/STREAM-LOGS.md | Original four cloud runs plus separately pinned public-URL and stream-log runs pass with genuine run-scoped identity against CT128 dev API. No deployed canonical claim/terminal execution or complete signed-dispatch integration. |
| Parameters, async execution, workspace imports, logs/errors | Current-engine cloud fixtures, local canonical receipts and dev/cloud-sdk-success.json | Deployed credentialed async workflows load pinned workspace helpers, return values and capture redacted structured logs. Canonical cloud error/terminal persistence remains unqualified. |
| Public execution URL versus SDK transport | dev/PUBLIC-URL.md; results/public-url local and cloud receipts | Explicit trusted metadata differs from the restricted virtual SDK endpoint. Local legacy/explicit controls pass; deployed genuine run-scoped SDK integration read/table write passes with attacker URL overrides ignored. Ended access/refresh401; executor closed and secret removed. Canonical cloud dispatch and physical termination remain unqualified. |
| Actual canonical error and output channels | backend/CANONICAL-FAILURE-REPAIRED.md; results/canonical-failure-repaired/v4.json | New current-upstream local workerd claim/consumer run persists Failed/ValueError and11 redacted logs, including independently checked stdout; lost-ACK replay preserves row IDs and one launch, credentials401. Old failed receipt is unchanged. Not deployed canonical transport or physical-stop acceptance. |
| Shared engine stdout/stderr repair | backend/STREAM-CAPTURE.md; dev/STREAM-LOGS.md; backend/CANONICAL-FAILURE-REPAIRED.md | Four baseline failures repaired;37 native tests, guest Pyodide and actual deployed engine/SDK succeed. Cloud success stdout/stderr persist through existing recovery; new local canonical failure stdout persists through normal consumer/replay. Old failed receipt is retained separately. |
| Integration/config reads and table writes | WORKERD-SDK-QUALIFICATION.md; dev/cloud-sdk-success.json | Actual deployed SDK config/integration reads and table writes pass against the isolated dev API, including encrypted synthetic secret decryption/redaction, peer403 and independent table70. OAuth and canonical end-to-end cloud dispatch remain unqualified. |
| Dependency closure and pinned provenance | Runtime manifest/source hashes, packaged closure and module-response tests | Tested closure exists. Broad package compatibility and missing-wheel builds are not implied. |
| Upstream SDK #1217 drift / TLS | backend/SDK1217-QUALIFICATION.md; backend/SDK1217-EXECUTION.md; backend/SDK-CLOSE.md; backend/SDK-REFRESH-CLOSE.md; raw receipts | Exact SDK139596497 with unchanged certifi2026.4.22 pure wheel passes real local config/table operations, explicit refresh/redaction, normal close and canonical replay/peer/revocation. The preserved interrupted-close control left sync open; OWNED-SDK-CLOSE.md repairs cooperative waiter cancellation with a retained task. Platform interruption remains unqualified. TRANSPORT-OPTIONS.md preserves 26 actual local constructor cases accepting ignored TLS/proxy/socket options. CAPABILITY-CONTRACT.md now rejects unsupported trusted-host declarations before binding/dispatch; discovery/enforcement of undeclared dynamic requirements is not implemented. Custom TLS, automatic challenge paths and deployed execution remain unqualified. Earlier receipts keep their pins. |
| Runtime versus resolver identity | backend/RUNTIME-PROVENANCE.md; SOURCE-RUNTIME-QUALIFICATION.md; dev runtime hash and bootstrap guard | Exact source-built bundle and ABI/native validation pass locally; the pinned engine closure also passes the runtime guard during actual deployed SDK execution. Broad deployed package-suite acceptance remains separate. |
| Native dependency qualification | backend/NATIVE-PACKAGE-QUALIFICATION.md; native-package-resume raw results; dev cloud receipts | Existing matching native Pydantic/PyYAML WASM wheels execute under official guest Pyodide: 32 selected upstream pydantic-core tests and 261 selected PyYAML native cases pass. No compilation was needed. The packaged closure executes the real engine/SDK on Cloudflare; those package suites have not been rerun inside deployed Workers. |
| Dynamic workspace/source integrity | Actual helper import; module/resolution integrity checks | Fixed loose-workspace path passes. Solution/artifact-workspace remapping is rejected. Trusted production artifact selection is not integrated. |
| Canonical dispatch ownership/fencing | Actual stage/claim/consumer tests; journal PostgreSQL tests | Local pass. Transport journal survives reconnect only, not host loss; it is not a second queue. |
| Retained physical-route principal | backend/ROUTE-OWNERSHIP.md; results/route-ownership | Companion routes now bind original worker/launcher before returning results or ACKing, including retained-result and acknowledged replay. Five baseline failures repaired; six focused cases and38 PostgreSQL/Redis journal regressions pass. Same-launcher owner/session rotation remains allowed. Not deployed canonical cloud routing. |
| Eligibility before claim | `results/preclaim-eligibility.xml`; backend/PRECLAIM-ELIGIBILITY.md | Existing handler non-advertisement blocks unqualified providers. Selective runtime requirements are not persisted/integrated. |
| Unsupported rejection before effects | Fixture admission tests; backend/REQUEST-PREFLIGHT.md | Private request byte/identity validation now precedes native context writes, route reservation and collection. ASCII, escaped Unicode and combined manifest/context overflow cases pass, with a near-256 KiB positive unit case. Not arbitrary-code classification, preclaim eligibility or maximum-size Pyodide execution acceptance. |
| Run/tenant credential scope and revocation | Two-tenant local SDK403s; dev cloud SDK receipts | Actual cloud-to-dev transport passes with run credentials, peer403, invalid token401 and ended fixture401. Parent signing key is absent. Canonical cloud dispatch credential issuance/revocation timing remains incomplete. |
| Live control-session authentication | backend/CONTROL-AUTH.md; canonical-refresh-control-auth.json | Real signed hello/reconnect and invalid-proof/replay/stale-socket rejection pass on isolated API. Empty handlers and no inventory kept execution unadmitted; this does not qualify physical-route adoption. |
| Signed control to isolate dispatch | backend/OPAQUE-WORKFLOW-HANDLES.md; backend/PRIVATE-HTTP-POOL.md; actual local engine/SDK receipts | Explicit experimental profile supports opaque workflow handles through the actual native pool; Linux defaults remain intact. Private canonical claim/context/result paths pass. Public host still rejects the profile; combined signed startup, dispatch and registration remain unqualified. |
| Runtime inventory and registration cache | backend/STARTUP-INVENTORY.md; actual probe/cache/engine receipts | Real local Python Worker reports pinned ABI and 11 observed packages; unchanged upstream Redis projection/readback/cleanup passes and owned client closes at retirement. Package mutation and full native startup remain refused. Cache online status is not scheduler admission or qualified memory/cancellation control. |
| SDK refresh lifecycle | SDK-REFRESH, REFRESH-CHALLENGE, CONCURRENT-REFRESH, REFRESH-INTERRUPTION and REFRESH-REDACTION under backend/ | Local real refresh/challenge/replay, mixed convergence and waiter cancellation pass. Original/replacement access and stable run refresh values are redacted in canonical stdout/logs/error; peer, lost-ACK replay and revocation pass. Whole-execution/isolate interruption and cloud remain unqualified. |
| Fresh security boundary and contamination | Fresh cloud state probes; local simultaneous canaries; three deployed credentialed fresh-state runs | Builtins/env/module/tmp markers are absent before effects across actual fresh credentialed cloud runs. Positive fixed-fixture evidence, not complete hostile isolation or physical-termination acceptance. |
| Duplicate physical dispatch | Journal single-launch checks, transport-waiter JUnit, backend/TRANSPORT-INTERRUPTION.md and backend/PROVIDER-INTERRUPTION.md | Actual SDK waiter detachment and provider-process interruption retain one launch. Real interruption produces RemoteProtocolError/no result; explicit upstream recovery fails the run without redispatch. Whole-host recovery/detection remain unqualified. |
| Terminal idempotency/stale rejection | Canonical claim/consumer PostgreSQL tests | Durable terminal state preserved. Mid-callback notification/cache/accounting cleanup is not repaired by stale replay. |
| Parent-owned terminal execution identity | backend/RESULT-IDENTITY.md; before/after/canonical JUnit | Native and remote callbacks now bind execution_id and sync from the existing parent ExecutionInfo. Four baseline failures reproduced; 65 focused tests plus one actual canonical two-run PostgreSQL regression pass, Ruff/Pyright clean. A forged B identity on route A completes only A; B's running state/claim survives duplicate delivery. Not deployed canonical cloud acceptance. |
| Real successful result after declared worker loss | backend/WORKER-LOSS-RECOVERY.md; actual workerd SDK and canonical recovery service | Failed state/timestamp preserved across late success/lost ACK/reconnect. Access/refresh are 401 before delivery; one launch/upsert, no redispatch. Explicit upstream terminal-log drain persists two logs; repeat drains zero. Automatic loss detection and interrupted-host recovery are not proved. |
| Upstream retained terminal-event/ACK reuse | backend/RETAINED-RESULTS.md; protocol JUnit and canonical-refresh-retained-response.json | Actual local Worker/SDK result survives downstream response loss and lost ACK using upstream retention; one launch/log row, Success/value 70, access/refresh revoked. Control link remains simulated; live authentication, upstream-link loss and whole-host recovery unqualified. |
| Reconnect/reconciliation without duplicate effects | backend/RETAINED-HTTP.md; backend/PREACK-LOSS.md; backend/RETAINED-CONTRACT.md | Real local engine/SDK stream retained through EOF while original collector loses ACK; canonical ACK loss/current-owner reconnect preserves state/log IDs and one POST. Without trusted terminal evidence, capacity remains uncertain. Retainer death, provider TCP disconnect, deployed retention and whole-host recovery remain unqualified. |
| I/O cancellation | backend/HTTP-CONTEXT-LIFETIME.md; backend/RPC-CONTEXT-LIFETIME.md; historical cloud probes | Actual local Python pyfetch gate is held before caller completion; native caller tail settles and the independent HTTP connection closes. Retained waitUntil control stays open and completes after release. No downstream effect within 500 ms; no universal or deployed CPU-stop proof. |
| Native completion/tail observation | backend/TAIL-OBSERVER-QUALIFICATION.md; preserved v1/v2 receipts | Retained local observer/handle control observes tails after waitUntil and Python completion. Controlled I/O abort reports outcome ok and timing fields zero. This is event-order evidence, not a cancellation classification, CPU measurement or cloud run. |
| CPU cancellation and physical termination | Historical cloud catch; SOURCE-RUNTIME-QUALIFICATION.md and backend/RPC-TERMINATION-QUALIFICATION.md | Actual-workerd independent observers record later CPU effects after both fetch abort (1672.304 ms) and RPC-context cancellation (1666.335 ms). Local catch loop completes without quota exception; cloud catch remains separate evidence. No trusted physical-stop receipt exists. |
| CPU continuation with capability expiry | backend/CPU-EGRESS-DEADLINE.md; actual finite local CPU/HTTP observer receipt | CPU loop completes after 1813 ms; a parent-clock 500 ms diagnostic gate refuses its effect with410, while the control write succeeds. No timer interrupts Python. This is effect containment, not canonical deadline reset, CPU-stop or deployed resource enforcement. |
| Transactional common startup candidate | backend/STARTUP-CANDIDATE.md; backend/STARTUP-REDIS.md; backend/STARTUP-LINUX.md | 12 mocked cases, six actual Redis cases and one signed Linux engine/SDK workflow plus retirement pass under a candidate API mount. Shared repair remains unapplied pending coordination/review. Pyodide configure negotiation and public admission remain unqualified. |
| Explicit runtime disposal alternative | backend/RPC-RESULT-LIFECYCLE.md; historical handle-disposal probe | Returned Python RPC Maps/promises have working disposers locally; primitive results do not. A causally gated pending call still completes after promise disposal while its parent context remains alive. Loader handles are different objects. No undocumented terminate API is required for normal success. |
| Deadlines/provider CPU failure mapping | backend/LATE-ADAPTER.md; backend/DEADLINE-RECOVERY.md; outcome-mapping fault matrix | Actual local canonical Timeout/revocation precedes finite late success; reservation stays held, late SDK write is denied, state/log IDs unchanged, lost timeout ACK recovers while original failure remains readable. CPU enforcement and interruption outcomes remain separate deployed gaps. |
| Worker interruption and cleanup | backend/PROVIDER-INTERRUPTION.md; canonical fixture cleanup and cloud retirement readbacks | Actual owned provider-container stop after SDK effect, explicit canonical loss recovery, credential rejection and log drain pass locally. Automatic/whole-host/deployed recovery and per-isolate physical-handle cleanup remain incomplete. Retired cloud allowance cannot be reused. |
| Oversized payload/memory exhaustion | backend/REQUEST-PREFLIGHT.md; backend/OUTPUT-BUDGET.md; backend/BODY-BUDGET.md; backend/MEMORY-PRESSURE.md; raw receipts | Exact request budget is checked before native bookkeeping. Actual engine/SDK output failures preserve effects, canonical reporting and peer completion; bounded drain overflow stays uncertain. Local real workflow allocated 160 MiB successfully, so this emulator evidence does not qualify deployed 128 MiB enforcement. Deployed exhaustion, maximum-size execution and measured isolate peak/capacity remain gaps. |
| Cold/warm latency and concurrency | WORKERD-SDK-QUALIFICATION.md; historical warm pair/minimal cold-pair reproducer | New single cold and warm actual-workerd canonical SDK runs pass at 2 GiB process cap; 1 GiB fails OOM. Historical cold concurrent pair remains failed. No production latency or concurrency acceptance follows. |
| CPU/peak memory/bundle/SDK overhead/cost | Historical artifacts; NODE-MEMORY-QUALIFICATION.md and actual SDK memory receipt | Guest observed WASM capacity 43.25 MiB after real SDK execution, separate Node snapshots. Total Cloudflare memory, resident peak/CPU, full Linux pool comparison and complete per-1,000 economics remain missing. |
| Five-workload local Linux/workerd reference | WORKERD-BENCHMARK.md; both raw benchmark receipts | Byte-identical helper passes 15 samples per backend; Linux uses real signed control/fork. Operation wall times only, three samples each; no general speed claim, production cold/warm/CPU/cost or matching memory comparison. |
| Five-workload deployed frontier | CLOUD-BENCHMARK.md; results/cloud-benchmark | Five fresh children,15 correctness samples,5.2–10.5s measured provider round trips. CPU-only clock did not advance; CPU/peak/billed cost remain null. Conditional creation-only estimate$2/1,000 after allowance. No warm-isolate/concurrency or matched current Linux cold-start acceptance. |
| Python↔JS marshaling overhead | backend/INTEROP.md and raw interop-local.json | Bounded local FFI/JSON microbenchmark now measured; null conversion mismatch explicit. Full SDK/cloud contribution remains unmeasured. |
| Linux compatibility | Focused engine/consumer tests and ordinary CI | Default Linux seams retained; every new candidate still requires its normal CI/review. |
| Upstream source reconciliation | backend/UPSTREAM-RECONCILIATION.md; recent exact-source local receipts | Historical main025f6c019 merge/parity evidence is preserved. Recent local native tests explicitly pin API1d33150 and packaged engine f49d4ba; those receipts do not assert current upstream-main parity. External capacity is not an isolate execution API. |
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

The [custom-limit documentation](https://developers.cloudflare.com/dynamic-workers/usage/limits/)
describes an exception at an invocation CPU/subrequest limit. General
[Workers limits](https://developers.cloudflare.com/workers/platform/limits/#cpu-time)
separately describe termination for consistently exceeded CPU limits. A caught
custom-limit exception does not prove absent plan enforcement or infinite CPU.
Keep invocation limit, plan bound, local emulator behavior and actual deployed
behavior separate. The failed hostile fixtures remain evidence about their tested
arrangement. Requalification must use supported completion/disposal/limits and
scoped capability revocation, with independent effect/accounting observations;
do not require an undocumented kill method or equate returned requests with stop.

## Authority and next implementation boundary

Thomas subsequently authorized the isolated pve-t340 dev stack, cf CLI deployment
to the existing account and CT128 expansion. The 2026-11-02 bounded private
qualification window is now retired after 13 reserved requests, verified public
closure and temporary-secret deletion. Dev UI availability is historical, not
freshly asserted here. Current continuation is restricted to existing CT128 local
qualification: no new cloud invocation/deployment, canary use, spending, credentials,
grants or runtime/security-setting changes. Retired allowances cannot be reused.
Further cloud acceptance needs applicable explicit authority; local evidence
does not authorize production routing or weakened guarantees.

Private companion contract work now includes automatic terminal delivery,
deadline/receipt recovery, real retained-stream adoption and input preflight.
Runtime inventory and the registration-cache projection are characterized;
remaining startup configuration/control operations are unqualified. Shared placement/capacity
requirements in backend/PRECLAIM-ELIGIBILITY.md still require coordination with
the other execution owner before edits. A second scheduling service or a workflow
supplied eligibility boolean is not a substitute.

No result here authorizes production routing. Keep Linux the supported default
for arbitrary Python, CPU-heavy work, persistent services and agents. Short
trusted I/O workflows remain the most promising candidate after the missing
canonical/cloud and runtime guarantees are resolved.
