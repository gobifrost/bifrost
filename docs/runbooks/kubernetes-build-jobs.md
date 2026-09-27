# Kubernetes App Build Jobs

This guide covers the opt-in Kubernetes execution path for isolated App build
Jobs. The normal production manifests keep local execution by default, there is
no Settings UI for this yet, and the broader Elastic Runtime is still not
implemented.

The implemented scope is narrow. The warm scheduler remains online for ordinary
PlatformJob work, using its existing local capacity. Build-class PlatformJobs
for independent App deploys and App SDK rebuilds (`application.deploy` and
`application.sdk_update`) are assigned to separate Kubernetes Jobs when the
operator enables the Kubernetes build backend. Those Jobs use the same
compatible Bifrost API image, but run with their own memory and CPU settings.

## What The Overlay Installs

Apply the opt-in overlay only for an application version that contains the
Kubernetes build backend:

```bash
kubectl apply -k deploy/kubernetes/builds
```

The overlay adds:

- `bifrost-build-controller` ServiceAccount for the scheduler;
- a namespace-scoped Role and RoleBinding that can manage `batch/jobs` and read
  pods;
- `bifrost-build-runner` ServiceAccount for build Job pods with token automount
  disabled;
- `bifrost-build-jobs-config` with backend, image, namespace, resource and
  concurrency settings;
- patches that give the API and scheduler the build backend settings;
- a scheduler patch that mounts the controller token.

The base `k8s/kustomization.yml` does not include this overlay. Applying the
normal manifests keeps local scheduler execution.

## Configuration

Replace the image placeholder with the exact Bifrost API image tag compatible
with the deployed API and scheduler:

```yaml
BIFROST_KUBERNETES_BUILD_IMAGE: "ghcr.io/gobifrost/bifrost-api:<compatible-tag>"
```

Initial settings:

```yaml
BIFROST_PLATFORM_BUILD_BACKEND: "kubernetes"
BIFROST_KUBERNETES_BUILD_NAMESPACE: "bifrost"
BIFROST_KUBERNETES_BUILD_CONFIGMAP: "bifrost-config"
BIFROST_KUBERNETES_BUILD_SECRET: "bifrost-secrets"
BIFROST_KUBERNETES_BUILD_SERVICE_ACCOUNT: "bifrost-build-runner"
BIFROST_KUBERNETES_BUILD_JOB_TYPES: "application.deploy,application.sdk_update"
BIFROST_KUBERNETES_BUILD_MEMORY_REQUEST_MIB: "512"
BIFROST_KUBERNETES_BUILD_MEMORY_LIMIT_MIB: "2048"
BIFROST_KUBERNETES_BUILD_MAX_JOBS: "2"
BIFROST_KUBERNETES_BUILD_PENDING_TIMEOUT_SECONDS: "300"
BIFROST_KUBERNETES_BUILD_CPU_REQUEST: "250m"
BIFROST_KUBERNETES_BUILD_CPU_LIMIT: "1"
```

The API receives these settings so enqueue placement can be persisted
consistently. `BIFROST_KUBERNETES_BUILD_JOB_TYPES` is the operator ceiling:
only those job types are eligible for remote placement when the backend is
enabled.

## Product Opt-In (Settings UI)

Once the deployment is configured, platform admins get a **Kubernetes**
section in Settings with an **Executions** list: one toggle per eligible
build-class job type, defaulting to the measured high-memory jobs (App
deploys and SDK rebuilds). A job launches a pod only when the toggle is on
**and** the deployment allowlist names it. Each row also carries a **max
parallel runs** input (1–32, empty resets to the code default) that replaces
the job policy's concurrency in the shared claim path — local and remote
runs alike. Changes apply to newly enqueued jobs; running jobs keep their
placement. The tab is hidden on deployments without the backend configured,
so there is nothing to explain away on local installs.

## Enable/Disable Notice

