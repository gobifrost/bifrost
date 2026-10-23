# #769 stage 3: reusable Kubernetes workers

Status: in progress. Parent plan: `docs/plans/2026-10-22-api-owned-unified-kubernetes-runtime.md`
(PR #1030), stage 3. Builds on stage 1 (#1031, API-hosted control) and stage 2
(#1032, durable dispatch).

## Gate

> Zero → first worker → repeated work in same worker → bounded burst → safe
> idle zero, including enqueue/drain race; cancellation/OOM/node loss/
> controller restart tested.

## Shape

A *reusable worker* is the existing worker runtime: the supervisor process
plus a process pool that forks a fresh child for every attempt, with the
same SDK socket boundary and the same credentials as today's worker
Deployment. It differs from that Deployment in three ways:

1. **Lifetime is a controller-created `batch/v1` Job, not a Deployment.**
   - One Job per worker incarnation: `parallelism=1`, `completions=1`,
     `restartPolicy=Never`, `backoffLimit=0`, a terminal TTL, and no service
     account token.
   - The Job is created suspended. Its UID is persisted on the incarnation
     row before the Job is unsuspended, the same protocol as build Jobs.
   - The pod admits itself by binding its pod UID once. A second start of
     the same Job cannot admit.
2. **Work comes from PostgreSQL, not RabbitMQ.**
   - Dispatches placed on the pool are claimed directly with `SKIP LOCKED`,
     assigned to one incarnation.
   - They are handed to the same consumer code (`process_message`), whose
     domain claim (`PENDING → RUNNING`, `queued → running`) still fences
     duplicates.
   - Workers poll briefly and are woken by a Redis hint. The hint is never
     authority.
3. **It drains itself.**
   - With no attempts for the idle timeout (default 300 s), the worker moves
     itself `ready → draining` in one conditional update and exits 0.
   - Its claim query requires `ready`, so a draining worker can never claim.
   - Work that arrives during the race stays unclaimed. The controller sees
     demand with no ready capacity and launches a replacement.
   - Reaching maximum lifetime also means drain, never killing active work.

**Placement** is decided at acceptance and persisted on the dispatch:
- `broker` is today's path: publish to RabbitMQ for warm workers.
- `pool` means claimed from PostgreSQL by reusable workers.
- `BIFROST_EXECUTION_PLACEMENT` (default `broker`) opts a deployment in.
  `pool` requires API-hosted control and the Kubernetes backend.
- In-flight dispatches keep their placement across configuration changes.

**Capacity** is owned by one controller in the control plane, on every API
replica, serialized by an advisory lock:
- Demand is unclaimed pool dispatches. Capacity is the free slots of
  workers that are reserved, starting or ready.
- Starting, unschedulable and draining workers count toward the right
  ceilings (`BIFROST_POOL_MAX_WORKERS`, slots per worker), so replicas
  never over-launch.
- A worker that stops heartbeating (node loss, OOM-killed supervisor) is
  marked lost and its Job deleted. Its claimed runs follow their domain's
  existing terminal sweeps, because started work is never replayed.
- The controller restarting is safe: incarnations, Job names and UIDs are
  durable, so it adopts its existing Jobs.

**Handler versions:** a worker claims only dispatches whose
`(handler, handler_version)` it supports. That is the version enforcement
deferred from stage 2.

## End state and upgrade safety (requirements from the 2026-10-22 review)

**The unified system takes over by itself.**
- Every stage is safe on upgrade with no operator action.
- Stage 1 and the pool placement are opt-in until stage 5 flips the defaults.
- Stage 2 (durable acceptance) applies to every install. It carries a
  one-release bridge, so old and new workers interoperate during a rollout.

**The scheduler and worker containers converge on one executor.**
- The executor is one runtime that claims from PostgreSQL and runs workflow
  runs, agent runs and platform jobs in fresh children.
- It runs in two forms:
  - *on demand*: a controller-created Kubernetes Job that drains to zero;
  - *static*: a plain Deployment or Compose container with no Job binding
    and no idle drain, for Docker installs and warm minimums.
- When stage 5 is done, the `scheduler` and `worker` services run identical
  code. The API hosts control, so any of them can be torn down or scaled
  freely. Docker installs keep working with the containers they already
  have.

**No dead resources are left on an instance.**
- Finished worker Jobs expire by TTL.
- Retired and lost incarnation rows are pruned by a scheduled task.
- Dispatch rows exist only while unclaimed.
- Redis bridge keys expire.
- Stage 5 removes what the unified runtime replaces, through forward
  migrations and documented manifest steps: RabbitMQ queues and exchanges,
  the worker Deployment and its KEDA ScaledObject, the scheduler-only
  RBAC, and the bridge code paths.

## Slices

| Slice | Scope |
| --- | --- |
| 3a | `execution_workers` incarnations, dispatch placement, pool claim loop + idle drain in the worker, static executor form (no Job binding, no drain), incarnation pruning, capacity controller with suspended-create/UID binding, Kind harness (zero workers) and experiment |
| 3b | Failure drills: cancellation, OOM, node loss, controller restart, unschedulable capacity; resource classes; measurements (cold start, image pull, idle tail) |
| 3c | One-shot placement through the same controller for build/heavy handlers (the existing build Jobs move onto incarnations) |

## Evidence (2026-10-22)

**Kind** (`BIFROST_KIND_POOL=1`, two API replicas hosting control, no broker
worker pods, ceiling 3 workers × 2 slots, idle 45 s;
`./test.sh kubernetes pool-experiment`):

| Phase | Result |
| --- | --- |
| Zero | 0 worker pods, 0 live executors |
| First run | worker launched from zero; cold start 12.6 s |
| Reuse | 3 more runs on the same pod in 3.5 s |
| Burst | 16 × 6 s runs in 27.4 s; peak 3 workers / 3 pods (the ceiling) |
| Idle | every worker drained itself (`retired`/`idle`); zero in 49 s |
| Drain race | run accepted while its worker was draining finished on a new worker in 12.9 s; zero again in 47 s |

**Docker, pool placement with one static executor and no broker worker:**
236 execution, agent, chat, event and scenario e2e tests passed (2 skipped).

**Latency,** end-to-end trivial sync run, 200 runs each, same stack:

| Path | p50 | p95 | p99 |
| --- | --- | --- | --- |
| Broker | 47 ms | 64–68 ms | 81–100 ms |
| Pool (static executor) | 55–57 ms | 86–94 ms | 109–128 ms |

Both paths are subsecond. The pool path's extra ~8 ms at p50 comes from
two hops:
- the Redis wake hint;
- an assignment transaction ahead of the run's own claim.

Folding the two transactions together is the next optimization. A cold
Job worker takes seconds, so interactive runs need warm capacity:
`BIFROST_POOL_MIN_WORKERS` or a static executor.

## Failure drills (Kind, `./test.sh kubernetes pool-drills`, 2026-10-22)

| Drill | Result |
| --- | --- |
| Worker process SIGKILLed on its node mid-run | lost after 28.6 s; its 2 running runs failed at once ("executor lost"), not replayed; queued work completed on another worker |
| Every API pod deleted during a 12-run burst | burst completed in 28.6 s; no duplicate Jobs (one per executor row) |
| Run exceeding the pod memory limit | that run failed visibly (executor lost); pool kept serving (next runs 0.2 s) |
| Unschedulable worker capacity | not completed: the drill's enqueue hit an API pod being replaced by its own `set env` rollout (exit 137). Harness fix: select a Ready, non-terminating API pod |
