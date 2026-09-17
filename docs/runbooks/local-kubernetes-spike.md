# Local Kubernetes execution spike

## What this delivers

An opt-in, disposable Kubernetes environment that runs Bifrost's real worker,
RabbitMQ and supporting services with KEDA. Existing Docker Compose and fixed
production deployments keep their behavior. There is no runtime-policy UI,
Kubernetes-aware scheduler controller, or remote PlatformJob execution in this
spike.

The worker fix also applies to existing installations: graceful shutdown waits
for dispatched workflow children and their result callbacks, rather than only
waiting for RabbitMQ handlers. When the execution grace expires, the worker
attempts to save a `WorkerShutdown` failure before closing its infrastructure
connections. It suppresses late child results after a shutdown result owns
completion. It does not automatically retry arbitrary workflow side effects.

## Run locally

Requirements: Linux Docker host with cgroup v2, Docker, kubectl, curl, Python 3,
sha256sum, and enough free memory for the Kubernetes node plus Bifrost and up to
four workers. The experiment used a 64 GiB host; that is an observation, not a
minimum requirement. Kind v0.24.0 (Kubernetes v1.31.0) and KEDA v2.16.1 are the
pinned experiment versions. They are not a production version recommendation.

From an isolated Bifrost worktree:

```bash
./test.sh kubernetes up
./test.sh kubernetes status
./test.sh kubernetes experiment warm
./test.sh kubernetes experiment sdk
./test.sh kubernetes experiment cold
./test.sh kubernetes experiment burst
./test.sh kubernetes experiment load
./test.sh kubernetes experiment drain
./test.sh kubernetes experiment deadline
./test.sh kubernetes collect
./test.sh kubernetes down
```

Run experiments serially: they change the local worker pool's bounds and pause
state. Each experiment writes an exclusive timestamped directory under `/tmp`,
or accepts `--output /tmp/my-unique-results`. It restores the original scaling
policy, and the deadline experiment restores the original worker environment
and waits for the rollout. It does not promise to restore the previous replica
count: scale-down is deliberately disabled in the default policy.

`up` downloads a checksum-verified, scoped Kind binary; creates a per-worktree
cluster; builds/loads the API image locally; installs KEDA; waits for dependencies
and migrations; and waits for Bifrost plus the scaler to become ready. Rebuilt
images and changed configuration trigger a rollout through a content revision.
Failures collect local Kubernetes diagnostics. All cluster commands use an
explicit private kubeconfig under `/tmp/bifrost-k8s-<worktree-hash>`; they do not
use the user's current context. No ports are exposed by default.

To inspect the API locally:

```bash
./test.sh kubernetes kubectl -- port-forward -n bifrost-local svc/api 8000:8000
```

The local manifests contain disposable fixture credentials, use ephemeral
storage, and do not provide a complete customer-facing frontend. Do not deploy
this directory into an existing customer cluster. `down` removes only this
worktree's local cluster, including its data.

## What each experiment proves

| Experiment | Observable assertion |
| --- | --- |
| `warm` | 100 sequential zero-delay executions return matching durable results; records p50/p95/p99. |
| `sdk` | 100 sequential executions call the real organizations SDK and return durable results; records p50/p95/p99. |
| `cold` | Removes all workers, submits work, and requires activation plus a successful durable result. |
| `burst` | Runs 32 concurrent four-second executions; records whether replicas actually increase, without requiring them to. |
| `load` | Runs 96 concurrent four-second executions; requires at least two ready replicas and successful results from at least two distinct worker pod hostnames. |
| `drain` | Waits for the active execution lease, deletes the sole owning worker pod during a 15-second execution, and requires Success. |
| `deadline` | Uses a one-second drain deadline, deletes the owning pod, and requires a returned and durably saved `WorkerShutdown` failure. |

Latency starts at the shared inline-execution producer and ends at the Redis
result receipt. It includes producer work, queue wait and execution, but excludes
HTTP authentication/routing and browser rendering. These small samples describe
the local experiment; they do not establish a production latency SLO or a
statistically bounded regression result. Synchronous inline executions skip UI queue-position tracking, matching the
workflow producer and consumer. This is an execution-path benchmark, not a pure
broker throughput benchmark.

Memory snapshots report cgroup current/peak, an estimate of working set
(`current - inactive_file`), anonymous/file/kernel bytes, and process PSS where
readable. The short measurement process adds some overhead. Cgroup peak is
historical for that container, not an isolated per-execution allocation.
See [the memory diagnostic](elastic-runtime-memory.md) for fresh import probes.

## Customer enablement and ownership

For the operator-managed approach demonstrated here:

1. The cluster operator installs a supported KEDA version and its CRDs/RBAC.
2. They configure RabbitMQ connectivity and a Kubernetes Secret referenced by
   `TriggerAuthentication`. Use a fully qualified broker address when KEDA runs
   in another namespace; configure the deployment's normal TLS/network policy.
3. They target the Bifrost worker Deployment with a `ScaledObject`, choosing a
   warm minimum, maximum replicas, polling interval and queue targets. Include
   every work queue served by that deployment so agent-only traffic can wake it.
4. They preserve measured worker resource requests/limits and a termination
   grace greater than Bifrost's drain deadline plus process cleanup time.
5. They verify activation, sustained load and termination with their own
   workload before enabling automatic scale-in.

KEDA owns current replicas while autoscaling is active. The Deployment manifest
omits `spec.replicas` so reapplying it does not overwrite HPA-managed capacity.
Operators still choose minimum and maximum capacity. For temporary fixed
capacity, set `autoscaling.keda.sh/paused-replicas` to the desired count. Remove
the annotation to resume autoscaling. For permanent manual ownership, remove
the `ScaledObject` and its owned HPA, then set Deployment replicas.

Bifrost itself needs **no Kubernetes API permission** for this operator-managed
spike. All Bifrost pod templates disable service-account token automounting.
KEDA's own installation has its separately installed controller permissions.
A future Bifrost-managed settings feature would need an additional, explicitly
scoped controller role; it is not installed here.

Scaling pods only reduces compute billing if the cluster can also release
unused nodes. Node autoscaling is an operator-owned capability outside this
local one-node proof. The API, scheduler and supporting services stay running.

## Boundaries before production scale-in

The default remains one warm worker, a maximum of four, and scale-down disabled.
AMQP queue length omits acknowledged workflow children and can miss short
bursts. It is not yet a complete active-capacity metric.

The graceful shutdown fix cannot guarantee durable completion if the result
persistence callback itself is hung or the database is unavailable through the
termination deadline. Kubernetes SIGKILL/node loss also bypasses graceful drain.
Those paths still need the durable attempt/reconciliation work described in the
design. Do not interpret the passing graceful-delete test as exactly-once
execution or comprehensive node-loss recovery.

Remote high-memory PlatformJobs, OOM/pending-capacity attribution, immutable
package hydration at zero replicas, agent-provider live execution tests,
generation-based policy transitions, and a protected lean one-shot runner remain
separate experiments. None is advertised as implemented by this harness.
