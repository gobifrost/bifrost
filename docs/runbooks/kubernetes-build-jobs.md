# Kubernetes Isolated Build Jobs

Build-class platform jobs can each run in their own Kubernetes pod instead of
on a worker: App deploys (`application.deploy`), App SDK rebuilds
(`application.sdk_update`), Solution deploys (`solution.deploy`), Solution
backup exports (`solution.export`) and Solution Git syncs
(`solution.git_sync`). Isolation is off by default. Without it, these jobs run
on workers, admitted by memory like any platform job, and the worker pool
scales up more often to make room.

## Turning Isolation On And Off

A platform admin turns isolation on in **Settings → Kubernetes → Scaling &
Isolation**, in the **Isolated Jobs** card:

- **Isolate Build Jobs** turns isolation on or off.
- **Max Isolated Pods** (default 2) bounds the isolated pods running at once,
  independently of the worker pool.
- One toggle per job type chooses which types are isolated (all five by
  default), and each type's **max parallel runs** override replaces its code
  default concurrency, for isolated and worker runs alike.

The page appears wherever the API may manage Jobs (the base manifests'
`k8s/api/rbac.yaml`). Nothing needs a restart: a job is placed in a pod or on
a worker when it is enqueued, from the settings at that moment, and the build
controller reads Max Isolated Pods and the on/off switch on every pass,
within seconds.

Turning isolation off stops admitting new pods only. Pods already running
finish and are reconciled until they end; queued isolated jobs not yet placed
in a pod move to the local backend and run on a worker. Keep the API's Job
Role while placed pods exist: the controller needs it to observe and clean
them up.

There is no silent fallback while isolation is on: if the controller cannot
create or observe a Job, the work stays owned by the isolated path and fails
visibly (for example `capacity_unavailable` after the pending timeout) rather
than running on a worker.

## Where The Controller Runs

The build-Job controller runs in every process whose service account may
manage Jobs, checked once at start. The base manifests bind the
`bifrost-worker-controller` Role to the API, so every API replica reconciles
isolated Jobs, and reconciliation is safe under any number of replicas. The
loop always runs there; the Settings toggle decides only whether it admits new
work. A single API replica is a single point of control: run at least two.

At start the controller reads the identity its pods run as (below). A
Kubernetes API failure there is retried with backoff (2 seconds, doubling to
a minute); only an identity no pod could run with (no image or environment
known) disables the controller until a restart.

## What An Isolated Pod Runs As

An isolated pod runs exactly as a worker Job does
(`worker_pool.manifest.resolve_worker_job_spec`): it copies the API pod's
namespace, image, image pull policy and `envFrom`, and runs as the
namespace's `default` service account with no token mounted. No image tag or
ConfigMap needs to be configured.

Optional deployment variables override that identity for both worker Jobs and
isolated pods:

| Variable | Default |
| --- | --- |
| `BIFROST_KUBERNETES_JOB_NAMESPACE` | The API pod's namespace |
| `BIFROST_KUBERNETES_JOB_IMAGE` | The API pod's image |
| `BIFROST_KUBERNETES_JOB_CONFIGMAP` and `BIFROST_KUBERNETES_JOB_SECRET` (both) | The API pod's `envFrom` |
| `BIFROST_KUBERNETES_JOB_SERVICE_ACCOUNT` | `default` |
| `BIFROST_KUBERNETES_JOB_CPU_REQUEST` / `_CPU_LIMIT` | `250m` / `1` (isolated pods) |
| `BIFROST_KUBERNETES_JOB_PENDING_TIMEOUT_SECONDS` | `300`: an isolated pod not started by then fails as `capacity_unavailable` |

These replaced the `BIFROST_KUBERNETES_BUILD_*` names. The old names still
work as aliases, and the API logs a startup warning naming each one in use
with its new name; the aliases are removed in the release after next.

Isolated pods never mount a service-account token. Job names and labels carry
only a fingerprint of the attempt's lease token; the token itself travels in
the pod's startup command, which the runner needs to report progress and
completion. Payloads, source archives and credentials never go in Job
manifests: the pod reads its inputs through Bifrost's storage with the
copied environment.

## Pod Memory: The Worker Sizing Rule

Isolated pods are sized by the same code as worker Jobs
(`api/src/services/worker_pool/sizing.py`), from Settings → Kubernetes:

