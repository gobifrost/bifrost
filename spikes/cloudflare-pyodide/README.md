# BiFrost on Python Workers: empirical spike 0.1.0

Current frontier (2026-11-03): real upstream engine/SDK execution is qualified
locally through canonical claim/result/replay paths, with historical deployed
SDK proofs kept separately. This is experimental YELLOW, not a production
executor. Public/untrusted admission stays disabled. Start with
[the acceptance ledger](ACCEPTANCE.md), then these recent increments:

* [Canonical synthetic HTTPS](backend/CANONICAL-HTTPS.md): actual engine/SDK,
  run identity, tenant separation and Failed/Success/replay/cleanup pass alongside
  controlled HTTPX vendor calls. The separate local candidate includes the
  [pinned redirect repair](backend/HTTPX-REDIRECT-QUALIFICATION.md); general
  HTTPS admission and deployed canonical execution remain unqualified.
* [Startup repair candidate](backend/STARTUP-CANDIDATE.md): 12 source/mock cases,
  [six real Redis cases](backend/STARTUP-REDIS.md), and one
  [signed Linux workflow/retirement](backend/STARTUP-LINUX.md) pass with an isolated
  API overlay. Shared-source adoption is still coordinated and unapplied.
* [Actual in-flight HTTP cancellation](backend/HTTP-CONTEXT-LIFETIME.md): local
  connection closure and retained-context control, with bounded effect observation.
  [CPU continuation/effect refusal](backend/CPU-EGRESS-DEADLINE.md) separately proves
  capability expiry while finite CPU work continues; neither proves CPU stop.
* [Fresh loader staging](dev/LOADER-STAGING.md): missing SDK cleanup JavaScript
  dependency is now copied and hash-recorded. Fresh-directory assembly passes;
  no deployed Python/SDK compatibility claim is added by that packaging test.
  [Explicit current artifact staging](dev/QUALIFIED-RUNTIME-STAGING.md) preserves
  the exact SDK bundle bytes and imports the full loader offline on CT128.
  The [fresh staged runtime](backend/STAGED-RUNTIME-EXECUTION.md) also passes two
  real local engine/SDK attempts with canonical replay, revocation and cleanup.

