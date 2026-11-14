# Deployed canonical engine/SDK acceptance — YELLOW

On 2026-11-07 the actual Cloudflare Python Worker passed the existing two-tenant
canonical fixture. This is deployed execution, not just imports or an execute-code
demo. PostgreSQL ownership, claims, run credentials, consumer result handling,
terminal reconciliation and accounting remain in the upstream native control plane.

The successful candidate was deployed with **cf CLI** as version
`2573973f-1887-4605-b84f-7f87e7a670a0` on the existing isolated executor
`bifrost-pyodide-spike-ddf316c1`. The native API remains pinned to `1d33150`,
packaged engine `f49d4ba`, SDK `139596497`. This does not qualify all code on
current upstream main. `cloud-canonical-acceptance.json` records exact pins,
source digests, response hashes, runtime/package inventory and selected assertions.

## Observed contract

* Two genuine authorized attempts, one launch each, with real pinned workspace
  module loading, async workflow code, config reads, table writes and persisted logs.
* Tenant denial, parent-secret absence, credential redaction and four fresh-state
  canaries (module, builtins, filesystem, environment) exercised by the workflows.
* Expected canonical Failed/Success outcomes; 10/1 persisted logs respectively;
  awaited SDK close on both; both ended run credentials return401.
* Duplicate dispatch reused the existing attempt. Stale terminal ownership was
  rejected. Lost terminal ACK and native owner reconnect reconciled the original
  result without relaunch. Busy accounting moved `[2,2,1,0]`; terminal obligations,
  mux state and fixture worker records were cleaned up.

The reconnect test concerns the native owner/result transport, not a proof that
an interrupted Cloudflare parent can recover its Python child. Real HTTP requests
travelled through a temporary authenticated SDK-only origin to the matched API.
The gateway and relay are test plumbing, not a new scheduler or business queue.

## Failures retained and fixed

1. Runv1 sent three child requests but timed out waiting for both table effects.
   It also accidentally built the native runner's `latest` image because the tag
   was omitted. Its log hash is retained; it is not accepted source evidence.
2. Runv2 refused an unsupported Compose `--no-build` flag before any child request.
   A subsequent local `--pull` syntax probe also failed without invoking cloud.
   The installed Compose help was checked; explicit cached image selection with
   its supported ordinary `run --rm --no-deps` command was preflighted successfully.
3. Runv3 used the pinned runner and retained bounded private response bytes. One
   child got WorkflowLoadError/410 with no callback observations; the peer loaded
   modules, denied a cross-tenant read, created/upserted a table, persisted logs,
   then hit the configured12-subrequest limit while polling the fixture gate.
4. Runv4 passed after removing callback authority from the parent's module Map.
   Immutable origin/deadline/manifest props travel with the loopback binding.
   Callback isolation no longer requires parent memory to exist in the receiving
   isolate. The explicit fixture subrequest budget is32; CPU remains1000ms and
   the workflow deadline remains30seconds. No test timeout was increased.

The regression reproduces a callback with serialized props and no parent state,
checks deadline/origin denial before network, and verifies changed module bytes
still fail the existing409 integrity contract. Its first assertion incorrectly
expected502; corrected to the existing contract, rather than changing runtime code.

