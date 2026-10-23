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
   - delete RabbitMQ queues and exchanges in a one-time cleanup;
   - make RabbitMQ an optional dependency, then drop it from compose and
     the manifests.

**Gate:**
- Every scheduler catalog entry and every legacy consumer family is mapped
  to a tested owner.
- An upgrade rehearsal from the release before the flip and a rollback
  rehearsal both pass.
- A Docker install upgraded in place keeps its existing services working.
- After retirement no RabbitMQ object, legacy Deployment or bridge key
  remains.