- **Standard pod:** a memory request of `BIFROST_POOL_WORKER_MEMORY_REQUEST_MIB`
  (512 by default) and a limit of **Worker Memory** (2048 MiB by default), so
  it is Burstable.
- **Sized pod:** when a job's memory requirement exceeds a standard pod's
  budget (its limit at the 85% memory threshold, less the process baseline),
  the pod gets the smallest limit whose budget holds the requirement, up to
  **Largest Worker**, and requests that whole limit, so it is scheduled only
  where the memory exists.
- **Too large:** while isolation is on, a requirement no pod up to Largest
  Worker can hold fails visibly instead of waiting. While it is off, the job
  moves to a worker and is measured against the executors instead.

The claim records the pod's memory request and limit, and the pod is created
with them even if Worker Memory changes before its Job exists.

Every isolated type learns its memory requirement: App deploys and SDK
rebuilds per App, Solution deploys by their content, and Solution exports
and Git syncs per Solution. A pod Kubernetes stops at its memory limit
(OOMKilled) fails with `memory_pressure`, and its profile learns at least the
pod's limit plus a 128 MiB margin, so the next run gets a larger pod. This
holds even when the controller first sees the stopped pod after the runner's
lease expired.

App build memory failures appear in the job error and its notification with
recovery guidance: increase the build container's memory limit, or, for a
non-Git-connected Solution, run `bifrost solution deploy` locally to build and
upload prebuilt output. Independent `bifrost app deploy` still builds on the
server; it does not accept a local prebuilt build. A preventive memory stop is
identified separately from a confirmed OOM, and an unexplained SIGKILL/exit 137
is described as a possible OOM rather than a confirmed one.

## Upgrading From Environment Variables

