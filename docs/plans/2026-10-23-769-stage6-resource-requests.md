# #769 stage 6: work requests memory from an executor

Status: approved in direction by Jack (2026-10-23: "Generally agree with your
design done right"); this is the spec being built. It supersedes the draft
`2026-10-23-769-resource-requests-design.md` on the local
`769-resource-requests-design` branch.

## Why

Since stage 5 every container runs the same executor, but each kind of work
counts capacity its own way: `max_workers` forks, `max_concurrency` agent
runs, one live and one backfill summary, two platform-job slots with their
own memory ratios, service slots, and a controller that sizes Jobs by slots
while memory is what runs out. None of these limits know about the others,
and fixed counts cannot fit an install of unknown size.

Production, 7 days to 2026-10-23 (42,255 runs, workers 2Gi limit, about
700Mi idle): a workflow fork's unique memory is 31 MiB at the median, 116 at
p95, 234 at p99 and 407 at most; a few workflows peak at 370–410 MiB. Peak
concurrency was 45 runs on 4 workers, so the fixed cap of 10 forks, not
memory, is what limits workflows today.

## The rule

1. **Every unit of work carries a memory request**, decided once when the
   work is accepted.
2. **Every executor knows its budget**, measured at start and kept current:
   - limit: the container's cgroup memory limit, or, with no limit (Docker's
     default), the host's memory;
   - baseline: the executor's own memory once it is ready (parent process,
     fork template, services it already runs are counted as work);
   - budget = limit × `memory_pressure_threshold` (0.85) − baseline.
3. **An executor claims the oldest waiting work whose request fits** in its
   free budget (budget minus what the work it holds reserves), and also
   only while its real memory is under the threshold. A workflow run
   reserves the larger of its request and its live growth (sampled every
   250 ms). Once it holds two pieces of work, an executor waits for the last
   run to reach its child and 100 ms more before claiming again, so the
   real-memory check sees what the last start did (Temporal's
   `rampThrottle`). Every kind of work claims through one serialized gate
   (`src/worker/admission.py`): read the free budget, claim, hold.
4. **No count caps.** `max_workers`, `max_concurrency`, the summary slots,
   `pool_worker_slots` and the platform-job slots stop being capacity.
   Policy limits stay: per-type platform-job concurrency (one deploy per
   App) and resource locks.
5. **Only dangerous memory stops work.** A run may grow past its request;
   at 95% of the container's real memory the run or service that grew most
   is stopped. A run killed for memory (by that stop, by the kernel, or lost
   with a Job worker Kubernetes reports OOMKilled) teaches its workflow the
   executor's budget less the 16 MiB margin, so its next run fits an
   executor alone. Any other loss (a crash, a forced stop, a lost node)
   teaches nothing. The one case
   nothing catches, a never-measured burst that allocates within its first
   instant, costs at most one learning kill per new or changed workflow
   (accepted by Jack, 2026-10-23).
