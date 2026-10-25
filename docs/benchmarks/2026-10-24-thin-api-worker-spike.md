# Thin worker with API-hosted execution control — 2026-10-24

**Historical follow-up:** [All 23 job types passed live on both runtime variants](2026-10-24-thin-runtime-live-jobs.md), with direct workflow timings and RAM measured after that larger workload. The tables below preserve the earlier, smaller experiment.

This local spike met the **100 MiB warm whole-worker target** on the exercised workload. The measurements describe the 2026-10-24 experimental source, not a production rollout or a result from the current default runtime.

## Measured memory and speed

Both variants use the same image/dependencies, the existing API container, one executor, and 2 GiB container limits. The control uses the import-reduced worker at `93b5712e1` plus the default-preserving runtime seams in this branch. This is a stricter comparison than the original 500–700 MiB production worker.

| After the same workload | Existing worker | Thin worker |
|---|---:|---:|
| Whole worker idle | 335.7 MiB | **75.5 MiB** |
| Existing API idle | 398.0 MiB | 465.0 MiB |
| API + worker idle | 733.8 MiB | **540.5 MiB** |
| Worker maximum in the final idle window | 339.6 MiB | 79.3 MiB |
| Worker observed peak during the probe | 722.7 MiB | 568.4 MiB |
| Warm agent + automatic summary, median of 5 | 278 ms | 289 ms |
| First agent + summary after reset | 1.824 s | 1.566 s |
| SDK AI calls inside a real workflow | [0.09, 0.079] s | [0.065, 0.041] s |

The worker saves about 260 MiB; the API absorbs about 67 MiB; the net saving is about **193 MiB / 26% for this one API + worker pair**. The thin worker's final idle maximum, including transient health-probe processes, stays below 100 MiB. Its resident processes are the small supervisor and the existing workflow template. No per-worker API sidecar is excluded from these totals.

The 11 ms warm agent/summary median difference is a small observed difference, not a guarantee of equal latency at production throughput. Both runs used a local deterministic provider and a quiet synthetic workload. The SDK test made two real provider calls per variant through the existing SDK routes, using the local socket for the control and network HTTP for the thin worker. Service lifecycle call time was 17.61 versus 17.56 seconds; the four maintenance jobs took 6.22 versus 6.19 seconds in the existing end-to-end fixture. Those test times include API waits and fixture work.

Memory is host-observed cgroup working set (`memory.current - inactive_file`), with raw current/anonymous/kernel values retained in the JSON. These are usage observations, not the 2 GiB limits or Kubernetes requests. The idle statistic uses the last ten seconds of each 60-second trace. The probe requested 100 ms sampling; actual elapsed timestamps are retained. Docker health confirmation lag meant the traces started at different active-work phases, so their **active CPU totals and workload peaks are diagnostic, not a valid paired CPU/peak comparison**. Request timings and final idle RAM are independently observed and comparable. A phase-aligned load test is still required for CPU and API throughput conclusions.

## Cold container startup

Fresh containers and fresh Python processes, with the image already local, warm filesystem/page caches, warm PostgreSQL/Redis, and no user requirements to install:

| Start → ready | Existing worker | Thin worker |
|---|---:|---:|
| Worker container | 6.44 s | **4.93 s** |
| API application | 8.52 s | 8.25 s |
| Worker cgroup peak by readiness | 252.0 MiB | **173.5 MiB** |

One trial per variant. Worker readiness is the existing ready marker, observed through the host's `/proc/<pid>/root` without adding a measurement process inside the container. API readiness uses Uvicorn's application-startup-complete timestamp. The API was ready before the worker started. This excludes image pulls, Kubernetes scheduling/CNI, and package installation from non-empty requirements. Thus **75 MiB warm does not mean a 100 MiB hard limit**: startup helpers briefly use about 174 MiB, and active jobs still need their admitted memory.

## Architecture in the 2026-10-24 spike

- The existing API process hosts the canonical `PoolWorker`, PostgreSQL claims/leases, workflow preparation/completion, Redis bookkeeping, SDK routes, and warm agent/summary execution.
- An authenticated development-only WebSocket carries commands, acknowledgements, child results, and resource samples. Route UUIDs fence late results. The channel is transport; durable dispatch and user-visible status remain in `WorkDispatch`, `PlatformJob`, and the existing notification system.
- The thin worker imports OS supervision, the WebSocket client, and the template controller. It owns child creation, exit handling, cancellation, deadlines, CPU/RSS sampling, and the canonical dangerous-memory policy. It does not construct the API application or ORM in its resident supervisor.
- Workflows and services fork from the existing clean template. They receive the existing scoped runtime credentials and use the same authorized SDK routes over HTTP. A post-fork child marker now identifies execution children independently of whether their SDK transport is a local socket or network HTTP; source loading remains scoped and bypasses direct Redis/S3 access in either case.
- Platform jobs use the unchanged registry and `src.jobs.platform.runner` in a subprocess in the physical worker. Heavy handler dependencies load while that job runs and disappear when the process exits. These trusted job children retain their existing database access and fenced progress/result writes.
- Requirements and package tools run in short-lived helpers in the physical worker. The API-hosted canonical package consumer coordinates progress, remote template recycle and package inventory. The installed dependency set/image remains fully capable; this reduces resident RAM, not image size.

