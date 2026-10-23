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

## Next session (decided by Jack, 2026-10-23)

Work in this order. The authority above still applies (merge through the
queue once a gate passes; production through GitOps, which needs Jack's
merge because the auto-mode classifier blocks GitOps merges).

1. **Legacy broker drain** (branch `769-legacy-broker-drain`, work in
   progress). Released installs (v1.4.1 and earlier) can have runs queued in
   RabbitMQ at the moment they upgrade; nothing reads them after 5b.
   - **Behaviour Jack chose:** on start, when `BIFROST_RABBITMQ_URL` is set,
     try to connect a few times; if it connects, drain the old queues until
     empty, disconnect, and stop. No recurring poll, no lasting connection.
     If it never connects, stop.
   - **Already written** (`api/src/services/legacy_broker_drain.py`, 10
     passing tests): per-queue recovery for `workflow-executions` (rebuild
     from the Redis pending record; promoted scheduled runs get their
     dispatch; expired context fails a durable row visibly),
     `agent-runs` (queued row + Redis context → dispatch; expired → failed),
     `agent-summarization(-backfill)` (re-request; backfill membership kept);
     a message carrying `dispatch_id` is only acknowledged; a failure nacks
     with requeue and stops that queue's pass. `aio-pika>=9.6,<10` is back in
     `pyproject.toml` / `requirements.lock` for this module only, and
     `rabbitmq_url` is an optional setting again.
   - **To change:** it is currently registered as a 5-minute scheduler
     catalog task (`legacy_broker_drain` in `src/scheduler/registry.py`).
     Replace that with a one-shot startup routine as above (handlers are safe
     on several API replicas: each message goes to one consumer and dispatch
     inserts are unique per subject). Then update the three tests that the
     catalog entry broke (`test_control.py` catalog tests) and classify the
     module's Redis reads in `test_access_list.py`
     (`_REDIS_PLAIN_READS`/authority inventory).
   - **Gate:** a Docker rehearsal from v1.4.1 with work left in RabbitMQ
     (stop its scheduler and worker, submit async and scheduled runs, then
     upgrade only the image): every queued run completes. Then the
     release-notes line: the next release recovers work left queued by an
     earlier release; remove the broker once the API has drained it.
2. **Drop the broker-era dispatch schema now** (no release ever wrote it;
   only dev builds #1032–#1039 did). Remove `queue`, `status`,
   `publish_count`, `last_published_at` and `placement` from
   `work_dispatches`, the reconciler's "re-place a broker dispatch" branch,
   and the `execution_record_exists` message field; drop their allowlist
   entries in `test_builder_residue_migration_rehearsal.py`. Production must
   run the #1040 image (or later) before it takes this change.
3. **Kind pressure test** before production: a sustained ramp of hundreds of
   runs mixing sync, async, scheduled, agent, summary and event work;
   memory-heavy runs that drive the memory gate and hand-back; sync latency
   percentiles at the warm floor and the Job-count curve; API and executor
   kills under load. Record results here.
4. **Production** (each step a GitOps PR Jack merges, then `prod_smoke`; on
   the first rollout also check DigitalOcean node autoscaling, the GHCR pull
   and the Flux sync):
   1. The newest main image with manifests unchanged (with the drain, no
      stage-4 hop is needed; close gocovi/kubernetes#30).
   2. The 1+x model: give the API the worker-controller and build Roles;
      remove the scheduler Deployment (proves the platform runs without
      it); worker Deployment to 1 replica as the always-warm floor,
      `BIFROST_POOL_MIN_WORKERS=0`, `MAX=6` (Jack to confirm static floor vs
      a warm Job).
   3. Remove RabbitMQ (Deployment, PVC, secret keys) after the drain logs
      show it empty.
5. **Small leftovers:** the platform-job phase "Waiting for scheduler memory"
   should say executor; the Kind build experiment's `--restart-scheduler`
   restarts the wrong process now that the API runs the build controller.
6. **Resource requests** (discuss with Jack first; nothing is built): draft
   `docs/plans/2026-10-23-769-resource-requests-design.md` on local branch
   `769-resource-requests-design`. Open questions: one claim path or a
   shared ledger; CPU or memory only; what "isolated" includes; ordering of
   large requests.

Close #769 after production reaches the 1+x model.

