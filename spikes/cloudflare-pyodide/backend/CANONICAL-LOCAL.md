# Real local Pyodide with canonical dispatch and completion

`node backend/run-authorized-local.mjs --canonical` passed with process exit 0.
This is the first combined local qualification of these previously separate
pieces, using actual upstream code and a real disposable API, PostgreSQL, Redis
and workspace storage. It is not a new cloud deployment or production ingress.

The fixture creates a PENDING execution and stages its dispatch through
`stage_workflow_run`. `claim_next` atomically claims it, and a duplicate claim
finds no work. The actual `WorkflowExecutionConsumer.process_message` looks up
workflow metadata, reconstructs organization context, chooses protocol 2 and
mints run-scoped credentials. A small host-side `LocalPool` implements only the
existing credential-route and route-execution boundary. No independent context,
token issuer, business queue or completion engine is substituted.

The physical route uses fresh `LOADER.load` and the prepared upstream
`run_execution`. It loads the pinned workspace workflow/helper, reads config
through the SDK, writes a table document and emits a structured log through the
actual API. The shared upstream envelope builder runs **inside Pyodide**. The
actual canonical consumer persists the result/log and revokes live-run access
through terminal state. After an injected failure between callback return and
transport ACK, a new persisted owner generation replays that same terminal
result without another physical launch or duplicate log row.

## Evidence

* `results/canonical-local.json`: exit 0, Success/value 70, independent table
  readback 70, one launch, one persisted log, cross-tenant 403, terminal credential
  401, no parent signing secret, credential redaction and reconnect assertions.
* `results/canonical-local-source.json`: 73 upstream files, including shared envelope.
* `results/canonical-local-runtime-manifest.json`: 477 packaged modules; payload SHA256
  `e4db1ebbd021387907062af4721543d5639889fe587884215cea1e0e1ec4e0c5`.
* `results/canonical-local-provenance.json`: invocation-source hashes. Historical engine,
  SDK, cloud, unit-test and benchmark receipts are preserved separately.
* `results/canonical-local-cleanup.json`: own test-network attachment removed; exact
  dedicated Wrangler supervisor revalidated/signaled; port 8799 ECONNREFUSED.

Paths above are relative to the spike directory. Offline verification:

```bash
node backend/verify-canonical-local.mjs
node --test probe-io.test.mjs backend/module-response.test.mjs backend/resolution-response.test.mjs
```

All 15 focused Node tests pass. Focused Docker Ruff on the new driver/preparation
scripts passes, as do JS/Python syntax and diff checks. API source is unchanged
from 336b6d251's 22-test/Pyright/Ruff qualification.

## Exact existing-environment setup

Use this worktree's disposable stack and tools containers only. The wrapper is
deliberately pinned to their paths and names; inspect it before adapting it.
There are no cloud calls in these commands. From the worktree root:

```powershell
docker exec bifrost-pyodide-test-tools-ddf316c1 bash ./test.sh stack up
docker exec bifrost-pyodide-test-tools-ddf316c1 env LOG_DIR=/tmp/bifrost-bifrost-test-79c5c14e BIFROST_TEST_IMAGE_TAG=bifrost-test-79c5c14e docker compose -p bifrost-test-79c5c14e -f docker-compose.test.yml stop worker scheduler
py -3 spikes/cloudflare-pyodide/backend/prepare-runtime.py --manifest canonical-local-source.json
docker cp spikes/cloudflare-pyodide/backend/. bifrost-pyodide-ddf316c1:/spike/backend/
docker exec -w /spike bifrost-pyodide-ddf316c1 /root/.local/share/uv/tools/workers-py/bin/python backend/pack-engine.py --current-runtime --manifest canonical-local-runtime-manifest.json
docker network connect bifrost-test-79c5c14e_default bifrost-pyodide-ddf316c1
```

Run the local JavaScript supervisor with the existing cached Wrangler binary in
an interactive terminal (not `cf deploy`):

```powershell
docker exec -it -w /spike bifrost-pyodide-ddf316c1 node /root/.npm/_npx/32026684e21afda6/node_modules/wrangler/bin/wrangler.js dev --config backend/wrangler.authorized-local.jsonc --port 8799 --ip 0.0.0.0
```

After it is ready, from a second terminal at the worktree root:

```powershell
node spikes/cloudflare-pyodide/backend/run-authorized-local.mjs --canonical
docker network disconnect bifrost-test-79c5c14e_default bifrost-pyodide-ddf316c1
```

Stop that exact Wrangler supervisor and verify connection refusal. A completed
tool handle alone is insufficient: this run found the earlier listener still
alive, reused it, then verified actual termination with SIGINT/readback. Do not
touch other ports/resources. Copy a newly generated manifest from the tools
container if retaining a new evidence set; preserve historical receipts and
generate matching provenance instead of treating old hashes as new evidence.

## Boundaries

Only this trusted fixed fixture is admitted. The local entry is not deployable
authenticated control ingress, and HTTP-user workflow-submit authorization is
not qualified by this manually staged fixture. General dependency eligibility,
Solution remapping, arbitrary workspace execution and production routing remain
unsupported. The bounded parent diagnostic map contains API path/status data,
not business authority or credentials; this is not a production telemetry design.
The claimer's 1 GiB budget is synthetic fixture input, not a measured isolate
capacity or an enabled routing policy; it must not be advertised by a provider.

Completed `ExecutionWorkerClaim` rows intentionally persist until the executor
row is removed (`execution_workers.py`). The test preserves that behavior and
proves fixture executor deletion cascades its claims. It does not invent a
per-run claim-release API or qualify the production retention controller.

The fault is injected after the **full** callback; interruption within individual
post-commit steps, whole-host loss, concurrent tenants and admission/recovery of
unsupported runs require further work. Provider CPU milliseconds and peak memory
remain unavailable (null), and the result duration is not a cold-start benchmark.
No cloud invocation, reopening, credential grant, spending or security-setting
change occurred. Catchable CPU-limit failure remains a material blocker to
arbitrary untrusted execution. Do not infer physical stop from logical ACK.

Current API review used the [Dynamic Workers reference](https://developers.cloudflare.com/dynamic-workers/api-reference/)
and [Workers best practices](https://developers.cloudflare.com/workers/best-practices/workers-best-practices/),
plus published `@cloudflare/workers-types` 5.20261031.1. The reference describes
`load` as creating a new Worker without ID caching and permits explicit outbound
bindings. These API checks do not replace runtime isolation/termination tests.
