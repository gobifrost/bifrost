# Explicit disposal is not an available execution-stop operation

The next hypothesis after failed fetch/RPC-context cancellation was explicit disposal of the live dynamic Worker or its entrypoint. Before running a CPU fixture, the actual local objects must expose the mechanism. A successful Python positive control prevents a failed import/startup from being mistaken for an API result.

Actual CT128 result: Python 3.14.2/Pyodide314.0.6 executed and returned the expected `cpython-314-wasm32-emscripten` ABI. Neither `WorkerStub` nor `getEntrypoint()` exposes callable `Symbol.dispose` or `Symbol.asyncDispose`. The fixture therefore invoked no invented disposer and ran no CPU-cancellation case. This eliminates this specific supported-object route in the tested local runtime; it is not a claim that disposal physically stopped Python, or that every future provider API is incapable of stopping it.

This is a missing provider lifecycle contract, not a pure-Python dependency or missing PyEmscripten wheel. Compiling a package cannot supply a host runtime operation that is not exposed. No replacement orchestration or fake stop receipt was introduced. Existing independent post-abort CPU effects remain separate negative evidence.

The official [RPC lifecycle guide](https://developers.cloudflare.com/workers/runtime-apis/rpc/lifecycle/) describes disposal of returned RPC object stubs and context lifetime. It does not turn every service binding/WorkerStub into a disposable execution handle. The fixture checks the real objects rather than assigning those semantics from the word “dispose.” A future supported mechanism still needs a noncooperative-work test with an independent observer, canonical late-result fencing and cleanup; a caught exception, rejected RPC or absence of callbacks is insufficient.

## Exact local execution

Source files: `guest-disposal-entry.mjs`, `disposal-fixture/`, `ct128-disposal-probe.py`. The helper stages the public fixture in `/opt/bifrost-tests/pyodide-ddf316c1/inbox/disposal-probe-v1/packet/` and executes it using **cf CLI**, not a direct Wrangler command. `cf dev` delegates to installed Wrangler4.156.0; port/IP are in the documented `wrangler.config.ts` development settings.

| Experiment | Change | Observed result |
|---|---|---|
| v1 | `cf dev --local --ip ... --port ...` | CLI rejects `--local`; Python never starts. |
| v2 | Remove unsupported `--local` | CLI dev implementation rejects `--ip`; Python never starts. |
| v3 | `cf dev`; set IP/port in typed dev config; HTTP readiness gate | Actual Python ABI positive control passes; all four disposer surfaces absent. |
| v4 | Export the inspected helper for regression tests; verify exact ABI and capture package/image pins | Same actual result on exact testable source; source hashes and cleanup verified. |

Receipts: `../results/disposal-probe/disposal-v{1,2,3,4}.json`. Failed attempts are retained, not rerun unchanged or reclassified as successes. The final packet hashes are verified by:

```sh
node --test spikes/cloudflare-pyodide/backend/guest-disposal-entry.test.mjs
node spikes/cloudflare-pyodide/backend/verify-disposal-probe.mjs
```

Four Node guard/inspection regressions pass. Actual runtime evidence is the guest receipt, not the unit doubles. Local commands used cf1.0.0-beta.26, the existing pinned bundle `3c3fd5a4179230e21e018e28b0e735e7c4abd8fe8e4ff39d2fd6bb2ea3a2e260`, verified cache-wrapper hash, and cached image `sha256:33cd92be03ec518c7b10e6b6abe5fbe19cb46fbf548fb93bee80f17967f54a1b`.

CT128 remains2CPU/8GiB. Each unique owned container had1CPU/1GiB/no swap/256PIDs and `--network=none`; readiness45s, observation35s, disk-used guard24GiB. Final elapsed20.821s is supervisor/CLI/ABI-probe wall time, not engine latency or billable CPU. No OOM was reported. Peak isolate memory is not measured. Existing dev-stack containers and volumes were preserved; each probe's running-after-cleanup list is empty. No Cloudflare invocation/deployment, credential, security grant, resource expansion or production effect occurred.

The local runtime is the pinned workerd stack already qualified in SOURCE-RUNTIME-QUALIFICATION.md. This does not replace a separately bounded actual Cloudflare stop proof. Arbitrary untrusted admission remains disabled. A potential next empirical route is independent evidence of a provider-enforced hard CPU ceiling against repeated catch-and-continue code; existing catch/abort receipts do not establish that guarantee, and it must not be inferred from an exception or silence.
