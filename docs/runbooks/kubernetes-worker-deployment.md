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

Upgrading from an earlier release needs no manifest change: the existing
Deployments keep their commands and probes. Apply the new base manifests to
let the API start on-demand worker Jobs (`k8s/api/rbac.yaml`); without them,
work runs on the static executors only. Then remove what earlier releases
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

The production-oriented manifest at `k8s/worker/deployment.yaml` uses fixed
replica ownership:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: bifrost-worker
  namespace: bifrost
spec:
  replicas: 2
  template:
    spec:
      automountServiceAccountToken: false
      terminationGracePeriodSeconds: 360
      containers:
        - name: worker
          image: ghcr.io/gobifrost/bifrost-api:latest
          command: ["python", "-m", "src.worker.main"]
          env:
            - name: PIP_NO_CACHE_DIR
              value: "1"
            - name: HOME
              value: /home/bifrost
          envFrom:
            - configMapRef:
                name: bifrost-config
            - secretRef:
                name: bifrost-secrets
          resources:
            requests:
              cpu: "100m"
              memory: "256Mi"
            limits:
              cpu: "2000m"
              memory: "2Gi"
```

In this mode, Kubernetes owns the fixed replica count through the Deployment
spec. Scale manually with `kubectl scale deployment bifrost-worker -n bifrost
--replicas=<count>`.

## Removing RabbitMQ and KEDA after upgrading

Bifrost no longer uses RabbitMQ or KEDA. The base manifests no longer ship the
`rabbitmq` Deployment and Service or the RabbitMQ secret keys. Applying the new
manifests does not delete what an earlier release created; after every Bifrost
pod runs this release, an operator may remove the leftovers.

Keep the broker and `BIFROST_RABBITMQ_URL` until the queues are empty. Each
API pod, when it starts with `BIFROST_RABBITMQ_URL` set, tries the broker a few
times, moves every run an earlier release left queued into PostgreSQL, logs
`Drained work queued by an earlier release`, and disconnects; it does not poll
again. During a rolling upgrade an older API pod can still queue work after the
new pods have drained, so check the queues once the rollout finishes and, if
any still hold messages, restart the API Deployment to drain them:

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
kubectl -n bifrost scale deployment bifrost-worker --replicas=2
```

Deleting the `ScaledObject` also deletes the HPA it owns. Restore a fixed
replica count afterwards (the base manifest uses 2), because a Deployment
managed by KEDA usually omits `spec.replicas`. If KEDA served nothing else in
the cluster, uninstall it the way it was installed.

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

On SIGTERM an executor stops claiming, hands its supervised services over, and
lets active runs and platform jobs finish for up to 300 seconds before it
exits. Keep the pod's grace period above that:

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

Do not set production limits from idle measurements.

The latest local memory pass measured the complete worker container at
approximately 155.0 MiB settled idle after simple and SDK workloads. That value
includes the worker supervisor and warm execution template after the import and
launcher cleanup. It is an idle observation, not a recommended request or
limit.

The final local load check completed 96 concurrent four-second executions across
four worker pods. Per-container historical peaks ranged from 233.9 MiB to
276.8 MiB during that run. Earlier local load samples peaked higher. Customer
workflow code, installed Python packages, provider SDKs, larger payloads and
concurrent child executions add memory beyond these fixtures.

The existing production-oriented worker manifest keeps:

```yaml
resources:
  requests:
    cpu: "100m"
    memory: "256Mi"
  limits:
    cpu: "2000m"
    memory: "2Gi"
```

Treat those as conservative placeholders until production workload measurements
exist. A high-memory build path should use a separate fixed-template pod or Job
with its own request and limit. That path is not implemented by this worker
deployment.

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
