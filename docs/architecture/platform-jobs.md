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
error, retry policy, timestamps, and lease state. Workflow claims, service
attempt leases, and PlatformJob leases remain PostgreSQL records; a physical
runner never turns a control-channel message into a new claim.

The API owns canonical execution control. During its lifespan, each API replica
starts the remote-control host. When an authenticated physical launcher
connects, that host constructs one canonical `PoolWorker` control instance for
that launcher. It claims and reconciles workflows, supervised services, and
local PlatformJobs, retaining the existing PostgreSQL fencing, result,
cancellation, timeout, and memory-admission paths. The physical launcher is a
small standard-library supervisor: it owns its process templates and child
processes, reports their inventory and cgroup telemetry, and carries out
commands from the API. It does not own ORM state or durable claim loops.

`ApiAgentExecutor` is separate. Each API process has one instance, admitted
from that API process's measured memory, and it claims only `agent.run`,
`agent.summarize`, and `agent.summarize_backfill`. It never starts a workflow,
service, PlatformJob, Kubernetes-control, or package loop. Agent capacity is
therefore an API capacity domain; workflow, service, and PlatformJob capacity
belongs to the connected physical launcher that will execute them. Adding or
warming physical workers does not add agent capacity.

The supported `python -m src.worker.main` and `python -m src.scheduler.main`
commands are retained. They now start the lightweight launcher while preserving
their established readiness, liveness, and scheduler-heartbeat behavior. A
launcher needs internal API WebSocket connectivity through `BIFROST_API_URL`
(the shipped manifests set `http://api:8000`) and authenticates its hello with
an HMAC made from the platform `BIFROST_SECRET_KEY`. This is the control
connection between a physical launcher and the API; SDK code uses its normal
scoped API HTTP tokens and never receives a worker-control socket.

The remote-control host authenticates and fences launcher enrollment, then
reconciles the launcher's inventory against durable rows before treating a
child as live. A Kubernetes Job and Pod supply their UIDs automatically at
enrollment; operators do not configure a separate enrollment system. A fenced
API owner performs all durable callbacks. On a rolling API handoff, a launcher
keeps its children alive while a successor API replica authenticates and adopts
its inventory. Losing a WebSocket therefore does not itself fail or kill
children. A connected launcher can instead be asked to drain through the
canonical control path.

One API replica also holds the fenced `scheduler-triggers` database lease. Only
that trigger leader runs APScheduler. On-demand work is persisted directly to
PostgreSQL and does not depend on Redis delivery. The lease uses database-time
expiry rather than a session advisory lock because supported deployments use
transaction-mode PgBouncer. A follower takes over after expiry when the leader
stops renewing; trigger services stop before a healthy leader voluntarily
releases its lease.

Leader failover recreates APScheduler's in-memory schedules. Existing callbacks
configured to run immediately at scheduler startup therefore have at-least-once
failover behavior. Long-running callbacks must migrate by making the elected
callback enqueue a deduplicated platform job. Small, idempotent housekeeping may
remain leader-only scheduler work.

The elected leader retains only short trigger and housekeeping callbacks:
workflow schedule processing, deferred execution promotion, stuck execution and
stuck event-delivery cleanup, metric snapshots, knowledge-storage metrics,
artifact retention, and diagnostics/backfill reconciliation. Recurring OAuth,
webhook, and Solution update callbacks enqueue singleton high-priority platform
jobs. The daily maintenance callbacks `audit.archive` and `run.retention`
(finished runs and events past the retention window) enqueue deduplicated
platform jobs that take a resource lock, so they never overlap a manual run.
User-initiated long-running work is claimed by an admitted launcher.

### Control host

Trigger leadership and dispatch reconciliation form the control plane
(`api/src/scheduler/control.py`), hosted by every API replica inside its
lifespan, after the schema gate and without delaying HTTP startup. The
remote-control host is also API-lifespan owned. It creates the one API agent
executor for that API process and creates launcher-specific `PoolWorker`
control only after a launcher has authenticated; it does not double-start a
separate agent worker for each launcher.

The API's normal shutdown budget allows active agent work to drain for 300
seconds; supported API deployments give the pod/process 310 seconds. Uvicorn
may close WebSockets before lifespan shutdown begins. In that case the remote
host releases its API ownership fence and lets a successor adopt physical
children instead of interpreting the closed transport as a child failure. A
connected launcher is drained through its canonical worker control before the
fence is released.

