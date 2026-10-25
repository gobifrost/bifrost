# Thin-worker follow-up: every job type and workflow latency

**All 23 registered PlatformJob types completed real work successfully on both the existing and thin worker designs.** Each matched run passed 15 tests, with no skips or failures; every recorded job succeeded on its first attempt. This is an explicit local spike, not a production deployment.

The architecture remains the [API-hosted-control spike](2026-10-24-thin-api-worker-spike.md) at `a263ba418`. This follow-up changes tests and fixtures, with no further runtime changes. The source/image hashes, 46 successful job records, 44 workflow samples, JUnit totals, and complete post-workload memory traces are in [the adjacent JSON](2026-10-24-thin-runtime-live-jobs.json).

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

Agents and summaries stay warm in the API. Generic platform jobs still execute the same registered handlers in fresh physical-worker subprocesses; their heavy dependencies disappear when those processes exit. The worker image retains all capabilities, rather than becoming a minimal dependency image. Default production entrypoints still use the existing design, including its SDK socket.

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

These commands name this worktree's project. In another worktree, use the project printed by `./test.sh stack status`. Boot once with `./test.sh stack up`. Choose `docker-compose.thin-spike.yml` for thin or `docker-compose.thin-spike-baseline.yml` for existing. Preserve `LOG_DIR`: it makes the Git fixture mount identical in the API, worker and runner.

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

Pyright and Ruff passed. Full backend, frontend, Playwright, production-load and merge-queue suites were not run. These spike tests require the explicit live-fixture overlay; they are not a claim that the branch is merge-ready.

Fixture errors found and permanently corrected: first-profile creation assigns all AI defaults (cleanup now restores all assignments); usage attribution needs a real SDK execution context (video now runs from a workflow); run retention has a 30-day minimum (fixture event is 31 days old); App metadata reads use slugs; shared Git mounts require the worktree `LOG_DIR`; and Git's per-command credential config replaces `GIT_CONFIG_COUNT` (the test rewrite now lives in `GIT_CONFIG_GLOBAL`). No product fallback, retries, timeout increases, skips, or xfails were added. Failed intermediate fixture runs are excluded from the successful evidence above.

## Cloudflare direction

The portability gain is an architectural inference, not a tested Cloudflare deployment: execution supervision is now separated from the main application, while durable coordination remains central. That creates a smaller unit of remote compute to place elsewhere. The full image and job capabilities remain available; this pass reduces resident memory rather than removing dependencies from the image.

