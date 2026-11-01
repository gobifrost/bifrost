# Execution frontier

## Decision and evidence level

**YELLOW**: a useful real upstream subset runs, with a small explicit settings
adapter. **RED** for using this shared-isolate design to execute arbitrary
untrusted workflows. The latter is a material incompatibility, not a tuning task.
There is deployed-Cloudflare evidence for the non-SDK subset; there is no
production acceptance or deployed SDK-to-Bifrost evidence.

Subsequent local qualification at a00ae73e9 connects actual canonical dispatch
claim, consumer-issued protocol-2 credentials, fresh Pyodide engine/SDK execution,
the shared result envelope and canonical persistence/reconnect. This closes the
functional local chain, not deployed acceptance or general workload admission.
The [post-commit fault matrix](backend/POSTCOMMIT-RECOVERY.md) preserves durable
Success/Failed/Timeout/Cancelled outcomes but exposes a narrower recovery
boundary: stale terminal replay does not resume skipped notification/cache work,
and native at-least-once log recovery can duplicate a committed log row. No new
provider orchestration or canonical platform repair is claimed.

The authorized fresh-isolate follow-on also remains **RED for first-class
arbitrary untrusted execution**: cloud CPU cancellation permits later effects,
and a Python workflow can catch Cloudflare's CpuLimitExceeded and emit another
effect. Fresh isolates repair the observed state-sharing problem, but do not
provide the required physical-stop contract. Current upstream engine fixtures
run successfully in fresh cloud isolates without a fake settings module. They
use synthetic context and do not establish genuine run-scoped SDK authorization.
See `IMPLEMENTATION.md` and `results/fresh-cloud-verification.json` for this
separate tested increment; the historical sections below retain their scope.

Retain arbitrary/CPU-heavy workflows, services and agents on Linux until an
independently enforced provider termination mechanism is verified. A trusted,
digest-approved short I/O profile remains a possible follow-on; approval must
precede effects, and canonical credentials/attempt/result/recovery still need
integration. Do not infer eligibility from an AST scan or imports alone. A second
queue, self-reported stopped state, or catchable exception is not a substitute.

`results/benchmark-sdk.json` contains five requested workload families plus
context/import/error probes. `verify.mjs ... --sdk` asserts expected values,
including integration config `{answer:42}` and table write/read `{value:42}`.
`sdk-readback.json` independently confirms the write through the actual API.
No mocked SDK responses or substitute REST implementation are involved.

## Dependency closure and compatibility

`prepare.py` copies upstream files byte-for-byte and records SHA256 plus base
commit in `source-manifest.json`. The tested packaging candidate is 64 upstream
files / 912,622 bytes, before dependencies; it is deliberately conservative,
not a proof that every included SDK source is necessary. Empty `src` package
scaffolds avoid eager ORM initialization. The upstream standalone SDK decorator
path is used; platform `src.sdk.decorators` is not included. The static graph
has 808 reachable local nodes, including optional, TYPE_CHECKING and function-local
imports; it overapproximates runtime reachability and is not the tested closure.
Package initializers/re-exports may add edges; inspect the runtime module inventory
alongside the AST graph. No claim of compatibility follows from graph membership.

