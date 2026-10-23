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
| #1031 | **Merged** 2026-10-23. Gate: runtime checklist in both control modes; 85/85 scheduler, scheduling and event e2e. |
| #1034 | **Merged** 2026-10-23. Found by the #1032 mixed-version check: the schema gate crashed an older image started against a newer database (alembic raises `ResolutionError`, not `CommandError`), breaking rolling deploys and rollback. A rollback is only safe to releases that contain this fix. |
| #1032 | **Merged** 2026-10-23. Gate: checklist with defaults; checklist with only a #1031-code worker behind the stage 2 API (15 runs on the old worker); CodeQL findings fixed. |
| #1033 | **Merged** 2026-10-23. Gate: all four Kind drills pass (unschedulable now starts from an empty pool and asserts the start deadline); checklist passes in Kind pool mode and Docker static-executor mode; latency at parity after the executor connection fix (below). |
| #1035–#1038 (4a–4d) | **Merged** 2026-10-23 in order. Gates: package generation with a frozen and a late executor; summaries 4/4 and backfills 4/4 in pool mode (a lost backfill-counter update fixed); checklist with both containers as executors and with each alone; event acceptance crash-before-commit tests plus event, scheduling and scenario e2e; a Kind pass of every stage-4 family on Job-form executors. |
| #1039 (5a) | **Merged** 2026-10-23. Gates: 2320 e2e; Docker upgrade from v1.4.1 with its unchanged compose file and seeded data, checklist green; Docker rollback to stage 4; Kind upgrade from stage-4 defaults (found and fixed: worker Jobs must copy the controller pod's `envFrom`) and rollback. Rollback targets must contain #1034. |
| #1040 (5b) | **Merged** 2026-10-23. RabbitMQ, KEDA and the Redis bridges retired; memory gating restored; executor drain fixed; init skips migrations on a newer database. Includes the 5c proof (below). |
| 5c proof | Kind with the base manifests' defaults: a 40-run burst scaled worker Jobs 1 → 6 → 1 (never past 6); kill, controller-restart, OOM and unschedulable drills pass. Docker v1.4.1 → 5b with its unchanged compose file passes on two executors. |
| Production | **Not rolled out.** Still on `1.4.2-dev.1792699618` (before #1031). gocovi/kubernetes#30 (stage-4 pin) is open and superseded by the drain below; close it once the drain ships. The nightly promotion (`bifrost-image-promotion`, midnight America/New_York) promotes the newest main build; Jack decides whether to suspend it until the drain is merged. |

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
- **Latency (2026-10-23, after the claim fold):**
  - Kind, same cluster: pool p50 50–53 ms, broker 50–53 ms.
  - Docker, same stack, 8 interleaved samples of 200 sync runs: pool p50
    median 58 ms, broker 60 ms. Before the fix pool was about 12 ms
    behind: the worker's 1 + 2 overflow database connections made every
    run reconnect (overflow connections are closed on return). The
    executor now pools its whole allowance; the connection cap is
    unchanged. The host was loaded (load average 7–13), so compare only
    interleaved samples.
- **Kind drills (2026-10-23):** kill: lost after 29 s, queued runs
  completed, interrupted runs failed and were not replayed;
  controller restart: peak 3 Jobs, no duplicates; OOM: failed visibly,
  pool kept serving; unschedulable: 2 workers lost at their start
  deadline, the run waited as Pending, within the ceiling, drained to
  zero after the settings were restored.

## Tooling

- **Runtime checklist:** `scripts/runtime_checklist.py`, run inside an API
  container: `docker cp` it in, then
  `docker exec -w /app <api> python /tmp/checklist.py > out.json`. Summarise
  with `python3 scripts/runtime_checklist_summary.py out.json`. On a debug
  stack, change a setting with `./debug.sh down`, then
  `SETTING=value BIFROST_FORCE_PORT=1 ./debug.sh up`, because `up` on a
  running stack is a no-op.

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

## Done 2026-10-23 (afternoon)

- **#1041 merged:** the legacy RabbitMQ drain. Each API replica drains the
  old queues once at start, until they stay empty for two minutes, then
  disconnects. v1.4.1-era runs get the lineage of the user they recorded
  (`shared.run_lineage.recorded_user_lineage`, Jack's choice); migration
  `20261023_legacy_run_lineage` backfills waiting rows. Gate: Docker v1.4.1
  upgrade with work stranded in RabbitMQ (kit `/tmp/769-drain-rehearsal`).
- **#1042 merged:** each worktree builds its own test API image
  (`BIFROST_TEST_IMAGE_TAG`).
- **#1043 merged:** the broker-era `work_dispatches` columns are dropped.
- **Stage 6 built** (branch `769-stage6-resource-requests`, pushed, no PR
  yet). Spec: `docs/plans/2026-10-23-769-stage6-resource-requests.md`.
  - 6a: dispatches carry a memory request; executors claim work that fits
    their free budget (no `max_workers`, `max_concurrency`,
    `pool_worker_slots`); learned per workflow from peak memory growth, reset
    on a code change, decaying 1/10; the controller sizes Jobs by memory and
    launches sized Jobs; work too large for any executor fails visibly;
    forked runs volunteer for the OOM killer; past 95% real memory the pool
    stops the run that grew most.
  - 6b: local platform jobs and services draw from the same budget (no
    platform-job slots, admission ratio or `max_service_workers`);
    Diagnostics shows platform jobs and memory instead of slots; declared
    `memory_request_mib` workflow setting (API, CLI, MCP, workflow dialog; not
    in the manifest, like `run_identity_id`).
  - Isolation (Jack): a property of a job definition ("runs in its own
    container"), never of a workflow run; build-class platform jobs are the
    first such jobs, on the existing build backend. Nothing new built.

## Next session

1. **Pressure test** (`./test.sh kubernetes pressure-test`, harness
   `scripts/kubernetes/pressure_test.py` + `pressure_driver.py`) on Kind with
   the stage-6 image. **First run (2026-10-23 ~15:00 ET): learning, sized and
   too-large phases passed; the understated phase failed** (log
   `/tmp/769-pressure-1.log`; the Kind cluster was left up for inspection).
   Two root causes, both confirmed:
   1. **Whole-pod OOM on Kubernetes.** The `scheduler` executor (1 GiB
      limit, 546 MiB budget) claimed five runs whose seeded learned value
      said 32 MiB but which allocated 200 MiB each within about a second; the
      container was OOMKilled (exit 137). Kubernetes 1.31 sets
      `memory.oom.group=1` on containers (verified in Kind; the Docker test
      stack shows 0), so the kernel kills every process in the container
      together and the runs' `oom_score_adj=1000` cannot save the executor
      there. It does work on Docker. The 1 s watchdog cannot beat a burst that
      fast. **Decision needed from Jack:** accept it (requests are measured,
      so only a sudden change in a workflow's memory does this) and tighten
      the watchdog (sample faster, act earlier), and/or add a rule for
      unmeasured or recently changed workflows (for example one run at a time
      per executor until a run is measured), and/or set the kubelet's
      `singleProcessOOMKill` on clusters that offer it (Kubernetes 1.32+).
      Note: runs lost with their executor record no growth, so learning does
      not see the spike that killed it.
   2. **Runs of a dead static executor stay Running.** After the restart the
      old static incarnation (`e752e0e2`) stayed `ready` with its heartbeat
      16 minutes stale; nothing failed its four started runs (only the Job
      controller marks workers lost and calls
      `worker_pool.recovery.end_runs_of_lost_worker`), so they wait for the
      stuck-execution sweep at their 30-minute timeout. **Fix (not yet
      written):** in the control plane (beside `_dispatch_reconcile_loop` in
      `src/scheduler/control.py`, so it runs on Docker too), mark any
      `ready`/`draining` executor of either form whose heartbeat is older
      than `HEARTBEAT_LOST_AFTER` (30 s) `lost` (`end_reason
      heartbeat_lost`, under SKIP LOCKED), then `end_runs_of_lost_worker` and
      `announce_ended_runs`. Unit test plus a Docker drill (kill the worker
      container mid-run).
   Then rerun the pressure test; it continues with the mixed ramp and the
   API and worker kills.
2. **Stage 6 PR.** Before opening: full unit suite and the e2e suites that
   touch execution, services, platform jobs and diagnostics
   (`./test.sh e2e` is the complete set); `./test.sh pr-preflight`. Then
   open the PR, add its Product Updates disposition (user-visible: executors
   size themselves by memory; Diagnostics; the memory setting) and queue it.
3. **Production** (each step a GitOps PR Jack merges, then `prod_smoke`; on
   the first rollout also check DigitalOcean node autoscaling, the GHCR pull
   and the Flux sync). The nightly promotion moves prod to newest main; the
   drain recovers RabbitMQ leftovers.
   1. Confirm the promotion's rollout, run `prod_smoke`, check the RabbitMQ
      queues are empty (restart the API Deployment if not), close
      gocovi/kubernetes#30.
   2. The 1+x model: give the API the worker-controller and build Roles;
      remove the scheduler Deployment; worker Deployment to 1 replica as the
      warm floor, `BIFROST_POOL_MIN_WORKERS=0`, `MAX=6` (Jack to confirm
      static floor vs a warm Job).
   3. Remove RabbitMQ (Deployment, PVC, secret keys).
4. **Leftovers:** the Kind build experiment's `--restart-scheduler` restarts
   the wrong process now that the API runs the build controller;
   `ExecutionWorker.slots` and `scheduler_replicas.job_slots` only serve
   older readers during a rollout and can go next release;
   `WorkflowExecutionRequest.code` is plain text though its description says
   base64.

Close #769 after production reaches the 1+x model.
