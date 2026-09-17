# Elastic execution engine

The elastic execution engine runs selected heavy PlatformJobs in temporary
Kubernetes pods with their own resource envelope, while the scheduler stays
warm for lightweight work. This document defines the engine's scope and the
contracts a future agent must follow to use or expand it — including the
planned future step of workflow lanes.

`docs/architecture/platform-jobs.md` remains the authority on the durable
job system itself. `docs/runbooks/kubernetes-build-jobs.md` is the operator
manual. This document is the bridge: what the engine is, what it is not,
and exactly how to extend it.

## Scope (what the engine is)

- **One engine, two backends.** Every PlatformJob is durably placed at
  enqueue time as `local` or `kubernetes` (`execution_backend` on the row).
  Local jobs run in scheduler child processes; Kubernetes jobs run in
  one-shot pods built from the same backend image. Placement is persisted
  so replicas never disagree after restart or config change.
- **Classification is owned by Bifrost, not operators.** A job type opts
  into remote execution by setting `execution_class="build"` in its
  `PlatformJobPolicy`. Product users then opt individual types in or out
  through Settings → Kubernetes → Executions (persisted in `system_configs`,
  defaulting to the measured high-memory jobs). Operators only tune the
  envelope (request/limit, concurrency, deadlines) and turn the backend on
  or off. Operators never decide *which* jobs go remote; the UI never
  decides *how much* a pod may use.
- **Three gates must agree for a pod to launch:** Bifrost classification
  (build class) **and** operator ceiling (backend flag + deployment
  allowlist `BIFROST_KUBERNETES_BUILD_JOB_TYPES`) **and** product opt-in
  (Settings toggle). The deployment allowlist is a ceiling, not a UI:
  a type omitted there stays local no matter what the toggle says.
- **Current remote set is exactly two job types:**
  `application.deploy` and `application.sdk_update`. Both compile code
  (npm + vite) in subprocesses. Everything else runs local.
- **Measured evidence decides membership.** A job moves remote only after
  a recorded cgroup working-set measurement justifies it:
  - App deploy / SDK rebuild: ~614 MiB peak observed live in Kind.
  - `workspace.reimport`: ~21 MiB delta over a small seeded workspace
    (3 workflows + 1 app + table + form, 2 s wall;
    `api/tests/e2e/platform/test_workspace_reimport_memory.py`).
    Verdict: stays local. No compile step exists in its path, and cost
    scales with workspace size, not with a build spike.

## Concepts (the five knobs)

1. **`execution_class`** (`PlatformJobPolicy`, `api/src/jobs/platform/base.py`).
   `"default"` or `"build"`. The only value the Kubernetes controller
   admits today. New classes (e.g. a future `"workflow"`) are added here.
2. **Backend placement** (`api/src/services/platform_jobs.py`, enqueue).
   `platform_build_backend=kubernetes` routes `execution_class="build"`
   jobs to `execution_backend="kubernetes"`. Default stays `local`.
3. **Resource envelope** (`BIFROST_KUBERNETES_BUILD_MEMORY_REQUEST_MIB`,
   `..._MEMORY_LIMIT_MIB`, `..._CPU_REQUEST`, `..._CPU_LIMIT`).
   Request = guaranteed reservation for node packing; limit = OOM ceiling.
   Request < limit means Burstable pods (dense packing, evictable under
   node pressure). Admission checks the job's learned
   `memory_required_bytes` against the **limit**, not the request.
4. **Which types may go remote** (`BIFROST_KUBERNETES_BUILD_JOB_TYPES`,
   default `application.deploy,application.sdk_update`). The operator-side
   ceiling: a build-class job launches a pod only if it is named here.
   Product users refine further in Settings (see 7).
5. **Concurrency, two layers.** Every build-class job type defaults to
   `max_concurrency=1` (pinned by test — parallelism is never accidental).
   `BIFROST_KUBERNETES_BUILD_MAX_JOBS` separately bounds concurrent
   *remote* attempts, independent of local scheduler slots. Platform
   admins can override per-type concurrency in Settings → Kubernetes →
   Executions (persisted in `system_configs`, 1–32, empty resets to the
   code default); the override replaces the policy default in the shared
   claim path, so it governs local and remote runs alike.
5. **Controller ownership** (`api/src/jobs/schedulers/kubernetes_jobs.py`).
   One deterministic suspended Job per claimed attempt; UID binding;
   first-pod-wins admission; no silent local fallback; terminal cleanup.
   PostgreSQL is status/progress/result truth; Kubernetes is capacity.
