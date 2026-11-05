# Local Kubernetes execution spike

## What this delivers

An opt-in, disposable Kind cluster that runs Bifrost's real runtime: two API
replicas (platform control and the on-demand worker controller, with RBAC to
manage worker Jobs), the `bifrost-scheduler` and `bifrost-worker` executors,
PostgreSQL, PgBouncer, Redis and SeaweedFS. Existing Docker Compose and fixed
production deployments keep their behavior.

## Run locally

Requirements: Linux Docker host with cgroup v2, Docker, kubectl, curl, Python 3,
sha256sum, and enough free memory for the Kubernetes node plus Bifrost and its
on-demand worker Jobs. Kind v0.24.0 (Kubernetes v1.31.0) is the pinned
experiment version. It is not a production version recommendation.

From an isolated Bifrost worktree:

```bash
./test.sh kubernetes up
./test.sh kubernetes status
./test.sh kubernetes control-experiment
./test.sh kubernetes pool-experiment
./test.sh kubernetes pool-drills
./test.sh kubernetes collect
./test.sh kubernetes down
```

Run experiments serially. Each writes its results under `/tmp`, or accepts
`--out`/`--output` for a chosen directory; the docstring at the top of each
script in `scripts/kubernetes/` lists its phases and assertions.

`up` downloads a checksum-verified, scoped Kind binary; creates a per-worktree
cluster; builds/loads the API image locally; waits for dependencies and
migrations; and waits for Bifrost to become ready. Rebuilt images and changed
configuration trigger a rollout through a content revision. Failures collect
local Kubernetes diagnostics. All cluster commands use an explicit private
kubeconfig under `/tmp/bifrost-k8s-<worktree-hash>`; they do not use the user's
current context. No ports are exposed by default.

To inspect the API locally:

```bash
./test.sh kubernetes kubectl -- port-forward -n bifrost-local svc/api 8000:8000
```

The local manifests contain disposable fixture credentials, use ephemeral
storage, and do not provide a complete customer-facing frontend. Do not deploy
this directory into an existing customer cluster. `down` removes only this
worktree's local cluster, including its data.

## What each experiment proves

| Experiment | Script |
| --- | --- |
| `control-experiment` | `control_spike.py`: one API replica holds the trigger lease; failover and rollouts never run a schedule minute twice. |
| `pool-experiment` | `pool_spike.py`: from the warm minimum (1) beside the static executors, a backlog beyond every slot scales worker Jobs to the ceiling (6) and never past it, a run accepted during the drain completes, and the pool returns to the minimum. |
| `pool-drills` | `pool_drills.py`: killed, unschedulable and out-of-memory workers and a restarted controller end visibly and never replay a run. |
| `build-experiment` | `build_spike.py`: isolated App build Jobs (requires `BIFROST_KIND_BUILD_JOBS=1`). |

See [the memory diagnostic](elastic-runtime-memory.md) for fresh import probes
and [Kubernetes worker deployment](kubernetes-worker-deployment.md) for the
production executor model.
