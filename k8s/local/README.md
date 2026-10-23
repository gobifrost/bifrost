# Local Kind Kubernetes Spike

This directory is a self-contained local Kubernetes harness for Bifrost. It is intentionally separate from the production-oriented `k8s/` manifests.

Use the repository test passthrough from the worktree root:

```bash
./test.sh kubernetes up
./test.sh kubernetes status
./test.sh kubernetes collect
./test.sh kubernetes down
```

The underlying harness is `scripts/kubernetes/local-kind.sh`:

```bash
scripts/kubernetes/local-kind.sh up
scripts/kubernetes/local-kind.sh status
scripts/kubernetes/local-kind.sh build-experiment --help
scripts/kubernetes/local-kind.sh collect
scripts/kubernetes/local-kind.sh down
```

The script writes all local state under `/tmp/bifrost-k8s-<worktree-hash>/`, including a scoped `kind` binary, kubeconfig, diagnostics, and rendered manifests. Every `kubectl` call passes that kubeconfig and the matching `kind-<cluster>` context so it does not use or mutate the user's active cluster context.

The harness deploys:

- PostgreSQL with pgvector
- PgBouncer
- Redis
- SeaweedFS S3
- Bifrost init migration/cache-warm job
- Bifrost API deployment (two replicas; the API service account may manage worker Jobs, see `api-rbac.yaml`)
- Bifrost scheduler and worker executor deployments

Kubernetes App build Jobs are disabled by default. To opt into the local
manifests for isolated build Jobs, run:

```bash
BIFROST_KIND_BUILD_JOBS=1 ./test.sh kubernetes up
```

That flag applies `k8s/local/build-jobs.yaml`, renders the build runner image
from `BIFROST_K8S_IMAGE`, and injects the build backend settings into the API.
The API's service account (`api-rbac.yaml`) already manages Jobs, so the API
runs the build-Job controller; the scheduler and worker executors mount no
service-account token.

After booting with the flag, run the build-Job experiment through the harness:

```bash
./test.sh kubernetes build-experiment --output /tmp/bifrost-build-jobs --restart-scheduler
```

The scheduler and worker Deployments have fixed replica counts. Additional capacity comes from on-demand worker Jobs (`app.kubernetes.io/name=bifrost-pool-worker`) that the API's worker controller starts when accepted work exceeds free executor capacity; idle Job workers drain themselves. `config.yaml` sizes those Jobs for a single-node cluster.

To test HTTP access without fixed host ports:

```bash
scripts/kubernetes/local-kind.sh kubectl -- port-forward -n bifrost-local svc/api 8000:8000
```
