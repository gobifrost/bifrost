# #769 handoff: from three PRs to one self-scaling worker (overnight plan)

Updated 2026-10-22, late. Read with `2026-10-22-769-stage2-durable-dispatch.md`,
`2026-10-22-769-stage3-reusable-workers.md` and
`2026-10-22-769-stages4-5-convergence.md`. The original handoff is PR #1030.

## Target (Jack, 2026-10-22)

- One worker image runs every kind of work. The API runs control (schedules
  and launching workers).
- On Kubernetes, workers scale on demand from **1 to 6** by default
  (`MIN=1`, `MAX=6`) and drain back to 1 when idle.
- The `scheduler` and `worker` services are equals: both run the same
  executor, so either can be torn down.
- RabbitMQ is removed. So are the transitional switches
  (`BIFROST_CONTROL_PLANE_HOST`, `BIFROST_EXECUTION_PLACEMENT`, the
  `api-control` overlay) and the bridge code.
- An install that changes nothing keeps working after upgrading.
  - Docker: its existing `scheduler` and `worker` services become two fixed
    executors (scale with `--scale`). Dynamic scaling is Kubernetes only.
  - Kubernetes: needs the API to be allowed to create Jobs. See decision D3.
- Subsecond workflow execution is kept. The warm floor (`MIN=1`, or a
  long-running worker) serves interactive runs; a cold Job worker takes
  about 12 s.

## Authority for this run (confirmed by Jack, 2026-10-22)

- **Merges.** Merge each PR in order through the merge queue
  (`gh pr merge <N>`, no strategy flag) once its gate below passes. A failed
  gate means a fix with a test; never a skip.
- **No waiting between steps.** Run straight through 5c. Restarts are safe:
  accepted work is durable, workers drain and leadership hands off. The only
  rule is that 5a and 5b merge after their upgrade rehearsal passes, never
  before.
- **Production.** Update production once a stage is fully tested locally.
  Kind runs the production image and manifests.
  - Production is Kubernetes, synced from GitOps by Flux. Change it through
    the GitOps repository.
  - After each rollout, run the read-only prod smoke test
    (`prod_smoke/prod_smoke.sh`).
  - Kind cannot cover three things, so check them on the first prod rollout:
    DigitalOcean node autoscaling, pulling the image from GHCR, and the
    Flux sync.
  - Jack's personal instance is available but not required.
- **Docker.** The expected setup is 1 scheduler + 1 worker. Unchanged, that
  becomes two identical executors. To scale, set replicas.
- **Kubernetes RBAC.** The base `k8s/` manifests give the API the
  Job-creating Role. It is the same namespace-scoped Role the
  `deploy/kubernetes/builds` runbook already grants the scheduler today:
  `jobs` create/patch/delete, `pods` get/list. The scheduler binding moves
  to the API, so an unchanged Kubernetes install scales after its next
  sync.

## Ground rules for every step

- Work in the stage worktrees under `.claude/worktrees/769-*`. Rebase on
  `origin/main` before each merge. After a base PR merges, retarget the next
  stacked PR to `main` and rebase it.
- Verify with the **debug stack** (`./debug.sh up` in the worktree): it
  runs the real services from source. Also verify the **production image**
  (`api/Dockerfile`) through Kind (`./test.sh kubernetes up`). Use
  `./test.sh` for unit and e2e.
- **Every-job checklist** (run in the mode under test; record results in
  the PR):
  1. All system schedules in `api/src/scheduler/registry.py` run and
     succeed. Check Diagnostics → Scheduler (every task has a successful
     last run), or query `scheduler_task_runs`.
  2. Platform jobs: one local job (e.g. file-index reconciliation) and one
     build-class job (App deploy). On Kind, the build-class job must run in
     a remote Job.
  3. Workflows:
     - sync, async, scheduled (deferred);
     - from a form, via an endpoint, as a data provider;
     - an SDK nested run, an inline-code run;
     - cancelling a pending run and a running run.
  4. Agents: API run, chat run, an event-triggered run, an agent tool that
     runs a workflow, and a delegated agent.
  5. Package install: install a package, then run a workflow that imports
     it on every live worker, including a newly started one (stage 4
     onwards).
  6. Latency: `scripts/kubernetes/sync_latency_bench.py`, run in an API container. The p50
     must stay within 10 ms of the broker baseline (about 47 ms) on the
     same stack.
- Never retry until green, skip, or extend timeouts. Every failure gets a
  cause.
- After each merge, comment on #769 and update the status table below.

## Sequence and gates

