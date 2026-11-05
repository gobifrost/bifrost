# Isolated development deployment

[Latest guarded canonical integration](GUARDED-LOCAL.md) now passes the real
engine/SDK, duplicate/reconciliation and cleanup fixture locally. Deployed
combined acceptance remains separate.

[Optional provider configuration and boundaries](PROVIDERS.md): Cloudflare
hosting/ingress/execution are independent choices behind upstream contracts.

Current preparation: [guarded canonical candidate](CANONICAL-CANDIDATE.md) builds
offline with cf CLI; [closed-guard/API alignment](CANONICAL-ALIGNMENT.md) verifies
local denial and an actual dev-schema mismatch. This candidate is not deployed.

[Matched API runtime](MATCHED-API.md) now boots at the canonical source/schema
in the isolated test project. Changed candidate admission has focused regression
coverage; execution through that changed entry and its deployment remain pending.

Latest cancellation qualification: [deployed incoming disconnect](REQUEST-SIGNAL-CLOUD.md).
The parent and I/O delivery controls pass; CPU physical termination remains
unproved. The temporary flag, endpoint and guard were retired after the test.

Latest termination result: [deployed CPU limit placement](CPU-LIMIT-CLOUD.md).
Both supported placements raised catchable CpuLimitExceeded and permitted
subsequent authenticated API effects. Untrusted backend admission stays disabled;
the temporary test endpoint and guard are retired.

Experimental deployment of upstream source `2037ac0d9ec95e94a77ce440001ae016d4215407`.
The companion deployment helpers do not modify production infrastructure or upstream orchestration code.

## Observed 2026-11-02

