# Compact experiment log

## Finite late result settles an invocation after logical Timeout, 2026-11-03

Hypothesis: validated normal completion/EOF can release a held opaque handle
after canonical Timeout, with no per-job kill or outcome overwrite. Add a fixed
40-second async workflow, keep provider alive, and deliver the original30-second
cutoff with the owned native task. Real SDK write70 independently read; native
deadline persists Timeout/401 while collection stays pending. Actual engine
returns Success/70 (wall40137ms); late log upload410. First settlement ACK lost:
handle stays held. Replay through real pool/consumer releases it; Timeout,
completed_at and log IDs unchanged, one launch/write. Provider independently
still running/no OOM before harness cleanup. V1 four phases exit0, supervisor
102.800s, Ruff/Pyright pass; unchanged deadline-helper ten-case proof retained.
Command: `ct128-canonical-failure.py --late-settlement`, assigned CT128 packet
in backend/LATE-SETTLEMENT.md. Fixture-owned config teardown avoids using the
revoked run credential. Raw results/late-settlement and source-bound verifier.
No cloud/admission or native shared-source changes, no arbitrary CPU-stop claim.
Next: reusable transport with immutable per-attempt state and same-relay
concurrency, retaining upstream authority and current admission holds.

## Owned per-attempt deadline delivery without worker-wide failure, 2026-11-03

Hypothesis: the native companion can deliver an authorized original cutoff through
the real consumer without clearing unresolved physical work or killing the relay.
V1 explicit delivery passes real Timeout/access+refresh401/replay after SDK70.
V2 adds owned timer and lost post-commit receipt; runtime fails because the
shielded AttemptResult awaitable is not a Future for asyncio.wait. Preserve that
receipt/error; V3 passes the retained transport Task and rejects wrong types.
A separate real20ms scheduler-stall test exposes a deadline race (1/1 fail);
recheck the immutable UTC cutoff before accepting timely completion. No clock
patch, timeout extension or diagnostic suppression. V3 all four runtime phases
exit0, supervisor93.280s, live Ready relay, upstream Timeout/one launch/log,
post-commit receipt loss propagated/replayed, original handle/timing/Busy resource
obligation held. Ten native helper tests plus Ruff/Pyright pass. Command:
`ct128-canonical-failure.py --attempt-deadline --revision 3`; artifact
backend/ATTEMPT-DEADLINE.md, results/attempt-deadline and source-bound verifier.
No native shared-source or cloud/admission change. Next: actual late result/EOF
settles the held handle after logical Timeout without overwriting it; deployed
physical enforcement remains a separate gate.

## Existing API automatic recovery with real heartbeat silence, 2026-11-03

Hypothesis: canonical relay-loss recovery needs no new queue, but healthy relay
heartbeats cannot detect a lost child invocation. Fixed automatic-loss mode
reuses the real API-hosted dispatch reconciler; no new control loop, direct
recovery call, clock patch, heartbeat backdating or altered30-second threshold.
After real SDK write70 and owned provider stop, the negative control keeps the
relay heartbeating and observes Running for46.269s. Stop only its heartbeat/
owner renewal: real API control marks worker lost/heartbeat_lost and run Failed
after30.182s, PostgreSQL heartbeat age33.586s. Stale-owner duplicate rejected,
access+refresh401, one launch, no result envelope, redacted log drained once;
native physical obligation remains held. Command: `ct128-canonical-failure.py
--automatic-loss`, exact CT128 packet path in backend/AUTOMATIC-RECOVERY.md.
All four phases exit0, supervisor139.114s; Ruff/Pyright pass. Post-run source and
API mount readbacks match upstream1d33150's control/liveness/recovery files.
Artifact: results/automatic-loss and verifier. Updated CONTRACT.md separates host
health, canonical outcome and physical settlement. This does not qualify full
pool startup or per-invocation failure/deadline handling; no cloud/admission or
native shared-source changes. Next is that live-relay per-invocation gap.

## Streamed opaque provider interruption without a terminal event, 2026-11-03

Hypothesis: the real native opaque pool can preserve canonical semantics when
the provider disappears after an SDK effect but before a terminal result reaches
the launcher. Reuse the fixed integration-secret interruption workload through
early accepted/result streaming; stop only the owned local cf CLI provider after
independent document70. V1 preflight Ruff caught an out-of-scope execution_id;
corrected to str(execution.id) in a separate packet. V2 Ruff passes; Pyright first
exhausted its Node heap under512 MiB, then passes within a checker-only1 GiB/
768 MiB old-space bound, no diagnostic or provider change. Actual runtime command:
`ct128-canonical-failure.py --stream-interruption --revision 2` in the assigned
CT128 packet. All four phases exit0, supervisor63.397s, provider exited143/PID0,
no OOM. Actual stream RemoteProtocolError/no result; canonical Running then
upstream explicit recovery Failed, durable SDK effect70, access+refresh401,
redacted log drained once, duplicate reuses failed receipt/one launch. Native
invocation identity/deadline/Busy obligation stays held after canonical failure.
Artifacts: backend/STREAM-PROVIDER-INTERRUPTION.md, raw v2 receipt and diagnosed
preflight/quality records under results/stream-interruption. No cloud or physical
isolate-stop claim. Next: automatic observation/reconciliation of uncertain
invocations through the existing control plane, without enabling admission.

## Native opaque inventory uncertainty, 2026-11-03

Hypothesis: Linux inventory conventions still bypass the opaque cancellation
boundary. New regressions reproduce two failures on the published pool: omitted
inventory fabricates a crash result/releases the route, and rejected inventory
sends Linux terminate. Guard omission after owner fencing; reject unowned live
opaque entries without invented commands; retain completed terminal replay.
Three bounded fail-first CT128 runs preserve intermediate evidence: v1 2/3 fail
then42 pass; v2 adds terminal replay,1/4 fail then43 pass; v3 adds fence priority,
1/5 fail then44 pass. Ruff/Pyright pass; all owned test containers stop, volumes
preserved. Command: `ct128-inventory-uncertainty.py --revision 3` under the exact
assigned guest paths in backend/INVENTORY-UNCERTAINTY.md. Artifacts include raw
JUnit/receipts and exact packet/source hashes. Historical live verifiers bind
their original API commits, not this new source. No cloud, runtime/admission or
production change. Next: interruption before any terminal result reaches the
launcher, retaining canonical recovery and unresolved provider obligations.

## Accepted stream loss with retained native result, 2026-11-03

Hypothesis: a real broken accepted stream can preserve the original native
invocation and reconcile an authenticated retained terminal event without a
second launch or renewed deadline. The TCP fixture forwards accepted, retains
the real engine/SDK result through upstream Launcher, then closes without the
terminal frame/chunked EOF. `ct128-canonical-failure.py --stream-loss` v1 passes:
RemoteProtocolError, canonical Running, busy invocation retained, duplicate receipt,
owner rotation/stale rejection, Success/70 through real pool/consumer, lost ACK,
access+refresh401 and cleanup. Control reconnect is explicitly simulated.
Standalone Pyright initially failed from absent native/companion import context
and conditional bindings; added an extends-only fixture import config and made
deadline bindings explicit, preserving diagnostics and the v1 artifact. V2 uses
`--stream-loss --revision 2`, with separate source/receipt and Ruff/Pyright checks.
Artifacts: backend/STREAM-LOSS-RECONCILIATION.md, results/stream-loss, source-bound
verifier. Next: interruption without a retained terminal event. No cloud calls,
untrusted admission, physical-stop inference or changes to shared native sources.

## Prompt opaque acceptance with awaited response lifetime, 2026-11-03

Hypothesis: the real pool can bind an invocation before completion without an
immortal waitUntil or a replacement execution authority. Added bounded two-frame
NDJSON transport, strict native identity/order/EOF collection, and an opt-in
fixed stream-pool probe. Command: `timeout 430 python3
/opt/bifrost-tests/pyodide-ddf316c1/inbox/stream-pool-v1/ct128-canonical-failure.py
--stream-pool`. Exit 0, local cf CLI/workerd engine/SDK, early ACK while collection
pending, canonical Success/70, one launch/write, lost terminal ACK replay,
credential 401 and cleanup pass. Supervisor 64.966 s, not startup/CPU latency.
Three Node framing tests and four guest Python tests, Ruff/Pyright pass. A unit
negative control explicitly observes work continuing after stream cancellation.
Artifact: backend/STREAMED-INVOCATION.md, results/stream-pool/v1.json and focused
checks. Next: lost-stream uncertainty after acceptance against retained ownership.
No cloud invocation, public admission, physical-stop claim or resource change.

## Actual owned provider interruption after SDK write, 2026-11-01

