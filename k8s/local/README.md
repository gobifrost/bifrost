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

Isolated build Jobs are off by default, as on any install: a platform admin
turns them on in Settings → Kubernetes → Scaling & Isolation. The API's
service account (`api-rbac.yaml`) already manages Jobs, so the API runs the
build-Job controller, and isolated pods copy the API pod. To also apply the
builds overlay's Job settings (every Job runs as the tokenless
`bifrost-build-runner` account), run:

```bash
BIFROST_KIND_BUILD_JOBS=1 ./test.sh kubernetes up
```

That flag applies `k8s/local/build-jobs.yaml` and gives the API its
ConfigMap. The scheduler and worker executors mount no service-account token.

Run the build-Job experiment through the harness. It turns isolated jobs on
in Settings for its run and restores the setting afterwards:

```bash
./test.sh kubernetes build-experiment --output /tmp/bifrost-build-jobs --restart-controller
```

`--restart-controller` deletes one API pod while builds run (the workload runs in the other), so the remaining build controller must carry the builds to completion.

The scheduler and worker Deployments have fixed replica counts. Additional capacity comes from on-demand worker Jobs (`app.kubernetes.io/name=bifrost-pool-worker`) that the API's worker controller starts when accepted work exceeds free executor capacity; idle Job workers drain themselves. `config.yaml` gives those Jobs a small memory request for a single-node cluster.

The pool experiment and drills change Settings → Kubernetes as an admin would (Idle time 45 seconds for their run, and each drill's own values) and restore what they changed:

```bash
./test.sh kubernetes pool-experiment
./test.sh kubernetes pool-drills --only sized_isolated_build max_workers_setting isolation_off
```

The `sized_isolated_build` drill requests a pod of about 3.5 GiB (its 2.5 GiB requirement plus the worker baseline, at the 85% memory threshold), so the Kind node needs that much free memory.

To test HTTP access without fixed host ports:

```bash
scripts/kubernetes/local-kind.sh kubectl -- port-forward -n bifrost-local svc/api 8000:8000
```
