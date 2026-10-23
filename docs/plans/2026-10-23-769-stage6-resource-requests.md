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

1. **Every unit of work carries a memory request** and an `isolated` flag,
   decided once when the work is accepted.
2. **Every executor knows its budget**, measured at start and kept current:
   - limit: the container's cgroup memory limit, or, with no limit (Docker's
     default), the host's memory;
   - baseline: the executor's own memory once it is ready (parent process,
     fork template, services it already runs are counted as work);
   - budget = limit × `memory_pressure_threshold` (0.85) − baseline.
3. **An executor claims the oldest waiting work whose request fits** in its
   free budget (budget minus the requests of the work it holds), and also
   only while its real memory is under the threshold. The request is held
   from claim to finish. One budget covers every kind of work.
4. **No count caps.** `max_workers`, `max_concurrency`, the summary slots,
   `pool_worker_slots` and the platform-job slots stop being capacity.
   Policy limits stay: per-type platform-job concurrency (one deploy per
   App) and resource locks.
5. **The kernel stays the backstop.** A run that outgrows its request is not
   stopped for that; only real pressure (the existing hard-limit and OOM
   handling) ends work.
6. **The controller launches for work nothing can fit.** On Kubernetes:
   standard Job workers for work a standard worker fits; a Job sized to the
   request when it is larger, up to `pool_worker_max_memory_mib`; a one-shot
   Job when `isolated` is set. Without Kubernetes, work that no executor can
   ever fit fails visibly ("needs N MiB; the largest executor has M").

## Requests are typical, not worst case

A request is what the work usually adds, so packing by requests never gives
workflows less room than today; the real-memory check catches the unusual
run.

- **Measured as peak growth.** A forked run's request is learned from its
  peak RSS minus its RSS at fork start (`ru_maxrss` is reset at fork, so this
  is the most memory the run itself added). Not RSS, which counts the shared
  template (about 55 MiB a fork), and not end-of-run PSS growth
  (`peak_memory_bytes`), which misses memory freed before the end.
- **Learned per workflow**, off the hot path: when a run completes, the
  executor recomputes the workflow's request as the 90th percentile of its
  last 50 measured runs plus a 16 MiB margin, and stores it on the workflow
  row. Acceptance reads it with the workflow lookup it already does.
- **Declared when known**: `@workflow(memory_mib=..., isolated=...)` overrides
  the learned value.
- **New work starts small**: a workflow with no history requests 64 MiB.

## By kind of work

| Work | Runs in | Request | Isolated |
| --- | --- | --- | --- |
| Workflow run (any trigger: schedule, event, form, endpoint, SDK, agent tool) | a fork | learned per workflow, else declared, else 64 MiB | when declared |
| Scheduled run | same as any workflow run; a schedule is a trigger, not a kind of work | | |
| Agent run | the executor process (async) | fixed 32 MiB; its tool calls are workflow runs with their own requests | no |
| Summary (live and backfill) | the executor process | fixed 16 MiB | no |
| Service | a long-lived fork | learned from the memory it reports, else 64 MiB; held for its lifetime | no |
| Local platform job | a runner subprocess (a fresh interpreter) | its learned profile, else the policy's `min_memory_headroom_mb` (what admission uses today) | no |
| Build-class platform job (deploy, SDK update, export) | its own Job on Kubernetes | as above | yes, on Kubernetes |

Agents are low by design: the heavy work they do is workflow runs, which
are admitted on their own. A fixed request still counts them, so a burst of
agent runs cannot pile onto one executor unseen.

## Fairness

Oldest first among work that fits. When the oldest waiting request has not
fit for 30 seconds, executors whose budget could hold it stop claiming newer
work until it fits, and on Kubernetes the controller launches a worker sized
for it. Large work never starves behind a stream of small runs.

## Phases

- **6a, pool work.** Request columns on dispatches; budget, reserved and
  largest-free on `execution_workers` heartbeats; claim by fit; learned
  workflow requests; the controller sizes by memory and launches sized Jobs;
  remove `max_workers`, `max_concurrency`, the summary slots and
  `pool_worker_slots` as capacity (leftover settings are ignored, never
  fatal).
- **6b, the rest.** Local platform jobs and services draw from the same
  budget (the two platform-job slots and the separate admission ratio go);
  build-class platform jobs are `isolated`, replacing the three-way
  build-backend opt-in where the API may create Jobs.
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