Hypothesis: a real provider-process loss must not fabricate a result or relaunch
uncertain effects. Change: fixed async delay after real upsert, independent API
document readback, bounded marker to the existing supervisor, stop only the owned
provider container, then unchanged upstream lost-worker recovery/log drain.
Command: guest ct128-workerd-provider-interruption.py; exact Compose argv in
results/provider-interruption. Initial attempt failed handoff-directory ownership
before interruption. Corrected only the new owned output UID/GID and atomic marker.
Result: actual RemoteProtocolError/no envelope; Running before recovery, Failed
after; durable value 70; one launch/no dispatch; access/refresh 401; one redacted
pre-interruption log recovered, repeated drain zero. Receipt SHA-256
`00b1fcb9562540337d462a4253139390008ec4883203046c361b28e5669a6d87`.
Docker Ruff/offline verifiers pass; containers stopped, volumes retained, zero
cloud. Killing this provider harness is not per-isolate physical-stop proof.
Next: coordinated runtime contract and automatic detection/deployed acceptance;
do not infer those guarantees from this explicit local recovery operation.

## Real SDK success after declared lost-worker recovery, 2026-11-01

Hypothesis: canonical lost-worker failure must outrank a late successful actual
workerd result, including after lost-ACK/reconnect replay. Change: opt-in fixture
calls unchanged end_runs_of_lost_worker/announce_lost_runs after collecting real
SDK success but before consuming it. Command: guest ct128-workerd-loss-recovery.py
(exact Compose argv in results/workerd-loss-recovery). Both runs preserve Failed,
null result and completion timestamp; access/refresh 401 before late delivery;
one physical launch/upsert, no new dispatch. Initial run sampled zero persisted
logs. Follow-on calls the existing terminal-log drainer: two rows persisted,
second drain zero, identical IDs. Both receipts retained; this is explicit
recovery-service fault injection, not physical interruption/detection acceptance.
Final SHA-256 `62bcd195b85b1d5a9d63713e4ffdbc8a3c514e90f43662b1af05f94a22bfce97`.
Docker Ruff and offline verifiers pass; owned containers stopped, volumes retained.
Next: coordinated runtime boundary and remaining automatic-loss/physical-stop/
deployed acceptance gates, without cloud reopening or new authority.

## CT128 matched Linux benchmark, 2026-11-01

Hypothesis: the byte-identical benchmark helper can run through the actual Linux
control/template path, providing a local reference and clarifying helper logging.
Change: opt-in benchmark in linux-control-reference.py; existing signed control,
canonical execution/credentials and pinned workspace loading remain upstream.
Command: guest `python3 .../inbox/linux-benchmark-v1/ct128-linux-benchmark.py`.
Result: 15 correctness samples pass, three independent document readbacks equal
70, one real command/PID ACK/result, terminal ACK, revoked credential 401, native
template/worker cleanup. The same helper warnings are absent on Linux, confirming
the current filter boundary rather than a Pyodide-only observation. Raw timings
are noisy three-sample local runs, not a platform performance/cost claim.
Artifact: results/linux-benchmark; receipt SHA-256
`b477d4aee3462ba5c69bf42dc8bf184dc6590ef30845ec8fa02fbec6b8ea50ae`.
Targeted Docker Ruff passes; containers stopped, volumes retained, zero cloud.
Next: remaining contract/admission gaps and SDK/interop attribution. Physical
termination remains unqualified; deployment and untrusted admission stay held.

## CT128 real-engine five-workload benchmark, 2026-11-01

Hypothesis: the proven canonical workerd SDK path can supply useful operation
measurements without another orchestration layer. Change: opt-in benchmark in
the existing pinned helper; three samples each for JSON, concurrent API reads,
integration reads, authenticated writes and async/handled-exception steps.
Command: guest `python3 .../inbox/workerd-benchmark-v3/ct128-workerd-benchmark.py`;
exact Compose invocation and source hashes in `results/workerd-benchmark`.
V1 failed before execution creation (missing Path import). V2 was correctly
rejected for an extra third manifest module. V3 retained the unchanged two-file
admission policy and passed all 15 correctness samples, one canonical launch,
terminal/reconnect replay and cleanup. Medians 2/21/20/14/17 ms are local operation
wall times, not CPU, cold-start or Cloudflare cost. Helper warning logs were
filtered; only the two workflow logs persisted. Receipt SHA-256
`6e9f41683e7d30fd863f736cb2d0259dface691bb962cc0b18efa450c83ce11e`.
Next: same workloads on the signed Linux reference; preserve the physical-stop
blocker and cloud hold. See WORKERD-BENCHMARK.md for limits and measurement scope.

Platform source baseline: `e5da6d0c1fb751d4155b1b65458bfedfc2bec112`.
Artifacts are under `results/`; generated copies retain source hashes.

| Hypothesis / change | Command | Observed result / artifact | Next |
|---|---|---|---|
| Supported toolchain can start on Windows; choose isolated new uv | `py -3 -m uv tool run --from uv uv run --python 3.14 pywrangler dev --port 8793` | wasm installation selected system Python 3.13 despite 3.14 environment; pylock requires >=3.14.2 | Bounded workaround: Linux tools container |
| Linux pywrangler avoids interpreter selection issue | `docker build -f .../Dockerfile.tools ...`; local container start | Package install worked; workerd rejected missing system CA roots | Install CA package in tools image |
| Small source closure can import and execute exact engine | `py -3 prepare.py`; `node bench.mjs` | 64 files, 912622 bytes; actual engine JSON/context/import/HTTPS/logging pass; raw `benchmark.json` | Real isolated API operations |
| CPU asyncio timeout can substitute for process supervision | `node security.mjs` | Falsified: 150 ms loop succeeds under 50 ms wait_for; I/O sleep cancels | Treat preemption as material boundary |
| Per-request context cleanup isolates workflows | `node security.mjs` | Falsified: builtins marker persists; duplicate request executes twice | Fresh execution security boundary needed |
| Real API authorization works | `node sdk-proof.mjs`; `node security.mjs` | API unavailable until debug stack ready; invalid-token test initially exceeded driver 15 s budget while SDK retried connection; corrected to 30 s after readiness diagnosis. Invalid token then returns actual BifrostAuthenticationError | No token mocking |
| Actual upstream SDK integration/table operations run unchanged | `./debug.sh up`; `node sdk-proof.mjs`; `node verify.mjs results/benchmark-sdk.json --sdk` | Integration config 42; upsert/read 42; independent REST `sdk-readback.json`; no platform source edits | Credential scope/ownership still unproved |
| Linux engine reproduces fixture behavior | `docker exec ... uv run python linux_bench.py` | Same engine functions/SDK work; sync callable TypeError and missing stdout occur on Linux too; `linux-engine-sdk.json` | Do not call engine-only timings full executor baseline |
| Sync network path is a blocker | fixed `sync_http_probe` request | Falsified for this HTTPX package/runtime: sync HTTPS returns 200; first request before hot reload was unknown fixture, repeated only after reload | Keep actual transport |
| Local runtime validates production memory ceiling | fixed `memory_probe` request | Falsified: 160 MiB bytearray succeeded; `memory.json` | Need deployed isolate measurements |
| Termination has durable recovery | `node termination.mjs` | Restart interrupts active request; `termination.json`; no claimed execution or recovery implemented | Full owner adapter required |
| Package deploys syntactically | `npx wrangler deploy --dry-run --outdir /spike/dist` | Passed; 9246.93 KiB / gzip 2432.48 KiB at recorded revision; `bundle.log` | Cloud validation |
| Existing Wrangler login can deploy | `npx wrangler@4.156.0 whoami` | Expired, refresh failed | User directed use of cf CLI |
| cf CLI has independent working authentication | `cf auth whoami` | Authenticated/token valid; cf 1.0.0-beta.22 | Migrate only companion package and try isolated deployment |
| cf can migrate current Wrangler config | `cf migrate wrangler.jsonc --dry-run` | Proposed config/package/lock only; real migration stopped on dirty-worktree safety gate | Commit tested prototype, then migrate |
| cf enables cloud validation | `cf deploy --dry-run`; `cf deploy --provision=false` | Initial dry-run omitted Python packages; copied tested Linux python_modules, then full upload passed (352 modules); startup validation 457-939 ms | Invoke guarded isolated Worker |
| Cloud executes useful subset | `node cloud.mjs`; `node verify.mjs results/benchmark-cloud.json` | Actual engine JSON/context/import/HTTPS/logging pass; cloud SDK calls have no Bifrost token and fail as expected | Scoped cloud-to-test-API future experiment |
| Cloud CPU interruption preserves timeout semantics | Timer fixture with 1000 ms CPU setting | Falsified: HTTP 500 and telemetry CpuLimitExceeded; cloud perf_counter differs during CPU-only work | Preserve incompatibility |
| Cloud memory boundary is observable | 160 MiB allocation; bounded 512 MiB touched allocation | First succeeds; latter HTTP 503 resource limit. Peak and exact resource subtype unknown | No inferred numerical cutoff |
| Cloud state/duplicate safety | `node cloud-security.mjs` | Marker and duplicate effects reproduced; auth 401, forwarding 403, payload 413 asserted | Fresh boundary prerequisite |
| cf exposes CPU telemetry | Exact-script `cf sql query` on logs.workersLogs | 49 scoped rows with CPU/wall; request cost estimates include failed envelopes, not successful SDK cost | No full-system cost claim |
| Retire exposure | Disable workersDev/observability; deploy; delete exact preview | No public targets; public URL readback 404 | Keep uploaded test Worker private |
| Final local syntax verification | compileall in running source tree | Generated bytecode triggered Wrangler reload during first benchmark request, causing a transport timeout; preserved benchmark-sdk-hot-reload-failure.json. Removed only generated /spike/src/__pycache__ trees and waited for Ready | One fresh run after diagnosed harness repair; keep syntax parsing outside watched output |
| User refinement: classify before replacing dependencies | Inspect pylock and module_cache_sync/virtual_import source; current Pyodide/Cloudflare build docs | No missing-wheel blocker in tested closure. Matching pydantic-core/PyYAML wheels already present. Corrected virtual loader from assumed substantial adaptation to pure-Python closure/runtime untested | Try actual loader closure; native-build any necessary compilable missing-wheel dependency before replacement |

