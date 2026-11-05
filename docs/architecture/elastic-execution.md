# Elastic execution engine

The elastic execution engine runs selected heavy PlatformJobs in temporary
Kubernetes pods (isolated jobs), sized for the job, while workers stay free
for lightweight work. This document defines the engine's scope and the
contracts a future agent must follow to use or expand it — including the
planned future step of workflow lanes.

`docs/architecture/platform-jobs.md` remains the authority on the durable
job system itself. `docs/runbooks/kubernetes-build-jobs.md` is the operator
manual. This document is the bridge: what the engine is, what it is not,
and exactly how to extend it.

## Scope (what the engine is)

- **One engine, two backends.** Every PlatformJob is durably placed at
  enqueue time as `local` or `kubernetes` (`execution_backend` on the row).
  Local jobs run on any executor (worker Jobs, or a static executor an
  install kept); Kubernetes jobs run in one-shot pods that copy the API pod.
  Placement is persisted so replicas never disagree after restart or a
  Settings change.
- **Classification is owned by Bifrost, policy by admins.** A job type opts
  into isolation by setting `execution_class="build"` in its
  `PlatformJobPolicy`. Platform admins turn isolation on or off, choose the
  isolated types and bound the pods in Settings → Kubernetes → Scaling &
  Isolation (persisted in `system_configs`, read by the controller every
  pass). Deployment env holds only what a Job runs as (the optional
  `BIFROST_KUBERNETES_JOB_*` identity, CPU and pending timeout); a Settings
  page never chooses an image or service account.
- **Three gates must agree for a pod to launch:** Bifrost classification
  (build class) **and** Isolate Build Jobs on **and** the job type's toggle
  on (all five build types by default).
- **Current isolated set is the five build types:** `application.deploy`,
  `application.sdk_update`, `solution.deploy`, `solution.export` and
  `solution.git_sync`. Each compiles Apps or packs archives in
  subprocesses. Everything else runs local.
- **Measured evidence decides membership.** A job type becomes build class
  only after a recorded cgroup working-set measurement justifies it:
  - App deploy / SDK rebuild: ~614 MiB peak observed live in Kind.
  - `workspace.reimport`: ~21 MiB delta over a small seeded workspace
    (3 workflows + 1 app + table + form, 2 s wall;
    `api/tests/e2e/platform/test_workspace_reimport_memory.py`).
    Verdict: stays local. No compile step exists in its path, and cost
    scales with workspace size, not with a build spike.

## Concepts (the knobs)

1. **`execution_class`** (`PlatformJobPolicy`, `api/src/jobs/platform/base.py`).
   `"default"` or `"build"`. The only value the Kubernetes controller
   admits today. New classes (e.g. a future `"workflow"`) are added here.
2. **Backend placement** (`decide_execution_backend`,
   `api/src/services/kubernetes_execution.py`, at enqueue). A build-class
   job goes to `execution_backend="kubernetes"` when Isolate Build Jobs is
   on and its type is enabled; otherwise `local`. Turning isolation off
   moves queued jobs that have no pod yet back to `local`.
3. **Pod sizing: the worker rule** (`api/src/services/worker_pool/sizing.py`).
   An isolated pod is sized like a worker Job. Standard: request
   `BIFROST_POOL_WORKER_MEMORY_REQUEST_MIB`, limit Worker Memory
   (Burstable). Sized: a job whose learned `memory_required_bytes` exceeds
   a standard pod's budget gets the smallest limit whose budget holds it,
   up to Largest Worker, with request = limit. Larger than that fails
   visibly (`worker_pool/unfit.py`). CPU comes from
   `BIFROST_KUBERNETES_JOB_CPU_REQUEST` / `_CPU_LIMIT`.
4. **Learning.** Every isolated type has a memory profile key (per App for
   App deploys and SDK rebuilds, per content for Solution deploys, per
   Solution for exports and Git syncs). A pod stopped at its memory limit
   teaches the profile at least that limit plus the margin, so the next
   run gets a larger pod.
5. **Concurrency, two layers.** Every build-class job type defaults to
   `max_concurrency=1` (pinned by test — parallelism is never accidental).
   Max Isolated Pods (Settings) separately bounds concurrent isolated
   pods, independent of the worker pool. Platform admins can override
   per-type concurrency on the same page (1–32, empty resets to the code
   default); the override replaces the policy default in the shared claim
   path, so it governs local and isolated runs alike.
6. **Controller ownership** (`api/src/jobs/schedulers/kubernetes_jobs.py`).
   One deterministic suspended Job per claimed attempt; UID binding;
   first-pod-wins admission; no silent local fallback once placed; terminal
   cleanup. PostgreSQL is status/progress/result truth; Kubernetes is
   capacity. The loop runs wherever the process may manage Jobs; the
   Settings switch decides only whether it admits new work.
