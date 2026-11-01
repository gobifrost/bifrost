# BiFrost on Python Workers: empirical spike 0.1.0

Guest offload update: CT128 protocol tests and control authentication qualified;
the subsequent pinned Pyodide runtime attempt is blocked before Python startup
by an HTTP 403 for its exact official bundle. See
[CT128 runtime frontier](backend/CT128-RUNTIME-FRONTIER.md) for preserved evidence,
the prepared (unexecuted) timestamped termination fixture and remaining gates.
Independent [native package qualification](backend/NATIVE-PACKAGE-QUALIFICATION.md)
now runs official Pyodide 314.0.6 in CT128: 32 upstream pydantic-core and 261
PyYAML native cases pass. This does not replace or qualify Cloudflare's bundle.
The same guest interpreter also passes four [actual upstream engine cases](backend/GUEST-ENGINE-SEMANTICS.md),
including async context, partial-success status and exception redaction. SDK
operations and Cloudflare execution remain separate acceptance gates.

**YELLOW for fixed, trusted, short workflows. RED for arbitrary untrusted Python
under the current termination contract, including fresh isolates.** Real upstream `engine.execute()` and unmodified SDK
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