Earlier no-credential benchmark and later authenticated benchmark are separate
artifacts. Local host load and SDK client setup vary; no reliable speedup or
cost conclusion follows from the local samples. Cloud CPU is now measured; peak
memory/cold-start, native extension breadth, run credential scope, persisted
fencing and recovery remain open experiments.

## Fresh-runtime follow-on, 2026-10-31

| Hypothesis | Specific change / command | Observed result / artifact | Next experiment |
|---|---|---|---|
| Current upstream worker needs a replacement workspace loader | Package actual worker/virtual_import/module_cache_sync plus pure-Python redis; `node backend/transport.test.mjs` | Synchronous RefreshingClient reaches explicit egress denial, `current-runtime-sync-transport.json`; no missing wheel or synchronous I/O blocker identified | Actual scoped module retrieval |
| Caller abort stops CPU even if parent timer cannot run | Separate Node observer/event loop, enable_request_signal, forward inbound signal; `node backend/external-cancel.test.mjs` | Falsified locally. Abort requested after before callback; after effect still arrives ~1.8 s later, `external-cpu-cancel-local.json` | Approved bounded cloud reproduction; do not advertise physical cancellation |
| External-abort harness invokes correctly | Initial loopback export called without Options | HTTP 500 before any event, `external-cpu-cancel-initial.json`; fixed by supplying props Options | One diagnosed new run, retained CPU failure |
| Merged debug API is usable | Read health/logs, run actual SDK builder | Old anonymous SDK source volume lacks hosted-binding; after source sync, old dependency volume lacks lucide-react | Synced current SDK sources, npm ci from current lock; restarted only API after failed startup; health 200 |

No cloud deployment or new credential was made. Dynamic Workers pricing page
currently states daily creation billing began May 26, 2026; an older changelog
still says it is not active. Use the current pricing page, not that stale note:
https://developers.cloudflare.com/dynamic-workers/pricing/ . This is not an
allowance or approval to spend. Official lifecycle references:
https://developers.cloudflare.com/dynamic-workers/api-reference/ and
https://developers.cloudflare.com/dynamic-workers/usage/limits/ .

## Approved cloud continuation

Thomas then explicitly approved 20 fresh Workers / $0.10 / 30-minute temporary
authentication on the existing isolated resource, with no BiFrost credentials.
The preceding no-cloud statement is historical. Used 16 reserved invocations;
all failed/uncertain attempts count and no request was automatically retried.

| Hypothesis | Specific change / command | Observed result / artifact | Next step |
|---|---|---|---|
| Fresh isolate repairs cross-run contamination | cf isolation-cloud mode; `node backend/cloud-isolation.mjs` | Four sequential/parallel canaries clean, egress denied, `isolation-cloud-fresh.json` | Preserve fresh boundary; not full executor acceptance |
| Cloud CPU cancellation differs from local | Same started CPU fixture and parent abort timer in actual cloud | Falsified: both effects, timer not fired before result | Require external physical-stop contract |
| Current engine closure works in cloud | Package actual current closure and fixed engine fixtures | First 3 calls HTTP 500; one captured PythonError shows virtual hook intercepting Cloudflare helper | Move runtime imports before installing hook |
| Import order is the smallest blocker | Import Workers before upstream bootstrap; redeploy diagnosed revision | Transform/context/async and structured log/ValueError pass, `engine-cloud-fresh.json` | Genuine authorized SDK/control adoption remains open |
| CPU budget is a stop receipt | Finite longer loop with cpuMs=1000 | CpuLimitExceeded observed, only before effect | Attempt hostile catch before claiming termination |
| Guest cannot catch provider stop | Fixed catch-BaseException fixture | Catches CpuLimitExceeded and emits caught/after, `cpu-catch-cloud-fresh.json` | Material RED boundary for required untrusted physical stop |
| Cleanup removes exposure | cf private deployment, secret delete, API/readback | No public/previews/logs, empty secrets, HTTP404, `fresh-cleanup-readback.json` | Local temp-secret deletion denied by automatic review; no bypass |

`node backend/verify-fresh-cloud.mjs` intentionally exits 1 for the two physical
stop incompatibilities while asserting the functional results. No missing native
wheel was encountered; the actual blocker was pure-Python bootstrap order, then
the provider lifecycle contract. No native-build/reimplementation claim follows.

## Authorized local upstream worker and SDK

| Hypothesis | Change / command | Observed result / artifact | Next experiment |
|---|---|---|---|
| Canonical run credentials work in fresh Pyodide | `node backend/run-authorized-local.mjs`, real disposable API and canonical protocol-2 issuer | Exit 0; worker Success; pinned helper/config/table write and independent value 70; `authorized-local-liveness.json` | Adopt canonical claim/terminal contract without fixture state mutation |
| Tenant and run liveness remain authoritative | Cross-org request; then transition only fixture execution to terminal | 403 cross-tenant; same credential 200 then 401; after teardown 401 | Full attempt fencing and duplicate submission conformance |
| Existing virtual resolver can retain pinned provenance | Verify resolver source bytes against admission manifest | 15 focused IO/module/resolver tests pass; Solution remapping explicitly refused | Qualify scoped Solution loading separately |

Bounded failed attempts identified exact local fixture errors: config set returns
204 (not 200), delete returns 200 (not 204), audit rows require exact-ID teardown,
and table auto-create requires the exact `/api/tables` route as well as children.
These were corrected without replacing SDK/auth behavior. Attempt 3 produced a
successful report but PowerShell treated benign Docker stderr as failure; a new
Node process wrapper and additional run-liveness experiment established exit 0.
The earlier `authorized-local.json` receipt is preserved separately. Temporary
test-network attachment and the dedicated Wrangler process were retired afterward.

## Canonical transport contract preparation

The continuation delivery `4da716b5-ecda-4b98-a3c9-f317cba986dd` was acknowledged
against current run 15; its queue had no pending question/request. The Containers
owner remains in its separate worktree. No shared remote_pool/host/launcher
implementation was edited and no live cloud test was started.

| Hypothesis | Change / command | Observation | Next experiment |
|---|---|---|---|
| Existing PostgreSQL session fences can protect a companion physical-route journal | `./test.sh tests/unit/dev/test_pyodide_attempt_journal.py tests/unit/worker/remote/test_ownership.py tests/unit/worker/remote/test_fencing.py -q` | First six tests pass, including concurrent duplicate reservation and stale commit fencing; expanded qualification recorded separately | Bind canonical claim acquisition and full consumer receipt reconciliation |
| Docker qualification can load only the companion under test | Initial directory mount, `./test.sh quality api` | Pyright zero errors; Ruff exposed pre-existing runtime-runner E402 from mounting the entire backend | Narrowed read-only mount to attempt_journal.py, not lint exclusions or bootstrap changes |

The journal is transport state only and retains acknowledged route tombstones
until irreversible launcher retirement. Capacity is bounded and fails closed.
No logical cancellation operation is offered as physical termination. The real
engine/SDK proof at 08f149c99 is retained, not rerun as this increment's deliverable.
Expanded final qualification: nine tests passed in 8.23 seconds, raw
`results/attempt-journal-junit.xml`, including the real canonical execution
repository write and rejection of a subsequent overwrite. This does not prove
the full workflow consumer's log/event/claim-release effects.

