# Guest WASM memory observations

The actual upstream engine, workspace loading, config/integration SDK, encrypted synthetic-secret redaction, table write and canonical terminal reconciliation passed with memory instrumentation in a fresh official Pyodide 314.0.6 Node process on CT128. This measures the real prepared dependency closure, not an import-only or mocked SDK workload.

| Observation point | WASM linear-memory capacity (bytes) | MiB | Node process RSS (bytes) |
|---|---:|---:|---:|
| Core loaded | 31,457,280 | 30 | 244,224,000 |
| Upstream bootstrap complete | 45,350,912 | 43.25 | 274,763,776 |
| SDK execution complete | 45,350,912 | 43.25 | 280,309,760 |

The instrumentation uses `memoryview(bytearray(1))`, the public PyProxy `getBuffer('u8')` view and `view.data.buffer.byteLength`; every view is released and proxy destroyed. It does not depend on private `_module.HEAP8` internals or retain buffers across memory growth. Pyodide documents that this backing buffer is the WASM memory: [JavaScript buffer API](https://pyodide.org/en/latest/usage/api/js-api.html#pyodide.ffi.PyBuffer.getBuffer). The tiny measurement allocation is included; no correction is subtracted. The exact pinned runtime successfully exercised the API.

These are three capacity snapshots, not live Python allocation counts, total memory, a time-sampled resident peak or Cloudflare billing metrics. Node RSS includes its own runtime/compiler/process overhead and must not be added to WASM capacity or substituted for Workers isolate memory. The guest mounts dependencies with NodeFS, so this also does not measure the packaged Worker filesystem, serialized module payload or full JavaScript allocation footprint in workerd. A 43.25 MiB observed WASM capacity therefore **does not prove compatibility with the 128 MB isolate limit**.

One fresh-process bootstrap observation was 5,178.670 ms, with already staged assets and warm guest filesystem caches. It is neither a Cloudflare cold-start measurement nor a statistically meaningful latency benchmark. No cost estimate is derived from it.

## Exact evidence and reproduction

- Receipt `results/node-memory-resume/success.json`, SHA256 `23f5739a64f893f9bde6166e4b270dd83b01de4ab7ca0d9432da35baef90177c`, independently matched after transfer.
- API source `f417d03f5c8d5e3a182e68f69e2bf601a3b60125`; prepared runtime source `785b560b9973020e1c13401383e1a73a87a54940`. Additional provider and canonical fixture SHA256 values are pinned in the receipt.
- Supervisor `backend/ct128-node-memory-qualification.py`; measured provider `backend/node-memory-provider.mjs`. It runs the same actual canonical fixture with `--node-pyodide --integration-probe --integration-secret-probe`.
- Existing CT128, 2 CPU / 4 GiB; provider 1 CPU / 1 GiB / 128 PIDs, unchanged isolated test network. Overall 900-second wall and 24 GiB disk-used guards; 66.950 seconds observed supervisor time. Every command exited 0.
- One launch, successful canonical terminal consumption and lost-ACK reconciliation, encrypted-secret redaction retained. All owned containers stopped; volumes and receipts retained. No laptop Docker, cloud invocation, resource expansion or security setting changes.

```sh
node spikes/cloudflare-pyodide/backend/verify-node-memory.mjs
```

This verifier performs no API calls. The supervisor refuses existing output paths and active containers; future execution requires a new reviewed exclusive run/packet name, not overwriting this evidence.

Experiment: instrument public WASM capacity around the proven SDK workload; command above plus the receipt's actual Docker/Compose invocation; result: actual workflow still passes and snapshots are now measured; next experiment: actual workerd total-memory and physical-stop qualification once its exact integrity-pinned runtime bundle is legitimately available. The recorded bundle 403 is preserved, with no denied-request retry or alternate access route. Cloudflare execution admission remains disabled.
