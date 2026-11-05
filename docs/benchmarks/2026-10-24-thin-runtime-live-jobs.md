# Thin-worker follow-up: every job type and workflow latency

**All 23 registered PlatformJob types completed real work successfully on both the existing and thin worker designs.** Each matched run passed 15 tests, with no skips or failures; every recorded job succeeded on its first attempt. This is a historical local spike measurement, not a production deployment.

The matched runs used the API-hosted-control spike at historical commit `ecc0890f2aeb7ed3f1945ec169a24068cb5689ea`; this follow-up changed tests and fixtures without a further runtime change. The source/image hashes, 46 successful job records, 44 workflow samples, JUnit totals, and complete post-workload memory traces are in [the adjacent JSON](2026-10-24-thin-runtime-live-jobs.json). They do not describe the current promoted default runtime.

## Workflow speed

| Caller receives completed result | Existing worker | Thin worker |
|---|---:|---:|
| Simple return-value workflow, warm median | 108 ms | 91 ms |
| Simple return-value workflow, first call | 178 ms | 158 ms |
| Workflow with one SDK config read, warm median | 125 ms | 124 ms |
| Workflow with one SDK config read, first call | 160 ms | 122 ms |

Each kind has one first execution and ten warm executions. These are full synchronous API request timings through real PostgreSQL dispatch, worker execution, and completion, without a status-polling interval floor. The JSON also retains the stored create/start/complete timestamps and function-body duration. First-call timings assume a container that is already ready; they do not include container or Kubernetes startup. Source was registered immediately before each kind, warming the normal write-through source cache.

This small local sample shows no workflow slowdown. It is not a production p95/p99 or concurrency result. The echo body itself is a few microseconds; the SDK case adds one real scoped config read. Actual workflow business work and upstream latency add to this overhead. The earlier cached-image worker-container start measurement was 6.44 seconds existing versus 4.93 seconds thin; see the architecture report for its one-trial conditions and startup-memory caveat.

## Whole-container RAM after all job types

Both variants use the exact same image `sha256:a030a8e7d836f9331090c99284307d398285e5bd76827c9f8a7378052ef16b95`, one existing API, one executor, and 2 GiB limits for each measured container. The scheduler container is idle so there is no second executor. The provider fixtures are the same external-input stand-ins in both runs.

| Final idle window | Existing worker | Thin worker |
|---|---:|---:|
| Whole worker, median | 328.6 MiB | **87.7 MiB** |
| Whole worker, maximum | 334.5 MiB | 88.0 MiB |
| Existing API, median | 473.1 MiB | 508.8 MiB |
| API + worker, sum of medians | 801.8 MiB | 596.5 MiB |

The net reduction is **205.3 MiB (25.6%) for this pair**. The warm thin worker remains below 100 MiB after the larger workload. The earlier 75.5 MiB result was after a smaller workload, so it should not replace this all-types observation.

RAM is host-observed cgroup working set (`memory.current - inactive_file`) across the whole container, including all processes and kernel charge. The trace runs for 30 seconds after the suite; the table uses its last ten seconds. These are usage measurements, not Kubernetes requests or reserved limits. This follow-up did not sample active workload CPU/peaks. Jobs retain their existing heartbeat-based memory fields in the raw evidence; a short job can finish between heartbeats, so those fields are not exact per-child peaks. Active jobs and startup helpers still require memory above the idle footprint.

## What happened to the socket

In the thin variant, the worker no longer hosts the private SDK API/Unix socket. Workflow and service children call the existing API's authorized SDK endpoints over HTTP using their scoped runtime credentials. The API-to-supervisor WebSocket carries control commands, acknowledgements, results, and resource samples. The actual workflow code still runs in worker children.

In the historical thin variant, agents and summaries stayed warm in the API. Generic platform jobs executed the same registered handlers in fresh physical-worker subprocesses; their heavy dependencies disappeared when those processes exited. The worker image retained all capabilities, rather than becoming a minimal dependency image. The historical default production entrypoints still used the existing design, including its SDK socket.

## Every registered handler ran

Times below are durable start-to-complete observations, including fresh-process imports and handler work, excluding pre-claim queue delay. One sample per type and variant; differences are diagnostic. Summary-backfill time includes waiting for its real summary child. The totals are 71.73 versus 71.97 seconds summed across types. Full test-suite time was 118.17 versus 114.32 seconds, including setup, HTTP waits, and cleanup; stack reset/start overhead is separate.

