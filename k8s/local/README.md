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
scripts/kubernetes/local-kind.sh experiment --help
scripts/kubernetes/local-kind.sh build-experiment --help
scripts/kubernetes/local-kind.sh collect
scripts/kubernetes/local-kind.sh down
```

The script writes all local state under `/tmp/bifrost-k8s-<worktree-hash>/`, including a scoped `kind` binary, kubeconfig, diagnostics, and rendered manifests. Every `kubectl` call passes that kubeconfig and the matching `kind-<cluster>` context so it does not use or mutate the user's active cluster context.

The harness deploys:

- PostgreSQL with pgvector
- PgBouncer
- RabbitMQ
- Redis
- SeaweedFS S3
- Bifrost init migration/cache-warm job
- Bifrost API, scheduler, and worker deployments
- KEDA with one operator-owned worker `ScaledObject`

Kubernetes App build Jobs are disabled by default. To opt into the local
manifests for isolated build Jobs, run:

```bash
BIFROST_KIND_BUILD_JOBS=1 ./test.sh kubernetes up
```

That flag applies `k8s/local/build-jobs.yaml`, renders the build runner image
from `BIFROST_K8S_IMAGE`, injects build backend settings into the API and
scheduler, and patches only the scheduler onto the controller ServiceAccount.
API and worker pods keep service-account token automount disabled.

After booting with the flag, run the build-Job experiment through the harness:

```bash
./test.sh kubernetes build-experiment --output /tmp/bifrost-build-jobs --restart-scheduler
```

Worker scale-in is disabled by default through the HPA scale-down behavior and a paused scale-in annotation. This keeps the spike conservative until worker drain behavior has been proven under real queue pressure. While the `ScaledObject` exists, KEDA owns worker replica count. To scale workers manually, first pause KEDA with `autoscaling.keda.sh/paused-replicas` or remove the `ScaledObject`; remove that pause annotation or re-apply the `ScaledObject` to return ownership to KEDA.

To test HTTP access without fixed host ports:

```bash
scripts/kubernetes/local-kind.sh kubectl -- port-forward -n bifrost-local svc/api 8000:8000
```
