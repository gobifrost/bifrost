# Kubernetes Worker Deployment

A Kubernetes worker Job is a **physical launcher**, not the durable execution
owner. Its retained `python -m src.worker.main` entrypoint starts the
lightweight standard-library supervisor and preserves the worker readiness
probe. The retained `python -m src.scheduler.main` entrypoint does the same
while preserving the scheduler heartbeat and readiness probes. Both connect to
the API's remote-control WebSocket through `BIFROST_API_URL`; shipped manifests
provide the internal default `http://api:8000`, and that internal connectivity
is required for a launcher to become ready.

The API owns the canonical `PoolWorker` control for each authenticated launcher.
It claims workflows, services, and local PlatformJobs and sends the physical
launcher only the process operations it must perform. Durable workflow claims,
service leases, and PlatformJob leases stay in PostgreSQL. Child SDK code uses
scoped API HTTP tokens; it never receives the launcher-control socket. The
launcher authenticates its enrollment hello with an HMAC made from the existing
platform `BIFROST_SECRET_KEY`. Kubernetes Job and Pod UIDs are supplied
automatically when the Job starts, so there is no separate enrollment setup for
operators.

## Current Implementation

- **On-demand worker Jobs.** The API's worker controller keeps **Warm Workers**
  (default 1) launchers warm and starts more, up to **Max Workers** (default
  6), when accepted workflow, service, or PlatformJob work exceeds free
  physical-launcher capacity. Idle Job workers drain back to the warm minimum.
  The controller does not add agent capacity.
- **API agents.** One `ApiAgentExecutor` runs per API process. It accepts only
  agent runs and summaries from that API process's memory budget. Scale API
  replicas and API memory independently for agent demand; worker Jobs do not
  provide overflow capacity for agents.