| # | Work | Gate before merge |
| --- | --- | --- |
| 1 | **#1031** (stage 1) | Every-job checklist with defaults, then with `BIFROST_CONTROL_PLANE_HOST=api`. Already CI green. |
| 2 | **#1032** (stage 2, all installs) | Retarget to `main` and rebase. Checklist with defaults. Rolling-upgrade check: an old-image worker plus the new API, which was done for e2e; repeat for the checklist. |
| 3 | **#1033** finish (3b) | Re-run the unschedulable drill (fix the harness to pick a Ready, non-terminating API pod after `rollout status`). Re-measure pool latency after the claim fold. Checklist in pool mode (Docker static executor; Kind on-demand). |
| 4a | **Stage 4, package installs** | Replace the RabbitMQ fanout with a desired package generation in PostgreSQL. Executors reach it before admitting work and reconcile it on heartbeat. Gate: checklist item 5, including a worker that was down during the install. |
| 4b | **Stage 4, summaries** | Live summaries and backfill become dispatch families, so a pending summary is never stranded. Remove the dead tuning-chat consumer and its queue. Gate: summaries complete in pool mode; the backfill PlatformJob completes. |
| 4c | **Stage 4, services and platform jobs** | The supervised-service claim loop and the local platform-job claim loops run inside the executor, so the `scheduler` and `worker` processes are identical. Gate: full checklist with both containers running the executor, then again with either one stopped. |
| 4d | **Stage 4, event deliveries** | The run is accepted inside the delivery's own transaction. Gate: event e2e plus a crash between commits (unit). |
| 5a | **Flip defaults** (the only PR that changes everyone) | Remove the two switches: the API always runs control and work always goes to executors. `src.worker.main` and `src.scheduler.main` both start the executor, so unchanged Compose files and manifests work. Defaults `MIN=1`, `MAX=6`. Gate: **upgrade rehearsal**. Start the previous release's images with the user's unchanged compose file and data, upgrade only the image tag, then run the full checklist. The same on Kind with the unchanged base manifests (plus D3). Then a rollback rehearsal. |
| 5b | **Retire RabbitMQ** | Remove consumers, publishers, topology, bridge code, the RabbitMQ health check, the test-stack and CI service, the KEDA ScaledObject, the worker-Deployment-only settings, and the `api-control` overlay. Tolerate a leftover `BIFROST_RABBITMQ_URL` or `rabbitmq` service in a user's compose (ignored, never fatal). Before removal, a startup check confirms no broker-placed dispatches remain. Document deleting the old queues and their dead-letter recovery. Gate: full checklist, the upgrade rehearsal again, and `grep -ri rabbit api/src` returns nothing but the migration note. |
| 5c | **Final proof** | Kind with unchanged base manifests: 1 warm worker scales to 6 under a burst, drains back to 1, and the checklist passes. Docker with the unchanged compose: the checklist passes on the two executors. |

Every PR before 5a is off by default or invisible, so `main` is safe at any
point. 5a and 5b merge only once their upgrade rehearsal passes; after that,
continue directly to 5c and the production rollout.

## Status

| Item | State |
| --- | --- |
| #1031 | Ready, CI green, not merged |
| #1032 | Draft, stacked on #1031 |
| #1033 | Draft, stacked on #1032. 3a proven in Kind and Docker; 3b: kill, controller-restart and OOM drills pass, unschedulable pending |
| Stages 4–5 | Planned (`2026-10-22-769-stages4-5-convergence.md`) |

## Evidence so far

These are the numbers to repeat or beat.

**Stage 1 (Kind, two API replicas):**
- leader SIGKILLed, takeover at lease expiry in 24–25 s;
- rolling restart with no gap;
- 95/95 scheduler e2e tests with API control.

**Stage 2:**
- broker down at acceptance, then the run completed;
- Redis `FLUSHALL` after acceptance, then the run completed;
- old and new workers together, 55/55;
- 440 execution, agent, chat and scenario e2e tests;
- +4–7 ms on a trivial sync run.

**Stage 3:**
- **Kind:**
  - zero to first worker: cold start about 13 s;
  - reuse: further runs on the same pod;
  - burst: grew to exactly the cap of 3;
  - idle: back to zero;
  - drain race: the run completed on a new worker.
- **Drills:**
  - worker killed mid-run: its runs failed at once ("executor lost"), not replayed;
  - every API pod deleted mid-burst: no duplicate Jobs;
  - run over its memory limit: failed visibly, the pool kept serving.
- **Docker pool mode:** 236 e2e tests.
- **Latency:** pool 55–57 ms p50 against broker 47 ms, before the claim
  fold.

## Tooling

- **Kind:**
  `BIFROST_KIND_BUILD_JOBS=1 BIFROST_KIND_API_CONTROL=1 BIFROST_KIND_POOL=1 ./test.sh kubernetes up`,
  then `pool-experiment`, `pool-drills` and `control-experiment`.
- **Docker pool mode:**
  1. Recreate `api` and `scheduler` with `BIFROST_EXECUTION_PLACEMENT=pool`
     and `--no-deps --force-recreate`.
  2. Stop `worker-1`.
  3. Start a static executor:
     `docker compose run -d -e BIFROST_WORKER_FORM=static worker python -m src.worker.pool_main`.
  4. Run e2e through `test-runner` with `-e BIFROST_EXECUTION_PLACEMENT=pool`,
     because `./test.sh` restarts the broker worker.
- **Latency:** `scripts/kubernetes/sync_latency_bench.py` (sync inline-code runs). Copy it into
  the API container and run it with `python /tmp/e2e.py 200`.
