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
work runs on the static executors only.

Roll back only to a release that includes #1034, otherwise older pods
crash-loop against the newer schema. After a rollback, an older release no
longer runs the worker controller, so remove the warm worker Jobs it leaves
(they also drain themselves within `BIFROST_POOL_MAX_LIFETIME_SECONDS`):

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

## Optional KEDA Scale-Out

> Legacy: KEDA scaled the worker Deployment on RabbitMQ queue length. Work is
> claimed from PostgreSQL now, so these queues stay empty; on-demand worker
> Jobs replace this (stage 5b of #769 removes it).

The local spike also proves an opt-in operator-owned KEDA `ScaledObject` against
the same all-role worker Deployment. While that `ScaledObject` exists, KEDA/HPA
owns the worker replica count. The Deployment should omit `spec.replicas` in
that ownership mode so reapplying the Deployment does not fight the autoscaler.

Current local example:

```yaml
apiVersion: keda.sh/v1alpha1
kind: TriggerAuthentication
metadata:
  name: bifrost-rabbitmq
  namespace: bifrost-local
spec:
  secretTargetRef:
    - parameter: host
      name: bifrost-local-secrets
      key: BIFROST_RABBITMQ_URL
---
apiVersion: keda.sh/v1alpha1
kind: ScaledObject
metadata:
  name: bifrost-worker
  namespace: bifrost-local
  annotations:
    autoscaling.keda.sh/paused-scale-in: "true"
spec:
  scaleTargetRef:
    name: bifrost-worker
  pollingInterval: 5
  cooldownPeriod: 3600
  minReplicaCount: 1
  maxReplicaCount: 4
  advanced:
    horizontalPodAutoscalerConfig:
      behavior:
        scaleDown:
          selectPolicy: Disabled
  triggers:
    - type: rabbitmq
      metadata:
        protocol: amqp
        queueName: workflow-executions
        mode: QueueLength
        value: "1"
      authenticationRef:
        name: bifrost-rabbitmq
    - type: rabbitmq
      metadata:
        protocol: amqp
        queueName: agent-runs
        mode: QueueLength
        value: "1"
      authenticationRef:
        name: bifrost-rabbitmq
    - type: rabbitmq
      metadata:
        protocol: amqp
        queueName: agent-summarization
        mode: QueueLength
        value: "1"
      authenticationRef:
        name: bifrost-rabbitmq
    - type: rabbitmq
      metadata:
        protocol: amqp
        queueName: agent-summarization-backfill
        mode: QueueLength
        value: "5"
      authenticationRef:
        name: bifrost-rabbitmq
```

Include every queue the all-role worker serves. Watching only
`workflow-executions` can strand agent-only traffic when no worker is running.

Workers no longer consume `agent-tuning-chat` (it never had a publisher).
Remove its trigger from an existing ScaledObject, and delete the empty queue
once every worker runs this release:
`rabbitmqctl delete_queue agent-tuning-chat`.
The package-install broadcast exchange is not a durable scale-from-zero signal:
exclusive auto-delete subscriber queues exist only while workers are connected.

Use `autoscaling.keda.sh/paused-replicas: "<count>"` for temporary fixed
capacity while leaving the `ScaledObject` installed. Remove that annotation to
resume autoscaling. For permanent manual ownership, remove the `ScaledObject`
and its owned HPA, then restore `spec.replicas` on the Deployment.

## Scale-Down Policy

Automatic scale-down is disabled in the proven configuration.

The workflow consumer currently acknowledges a RabbitMQ message after dispatch
to the local child execution, before that child completes. RabbitMQ queue length
therefore does not include acknowledged active workflow children. Scaling down
only because the broker queue is empty can terminate work that is still running
inside a worker pod.

The current graceful drain fix handles ordinary pod deletion: the worker stops
new deliveries, waits for consumer tasks and active workflow children, and
returns either the real result or a durable `WorkerShutdown` failure when the
drain deadline is exceeded. That is enough for conservative manual drain and for
the local deletion tests. It is not enough to enable automatic scale-in from
queue depth.

Keep these values aligned:

```yaml
env:
  - name: BIFROST_DRAIN_DEADLINE_SECONDS
    value: "300"
terminationGracePeriodSeconds: 360
```

The Kubernetes grace period should exceed the Bifrost drain deadline by enough
time for connection cleanup.

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

KEDA has its own controller permissions from the KEDA installation. Bifrost pods
do not receive those permissions. If Bifrost later manages Deployments, Jobs or
KEDA resources itself, that requires a new namespace-scoped ServiceAccount,
Role and RoleBinding for the controller role. That controller does not exist in
the current implementation.

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

- package-install notifications are broadcasts to live workers, not durable
  commands for future zero-replica pods;
- ephemeral pods cannot rely on filesystem mutations made inside earlier pods;
- requirements text is not a versioned, immutable artifact contract;
- a pod can only safely accept work after dependency hydration succeeds.

For production elasticity, each pool or Job template needs an image or durable
artifact fingerprint that fully describes the dependency set it can run.

## External Services

The local `k8s/local/` directory is a disposable Kind fixture. It includes
PostgreSQL, PgBouncer, RabbitMQ, Redis and SeaweedFS with fixture credentials,
`emptyDir` storage and local images. It is not production-ready.

Production deployments need operator-managed services or production-grade
in-cluster equivalents:

- PostgreSQL with backups, HA, maintenance, connection-pool sizing and upgrade
  procedures;
- RabbitMQ with durable storage, HA policy, TLS/authentication and monitored
  queue depth;
- Redis with the selected persistence/HA posture for Bifrost's cache and result
  paths;
- S3-compatible object storage with durable buckets, credentials rotation and
  lifecycle policy;
- ingress, TLS, WebSocket support and public URL/WebAuthn configuration.

The local KEDA example uses the AMQP URL from `BIFROST_RABBITMQ_URL`. In a real
cluster, keep that URL fully qualified and use the deployment's normal TLS and
network-policy posture.

## Draining Operations

For manual drain:

1. Pause KEDA at the desired capacity or remove the `ScaledObject` if KEDA owns
   the Deployment.
2. Reduce replicas gradually.
3. Watch worker logs for graceful drain completion.
4. Verify durable workflow or agent terminal state rather than only pod exit.

Useful checks:

```bash
kubectl get pods -n bifrost -l app.kubernetes.io/name=bifrost-worker
kubectl logs -n bifrost -l app.kubernetes.io/name=bifrost-worker --tail=200
kubectl get hpa -n bifrost
kubectl get scaledobject -n bifrost
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
- there is no Bifrost controller/settings surface for runtime policy,
  generation transitions or KEDA/Job reconciliation;
- there is no one-shot `run-one` worker path with a fixed high-memory queue and
  pod template;
- OOM/pending-capacity attribution is not wired into durable job results;
- scale-down remains disabled because RabbitMQ queue length does not account
  for acknowledged active workflow children;
- dependency hydration is not yet immutable or artifact-fingerprinted for
  zero-replica pools;
- package-install fanout cannot wake future pods from zero replicas;
- local fixture services are not production storage, broker, cache or ingress
  designs;
- node autoscaling, node selectors, tolerations and priority classes remain
  operator-owned and unproven for this runtime.

Until those are built, use fixed warm scheduler/worker capacity for production
work and treat KEDA worker scale-out as an operator-controlled capacity
experiment for the existing all-role worker.
