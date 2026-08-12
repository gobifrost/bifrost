# Platform jobs

## Decision

`PlatformJob` is Bifrost's canonical system for durable, non-workflow work that
can outlive an HTTP request. New platform operations must extend this system
instead of creating a feature-specific job table, worker, scheduler host,
status endpoint, progress event, or UI polling implementation.

The migrated job families are application publishing, OAuth refresh, webhook
renewal, Solution update checks, Solution export/deploy/install, isolated
Solution application builds, embedding reindex, workspace reimport and git
operations, and agent-summary backfills.
Feature-specific rows that remain for API compatibility are projections; they
do not own execution.

## When to use it

Use a platform job when a unit of work is user-initiated or independently
observable and needs one or more of:

- execution beyond a normal HTTP timeout;
- status, progress, result, or error state that survives the initiating request;
- active-operation deduplication or concurrency control;
- bounded retries after process or container loss;
- timeout, cancellation, or memory-pressure protection.

Keep short, request-scoped work synchronous. Workflow and agent execution
continue to use the execution worker infrastructure. A recurring scheduler task
may remain a scheduler function when it is small and maintenance-oriented; when
it creates long-running durable units, the trigger should enqueue platform jobs
instead of performing those units inline.

If a requirement does not fit the current platform-job model, make an explicit
architecture decision and extend the shared model. Do not introduce a parallel
job framework as a local workaround.

## Runtime and durability

PostgreSQL is the source of truth. Each `platform_jobs` row stores the typed
payload version, requester and resource scope, progress, result or structured
error, retry policy, timestamps, and lease state.

Every scheduler replica is a platform-job host; there is no separate
platform-job container. Each replica runs two claim loops as an internal safety
ceiling, not a deployment setting. Each loop:

1. recovers expired leases from a stopped scheduler or runner;
2. claims one available row with `FOR UPDATE SKIP LOCKED`;
3. fences that attempt with a unique lease token;
4. starts the registered handler in a fresh child process and process group;
5. heartbeats the lease while enforcing timeout, cancellation, and cgroup
   memory policy;
6. records a terminal result or requeues an eligible lost attempt.

The child shares the scheduler container's cgroup. Admission checks require the
job policy's minimum memory headroom and ratio before starting. The host stops
the child process group at its hard memory ratio before the scheduler container
reaches its limit. If the scheduler container still exits or is OOM-killed, the
durable lease expires and a later scheduler instance retries or fails the job
according to policy.

PostgreSQL row locking assigns each job to exactly one slot on one replica.
Adding replicas adds both independent cgroups and execution capacity. Two slots
allow serialized Solution work to dispatch isolated child work while retaining
capacity for other system jobs. This is deliberately an implementation detail:
operators should provision scheduler resources and replicas, not tune an
internal process count. If resource classes become necessary, evolve the common
host and its policy contract; do not add per-feature worker containers.

One replica also holds the fenced `scheduler-triggers` database lease. Only
that trigger leader runs APScheduler. On-demand work is persisted directly to
PostgreSQL and does not depend on Redis delivery. The lease uses database-time expiry rather than a session advisory
lock because supported deployments use transaction-mode PgBouncer. A follower
takes over after expiry when the leader stops renewing; trigger services stop
before a healthy leader voluntarily releases its lease.

Leader failover recreates APScheduler's in-memory schedules. Existing callbacks
configured to run immediately at scheduler startup therefore have at-least-once
failover behavior. Long-running callbacks must migrate by making the elected
callback enqueue a deduplicated platform job. Small, idempotent housekeeping may
remain leader-only scheduler work.

The elected leader retains only short trigger and housekeeping callbacks:
workflow schedule processing, deferred execution promotion, stuck execution and
event cleanup, metric snapshots, knowledge-storage metrics, artifact retention,
and diagnostics/backfill reconciliation. Recurring OAuth, webhook, and Solution
update callbacks enqueue singleton high-priority platform jobs. User-initiated
long-running work is claimed by any replica.