| Upstream surface/package | Classification | Actual boundary/evidence |
|---|---|---|
| `engine.execute`, `ExecutionRequest`, `ExecutionResult` | Small adapter | Exact engine source; only `src.config.get_settings().public_url` replaced by explicit settings adapter. Successful result/status/variables/error paths tested. |
| `_execute_workflow_with_trace`, PEP 669 `sys.monitoring` | Works unchanged for tested functions | Locals captured on return; coercion/context/log/error fixtures pass. No tracing clone. |
| `bifrost._execution_context`, `_context` | Works unchanged for tested scope | ContextVar proxy visible in workflow; dynamic secret scrub exercised. Caller/org in harness are synthetic, not upstream authorization claims. |
| `bifrost.client`, integrations and tables | Works unchanged for tested requests | Actual injected `BifrostClient`; real network auth, Pydantic parsing, integration read, upsert and readback. No custom HTTP adapter needed with current Cloudflare Pyodide HTTPX package. |
| HTTPX 0.28.1 | Works with runtime-supplied package | Async HTTPS fanout and synchronous HTTPS both returned 200. Pylock selects Cloudflare/Pyodide wheel. This does not imply arbitrary socket libraries work. |
| Pydantic 2.14.2 / pydantic-core 2.50.2 | Works with wasm wheel | Real SDK DTOs validated on actual operations. Native Linux wheels cannot simply be copied. |
| JSON, asyncio sleep/gather, bundled dynamic Python import | Works unchanged in tested cases | JSON transform, three HTTPS requests, deferred import of `workspace_helper`. Not Redis-backed workspace resolution. |
| `virtual_import.py` | Pure Python; closure/runtime untested | Child `module_cache_sync` already supports scoped SDK HTTP without direct Redis/S3. Eager redis imports and thread-local assumptions need testing before claiming an adapter is necessary. Prototype only proves a bundled workspace import. |
| `async_executor.py`, workflow consumer | Remain upstream | Durable dispatch/DB/Redis/authorization are not shipped. No alternative authoritative queue created. |
| `worker_sdk_http.py` | OS server incompatible in isolate | Parent FastAPI/uvicorn Unix-socket service remains server-side. Ordinary API transport succeeds; private parent socket is unavailable. |
| `pool_main.py`, template process, launcher | Fundamentally incompatible unchanged | Linux child processes, signals, /proc CPU/memory sampling, admission and process termination do not map to Python Worker execution. |
| SQLAlchemy/asyncpg, Redis, S3 cache modules | Excluded, not validated | Platform owns these dependencies. A successful small engine closure does not make server modules wasm-compatible. |
| keyring/watchdog/textual/CLI/dev tooling | Optional/excluded | No interactive credential storage, file watcher or CLI is used in the Worker. |
| AI providers, native scientific packages, subprocess/thread execution | Unverified or incompatible OS facilities | No agent/native-package compatibility claim. `threading.Lock` creation in engine succeeds; starting threads/processes was not validated. |

The full `execute()` path still imports server settings at call time; the adapter
provides exactly the one used field. It does not issue authority, reconstruct
settings, alter results or replace engine behavior. Namespace scaffolding and
standalone decorator selection are intentional packaging differences. All are
material to reproducibility and must be maintained against the pinned upstream.

For subsequent unsupported dependencies, follow
[native-first dependency qualification](dependency-qualification.md). Distinguish
missing wheels from OS-contract incompatibility; attempt a native PyEmscripten
build for compilable candidates before considering replacement code.

## Control plane: A versus B

The current remote implementation is newer than a bare request/result RPC:
`remote/protocol.py` has HMAC-authenticated hello, monotonic sequences, session
generation and bounded/fragmented frames. `ownership.py` acquires and renews
database-clock owner leases. `fencing.py` checks the owner inside the DB transaction.
`remote/host.py::RemoteSession.may_accept` checks ownership before handling events;
the WebSocket receiver rejects stale generation/sequence. `launcher.py` physically
supervises children, acknowledges terminal delivery, retains pending terminal
inventory and handles handoff/drain. `workflow_execution.py` chooses credential
protocol before parent-side `issue_run_credentials`, then dispatches the child.

**A: remote protocol adapter.** Keep the API-owned consumer, durable claims,
authorization, credential issuance, fenced commits and recovery unchanged. A
trusted external launcher could translate physical process commands into Worker
invocations. It must preserve heartbeat, ownership loss, dispatch identity,
terminal inventory/ACK and stale result semantics. Merely speaking WebSocket or
returning JSON is insufficient. It also needs a reliably terminable, fresh security
boundary for each run. No such adapter is implemented here; adapting the actual
launcher would require meaningful supervision work, beyond a transport shim.

**B: direct API proof.** Implemented: a local driver invokes fixed workflows in
workerd; actual SDK operations call the authorized isolated API. No durable
execution claim, direct DB write, engine credential minting, or result commit is
performed by the prototype. It does not pretend its fixed execution UUID is a
platform execution. Debug user session credentials prove API compatibility only;
they are not scoped run credentials or an authorization-equivalence proof.

Recommend B only for continued experiments; A only after isolation feasibility
is established. A production adapter must never retain this driver credential
model, use an admin token for untrusted runs, or infer durable completion from HTTP
200. `HarnessError/TimeoutError` is explicitly not upstream `Timeout` status.

## Security and reliability results