## Full consumer terminal replay

The preceding PR-only rate-limit turn was no implementation progress. Continued
locally from f8cc75e451a304c1a9e8084d898bf8badb536e75 without another GitHub poll.

| Hypothesis | Change / command | Observed result / artifact | Next experiment |
|---|---|---|---|
| Reuse the actual consumer to resolve post-completion replay without a second result authority | `reconcile_workflow`, upstream ExecutionInfo metadata and consumer callback; `./test.sh tests/unit/dev/test_pyodide_attempt_journal.py -q` | Ten passed in 12.41s; actual PostgreSQL result and single log row, Redis log ACK/cache cleanup, owner reconnect with no relaunch; `consumer-reconciliation-junit.xml` | Canonical dispatch acquisition/release and interruption before launch |
| Guest terminal metadata cannot select another execution or override parent sync behavior | Strict execution/consumer identity and canonical envelope type; actual parent metadata attachment | Three negative admission tests pass; guest sync=True does not create a sync-result queue entry for parent sync=False | Reuse/extract upstream simple_worker envelope builder before physical Worker connection |

No upstream consumer/remote_pool/launcher code changed. The injected failure is
after the real consumer completes, before journal ACK; individual post-commit
interruption windows and whole-host loss remain unqualified. Claims and cleanup
are fixture-only. Physical execution is a deterministic callback, not a new cloud
test. CPU physical-stop incompatibility remains unchanged.

## Shared result envelope and canonical pre-launch claims

Continued from 7c7bf4bd3841f09e28a031874f66595692648966. The Containers worktree
has no dirty changes to simple_worker/result_envelope, and its simple_worker
matches this baseline; no remote_pool/host/launcher edits were made.

| Hypothesis | Change / command | Observed result / artifact | Next experiment |
|---|---|---|---|
| Reuse one pure engine-result envelope across physical runtimes | Extract the exact mapping from simple_worker; use it in the companion consumer test; `./test.sh tests/unit/execution/test_result_envelope.py tests/unit/dev/test_pyodide_attempt_journal.py tests/unit/test_solution_module_isolation.py -q` | 22 passed in 19.98s; Linux PSS/growth accounting and service/error/default mapping preserved; `claim-envelope-junit.xml` | Package shared helper with actual local Pyodide output |
| Canonical claims handle duplicate acquisition and interruption before launch | Real claim_next, release_workflow_run and reclaim against PostgreSQL; reservation callback rejects before commit | Duplicate claim absent; pre-launch handback returns PENDING, removes claim and preserves queue time; reservation failure rolls back atomically | Completed-run claim release and stale/competing-owner handback qualification |

These are current-owner pre-launch tests, not proof of stale-owner handback,
completed-run claim cleanup, credential issuance, physical termination or cloud
acceptance. The handback helper is unchanged; security behavior was not modified.
The new pure envelope has not yet been packaged or executed in Pyodide. No native
dependency or PyEmscripten build issue was encountered by this host-side increment.

## Combined real local canonical execution

Continued from 336b6d2512512d00d20fcc00974d58d0d590989b. Reused the existing own
local fixture listener after checking it was live; no duplicate supervisor,
cloud invocation or shared Containers edit. Ordinary test-stack worker/scheduler
were stopped before staging the fixture dispatch.

| Hypothesis | Change / command | Observed result / artifact | Next experiment |
|---|---|---|---|
| Actual Pyodide output can use upstream claim, consumer-issued identity and terminal persistence | Add canonical local fixture/route mode, package shared envelope; `node backend/run-authorized-local.mjs --canonical` | First run exit 0; actual module/helper load, SDK config/table operations, value 70, canonical result/log persistence; `canonical-local.json` | Concurrent tenants and interruption during individual post-commit steps |
| Retained terminal result survives reconnect without new effects | Inject exception after full consumer return, advance persisted owner generation, replay | One physical launch, one log row, same persisted result, transport ACK after replay; no rerun needed | Whole-host loss and recovery qualification |
| Terminal state revokes actual run credentials | Same consumer-issued credential before effects and after completion | Cross-tenant request 403; after terminal, credential 401; no credential in retained evidence | HTTP-user request authorization/admission qualification |
| Completed claim rows follow upstream retention, not per-run release | Inspect execution_workers.py, retain claim after completion, delete fixture executor through World cleanup | Claim retained as specified, executor FK cascade removes it | Production retention controller is still unqualified |
| Local cleanup actually retires the physical listener | Disconnect own test network; revalidate exact Wrangler supervisor and SIGINT it | Port 8799 ECONNREFUSED, bridge network only; `canonical-local-cleanup.json` | Reopen only for a deliberate authorized local experiment |

Correction to earlier local cleanup wording: ending the old tool handle did not
stop its Wrangler process. It remained unreachable from the detached API network,
was found/reused here and is now independently verified stopped. Cloud cleanup
evidence and its revoked remote secret were not changed. Package regeneration
after a metadata-note correction produced the identical tested payload hash
e4db1ebbd021387907062af4721543d5639889fe587884215cea1e0e1ec4e0c5.

Offline receipt/provenance verification and 15 focused Node tests pass. Focused
Docker Ruff and syntax/diff checks pass. No new native dependency failed: the
existing wasm wheels plus the added pure-Python envelope ran successfully.
Actual CPU/peak memory remain unknown, and no cloud acceptance or physical-stop
improvement is claimed. Runtime/API review used current Dynamic Workers docs and
published workers-types 5.20261031.1 without changing the pinned runtime.

## Intermediate post-commit interruption

Continued from a00ae73e98a65dfd1f1001bda595754ce51f2580. Production consumer,
logging and provider implementations remain unchanged; only qualification tests
and evidence were added. No cloud invocation or listener reopening occurred.

| Hypothesis | Change / command | Observed result / artifact | Next experiment |
|---|---|---|---|
| After-callback replay proves complete post-commit recovery | Fail terminal notification after real result/log commit, reconnect and replay; `./test.sh tests/unit/dev/test_pyodide_attempt_journal.py -q` | Falsified: durable outcome preserved but notification/cache repair not performed; native log recovery appends a second row | Canonical recovery design review if stronger delivery is required; do not add provider business queue |
| Reported failure outcomes retain native mapping across the same windows | Expand Success-only qualification to RuntimeError, TimeoutError, CancelledError and CpuLimitExceeded | Final 21 pass; ten fault-case property records in postcommit-recovery-junit.xml, parsed/source-hashed postcommit-recovery.json | Provider-boundary classification and actual physical stop remain separate |

The initial success-only 13-test receipt is retained separately. Final tests
passed in 9.07 seconds; focused Docker Ruff/diff checks passed. The new evidence
qualifies the earlier one-log claim to its after-full-callback window. This is
upstream's documented at-least-once log behavior, not a new Pyodide divergence.
Callbacks are deterministic fixtures, so no actual Worker/CPU/cancellation or
new end-to-end/cloud acceptance evidence is claimed by this matrix.

## Transport waiter cancellation retention

Continued from d352dc663a0dfe40d38892a25b9b78725f0041ee. Only the companion
journal and its local contract regression changed; shared Linux/Containers
execution code and production resources remain unchanged.

| Hypothesis | Change / command | Observed result / artifact | Next experiment |
|---|---|---|---|
| A disconnected caller cannot destroy a retained physical-route result | Cancel a waiter before gated launch completion, rotate real PostgreSQL owner, adopt/reconcile and retire | Before fix: failed, raw task cancelled; transport-waiter-before.xml | Shield collection without claiming physical stop |
| One shared shielded receipt preserves duplicate/reconnect semantics | Return AttemptResult instead of raw task; `./test.sh tests/unit/dev/test_pyodide_attempt_journal.py tests/unit/worker/remote/test_ownership.py tests/unit/worker/remote/test_fencing.py -q` | 29 passed, 15.75 s; transport-waiter-after.xml | Actual provider transport interruption still needs separate qualification |

No new cloud calls, endpoint reopening or credential changes. This test uses a
deterministic launch callback, not a fresh Python Worker. Whole-host loss and
physical CPU termination remain unresolved. Quality's first invocation was
refused while the test command held the stack lock; it was started sequentially
after that command completed, without bypassing the lock.

## Actual provider collection after waiter detachment

Continued from ea35a2ec23f5b17aa6cc56d2aa8322df72c7bd5e using the own isolated
local API and Wrangler 4.156.0 supervisor only. No cloud resources were invoked.