Cloudflare documents [custom loopback props](https://developers.cloudflare.com/workers/runtime-apis/context/#specifying-ctxprops-when-using-ctxexports)
and [global outbound Service Bindings](https://developers.cloudflare.com/dynamic-workers/api-reference/#globaloutbound).
The missing callback Map entry is observed; cross-isolate routing is the working
diagnosis. We did not capture a provider isolate ID to independently identify it.

## Security and compatibility boundary

**Arbitrary/untrusted admission remains disabled.** The successful acceptance is
for reviewed SDK/I/O fixture workflows. Physical CPU termination, hostile memory
exhaustion, abnormal parent interruption and arbitrary background-task cancellation
are still unqualified. Previous failed cancellation experiments remain valid.

The new callback is stateless. Its immutable deadline fences new SDK requests;
upstream terminal credential revocation fences subsequent authorized API effects.
There is no cross-isolate mutable `close()` capability. Consequently, do not claim
immediate parent-driven capability revocation or physical termination. A request
already accepted by the API can still have an uncertain outcome requiring canonical
reconciliation. This remains material for arbitrary user Python.

Callback HTTP traces are **not aggregated across isolates**. The result explicitly
labels that observation scope; its empty `api_calls` is not evidence of zero calls.
The accepted assertions use actual API effects and canonical persisted logs.
Do not transfer historical callback-trace/log-delivery assertions to this source.
Normal completion awaits the Python RPC result, response stream and SDK close;
it does not require an undocumented per-success isolate kill.

Runtime observed in cloud: Pyodide314.0.6, CPython3.14.2,
`cpython-314-wasm32-emscripten`, Emscripten5.0.3. Exact12-package inventory is
retained, including pydantic-core2.50.2 and PyYAML6.0.3. This increment introduces
no dependency substitution or new wheel build. Existing wheel-test evidence is
separate. CPU, peak memory, billing and pure startup were not measured here.

## Bounds and cleanup

One original30-minute expiry was preserved across diagnosed attempts; no extension.
Nine child requests total: three inventory probes and six fixed workflow attempts.
Each run's relay permits at most3 requests. The test stayed below the approved
20-child dimension; exact incremental billing was not measured. No new paid
resource, persistent credential, production change or untrusted admission occurred.

Both existing Workers now have workers.dev/previews disabled, expiry0 and no
temporary secrets. Executor active version after secret removal is
`214f9e26-5b51-46bc-b751-1f154a25e781`, deployment
`280f47f5-0d99-4b4b-8f3f-4b4d2a369012`. The temporary cf quick tunnel and owned
SSH forwarding process stopped. Owned test/relay containers stopped; volumes,
uncertain locks and the separate existing dev stack were preserved. Temporary
secret profiles were removed; bounded private response evidence remains on CT128.
`cloud-canonical-closure.json` records independent readback.

## Reproduction and evidence layers

Assigned guest: CT128,2CPU/8GiB. Canonical checkout:
`/opt/bifrost-tests/pyodide-ddf316c1/upstream-025f6c0`.
The existing pinned checkout uses its historical explicit stack lifecycle; current
upstream test.sh lifecycle is different. Do not apply that old lifecycle to main.

```sh
# Offline build, refusing existing output and verifying the exact runtime bundle:
python3 inbox/cloud-source-v2/spikes/cloudflare-pyodide/dev/prepare-cloud-canonical.py
# Matched disposable stack was started with its repository test.sh, cached image:
BIFROST_SKIP_BUILD=1 BIFROST_TEST_IMAGE_TAG=bifrost-test-7ddfac36 ./test.sh stack up
# After the guarded relay and temporary cloud window are prepared:
BIFROST_TEST_IMAGE_TAG=bifrost-test-7ddfac36 timeout 180 docker compose \
  -p bifrost-test-5dc8d94e -f docker-compose.test.yml --profile test run --rm --no-deps \
  --volume "$PWD/spikes/cloudflare-pyodide:/spike:ro" \
  --volume /opt/bifrost-tests/pyodide-ddf316c1/inbox/staged-runtime-v4:/qualification:ro \
  test-runner python /qualification/shared-native-pool.py
```

The cf uploader consumed hash-matched prebuilt output from CT128, with
`cf deploy --prebuilt --provision=false --secrets-file <temporary-private-file>`.
The outer controller's finally block deployed expiry0/private configs, removed
both secrets and stopped only owned containers. This is an intentionally guarded
acceptance procedure; do not run the fixture against another API or reopen the
retired profiles. Raw run directories `runs/cloud-canonical-v1` throughv4 are
retained. `export-cloud-canonical.py` emits whitelisted assertions and hashes;
raw `.bin` network responses are bounded inert private evidence, never executed.

Focused source verification:18 Node tests pass, including callback isolation,
integrity, gateway, admission and staging; Ruff passes qualification utilities. Full native
suite, current-main deployment, benchmark refresh and physical-stop acceptance
were not run. Historical local receipts now verify their historical source pin.

Next gate: qualify abnormal invocation termination and capability obligations
before expanding admission. In parallel, normal maintainer review can assess this
experimental optional provider; successful fixture execution is not upstream acceptance.