A handler may release its scheduler slot in `waiting` state after dispatching
durable child work. The child tracker completes the parent row later. Summary
backfills and Solution app builds use this path so RabbitMQ fan-out or an
isolated build process does not occupy scheduler capacity.

Lease-token checks fence stale runners: only the current attempt may update
progress or record a terminal result. A handler may be retried after runner
loss only when its side effects are idempotent. Otherwise set
`retry_on_runner_loss=False` or use a single attempt.

## Capacity and resource policy

`PlatformJobPolicy` is an execution guardrail, not a second container scheduler.
Every job type must declare a timeout, retry behavior, minimum memory headroom,
and admission/hard memory ratios. The host evaluates those values against the
container's cgroup-v2 working set and memory limit before and during execution.
Deployments therefore need a real container memory limit; without one, memory
admission is unavailable.

Size a scheduler replica for its host plus the heaviest safe combination of jobs
it may admit. Establish a new job's initial policy from
representative data volume: record peak cgroup working set and wall-clock
duration, include operational margin, and exercise low-memory deferral and
timeout behavior in tests. These measurements inform policy and deployment
guidance; they are not per-job CPU or memory reservations.

The kernel persists the cgroup working set at job start, the peak observed while
the attempt runs, and the cgroup limit. The difference between start and peak is
shown in Scheduler Diagnostics as a practical sizing sample. It includes the
scheduler host and any other occupied slots, so treat it as an operational
upper-bound signal rather than exact process-tree attribution. Add shared
process-tree/CPU telemetry only when these representative samples prove
insufficient.

Scale replicas when queue wait time grows while all slots are occupied. Increase
container memory when representative jobs are repeatedly deferred or approach
the hard ratio. Do not add Kubernetes-style resource classes until observed
workloads show that one common scheduler size cannot safely serve the registered
job types.

`GET /api/platform/scheduler` and the Diagnostics → Scheduler tab expose leader
health, online replicas and active claims, queue age, memory-admission waits,
maximum replica memory utilization, every registered schedule and its last run,
per-job memory samples, and explicitly published operator-safe logs. Persistent
memory waits/high utilization indicate vertical scaling; a growing queue while
all slots are occupied indicates horizontal scaling.

## Shared caller contract

An enqueue endpoint returns HTTP `202 Accepted` with
`PlatformJobAccepted` and a `Location: /api/platform-jobs/{job_id}` header.
Repeated requests with the same active deduplication key reuse the existing
operation instead of launching conflicting work. An endpoint must never return
a reused job ID that the caller cannot read.

All job types use the shared observation endpoints:

- `GET /api/platform-jobs` lists visible jobs;
- `GET /api/platform-jobs/{job_id}` returns durable status, progress, result,
  and error;
- `POST /api/platform-jobs/{job_id}/cancel` requests cancellation when allowed
  by the job policy.

Jobs are requester-visible; platform administrators may inspect all jobs.
Enqueue endpoints remain responsible for authorizing the underlying action and
setting the correct organization and resource metadata.

`PlatformJobPublic` is the single status contract for HTTP and WebSocket
delivery. Changes that break a CLI-consumed enqueue or status contract require
a matching `CONTRACT_VERSION` bump in both the server and packaged CLI.

## UI, CLI, and MCP behavior

The browser does not poll jobs. Enqueue code attaches a standard notification,
and job changes are projected into the existing user notification WebSocket
channel. The `platform_job_updated` event carries the same
`PlatformJobPublic` shape as the status endpoint for screens that need
job-specific live behavior. Reconnect or page-load recovery may read the shared
status/list endpoints; it must not introduce a per-feature polling system.

The CLI may poll `GET /api/platform-jobs/{job_id}` because it does not maintain
the browser's WebSocket session. Each poll is a short HTTP request; a CLI-side
overall wait deadline must explain that the durable server operation may still
be running. Retrying the enqueue request must be safe through its deduplication
key.

MCP mutations must remain thin REST wrappers. They enqueue through the same
REST endpoint and observe the same platform-job contract; MCP tools must not
invoke handlers or repositories directly.

