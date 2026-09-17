# Local Kubernetes Spike Results

For the subsequent memory-reduction pass and corrected latency comparison, see
[worker memory reduction results](worker-memory-reduction-results.md). Earlier
inline timing measurements below included stale UI queue-tracking overhead.

Related docs:

- [Local Kubernetes spike runbook](./local-kubernetes-spike.md)
- [Elastic runtime memory guide](./elastic-runtime-memory.md)

## Summary

The local Kind/KEDA spike proved the basic runtime path: Bifrost can run API, scheduler, workers, PostgreSQL, RabbitMQ, Redis, and SeaweedFS in a local Kubernetes cluster, and KEDA can scale workers from RabbitMQ backlog. The sustained load run completed all 96 workflow executions successfully across 4 worker pods.

The main behavior gap found during the spike was worker drain correctness. The original shutdown waited for message handlers but missed the child execution they had dispatched, leaving a delayed execution without a terminal result. The drain fix changed that result from timeout to success. A later deadline case correctly reported `WorkerShutdown`, but its cleanup exposed a start/stop race. The final image fixes that race, and the complete deadline experiment now passes, including restoration of the normal worker configuration.

This is a local spike result, not a production rollout decision. It does not cover the slim runner work, remote PlatformJobs, production ingress/TLS, production storage, production observability, or a full production Kubernetes release plan.

## Measured Results

| Run | Purpose | Load | Result | Latency / timing | Notes |
| --- | --- | ---: | --- | --- | --- |
| `baseline-warm` | Warm single-worker baseline | 100 executions, concurrency 1 | 100/100 success | p50 135 ms, p95 343 ms, p99 386 ms | No scale expected. Worker working set stayed flat: 318.3 MiB before, 318.6 MiB after. |
| `baseline-drain` | Confirm original drain behavior | 1 delayed execution, 15 s payload delay | 0/1 success | caller timeout at 45.4 s | Durable status was still `Running`, confirming incomplete child-execution drain. |
| `fixed-drain` | Verify drain fix | 1 delayed execution, 15 s payload delay | 1/1 success | 15.3 s | Same drain scenario completed successfully after the fix. |
| `final-warm` | Final-image warm comparison | 100 executions, concurrency 1 | 100/100 success | p50 145 ms, p95 264 ms, p99 341 ms | Working set 316.7 MiB before, 317.8 MiB after. Small samples do not establish a latency regression or improvement. |
| `final-drain` | Final-image graceful shutdown | 1 delayed execution, 15 s payload delay | 1/1 durable Success; experiment passed | 15.26 s | Replacement worker became ready. |
| `final-deadline` | Bounded shutdown and cleanup | 1 delayed execution, 1 s drain grace | Expected durable `WorkerShutdown`; experiment passed | 2.05 s | Configuration restored and replacement worker ready. |
| `cold` | Cold worker execution | 1 execution | 1/1 success | 9.6 s | First execution on cold worker completed. |
| `sustained-load` | Autoscaling and throughput | 96 executions, concurrency 96, 4 s payload delay | 96/96 success | p50 74.1 s, p95 78.1 s, p99 78.2 s | KEDA reached 4 ready workers at ~65.1 s. Work distribution: 50 / 17 / 19 / 10 by worker pod. |

Latency is producer-to-result time, not HTTP/browser latency. Cold activation
used a preloaded image on an existing local node. Sustained-load time includes
inline producer queue-position publication overhead; it is not a pure broker
throughput measure.

## Scaling Observations

The short 32-execution burst completed without scaling. That is acceptable for the local default: the burst finished before KEDA had a useful scaling window.

The 96-execution sustained run did scale. Replica sampling showed one ready worker initially, then four ready worker pods by 65.07 seconds. All 96 executions completed successfully.

The local KEDA setup keeps automatic scale-down disabled. Queue depth does not account for acknowledged active children, and forced node loss or unavailable result storage still needs durable reconciliation.

## Memory Observations

Warm baseline worker memory stayed effectively flat:

- Before: 318.3 MiB working set, 344.2 MiB peak.
- After 100 executions: 318.6 MiB working set, 344.2 MiB peak.

After sustained load, the four worker pods reported working sets around 317-319 MiB each:

- 319.0 MiB, 319.0 MiB, 317.1 MiB, 319.0 MiB.
- Peaks during the sustained run were about 456-457 MiB per worker pod.

Fresh-process RSS from the staged import probe (not cgroup working set):