| Hypothesis | Change / command | Observed result / artifact | Next experiment |
|---|---|---|---|
| The shielded receipt retains an actual Python Worker result after its caller detaches in flight | Cancel a separate waiter after HTTP request body completion; `node backend/canonical-refresh.mjs --run canonical-refresh-transport-waiter transport concurrent-runtime-manifest.json` | Exit 0, actual SDK value 70, one launch, canonical persistence/lost-ACK reconciliation and terminal access 401; canonical-refresh-transport-waiter.json | A dropped provider socket and whole-supervisor interruption remain separate tests |
| Exact cached bundle still matches its tested pin | Parse generated runtime and use packer's sorted Python JSON hash | Payload 21b82a20... matches; raw source mismatch was verified CRLF normalization, not a different payload | Make source-byte versus serialized-module provenance explicit when changing the packer |

Initial API-starting preflight refused before fixture effects; no business
retry occurred. Offline verifier, 26 focused Node cases, Docker Ruff and diff
checks passed. Cleanup receipt confirms port refusal, bridge-only network and
stopped API. No physical workflow stop, cloud acceptance or refresh revocation
claim follows from this waiter-detachment fixture. See backend/TRANSPORT-INTERRUPTION.md.

## Actual provider response loss

Continued from 832ed9d0ddcaee4d28accb6392f4e2ce4eec675c; own local Wrangler/API
only. Shared consumer/Linux/Containers implementations remain unchanged.

| Hypothesis | Change / command | Observed result / artifact | Next experiment |
|---|---|---|---|
| Retaining a receipt also recovers a lost collector connection | Local TCP injector discards the actual provider response after independent SDK document read; `node backend/canonical-refresh.mjs --run canonical-refresh-response-loss-observed response-loss concurrent-runtime-manifest.json` | Falsified: provider success/value 70, actual RemoteProtocolError, canonical Failed/no success result, access 401, one launch; recovery_acceptance false | Separate routing acceptance from asynchronous result collection and identify a reconnectable physical terminal receipt; no replay |
| Observer can inspect a persisted error_type | Initial fixture reads nonexistent ORM field | AttributeError; failed receipt and exact initial driver retained | Corrected observer uses real error_message and captured transport exception |

The failed process was terminal and its fixture worker count zero before the
new fixed-fixture run. Source/provenance verifiers, Docker Ruff and JS syntax/diff
checks pass. Exact cleanup is retained in response-loss-local-cleanup.json.
No new cloud, physical-stop or successful recovery claim follows from a passing
negative-characterization process. See backend/RESPONSE-LOSS.md.

## Asynchronous routing acceptance

Continued from 6194730cb40b109665f2259ee1fdc5e07beab5c4. The companion route now
returns its retained dispatch receipt before waiting for collection. Collection
and canonical terminal delivery occur outside the consumer's setup handler.

| Hypothesis | Change / command | Observed result / artifact | Next experiment |
|---|---|---|---|
| Matching upstream asynchronous routing preserves successful execution | `node backend/canonical-refresh.mjs --run canonical-refresh-async-collection transport concurrent-runtime-manifest.json` | Exit 0, SDK value 70, one launch, cancelled waiter retained, canonical persistence/reconnect, terminal access 401 | Reconnectable physical terminal receipt |
| Late socket loss must not become a canonical setup failure | `node backend/canonical-refresh.mjs --run canonical-refresh-response-loss-async response-loss concurrent-runtime-manifest.json` | Exit 0 characterization: RemoteProtocolError, canonical Running/no result/error, no replay; credential remains 200 until fixture purge then 401 | Canonical recovery/deadline and physical-state proof remain required |

This is a correction of routing semantics, not successful unknown-result recovery.
Exact payload pin unchanged, actual local Worker executions only. Docker Ruff,
source/provenance verifiers and JS syntax/diff checks pass. Own supervisor/network/
API cleanup is retained in async-collection-local-cleanup.json. No shared API,
Containers, cloud invocation, persistent access or security setting changed.

## Collection inventory boundary

Continued from 815ae7dad81273b2bb1a3df38f7b1ca0a0006ebb. No cloud invocation or
actual Worker relaunch; this increment is the host transport inventory contract.

| Hypothesis | Change / command | Observed result / artifact | Next experiment |
|---|---|---|---|
| A done collector provides a terminal receipt | Fail/cancel collection before inventory | Falsified: both advertised terminal true; collection-inventory-before.xml | Report collector state independently of physical state |
| Failed/cancelled collection must retain uncertainty without effects | Explicit states plus owner rotation, ACK/capacity/retirement regressions; `./test.sh tests/unit/dev/test_pyodide_attempt_journal.py tests/unit/worker/remote/test_ownership.py tests/unit/worker/remote/test_fencing.py -q` | 31 passed, 13.98 s; collection-inventory-after.xml | Reuse upstream retained event/ACK semantics for reconnectable physical results |

Upstream Launcher.send_terminal, retained terminal inventory and host delivery/
ACK paths were read. They remain unmodified along with Linux/Containers source.
Inventory exception type is reported, never its message; physical termination
remains unverified in every state. See backend/CONTRACT-JOURNAL.md.

## Upstream retained-result consumption

Continued from 9cf1d24ddd899ee4293284d3e96d98b4f08f6276. Added an explicit
companion recovery method using upstream result event identity and the normal
fenced consumer; no new terminal store, business queue or code replay.

| Hypothesis | Change / command | Observed result / artifact | Next experiment |
|---|---|---|---|
| Actual upstream retention/ACK code can recover a failed collector without duplicate canonical effects | `./test.sh tests/unit/dev/test_pyodide_attempt_journal.py tests/unit/worker/remote/test_launcher.py tests/unit/worker/remote/test_ownership.py tests/unit/worker/remote/test_fencing.py -q` | Initial 33, final 40 passed; value 70, one completion/log row/launch, lost ACK preserves event until actual ACK receiver consumes it | Place retention around the actual local Worker response-loss fixture |
| Recovery entry must reject unbound or inapplicable results before consumption | Seven negative cases plus stale owner | Consumer not called/route not ACKed on rejection; retained-result-final.xml | Live authenticated transport and physical-handle adoption remain unqualified |

Sockets and result are synthetic in this protocol/DB test; no new SDK, Pyodide
or cloud acceptance follows. Linux templates remain unstarted. Actual upstream
Launcher and Bridge methods are used, not copied. See backend/RETAINED-RESULTS.md.

## Actual local retained response

Continued from 1aa35845b0c3797f3eea45b0c4d0948863dd6780. The existing test gateway
retains the actual Python Worker envelope through upstream Launcher.send_terminal
before dropping the downstream collector response. No production/shared code
or cloud resources changed.

| Hypothesis | Change / command | Observed result / artifact | Next experiment |
|---|---|---|---|
| Actual engine/SDK results can recover through the upstream retained-event path | `node backend/canonical-refresh.mjs --run canonical-refresh-retained-response retained-response concurrent-runtime-manifest.json` | First fixture exit 0; actual TCP response loss, one launch/upsert/log row; new owner consumes Success/value 70; canonical-refresh-retained-response.json | Live authenticated control link and physical-handle inventory/adoption |
| Owner generation alone explains revocation | Check original scoped access after owner reconnect, then after canonical Success | 200 after reconnect, access/refresh 401 after Success before fixture purge | Interrupted-run revocation/deadline remains unqualified |
| Lost ACK must not lose the actual retained event or repeat effects | Lose first control ACK, replay, feed later ACK to upstream receiver | Same log IDs, one launch, retained event cleared only by receiver | Worker-to-gateway loss and whole-host recovery remain separate |

Control transport is simulated, but Worker/SDK result and provider TCP drop are
real local execution. Exact runtime payload pin unchanged and verified. Source/
evidence verifier, focused Docker Ruff and JS syntax/diff checks pass. Exact
own supervisor/network/API cleanup is retained in retained-response-local-cleanup.json.
Broad recovery_acceptance and physical termination remain false; no cloud claim.

## Live control authentication and remote-test checkpoint

Continued from e09af99620783221edb9e542c2d84ae4d2966b84. Existing test stack was
stopped with a missing temporary bind directory; its exact own helper/directory
and ephemeral database were restored through the normal harness before the
probe. No production or other project's resources changed.

| Hypothesis | Change / command | Observed result / artifact | Next experiment |
|---|---|---|---|
| Real control authentication can be tested without unqualified execution admission | Pre-register empty handlers, withhold inventory; `node backend/canonical-refresh.mjs --run canonical-refresh-control-auth control-auth` | First probe exit 0; actual signed hello generations 1 then 2; invalid HMAC, captured replay, old socket and stale generation each close 1008; zero claims, starting throughout | Owner-verified remote harness; physical inventory/adoption remains unqualified |
| Heavy laptop tests can stop at an exact checkpoint | Consume delivery af7a12d2-0873-4f74-9eab-e6c75099e0f3; preserve source/result, stop own containers only | Delivery ACK succeeded in run22; all own test infrastructure/helper stopped, no volume/lock deletion | Existing pve-t340 owner must verify capacity/ownership before remote execution |

