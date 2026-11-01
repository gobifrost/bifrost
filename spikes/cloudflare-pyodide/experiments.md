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