This moves the ownership of claims and warm privileged execution as well as SDK HTTP handling. It is more than moving the SDK socket back to an API URL. Agent/summary execution remains warm in the API; the rejected subprocess-per-agent experiment and its multi-second cold penalty are not part of this branch.

At the time of this experiment, the default production entrypoints remained the existing worker and API. The thin candidate was selected with Compose overlays and a Uvicorn factory. Internal backend injection reused the canonical platform-job lease/cancel/timeout/loss state machine. Remote resource samples initialized job memory at claim time; the API cgroup was not used to size a remote attempt.

## What was actually exercised

Each matched runtime run passed **40 tests**. Real work included six API-enqueued agents with six automatic summaries, one registered workflow making two SDK AI calls, four canonical platform-job handlers, and a service ready/emit/log/restart/stop lifecycle. The rest of the 40 are focused transport, admission/resource, package-proxy and diagnostic tests. A separate focused run passed **155 tests**, including normal worker lifecycle, platform-job supervision/memory profiles, services, source isolation, credentials and real template-process behavior. The runs overlap; these are not 195 unique tests. Three additional catalogue tests passed, checking the definitions, lazy imports, and policy materialization for all 23 registered types. This is definition-level coverage, not evidence that all handlers ran.

**We did not live-test all 23 platform-job types.** The exact live coverage is:

| Registered PlatformJob type | Thin-runtime evidence |
|---|---|
| `agent.summary_backfill` | parent reconciliation only; handler/backfill children not exercised |
| `application.deploy` | not live-tested in this spike |
| `application.publish` | not live-tested in this spike |
| `application.sdk_update` | not live-tested in this spike |
| `artifact.retention_cleanup` | not live-tested in this spike |
| `audit.archive` | not live-tested in this spike |
| `audit.query` | not live-tested in this spike |
| `chat.video_generation` | not live-tested in this spike |
| `embedding.reindex` | not live-tested in this spike |
| `files.move` | not live-tested in this spike |
| `model_catalog.refresh` | not live-tested in this spike |
| `oauth.refresh` | passed |
| `run.retention` | not live-tested in this spike |
| `sdk.video_generation` | not live-tested in this spike |
| `solution.deploy` | not live-tested in this spike |
| `solution.export` | not live-tested in this spike |
| `solution.git_sync` | not live-tested in this spike |
| `solution.update_check` | passed |
| `webhook.renew` | passed |
| `workspace.bundle_import` | not live-tested in this spike |
| `workspace.file_index_reconcile` | passed |
| `workspace.git` | not live-tested in this spike |
| `workspace.reimport` | not live-tested in this spike |

Work-dispatch families exercised live: `workflow.execute`, `agent.run`, and `agent.summarize`. `agent.summarize_backfill` was not exercised as a live child. The summary-backfill fixture reconciles its parent status only. Services exercised the real canonical claim loop. The isolated Kubernetes Job launch/admission path was not exercised. No external OpenAI, Anthropic, GitHub, or video-provider call was made.

## Rollout gaps recorded at the time of the spike

1. Replace the single-launcher local bootstrap file with incarnation-scoped production admission, preserving the existing Kubernetes Job UID / Pod UID binding, and wire the thin entrypoint into worker Job manifests.
2. Implement reconnect/adoption and control-owner fencing across API rolling restarts and network failures. The prototype ended its session/children when its API connection died.
3. Make API and physical-worker memory domains explicit **before claiming work**, including capacity planning. The prototype could hold an agent claim while waiting for API capacity.
4. Verify live job families, large input/result handling, real package changes, cancellation/loss/drain under concurrent work, phase-aligned CPU, and SDK/API p95/p99 throughput.
5. Consolidate the experiment into the shared execution backend and run focused verification plus the merge gate before rollout.

## Production-promotion implementation status — 2026-10-25

The current worktree promotes the thin runtime to the default API, worker, scheduler, Compose, and Kubernetes wiring. It replaces the experiment-only bootstrap and overlays with authenticated launcher control, incarnation and owner fencing, API-resident agent/summary execution, remote physical launchers, and the existing Kubernetes control loops for legacy scheduler-Role installations.

The [default-runner report](2026-10-25-default-remote-runner.md) records current
local verification and measurements. This is not a production rollout claim.
The historical matched measurements above remain evidence about the commits
named below. PR #1066 tracks the merge gate.

