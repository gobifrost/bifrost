# Kubernetes settings: scaling and isolation in Settings, one sizing rule

Design, 2026-10-24. Approved by Jack in the #1052 session after Fable's
review:
- queued isolated jobs move to a worker when isolation is turned off;
- there is no separate isolated-pod memory setting (pods use the worker
  sizing rule);
- build order is the agent prompt block first, then this.

## Why

Two kinds of Kubernetes Job run Bifrost work, and their configuration has
drifted.

- **Worker Jobs** (#769) are reusable executors. The API starts them as work
  arrives, within `BIFROST_POOL_MIN_WORKERS` / `BIFROST_POOL_MAX_WORKERS`.
- **Isolated Jobs** (#781) run one build-class platform job in its own pod:
  App deploy, SDK update, Solution deploy, export and git sync.

Isolation is an option, independent of scaling. Without it, these jobs run on
workers, and the pool scales up more often to make room.

The drift:

1. **Two lists choose which jobs run isolated.**
   - The env allowlist `BIFROST_KUBERNETES_BUILD_JOB_TYPES`.
   - The per-type toggles in Settings → Kubernetes Executions.
   - Both default to the same five types, kept in two constants that tests
     pin together. A job is isolated only when both lists include it.
2. **Isolated pods have a fixed size.**
   - Every isolated pod gets `BIFROST_KUBERNETES_BUILD_MEMORY_LIMIT_MIB`
     (2 GiB).
   - A job whose learned requirement exceeds that waits as "Waiting for
     Kubernetes capacity" forever.
   - The sweep that fails work too large for any executor
     (`unfit.fail_platform_jobs_that_never_fit`) covers only local jobs.
3. **"Build" names configure worker Jobs too.**
   - `BIFROST_KUBERNETES_BUILD_IMAGE`, `_CONFIGMAP`, `_SECRET` and
     `_SERVICE_ACCOUNT` also configure worker Jobs
     (`worker_pool.manifest.resolve_worker_job_spec`).
   - In production, worker Jobs run as `bifrost-build-runner`.
4. **Policy lives in env.** Scaling and isolation are runtime policy an admin
   should be able to see and change, but today they are env variables.
5. **Retired variables are silently ignored.** That covers v1.4.1's
   `BIFROST_RABBITMQ_URL`, `BIFROST_MAX_CONCURRENCY` (the compose default was
   10), `BIFROST_MAX_WORKERS` and `BIFROST_MAX_SERVICE_WORKERS`, because
   `Settings` uses `extra="ignore"`.

Most installs run dev images, so they have the post-v1.4.1 variables set.
The migration must be transparent and must break nothing.

## Rules

### Where each setting lives

**Settings → Kubernetes.** Runtime policy, read by the controllers every pass,
so a change applies without a restart. A worker Job records the memory limit
it was launched with, so a Worker memory change sizes new Jobs only.

| Setting | Replaces | Default |
| --- | --- | --- |
| Warm workers | `BIFROST_POOL_MIN_WORKERS` | 1 |
| Max workers | `BIFROST_POOL_MAX_WORKERS` | 6 |
| Worker memory (MiB) | `BIFROST_POOL_WORKER_MEMORY_LIMIT_MIB` | 2048 |
| Largest worker (MiB) | `BIFROST_POOL_WORKER_MAX_MEMORY_MIB` | 8192 |
| Idle time (seconds) | `BIFROST_POOL_IDLE_SECONDS` | 300 |
| Isolated jobs: on/off | `BIFROST_PLATFORM_BUILD_BACKEND` (`kubernetes` = on) | off |
| Isolated jobs: which types | the existing toggles (unchanged) | the five build types |
| Isolated jobs: per-type concurrency | the existing overrides (unchanged) | code policy |
| Max isolated pods | `BIFROST_KUBERNETES_BUILD_MAX_JOBS` | 2 |

**Deployment (env).** Identity and rarely changed tuning. A Settings page
must not choose what image or service account Bifrost runs as.

| Env | Notes |
| --- | --- |
| `BIFROST_KUBERNETES_JOB_NAMESPACE` / `_IMAGE` / `_CONFIGMAP` / `_SECRET` / `_SERVICE_ACCOUNT` | Renamed from `BIFROST_KUBERNETES_BUILD_*`; old names are aliases with a startup warning. Usually unset: Jobs copy the API pod. |
| `BIFROST_POOL_WORKER_MEMORY_REQUEST_MIB`, `_CPU_REQUEST`, `_CPU_LIMIT`, `BIFROST_POOL_MAX_LIFETIME_SECONDS`, `BIFROST_POOL_START_TIMEOUT_SECONDS` | Unchanged. |
| `BIFROST_KUBERNETES_BUILD_CPU_REQUEST` / `_CPU_LIMIT` / `_PENDING_TIMEOUT_SECONDS` | Renamed to `BIFROST_KUBERNETES_JOB_*`, aliased. |

**Removed:**
- `BIFROST_KUBERNETES_BUILD_JOB_TYPES`. The toggles are the one list.
- `BIFROST_KUBERNETES_BUILD_MEMORY_REQUEST_MIB` and `_LIMIT_MIB`. Isolated
  pods are sized exactly like worker Jobs (below). Production and the
  shipped overlays set them to the worker values (512 / 2048), so nothing
  changes there.

### One-time import (transparent migration)

On start, before the controllers run, each Settings field that has never
been stored takes the matching env value, if one is set, and records
`imported_from_env: <variable>` with the date.

- **Toggles:** the five default types, intersected with
  `BIFROST_KUBERNETES_BUILD_JOB_TYPES` when that is set. A narrowed list
  stays narrowed.
- **Several API replicas:** the import runs under its own blocking advisory
  lock, in the control host's start before `start_kubernetes_loops` (and in
  the scheduler container's executor, which hosts the same loops under the
  builds overlay). "Never stored" is checked inside the lock, so no
  controller runs a pass on defaults first. The API also runs it before it
  serves requests, so a build enqueued right after an upgrade never reads
  the defaults.
- **Consistent pairs:** the import keeps what the variables did. Worker
  memory above Largest worker raises Largest worker to it (the controller
  used the larger of the two), and Warm workers above Max workers are
  lowered to Max (it never launched more). A Worker memory below
  `BIFROST_POOL_WORKER_MEMORY_REQUEST_MIB`, or a value that conflicts with
  one an admin stored, is skipped with a warning. Settings likewise rejects
  Worker memory below that request: no worker could be created.
- **Rolling upgrade:** old replicas keep reading env, and new ones read the
  imported values, which are the same. Settings edits made during the
  rollout are not honoured by old replicas until they are replaced.
- **Provenance:** `imported_from_env` clears when an admin edits the field.

After the import, those variables are not read. Every install behaves the
same on day one, and the Settings page shows where each value came from.

### Leftovers stay visible until removed

At startup, the API (not worker Jobs) logs one warning naming every
variable that is set but not read. These are the imported policy variables above, the removed ones,
and v1.4.1's retired variables.

The Kubernetes Settings page shows the same list as a notice: "No longer
used; remove from your environment: …". This is computed from the process
environment, so it disappears once the variables are gone. Docker installs
see the startup warning only, since the page is Kubernetes-only.

`BIFROST_RABBITMQ_URL` is still read today: each API start uses it to move
runs an earlier release left in RabbitMQ into PostgreSQL. The drain records
when it finishes on this release; from then on it no longer connects, and
the variable is unread like any other (Jack, 2026-10-24). Replicas drain
one at a time under an advisory lock, so none records the drain while
another still holds a message. A rollback to a release that queues to
RabbitMQ again needs that record cleared.

### Aliases

Each renamed variable reads the new name first, then the old one. When the
old name supplies a value, the API logs a startup warning to rename it. The
aliases are removed in the release after next.

### One sizing rule for isolated pods (the worker rule)

An isolated pod is sized like a worker Job, by the same code.

- **Standard pod:** request `BIFROST_POOL_WORKER_MEMORY_REQUEST_MIB` and limit
  *Worker memory* (Burstable, as standard workers are today).
- **Sized pod:** when the job's requirement exceeds a standard pod's budget,
  the limit is `memory_requests.sized_job_limit_bytes(requirement,
  threshold, baseline, Largest worker)` and the request equals the limit, as
  sized worker Jobs do. This accounts for the memory threshold and the
  process baseline, so the runner's own memory guard does not stop the job
  short of its requirement.
- **Too large:** a requirement no pod up to *Largest worker* can hold fails
  visibly. `fail_platform_jobs_that_never_fit` gets a second query for the
  Kubernetes backend, compared against the largest *pod* budget (not
  `largest_budget`, which is executor budgets). The "Waiting for Kubernetes
  capacity" wait on `remote_memory_bytes` is removed.
- **Learning:** every isolated job type gets a memory profile key (today
  only Solution jobs have one, so App deploys, SDK updates, exports and git
  sync learn nothing). An OOM-killed pod's profile learns at least the
  pod's limit plus the margin, so the next attempt is sized larger.

### Isolated Jobs copy the API pod (new work)

Today only worker Jobs copy the API pod (`resolve_worker_job_spec`).
Isolated Jobs hard-require all five identity variables, in
`validate_build_settings` (the build loop disables itself without them),
in `build_job_manifest`, and in `claim_platform_job`, which raises when the
namespace is unset. Change: the build controller resolves the same
`WorkerJobSpec` at loop start, and `build_job_manifest` takes it. The claim
stamps the client's namespace. Identity variables then become optional for
both kinds of Job.

### Loops always run; "off" stops admitting only

`start_kubernetes_loops` starts the build loop wherever the API may manage
Jobs, not only when the env flag is set. Each pass reads *Isolated jobs*:
- **When off:** no new job is placed in Kubernetes, but rows already placed
  there keep being reconciled until they end.
- **Queued rows not yet started when isolation is turned off** move to the
  local backend and run on a worker, admitted by memory like any local
  platform job (Jack, 2026-10-24).

The enqueue fallback that guesses the default types on a database error
(`decide_execution_backend`) is removed: an enqueue already needs the
database.

### Where the page appears

Settings → Kubernetes appears wherever the API may manage Jobs
(`kubernetes_control.may_manage_jobs`, cached per process, because it makes
five access reviews). `/api/admin/kubernetes/status` reports `configured`
from that check. The "Heavy builds now run in Kubernetes pods" detection
notice (`announce_detection_if_transitioned`) is deleted. Every base
install already binds the Role, so it would announce isolation that is
off, and on/off is now the admin's own toggle.

### Workers read pool policy from the database

Worker processes read *Warm workers* and *Idle time* from the Settings row
when they decide to idle-drain, instead of the env the controller injects
into their Job (`pool_main` today). So lowering *Warm workers* applies to
running workers too. The injected `BIFROST_POOL_*` variables are removed
from the worker Job manifest.

## Production migration

1. Deploy. The import copies min 1, max 6, worker memory 2048, isolated jobs
   on, the five types and max isolated pods 2. A build an older replica
   claims during the rollout has no recorded memory request; the controller
   treats its request as its limit, as the migration does.
2. Confirm on the Settings page and in the startup log: one "Imported
   Kubernetes settings from the environment" line (from whichever replica won
   the lock), the unread-variables warning (`BIFROST_POOL_*`,
   `BIFROST_PLATFORM_BUILD_BACKEND`, `BIFROST_KUBERNETES_BUILD_JOB_TYPES`,
   `_MAX_JOBS`, `_MEMORY_*`) and the renamed-variables warning for the
   `BUILD_*` identity names. `GET /api/admin/kubernetes/status` from an API
   pod reports `configured: true`. Confirm the RabbitMQ drain record exists
   before RabbitMQ is deleted.
