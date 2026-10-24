# Kubernetes Worker Deployment

Every Bifrost execution container is an **executor**
(`api/src/worker/pool_main.py`): the `bifrost-worker` and `bifrost-scheduler`
Deployments (static executors, through their unchanged entry points) and the
on-demand worker Jobs that the API starts. An executor claims workflow runs,
agent runs, summaries, supervised services and local platform jobs from
PostgreSQL; either Deployment can be scaled or removed. Platform control
(trigger leadership and dispatch reconciliation) runs in the API replicas.

## Current Implementation

- **Static executors.** `python -m src.worker.main` and
  `python -m src.scheduler.main` both start the executor and keep their own
  health files, so existing probes keep passing.
- **On-demand worker Jobs.** The worker controller runs in every process whose
  service account may manage Jobs (the base manifests bind
  `bifrost-worker-controller` to the API in `k8s/api/rbac.yaml`). It keeps
  `BIFROST_POOL_MIN_WORKERS` (default 1) worker Jobs warm and starts more, up
  to `BIFROST_POOL_MAX_WORKERS` (default 6), when accepted work exceeds the
  free capacity of every executor. Idle Job workers drain back to the minimum.
  Without build settings, a worker Job copies the controller pod's namespace,
  image and `envFrom`, and runs with the `default` service account and no
  token.
- Supervised services need an executor: the static executors or the warm Job
  worker.

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
and disconnects; it does not poll again. Check the queues once the rollout
finishes; if any still hold messages (a rollout that took longer), restart the
API Deployment to drain them:

```bash
kubectl -n bifrost exec deploy/rabbitmq -- rabbitmqctl list_queues name messages
kubectl -n bifrost rollout restart deployment bifrost-api
```

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

The `bifrost-worker` and `bifrost-scheduler` Deployments keep the replica
counts you set. On-demand worker Jobs drain themselves after
`BIFROST_POOL_IDLE_SECONDS` without work or `BIFROST_POOL_MAX_LIFETIME_SECONDS`
of life, and the controller keeps at least `BIFROST_POOL_MIN_WORKERS` warm.

After an upgrade, the controller replaces worker Jobs running a build that no
live controller runs, once every controller of that build has stopped: it
launches current Jobs for the warm minimum, and when they can claim, the
outdated Jobs drain (`end_reason = rotated`). They are never deleted. All
outdated Jobs drain in the same pass, so under heavy load capacity dips until
their runs finish, because draining Jobs still count toward
`BIFROST_POOL_MAX_WORKERS`.

A drain the platform asks for (idle, rotated, maximum lifetime) stops claiming
and lets runs and platform jobs finish, each within its own timeout; it has
no deadline of its own. A run without a timeout keeps its drained Job (and its
place under `BIFROST_POOL_MAX_WORKERS`) until it ends, and the API logs a
warning once such a drain passes the maximum lifetime.

On SIGTERM an executor stops claiming, hands its supervised services over, and
lets active runs and platform jobs finish for up to 300 seconds before it
exits, also when SIGTERM arrives during a drain. Keep the pod's grace period
above that:

```yaml
terminationGracePeriodSeconds: 360
```

## Security And RBAC

The current worker does not need Kubernetes API access.

Use the current pod hardening pattern:

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

Only the API manages Kubernetes resources: `k8s/api/rbac.yaml` binds the
namespace-scoped `bifrost-worker-controller` Role to the API ServiceAccount so
it can create, start and remove worker Jobs and read their pods. Worker Jobs run
with the `default` ServiceAccount and no token.

## Resource Sizing

Recommended defaults:

- **Kubernetes:** worker Jobs with a 512Mi request and a 2Gi limit, one warm
  (`BIFROST_POOL_MIN_WORKERS=1`) and up to six (`BIFROST_POOL_MAX_WORKERS=6`);
  two API replicas (256Mi request, 1Gi limit) and two client replicas.
- **Docker Compose:** the `worker` service with two replicas and a 2 GB
  limit each, one API and one client; give the host 8 GB.

Production on 2026-10-24, with this layout, measured about 430 MiB per API
pod, 5–7 MiB per client pod and about 620 MiB for the warm worker Job.
Customer workflow code, installed packages, provider SDKs, large payloads
and concurrent runs add to a worker's memory; raise
`BIFROST_POOL_WORKER_MEMORY_LIMIT_MIB` (or the Compose limits) for workflows
that need more. A workflow that no standard worker can hold gets a worker
Job sized for it, up to `BIFROST_POOL_WORKER_MAX_MEMORY_MIB`.

### How executors use their memory

Every executor admits work by memory (`docs/plans/2026-10-23-769-stage6-resource-requests.md`):
its budget is 85% of its container's limit (`memory_pressure_threshold`)
minus what the executor itself uses, and work claims from it by its memory
request. At 95% of the container's real memory, the executor stops the run
or service that grew most; the stopped run teaches its workflow a larger
request.

**Whole-container OOM on Kubernetes.** On cgroup v2, Kubernetes sets
`memory.oom.group` on containers, so when the kernel's OOM killer acts it
kills every process in the container, the executor included, not only the
run that grew (Docker kills only that run). The executor's runs then fail
as lost. When it was a Job worker, the controller sees the pod OOMKilled and
those runs' workflows learn the executor's budget, so their next run fits an
executor alone; a static executor (a Deployment pod) restarts without that
lesson. Clusters on Kubernetes 1.32 or later can set the
kubelet's `singleProcessOOMKill: true` to kill one process instead; Bifrost
does not rely on it. Size the worker limit with headroom for the largest
workflow you run.

**Docker.** The Compose template runs two `worker` replicas limited to 2 GB
each, which gives them separate budgets. A container without a limit takes
85% of the host's memory as its budget, so two of them on one host each
believe they have it; only the real-memory check, measured against the host,
keeps them within it. Compose files from earlier releases also have a
`scheduler` service (limited to 1 GB) and an unlimited `worker`. The
`scheduler` runs the same executor, so an unchanged file keeps working; set a
2 GB limit on both, or switch to the new file and run
`docker compose up -d --remove-orphans` to retire the `scheduler` container.

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

For manual drain:

1. Reduce replicas gradually.
2. Watch worker logs for graceful drain completion.
3. Verify durable workflow or agent terminal state rather than only pod exit.

Useful checks:

```bash
kubectl get pods -n bifrost -l app.kubernetes.io/name=bifrost-worker
kubectl logs -n bifrost -l app.kubernetes.io/name=bifrost-worker --tail=200
kubectl get jobs -n bifrost -l app.kubernetes.io/name=bifrost-pool-worker
```

Graceful SIGTERM and deadline behavior were proven in local Kind. Abrupt node
loss, SIGKILL after a missed deadline, database outage during result
persistence, and OOM termination still need durable attempt reconciliation.

## Missing Production Blockers

The current implementation is not the user's complete dynamic high-memory build
solution. The concrete blockers are:

- dynamic high-memory build pods are not implemented;
- PlatformJob execution still runs under scheduler ownership rather than a
  remote Kubernetes Job attempt protocol;
- there is no settings surface for runtime policy or generation transitions;
- there is no one-shot `run-one` worker path with a fixed high-memory queue and
  pod template;
- OOM/pending-capacity attribution is not wired into durable job results;
- dependency hydration is not yet immutable or artifact-fingerprinted for
  zero-replica pools;
- local fixture services are not production storage, cache or ingress
  designs;
- node autoscaling, node selectors, tolerations and priority classes remain
  operator-owned and unproven for this runtime.

Until those are built, keep fixed warm scheduler/worker capacity for production
work alongside the on-demand worker Jobs.
