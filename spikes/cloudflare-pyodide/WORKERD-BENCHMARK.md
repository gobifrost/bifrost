# Actual workerd five-workload measurements

Five fixed workloads passed three correctness-checked samples each inside one
actual upstream engine workflow in CT128. Real SDK/API calls, run-scoped
credentials and canonical terminal consumption were used, without SDK mocks.
These are operation wall times within one workflow, not five independently
scheduled executions or deployed Cloudflare measurements.

| Workload | Samples (wall ms, rounded) | Median ms | Correctness |
| --- | --- | ---: | --- |
| JSON transformation | 2, 3, 2 | 2 | JSON round trip of 1,000 rows; sum 999,000 |
| HTTP orchestration | 21, 24, 15 | 21 | Concurrent pair of HTTP-backed config SDK reads returns 42 each |
| Integration read | 20, 16, 23 | 20 | Integration SDK configuration value 42 |
| Table update | 15, 14, 14 | 14 | Three distinct IDs; authenticated upserts return 70 |
| Multi-step | 17, 17, 16 | 17 | Two async reads, yield, handled ValueError and logging call; sum 84 |

Python `time.perf_counter()` has approximately one-millisecond observed granularity
here; raw floats do not justify sub-millisecond precision. HTTP uses the isolated
API via the existing service-binding gateway, not external Internet HTTPS. These
latencies include SDK, transport and API work, not isolated SDK overhead. There is
no matching five-workload Linux baseline yet.

The canonical fixture observed one launch, successful terminal consumption,
lost-ACK reconciliation without another launch, independent proof-document value
70 and post-terminal credential rejection (401). Provider observations record 13
successful config reads, four integration reads and four successful upserts,
including prerequisites. Both workflow and helper are pinned in the existing
two-file manifest.

Only two logs were retained: the prerequisite redacted integration log and final
workflow log. Three helper warnings were absent. Current upstream
`WorkflowLogHandler.filter` in `engine.py` accepts workflow-file paths,
workspace/repo paths and script/traceback logs; virtual helper paths do not match
that filter here. Complete helper-log capture is not proven. Neither upstream
logging nor admission was changed.

Wrangler reported approximately 7.145 seconds for the fresh provider request,
including startup/engine/SDK work; the supervisor took 60.090 seconds including
stack startup/cleanup. Neither is production cold-start latency. Provider cgroup
peak was 1,258,438,656 bytes under a 2-GiB cap, including Wrangler/workerd processes,
not isolate memory. CPU time, isolate peak, Cloudflare cost and cost per 1,000 are
unmeasured/null. Zero cloud/canary invocations occurred.

## Provenance and commands

API source `f417d03f5c8d5e3a182e68f69e2bf601a3b60125`; prepared runtime source
`785b560b9973020e1c13401383e1a73a87a54940`; workerd `1.20261023.1`, Wrangler
`4.156.0`, Pyodide/CPython `314.0.6`/`3.14.2`. The verified source-built bundle
cache was reused. Exact fixture/workload/worker hashes and commands are in
`results/workerd-benchmark/receipt.json`, SHA-256
`6e9f41683e7d30fd863f736cb2d0259dface691bb962cc0b18efa450c83ce11e`.
Host and local receipt hashes matched.

CT128 stayed at 2 CPU / 4 GiB, no swap. Provider: one CPU, 2 GiB, 128 PIDs, existing
isolated project network. Supervisor: 900-second wall/24-GiB used-disk guards. All
owned containers stopped, volumes retained, no OOM events. No laptop Docker.

Stage `canonical-local.py`, `benchmark-workloads.py` and
`ct128-workerd-benchmark.py` at the supervisor's exclusive packet path and run it
inside the authorized guest. It verifies hashes/clean source and refuses an
occupied guest or existing run directory. Do not reuse retained evidence paths.
The recorded Compose invocation ends with:

```sh
python /qualification/canonical-local.py --integration-probe --integration-secret-probe --benchmark-probe
```

Offline evidence verification and unchanged admission regressions:

```sh
node spikes/cloudflare-pyodide/backend/verify-workerd-benchmark.mjs
node --test spikes/cloudflare-pyodide/backend/fixture-admission.test.mjs
```

Failed receipts/logs are retained. V1: missing driver Path import, before execution
creation; fixed the import. V2: expected 422 for a third manifest file. V3 placed
the functions inside the already permitted pinned helper. No admission or runtime
limit changed; no unchanged retries or failure waivers.

Next: matched Linux signed-control measurements and SDK/interop attribution.
Physical termination and deployed acceptance remain blocking requirements.