6. **The controller launches for work nothing can fit.** On Kubernetes:
   standard Job workers for work a standard worker fits, and a Job sized to
   the request when it is larger, up to `pool_worker_max_memory_mib` (local
   platform jobs waiting for memory count as demand too). Work no executor
   can ever fit fails visibly within seconds ("needs N MiB; the largest
   executor can hold M"): runs and platform jobs fail, services are blocked
   `too_large` until an executor can hold them. Work that merely waits is a
   metric (Diagnostics: runs waiting and the oldest wait) and fails only at
   its claim deadline. Workers drain themselves after inactivity; a sized
   Job is never the warm minimum.

## Requests are typical, not worst case

A request is what the work usually adds, so packing by requests never gives
workflows less room than today; the real-memory check catches the unusual
run.

- **Measured as peak growth.** A forked run's request is learned from its
  peak RSS minus its RSS at fork start (`ru_maxrss` is reset at fork, so this
  is the most memory the run itself added). Not RSS, which counts the shared
  template (about 55 MiB a fork), and not end-of-run PSS growth
  (`peak_memory_bytes`), which misses memory freed before the end.
- **Learned per workflow**, in the completion update the executor already
  makes: the workflow's learned peak rises at once to any larger run and
  otherwise moves 1/10 of the way down toward each smaller one (a decaying
  maximum, one primary-key update, no scan). Its request is that value plus
  a 16 MiB margin. Acceptance reads it with the workflow lookup it already
  does.
- **Declared when known**: a workflow's `memory_request_mib` setting
  overrides the learned value. Like `timeout_seconds`, they are
  operational settings set through the API, CLI, MCP and UI, never from the
  decorator.
- **New work starts small**: a workflow with no history requests 64 MiB.

## By kind of work

| Work | Runs in | Request |
| --- | --- | --- |
| Workflow run (any trigger: schedule, event, form, endpoint, SDK, agent tool) | a fork | declared, else learned per workflow, else 64 MiB |
| Scheduled run | same as any workflow run: a schedule is a trigger, not a kind of work | |
| Agent run | the executor process (async) | fixed 32 MiB; its tool calls are workflow runs with their own requests |
| Summary (live and backfill) | the executor process | fixed 16 MiB |
| Service | a long-lived fork | learned from the memory it reports, else 64 MiB; held for its lifetime |
| Local platform job | a runner subprocess (a fresh interpreter) | its learned profile, else the policy's `min_memory_headroom_mb` (what admission uses today) |
| Build-class platform job (deploy, SDK update, export) | its own Job on Kubernetes | as above |

Agents are low by design: the heavy work they do is workflow runs, which
are admitted on their own. A fixed request still counts them, so a burst of
agent runs cannot pile onto one executor unseen.

## Isolation

Isolation is a property of a job definition, not of a run: "this job runs
in its own container". Build-class platform jobs are the first such jobs
(the Kubernetes build backend gives each one its own Job); code-editing
containers would be the next. Workflows and agents never ask for it; their
capacity is dynamic workers the controller starts and drains. Installs that
cannot start containers (Docker) cannot run isolated jobs; build-class jobs
there run in the local runner, as today. Folding the build backend into the
worker controller is a later change, made when the next isolated job type
arrives.

## Lost executors

An executor heartbeats every 5 s with the database's clock. It is lost once
it has not heartbeated for 30 s while the database was reachable: each
control loop (the Job controller; the dispatch reconciler for static
executors) judges that only after it has itself reached the database for
30 s without a break, so an outage never marks every executor lost. A lost
executor's runs fail visibly and are never replayed. Results are written
only by the executor that still holds the run's claim, so a lost executor's
late result never overwrites the loss; an executor told it is lost stops its
runs instead of draining. Resilient Workflows retries (after #769) build on
this fencing.

## Fairness

Oldest first among work that fits. When the oldest waiting request has not
fit for 30 seconds, executors whose budget could hold it stop claiming newer
work until it fits, and on Kubernetes the controller launches a worker sized
for it. Large work never starves behind a stream of small runs.

## Phases

- **6a, pool work.** Request columns on dispatches; budget, reserved and
  largest-free on `execution_workers` heartbeats; claim by fit; learned
  workflow requests; the controller sizes by memory and launches sized Jobs;
  work no executor can ever fit fails visibly;
  remove `max_workers`, `max_concurrency`, the summary slots and
  `pool_worker_slots` as capacity (leftover settings are ignored, never
  fatal).
- **6b, the rest.** The declared `memory_request_mib` workflow setting.
  Local platform jobs and services draw from the same
  budget (the two platform-job slots, the separate admission ratio and
  `max_service_workers` go).
- **6c, proof.** The Kind pressure test (handoff step 3) against 6a + 6b.

## Compatibility

- Expand-only migrations. During a rolling upgrade an older executor claims
  by slots and ignores requests; a newer one claims by fit. Both are fenced
  by the same claim, so neither double-runs work.
- Settings that stop meaning capacity are removed. A leftover
  `BIFROST_MAX_WORKERS` or similar in someone's environment is ignored
  (settings ignore unknown variables), never fatal.
- Platform jobs keep their own table and claim query (their status,
  progress and cancellation contract is unchanged); they share the
  executor's budget rather than moving into `work_dispatches`.
- Docker installs need nothing: two static executors size themselves from
  the host.