[Cloudflare Containers](https://developers.cloudflare.com/containers/get-started/) runs container images inside Linux VMs and supports on-demand instances coordinated through Workers/Durable Objects. It is the plausible target for the current fork/subprocess runner. [Native Python Workers](https://developers.cloudflare.com/workers/languages/python/stdlib/) use Pyodide; multiprocessing and threading are nonfunctional there, so native Workers would require a different execution implementation. These product capabilities were checked on 2026-10-25.

A Cloudflare port still needs validated container lifecycle/admission, control reconnection and ownership fencing, database/API/storage reachability and regional latency, package availability, and measured throughput. Trusted platform-job children currently retain direct PostgreSQL access. The local memory reduction alone does not establish Cloudflare deployment compatibility, cold-start behavior, or cost.

## Remaining production work

Live type coverage is now complete for the local backend. Production incarnation-scoped Kubernetes enrollment, API reconnect/adoption and owner fencing, and correct pre-claim API/worker resource admission are still blockers. The existing prototype can hold an agent claim while waiting indefinitely for API capacity, and ends children when the control connection dies. Also validate isolated Kubernetes launches, real package changes, large payloads, cancellation/loss/drain under load, and API/SDK throughput. Consolidate the dev adapter/test-fixture lifecycle and rebase onto current main before a PR or rollout.

## Hosted deployment assessment — 2026-10-25

This is a source-backed implementation roadmap, not a deployed or tested Cloudflare/Neon configuration. The measurements above remain local Docker results. No runtime code changes were made for this assessment.

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

### Shared runtime work before a provider port

1. **Separate memory admission by execution location before claiming.** `ApiExecutor._run_claim` currently waits for API memory after PostgreSQL assignment. Add a narrow claim-admission seam to `PoolWorker`, keeping its canonical claim loop. Under a consistent lock order, reserve the resources required by each handler before committing its claim. Remote workflow work must remain eligible when only the API's agent/summary budget is exhausted. Cover full API capacity, concurrent reservations, oversized requests and release after failed startup. Resource advertisements and controller sizing must describe the same domains; a new remote launcher does not add API agent capacity.
2. **Make launcher identity independent of its connection.** Reuse canonical worker incarnations, liveness and lost-worker recovery. Replace the single-use local bootstrap with authenticated enrollment bound to an immutable physical launcher identity. Kubernetes enrollment must retain its Job/Pod identity checks; a Cloudflare adapter needs an equivalent authenticated provider-instance binding. Add a persisted control-owner lease and session generation so another API replica can adopt a launcher without trusting an old socket or a PID on another machine.
3. **Reconcile active work across reconnects.** Inventory active route IDs with execution/attempt/job lease identity and retain unacknowledged terminal results until ownership-aware reconciliation accepts them. Superseded sessions must not issue commands or write completion. A transient transport loss should not immediately destroy all children; an expired ownership lease must still trigger canonical loss handling. Preserve existing workflow replay semantics: do not automatically rerun started workflows that may have external side effects. Test disconnect during launch, execution and completion, API restart, competing API owners, stale results, container loss and cancellation while disconnected.
4. **Consolidate the implementation and validate Kubernetes.** Promote the dev adapter into one supported backend boundary, remove obsolete experiment-only paths, and exercise real package changes, large payloads, cancellation/drain, isolated launches and concurrency. Rebase against current main and repeat focused verification after the implementation changes. The all-types local result does not prove these failure paths.

The existing worker lifecycle, workflow completion ownership checks, service attempt tokens, PlatformJob lease tokens and unstarted-claim recovery are capabilities to extend. Cloudflare lifecycle coordination must not become another business-job queue or replace PostgreSQL dispatch authority.

### Cloudflare and Neon proof

After the shared runtime is sound, implement a provider adapter that starts/stops launchers and delivers advisory wakes while the canonical PostgreSQL claim remains authoritative. Validate one workflow, one SDK-reading workflow, one real app build and live log delivery first, then repeat every registered job type on the hosted backend. Trusted platform-job subprocesses still need database, Redis and object-storage connectivity; they are not API-only clients today.

Measure cold and warm HTTP latency, dispatch latency, SDK round trips, container startup, build speed, concurrency, dependency restoration and billing. Keep control and data geographically close and test actual network latency: Cloudflare exposes regional placement constraints, which do not promise that a container shares a datacenter with Neon. Source: [Cloudflare placement](https://developers.cloudflare.com/containers/concepts/placement/).

### What full scale-to-zero would additionally require

Current idle traffic prevents database sleep: executors poll PostgreSQL for work every second, local PlatformJob claims scan every two seconds, and API control continuously renews leadership and reconciles dispatch. Merely switching the database URL to Neon will not remove those queries. Neon normally suspends after five inactive minutes and reports resume in a few hundred milliseconds; that is a provider claim, not a Bifrost measurement. Source: [Neon scale to zero](https://neon.com/docs/introduction/scale-to-zero).

To let all compute sleep, externalize wake timing, not job authority. A durable next-due alarm or request must start the existing control/dispatch paths; committed work must remain discoverable if its immediate wake is lost. Cloudflare Durable Object alarms can wake coordination without a live container and have at-least-once delivery, so callbacks must use Bifrost's existing idempotence and fencing. Scheduler ordering, missed schedules and maintenance/recovery deadlines need explicit parity tests. Source: [Durable Object alarms](https://developers.cloudflare.com/durable-objects/api/alarms/).

The current API also owns browser WebSockets and Redis subscriptions. Retaining an open socket can require keeping that API alive. Sleeping it would require a deliberately implemented edge connection owner plus authorized replay/reconnect, or an explicit change in realtime behavior. Redis pub/sub alone cannot replay missed events. Continuous user services and actively running jobs are real work and must keep their runtime alive.

Warm API/control and database capacity is the initial choice for preserving response speed. A fully sleeping environment would add application/container/database cold starts. It cannot be promised to match warm latency. Removing Redis or rewriting the Python API as native Workers is not required for the first hosted deployment.