6. **Operator visibility.** `execution_backend` rides the shared
   `PlatformJobPublic` contract, and Diagnostics → Scheduler shows a
   "Kubernetes" badge on remote jobs. No separate dashboard; pod-level
   detail stays in kubectl.
7. **Product opt-in.** Settings → Kubernetes → Executions lists every
   build-class job type with toggles, persisted in `system_configs` and
   defaulting to the measured high-memory jobs. The tab appears only when
   the deployment configured the backend. Future K8s options (e.g.
   built-in monitoring) grow as sections under the same tab.

## How to add a job class to remote execution

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
3. **Confirm the envelope fits.** Learned `memory_required_bytes` must
   sit under the memory limit with margin; raise the limit setting if
   the evidence says so, and note it in the runbook.
4. **Pilot through the allowlist and the UI.** Add the job type to
   `BIFROST_KUBERNETES_BUILD_JOB_TYPES` in one environment first, then
   enable it in Settings → Kubernetes → Executions. It stays local
   everywhere the toggle is off, and everywhere the deployment omits it,
   until the pilot proves out.
5. **Cover the paths.** Backend selection, claim exclusion, controller
   create/restart/cancel/OOM for the new type, plus a live run while a
   light local job succeeds (the scheduler must stay responsive).
6. **Document.** Add the type to the remote set above and to the
   runbook's scope section. Link the measurement.

## How to extend the engine (future work, not implemented)

- **New execution classes.** Add the literal in `base.py`, teach the
  controller (or a sibling controller) its claim filter and envelope,
  and give it its own `MAX_JOBS` + request/limit settings. Do not reuse
  the build envelope for a different cost profile.
- **Per-class envelopes.** If classes diverge (e.g. multi-app solution
  deploys vs single-app publishes), split settings per class instead of
  widening one envelope for everyone.
- **Workflow lanes.** The intended evolution: workflow executions get
  lane placement driven by the now-trustworthy per-execution memory
  recording (failure samples forwarded, org routing fixed, peaks
  null-aware). Lanes need per-workflow p95 profiles first — do not build
  lane placement on pre-fix historical data.
- **Product toggle.** A future Bifrost setting may enable/disable
  dedicated execution when the deployment advertises the capability.
  Until then, the deployment config backend flag is the only switch.

## Non-goals (do not build these here)

- No per-feature job tables, workers, status endpoints, or polling loops
  — extend PlatformJob (see `platform-jobs.md`).
- No silent local fallback when Kubernetes is unavailable — work waits
  visibly and fails with a structured capacity error.
- No `multiprocessing.spawn` fallbacks, dead code, or "just in case"
  alternate paths.
- Do not lower production limits to idle measurements, and do not treat
  the elastic-runtime import diagnostic as live execution data.

## Evidence log

| Workload | Peak / delta | Envelope at the time | Verdict |
| --- | --- | --- | --- |
| App deploy + SDK rebuild (Kind, 2 parallel) | 614 MiB cgroup peak | 1 GiB request = limit | Remote. Basis for 2 GiB limit. |
| `workspace.reimport` (seeded: 3 workflows, 1 app, table, form) | 21 MiB delta, 2 s | local scheduler | Local. No compile step; scales with workspace size. |

## File map

| Concern | Location |
| --- | --- |
| Policy + execution class | `api/src/jobs/platform/base.py` |
| Backend selection at enqueue | `api/src/services/platform_jobs.py` |
| Product opt-in + deployment gate | `api/src/services/kubernetes_execution.py` |
| Settings API | `api/src/routers/kubernetes.py` |
| Settings UI | `client/src/pages/settings/KubernetesExecutions.tsx` |
| Claim boundary, leases, runner | `api/src/jobs/schedulers/platform_jobs.py` |
| Kubernetes controller | `api/src/jobs/schedulers/kubernetes_jobs.py` |
| Job manifest (request/limit, fencing) | `api/src/jobs/platform/kubernetes_client.py` |
| One-job pod supervisor | `api/src/jobs/platform/kubernetes_runner.py` |
| Deployment settings | `api/src/config.py` (build-jobs section) |
| Opt-in overlay / local harness | `deploy/kubernetes/builds/`, `k8s/local/build-jobs.yaml` |
| Operator manual | `docs/runbooks/kubernetes-build-jobs.md` |