No new heavy laptop batch followed the migration instruction. Passive capacity
query c5842180-9153-4841-b5f7-130aa489f58b was denied source_run_not_active with
safe_to_resend false, preserved without alternate caller/retry. Remote execution
has not occurred. See CONTROL-AUTH.md and REMOTE-TEST-HANDOFF.md under backend/.

## Offline termination-evidence qualification

Heavy laptop tests remain stopped. Bounded read-only owner lookup supplied no
verified current capacity handoff; this environment has no linked remote
environments. No message, new link, guest selection or remote test was started.

| Hypothesis | Change / command | Observed result / artifact | Next experiment |
|---|---|---|---|
| Cloud CPU after effects prove received cancellation failed | Audit raw abort_requested flag | Flag is false: test was not exercised; verifier now reports not_exercised/null | Independent timestamped stop receipt required on a verified target |
| Quota exception is a physical stop | Audit distinct cpu_catch record | Actual CpuLimitExceeded caught, then caught/after effects; acceptance remains failed | Provider physical-stop primitive still required |
| Offline verification may rewrite historical evidence | Remove writer; `node --test backend/termination-evidence.test.mjs` | Four pass; real verifier exits 1 and preserves old report bytes | No runtime replay or evidence overwrite |

Historical source snapshot hashes are verified separately from deployed-version
associations. The local observer's 1.809 s is before-to-after, not a measured
abort-to-after interval. See backend/TERMINATION-EVIDENCE.md and the new audit
JSON. No source-mocked SDK/Worker compatibility or new cancellation proof follows.

## CT128 run72: pinned runtime frontier

| Hypothesis | Specific change / command | Observed result / artifact | Next experiment |
|---|---|---|---|
| Public pins reproduce the dependency closure without laptop caches | ct128-runtime-stage.py over existing SSH/pct exec 128 | npm ci and 11 hash-verified wheels; 455 packaged modules at source 785b560b; results/ct128-run72/staging.json | Invoke ABI fixture with no network |
| Pinned workerd includes a usable offline Python runtime | ct128-runtime-probe.py, network=none, 1.5 CPU/3 GiB container | HTTP 500 before Python start: required bundle missing, DNS refused; abi-probe.json/runtime.log | Inspect exact official bundle/cache interface |
| Official integrity-pinned bundle is publicly fetchable | ct128-runtime-cache.py, exact source metadata URL/digest | HTTP 403; cache-denial.json retained, no alternate route/retry/R2 | Authorized supply of exact artifact required |
| Independent timestamps can establish stop from abort alone | Prepare guest-termination-entry/probe + classifyTimedCancellation | Not invoked; 3 offline classifier/opt-in tests pass and never assert physical-stop or canonical fencing acceptance | Once runtime qualifies, run completion/catch/abort negative controls; require separate trusted stop and actual canonical fencing proof |
| Diagnostic guard test needs path check/use | Separate missing-output observation from exclusive safe output | 8 guard tests pass; no credential-bearing file created | Fresh CodeQL on new head |

Package ABI and execution remain unqualified in this guest. Detailed commands,
source hashes, denial and required future cloud proof are in
backend/CT128-RUNTIME-FRONTIER.md. All owned containers stopped; no heavy laptop,
cloud execution, credentials, resource expansion or baseline/control-auth rerun.

## Run73: authorized publisher supply investigation

Hypothesis: the publisher exposes a supported authenticated/release/cache route
for the exact bundle. Source inspection at workerd 7caf6dee verifies anonymous
canonical GET and integrity-checked local cache; no consumer login header.
Existing-identity GitHub release inspection lists five native binaries, not the
bundle. Read-only stopped task-container and selected task-cache filename
inspection found no matching copy. The old 403 was not retried. No asset was
acquired and no runtime fixture was invoked. See backend/RUNTIME-BUNDLE-SUPPLY.md
and results/ct128-run73-supply-review.json. Next: publisher restores anonymous
availability or an already lawful matching file is supplied via the approved
owner inbox. No credentials, R2 route, mirror or different bundle substituted.

## Resume: actual native package tests instead of waiting on supply

| Hypothesis | Change / command | Observed result / artifact | Next step |
|---|---|---|---|
| The reconstructed workerd URL might be wrong | ct128-native-selection.py, native binary verbose, network=none | Exact same URL selected; DNS refused before GET; native-selection.json | Keep publisher supply gate distinct |
| Matching native wheels can execute in the official interpreter | ct128-pyodide-package-qualification.py | Hash-verified official 314.0.6 core; expected Python/ABI observed; native validation/YAML checks pass | Actual upstream package assertions |
| The native serializer works beyond import/smoke | ct128-pydantic-upstream-tests.py | 32 unchanged upstream tests pass, JUnit 0 failures/errors/skips | Cloudflare-specific qualification still required |
| Native YAML scanner/parser/emitter match upstream fixtures | ct128-pyyaml-upstream-tests.py | 261 cases pass; first observer parser failure and stopped-container-name conflict preserved | Continue SDK/engine runtime gates after workerd supply |

Results and exact hashes: results/native-package-resume. The old pydantic-core
tag lookup failed; wheel metadata and exact pinned source sdist resolved the
monorepo source location. No moving-main test substitution. No shared conftest/GC
or full package-suite acceptance claimed. All guest containers stopped, no OOM.
No failed vendor GET was retried, no source wheel replaced, and no cloud/R2 or
heavy laptop tests ran. Node Pyodide is a package test harness, not a new backend.

## Follow-on actual guest engine semantics

Hypothesis: the same official guest interpreter can execute the unchanged
prepared upstream engine, beyond native-package imports/tests. Command:
ct128-engine-semantics.py over existing SSH/pct exec 128, network=none,
1 CPU/1 GiB/128 PIDs/120 s. Initial raw sync functions returned Failed/TypeError
because upstream awaits callables. A diagnostic-only run retained those results;
the async/context case and ValueError redaction already worked. Correcting the
fixture to real async @workflow functions, without modifying upstream, yielded
four passing cases: JSON sum/context, async parameters/scope, CompletedWithErrors,
and Failed/ValueError with synthetic credential redacted from full output.

Source 785b560b, engine SHA b9bd1d04a17d9dfc6a9418550ffb361c734d99558bdd7c710f3c53b0fdbc06ab.
Raw successful and failed observations: results/engine-semantics-resume.
Offline verify-engine-semantics.mjs confirms the source binding/results and
explicitly false SDK/cloud/physical-stop qualification flags. Actual upstream
semantics on guest Pyodide pass; the workerd supply and fresh physical-stop
requirements remain. Next independent boundary is real SDK transport/API work
on the isolated guest stack, without a stub or a new authoritative job queue.

## Retained route principal, 2026-11-02

Hypothesis: live-session validation alone does not bind a journal route to its
original physical worker/launcher. Six focused cases against the real journal
reproduced five failures and one reconnect control pass. Change: pin canonical
worker_id/launcher_id per route and reject conflicts before receipt/ACK access;
allow legitimate API-owner/session rotation for the same launcher. Commands:
`python -m unittest -v test_attempt_journal_owners` and repository
`./test.sh tests/unit/dev/test_pyodide_attempt_journal.py -q` on CT128.
Observed: six focused cases and38 full journal cases pass, including two actual
PostgreSQL-backed live remote sessions; Ruff passes. Guest receipt/overlay hashes
and baseline/final logs are in results/route-ownership; supervisor elapsed115.249s,
owned test project stopped, volumes/dev services preserved. No cloud invocation.
Next: coordinated canonical physical-handle/admission extension and provider
physical-stop proof; this companion repair does not satisfy those gates.

## Shared printed-output capture, 2026-11-02

Hypothesis: missing stdout is discarded pure-Python engine buffers, not Pyodide
transport/native-package incompatibility. The first fixture missed required
organization metadata (five TypeErrors retained); corrected fixture against
unchanged engine reproduced four failures and one structured-log control pass.
Change: one shared capture context flushes stdout/stderr through existing
redaction, returned logs and authenticated log delivery on success/failure.
Commands: `ct128-stream-capture.py --after` runs repository ./test.sh for capture,
credential redaction, execution contract and logging-lock consumers, then
Ruff/Pyright. All37 cases and both checkers pass. `--pyodide` runs the actual
modified engine with the pinned guest closure: Success70, redacted stdout/stderr,
Failed/ValueError and redacted pre-error output; no mocked engine or SDK calls.
Artifacts: results/stream-capture, backend/STREAM-CAPTURE.md and source verifier.
CT128 only; owned test project stopped, volumes/dev services preserved. No cloud
deployment/invocation. Canonical Product Updates draft fix disposition validates
in the cached guest image. Next: pin modified engine into a separately qualified
runtime closure and prove actual credentialed log persistence; old failed
canonical stdout fixture and deployed runtime receipts retain original pins.