## Adding a job type

1. Add `api/src/jobs/platform/<job_name>.py`.
2. Define a Pydantic payload model and version. Stored payloads must be
   sufficient to run after the request context is gone. Set
   `encrypt_payload=True` when the durable payload contains credentials, config
   values, passwords, or other protected inputs; the public JSON column then
   contains only a protected marker.
3. Implement a handler with the
   `PlatformJobHandler(PlatformJobContext, payload)` contract. Report meaningful
   phases and bounded progress with `context.report(...)`. Return a
   JSON-serializable result.
4. Raise `PlatformJobFailure(code, message, retryable=...)` for expected
   operator-visible failures. Do not hide errors in logs or depend on the
   initiating request to surface them.
5. Define `PlatformJobPolicy` explicitly: timeout, attempts, runner-loss retry,
   memory admission and hard limits, and whether a running job may be
   cancelled. Retry-enabled handlers must be idempotent.
6. Register the definition in `api/src/jobs/platform/registry.py`. The registry
   is the only dispatch map.
7. In the authorized REST endpoint, call `enqueue_platform_job(...)` with a
   stable active-operation deduplication key and complete requester, resource,
   title, and action metadata. Attach a notification, commit the row, publish
   its initial update, and return `202 PlatformJobAccepted` with `Location`.
8. Reuse the generic status/cancel routes and WebSocket contract. Add only the
   feature-specific CLI enqueue command or UI trigger; do not add a
   feature-specific status transport.
9. Keep MCP as a thin wrapper over that REST endpoint.

## Test contract

Every new job type needs focused coverage for:

- payload validation and registry dispatch;
- enqueue response and durable visibility;
- progress plus terminal success;
- structured handler failure;
- duplicate/concurrent enqueue behavior;
- lease fencing, runner loss, retry policy, timeout, and cancellation behavior
  affected by its policy;
- caller visibility and organization authorization;
- WebSocket/notification projection without browser polling;
- CLI timeout wording and safe retry behavior when a CLI command is added.

Use the shared platform-job service and scheduler tests for generic behavior;
add feature tests only for the handler's business semantics and its caller
surfaces.

## Local scheduler validation

With a worktree debug stack running, execute `./debug.sh fixtures` to seed and
run deterministic scheduler workloads through the same trigger and platform-job
paths used in production. The fixture service supplies local OAuth-token and Git
endpoints, so this validation does not require a live OAuth account or remote
repository.

The command verifies OAuth refresh, webhook renewal, Solution update detection,
file-index reconciliation, summary-backfill reconciliation, and Solution-build
reconciliation, then leaves their runs and published logs available under
Diagnostics → Scheduler. Fixture registration and seeding are rejected when the
application environment is production.

## Canonical implementation map

| Concern | Location |
| --- | --- |
| Durable ORM row | `api/src/models/orm/platform_jobs.py` |
| Public HTTP/WebSocket contracts | `api/src/models/contracts/platform_jobs.py` |
| Enqueue, progress, completion, notifications | `api/src/services/platform_jobs.py` |
| Handler and policy contracts | `api/src/jobs/platform/base.py` |
| Registered job types | `api/src/jobs/platform/registry.py` |
| Isolated child runner | `api/src/jobs/platform/runner.py` |
| Lease recovery, claiming, and resource enforcement | `api/src/jobs/schedulers/platform_jobs.py` |
| Trigger leader election | `api/src/scheduler/leadership.py` |
| Generic status and cancellation API | `api/src/routers/platform_jobs.py` |
| Scheduler host registration | `api/src/scheduler/main.py` |
| Scheduler run/replica/log diagnostics | `api/src/services/scheduler_diagnostics.py` |
| Diagnostics API | `api/src/routers/scheduler_diagnostics.py` |
| Diagnostics UI | `client/src/pages/diagnostics/components/SchedulerTab.tsx` |
| Browser WebSocket transport | `client/src/services/websocket.ts` |