- pve-t340 CT128 (`bifrost-pyodide-test-01`): expanded with explicit user authorization to 8 GiB RAM; 2 CPUs, no swap, 40 GiB root unchanged.
- Exclusive checkout: `/opt/bifrost-tests/pyodide-ddf316c1/dev-2037ac0`.
- Compose project `bifrost-debug-074217d4`: healthy API, Linux worker, scheduler, PostgreSQL, PgBouncer, Redis; SeaweedFS and compiled nginx frontend running.
- Initial Vite process hit its 768 MiB cap. Its failure state was retained; replaced with upstream compiled assets, built successfully in a bounded 5 GiB single-job container.
- Actual Cloudflare frontend: https://bifrost-pyodide-dev-ui-ddf316c1.midtown-technology-group.workers.dev
- Version `29751da1-8074-499a-bec5-4e570c125347`, deployed with `cf deploy` (not a direct Wrangler command).
- Four routing regression tests passed: `node --test edge.test.mjs` from `frontend/`.
- Cloud HTTP readback: `/` 200, `/health/ready` 200, `/__bifrost_modules/react.js` 200, `/_public_form_frame_policy` 404, `/api/workflows` 401.
- Authenticated qualification from CT128 stopped at Cloudflare HTTP 403/error 1010. No security control was changed or alternate caller used. Error body SHA256 `2938e9f1284180959e33ab1718d0793a72ff6e4cdb8108c34dcd14e69446de5c`.
- Resolved that harness failure using Cloudflare's documented descriptive application User-Agent (`bifrost-pyodide-dev-qualification/1.0`), replacing urllib's library default. No browser impersonation, WAF setting, access grant or alternate identity. See [official error 1010 guidance](https://developers.cloudflare.com/support/troubleshooting/http-status-codes/cloudflare-1xxx-errors/error-1010/).
- Actual authenticated cloud-to-guest readback now passes: login issues an upstream access token, then workflows, tables, forms, agents and integrations each return HTTP 200. Sanitized evidence is in `edge-auth-readback.json`; credentials/tokens never leave guest memory or appear in the receipt.

The compiled UI executes on Workers. API requests are proxied to the temporary origin; API/business logic, persistence, scheduling and ordinary UI-dispatched workflow execution remain on pve-t340. Physical termination of noncooperative Python remains unproven and untrusted workflow admission remains disabled.

## Real upstream engine and SDK on Cloudflare

[CLOUD-BENCHMARK.md](../CLOUD-BENCHMARK.md) records five later fresh-child
workloads with15 correctness samples, measured request round trips and explicit
frozen-timer/cost-estimate limitations. Its test window is retired. The updated
run_cloud_qualification.py expects benchmark-workloads.py staged beside
authorized-local.py in the owned guest controller directory; run_cloud_benchmark.py
executes the five fixed selections serially under one exclusive ledger.

The later [stream-log qualification](STREAM-LOGS.md) overlays the shared engine
revision f49d4ba99 into a separately pinned runtime artifact. Actual deployed
stdout/stderr are redacted and persist through existing upstream log recovery
into PostgreSQL, alongside config/table SDK success. Earlier receipts below
retain their original closure/source pins. Current stage_runtime.mjs replaces
only that qualified engine and the companion bootstrap; it verifies both pins
and leaves all453 other module/native artifacts unchanged.

`cloud-sdk-success.json` contains four successful actual deployed Python Worker executions, including three integration/fresh-state runs. The fixture uses upstream `run_execution`, engine, virtual imports, SDK, run-credential issuance and API authorization; no SDK responses are mocked. Its isolated fixture rows use the existing committed token-test world and are cleaned up. This is explicitly not a production execution adapter or a substitute authoritative job queue.

Observed:

- Hash-pinned workflow and user helper loaded through authenticated API endpoints.
- Async workflow returned JSON; configuration read and table upsert produced independently read-back value `70`.
- Integration configuration and encrypted synthetic integration secret were read through the actual SDK. Secret values and run tokens were redacted from result/log/variable output; no server signing key was passed to Cloudflare.
- Cross-tenant configuration/integration reads denied with 403; invalid run credential denied with 401 before dynamic execution. Fixture terminal state and deletion invalidate its credential (401).
- Three fresh loads checked builtins, imported-module state, environment and `/tmp` before SDK effects, found no preceding marker, then intentionally left markers behind. This supports fresh per-run state isolation for the tested trusted fixture; it does not prove hostile-code containment or physical termination.
- Unsupported canonical dispatch mode is rejected (422) before module/SDK effects. Canonical ownership, fencing and terminal reconciliation remain upstream responsibilities and are NOT implemented by this endpoint.

The original prepared runtime SHA256 is `e1cca587e10cb755d49df0f2325c038a361a25f7031142a9e9e896a3c004bd39`. Source `785b560b9973020e1c13401383e1a73a87a54940` has no `api/src` or `api/bifrost` differences from dev source `2037ac0`. Runtime guard checks Pyodide 314.0.6, CPython 3.14.2, `cpython-314-wasm32-emscripten`, Emscripten 5.0.3. The compiled native Pydantic wheels and earlier dependency tests remain documented in the spike's existing receipts. Current staging derives a new artifact by replacing only the companion bootstrap; [PUBLIC-URL.md](PUBLIC-URL.md) records its separate provenance and actual local context qualification. That correction is not yet deployed. `source-manifest.json` remains the historical initial-deployment manifest, not a current-tree fingerprint.

Experiment progression: global fetch to another same-zone Worker returned Cloudflare text/plain 404; the supported Service Binding fixed it. Four subrequests were insufficient for the real SDK path (actual `Too many subrequests` failure); eight passed config/table execution, twelve cover integration/peer-denial qualification. CPU remains bounded at 1000 ms. Authorization and destination allowlists were not widened. Supervisor bundle upload: ~14,589 KiB uncompressed/~4,126 KiB gzip. Cloud isolate peak memory and CPU consumption remain unavailable; engine `duration_ms` is not billable CPU or full cold-start latency.

Latest successful open version: `0196e183-467e-446d-8cfa-3a0c89e1506b`. The private run window reserved 13 of at most 20 HTTP invocations (includes rejected/preflight requests, so this overcounts fresh Python creations). No old canary allowance was reused. The test endpoint was then closed with `cf deploy`, expiry zero, workers.dev disabled; closed version `a4cd8d49-da20-4732-8c43-ad56aea2c250`. Temporary `DEV_AUTH` was deleted through cf CLI; secret deletion produced final version `6ae88855-3338-43d7-85e7-4b1700e86f70`, deployment `e52f84d9-77e2-4a74-8219-4fcb4402d54f`. Secret-list readback was empty. An HTTP request carrying the retired secret returned 404. Read-only SQL verification found zero qualification tables, workflows and configs remaining. No dev volumes were removed.

Decision: **YELLOW for short trusted I/O workflows; RED for arbitrary untrusted Python until physical termination is proven.** This is real cloud engine/SDK execution, but not canonical end-to-end UI dispatch acceptance.

## Reproduction

Guest-only deployment helpers require the assigned checkout and private per-deployment generated dev credentials. Never copy production credentials. `prepare_stack.py` starts the actual `debug.sh` stack; `serve_compiled.py` installs the compiled frontend; `verify_stack.py` checks readiness and password configuration without printing secrets. Preserve existing volumes and uncertain locks.

Frontend: `npm ci --ignore-scripts --no-audit --no-fund`, `node --test edge.test.mjs`, then set `DEV_API_ORIGIN` to the approved HTTPS origin and `DEV_EXPIRES_AT` to a bounded Unix timestamp in milliseconds. Run `cf deploy --dry-run`, then `cf deploy --prebuilt`. Build output and assets are ignored. Rebuild whenever origin or expiry changes.

The current UI expires at `1793721995178` milliseconds since epoch. Its anonymous cf CLI tunnel runs inside the owned CT128 container under `timeout 86400`, with no restart policy. Expiration returns 410; it does not delete assets or the dev stack. Cleanup must stop only the owned tunnel and disable the owned dev Worker, preserving dev volumes. Do not run `debug.sh down` as incidental cleanup: it deletes volumes.

Executor: stage the exact public prepared artifact with `node stage_runtime.mjs <artifact>`, then `cd executor && npm ci --ignore-scripts --no-audit --no-fund && node --test policy.test.mjs`. The default config is closed. An approved bounded run sets `DEV_EXECUTOR_OPEN=true` and `DEV_EXECUTOR_EXPIRES_AT` (milliseconds) and uses `cf deploy --secrets-file <private-DEV_AUTH-only.json>`. Never commit the private file. Inside the assigned guest, stage its expiring private profile and run `python3 run_cloud_qualification.py --integration-fresh` (one job, 768 MiB/1 CPU/180-second bound). `export_receipts.py` exports only successful sanitized fixture JSON. Close using `DEV_EXECUTOR_OPEN=false DEV_EXECUTOR_EXPIRES_AT=0 cf deploy`, delete `DEV_AUTH` with cf CLI and verify public closure.

Next: integrate the smallest physical runtime boundary with canonical dispatch/attempt/result ownership, with explicit capability rejection while physical stop is unsupported. Shared remote-pool source ownership must be reconciled before touching it. Do not fake a Linux PID, silently fall back, or infer API portability from successful proxying.

Other platform portions: static UI/assets now run on Workers. Short HTTP ingress and API proxying are feasible and tested here. Actual FastAPI/auth/SDK API handlers, database transactions, Redis, durable scheduling/recovery and persistent services remain on the dev VM. Moving those requires their own dependency/runtime proofs; adding D1, Queues or Durable Objects would create unnecessary competing authority. Agent HTTP/tool steps may share the trusted workflow subset, while persistent agents and CPU-heavy/untrusted steps retain Linux execution.