| Stage | RSS |
| --- | ---: |
| Python plus diagnostic stdlib imports | 15.9 MiB |
| Configuration | 38.9 MiB |
| Database and mapper configuration | 110.5 MiB |
| Worker application imports | 123.0 MiB |
| Constructed consumers, no warm template | 132.4 MiB |
| Scheduler module | 153.3 MiB |
| Full PlatformJob registry | 151.5 MiB |
| Direct SDK-update handler module | 145.2 MiB |

Each stage runs in a fresh interpreter. These values are not incremental or
additive. They show a material fixed floor, while the ready worker's warm
process topology accounts for additional resident memory. The short steady
workload did not establish a leak. Removing the warm template would require a
separate latency comparison; this spike does not remove it or implement a lean
protected runner.

## Drain Findings

The original drain run produced:

- `status: caller_timeout`
- `durable_status: Running`
- elapsed time 45.4 s for a 15 s delayed payload

That confirmed the pre-fix worker could lose caller-visible completion during termination.

The fixed drain run produced:

- `status: Success`
- `durable_status: Success`
- elapsed time 15.3 s for the same 15 s delayed payload

That confirms the basic drain fix worked for an in-flight delayed execution.

The final deadline experiment returned and durably saved `WorkerShutdown` in 2.05 seconds for a 15-second payload with a one-second drain grace. Its configuration restoration and replacement-worker rollout also passed. Startup now stops opening consumers when shutdown is requested and waits for cleanup before the main task exits. An early SIGTERM handler also covers the heavy-import startup window.

## Current Validation State

Final scoped validation:

- 102 targeted tests passed (zero failures or errors in JUnit).
- `./test.sh quality api` passed: Python type checking and lint.
- Production Docker image build passed.
- Independent focused review approved the startup lifecycle changes.
- Kubernetes warm, graceful-drain and forced-deadline experiments passed on the final image, including cleanup.
- `bash -n scripts/kubernetes/local-kind.sh test.sh` and `git diff --check` passed.
- Harness Python lint passed via `docker run --rm -v "$PWD/scripts/kubernetes:/probe:ro" --entrypoint ruff bifrost-test-api-dev:latest check /probe`.

Exact targeted test command:

```bash
./test.sh tests/unit/test_worker_startup_signal.py tests/unit/test_worker_startup_lifecycle.py tests/unit/test_import_hygiene.py tests/unit/execution/test_process_pool.py tests/unit/jobs/consumers/test_workflow_execution_session.py tests/unit/test_elastic_runtime_memory.py tests/unit/test_elastic_runtime_spike.py tests/unit/test_kubernetes_spike_manifests.py tests/unit/test_compose_test_harness.py -q
```

Final image: `bifrost-elastic-spike:769-final`, Docker image ID
`sha256:400d7d68e1f8d058b492d621a5bed0fa109d86f1f849b0fd9aec3ab8e1a261ca`.
Cold and sustained-load results used the earlier drain-fix image; the final image
adds startup/shutdown coordination. Warm, graceful-drain and deadline experiments all passed on the final image.

The full backend, browser, and pre-PR suites were not run. This remains an
uncommitted local spike; no PR or production deployment was created.

## Commands And Artifacts

Primary captured artifacts:

```text
/tmp/bifrost-769-baseline-warm/workload.jsonl
/tmp/bifrost-769-baseline-warm/before-memory.json
/tmp/bifrost-769-baseline-warm/after-memory.json
/tmp/bifrost-769-baseline-drain/workload.jsonl
/tmp/bifrost-769-fixed-drain/workload.jsonl
/tmp/bifrost-769-cold/workload.jsonl
/tmp/bifrost-769-sustained-load/workload.jsonl
/tmp/bifrost-769-sustained-load/replicas.json
/tmp/bifrost-769-sustained-load/after-memory.json
/tmp/bifrost-769-memory-final.json
/tmp/bifrost-769-final-deadline/workload.jsonl
/tmp/bifrost-769-final-warm/workload.jsonl
/tmp/bifrost-769-final-drain/workload.jsonl
```

Representative local harness commands:

```bash
./test.sh kubernetes up
./test.sh kubernetes status
./test.sh kubernetes experiment --help
./test.sh kubernetes collect
./test.sh kubernetes down
```

Direct harness equivalents:

```bash
scripts/kubernetes/local-kind.sh up
scripts/kubernetes/local-kind.sh status
scripts/kubernetes/local-kind.sh experiment --help
scripts/kubernetes/local-kind.sh collect
scripts/kubernetes/local-kind.sh down
```
