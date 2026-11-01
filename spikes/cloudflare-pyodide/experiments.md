# Compact experiment log

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