The Kubernetes loops (the on-demand worker controller and the build-Job
controller) normally run from the API, which has the base manifests' Role. The
legacy scheduler/worker Role remains supported: when one of those launchers has
an in-cluster service-account token, its token-gated compatibility helper uses
the same canonical Kubernetes loops only if its own authorization check permits
it. A tokenless static launcher does not start that helper. This preserves
existing Role-only installations without granting Kubernetes credentials to
ordinary physical worker processes.

All replicas use the same `scheduler-triggers` lease that older schedulers
used, so during a rolling upgrade an old scheduler and a new API replica cannot
both lead. The schedule catalog (`api/src/scheduler/registry.py`) is the single
source for the jobs a leader installs and for Diagnostics; every callback
re-checks a conservative local lease deadline before it starts, so a leader that
stops renewing stops producing triggers before PostgreSQL can elect a
successor. Scheduled callbacks on one leader share a bounded number of slots so
a startup burst cannot exhaust its database pool. A control loop that fails
inside an API replica releases control on that replica only; HTTP keeps serving
and another replica takes over.

Workflow schedule occurrences survive failover. Each scan covers at least the
processor's one-minute cadence, extended back to the previous successful scan
(capped at ten minutes), and every fired occurrence carries a unique
`events.occurrence_key` (source and UTC instant). A successor fires an
occurrence missed during failover once; overlapping leaders cannot fire it
twice; DST-repeated wall-clock times are distinct instants.

A handler may release its scheduler slot in `waiting` state after dispatching
durable child work. The child tracker completes the parent row later. Summary
backfills and Solution app builds use this path so summary fan-out or an
isolated build process does not occupy scheduler capacity.

### Isolated build execution (Kubernetes)

Platform jobs execute on an admitted physical launcher by default. When a
platform admin turns on Isolate Build Jobs in Settings → Kubernetes → Scaling &
Isolation, build-class jobs whose type is enabled there — independent App
deploys and App SDK updates (`application.deploy` and
`application.sdk_update`), Solution deploys and Git syncs (`solution.deploy`
and `solution.git_sync`), and Solution backup exports (`solution.export`) — are
durably placed on the Kubernetes backend at enqueue time. The row stores that
placement in `execution_backend` so replicas do not disagree after restart or a
Settings change. Turning isolation off stops placing new pods; pods already
running finish, and queued isolated jobs that never got a pod move to the local
backend and run on a launcher.

The build controller watches `execution_backend='kubernetes'` rows and creates
one deterministic `batch/v1` Job for each claimed attempt, at most Max Isolated
Pods at once. Local PlatformJob claims filter to `execution_backend='local'`,
so isolated builds do not occupy physical-launcher capacity; launchers keep
serving maintenance jobs and non-build PlatformJobs while the build pod runs in
its own cgroup.

An isolated pod runs the same launcher entrypoint. It copies the API pod's
namespace, image, and `envFrom` (or the optional `BIFROST_KUBERNETES_JOB_*`
identity) and mounts no service-account token. Its Kubernetes Job and Pod UID
are supplied automatically to the launcher enrollment. It is sized by the
worker rule (`api/src/services/worker_pool/sizing.py`): a standard pod requests
the worker memory request with a limit of Worker Memory; a job whose learned
requirement exceeds that pod's budget gets a sized pod (request = limit, up to
Largest Worker); a larger requirement fails visibly.

Remote launch is fenced by durable Kubernetes identity:

1. the controller claims the row with the same lease-token mechanism used by
   local jobs, recording the pod's memory limit;
2. it persists the target namespace and deterministic Job name before launch;
3. it creates the Job suspended, records the returned Job UID, then unsuspends
   only that UID-bound Job;
4. the launcher presents the expected Kubernetes Job UID and its Pod UID when
   it enrolls with the API; and
5. progress, heartbeats, and terminal completion still use the PlatformJob
   lease token, so stale pods cannot complete a superseded attempt.

Kubernetes is capacity, not authority. PostgreSQL remains the status, retry,
progress, cancellation, and result source of truth. Kubernetes Job retry is
disabled; Bifrost retry policy owns any subsequent attempt. Controller outages,
API failures, or namespace mismatches do not silently fall back to local
execution. Existing remote attempts keep their persisted namespace and fail
closed until the controller can account for the bound Job or pod. Operators
must drain and clean up remote attempts before changing namespace, cluster, or
controller credentials.

Lease-token checks fence stale runners: only the current attempt may update
progress or record a terminal result. A handler may be retried after runner
loss only when its side effects are idempotent. Otherwise set
`retry_on_runner_loss=False` or use a single attempt.