- **Policy lives in Settings.** Warm Workers, Max Workers, Worker Memory,
  Largest Worker and Idle Time are set in Settings → Kubernetes → Scaling &
  Isolation. The controller reads them every pass and launchers read Warm
  Workers and Idle Time when they decide to drain, so a change applies without
  a restart; a Worker Memory change sizes new Jobs only. An upgraded install
  imports them once from the `BIFROST_POOL_*` variables earlier releases read,
  then lists those variables as no longer used (see
  [Upgrading From Environment
  Variables](kubernetes-build-jobs.md#upgrading-from-environment-variables)).
- **Job identity.** Without the optional `BIFROST_KUBERNETES_JOB_*` variables,
  a worker Job copies the controller pod's namespace, image, and `envFrom`,
  runs with the `default` service account and no token, and enrolls its Job/Pod
  UID automatically with the API.
- **Legacy controller Role.** The API normally owns Kubernetes control through
  the base manifests' Role. Existing installations that bound that controller
  Role only to a scheduler or worker remain supported: if the launcher has an
  in-cluster service-account token, its token-gated compatibility helper starts
  the same canonical loops after its authorization check. Tokenless launchers
  do not start the helper.
- **Rolling API handoff.** The physical launcher keeps children alive when its
  API WebSocket closes during an API rollout. A successor API replica
  authenticates and adopts its inventory; durable leases fence stale owners.
  A connected launcher follows the normal drain path instead.

### Upgrading and rolling back

Upgrading from an earlier release works without a manifest change: the
existing Deployments keep their commands and probes, and work runs on them.
Applying the new base manifests is recommended: it lets the API start
on-demand worker Jobs (`k8s/api/rbac.yaml`) and retires the static executors
(below). Then remove what earlier releases
left behind (see [Removing RabbitMQ and KEDA after
upgrading](#removing-rabbitmq-and-keda-after-upgrading)).

Roll back only to a release that includes #1034, otherwise older pods
crash-loop against the newer schema. A release's init step skips migrations
when the database is newer than its image, from the release that retired
RabbitMQ onward. Rolling back further than that across a migration, first
downgrade the database with the newer image, to the newest revision the
older image ships, immediately before applying the older release (the newer
pods expect the newer schema until they are replaced):

```bash
# Kubernetes: from a pod still running the newer image
kubectl -n bifrost exec deploy/bifrost-api -- alembic downgrade <older-head>
# Docker Compose: with the newer image still in API_IMAGE / the compose file
docker compose run --rm --no-deps --entrypoint alembic init downgrade <older-head>
```

After a rollback to a release without the worker controller, remove the warm
worker Jobs it leaves (they also drain themselves within
`BIFROST_POOL_MAX_LIFETIME_SECONDS`):

```bash
kubectl -n bifrost delete jobs -l app.kubernetes.io/name=bifrost-pool-worker
```

The base manifests retire the static executors: `k8s/worker/deployment.yaml`
and `k8s/scheduler/deployment.yaml` set `replicas: 0`, so applying them stops
pods an earlier release left running, and the warm worker Job takes over.
Delete both Deployments after upgrading:

```bash
kubectl -n bifrost delete deployment bifrost-worker bifrost-scheduler
```

To keep a fixed executor anyway (for example, an install whose API may not
manage Jobs), scale one back up; it claims work like any worker Job, but a
whole-container OOM kill restarts it without teaching the workflow a larger
request (see [How executors use their memory](#how-executors-use-their-memory)).

## Removing RabbitMQ and KEDA after upgrading

Bifrost no longer uses RabbitMQ or KEDA. The base manifests no longer ship the
`rabbitmq` Deployment and Service or the RabbitMQ secret keys. Applying the new
manifests does not delete what an earlier release created; after every Bifrost
pod runs this release, an operator may remove the leftovers.

Keep the broker and `BIFROST_RABBITMQ_URL` until the queues are empty. Each
API pod, when it starts with `BIFROST_RABBITMQ_URL` set, tries the broker a few
times, moves every run an earlier release left queued into PostgreSQL until the
queues have stayed empty for two minutes (covering work an older pod queues
during the rolling upgrade), logs `Drained work queued by an earlier release`,
and disconnects; it does not poll again.

A drain that left nothing queued is recorded in the database
(`system_configs`, category `runtime`, key `legacy_broker_drained`). From then
on no API start connects to the broker, and each start lists
`BIFROST_RABBITMQ_URL` among the environment variables no longer used: once
the API has logged the drain, the variable can be removed. Check the queues
once the rollout finishes; if any still hold messages (a rollout that took
longer), delete the record and restart the API Deployment to drain them:

```bash
kubectl -n bifrost exec deploy/rabbitmq -- rabbitmqctl list_queues name messages
# In PostgreSQL:
#   DELETE FROM system_configs WHERE category = 'runtime' AND key = 'legacy_broker_drained';
kubectl -n bifrost rollout restart deployment bifrost-api
```

A rollback to a release that queues to RabbitMQ needs that record deleted as
well, so the next upgrade drains what the rolled-back release queued.

A recovered run runs for the user the earlier release recorded, as that
release would have run it. A run whose context expired in Redis (after an
hour) is marked failed.

Remove any KEDA `ScaledObject` and `TriggerAuthentication` that target
`bifrost-worker`. Left in place, a `ScaledObject` keeps owning the worker
Deployment's replica count through its HPA and scales it on queues that no
longer receive messages:

```bash
kubectl -n bifrost delete scaledobject bifrost-worker --ignore-not-found
kubectl -n bifrost delete triggerauthentication bifrost-rabbitmq --ignore-not-found
```

Deleting the `ScaledObject` also deletes the HPA it owns. The `bifrost-worker`
Deployment itself is retired: the base sets it to zero replicas, and once its
pods are gone you delete it with `bifrost-scheduler` (see the Kubernetes
README's upgrade step). If KEDA served nothing else in the cluster, uninstall
it the way it was installed.

After that drain nothing reads RabbitMQ's queues: `workflow-executions`,
`agent-runs`, `agent-summarization`, `agent-summarization-backfill`,
`agent-tuning-chat`, the `package-installations` exchange, and the `-poison`
dead-letter queues (with their `-dlx` exchanges). Before deleting the broker, an operator who
cares about dead-lettered messages can inspect them with `rabbitmqadmin` or the
management UI. There is nothing to replay automatically: a dead-lettered run
was already recorded as failed, or it was never accepted durably. Earlier
releases still connect to the broker, so delete it only once you no longer
plan to roll back to one.

Then delete the broker and its secret keys:

```bash
kubectl -n bifrost delete deployment,service -l app.kubernetes.io/name=rabbitmq
kubectl -n bifrost delete pvc rabbitmq-data --ignore-not-found
kubectl -n bifrost patch secret bifrost-secrets --type=json -p='[
  {"op": "remove", "path": "/data/BIFROST_RABBITMQ_URL"},
  {"op": "remove", "path": "/data/BIFROST_RABBITMQ_PASSWORD"}
]'
```

The shipped broker used `emptyDir` storage; the PVC exists only if it was
switched to the `rabbitmq-data` claim its manifest suggested. The JSON patch
fails if either key is already absent; remove only the keys present. Restart
the Bifrost Deployments if their pods should stop carrying the removed
variables.

## Scale-Down And Drain

On-demand worker Jobs drain themselves after **Idle Time** (Settings →
Kubernetes) without work or `BIFROST_POOL_MAX_LIFETIME_SECONDS` of life, and
the controller keeps at least **Warm Workers** warm. Lowering Warm Workers lets
running idle launchers drain too. A static launcher an install kept retains its
configured replica count.

After an upgrade, the controller replaces worker Jobs running a build that no
live controller runs, once every controller of that build has stopped: it
launches current Jobs for the warm minimum, and when they can accept work, the
outdated Jobs drain (`end_reason = rotated`). They are never deleted. If a full
pool cannot launch a replacement, outdated ready standard Jobs above Warm
Workers can drain first to free a slot. Workers count toward the warm minimum
only after reporting their memory budget. The controller waits when a current
replacement is already starting, and never drains below Warm Workers or exceeds
Max Workers. A full pool exactly at its warm minimum still waits for ordinary
retirement. Draining Jobs continue to count toward Max Workers until their
children finish.

A drain the platform asks for (idle, rotated, maximum lifetime) stops new
workflow, service, and PlatformJob dispatch to that launcher and lets its
physical children finish under their own timeout and lease rules. A run without
a timeout keeps its drained Job (and its place under Max Workers) until it ends,
and the API logs a warning once such a drain passes the maximum lifetime.

API rollout drain is distinct. An API process drains its `ApiAgentExecutor` for
up to 300 seconds and its supported deployment gives it a 310-second shutdown
budget. A launcher whose WebSocket is closed during that lifecycle remains
alive for a successor API replica to adopt; it is not treated as a request to
kill its children. Keep any launcher pod grace period above the time its own
running children require:

```yaml
terminationGracePeriodSeconds: 360
```

## Security And RBAC

A physical launcher does not need Kubernetes API access for ordinary workflow,
service, or PlatformJob execution. Use the current pod hardening pattern:

```yaml
spec:
  automountServiceAccountToken: false
  securityContext:
    runAsUser: 1000
    runAsGroup: 1000
    fsGroup: 1000
  containers:
    - name: worker
      securityContext:
        allowPrivilegeEscalation: false
        runAsNonRoot: true
        capabilities:
          drop: ["ALL"]
        seccompProfile:
          type: RuntimeDefault
        readOnlyRootFilesystem: true
      volumeMounts:
        - name: home
          mountPath: /home/bifrost
        - name: tmp
          mountPath: /tmp
  volumes:
    - name: home
      emptyDir: {}
    - name: tmp
      emptyDir: {}
```

The API normally manages Kubernetes resources: `k8s/api/rbac.yaml` binds the
namespace-scoped `bifrost-worker-controller` Role to the API ServiceAccount so
it can create, start, and remove worker Jobs and isolated build Jobs and read
their pods. Those Jobs run with the `default` ServiceAccount (or
`BIFROST_KUBERNETES_JOB_SERVICE_ACCOUNT`, which the
`deploy/kubernetes/builds` overlay sets to the tokenless, RBAC-free
`bifrost-build-runner`) and normally never mount a token.

An existing scheduler-Role-only deployment remains supported without a manual
RBAC migration. Its scheduler launcher sees the mounted token and starts the
compatibility Kubernetes-control helper only when the Role passes the canonical
authorization check. This preserves its controller behavior while keeping
ordinary launcher templates tokenless and least-privileged.

## Resource Sizing

Recommended defaults:

- **Kubernetes:** physical worker Jobs with a 512Mi request and a 2Gi limit
  (Worker Memory 2048), one warm (Warm Workers 1) and up to six (Max Workers
  6); two API replicas (256Mi request, 1Gi limit) and two client replicas.
  Size the API limit for its own HTTP/control workload plus its separate agent
  capacity.
- **Docker Compose:** the `worker` service with two replicas and a 2 GB limit
  each, one API and one client; give the host 8 GB. The worker and scheduler
  commands remain their established entrypoints but run launchers.

The 2026-10-24 layout measurement of roughly 430 MiB per API pod, 5–7 MiB per
client pod, and 620 MiB for a warm worker Job is a historical baseline, not a
capacity guarantee for this runtime. Customer workflow code, installed
packages, provider SDKs, large payloads, concurrent runs, and agent load add
to the relevant physical-worker or API budget. Raise Worker Memory (or Compose
limits) for workflows that need more, and size API memory separately for agent
work. A workflow that no standard worker can hold gets a worker Job sized for
it, up to Largest Worker. Isolated build Jobs follow the same sizing rule
(`docs/runbooks/kubernetes-build-jobs.md`).

### How physical launchers use their memory

The API-side `PoolWorker` admits workflow, service, and PlatformJob work using
the physical launcher's cgroup telemetry
(`docs/plans/2026-10-23-769-stage6-resource-requests.md`): its budget is 85%
of the launcher's container limit (`memory_pressure_threshold`) minus what that
launcher reports as in use, and work claims from it by its memory request. At
95% of the physical container's real memory, the control plane stops the run or
service that grew most; the stopped run teaches its workflow a larger request.
The API agent executor uses the API process's measured memory and does not draw
from this worker budget.

**Whole-container OOM on Kubernetes.** On cgroup v2, Kubernetes sets
`memory.oom.group` on containers, so when the kernel's OOM killer acts it kills
every process in the launcher container, not only the run that grew (Docker
kills only that run). Its API-owned claims then expire or reconcile as lost.
When it was a Job worker, the controller sees the pod OOMKilled and those runs'
workflows learn the launcher's budget, so their next run fits a launcher alone.
Clusters on Kubernetes 1.32 or later can set the kubelet's
`singleProcessOOMKill: true` to kill one process instead; Bifrost does not rely
on it. Size the worker limit with headroom for the largest workflow you run.

**Docker.** The Compose template runs two `worker` replicas limited to 2 GB
each, which gives them separate physical budgets. A container without a limit
takes 85% of the host's memory as its budget, so two of them on one host each
believe they have it; only the real-memory check, measured against the host,
keeps them within it. Compose files from earlier releases also have a
`scheduler` service (limited to 1 GB) and an unlimited `worker`. Their commands
remain compatible launcher entrypoints; set real limits on both, or switch to
the current file and run `docker compose up -d --remove-orphans`.

## Dependency Hydration

Current workers install and inspect Python requirements during startup or
recycle using durable requirements state. The recent memory work moved
requirements setup into a short-lived helper so storage/client imports do not
stay resident in the supervisor.

This is still not a complete elastic dependency model:

- ephemeral pods cannot rely on filesystem mutations made inside earlier pods;
- requirements text is not a versioned, immutable artifact contract;
- a pod can only safely accept work after dependency hydration succeeds.

For production elasticity, each pool or Job template needs an image or durable
artifact fingerprint that fully describes the dependency set it can run.

## External Services

The local `k8s/local/` directory is a disposable Kind fixture. It includes
PostgreSQL, PgBouncer, Redis and SeaweedFS with fixture credentials,
`emptyDir` storage and local images. It is not production-ready.

Production deployments need operator-managed services or production-grade
in-cluster equivalents:

- PostgreSQL with backups, HA, maintenance, connection-pool sizing and upgrade
  procedures;
- Redis with the selected persistence/HA posture for Bifrost's cache and result
  paths;
- S3-compatible object storage with durable buckets, credentials rotation and
  lifecycle policy;
- ingress, TLS, WebSocket support and public URL/WebAuthn configuration.

## Draining Operations

For a manual drain:

1. Lower Warm Workers (and, to stop growth, Max Workers) in Settings →
   Kubernetes; workers above them drain once idle.
2. Watch worker logs for graceful drain completion.
3. Verify durable workflow, service, or PlatformJob state rather than only pod
   exit.

Useful checks:

```bash
kubectl get jobs -n bifrost -l app.kubernetes.io/name=bifrost-pool-worker
kubectl get pods -n bifrost -l app.kubernetes.io/name=bifrost-pool-worker
kubectl logs -n bifrost -l app.kubernetes.io/name=bifrost-pool-worker --tail=200
```

The current launcher handoff and drain behavior should be validated in the
target cluster. Abrupt node loss, SIGKILL after a missed deadline, database
outage during result persistence, and OOM termination need durable attempt
reconciliation.

## Remaining Gaps

Physical worker Jobs are the production capacity for workflows, services, and
PlatformJobs. API replicas separately carry agent capacity. Large work is
covered: a workflow no standard worker can hold gets a sized worker Job, and
build-class PlatformJobs can run in isolated pods sized the same way, both
learning from OOM kills. What remains operator-owned or unbuilt:

- dependency hydration is not yet immutable or artifact-fingerprinted for
  zero-replica pools;
- local fixture services are not production storage, cache, or ingress designs;
- node autoscaling, node selectors, tolerations, and priority classes remain
  operator-owned and unproven for this runtime.