| PlatformJob type | Existing | Thin | Status |
|---|---:|---:|---|
| `agent.summary_backfill` | 3.06 s | 2.83 s | Passed |
| `application.deploy` | 8.23 s | 7.48 s | Passed |
| `application.publish` | 2.93 s | 3.16 s | Passed |
| `application.sdk_update` | 5.49 s | 5.37 s | Passed |
| `artifact.retention_cleanup` | 1.97 s | 1.88 s | Passed |
| `audit.archive` | 2.23 s | 2.10 s | Passed |
| `audit.query` | 1.91 s | 1.92 s | Passed |
| `chat.video_generation` | 3.88 s | 3.76 s | Passed |
| `embedding.reindex` | 3.02 s | 2.86 s | Passed |
| `files.move` | 2.75 s | 2.70 s | Passed |
| `model_catalog.refresh` | 1.76 s | 1.82 s | Passed |
| `oauth.refresh` | 1.96 s | 2.38 s | Passed |
| `run.retention` | 1.49 s | 1.43 s | Passed |
| `sdk.video_generation` | 4.55 s | 3.98 s | Passed |
| `solution.deploy` | 3.41 s | 3.31 s | Passed |
| `solution.export` | 2.83 s | 3.31 s | Passed |
| `solution.git_sync` | 2.77 s | 2.71 s | Passed |
| `solution.update_check` | 2.76 s | 3.09 s | Passed |
| `webhook.renew` | 2.22 s | 2.83 s | Passed |
| `workspace.bundle_import` | 3.50 s | 3.67 s | Passed |
| `workspace.file_index_reconcile` | 2.21 s | 2.75 s | Passed |
| `workspace.git` | 3.45 s | 3.21 s | Passed |
| `workspace.reimport` | 3.35 s | 3.43 s | Passed |

The checks seed meaningful work: expired artifacts are removed from object storage; audit events are archived/exported; old events are deleted; a file is moved; source is indexed/imported; embeddings change; two video artifacts are submitted, polled, downloaded, stored and read; source Apps are compiled and rebuilt; inline Apps are published; Solution deploy/export/Git sync and workspace Git fetch run against real local repositories. OAuth, webhook renewal, Solution update detection and file-index repair reuse the existing scheduler fixture suite.

The SDK video job is initiated from a real workflow, which waits for the durable job and reads its artifact through the scoped SDK transport. The backfill creates one real `agent.summarize_backfill` dispatch and checks completed summary/run counters. Some tests enqueue through the canonical service after seeding fixtures, while others use HTTP; none invoke handlers directly or manufacture running leases.

No paid model call is made. Chat/embedding/video upstreams are deterministic local fixtures; the 24-byte video fixture exercises the transport/storage contract rather than media inference quality. Model-catalog refresh makes a real credential-free models.dev request. This is one successful path per registered type on the local backend, not every option/provider combination or the isolated Kubernetes backend.

## Reproduce the matched runs

### Historical exact reproduction

These overlay commands apply only after checking out historical commit `ecc0890f2aeb7ed3f1945ec169a24068cb5689ea` in a disposable worktree. That commit contains the experiment overlays; the current source intentionally deletes them.

```bash
COMPOSE_PROJECT_NAME=bifrost-test-b60bcc82 \
BIFROST_TEST_IMAGE_TAG=bifrost-test-b60bcc82 \
LOG_DIR=/tmp/bifrost-bifrost-test-b60bcc82 \
docker compose -f docker-compose.test.yml -f docker-compose.thin-spike.yml \
  -f docker-compose.thin-live-jobs.yml --profile e2e \
  up -d --no-build --force-recreate api worker scheduler thin-providers

./test.sh tests/performance/test_workflow_dispatch_latency.py \
  tests/e2e/platform/test_thin_platform_jobs_providers.py \
  tests/e2e/platform/test_thin_platform_jobs_misc.py \
  tests/e2e/platform/test_thin_platform_jobs_build.py \
  tests/e2e/platform/test_scheduler_fixture_workloads.py -q -s --durations=0

python3 api/scripts/worker_memory_probe.py \
  --container bifrost-test-b60bcc82-worker-1 \
  --container bifrost-test-b60bcc82-api-1 \
  --duration-seconds 30 --interval-seconds 0.1

BIFROST_SKIP_BUILD=1 BIFROST_TEST_IMAGE_TAG=bifrost-test-b60bcc82 ./test.sh quality api
```