3. Run prod_smoke.
4. After a soak (the rollback window), remove the imported variables from
   GitOps (`bifrost-config`, `bifrost-build-jobs-config`). Rename the
   identity variables, or delete them so Jobs copy the API pod. Until then,
   a rollback to the previous image still finds its env and keeps running
   isolated jobs. Then confirm the notice is empty.

## Also removed or updated (no orphans)

- `kubernetes_remote_job_types`, `DEFAULT_KUBERNETES_JOB_TYPES` and their
  pinning tests.
- `allowed_by_deployment` (`KubernetesExecutionJobType`),
  `KubernetesStatus.backend`, the client service and types, and the access
  list if routes change.
- The detection-notice machinery (`KUBERNETES_NOTICE_KEY`, `_detection_*`).
- The variables the shipped overlays and templates still set:
  `deploy/kubernetes/builds/build-config.yaml`, `k8s/local/build-jobs.yaml`
  and `docker-compose.debug.yml`. A fresh install must not boot into the
  "no longer used" notice.

## Tests

- Import: each field; never overwrites a stored value; a narrowed allowlist
  stays narrowed; two replicas importing at once.
- Unread-variable report and aliases.
- Controllers read Settings each pass.
- Isolated pod sizing through the worker rule: standard, sized, too large
  fails visibly, an OOM kill learns (every isolated type has a profile).
- Isolated Jobs with no identity variables copy the API pod.
- Turning isolation off stops admitting but finishes running pods.
- Lowering Warm workers in Settings applies to running workers.
- Page visibility gate.
- Kind drill: an isolated build whose requirement rises past 2 GiB gets a
  larger pod. Change Max workers in Settings and the pool follows without a
  restart.

## Out of scope

- The agent prompt block for release notes
  (`2026-10-24-release-note-agent-prompt.md`).
- Dropping `slots` / `job_slots` (#1053).