| Test | Observation | Implication |
|---|---|---|
| I/O timeout | Two-second sleep canceled with 50 ms wait_for | Cooperative cancellation works; durable timeout status not reproduced. |
| CPU cancellation | 150 ms busy loop returned Success despite 50 ms timeout | Event-loop cancellation cannot preempt CPU Python. Linux parent kill semantics absent. |
| Cloud CPU interruption | Timer loop returned HTTP 500; telemetry traceback ends in `introspection.CpuLimitExceeded: Python Worker exceeded CPU time limit` | Platform interrupt bypasses ordinary engine result reporting. Cloud clocks do not advance like native perf_counter during CPU-only work; this is not an equivalent bounded CPU benchmark. |
| Cross-execution state | builtins marker written in A read by B | Fresh globals/context reset is insufficient; modules/interpreter shared. |
| Duplicate invocation | Same fixed request ID incremented global counter twice | No deduplication/side-effect fencing. Upstream must own this. |
| Invalid runtime credential shape | Synthetic token reached API; `BifrostAuthenticationError` | Actual API rejects invalid bearer. Run-specific scope/refresh not proven. |
| Registered secret in logs/exception | Synthetic value absent from returned logs/variables/error; error redacted | Upstream scrub works for this case. Not an exfiltration boundary against hostile code. |
| Stdout | print executed, absent from returned engine log list on both runtimes | Existing engine-only boundary; additional Linux worker logging plumbing not reproduced. |
| Oversized params | 70 KB parameters rejected 413 | Guard runs after JSON parse, not safe admission for arbitrary large bodies. |
| Worker termination | Container restart interrupted active ten-second I/O request | Request failed; no durable recovery or cancellation ACK. Local container death, not Cloudflare eviction. |
| Memory pressure | 160 MiB Python bytearray succeeded locally | Local dev does not validate documented 128 MB production enforcement. Exhaustion/neighbor behavior unverified. |
| Cloud memory | 160 MiB allocation succeeded; bounded 512 MiB allocation with page touches returned HTTP 503, `Worker exceeded resource limits` | Resource failure is real; peak memory and exact memory-versus-CPU cause not measured. Do not infer an effective 128 MB cutoff from allocation size. |
| Stale result submission | No authoritative result submission surface implemented | DB-fenced stale rejection is source-assessed only, not tested through adapter. |
| Concurrency | Four overlapping JSON requests returned expected values | No evidence of safe concurrent credentials/logging/monitoring. Global SDK client and root handlers remain shared. |

Fresh execution isolation is necessary for arbitrary hostile code under this
design. Standard Workers reuse isolates; an invocation is not a fresh Python
interpreter guarantee. Workflow code can inspect modules, alter SDK globals, reach
its credential objects, persist state or exfiltrate secrets through allowed HTTP.
Secret redaction cannot fix this. Running distinct tenants in one interpreter
cannot preserve upstream's one-shot child boundary. Evaluate a platform-supported
per-run sandbox/Worker design separately; do not promise an undeployed scheme is
equivalent. No claim that Cloudflare's isolation between separate Workers fails.

## Benchmark interpretation and cost

Raw records include client wall time, upstream duration, return/log/exception
payloads, warm samples and concurrency. `linux-engine.json` is a no-credential
comparison; `linux-engine-sdk.json` uses the real isolated API and Linux CPython
3.14.8 with the same source/fixtures. Both are **engine-only**, not full upstream
pool/dispatch benchmarks. There is no full Linux executor latency comparison.
The real debug pool was booted but not benchmarked via authoritative dispatch.

Production cold start, peak isolate memory and SDK-only overhead remain
**unmeasured**. Deployed CPU/wall telemetry is in `cloud-metrics.json`, scoped to
this exact Worker, with 49 log rows at collection. Console/error rows
(`httpStatus=0`) are excluded from invocation summaries. Observed CPU medians
include JSON 35 ms and HTTPS orchestration 29 ms; samples are small and JSON
combines initial, warm and concurrent requests. Five explicit warm JSON client
samples had a 62.75 ms median in the final cloud benchmark, including network.
Four concurrent JSON requests returned correct values, with variable latency.
Cloud startup validation reported 457-939 ms across deployments; this is not
invocation cold-start latency. See `cloud-imports.json` for observed first
authenticated client latency, also not a cold-start claim.

Local timings include Docker Desktop, shared-host load,
client creation, HTTP/JSON/FFI and API latency. There is no direct FFI microbenchmark
or apples-to-apples overhead decomposition; do not subtract these runs to infer
Pyodide overhead. Successive fixture runs may warm caches. Bundle dry-run:
9,246.93 KiB total / 2,432.48 KiB gzip at that experiment revision (later probe
fixtures change this slightly). Vendored package data dominate the bundle.

