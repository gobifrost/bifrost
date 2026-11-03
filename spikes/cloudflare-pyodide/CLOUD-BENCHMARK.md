# Fresh Python Worker benchmark frontier

Five fixed workloads each executed inside one fresh deployed Python Worker using the actual upstream engine/SDK and genuine run-scoped credentials against the isolated CT128 API. Each workload produced three correctness-checked operation samples. No SDK was mocked, no warm isolate was reused and no automatic retry occurred. The shared fixed benchmark helper's new selected-workload option preserves its original all-workloads mode and stays within existing CPU/subrequest limits.

| Workload | Fresh-child request round trip | Inner I/O-clock median | Correctness |
|---|---:|---:|---|
| JSON transformation |10.49s|Unavailable: clock did not advance|1,000 rows, sum999,000|
| HTTP orchestration |5.95s|69ms|Concurrent pair of real config SDK reads, each42|
| Integration read |5.41s|71ms|Actual integration SDK config42|
| Table update |5.77s|154ms|Three authenticated upserts, independently read back70 each|
| Multi-step workflow |5.20s|125ms|Async reads, yield, handled ValueError, logging call; sum84|

Round trips use native Python's continuous perf_counter in the guest controller. They include parent/preflight, runtime initialization, module loading, SDK/API work, serialization and network transport. They are not isolated startup measurements. The first parent request may differ from later ones; placement/cache state was not controlled, so none is labeled a warm-start measurement. Each request still creates a fresh child using load(). Three samples within one child do not constitute three cold executions or a concurrency test. HTTP orchestration here uses HTTPS-backed SDK API calls, not arbitrary external SaaS egress.

## Timer and measurement boundary

The deployed JSON samples returned0,0,0. This does not establish zero CPU cost, a sub-millisecond duration or a speedup. [Cloudflare documents](https://developers.cloudflare.com/workers/runtime-apis/performance/) that deployed timer APIs advance only after I/O; local workerd timers advance continuously. The observed Python timer behavior is consistent with that restriction. Nonzero inner values are I/O-clock deltas, not CPU time; raw receipts retain the historical samples_wall_ms field without relabeling its observed values. Engine-reported duration is also not provider CPU measurement.

The cf CLI analytics catalogue was readable using existing access. A scoped logs.workersLogs query for this exact Worker/time window returned0 rows with observability unchanged/disabled. No security or logging setting was enabled to force data. CPU milliseconds, peak isolate memory and measured billed cost therefore remain null. The existing local Linux/workerd reference in WORKERD-BENCHMARK.md remains historical; this increment did not run a matched current Linux cold-start experiment, and no speed/cost ratio is inferred from the old numbers. Earlier helper-log omission also remains an explicit shared-engine filter boundary.

## Estimates, not invoice measurements

[Current Dynamic Workers pricing](https://developers.cloudflare.com/dynamic-workers/pricing/) charges $0.002 per fresh Worker creation after the included creation allowance, $0.30/million requests and $0.02/million CPU milliseconds. Dynamic CPU includes initialization as well as execution. load() creates a separately counted Worker each invocation. Consequently1,000 fresh executions have a **$2 creation-only charge after the allowance**, before CPU, requests, API hosting and monthly-plan allocation. This is a conditional marginal estimate, not a measured account bill; remaining allowance is unknown. Five fresh children imply a $0.01 creation-only estimate under the same assumption. CPU/request charges and total per1,000 remain unmeasured. Reusing a cached ID to reduce creation charges would change the fresh-run isolation contract and is not proposed as an optimization here.

The results do not establish the original economic hypothesis. The current full-engine/fresh-child path takes5–10s per request for these short fixed jobs; this is not an incremental latency comparison against a matched Linux run. Linux remains the default for short latency-sensitive jobs, CPU-heavy work, arbitrary user Python and persistent services. A trusted I/O-heavy workload with substantially longer external waits might amortize initialization, but that is a next experiment, not an accepted routing policy. Medium-duration/agent workflows additionally need canonical ownership, interruption recovery and physical termination qualification. Static UI and the guarded API ingress/proxy remain the portions already deployed on Workers.

## Reproduce and inspect

```sh
# Existing prepared runtime; cf CLI deployment uses a new bounded private profile.
cd spikes/cloudflare-pyodide/dev/executor
cf deploy --secrets-file <temporary-guard-json>
# Assigned guest, staged helper/controller, existing dev stack:
python3 run_cloud_benchmark.py
# Or one selected workload, within that same exclusive ledger:
python3 run_cloud_qualification.py --benchmark json_transform --private-directory cloud-private-benchmark-v1
# Offline retained evidence:
node spikes/cloudflare-pyodide/dev/verify-cloud-benchmark.mjs
```

Do not reuse the retired profile/ledger or rerun a partially accepted workload without reconciliation. The wrapper stops at the first failure and fsyncs each completed sanitized receipt. Source hashes, raw results, analytic response, summary and chart are in results/cloud-benchmark. Three selector guards and focused guest Ruff passed. The five-workload window used15 reserved fixture POSTs plus one closure GET within20 requests/$0.10/30min. Each fixture verified unsupported canonical mode422, invalid credential401, peer403 and ended access/refresh401. No production resources, persistent grants, new queues or runtime security settings changed.

Runtime artifactbc4eae48...6daf0 and the upstream engine/SDK pins remain as in dev/STREAM-LOGS.md. Open version840082e7-f2eb-4f1d-aa91-cc935a03ced2 was retired; final version8268db76-7b21-44e5-8c74-4c6b6d577b89 has workersDev=false/expiry0 and no temporary secret. Retired-guard GET returned404. Control containers exited/removed, fixtures cleaned, dev services/volumes preserved. Physical CPU termination and canonical Cloudflare dispatch remain incomplete; untrusted admission stays disabled.