Earlier releases configured isolation in deployment env. On its first start,
this release imports each Settings value that was never stored from the
matching variable, records where it came from (the page shows "Imported from
… on …"), and stops reading the variable:

| Variable | Becomes |
| --- | --- |
| `BIFROST_PLATFORM_BUILD_BACKEND` | Isolate Build Jobs (`kubernetes` = on, `local` = off) |
| `BIFROST_KUBERNETES_BUILD_JOB_TYPES` | The per-type toggles: the five defaults, narrowed to this list |
| `BIFROST_KUBERNETES_BUILD_MAX_JOBS` | Max Isolated Pods |
| `BIFROST_KUBERNETES_BUILD_MEMORY_REQUEST_MIB` / `_LIMIT_MIB` | Removed: pods use the worker sizing rule |

The worker pool's `BIFROST_POOL_MIN_WORKERS`, `BIFROST_POOL_MAX_WORKERS`,
`BIFROST_POOL_WORKER_MEMORY_LIMIT_MIB`, `BIFROST_POOL_WORKER_MAX_MEMORY_MIB`
and `BIFROST_POOL_IDLE_SECONDS` are imported the same way. Each API start
logs one warning naming every variable that is set but no longer read, and
the Kubernetes Settings page shows the same list ("No longer used; remove
from your environment"). The list disappears once the variables are gone.

After an upgrade:

1. Confirm the imported values on the Settings page and in the startup log.
2. After the rollback window, remove the imported and removed variables from
   the deployment (`bifrost-config`, `bifrost-build-jobs-config`). Rename the
   identity variables you keep to `BIFROST_KUBERNETES_JOB_*`, or delete them
   so Jobs copy the API pod. Until then, a rollback to the previous image
   still finds its env.
3. Confirm the notice is empty.

`GET /api/admin/kubernetes/status` must report `configured: true` from an API
pod: the page and the build controller follow the API's own Job permissions.
If the API cannot manage Jobs, the import still copies
`BIFROST_PLATFORM_BUILD_BACKEND`, and nothing claims isolated jobs, so check
the status after upgrading. Raising `BIFROST_POOL_WORKER_MEMORY_REQUEST_MIB`
above a stored Worker Memory stops Job creation with an error logged on every
pass; Settings rejects the opposite change.

### Rolling Back

Keep the imported variables in the deployment until you no longer need to roll
back: the previous image reads them from env. On the previous image:

- a standard worker Job still `reserved` when the rollback lands starts with
  its whole limit requested (the previous release reads a recorded limit as a
  sized Job);
- the "Kubernetes execution enabled" notice is announced once more;
- saving a job-type toggle rewrites the Settings row without the scaling
  fields; the next upgrade imports them again while the variables are still
  set.

`BIFROST_RABBITMQ_URL` is listed as unused only after a drain reached the
broker and recorded it. Before deleting RabbitMQ, confirm the record exists:
`SELECT value_json FROM system_configs WHERE category = 'runtime' AND key = 'legacy_broker_drained';`

## The Builds Overlay

The optional overlay at `deploy/kubernetes/builds` is not part of the base
`k8s/kustomization.yml`. It does not turn isolation on; that is the Settings
toggle. It installs:

- `bifrost-build-runner`, a ServiceAccount with token automount disabled and
  no RBAC, which every Job the API starts (worker Jobs and isolated pods) runs
  as, through `BIFROST_KUBERNETES_JOB_SERVICE_ACCOUNT`. Unlike the namespace's
  `default` account, it carries nothing an operator or cloud integration
  attached to `default`;
- `bifrost-build-jobs-config`, with that service account and the isolated
  pods' CPU and pending timeout;
- a patch that gives the API that ConfigMap;
- `bifrost-build-controller`, a ServiceAccount with a namespace-scoped Role
  (`batch/jobs`: `get`, `list`, `create`, `patch`, `delete`; `pods`: `get`,
  `list`), and a scheduler patch that runs the scheduler executor as it, so a
  scheduler scaled back up reconciles Jobs too. The base retires the
  scheduler (zero replicas); drop `scheduler-controller-patch.yaml` to keep
  the controllers in the API only.

```bash
kubectl apply -k deploy/kubernetes/builds
```

Do not use `kubectl delete -k deploy/kubernetes/builds` to undo it: the
overlay includes the base Deployments as resources, so deleting the
kustomization deletes the application. Turn isolation off in Settings, let
running pods finish, then remove only the overlay objects you no longer need.

Isolated pods record their namespace. Drain them before changing the
namespace, cluster or controller credentials: a pod placed in another
namespace stops reconciliation until it is accounted for.

## Limits

- Each type's policy decides retries and cancellation. App deploys and SDK
  rebuilds run once (`max_attempts=1`) and cannot be canceled while running;
  Solution deploys, exports and Git syncs may run a second attempt, and a
  running Git sync can be canceled. Cancel works for any job still queued.
- A lost pod fails visibly with a structured code (`capacity_unavailable`,
  `runner_lost`, `runner_exited`, `memory_pressure`, `timeout`); it never
  falls back to a worker.
- If a Job is deleted out of band while its pods still run, the controller
  waits for those pods to stop before it releases the attempt. Its Role can
  only read pods, so remove orphan pods manually.

## Local Kind

Isolation in local Kind is turned on in Settings, as anywhere. To also apply
the overlay's Job settings (Jobs run as `bifrost-build-runner`), boot with:

```bash
BIFROST_KIND_BUILD_JOBS=1 ./test.sh kubernetes up
```

The build experiment turns isolation on for its run and restores it.
`--restart-controller` deletes one API pod while builds run (the workload runs
in the other):

```bash
./test.sh kubernetes build-experiment --output /tmp/bifrost-build-jobs --restart-controller
```

The pool drills cover sizing and the Settings toggles:

```bash
./test.sh kubernetes pool-drills --only sized_isolated_build max_workers_setting isolation_off
```

- `sized_isolated_build`: an App deploy whose learned requirement (2.5 GiB)
  exceeds a standard pod's budget gets a pod whose memory request equals its
  limit, above 2 GiB, and succeeds.
- `max_workers_setting`: a Max Workers change in Settings applies to the pool
  without an API restart.
- `isolation_off`: turning isolation off lets the running isolated build
  finish in its pod and moves the queued one to a worker.

## Earlier Local Evidence

Before isolation moved to Settings, a local Kind run validated the App build
path:

```bash
./test.sh kubernetes build-experiment --output /tmp/bifrost-769-build-live-corrected --restart-scheduler
```

It passed two real App deploys in 1 GiB Kubernetes build pods while a
lightweight `artifact.retention_cleanup` job ran locally and the scheduler pod
was replaced; the SDK rebuild then published a new active deployment.
Observed peak kernel cgroup memory was 614.25 MiB for a build pod and
408.14 MiB for the scheduler pod (kernel cache included; the fixture adds a
synthetic 30-second build delay to make overlap deterministic). This is
capacity evidence for that fixture, not a latency benchmark.