7. **Operator visibility.** `execution_backend` rides the shared
   `PlatformJobPublic` contract, and Diagnostics → Scheduler shows a
   "Kubernetes" badge on isolated jobs. No separate dashboard; pod-level
   detail stays in kubectl.

## How to add a job class to isolated execution

Do these in order; do not skip measurement.

1. **Measure first.** Add or reuse an e2e memory test following
   `test_workspace_reimport_memory.py`: seed representative data, run to
   terminal state, assert success + recorded `memory_start/peak_bytes`,
   log entity count, wall time, and peak-minus-start MiB. Record the
   numbers in this document's evidence table below.
2. **Set the policy.** `execution_class="build"`,
   `max_concurrency=1` (no exceptions — the convention test enforces it),
   keep `max_attempts=1` until retry-through-
   cleanup is explicitly tested for the new type.
3. **Give it a memory profile key.** Enqueue it with a
   `memory_profile_key` (`build_resource_memory_profile_key` or a content
   fingerprint), so the worker sizing rule sizes its pod from what earlier
   runs needed and an OOM-killed pod teaches the next run.
4. **Pilot through the per-type toggle.** Add the type to
   `DEFAULT_REMOTE_JOB_TYPES` (`kubernetes_execution.py`) only once the
   pilot proves out. Until then a platform admin enables it in Settings →
   Kubernetes → Scaling & Isolation in one environment first; it stays on
   workers everywhere the toggle is off.
5. **Cover the paths.** Backend selection, claim exclusion, controller
   create/restart/cancel/OOM for the new type, plus a live run while a
   light local job succeeds (workers must stay responsive).
6. **Document.** Add the type to the isolated set above and to the
   runbook. Link the measurement.

## How to extend the engine (future work, not implemented)

- **New execution classes.** Add the literal in `base.py`, teach the
  controller (or a sibling controller) its claim filter and envelope,
  and give it its own pod bound and sizing. Do not reuse the build
  class for a different cost profile.
- **Per-class bounds.** If classes diverge (e.g. multi-app solution
  deploys vs single-app publishes), split Max Isolated Pods per class
  instead of widening one bound for everyone.
- **Workflow lanes.** The intended evolution: workflow executions get
  lane placement driven by the now-trustworthy per-execution memory
  recording (failure samples forwarded, org routing fixed, peaks
  null-aware). Lanes need per-workflow p95 profiles first — do not build
  lane placement on pre-fix historical data.

## Non-goals (do not build these here)

- No per-feature job tables, workers, status endpoints, or polling loops
  — extend PlatformJob (see `platform-jobs.md`).
- No silent local fallback when Kubernetes is unavailable — placed work
  waits visibly and fails with a structured capacity error. (Turning
  isolation off in Settings is an admin's decision, not a fallback: only
  jobs that never got a pod move to workers.)
- No `multiprocessing.spawn` fallbacks, dead code, or "just in case"
  alternate paths.
- Do not lower production limits to idle measurements, and do not treat
  the elastic-runtime import diagnostic as live execution data.

## Evidence log

| Workload | Peak / delta | Envelope at the time | Verdict |
| --- | --- | --- | --- |
| App deploy + SDK rebuild (Kind, 2 parallel) | 614 MiB cgroup peak | 1 GiB request = limit | Isolated. Basis for the 2 GiB standard pod. |
| `workspace.reimport` (seeded: 3 workflows, 1 app, table, form) | 21 MiB delta, 2 s | local scheduler | Local. No compile step; scales with workspace size. |

## File map

| Concern | Location |
| --- | --- |
| Policy + execution class | `api/src/jobs/platform/base.py` |
| Backend selection at enqueue | `api/src/services/platform_jobs.py` |
| Settings → Kubernetes policy, import, placement | `api/src/services/kubernetes_execution.py` |
| Unread and renamed variable reports | `api/src/services/deployment_environment.py` |
| Settings API | `api/src/routers/kubernetes.py` |
| Settings UI | `client/src/pages/settings/KubernetesSettings.tsx` |
| Pod sizing (worker rule) | `api/src/services/worker_pool/sizing.py` |
| Claim boundary, leases, runner | `api/src/jobs/schedulers/platform_jobs.py` |
| Kubernetes controller | `api/src/jobs/schedulers/kubernetes_jobs.py` |
| Job manifest (request/limit, fencing) | `api/src/jobs/platform/kubernetes_client.py` |
| One-job pod supervisor | `api/src/jobs/platform/kubernetes_runner.py` |
| Job identity settings | `api/src/config.py` (Kubernetes Job identity section) |
| Opt-in overlay / local harness | `deploy/kubernetes/builds/`, `k8s/local/build-jobs.yaml` |
| Operator manual | `docs/runbooks/kubernetes-build-jobs.md` |
