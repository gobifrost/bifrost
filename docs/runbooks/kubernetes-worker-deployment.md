# Kubernetes Worker Deployment

This runbook describes the worker deployment behavior proven in the current
Kubernetes spike. It documents the existing general-purpose worker running in
Kubernetes with optional operator-owned KEDA scale-out. It does not describe a
completed Elastic Runtime, a Bifrost-managed controller, dynamic high-memory
build pods, or one-shot execution Jobs.

For the local proof, see [local-kubernetes-spike.md](./local-kubernetes-spike.md)
and [local-kubernetes-spike-results.md](./local-kubernetes-spike-results.md).

## Current Implementation

The current worker is a single all-role process:

- workflow executions from `workflow-executions`;
- agent runs from `agent-runs`;
- live summaries from `agent-summarization`;
- summary backfills from `agent-summarization-backfill`;
- tuning chat work from `agent-tuning-chat`;
- package installation broadcast messages from the `package-installations`
  fanout exchange.

Every worker pod starts the same `python -m src.worker.main` entry point. There
is no lane-specific worker image, no `run-one` entry point, no Bifrost controller
that reconciles Kubernetes resources, and no Bifrost Settings surface for
runtime policy.

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
    - type: rabbitmq
      metadata:
        protocol: amqp
        queueName: agent-tuning-chat
        mode: QueueLength
        value: "1"
      authenticationRef:
        name: bifrost-rabbitmq
```

Include every queue the all-role worker serves. Watching only
`workflow-executions` can strand agent-only traffic when no worker is running.
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