* [Upstream SDK #1217 real execution](backend/SDK1217-EXECUTION.md): config/table
  operations pass with the exact SDK update and pure wheel. [Awaited SDK close](backend/SDK-CLOSE.md)
  passes with both SDK versions; custom TLS and interrupted cleanup remain open.
  Earlier receipts retain their pins.
* [Owned SDK close completion](backend/OWNED-SDK-CLOSE.md): cooperative waiter
  cancellation no longer skips sync close; actual platform interruption remains unproved.
* [RPC result lifecycle](backend/RPC-RESULT-LIFECYCLE.md): documented result/promise
  disposal works locally; a gated I/O call still completes after pending-promise
  disposal. This is resource management, not a physical-stop receipt.
* [Private no-send refusal acknowledgement](backend/REFUSAL-ACK.md): explicit
  canonical consumption permits cleanup without a synthetic workflow result;
  automatic public-host integration remains open.
* [Canonical generic setup-failure fence](backend/SETUP-FAILURE-FIX.md): owned
  writes and post-write notification guards pass real PostgreSQL controls;
  companion refusal acknowledgement remains separate.
* [Exact package binding](backend/PACKAGE-BINDING.md): declared versions must
  match the observed pinned runtime before dispatch; compatibility still needs
  execution evidence.
* [Private declared-capability check](backend/CAPABILITY-CONTRACT.md): pinned,
  reviewed workflows require an explicit supported declaration before dispatch;
  arbitrary-code discovery and admission remain unsupported.
  [API-only egress correction](backend/HTTPS-CAPABILITY.md) refuses direct external
  HTTPS before effects; an approved-origin adapter is still required for it.
* [HTTP transport option boundary](backend/TRANSPORT-OPTIONS.md): 26 actual local
  constructor cases silently accept unsupported settings; pre-effect enforcement
  remains incomplete and untrusted admission stays disabled.
* [Lossless JSON and measured parent overhead](backend/JSON-TRANSPORT-PERFORMANCE.md)
* [Output-frame and child-body budgets](backend/BODY-BUDGET.md)
* [Local memory boundary](backend/MEMORY-PRESSURE.md)
* [Actual run refresh/redaction](backend/REFRESH-REDACTION.md)
* [Pre-launch fencing and canonical retirement gap](backend/PRELAUNCH-RETENTION.md)
* [Workspace import provenance](backend/IMPORT-PROVENANCE.md), including
  [runtime bootstrap namespace refusal](backend/BOOTSTRAP-PROVENANCE.md)

Full native startup, public-host refusal-ACK integration, deployed memory/
isolation and abnormal cancellation remain incomplete. Existing cloud test
allowances are retired; this continuation makes no new cloud invocation or
deployment. Shared-interface coordination was denied by the existing caller
(`source_run_not_active`, `safe_to_resend:false`); the exact denial and required
receipt reconciliation are preserved in PRELAUNCH-RETENTION.md. Do not retry it
under another caller. The entries below retain earlier stages and their evidence.

Latest integrity control: [source change after preparation](backend/SOURCE-CHANGE-CONTROL.md).
Two exact-candidate local runs reject a changed eager dependency before workflow
effects while preserving the held peer. Failed predecessors, upstream empty-log
semantics and the bounded HTTP-denial fix are preserved; deployed acceptance remains open.

Latest source preparation: [authenticated API workspace pins](backend/API-WORKSPACE-PREPARE.md).
Real two-tenant execution now uses API-derived entry/dependency hashes instead
of fixture-supplied pins. Twenty-three focused tests and the real local lifecycle
pass; dynamic dependency discovery, atomic revisions and deployed admission remain open.

Latest implementation: [reusable private HTTP workflow adapter](backend/PRIVATE-HTTP-POOL.md).
The shared fixture now delegates to backend code around the actual native pool.
Seventeen focused tests and real two-tenant engine/SDK execution pass. Normal
manifest preparation for declared imports now has the follow-on above; public
startup and deployed cancellation remain open.

Latest real concurrency qualification: [one native pool, two tenant workflows](backend/SHARED-NATIVE-POOL.md).
Both actual engine/SDK runs reach independent effects concurrently; failure,
lost ACK and owner reconnect preserve the successful peer. This is warm local
qualification, with public admission and deployed cancellation still unaccepted.

Latest native request work: [attempt-scoped collection](backend/WORKFLOW-REQUEST-MUX.md).
Concurrent synthetic collectors retain separate results; owner-rotation failures
were reproduced and repaired. The shared-pool follow-on above adds real local execution.

Latest transport increment: [per-attempt HTTP invocation](backend/HTTP-INVOCATION.md).
Real local engine/SDK execution and late terminal reconciliation pass through
the extracted component. Combined deployed lifecycle and cancellation remain open.

Latest lifecycle qualification: [late finite completion after Timeout](backend/LATE-SETTLEMENT.md).
An actual 40-second engine/SDK success now returns after canonical Timeout; the
real result/ACK path releases its held handle without overwriting the outcome,
relaunching, or requiring a per-job process kill. Deployed cancellation and
untrusted admission remain unaccepted.

Latest native contract repair: [opaque inventory uncertainty](backend/INVENTORY-UNCERTAINTY.md).
44 focused native cases pass, including Linux controls, terminal replay and owner
fencing. Missing invocation inventory no longer fabricates a Linux crash result.

Latest implementation: [per-attempt deadline with a healthy relay](backend/ATTEMPT-DEADLINE.md).
An owned native timer now delivers the original cutoff through upstream's result
consumer, persists Timeout and revokes credentials without releasing an unresolved
invocation. Lost delivery receipt/replay and ten focused helper tests pass locally.
Production startup/admission and deployed physical settlement remain unaccepted.

The [automatic relay-loss recovery qualification](backend/AUTOMATIC-RECOVERY.md)
separately proves the existing API loop's worker-loss path.
The existing API loop automatically ends the run after real relay silence,
revokes credentials and fences stale dispatch. The negative control leaves the
run Running while a relay heartbeats despite provider loss; the deadline adapter
above addresses the cutoff path, with full runtime integration still pending. The prior
[streamed provider interruption](backend/STREAM-PROVIDER-INTERRUPTION.md) and this
follow-on both retain unresolved physical obligations; neither establishes
Cloudflare per-isolate cancellation acceptance.

Latest integration: [accepted stream loss and canonical reconciliation](backend/STREAM-LOSS-RECONCILIATION.md).
The real ThinRemoteProcessPool now passes a local workerd engine/SDK run and
canonical lost-ACK replay without a fake PID. Prompt dispatch ACK now passes
locally, including retained-result recovery after stream loss. This private
adapter still awaits authorized runtime selection and deployed resource/
cancellation qualification; public Pyodide admission remains disabled.

Current lifecycle assessment: [normal completion and abnormal cancellation](backend/LIFECYCLE.md).
Normal success requires awaited work, completed streams and released returned
RPC stubs, not destruction of an isolate. The [local native tail controls](backend/TAIL-OBSERVER-QUALIFICATION.md)
observe completion after bounded waitUntil work; an aborted I/O case also reports
ok, so tail outcome alone is not cancellation proof. Custom CPU-limit exceptions
and plan-level enforcement are separate. Untrusted admission remains disabled.
The [actual cloud engine/SDK benchmarks](dev/CLOUD-BENCHMARK.md) are fresh-child
round trips including execution, not pure startup measurements. Historical
milestones below retain their original scope and do not override this assessment.

Latest guest evidence: [five-workload real engine/SDK measurements](WORKERD-BENCHMARK.md),
[real Linux signed-control reference](backend/LINUX-CONTROL-REFERENCE.md), and
[Python RPC cancellation counterexample](backend/RPC-TERMINATION-QUALIFICATION.md).
Both fetch and RPC cancellation permit post-abort CPU effects. Untrusted
admission and deployed acceptance remain held.
The benchmark now includes the same pinned workloads on the real signed-control
Linux executor, with raw samples and explicit comparison limits.
The [lost-worker recovery fixture](backend/WORKER-LOSS-RECOVERY.md) also preserves
canonical failure against a late real SDK success, revokes credentials before
delivery, and exercises upstream terminal-log recovery without redispatch.
An [actual local provider interruption](backend/PROVIDER-INTERRUPTION.md) now
exercises socket loss after an SDK effect and explicit canonical recovery. It
does not establish per-isolate physical stop or automatic failure detection.

Guest offload update: the publisher-source build now reproduces the exact pinned
bundle, resolving the recorded HTTP 403 supply blocker without retrying it.
[Actual workerd ABI/native validation passes](SOURCE-RUNTIME-QUALIFICATION.md)
network-disabled in CT128. Its independent timed observer also records a CPU
effect 1.67 seconds after abort, so physical termination remains unqualified.
The [original CT128 frontier](backend/CT128-RUNTIME-FRONTIER.md) is preserved as history.
Independent [native package qualification](backend/NATIVE-PACKAGE-QUALIFICATION.md)
now runs official Pyodide 314.0.6 in CT128: 32 upstream pydantic-core and 261
PyYAML native cases pass. This does not replace or qualify Cloudflare's bundle.
The same guest interpreter also passes four [actual upstream engine cases](backend/GUEST-ENGINE-SEMANTICS.md),
including async context, partial-success status and exception redaction.
Actual guest [workerd SDK/canonical-consumer execution](WORKERD-SDK-QUALIFICATION.md)
now passes both cold and warm cases against the isolated upstream API, including
integration reads, encrypted synthetic-secret redaction and table writes. The
earlier [Node-hosted proof](NODE-SDK-QUALIFICATION.md) is preserved separately.
Neither establishes deployed Cloudflare qualification or physical termination.
Current work uses only the existing bounded CT128 allocation. Laptop Docker
remains stopped; no new cloud invocation or deployment is authorized.
Guest [WASM memory observations](NODE-MEMORY-QUALIFICATION.md) are now preserved
for that actual SDK workload; they do not establish total Cloudflare isolate memory.

**YELLOW for fixed, trusted, short workflows. Arbitrary untrusted Python is not
accepted pending abnormal cancellation, resource and isolation qualification.** Real upstream `engine.execute()` and unmodified SDK
integration reads/table writes ran in local Cloudflare workerd/Pyodide. This is
not a production execution target or a completed remote-launcher implementation.

Historical spike authority: gobifrost/bifrost at `e5da6d0c1fb751d4155b1b65458bfedfc2bec112`.
The follow-on backend work is reconciled with upstream `e611d31bbba3d7601980271c774cad5e821b1e97`.
It adds two small engine/worker seams; see [current acceptance](IMPLEMENTATION.md)
and [proposed physical contract](backend/CONTRACT.md). Historical measurements
below do not establish acceptance of that backend.
The approved fresh-cloud follow-on executed the current engine successfully,
but CPU cancellation still permits later effects and the provider CPU exception
can be caught. See the final section of [IMPLEMENTATION.md](IMPLEMENTATION.md).
See [assessment.md](assessment.md), [experiments.md](experiments.md), and
[raw evidence](results/). No production resources or credentials were used.

Unsupported dependency work follows [native-first qualification](dependency-qualification.md):
classify, build if compilable, run relevant package tests under Pyodide, then
verify actual cloud execution. Missing-wheel and OS-contract failures are distinct.

PR data-flow review and the disposition of all 15 CodeQL comments are in
[CODEQL_REVIEW.md](CODEQL_REVIEW.md). Run its focused transport/integrity tests
with `node --test probe-io.test.mjs backend/module-response.test.mjs`. These tests
make no live cloud requests and do not renew the retired cloud experiment.

## Reproduce

Latest local qualification: [two concurrent canonical tenant runs](backend/CANONICAL-ISOLATION.md).
The follow-on [fixture admission check](backend/FIXTURE-ADMISSION.md) rejects
unsupported contexts before runtime/API effects and retains real SDK qualification.
Warm execution passes with real run-scoped SDK identity; cold concurrent startup
fails in the tested native workerd runtime. This does not resolve physical CPU
termination or deployed end-to-end acceptance.

Requirements: Docker, Python 3.13+ for source preparation, Node 24+, Git Bash on
Windows for the repository debug launcher. From the worktree root:

```powershell
py -3 spikes/cloudflare-pyodide/prepare.py
docker build -f spikes/cloudflare-pyodide/Dockerfile.tools -t bifrost-pyodide-tools:ddf316c1 spikes/cloudflare-pyodide
```

Start the local Worker (replace the absolute host source with this directory):

```powershell
docker run -d --name bifrost-pyodide-ddf316c1 -p 127.0.0.1:8793:8793 --mount type=bind,source=C:/Users/ThomasBray/.t3/worktrees/bifrost/t3-ddf316c1/spikes/cloudflare-pyodide,target=/input,readonly bifrost-pyodide-tools:ddf316c1 sh -c 'cp -r /input/src /spike/src && cp /input/pyproject.toml /input/uv.lock /input/pylock.toml /input/package.json /input/package-lock.json /input/wrangler.jsonc /spike/ && npm ci --ignore-scripts && pywrangler dev --ip 0.0.0.0 --port 8793'
docker logs bifrost-pyodide-ddf316c1 --tail 10
```

Wait for Wrangler's `Ready` before sending requests. No credentials are needed
for the first pass. From this directory:

```powershell
node bench.mjs
node verify.mjs
node verify-sources.mjs
```

For real SDK operations, boot **this worktree's** isolated stack from its root:

```powershell
& 'C:/Program Files/Git/bin/bash.exe' ./debug.sh up
& 'C:/Program Files/Git/bin/bash.exe' ./debug.sh status
```

This recorded worktree uses `http://localhost:39046`, port mode, the documented
debug seed login, and project `bifrost-debug-19e8149c`. `sdk-proof.mjs` and
`src/probe.py` intentionally pin this isolated target. Change both URLs explicitly
if your worktree allocates another port; never point them at a shared/live API.
The login exists only inside a disposable development stack. Tokens are held in
memory and omitted from artifacts. No user's saved Bifrost login is selected.

```powershell
node sdk-proof.mjs
node verify.mjs results/benchmark-sdk.json --sdk
node security.mjs
docker cp linux_bench.py bifrost-pyodide-ddf316c1:/spike/linux_bench.py
docker exec bifrost-pyodide-ddf316c1 uv run python linux_bench.py
docker cp bifrost-pyodide-ddf316c1:/spike/linux-benchmark.json results/linux-engine-sdk.json
node termination.mjs
```

Run security and SDK scripts sequentially. The last script intentionally
restarts only the spike container during a fixed sleeping fixture. It proves
interruption, not durable recovery. Memory and synchronous HTTP probes:

```powershell
Invoke-RestMethod http://localhost:8793 -Method Post -ContentType application/json -Body '{"workload":"memory_probe"}'
Invoke-RestMethod http://localhost:8793 -Method Post -ContentType application/json -Body '{"workload":"sync_http_probe"}'
```

The source mount is read-only and copied at start. For iteration, `docker cp
src/. bifrost-pyodide-ddf316c1:/spike/src/`; let Wrangler finish hot reload before
invoking a new fixture. Generated source copies and environments are ignored.

## Bundle and deployment

```powershell
docker exec bifrost-pyodide-ddf316c1 npx wrangler deploy --dry-run --outdir /spike/dist
```

Dry-run and **cloud deployment through `cf` passed**. The existing Wrangler login
was expired, but `cf auth whoami` confirmed independent valid authentication.
The companion config was migrated with `cf migrate wrangler.jsonc`. From this
directory, using that existing cf installation:

```powershell
npm ci
docker cp bifrost-pyodide-ddf316c1:/spike/python_modules/. python_modules/
cf deploy --dry-run
cf deploy --provision=false
```

Final config disables public workers.dev/preview URLs and observability. No
queues, databases or service bindings are provisioned. Temporary cloud tests
enabled workersDev and observability behind a generated `SPIKE_AUTH` test secret
with `cf deploy --secrets-file <temporary-json-file>`. `cloud.mjs` and
`cloud-security.mjs` read that secret from the OS temp directory, never artifacts.
For a deliberate rerun, temporarily set `workersDev: true` and
`observability.enabled: true` in `cloudflare.config.ts`, then run:

```powershell
./cloud-auth.ps1
cf deploy --provision=false --secrets-file "$env:TEMP/bifrost-pyodide-ddf316c1-secrets.json"
node cloud.mjs
node cloud-security.mjs
node verify.mjs results/benchmark-cloud.json
```

Restore workersDev/observability to false and deploy afterward. The cloud runtime
probes used **no Bifrost credentials** and explicitly reject their forwarding.
The SDK URL remains local. Never add arbitrary-code request bodies. Cloud raw
CPU/wall telemetry, resource failures and measured cost inputs are in `results/`;
`measurements.html` is a standalone chart. `summarize.py` rebuilds the summary.

Native Windows pywrangler 1.17.9 resolved the system 3.13 interpreter during wasm
installation despite a selected 3.14 environment; Docker avoids that observed
tooling failure. Tested versions: Wrangler 4.156.0, workers-py 1.17.9,
Pyodide 314.0.7 / CPython 3.14.2 (see pylock); Linux CPython 3.14.8; global cf
1.0.0-beta.22 (migration's locked local cf package is beta.26).

## Cleanup

The [combined canonical local qualification](backend/CANONICAL-LOCAL.md) now
connects real dispatch claim, consumer-issued credentials, fresh Pyodide engine
and SDK execution, shared envelope, persisted result/log and lost-ACK replay.
It is local evidence; cloud and physical-stop acceptance remain incomplete.
The [post-commit fault matrix](backend/POSTCOMMIT-RECOVERY.md) additionally shows
durable outcomes surviving while notification/cache follow-up remains unrepaired
and native at-least-once log recovery may append duplicates.

For the subsequent real upstream worker + authorized SDK qualification in local
workerd, see [authorized local setup and evidence](backend/AUTHORIZED-LOCAL.md).
This proves actual API interaction locally; it does not transfer that proof to
cloud deployment or canonical dispatch/terminal acceptance.
The subsequent [host transport journal experiment](backend/CONTRACT-JOURNAL.md)
tests duplicate delivery and reconciliation against upstream PostgreSQL fences
and claim-protected result writes, without changing the Linux adapter.

The approved fresh-cloud experiment used 16 of 20 invocations and is retired:
no public/previews, no remote secret binding, HTTP404. Verify retained artifacts
with `node backend/verify-fresh-cloud.mjs` (expected exit 1 for physical-stop
incompatibility) and `node backend/fresh-cleanup-readback.mjs`. Replaying a live
experiment requires current authority; do not reset `fresh-cloud-budget.json`.
`isolation-cloud` and `engine-cloud` modes require an explicit future
`SPIKE_EXPIRES_AT` Unix-millisecond environment value and a secrets file passed
to cf deploy. The default expiry is zero (fail closed). `isolation` mode retires
public access. Never put a BiFrost credential into these diagnostic modes.

```powershell
docker stop bifrost-pyodide-ddf316c1
docker rm bifrost-pyodide-ddf316c1
```

From the worktree root, `./debug.sh down` removes only this debug stack and its
disposable data. Never run cleanup against another project's containers.
