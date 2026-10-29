# #1052: replace worker Jobs running an older image

Design for issue #1052, written 2026-10-24 after #769 reached production.

## Problem

After an upgrade on Kubernetes, worker Jobs started before it keep running
the old image. Extra Jobs retire after `pool_idle_seconds` (5 min), but the
warm minimum retires only at `pool_max_lifetime_seconds` (6 h). Until then,
old executor code runs against the new schema and API. In production on
2026-10-24 the warm Job had to be drained by hand
(`begin_drain(incarnation, "rotated")`), which community installs cannot do.

## Rules

Revised after Fable's design review (2026-10-24). The first draft compared
image strings and ordered controllers by start time. That misses `:latest`
installs, where an upgrade keeps the same image string. It also stamped the
image when the row was reserved, but another replica could create the Job
from its own image. And an old controller that started late rolled the pool
back.

### 1. Which Jobs are outdated

- **The build is the version, as the worker reports it.** Every published
  image sets its own `BIFROST_VERSION` (dev builds and releases, including
  `:latest`). A worker writes `get_version()` to its row when it is
  admitted (`execution_workers.version`). That is the code actually running,
  whichever node cache or tag supplied it. Static executors stamp it too, for
  Diagnostics, but they are never replaced.
- **The launcher is the controller that created the Job.** The row records
  the controller instance that created the Job
  (`execution_workers.launched_by`), in the same commit that binds the Job
  UID at `reserved → starting`. The replica that only reserved the row is
  not recorded. A controller that adopts a Job created by an instance that
  died before binding it records no launcher.
- **Live controllers are published.** Each controller instance (one id per
  loop start) writes its version to a Redis hash on every pass and removes
  itself when it stops. An instance is live while it wrote within
  `HEARTBEAT_LOST_AFTER` (30 s), measured on Redis's clock.
- **A Job is outdated when it is admitted, no live controller runs its
  version, and the controller that launched it is no longer live.** Two
  things follow:
  - A row with no version or launcher came from a release before this rule,
    so it is replaced once its launchers are gone.
  - A reserved or starting Job is never outdated: its version is unknown
    until it is admitted.

What this gives:

- **Restart on the same build:** nothing is replaced, because a live
  controller runs that version.
- **Rolling upgrade:** while an old replica lives, its version is live and
  nothing is replaced. Once the last old replica stops (a rolling update
  stops old pods only after new ones are ready), the old Jobs are replaced.
  There is no ordering, no clock, and no ping-pong.
- **Rollback:** symmetric.
- **A permanently mixed install** (for example, a pinned scheduler
  container running an old controller) never rotates. It does not
  oscillate.
- **No replacement loop.** A Job launched by a live controller is never
  outdated, even if it reports another version, for example because an
  explicit `BIFROST_KUBERNETES_BUILD_IMAGE` differs from the API's image. It
  is replaced at most once, after that controller stops.
- **Pull policy:** worker Jobs copy the controller container's
  `imagePullPolicy` when the image comes from the controller's own pod.
  On `:latest` that is `Always`, so a replacement never starts from a stale
  node cache. With explicit build settings, it stays `IfNotPresent`.
- **Limit:** images built without `BIFROST_VERSION` all report `unknown`
  and are never told apart. Local builds are the only case.

### 2. Launch the replacement first

In `launch_for_demand`, outdated Jobs no longer count toward the warm
minimum (`undrained`). The warm minimum therefore launches current-image
replacements. Outdated Jobs that are still ready keep counting as free
capacity for placing waiting work: they still claim until they drain. All
Jobs, including outdated ones, still count toward `pool_max_workers`.

### 3. Drain once the replacements hold the warm minimum

The new `replace_outdated()` step runs in each reconcile pass after
`launch_for_demand`. Under the capacity lock (try-lock, the same one used
by launches and idle drains):

- If the ready, current standard Jobs that have measured their budget
  (their pool is up, so they can claim) number at least
  `pool_min_workers`, move every **ready** outdated Job to `draining` with
  `end_reason = "rotated"`. All of them drain in one pass. Draining in
  smaller batches would not avoid a capacity dip under heavy load, because
  draining Jobs still count toward `pool_max_workers`.
- Skip a Job that still holds `reserved_for_dispatch_id`: it was launched
  for one large dispatch it has not claimed yet.
- Otherwise do nothing yet.

The controller never deletes the Job: a missing Job is treated as lost, and
its runs would fail. A drained worker sees `draining` on its next heartbeat
(within 5 s), stops claiming, lets its runs finish, retires, and exits 0.

Consequences:

- **Interactive runs always find warm capacity.** The old warm Job serves
  until its replacement is ready (cold start is about 13 s).
- **A broken new image never takes down the working one.** If the
  replacement never becomes ready, it is lost at its start deadline and
  relaunched, and the outdated Job keeps serving.
- **`pool_min_workers = 0` (scale to zero):** outdated Jobs drain at once,
  and demand launches current Jobs.
- **Outdated Jobs that are still starting** (launched just before the
  upgrade) are drained on the first pass after they are admitted.
