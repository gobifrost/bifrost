# #769 status and handoff (2026-10-22, end of session)

Read with: `2026-10-22-api-owned-unified-kubernetes-runtime.md` (original
handoff, PR #1030), `2026-10-22-769-stage2-durable-dispatch.md`,
`2026-10-22-769-stage3-reusable-workers.md`,
`2026-10-22-769-stages4-5-convergence.md`.

## PRs

Nothing is queued or set to auto-merge.

| PR | Stage | Base | State |
| --- | --- | --- | --- |
| #1031 | 1: API-hosted control (opt-in) | `main` | Ready. CI green: unit, e2e, CodeQL, lint, Product Updates. |
| #1032 | 2: durable acceptance, workflow + agent runs | #1031 | Draft. Stacked; retarget to `main` once #1031 merges. |
| #1033 | 3: pool executors + on-demand Kubernetes workers (opt-in) | #1032 | Draft. Stacked; 3b drills partly done (see below). |

Merge in order. Each PR is reviewable on its own diff.

## Why each PR is safe to merge: protecting existing environments

**#1031 (stage 1). Nothing changes unless an operator opts in.**
- `BIFROST_CONTROL_PLANE_HOST` defaults to `scheduler`, which runs exactly
  the old control loop. The schedule catalog that drives it is pinned by
  test to the previous schedule set.
- Opting in moves control into the API. Mixed versions cannot double-run
  triggers, because both hosts compete for the same `scheduler-triggers`
  lease.
- Schedule occurrences gained a unique key and a scan cursor, so failover
  neither drops nor duplicates them. Unkeyed events written by an older
  image during a rollout or rollback also count as fired.
- The migration is additive. Its unique index is built `CONCURRENTLY` and
  is partial, so existing rows are unaffected.
- Evidence:
  - Kind, two API replicas: leader process SIGKILLed, takeover at lease
    expiry (24–25 s); rolling restart with no gap; the scheduler never led.
  - Docker, API-hosted control: 95/95 scheduler e2e tests pass. Docker,
    mixed hosts: fenced failover.
  - Full unit suite 9,832 passed; CodeQL 0 on changed lines.

**#1032 (stage 2). Applies to every install. This is the one to review hardest.**
- Every workflow, inline-code and agent run is now accepted as a durable
  row plus a dispatch, committed before it is published.
- A Redis outage, a flushed Redis, or a broker failure no longer loses or
  fails an accepted run. The control plane republishes lost hand-offs and
  fails a run visibly once its delivery budget is spent. Started work is
  never replayed.
- Mixed versions are covered by a one-release bridge: an old worker served
  the new API alongside a new worker, 55/55 e2e. It also fixes a latent bug
  where the deferred promoter could strand a run forever after crashing at
  the wrong moment.
- Evidence:
  - Drills: broker down during acceptance, then completed after recovery;
    Redis `FLUSHALL` after acceptance, then completed.
  - 224 execution e2e tests and 216 agent, chat and scenario e2e tests.
  - Real-PostgreSQL tests of lock order, duplicates, cancel-before-claim
    and the reconciler.
- Cost: about +4–7 ms on a trivial sync run (39 → 43–46 ms p50). Workflow
  execution stays well subsecond.

**#1033 (stage 3). Nothing changes unless an operator opts in.**
- `BIFROST_EXECUTION_PLACEMENT` defaults to `broker`, which keeps the
  stage 2 path unchanged.
- The new tables are additive. The only default-visible change is one
  hourly maintenance task, listed in Diagnostics.

## Dynamic worker scaling now exists

- **0, 1 or more workers.** With `BIFROST_EXECUTION_PLACEMENT=pool`:
  - runs wait in PostgreSQL for whatever executors exist;
  - an executor is either a static container (Docker installs, warm
    capacity) or an on-demand Kubernetes Job that the API-hosted controller
    launches for demand;
  - Job workers are capped by `BIFROST_POOL_MAX_WORKERS`, kept warm by
    `BIFROST_POOL_MIN_WORKERS`, and drain to zero after
    `BIFROST_POOL_IDLE_SECONDS`.
  - Kubernetes node autoscaling reacts to their resource requests, the same
    model as today's dynamic build containers.
- **Proven in Kind** (twice, no broker workers):

  | Phase | Result |
  | --- | --- |
  | Zero | no worker pods |
  | First run | worker launched from zero; cold start 12.6–12.9 s |
  | Reuse | further runs on the same pod (3 runs in 3.5 s) |
  | Burst | 16 × 6 s runs used exactly the ceiling of 3 workers |
  | Idle | every worker drained itself back to zero (~49 s with a 45 s idle) |
  | Drain race | a run arriving while its worker drained still completed, on a new worker |

- **Failure drills in Kind:**
  - worker SIGKILLed mid-run: lost in 28.6 s, its runs ended at once with a
    clear error and never replayed, queued work moved to another worker;
  - every API pod deleted mid-burst: no duplicate Jobs, burst completed;
  - run exceeding pod memory: that run failed visibly, the pool kept
    serving.
- **Docker** (pool placement, one static executor, no broker worker):
  236 execution, agent, chat, event and scenario e2e tests passed.
- **Latency on the same stack:** broker 47 ms p50, pool 55–57 ms p50
  before folding the pool claim into one transaction (that fold is
  committed but not yet re-measured). A cold Job worker takes about 12 s,
  so the interactive path must keep warm capacity: a static executor or
  `POOL_MIN_WORKERS`. This is in the stage 3 and stage 4–5 plans.

## Next steps, in order

1. Re-run the unschedulable drill. It aborted on a harness race: it
   launched its run against an API pod that was being replaced mid-rollout
   (`kubectl exec` exit 137). Fix: select a Ready, non-terminating API pod
   after `rollout status`. Then record the 3b results in the stage 3 plan
   and #1033.
2. Re-measure pool latency after the claim fold:
   `/tmp/769-e2e-bench.py`, run in an API container, against a static
   executor started with
   `docker compose run -d -e BIFROST_WORKER_FORM=static worker python -m src.worker.pool_main`.
3. Stage 4 (`2026-10-22-769-stages4-5-convergence.md`): move summaries,
   package installs (as a desired package generation), supervised services
   and local platform jobs onto the executor, so the scheduler and worker
   containers become identical.
4. Stage 5: flip the defaults, drain, and retire RabbitMQ and the legacy
   objects with forward cleanup, so no dead resources remain.

## How to resume

- Worktrees: `.claude/worktrees/769-api-control-stage1`,
  `769-durable-dispatch-stage2`, `769-reusable-workers-stage3`. Each has its
  own test stack (`./test.sh stack status`).
- Kind pool mode:
  `BIFROST_KIND_BUILD_JOBS=1 BIFROST_KIND_API_CONTROL=1 BIFROST_KIND_POOL=1 ./test.sh kubernetes up`,
  then `./test.sh kubernetes pool-experiment` and `./test.sh kubernetes pool-drills`.
- Docker pool mode: recreate `api` and `scheduler` with
  `BIFROST_EXECUTION_PLACEMENT=pool`, stop `worker-1`, and start a static
  executor as in step 2. Run e2e through `test-runner` with
  `-e BIFROST_EXECUTION_PLACEMENT=pool`, because `./test.sh` restarts the
  broker worker.