## Upstream capacity and execution reconciliation, 2026-11-03

Hypothesis: accepted upstream external-capacity changes may alter the physical
execution boundary. Fetched main025f6c019 and merged it conflict-free into1d33150eb.
Inspection: ExternalCapacityClient provisions worker resources under canonical
ownership; signed external resource UID does not replace Linux per-run PID ACKs.
No isolate profile or workflow handler enabled. Exact merged-source guest
./test.sh selection covers journal, remote pool/protocol/credential negotiation,
external capacity/policy, parent result identity and print/redaction:129 pass,
no failures/errors/skips; supervisor60.465s. Initial harness preflight duplicated
an existing mem_limit and was rejected before new containers; bounded correction
replaces keys and validates Compose first. Receipt/JUnit/raw failed preflight
are retained under results/upstream-reconcile; task-owned project stopped.
Closure source audit:79 tracked modules compared,73 identical, six explicit
server-namespace adapters, no unexpected drift. Live dev API stays2037ac0;
source/native checks are not a redeployment. Next is the still-missing negotiated
physical runtime/admission contract and actual provider physical-stop proof.

## Deployed five-workload benchmark, 2026-11-03

Hypothesis: useful short engine/SDK workflows execute economically in fresh
isolates. Added a fixed workload selector without changing operation bodies,
runtime limits or default all-workload behavior. Three native guard cases and
guest Ruff pass. Command: run_cloud_benchmark.py on CT128; five actual cloud
children,15 correctness samples, config/HTTP/integration/table/async-error/logging
workloads pass. Table documents independently read70; each run's peer403,
invalid token401, unsupported canonical422 and ended access/refresh401 pass.
Fresh provider round trips measured5.2–10.5s. JSONtimer0,0,0 is not CPU evidence:
official deployed clocks advance at I/O, unlike local workerd. Scoped cf CLI
analytics query returned0 rows with logging unchanged; CPU/peak/billed cost stay
null. Official fresh creation price0.002USD implies a conditional2USD/1,000
after allowance, before CPU/requests—not a measured bill or Linux comparison.
Artifacts: CLOUD-BENCHMARK.md and results/cloud-benchmark, raw samples/ledger,
source hashes, summary and standalone chart. Window15POSTs+1cleanupGET within
20/$0.10/30min retired; cf CLI closed deployment, secret list empty, public404.
The old Linux verifier now reads its helper from the same immutable tested
ca40ad458 source as its driver; no old receipt/hash was rewritten. Next remains
canonical dispatch/physical stop, with trusted longer-I/O latency as a separate
candidate experiment; no economic routing advantage is established.

## Canonical failure/stdout repair, 2026-11-03

Hypothesis: the shared engine repair closes the historical canonical stdout
failure without substituting simpler terminal/error semantics. Reused the fixed
--failure-probe scenario, actual current-upstream API/claims/consumer and real
local workerd SDK with artifactbc4eae48...6daf0, enginef49d4ba99. V1 manifest
lacked Wrangler declaration; v2 lacked the pinned wrapper's /spike mount. Both
stopped before credentialed fixture launch; receipts retained. Bounded fixes
reuse existing cached dependencies/mounts, no installation or alternate access.
V3 passes actual ValueError/Failed/11-row persistence, lost ACK/reconnect, one
launch, absent document, ended access/refresh401 and fixture cleanup. V4 adds
direct PostgreSQL stdout/structured-message equality and raw-credential checks;
all pass, supervisor62.447s. No original stdout assertion was removed. Guest
Ruff passes; all owned test containers stopped, dev/volumes preserved. No cloud
invocation or physical-stop claim. Artifacts: backend/CANONICAL-FAILURE-REPAIRED.md,
results/canonical-failure-repaired/v4.json and the source-bound verifier. The
old failed receipt still verifies against d352dc663; no old hashes/flags changed.
Next remains negotiated physical handles/admission and deployed canonical
control, with provider physical termination a material untrusted-workload gate.

## Per-attempt HTTP component, 2026-11-03

Hypothesis: the physical HTTP operation can be extracted without changing native
scheduling/result ownership. Change: immutable single-use request and per-attempt
HTTP session, existing streamed frame decoder; native harness calls this component.
Command: timeout 430 python3 inbox/http-component-v1/ct128-canonical-failure.py
--http-component on CT128. Result: four phases exit 0, real upstream engine/SDK
read/module/upsert70, canonical Timeout preserved, genuine late Success/EOF,
lost ACK/replay, one launch; provider remains running before owned cleanup.
Supervisor102.420s includes deliberate40s await. Separate four native unit cases,
Ruff and Pyright pass in bounded network-disabled Docker. No cloud calls.
Artifact: backend/HTTP-INVOCATION.md, results/http-component/v1.json and verifier.
Historical late-settlement verifier now binds d7620e68e rather than changed source.
Next: replace singleton native transport route state, then qualify multiple
attempts through the same native relay/session. This is not deployed CPU-stop
or arbitrary-workload admission proof; no second queue or retry owner introduced.

## Attempt-scoped request collection, 2026-11-03

Hypothesis: independently retained collection slots can preserve native workflow
identity across concurrent request waiters without a new scheduling authority.
Change: WorkflowRequestMux binds trusted collectors per execution, snapshots
native payloads, retains one accepted future/task/result per run, refuses changed
duplicates, and leaves collection live when a request waiter is cancelled.
Command: native network-disabled python -m unittest -v test_workflow_request_mux
 test_http_invocation on CT128; targeted Ruff/Pyright against four changed files.
Five exposing mux cases initially fail two assertions: replacement-generation
retained ACK and owner rotation before scheduled collector start. Both repaired
by immutable admission generation and boundary rechecks. Source/fail-first
summary retained. V2 assertions passed but exposed an unobserved retained task in
the ownership-race test; V3 explicitly awaits/asserts that failure, no task cancel.
Final nine-case run passes without that warning; Ruff/Pyright pass. Real single-
attempt engine/SDK late-settlement runs v1 and v3 pass independently; v4 binds the
final harness context-update ordering. No cloud calls or shared API edits.
Artifacts: backend/WORKFLOW-REQUEST-MUX.md, results/request-mux and source verifier.
Next: remove remaining singleton validator fields and instantiate one actual
native pool/worker/session for two engine/SDK runs, then bounded terminal-ACK
retirement. Synthetic concurrency is not that acceptance, nor CPU-stop proof.

Final request-mux V4 passes all four real phases, supervisor102.210s, provider
still running/no OOM before owned cleanup and no running owned test containers
afterwards. Nine focused cases and Ruff/Pyright pass on the exact final packet.

## Shared actual native pool, 2026-11-03

Hypothesis: two real run-scoped engine/SDK workflows can share one native pool
without sharing mutable credential/result state or losing peer obligations.
Change: dedicated fixed two-tenant fixture with per-attempt slots, one real
ThinRemoteProcessPool/consumer/worker/launcher, existing request mux and journal.
Each run retains the existing two-module admission shape and30s deadline. One
credential-free prewarm precedes claims; known cold concurrent initialization
failure is retained, not reclassified as passing cold proof. New mux retirement
refuses pending/failed collection and follows actual consumer ACK/journal retire.
Initial V1 quality:11tests/Ruff pass; Pyright finds one callback/local name
collision before real invocation. Rename persisted local, preserve initialsource.
V2 command: timeout430 python3 inbox/shared-pool-v2/ct128-canonical-failure.py
--shared-pool --revision2. Four phases pass72.174s. Both actual SDK effects70/78
are independently observed while both workflows wait. Release failure first,
realValueError/redaction/Failed, loseACK, rotateactualownerlease, rejectstale
terminal, replaywithoutchangingcompleted_at/logIDs, retainlivepeercredential200
andfailed401, duplicatepeerreceiptwithoutlaunch, thenSuccess78andall401/retire.
V3 adds canonical heartbeat/reservation readbacks as busycounts2,2,1,0; passes
72.499s. V4 requalifies exact final packet after clarifying companion-journal
versus upstream-API terminology. Eleven focused guest cases/Ruff/Pyright pass.
Artifacts: backend/SHARED-NATIVE-POOL.md, results/shared-pool raw receipts/source
and source-bound verifier. Provider staysrunning/noOOM untilownedcleanup; no
cloudcall or API-sourceedit. Controlsocketreconnect is fixture-supplied, despite
actualPGownership/claims/consumer: not combinedsigned-control/deployedacceptance.
Next: extract the shared fixture adapter into reusable private backend code,
keep canonical token/provenance authority, and qualify supported startup without
inventing Linux commands. Public admission and abnormalCPUstop remain unaccepted.

