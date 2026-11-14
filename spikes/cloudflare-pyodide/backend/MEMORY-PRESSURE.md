# Bounded local memory-pressure qualification

`shared-native-pool.py --memory-pressure` performs one 160 MiB bytearray
allocation inside a genuine upstream workflow after its authorized SDK write.
It touches both ends, records either allocation success or Python MemoryError,
frees a successful allocation, then raises a controlled workflow ValueError.
The existing independent peer, duplicate-dispatch, stale-owner, lost-ACK replay,
credential revocation, canonical accounting and cleanup checks remain active.

This is a bounded allocation probe, not an allocation-until-host-exhaustion loop.
It uses the existing CT128 allocation and provider container CPU/memory/PID limits;
no runtime setting or guest resource is changed. No new packages or native builds
are involved: bytearray is the existing Python/Wasm allocator path.

Run through the source-pinned guest harness:

```sh
timeout -k 5 430 python3 /opt/bifrost-tests/pyodide-ddf316c1/inbox/memory-pressure-v1/ct128-canonical-failure.py \
  --memory-pressure --revision 1
```

Interpretation must distinguish the requested Python allocation, interpreter
heap, local workerd limits, the whole provider container and deployed Cloudflare
limits. Allocation success is evidence against using this local run to validate
a 128 MiB deployed isolate bound. A caught MemoryError does not demonstrate
physical invocation stop or hard platform enforcement. Whole-provider OOM would
be a material local failure requiring uncertain-effect recovery; a successful
peer alone would not establish production tenant isolation.

Peak isolate memory and billed CPU remain unmeasured. The synthetic allocation
size is not a peak-memory metric. Raw source/runtime/image/packet hashes and
canonical observations belong in the separate run receipt; no deployed memory
enforcement or arbitrary-untrusted-code acceptance follows. Admission stays off.

## Observed boundary

V1 exited 1 because the fixture required exactly one matching log. V2 preserves
both actual upstream entries: the workflow warning and the final ValueError
traceback line. That is legitimate logging, not a duplicated terminal receipt.
The fixture now identifies the outcome from the canonical error and records the
matching-log count separately; failed-run evidence remains preserved.

V2 (`--revision 2`) exited 0. The actual Python allocation of 167772160 bytes
succeeded, was freed, then canonical Failed recorded
`bounded_memory_probe=allocated_160mib`. The peer completed Success78; both
completed credentials returned401. Lost-ACK replay preserved canonical state/log
IDs, stale-owner reporting was rejected, duplicate dispatch reused the original
attempt, native reservations were2,2,1,0 and canonical accounting/cleanup passed.
The provider remained alive without OOM before cleanup; no owned containers
remained running. Supervisor73.290s includes setup/prewarm/work/cleanup, not pure
startup latency. Ruff and Pyright passed for the final changed fixture files.

This is concrete evidence that the current local emulator probe does not qualify
the deployed128MiB boundary. It neither proves infinite allocation nor absence
of deployed platform enforcement. A finite allocation was tested, not actual
deployed memory exhaustion. A cloud memory-failure mapping and peer-isolation
test remains required under separate applicable cloud authority; no cloud call
was made here.

Raw receipts: `../results/memory-pressure/receipt-v1.json` and `receipt-v2.json`.
Run `node backend/verify-memory-pressure.mjs` from the spike directory to verify
the current fixture source against the recorded hashes and inspect the exact
local observations. No runtime or allocator compatibility patch was necessary.
