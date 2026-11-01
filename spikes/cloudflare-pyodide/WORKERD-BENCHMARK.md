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
latencies include SDK, transport and API work, not isolated SDK overhead. A matched
local Linux run is now recorded below.

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

## Matched Linux signed-control reference

The byte-identical workload helper (SHA-256
`77e16fbd6adc0272bdd94c9843c81d1a2de04894d64c1267ef97178334cec3f2`)
also passed all 15 samples through upstream's signed WebSocket control path and
actual LinuxTemplate fork. API source, test image, synthetic values and guest
allocation were unchanged. Native Python was 3.14.8; Pyodide Python was 3.14.2.

| Workload | Linux samples (wall ms, rounded) | Linux median ms | Local workerd median ms |
| --- | --- | ---: | ---: |
| JSON transformation | 1.33, 1.00, 5.03 | 1.33 | 2 |
| HTTP orchestration | 21.00, 43.92, 33.48 | 33.48 | 21 |
| Integration read | 31.71, 38.23, 24.00 | 31.71 | 20 |
| Table update | 26.38, 54.48, 23.09 | 26.38 | 14 |
| Multi-step | 21.90, 69.00, 28.05 | 28.05 | 17 |

These are three samples from one sequential run per backend, with different
transport/runtime paths and uncontrolled cache/scheduler effects. They are not
statistical evidence of a general speed advantage or pure Pyodide overhead.
Linux's wider sample spread makes ratios particularly misleading. Production
cold/warm start, CPU, isolate memory, concurrency and cost comparisons remain
unmeasured here. Earlier experimental evidence retains its separate scope.

Linux independently read back all three benchmark documents as 70, observed one
workflow command/positive PID ACK/result event, consumed and acknowledged the
terminal event, and rejected the completed run credential with 401. Both native
templates stopped and fixture worker cleanup passed. Only the same two workflow
logs persisted; helper warnings were absent on Linux too. This confirms the
observed logging boundary across these two paths without changing the engine.

Supervisor `backend/ct128-linux-benchmark.py` invokes the opt-in
`linux-control-reference.py --benchmark-probe` through the same disposable stack.
The test runner was capped at one CPU, 2 GiB and 128 PIDs, with 240-second wall and
24-GiB used-disk guards; CT128 remained 2 CPU / 4 GiB/no swap. Supervisor elapsed
64.174 seconds includes stack startup/cleanup and is not comparable workflow
latency. Targeted Docker Ruff passed; no OOM claim or peak/CPU comparison is made
from the Linux receipt. Owned containers stopped and volumes were retained.

Exact commands, pinned fixture/workspace hashes, samples and cleanup:
`results/linux-benchmark/receipt.json`, SHA-256
`b477d4aee3462ba5c69bf42dc8bf184dc6590ef30845ec8fa02fbec6b8ea50ae`.
Host and local hashes matched. Verify without running containers:

```sh
node spikes/cloudflare-pyodide/backend/verify-linux-benchmark.mjs
node spikes/cloudflare-pyodide/backend/verify-workerd-benchmark.mjs
```

The older Linux-reference verifier now retrieves its fixture from its immutable
tested commit `c3f5cf807fdbf22330596fca294f064e453c589e`; changing the current
opt-in fixture does not rewrite earlier source provenance.

Next: SDK/interop attribution and remaining admission/termination contract gaps.
Physical termination and deployed acceptance remain blocking requirements.