At the time of the historical run, Pyright and Ruff passed. Full backend, frontend, Playwright, production-load and merge-queue suites were not run. The historical spike required an explicit live-fixture overlay; that result was not a merge-readiness claim.

Fixture errors found and permanently corrected: first-profile creation assigns all AI defaults (cleanup now restores all assignments); usage attribution needs a real SDK execution context (video now runs from a workflow); run retention has a 30-day minimum (fixture event is 31 days old); App metadata reads use slugs; shared Git mounts require the worktree `LOG_DIR`; and Git's per-command credential config replaces `GIT_CONFIG_COUNT` (the test rewrite now lives in `GIT_CONFIG_GLOBAL`). No product fallback, retries, timeout increases, skips, or xfails were added. Failed intermediate fixture runs are excluded from the successful evidence above.

### Current canonical validation

The current default runtime uses the canonical fixture lifecycle through `./test.sh`, without an overlay. Start the normal stack once, then invoke the exact test paths directly; do not prefix the command with `e2e`, which selects the whole E2E suite.

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

Current promotion verification and its limitations are recorded in the
[default-runner report](2026-10-25-default-remote-runner.md); the figures above
remain historical matched measurements.

## Cloudflare direction

The historical portability gain is an architectural inference, not a tested Cloudflare deployment: execution supervision was separated from the main application while durable coordination remained central. That created a smaller unit of remote compute to place elsewhere. The full image and job capabilities remained available; the pass reduced resident memory rather than removing dependencies from the image.