Final shared-pool V4 passes four phases, supervisor72.338s; nativebusy2,2,1,0,
canonicalpeer/settledaccountingverified,11focusedcasesandRuff/Pyrightpass.
Provideralive/noOOMbeforecleanup;ownedrunningcontainersaftercleanupempty.

## Reusable private HTTP backend, 2026-11-03

Hypothesis: the successful shared-pool transport can become reusable backend
code without cloning engine, authorization, claims or terminal semantics.
Change: PrivateHttpWorkflowPool composes actual ThinRemoteProcessPool plus the
existing companion journal/mux/HTTP framing. Explicit bounded pinned manifests,
immutable context/active metadata, current canonical-owner/credential-route
checks, receipt reservation BEFORE physical dispatch, duplicate/cancel/streamloss
retention, actual native result callback and ACK-gated retirement. No registration,
token issuer, business-job queue, automatic retry or public admission added.
Shared fixture delegates physical routing/reconciliation to the new module.
Six new adapter cases use real native pool plus mocked ownership/Redis and HTTP
byte fixtures: pre-bookkeeping refusals, owner loss at reservation, immutable
single dispatch/ACK retirement, routing waiter cancellation, lost stream retained
without relaunch, native persistent-service refusal. These are transport tests,
not mocked SDK compatibility. Total17guestcases pass. V1Ruff finds unused time
import after extraction; stops before live invocation, source/diagnostic kept.
Removeunusedimport; V2 tests/Ruff/Pyrightpass. Real command timeout430python3
inbox/private-pool-v2/ct128-canonical-failure.py --private-pool --revision2 passes
four phases72.523s: two actual tenantengine/SDK runs, held70/78, ValueError/Failed,
lostACK/current/staleownerreplay, livepeer200/failed401, adapterduplicate reuses
originalreceipt, Success78/all401, native/canonicalaccounting2,2,1,0andretire.
Allownedtestcontainersstopped, provideralive/noOOMbeforecleanup; no cloudcalls.
Artifacts: backend/PRIVATE-HTTP-POOL.md, results/private-pool/v2.json and verifier.
Historicalshared-pool verifier now reads exact6913d999d source, not newcode.
Remaining realgap: baselineconsumer initializes content_hashNone; fixed fixture
explicitly supplies knownpublishedhash. Adapterrefusesmissing/mismatchhash and
doesnotinventpins. Next: authoritativeAPI/workspaceplan preparation for normal
claimedruns, then supported startup without Linuxconfigure/package commands.
FixedWorkeradmission, signedcontrolsocket, deployedcombinedlifecycle and abnormal
CPUsettlement remain unaccepted. Coldconcurrentstartupfailure retained; prewarm
iscredentialfreebeforeclaims, not coldlatency or arbitrarycodeadmission proof.

## Authoritative API preparation, 2026-11-03

Hypothesis: existing run-authenticated module endpoints can supply the private
adapter's entry/dependency pins without fixture hash injection or another source
resolver. Change: bounded prepare_workspace helper reuses upstream path/name
functions, GET modules and modules-resolve under the genuine runtime principal,
checks identities/sourceSHA256/storagepaths, preserves existingpins and returns
an unmutated-context copy with derivedpin. Declaredworkspaceimports only; no
code execution, dynamicclosure inference, credentialrefresh, redirect or scopes.
Native tests cover snapshots, hashes, corrupt/oldpins,401/403/307refusal, package/
namespacepaths, missing/scopedimports and size/inputbounds. V1 exposes oneof23
failures: ../escape is normalized upstream and entryGET occurred before this
layer rejected noncanonicaldeclarations. Before-source/test/failure kept; add
canonical dotted/slash equality check BEFOREnetwork. V2 all23cases/Ruff/Pyright
pass with unchanged tests, no retry/timeoutmask. Realcommand timeout430python3
inbox/api-prepare-v2/ct128-canonical-failure.py --api-prepare --revision2 passes
fourphases98.657s. Actualinvalidcredential401 beforebinding; eachgenuine run has
2successfulparentGETs andAPIderivedmanifest matchingindependentpublishedsource,
originalcontextpinNone. Actualengine/SDK70/78, ValueError/Failed/lostACK/replay,
staleownerreject, peer200/failed401thenall401, accounting2,2,1,0andretirementpass.
Provideralive/noOOMbeforeownedcleanup; noownedrunningcontainersafter. API source/
resolverfilesreadbackcleanagainst1d33150. No cloudcalls or API-sourcepatches.
Artifact: backend/API-WORKSPACE-PREPARE.md, results/api-prepare/v2.json, native
sourcehashreadback and source-boundverifier. Historicalprivateverifier now binds
57dd06d44 exactsource. Counts show2extra parentGETs/run, notCPU/startup/bill; total
supervisorincludesprewarm/setup/barriers/cleanup. Package/namespacepreparation is
unitHTTPproof only; actualWorker stillfixedflat2-moduleadmission. No atomicwhole-
workspacecommit or dynamicimportsafety claimed. Next: guest-only source-change
negativecontrol before workfloweffects, then supported startup/capabilitypolicy
preparation; publicadmission and deployedCPUsettlement remain unaccepted.

## Changed helper after preparation, 2026-11-03

Hypothesis: a changed declared eager dependency is rejected before workflow SDK
effects, without disturbing a held peer. Fixed source-change mode prepares real
API pins, overwrites only ownedhelper with fold999, independently confirms new
APIhash200, retains oldmanifest, then invokesactualengine/SDK via privatepool.
V1failsblanketlogassert: upstreamWorkflowLoadErrorbeforefunctiontracinghaslogs[];
acceptonlythatexactenvelope withFailed/nonemptyerror, neverfabricatelogs. V2fails
heldpeercheck; V3diagnosticrecords BifrostAPIError/410 andoriginaldeadlinealready
10783.879mspast, nativebusy2stillheld. All3failedreceipts/sourcespreserved. V2
immediatestopreadbacksawSeaweedFSid;readonlyfollowupthatcontainerexited/project
empty, originalreceiptunchanged. No volume/uncertainlockdeletion. Boundedfix:
knownWorkspace module digest mismatch becomesHTTP409+structuredtraceinstead of
throwing acrossPython/JSbridge. Sameverifier/policy, cutoff30s, heldbarrier and
resourcebounds; authresponses/unrelated/providerlimiterrorsunchanged.3Nodecases
pass, targetedRuff/Pyrightpass; unchanged23helpertests retainedbysourcehash.
V4command timeout430python3 inbox/source-change-v4/ct128-canonical-failure.py
--source-change --revision4 passes102.704s. Oneboundedexact-candidatestability
runV5 --revision5 passes94.861s, allpacketdigestsequal. Changedhelperisavailable
butchildgets409digestdenial, no config/tablecalls, independentdocument404 before
terminal; WorkflowLoadError/Failed/zeroengine+persistedlogs. Peerremainspending
with23.845/24.447soriginalbudgetleft, survivesfailureACKloss/reconnect/staleowner
fencing, succeeds78afterrelease; bothcreds401andaccounting2,2,1,0/retire/cleanup.
Provideralive/noOOMbeforeownedcleanup; noownedrunningcontainersafterV4/V5.
Artifact: backend/SOURCE-CHANGE-CONTROL.md, results/source-change rawfailures,
diagnostics, Node/qualityevidence andsourceboundverifier. No cloudcall/settings/
admission/auth/resourcechange. ActualWorker sourcechangeisHTTPerrormappingonly,
notanenginefork. Eagerimports only: lateimports mayfollowearlierauthorizedeffects;
noatomicworkspace, rollback, arbitrary-isolation orCPUstopclaim. Totalsinclude
setup/prewarm/barriers/cleanup, notpurestartup/CPU/bill. Next: automaticnative
terminaldelivery/deadlines andsupportedstartupboundary, keeping publicadmission
closed and without creating another durablejobqueue.

### Per-route terminal acknowledgment concurrency
Hypothesis: automatic reporting must not let a stalled canonical ACK block other
attempts. Change: companion journal delivery lock per retained route; keep global
registry locking for dispatch/retirement and retain both owner fences. Command:
CT128 results/delivery-lock/pyodide-delivery-lock-v1.sh then v2.sh; bounded Docker
unittest, Ruff/Pyright, plus six existing owner/reconnect cases. Observation: old
source deterministically fails unrelated dispatch; changed source passes both
interleaving/owner-change cases and all six owner guards. V1 read-only Ruff cache
failure retained; --no-cache corrects invocation in v2. Artifact:
backend/DELIVERY-LOCK.md and results/delivery-lock. No cloud/production/runtime
setting change or physical-stop claim. Next experiment: owned automatic terminal
delivery, error retention and actual canonical callback qualification.