At current published paid marginal rates, **estimate only**, for mean CPU c ms:
`cost/1000 = $0.0003 + $0.00002*c`, excluding the $5 monthly minimum, included
allowances and upstream API/Linux/control-plane costs. For example c=10,100,1000
would yield $0.0005,$0.0023,$0.0203. `metrics-summary.json` applies the formula to
observed request mean CPU. These are marginal estimates, not invoices or full
system economics. Cloud integration/table requests fail without credentials
inside HTTP 200 envelopes; their request costs are not successful SDK execution
costs. I/O wall time is not billable CPU time.

## Routing recommendation

| Workload | Recommendation |
|---|---|
| Short I/O-heavy trusted automations | Promising restricted pilot after cloud measurements and ownership adapter; actual HTTP/SDK subset works. |
| Medium async workflows | Conditional: connected request lifetime, deadlines, eviction/recovery and cancellation need proof. Keep Linux default. |
| CPU-heavy workflows | Retain Linux: preemption, 128 MB budget, CPU ceiling and wasm overhead remain limiting/unmeasured. |
| Arbitrary user Python | Reject shared Worker design; fresh security boundary and runtime credential isolation unresolved. |
| Persistent services | Retain supervised Linux services; Worker request lifecycle is not persistent process ownership. |
| Agent workloads | Retain Linux pending provider/tool/package/streaming, multi-step durability and cancellation experiments. |

Next experiments: (1) cloud-to-isolated-API reachability with genuine run-scoped
credentials and measured SDK overhead, (2) fresh per-run security-boundary feasibility with hostile state and
credential probes, (3) only then a small launcher adapter that exercises actual
lease replacement, stale terminal events, cancellation and recovery against the API.

## Cloud resource disposition

User-directed `cf` CLI 1.0.0-beta.22 had valid authentication independent of
Wrangler. `cf migrate` generated only this companion package's config/dependency
changes. Python packages were copied from tested Linux `python_modules`, since
failed Windows sync left them absent. A smaller cf dry-run before copying these
packages was insufficient and was not used for cloud proof. Full deploy included
352 modules: 9248.29 KiB / gzip 2433.44 KiB at final revision.

The dedicated Worker temporarily had a workers.dev route behind a generated
test secret, 1000 ms CPU setting and scoped invocation logs. No Bifrost credential
was uploaded; the entrypoint rejects SDK-token forwarding. Authentication 401,
forwarding 403, payload 413, state contamination and duplicates were tested
remotely. The PowerShell RNG API initially failed and emitted a predictable test
gate; it was corrected and rotated before measurements. Only fixed,
credential-free fixtures existed during that interval.

Public workers.dev and preview URLs are now disabled, observability disabled,
and the isolated preview was deleted. The Worker remains uploaded with no public
targets, no storage/queue/service bindings, and only its test auth secret.
Final version: `dc47f427-5115-4aa6-bad6-92213520be41`. No shared infrastructure or
production platform source was changed. CLI receipts and post-cleanup HTTP
readback are retained separately from runtime benchmark artifacts.

## Current vendor facts, checked 2026-10-30 local / 2026-10-31 UTC

Cloudflare docs report 128 MB **per isolate including wasm**, shared by concurrent
invocations; paid CPU defaults to 30 s and can be configured to 5 min; free CPU
is 10 ms. HTTP wall lifetime depends on the connected client; waitUntil is not
durable recovery. Current docs list a 64 MiB Worker size limit and one-second
startup budget. Python snapshots/package loading affect startup and bundle
interpretation. Runtime filesystem state is ephemeral with isolate lifetime.
These are vendor constraints, not local measurements.

- [Python runtime and snapshots](https://developers.cloudflare.com/workers/languages/python/how-python-workers-work/)
- [Python packages](https://developers.cloudflare.com/workers/languages/python/packages/)
- [Python standard library/filesystem](https://developers.cloudflare.com/workers/languages/python/stdlib/)
- [Workers limits](https://developers.cloudflare.com/workers/platform/limits/)
- [Pricing](https://developers.cloudflare.com/workers/platform/pricing/)
- [Workers execution model](https://developers.cloudflare.com/workers/reference/how-workers-works/)