## Reproduction and verification

### Historical exact reproduction

The measurements above used experiment commit `ecc0890f2aeb7ed3f1945ec169a24068cb5689ea`, which still contains `docker-compose.thin-spike.yml` and `docker-compose.thin-spike-baseline.yml`. Check out that commit in a disposable worktree to use the following historical overlay commands; those files are intentionally absent from the current default-runtime source.

```bash
COMPOSE_PROJECT_NAME=bifrost-test-b60bcc82 \
BIFROST_TEST_IMAGE_TAG=bifrost-test-b60bcc82 \
docker compose -f docker-compose.test.yml -f docker-compose.thin-spike.yml \
  --profile e2e up -d --no-deps --no-build --force-recreate api worker scheduler

./test.sh tests/performance/test_agent_dispatch_latency.py \
  tests/performance/test_sdk_ai_footprint.py \
  tests/e2e/platform/test_scheduler_fixture_workloads.py \
  tests/e2e/platform/test_service_worker_mode.py::TestServiceWorkerMode::test_healthy_service_lifecycle \
  tests/unit/dev tests/unit/services/execution/test_cpu_sampler.py \
  tests/unit/test_worker_memory_probe.py -q -s --durations=0

python3 api/scripts/worker_memory_probe.py \
  --container bifrost-test-b60bcc82-worker-1 \
  --container bifrost-test-b60bcc82-api-1 \
  --duration-seconds 60 --interval-seconds 0.1

BIFROST_SKIP_BUILD=1 BIFROST_TEST_IMAGE_TAG=bifrost-test-b60bcc82 ./test.sh quality api
```

The scheduler container was deliberately idle in both historical overlays so each comparison had one executor. The ordinary API hosted its canonical scheduler/control loops. `test.sh` reset/start behavior retained the selected historical commands.

### Current canonical validation

The current source has no thin-runtime Compose overlay. Start the normal test stack once, then pass the exact test paths directly to `./test.sh`; do not prefix the command with `e2e`, because that selects the whole E2E suite rather than these paths.

```bash
./test.sh stack up

./test.sh \
  tests/performance/test_workflow_dispatch_latency.py \
  tests/e2e/platform/test_thin_platform_jobs_providers.py \
  tests/e2e/platform/test_thin_platform_jobs_misc.py \
  tests/e2e/platform/test_thin_platform_jobs_build.py \
  tests/e2e/platform/test_scheduler_fixture_workloads.py -q -s --durations=0

./test.sh quality api
```

Promotion-run results and exact verification commands are recorded in the
[default-runner report](2026-10-25-default-remote-runner.md) and its JSON evidence.

The separate 155-test command was:

```bash
BIFROST_TEST_UNIT_ONLY=1 ./test.sh \
  tests/unit/dev tests/unit/test_pool_main_runtime_seams.py \
  tests/unit/test_pool_main_hosting.py tests/unit/test_pool_main_summaries.py \
  tests/unit/test_pool_main_packages.py tests/unit/test_pool_main_database.py \
  tests/unit/jobs/schedulers/test_platform_jobs.py \
  tests/unit/jobs/schedulers/test_platform_job_execution_backend.py \
  tests/unit/services/test_platform_job_memory_profiles.py \
  tests/unit/jobs/test_platform_job_runner.py tests/unit/test_service_claim.py \
  tests/unit/jobs/consumers/test_workflow_execution_lineage.py \
  tests/unit/jobs/consumers/test_workflow_execution_session.py \
  tests/unit/core/test_module_cache.py tests/unit/core/test_module_cache_marked_child.py \
  tests/unit/core/test_module_cache_import_boundary.py \
  tests/unit/execution/test_worker_credentials.py \
  tests/unit/execution/test_template_process.py \
  tests/unit/execution/test_template_import_boundary.py -q
```

The catalogue verification command was `BIFROST_TEST_UNIT_ONLY=1 ./test.sh tests/unit/jobs/test_platform_job_catalog.py -q` (3 passed).

Pyright and Ruff passed. Full backend, frontend, Playwright, and merge-queue suites were not run. No API DTO or generated TypeScript contract changed.

Implementation failures found and resolved included socket-only execution-child detection, attributing remote job memory to the API at claim time, control-route registration behind the MCP catch-all, result-before-ACK ordering, and a pre-existing test harness mock that returned a truthy `AsyncMock` for an empty platform-job queue and starved the event loop. The harness now returns `None` explicitly. No retries, increased timeouts, skips or xfails were added to obtain the passing results.

The adjacent JSON contains image IDs, dependency/source hashes, all 23 job-coverage entries, request timing samples, raw cgroup traces, cold-start traces, and JUnit totals. Failed intermediate runs are excluded; active CPU/peak traces are retained as diagnostics only.