[Cloudflare Containers](https://developers.cloudflare.com/containers/get-started/) runs container images inside Linux VMs and supports on-demand instances coordinated through Workers/Durable Objects. It is the plausible target for the current fork/subprocess runner. [Native Python Workers](https://developers.cloudflare.com/workers/languages/python/stdlib/) use Pyodide; multiprocessing and threading are nonfunctional there, so native Workers would require a different execution implementation. These product capabilities were checked on 2026-10-25.

A Cloudflare port still needs validated container lifecycle/admission, control reconnection and ownership fencing, database/API/storage reachability and regional latency, package availability, and measured throughput. Trusted platform-job children currently retain direct PostgreSQL access. The local memory reduction alone does not establish Cloudflare deployment compatibility, cold-start behavior, or cost.

## Production-promotion implementation status — 2026-10-25

The current worktree promotes the runtime through the default API, worker, scheduler, Compose, and Kubernetes wiring. It replaces the experiment-only bootstrap and overlays with authenticated launcher control, incarnation and owner fencing, API-resident agent/summary execution, remote physical launchers, and canonical fixture lifecycle.

The [default-runner report](2026-10-25-default-remote-runner.md) records focused
verification, live job coverage, packages, large-result/cancellation recovery,
Kubernetes enrollment, API restart adoption, and isolated builds. The figures
above continue to describe only the historical matched experiment. PR #1066
tracks the merge gate; local validation is not a production rollout claim.

## Hosted deployment assessment — 2026-10-25

This remains a future, source-backed implementation roadmap, not a deployed or tested Cloudflare/Neon configuration. The measurements above remain local Docker results from the historical prototype. The current default-runtime promotion does not make a hosted deployment claim.

### First hosted deployment

The useful first target removes cluster management while retaining warm request handling. Full application scale-to-zero is a later milestone with different latency and scheduling requirements.

| Responsibility | Proposed host | Initial lifecycle |
|---|---|---|
| Static frontend and request routing | Cloudflare static hosting and a small routing Worker | Managed edge delivery |
| Existing Python API, execution control, agents and summaries | Cloudflare Containers, subject to network/throughput validation | Warm; replicas sized for availability and measured load |
| Workflow/service processes and platform-job handlers | Same thin launcher in Cloudflare Containers | Demand-started with a configurable warm minimum and idle drain |
| Relational state, work dispatch, worker identities and leases | Neon Postgres | Warm for predictable production latency initially |
| Source, application output and artifacts | R2 through the existing S3 storage boundary | Persistent independently of container lifecycle |
| Cache, log/event streams, result waits and pub/sub | A compatible managed Redis service | Retained initially; provider feature validation required |

Neon supports PostgreSQL and pgvector, so this direction preserves the relational data model rather than porting it to SQLite. Its transaction-mode pooled endpoint needs an actual compatibility rehearsal with our asyncpg/SQLAlchemy configuration; use an appropriate direct connection for migrations and session-dependent administration. Bifrost's scheduler leadership already uses database-time row leases specifically for transaction-pool compatibility. Source: [Neon connection pooling](https://neon.com/docs/connect/connection-pooling), [pgvector](https://neon.com/docs/extensions/pgvector).

R2 exposes an S3-compatible API and `RepoStorage` already accepts a configurable endpoint. That is a promising existing boundary, not evidence that every upload, presigned URL, multipart operation or browser CORS path has been validated on R2. Source: [R2 compatibility](https://developers.cloudflare.com/r2/api/s3/api/).

### Runtime validation before a provider port

1. **Validate memory admission by execution location before a provider port.** The promoted runtime separates API-agent and remote-executor ownership. Prove that its pre-claim reservations hold under full API capacity, concurrent reservations, oversized requests, and failed startup, and that resource advertisements/controller sizing describe the same domains.
2. **Validate launcher identity and adoption.** The promoted runtime binds authenticated launchers to canonical incarnations and owner fencing. Exercise Kubernetes Job/Pod bindings, API rolling restart, competing owners, stale results, and a provider-instance binding with no shared pod PID.
3. **Validate active-work reconciliation.** Exercise disconnect during launch, execution, and completion; retain and reconcile unacknowledged terminal results; preserve canonical loss handling after a genuinely expired lease; and avoid automatically replaying started workflows with external side effects.
4. **Validate the supported backend boundary in Kubernetes.** Exercise real package changes, large payloads, cancellation/drain, isolated launches, and concurrency. The historical all-types local result does not prove these failure paths.

The existing worker lifecycle, workflow completion ownership checks, service attempt tokens, PlatformJob lease tokens and unstarted-claim recovery are capabilities to extend. Cloudflare lifecycle coordination must not become another business-job queue or replace PostgreSQL dispatch authority.

### Cloudflare and Neon proof

After the shared runtime is sound, implement a provider adapter that starts/stops launchers and delivers advisory wakes while the canonical PostgreSQL claim remains authoritative. Validate one workflow, one SDK-reading workflow, one real app build and live log delivery first, then repeat every registered job type on the hosted backend. Trusted platform-job subprocesses still need database, Redis and object-storage connectivity; they are not API-only clients today.

Measure cold and warm HTTP latency, dispatch latency, SDK round trips, container startup, build speed, concurrency, dependency restoration and billing. Keep control and data geographically close and test actual network latency: Cloudflare exposes regional placement constraints, which do not promise that a container shares a datacenter with Neon. Source: [Cloudflare placement](https://developers.cloudflare.com/containers/concepts/placement/).

### What full scale-to-zero would additionally require

Current idle traffic prevents database sleep: executors poll PostgreSQL for work every second, local PlatformJob claims scan every two seconds, and API control continuously renews leadership and reconciles dispatch. Merely switching the database URL to Neon will not remove those queries. Neon normally suspends after five inactive minutes and reports resume in a few hundred milliseconds; that is a provider claim, not a Bifrost measurement. Source: [Neon scale to zero](https://neon.com/docs/introduction/scale-to-zero).

To let all compute sleep, externalize wake timing, not job authority. A durable next-due alarm or request must start the existing control/dispatch paths; committed work must remain discoverable if its immediate wake is lost. Cloudflare Durable Object alarms can wake coordination without a live container and have at-least-once delivery, so callbacks must use Bifrost's existing idempotence and fencing. Scheduler ordering, missed schedules and maintenance/recovery deadlines need explicit parity tests. Source: [Durable Object alarms](https://developers.cloudflare.com/durable-objects/api/alarms/).

The current API also owns browser WebSockets and Redis subscriptions. Retaining an open socket can require keeping that API alive. Sleeping it would require a deliberately implemented edge connection owner plus authorized replay/reconnect, or an explicit change in realtime behavior. Redis pub/sub alone cannot replay missed events. Continuous user services and actively running jobs are real work and must keep their runtime alive.

Warm API/control and database capacity is the initial choice for preserving response speed. A fully sleeping environment would add application/container/database cold starts. It cannot be promised to match warm latency. Removing Redis or rewriting the Python API as native Workers is not required for the first hosted deployment.
