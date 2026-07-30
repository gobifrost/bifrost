# Platform jobs

## Decision

`PlatformJob` is Bifrost's canonical system for durable, non-workflow work that
can outlive an HTTP request. New platform operations must extend this system
instead of creating a feature-specific job table, worker, scheduler host,
status endpoint, progress event, or UI polling implementation.

Application publishing (`application.publish`) is the first migrated consumer.
Older systems such as Solution deploy/export jobs and reindexing remain
migration candidates. Their existing implementations are not templates for new
work.

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

The existing singleton scheduler container is the platform-job host; there is
no separate platform-job container. Every two seconds it:

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

The host currently runs one platform job at a time. If shared concurrency or
different resource classes become necessary, evolve the common host and its
policy contract; do not add per-feature worker containers.

Lease-token checks fence stale runners: only the current attempt may update
progress or record a terminal result. A handler may be retried after runner
loss only when its side effects are idempotent. Otherwise set
`retry_on_runner_loss=False` or use a single attempt.

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
   sufficient to run after the request context is gone, but must not contain
   secrets that should not be persisted.
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
| Generic status and cancellation API | `api/src/routers/platform_jobs.py` |
| Scheduler host registration | `api/src/scheduler/main.py` |
| Browser WebSocket transport | `client/src/services/websocket.ts` |