## Capacity and resource policy

`PlatformJobPolicy` is an execution guardrail, not a second container scheduler.
Every job type must declare a timeout, retry behavior, minimum memory headroom,
and admission/hard memory ratios. The API control worker evaluates those values
against the physical launcher's cgroup-v2 working set and memory limit before
and during execution. Deployments therefore need a real physical-launcher
memory limit; without one, memory admission is unavailable.

Size a physical launcher for its host plus the heaviest safe combination of
workflow, service, and PlatformJob work it may admit. Establish a new job's
initial policy from representative data volume: record peak cgroup working set
and wall-clock duration, include operational margin, and exercise low-memory
deferral and timeout behavior in tests. These measurements inform policy and
deployment guidance; they are not per-job CPU or memory reservations.

API agent execution is admitted separately from measured API memory. Do not use
worker count, warm workers, or isolated-build capacity as agent capacity. Size
API replicas and their memory limit for the agent work they are configured to
serve, in addition to HTTP and control-host overhead.

Every isolated (build-class) job type also persists an explainable
`memory_required_bytes` from a learned memory profile: Solution deploys keyed
by a stable fingerprint of their source or prebuilt workload, App deploys and
SDK updates by their App, and Solution exports and Git syncs by their Solution
(`api/src/services/platform_job_memory_profiles.py`). Future runs of the same
workload reuse the observed requirement, and an isolated pod Kubernetes stopped
at its memory limit teaches the profile at least that limit plus the safety
margin, so the next run gets a larger pod. Unknown workloads floor at 512 MiB;
other jobs keep using their static policy floor.

The kernel persists the cgroup working set at job start, the peak observed while
the attempt runs, and the cgroup limit. The difference between start and peak
is shown in Scheduler Diagnostics as a practical sizing sample. It includes the
physical launcher and any other occupied slots, so treat it as an operational
upper-bound signal rather than exact process-tree attribution. Add shared
process-tree/CPU telemetry only when these representative samples prove
insufficient.

Scale physical launchers when queue wait time grows while their admitted slots
are occupied. Increase their container memory when representative jobs are
repeatedly deferred or approach the hard ratio. Scale API replicas and memory
for agent demand independently. Do not add Kubernetes-style resource classes
until observed workloads show that one common physical-worker size cannot safely
serve the registered job types.

Isolated build pods are sized per job by the worker rule above and bounded by
Max Isolated Pods (Settings → Kubernetes), independently of physical-launcher
capacity; their CPU request and limit are deployment env
(`BIFROST_KUBERNETES_JOB_CPU_REQUEST` / `_CPU_LIMIT`). They are not a general
resource-class system for every PlatformJob.

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

- `GET /api/platform-jobs` lists visible jobs with `limit`, `offset`, `status`,
  `active_only`, and `search` filters. The response includes `total`, `limit`,
  and `offset` so history views can paginate without silently truncating jobs;
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
| API agent executor | `api/src/worker/api_agent.py` |
| API remote-control host and launcher-specific `PoolWorker` | `api/src/worker/remote/host.py` |
| Physical launcher and process supervisor | `api/src/worker/remote/launcher.py` |
| Retained worker/scheduler launcher entrypoints | `api/src/worker/main.py`, `api/src/scheduler/main.py` |
| Token-gated legacy Kubernetes controller host | `api/src/scheduler/kubernetes_host.py` |
| Isolated child runner | `api/src/jobs/platform/runner.py` |
| Kubernetes build runner | `api/src/jobs/platform/kubernetes_runner.py` |
| Kubernetes Job client and manifest builder | `api/src/jobs/platform/kubernetes_client.py` |
| Kubernetes build controller | `api/src/jobs/schedulers/kubernetes_jobs.py` |
| Lease recovery, claiming, and resource enforcement | `api/src/jobs/schedulers/platform_jobs.py` |
| Trigger leader election | `api/src/scheduler/leadership.py` |
| Generic status and cancellation API | `api/src/routers/platform_jobs.py` |
| Trigger control host | `api/src/scheduler/control.py` |
| Scheduler run/replica/log diagnostics | `api/src/services/scheduler_diagnostics.py` |
| Diagnostics API | `api/src/routers/scheduler_diagnostics.py` |
| Diagnostics UI | `client/src/pages/diagnostics/components/SchedulerTab.tsx` |
| Browser WebSocket transport | `client/src/services/websocket.ts` |
