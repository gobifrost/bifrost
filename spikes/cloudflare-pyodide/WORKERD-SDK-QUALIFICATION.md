# Canonical SDK execution in actual guest workerd

Both cold and explicitly prewarmed cases now pass through actual local workerd, using the exact source-built bundle and unchanged upstream engine/SDK code. This advances beyond the Node-hosted Pyodide proof. It does not establish deployed Cloudflare acceptance, a production dispatcher adapter or untrusted physical termination.

The upstream consumer claimed a real test execution and issued its run credentials. The Worker loaded the hash-pinned workflow and helper through the actual API, read config and integration configuration, received/decrypted a synthetic encrypted integration secret, and wrote a table document independently read back as **70**. A peer integration read returned 403. Return/structured-log secret probes were redacted. Canonical terminal consumption and lost-ACK owner reconciliation retained one provider launch and two log rows; runtime access returned 401 after completion. The upstream table SDK's first 404, table creation 201 and successful upsert 200 are preserved, with one successful upsert.

## Bounded experiments and memory boundary

| Run | Change | Observed result |
|---|---|---|
| v1 cold, 1 GiB | Actual workerd with verified cache, no warmup | OOM; proxy reported lost connection, canonical fixture failed. Docker's own OOM event/state identifies the resource failure. |
| v2, 1 GiB | Credential-free engine import diagnostic before any new run identity | OOM during warmup; gate refused canonical fixture dispatch. Its JSON parse reported SyntaxError from the failed response, not a Python source syntax error. |
| v3 warm, 2 GiB | Same guest allocation, larger bounded provider process, cgroup measurements | Pass. Peak 1,340,653,568 bytes, OOM counters zero; 71.386 seconds total supervisor time. |
| v4 cold, 2 GiB | Remove warmup at the same cap | Pass. Peak 1,210,347,520 bytes, OOM counters zero; 71.116 seconds total supervisor time. |

Every run is a new disposable fixture identity with unique workspace/config/table names. These are diagnosed experiments, not automatic retries of an uncertain production attempt. Both failed cases remain preserved. No CPU, timeout, network-security or runtime compatibility flag was relaxed.

The cgroup peak covers **Wrangler, the workerd parent and child runtimes together**, including startup/packaging. It is not a per-isolate measurement, exact minimum, Cloudflare limit, or a replacement for the separately measured guest WASM capacity. The result establishes a usable 2 GiB process cap for this particular local harness within the existing 2 CPU / 4 GiB CT128 allocation. No guest resource was expanded. Single-provider CPU is one core, PID cap 128, no swap; overall wall guard 900 seconds and disk-used guard 24 GiB. The test stack reused its existing disposable DB/Redis/storage volumes and stopped worker/scheduler before provider execution.

All owned containers stopped; volumes and receipts retained. No laptop Docker, Cloudflare/R2 invocation, public endpoint, production credential, new persistent grant or production platform change was involved. Runtime admission remains disabled because the independent actual-workerd timed CPU test still records effects after abort.

## Exact provenance and reproduction

API checkout `f417d03f5c8d5e3a182e68f69e2bf601a3b60125`; prepared engine/SDK/package source `785b560b9973020e1c13401383e1a73a87a54940`. Workerd 1.20261023.1 uses the cache from SOURCE-RUNTIME-QUALIFICATION.md. The unchanged authorized test entrypoint and its support modules were copied to task-owned writable bundler scratch, while the prepared upstream source remains read-only. Exact Worker source, generated-runtime and canonical fixture hashes are in each receipt. The canonical fixture SHA256 remains `5ebf9c12d5727807922921f31767e09d8c5be0e71a6b064cf4a9b89604a0c9cf`.

Current cold supervisor: `backend/ct128-workerd-sdk-qualification.py`. Historical supervisors are retained as inert `.py.txt` evidence. Raw receipts, sanitized API observations, cgroup values, warmup and provider logs are under `results/workerd-sdk-resume/`. Cold successful receipt SHA256 `1ba364a47c97898e12e1730d823f149e56360795d98301abd6b035039b735702`; warm receipt SHA256 `e15cd8c56ac1dbe171283b8ad9e391ec1aa3c50f9ce1026b066faf7826417d33`. Both matched their transfer readback.

The actual test command is the receipt's Docker Compose test-runner invocation of `python /qualification/canonical-local.py --integration-probe --integration-secret-probe`. The supervisor refuses existing output directories/active containers; future execution needs a new reviewed exclusive name. Do not overwrite evidence or run the old failed packets.

```sh
node spikes/cloudflare-pyodide/backend/verify-workerd-sdk.mjs
node --test spikes/cloudflare-pyodide/backend/fixture-admission.test.mjs spikes/cloudflare-pyodide/backend/module-response.test.mjs spikes/cloudflare-pyodide/backend/resolution-response.test.mjs spikes/cloudflare-pyodide/backend/test-api-route.test.mjs
```

The 33 focused admission/integrity/route regressions pass. These offline commands make no API or cloud call. This is a fixed loose-workspace workflow, not arbitrary dependency admission, full signed transport/dispatcher adoption, host-loss recovery, complete exception/stdout capture or comprehensive hostile isolation. Next relevant contract work is combined canonical control-session/dispatch integration on the isolated stack, in coordination with existing shared-source ownership; no parallel queue or source collision is authorized.