When the deployment's configured state flips in either direction, the API
emits one platform-admin notification at startup ("Kubernetes execution
enabled/disabled") with a **Learn more** button opening the short version
of this story plus a direct link to the Executions tab. Dismissing parks it;
it fires again only on the next flip — never more than once per change.
Fresh installs that never configured the backend stay silent.

The scheduler receives these settings and the controller ServiceAccount so
it can create, bind, start, observe and clean up Jobs. API and worker pods do
not need Kubernetes credentials.

Memory is split into a request and a limit, mirroring the existing CPU
request/limit pair. The request (512 MiB) is the guaranteed reservation the
Kubernetes scheduler uses for node packing; the pod may burst up to the
limit (2 GiB) when the node has headroom, and is OOM-killed past it. That
split makes build pods Burstable instead of Guaranteed: the tradeoff for
denser packing is that Burstable pods are evicted before Guaranteed ones
when a node comes under memory pressure. The earlier single-value evidence
(614 MiB peak in a 1 GiB pod) sits comfortably under the 2 GiB ceiling with
room for larger apps. The controller refuses to start when the request
exceeds the limit.

## RBAC And Runner Isolation

The scheduler controller Role is intentionally small:

- `batch/jobs`: `get`, `list`, `create`, `patch`, `delete`;
- core `pods`: `get`, `list`.

The runner ServiceAccount has token automount disabled. Build pods should use
the configured Bifrost ConfigMap and Secret through `envFrom`, with only job and
attempt identifiers passed as Job-specific environment. Do not put payloads,
source archives, credentials or tokens in Job manifests.

## No KEDA Requirement

Build Jobs do not require KEDA. The scheduler controller creates bounded Jobs
directly from persisted build placement. `BIFROST_KUBERNETES_BUILD_MAX_JOBS`
limits concurrent remote build attempts independently of ordinary scheduler
slots.

This is separate from the existing all-role worker KEDA experiment. Worker queue
autoscaling does not solve isolated App builds and should not be presented as
the build-pod solution.

## Local Kind Opt-In

The local harness preserves its default behavior. To exercise the local build
Job manifests, set the explicit flag before `up`:

```bash
BIFROST_KIND_BUILD_JOBS=1 ./test.sh kubernetes up
```

The local path applies `k8s/local/build-jobs.yaml`, renders
`BIFROST_KUBERNETES_BUILD_IMAGE` from `BIFROST_K8S_IMAGE`, injects the build
settings into API and scheduler Deployments, and patches only the scheduler to
use `bifrost-build-controller` with token automount enabled. API and worker
pods keep token automount disabled.

## Live Local Evidence

A corrected local Kind run validated the implemented App build path with this
command:

```bash
./test.sh kubernetes build-experiment --output /tmp/bifrost-769-build-live-corrected --restart-scheduler
```

Artifacts were written to
`/tmp/bifrost-769-build-live-corrected/{result.json,memory.json,run.json}`. The
run used image
`bifrost-elastic-spike:769-build-jobs-v3@sha256:f0ace6fa14dd4c9c190cc771be646971d5721a5a55f3f0b3199e0fce5cfff72b`; the host
harness piped the corrected fixture source into the API pod with `kubectl`
standard input.

The run passed two real App deploys in 1 GiB Kubernetes build pods. While both
builds were running, a lightweight `artifact.retention_cleanup` PlatformJob ran
and completed locally, and the scheduler pod was replaced. After the replacement,
the remote builds finished, the SDK rebuild published a new active deployment,
and readable build artifacts were confirmed. (This run predates the
`max_concurrency=1` default on App deploys; the live experiment script now
lifts the cap through the Settings override for the duration of the run and
restores it afterwards.)

Observed peak kernel cgroup memory during this run was 614.25 MiB for a build
pod and 408.14 MiB for the scheduler pod. These measurements include kernel
cache and use the current production image. They are capacity evidence for this
local fixture, not a latency benchmark; the fixture includes a synthetic
30-second build delay to make overlap deterministic.

Focused verification reached 89 passing tests. Review then corrected cross-backend
concurrency and resource locking; all 23 scheduler tests passed with that fix.
API lint/type checks passed before that final two-predicate correction.
Full `./test.sh pre-pr` has not run; this is local evidence, not release completion.

Exact focused commands:

```bash
./test.sh tests/unit/test_kubernetes_build_controller.py tests/unit/test_platform_job_kubernetes_client.py tests/unit/test_kubernetes_build_manifests.py tests/unit/test_kubernetes_spike_manifests.py tests/unit/jobs/platform/test_kubernetes_runner.py tests/unit/jobs/schedulers/test_platform_jobs.py tests/unit/services/test_platform_jobs.py tests/unit/test_contract_version.py tests/e2e/platform/test_application_sdk_update.py -q
./test.sh quality api
./test.sh tests/unit/jobs/schedulers/test_platform_jobs.py -q
```

## Disabling Remote Builds

Disable gracefully. Do not remove the scheduler controller credentials while
persisted remote build attempts still exist.

1. Switch the API configuration to `BIFROST_PLATFORM_BUILD_BACKEND=local` first
   so new App deploy and SDK rebuild jobs are persisted with local placement.
2. Keep the scheduler running with the Kubernetes build controller and RBAC so
   it can observe, finish and clean up existing remote attempts.
3. Confirm all persisted remote build jobs are terminal or have been safely
   canceled through Bifrost's durable job contract.
4. Confirm Kubernetes Jobs owned by those attempts have completed cleanup.
5. Only then switch the scheduler to local build configuration and remove the
   scheduler controller RoleBinding if it is no longer needed.

Do not use `kubectl delete -k deploy/kubernetes/builds` as a shutdown step: the
opt-in overlay includes the base Deployments as resources, so deleting the
kustomization can delete the application workloads. Disable through
configuration changes, drain the remote work, then remove only the now-unused
controller RBAC or build-specific objects intentionally.

There must be no silent local fallback. If Kubernetes build placement is enabled
and the scheduler cannot create or observe Jobs, the affected build work should
remain durably owned by the remote placement path and surface an actionable
capacity/controller error rather than running inside the warm scheduler.

Remote attempts persist their target namespace. Treat namespace, cluster and
controller credentials as part of the remote ownership boundary: drain and clean
up existing remote attempts before changing any of them. A mid-flight namespace
or credential change must fail closed until the existing remote work is
accounted for.

## Current Limits

The current remote build scope is independent App deploy and App SDK rebuild:
`application.deploy` and `application.sdk_update`. Those jobs use durable
backend placement, scheduler-owned Kubernetes Job creation, UID-bound runner
admission, lease-token heartbeats and completion, bounded remote concurrency,
pending timeout handling and Kubernetes Job cleanup.

Build policies use single attempts (`max_attempts=1`) with no retry on runner
loss: a lost remote attempt fails visibly with a structured error code
(`capacity_unavailable`, `runner_lost`, `runner_exited`, `memory_pressure`,
`timeout`) rather than silently retrying or falling back to local execution.

Running builds are not cancellable: both build policies leave
`allow_running_cancellation` disabled, so cancel requests for a running
remote build are refused and the attempt runs to a terminal result. Cancel
while the job is still queued (before the controller claims it) works
normally through the shared cancel endpoint.

Kubernetes object metadata never carries the lease token: Job names and
labels carry only a SHA-256 fingerprint of the attempt token. The full token
travels inside the pod's startup command, which the runner needs to
authenticate progress and completion calls.

Known limit: if a Kubernetes Job is deleted out of band while its pods are
still running, the controller waits for those pods to stop before clearing
the durable attempt name. The controller Role grants pods only `get`/`list`,
so it cannot force-delete orphan pods; remove them manually (or re-apply the
overlay Job) to let the attempt finish cleanup.

Solution build execution is not implemented for Kubernetes build Jobs yet and
still needs an explicit product decision before being advertised.