- **The current Job may idle-drain first.** If the current Job reaches its
  idle drain before the outdated one is rotated (both API pods restarting),
  the warm minimum relaunches. That costs one extra Job and then converges.
- **Edge case:** with `pool_max_workers` outdated Jobs and no room, no
  replacement can launch. Outdated Jobs above the warm minimum still
  idle-drain once the load drops, which frees room, and the replacement
  follows. Under load that never drops, `max_lifetime` bounds it, as it
  does today.

### 4. A drain lets runs finish

Today `PoolWorker._drain` gives every drain a 300 s grace, and any workflow
still running after that is recorded as interrupted by shutdown. A rotated
(or `max_lifetime`) drain would therefore fail runs longer than 5 min,
which defeats "drain so its runs finish".

New rule:

- A drain the control plane asks for (`rotated`, `max_lifetime`, `idle`)
  waits for runs and platform jobs with no deadline. Each run still ends at
  its own timeout.
- Only SIGTERM starts the 300 s grace, which matches the pod's
  `terminationGracePeriodSeconds` of 360. A separate `_terminating` event,
  set only by the signal handler, starts it whenever SIGTERM arrives,
  including partway through a drain. The bounded phase
  (`drain_active_executions`) begins only then.
- An executor declared lost still has no grace.
- A run that never ends (`timeout_seconds = 0`, or a local platform job
  with no maximum) keeps a drained Job alive and counting toward
  `pool_max_workers`. The controller logs a warning once per worker when a
  draining Job with active runs passes `pool_max_lifetime_seconds`.

**First rollout of this release (production and community):** controllers
from earlier releases never publish themselves, and their Jobs carry no
version or launcher. The first new controller therefore rotates them during
the rollout, not after the last old replica stops. That is benign: the
replacement runs the new code. But the Job being replaced runs the old code,
so it still has the 300 s grace. Check that no long run is on the warm Job
before promoting.

### 5. Static executors register `starting` (leftover m2)

- `register_static_worker` inserts `state = "starting"` with no budget.
  The early measurement goes away; `slots` is written as 0, as Jobs already
  do.
- `heartbeat_worker` also renews `starting` rows, and the heartbeat loop
  does not stop on `starting`.
- After `_start_executor` measures the budget, a new `mark_static_ready`
  flips the row to `ready`.
- `end_silent_static_executors` also covers `starting`, so a static
  executor that dies while starting is lost.
- Its supervised services start inside `_start_executor`, so a `starting`
  executor can already hold services. That is harmless: services hold
  their own memory.
- **Rollout note:** an older controller counts a `starting` static row as
  an expected Job budget, so it may launch too few Jobs during the overlap.
  This only affects installs with static executors and a Job controller;
  production has none.
- In `launch_for_demand`, only Job rows count an expected budget while
  `reserved` or `starting`. A static row counts only once it is ready and
  measured. `largest_budget` already ignores a NULL budget.

### 6. Other leftovers

- **Kind build experiment:** rename `--restart-scheduler` to
  `--restart-controller`. On build overlap it deletes the API pod that is
  not running the workload, and it samples memory on API pods instead of
  the removed scheduler. Update `k8s/local/README.md` and the builds
  runbook to match.
- **`WorkflowExecutionRequest.code`:** describe it as plain-text Python, not
  base64. Refresh the contract fingerprint if the description is part of it.
- **Drop `ExecutionWorker.slots` and `scheduler_replicas.job_slots`:**
  deferred. No release has shipped since v1.4.1. v1.4.1 still writes and
  reads `job_slots`, and an executor from the current image reads `slots`
  when it is admitted, so dropping either now breaks rolling upgrades,
  which is the window these columns exist for. A new issue tracks dropping
  them in the release after the next one.

## Tests

- **Unit tests:**
  - the outdated predicate: a live version, a live launcher, unknown
    values, and a not-yet-admitted Job;
  - the version stamped at admission and the launcher stamped at Job
    creation;
  - live controllers published to and expired from Redis;
  - `launch_for_demand` excludes outdated Jobs from the warm minimum but
    keeps them in the `pool_max_workers` count;
  - `replace_outdated` drains only once the current Jobs hold the warm
    minimum, drains only ready Jobs, skips Jobs reserved for a dispatch,
    and never deletes a Job;
  - `pool_min_workers = 0`;
  - the current Job idle-draining first converges;
  - the pull policy is copied;
  - static registration goes `starting` → `ready` and heartbeats while
    starting;
  - the drain waits with no deadline without SIGTERM, and SIGTERM arriving
    mid-drain starts the bounded grace.
- **Migration:** adds `execution_workers.version` and `launched_by`.
- **Kind drill (`pool-drills --only upgrade`), run before merge:**
  - **Same-tag upgrade:** the warm Job runs a 400 s workflow (past the
    300 s grace). Rebuild the image under the same tag with a new
    `BIFROST_VERSION`, then restart the API Deployment. Expect:
    - exactly one replacement on the new version becomes ready before the
      old Job drains as `rotated`;
    - a sync run during the rotation is served;
    - the long run succeeds and the old Job retires;
    - no `lost` rows, and each row's version matches what its pod runs.
  - **Rollback:** restore the previous image. The pool rotates back once,
    with no churn.
