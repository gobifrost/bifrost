# #769 stage 2: durable work acceptance and dispatch

Status: in progress. Parent plan: `docs/plans/2026-10-22-api-owned-unified-kubernetes-runtime.md`
(PR #1030), stage 2. Stage 1 (API-hosted control) is PR #1031.

## Gate

> Workflow, agent and PlatformJob acceptance survives API/Redis failure; scope,
> impersonation and audit preserved; duplicate delivery, leases, deadlines,
> durable waiting and mixed versions tested.

## Where acceptance stands today (code-verified 2026-10-22)

| Family | Durable at acceptance? | Recovery when dispatch is lost |
| --- | --- | --- |
| Immediate workflow runs (HTTP execute, SDK, endpoints, forms, data providers, events) | No. Only a Redis blob (1h TTL); the worker creates the `Execution` row | None. A lost message or expired blob makes the run vanish |
| Scheduled workflow runs | Yes (`SCHEDULED` row) | Promoter reverts on publish failure, but a crash after its `PENDING` commit strands the row forever (`started_at` is NULL so no sweep matches) |
| Event deliveries | `EventDelivery` row, but `QUEUED` becomes durable only on the caller's later commit | Stuck sweep marks them failed after 5 min; nothing re-dispatches |
| Agent runs | Yes (`queued` row), context in Redis (1h) | Stale-run sweep fails them after timeout + 300s; nothing re-dispatches |
| Live summaries | `summary_status='pending'` | None |
| PlatformJobs | Yes; claimed by DB lease, no broker | Lease recovery (the template for the rest) |

Duplicate delivery is fenced for scheduled runs and agent runs only. A
redelivered immediate run hits a duplicate primary key and is dead-lettered.

## Design

**One outbox, owned by the domain transaction.** A `work_dispatches` row is the
durable intent to deliver one unit of domain work. It is inserted in the same
transaction as the domain record and deleted by the executor's claim. While
it exists it holds:

- the subject: `family` plus `subject_id`;
- the handler and its version;
- the transport queue and the message;
- the durable payload, which replaces the Redis-only context.

The table therefore only ever holds unclaimed work.

1. **Accept.** In one transaction:
   - insert the domain row in its waiting state (`Execution.PENDING`, `AgentRun.queued`);
   - insert the dispatch row (`status='pending'`).

   Commit. Then publish on the fast path and mark the dispatch row
   `published`. A publish failure no longer fails an accepted request:
   - async callers get their id;
   - sync callers keep waiting and are served once the reconciler publishes.

   Direct API → RabbitMQ → warm worker latency is preserved. The only cost
   added to the hot path is one insert and one update.
2. **Claim.** The executor claims in one transaction:
   - the domain row's waiting → running transition (the existing fenced
     `PENDING → RUNNING` claim);
   - deleting the dispatch row, returning its payload.

   A duplicate or stale delivery finds nothing to claim, so it is acked and
   dropped.
3. **Reconcile.** The control plane (stage 1) runs a reconciler on every
   control replica, using `SKIP LOCKED` batches. It handles three cases:
   - dispatches still `pending` a few seconds after creation, because the API
     died or publish failed, are published;
   - `published` dispatches left unclaimed past the redelivery window
     (broker loss) are republished with a bounded count;
   - dispatches whose subject is no longer waiting (cancelled, or claimed by
     an older worker) are deleted.

   Once the publish budget is spent, the subject fails visibly with a
   structured error. Started work is never replayed. Lost running work keeps
   its existing terminal sweeps, because lease fencing cannot make external
   side effects exactly-once.
4. **Versioned handlers.** Each dispatch names `handler` and `handler_version`.
   - Executors declare the versions they accept.
   - A message for an unknown version is requeued for a compatible executor.
     It is never executed or dead-lettered.
   - The reconciler reports versions no live executor accepts.
5. **Mixed versions (one-release bridge, removed in stage 5).**
   - A new API keeps writing the Redis pending blob, so an old worker can
     still run a durable row through its existing `execution_record_exists`
     claim. The write is best-effort, because PostgreSQL is authoritative.
   - A new worker still accepts legacy Redis-first messages, which have no
     dispatch id.

## Slices (each its own PR)

| Slice | Scope | Evidence |
| --- | --- | --- |
| 2a | `work_dispatches` table, acceptance/claim/reconcile service, control-plane reconciler, immediate + promoted workflow runs | Kill API between commit and publish; publish with RabbitMQ down; Redis flushed after acceptance; duplicate message; cancel while pending; old worker + new API; new worker + old message; promoter crash after commit |
| 2b | Agent runs (context in PG, re-dispatch, claim fencing), event deliveries made durable before queueing | Same failure set for agent runs; event delivery survives API crash; delegated agents unchanged |
| 2c | Live summary dispatch; handler-version requeue and reporting; sync waiter deadlines (no infinite BLPOP) | Mixed handler versions; summaries survive publish failure |

PlatformJobs are already durable and fenced; they join the shared handler
vocabulary in stage 3, when reusable workers claim them.
