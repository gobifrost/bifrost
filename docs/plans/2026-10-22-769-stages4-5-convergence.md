# #769 stages 4–5: converge on one executor, then retire the old runtime

Status: planned. Parent plan: `docs/plans/2026-10-22-api-owned-unified-kubernetes-runtime.md`
(PR #1030). Builds on stage 1 (#1031), stage 2 (#1032) and stage 3 (#1033).

## Requirements (2026-10-22 review)

- **Upgrades never break an environment.**
  - Every release is safe with no operator action.
  - New paths ship opt-in, and become the default only when they match the
    old path.
  - A rollback restores a compatible pair.
- **The unified system takes over.**
  - When stage 5 is done, the `scheduler` and `worker` services run the
    same executor and the API hosts control.
  - Removing either service, or scaling it to zero on Kubernetes, loses
    nothing.
- **No dead resources.**
  - Nothing the old runtime created is left behind: queues, exchanges,
    Deployments, KEDA objects, RBAC, rows, keys.
  - Removal happens through forward migrations, startup reconciliation, or
    documented manifest steps.
- **Subsecond workflow execution is kept.** The interactive path always has
  warm capacity: a static executor or the warm minimum.

## Stage 4: every family on the executor

| Work | Today | Stage 4 |
| --- | --- | --- |
| Workflow and agent runs | Broker or pool (stages 2–3) | Unchanged |
| Live summaries, summary backfill | RabbitMQ consumers, fire-and-forget publish | Dispatch families claimed by executors; summary acceptance becomes durable (a `pending` summary is never stranded) |
| Agent tuning chat | Consumer with no publisher (dead) | Removed, with its queue declaration |
| Package installs | RabbitMQ fanout to each live worker; missed by any worker that is down | A **desired package generation** in PostgreSQL. Executors reconcile it on heartbeat (install, then recycle the process pool) and reach it before admitting work at boot. A cold worker never runs with stale packages. |
| Supervised services | Claim loop inside the broker worker | Same claim loop inside the executor |
| Local platform jobs | Scheduler-only claim loops | Executors claim platform jobs with the same lease protocol, so the scheduler container no longer differs from the worker |
| Event deliveries | Run durable; `QUEUED` committed by the caller later | The run is accepted inside the delivery's own transaction |

**Gate:**
- Each family is accepted, executed and recovered on a pool executor in
  Docker (static) and in Kind (Job).
- A new cold worker runs a workflow that imports a package installed while
  it did not exist.
- Package generation converges after a worker misses the change.

## Stage 5: flip, drain, retire

1. **Flip the defaults** (one release):
   - `BIFROST_CONTROL_PLANE_HOST=api` and `BIFROST_EXECUTION_PLACEMENT=pool`;
   - the compose `scheduler` and `worker` commands both run the static
     executor;
   - existing Kubernetes installs take the `api-control` overlay.

   Mixed versions stay safe:
   - the lease is shared;
   - dispatches keep their placement;
   - the one-release bridges are still present.
2. **Drain, then disable the old authority once:**
   - a startup check waits for broker-placed dispatches and RabbitMQ queues
     to empty, and reports anything stuck;
   - dead-letter contents are preserved, with their operator recovery path
     documented.
3. **Retire** (the following release):
   - remove RabbitMQ consumers, publishers and topology, and the
     Redis-first bridge code;
   - remove the worker Deployment, the KEDA ScaledObject and the
     scheduler-only RBAC, with documented manifest steps and a startup
     check that names any leftover object it finds;
   - delete RabbitMQ queues and exchanges in a one-time cleanup,
     including `agent-tuning-chat` (no consumer since stage 4b);
   - make RabbitMQ an optional dependency, then drop it from compose and
     the manifests.

**Removed at the end (transitional only):**
- `BIFROST_CONTROL_PLANE_HOST` and `BIFROST_EXECUTION_PLACEMENT`;
- the `deploy/kubernetes/api-control` overlay;
- the Redis pending-record bridge and the legacy message paths.

The capacity settings stay, renamed to read naturally (for example
`BIFROST_WORKERS_MIN=1`, `BIFROST_WORKERS_MAX=6`, the idle timeout, and
worker CPU and memory). On-demand workers turn on when Kubernetes Job
permissions are detected; no flag is needed.

**Gate:**
- Every scheduler catalog entry and every legacy consumer family is mapped
  to a tested owner.
- An upgrade rehearsal from the release before the flip and a rollback
  rehearsal both pass.
- A Docker install upgraded in place keeps its existing services working.
- After retirement no RabbitMQ object, legacy Deployment or bridge key
  remains.

## Stage 5a design (2026-10-23, from the code and production)

Production (GitOps `gocovi/kubernetes`, Flux) runs API ×2 on the default
service account, scheduler ×1 on `bifrost-build-controller` (the builds
overlay: `jobs` create/patch/delete, `pods` get/list) with
`BIFROST_PLATFORM_BUILD_BACKEND=kubernetes`, worker ×4, no KEDA. An
unchanged install must keep building Apps after 5a, so control cannot simply
move to the API: the API holds no Job permission there.

- **Leadership, schedules and the dispatch reconciler run in the API.** The
  `BIFROST_CONTROL_PLANE_HOST` switch is removed. During a rolling upgrade an
  older scheduler still competes for the same fenced lease, which is safe.
- **The Kubernetes loops follow the permission, not a flag.** The build-Job
  loop and the on-demand worker controller start in every process (API or
  executor) whose service account may create Jobs, checked once at start with
  a `SelfSubjectAccessReview`. Both loops already tolerate several replicas
  (SKIP LOCKED claims, the capacity lock). Unchanged production keeps them in
  the scheduler container's executor; an install that applies D3 (the base
  manifests' Role bound to the API) runs them in the API too.
- **Every new dispatch is pool-placed.** The `BIFROST_EXECUTION_PLACEMENT`
  switch is removed. The reconciler re-places a broker-placed dispatch left by
  an older API pod during a rollout to pool at once (otherwise it would wait
  60–90 minutes for a broker consumer, then fail). A duplicate RabbitMQ
  delivery to a still-running older worker is fenced by the claim.
- **Both entry points start the static executor.** `src.worker.main` and
  `src.scheduler.main` run `PoolWorker` and keep writing their own health
  files (`/tmp/bifrost-worker-ready`; the scheduler heartbeat and ready
  files), so unchanged Compose healthchecks and Kubernetes probes keep
  passing. The scheduler's liveness probe would otherwise kill it.
- **Job specs default from the cluster.** Without build settings, worker Jobs
  use the controlling pod's own namespace (service-account `namespace` file),
  image and `envFrom` sources (its own pod spec, so renamed ConfigMaps and
  Secrets carry over), and the `default` service account with no token.
- **Defaults** `BIFROST_POOL_MIN_WORKERS=1`, `BIFROST_POOL_MAX_WORKERS=6`.
  Supervised services need an executor: in Docker the static executors, on
  Kubernetes the static executors or the warm Job worker.

## Stage 5b design (2026-10-23)

5a already started no broker consumer, so 5b removes code and topology that
nothing runs any more, plus the one-release bridges:

- **Broker code:** `src.jobs.rabbitmq`, the consumer base classes, the
  package-install broadcast, the RabbitMQ readiness check and
  `BIFROST_RABBITMQ_URL` in settings, and the `aio-pika` dependency. Settings
  ignore unknown variables, so an older compose file or manifest that still
  sets the URL keeps starting; the execution template still scrubs it from
  children, because it carries credentials.
- **Redis bridges:** the pending-execution record and the agent-run context
  that new APIs wrote for older workers, the Redis-only "pending receipt"
  read and cancel paths, and the consumer's Redis-first message path. Every
  run has its PostgreSQL row and dispatch from acceptance (stage 2).
- **Topology:** the rabbitmq service in every compose file, the Kubernetes
  RabbitMQ manifests, KEDA, and the Kind `experiment` harness built on it.
  The runbooks say how to delete the leftovers; the queues and dead letters
  are not read and nothing replays them.
- **Dispatch table:** `queue`, `status`, `publish_count` and
  `last_published_at` are no longer written or mapped. 5a pods still write
  them during a rolling upgrade, so this release only relaxes them (nullable
  or defaulted) and the `execution_record_exists` message field stays; the
  next release drops both.

**Memory gating restored.** Under RabbitMQ a fork refused for memory
pressure NACKed the message to another worker. With PostgreSQL claims the
refusal came after the claim and left the run RUNNING with no child. Now:

- an executor over `memory_pressure_threshold` claims nothing new and says
  so in its heartbeat (`execution_workers.memory_pressured`);
- the worker controller does not count a pressured executor's free slots,
  so it starts a worker Job for the work left unclaimed;
- a fork still refused after a claim hands the run back (RUNNING → PENDING,
  a fresh dispatch, the claim record removed) for another executor.

**Executor drain.** An executor's drain now ends with the process pool's own
drain, so runs still active at the grace deadline are recorded as
interrupted by shutdown and in-flight result handling completes, as the
broker consumer's drain did.

**Production rollout.** Production runs an image from before #1031, whose
in-flight RabbitMQ messages have no dispatch rows. Roll it to a stage-4
image first (dispatch rows plus broker consumers), then to 5a/5b, whose
reconciler re-places any broker-placed dispatch at once.
