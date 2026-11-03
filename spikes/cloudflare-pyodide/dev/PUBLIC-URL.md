# Trusted public URL versus SDK transport

The companion bootstrap previously passed the SDK's virtual API endpoint as upstream `public_url`. Actual cloud SDK receipts consequently show `https://test-api.invalid` in execution context. That address is deliberately restricted by the outbound capability; it is not the platform URL workflows should use when producing links.

The correction adds an explicit trusted public-URL argument to `run_authorized_attempt`. The existing three-argument call retains its previous value. The deployed-fixture source builds its child payload from supervisor configuration: the SDK endpoint stays `https://test-api.invalid`, while public metadata comes from the configured HTTPS platform root. Request-provided `api_url` and `public_url` cannot select either value. Invalid public configuration is rejected before module/API/runtime effects. No egress capability, authorization, canonical dispatch or Linux code is changed.

## Actual local upstream-worker evidence

Using cf CLI inside CT128, two fresh Pyodide runs executed the actual upstream worker and engine's fixed inline-script path:

| Control | Returned public URL | Returned BIFROST_API_URL |
|---|---|---|
| Existing bootstrap default | `https://test-api.invalid` | `https://test-api.invalid` |
| Explicit supervisor URL | Actual dev UI HTTPS origin | `https://test-api.invalid` |

Both returned Success, the selected execution ID, no parent signing secret, and unknown CPU/peak-memory values as null. The explicit value also appears in the upstream result's execution_context. The fixture imports the SDK context proxy but makes **zero SDK API calls** and supplies an unused synthetic invalid token. It is context conformance, not genuine run authorization, tenant isolation or new cloud SDK acceptance. Earlier authenticated cloud receipts remain separate and unchanged.

`results/public-url/receipt.json` records the packet hashes, actual results, ABI, cf/Wrangler/image pins and cleanup. Python3.14.2/Pyodide314.0.6/SOABI checks pass. The network-disabled container was bounded to1CPU/2GiB/no swap/256PIDs within CT128's existing2CPU/8GiB allocation. The2GiB process cap follows the existing full-engine local qualification; it is not a claim about128MiB isolate usage. Elapsed21.898s includes CLI/build/startup/two probes and is not billable CPU or workload latency. No OOM occurred; peak memory remains unmeasured. The owned container stopped; dev stack/volumes remained intact. No cloud invocation/deployment, spending, credentials, grants or production resources changed.

## Reproducible closure

Keep the original public base artifact separately. `stage_runtime.mjs` verifies its SHA256 `e1cca587e10cb755d49df0f2325c038a361a25f7031142a9e9e896a3c004bd39`, then replaces only the companion `runtime_runner.py` module from tracked source. It does not modify/reimplement upstream engine/SDK or native wheels. The derived artifact is `7576c38299adfb147952df5b44d6b3c32ee3b3519251f9a63b27bdc9fbe5c9d1`; bootstrap SHA is recorded alongside it in `runtime-provenance.json`. Input provenance and output provenance are distinct. Feeding the derived output back as an original base is correctly rejected.

```sh
node spikes/cloudflare-pyodide/dev/stage_runtime.mjs <original-pinned-base-artifact>
node --test spikes/cloudflare-pyodide/dev/executor/policy.test.mjs spikes/cloudflare-pyodide/dev/stage_runtime.test.mjs
node spikes/cloudflare-pyodide/dev/verify-public-url.mjs --commit ff4a339b434b58c88abac8d0c33057a52002aec1
```

The guest fixture uses `public-url-fixture/` plus the staged generated runtime/runtime-code, actual executor python-entry/policy modules and provenance file. `ct128-public-url.py` reuses the bounded cf CLI supervisor from `backend/ct128-disposal-probe.py`; its copy must be staged beside that helper inside the owned guest packet. The helper refuses to overwrite the existing run directory. The two bootstrap-delegation unittest cases run in the cached guest image, separately from actual runtime acceptance. Five Node policy/integrity regressions and focused guest Ruff pass.

`cf deploy --dry-run` from `dev/executor/` passed before deployment. A separate deployed qualification then passed at Worker version `69646acf-2a5d-4733-acb5-e0e9e0103651`, using the derived runtime above and the existing isolated CT128 dev API. See `results/public-url/cloud-receipt.json`: genuine run-scoped engine/SDK execution returned70, read integration configuration, wrote a table document independently read back as70, denied peer-tenant access403, redacted credentials/integration secrets, and rejected ended access and refresh credentials401. Request-supplied attacker URLs were ignored: upstream execution_context contains the trusted dev platform URL while BIFROST_API_URL remains the virtual transport. Fresh-run canaries were absent, but this single run does not establish cross-run or physical-termination acceptance.

The new window had a five-request/$0.10/30-minute cap. Three fixture requests were reserved before sending (unsupported canonical mode422, invalid credential preflight401, successful actual Python run), followed by one closed-endpoint GET. The ledger is retained; no uncertain request was retried. The workflow's643ms duration is upstream workflow wall time, not provider CPU/startup measurement or a cost invoice. Provider CPU, peak memory and billed cost remain unmeasured.

Cleanup used cf CLI to deploy workersDev=false/expiry0 and delete DEV_AUTH. Final version `7e90a551-d0ae-4cdb-b38d-f1ac7ed11c20` has no secrets; the public endpoint returned404 even with the retired guard. The qualification container exited0 and was removed; dev services and volumes were preserved. `cloud-cleanup.json` and `cloud-source.json` distinguish deployed evidence, closure provenance and cleanup. Physical termination and canonical cloud dispatch remain incomplete; untrusted admission stays disabled.

Historical source verification accepts an explicit tested commit so later runtime/fixture development does not rewrite the old receipt. The public-URL cloud source files verify against ff4a339b434b58c88abac8d0c33057a52002aec1. The retained guard verifies against d09c4e113efea3ba9e3c8c94b42a6dfc6b5474fa, while the refresh challenge retains its existing4939d3d61623692bf310bb482057b4588d10d213 pin. No old expected hashes/results were replaced. Current staging now additionally overlays the qualified engine revision; see STREAM-LOGS.md for its separate artifact and deployed evidence.
