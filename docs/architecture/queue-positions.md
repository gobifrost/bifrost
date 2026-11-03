# Queue Positions

Queue positions are an advisory UI projection of PostgreSQL `WorkDispatch`
and pending `Execution` rows. They do not control admission, dispatch, claim,
worker routing, cancellation, or completion. The canonical ordering remains
dispatch creation time, then dispatch ID, including assigned pending work and
excluding synchronous executions.

After a committed queue mutation, `request_queue_position_refresh()` only marks
local state and schedules one task. It performs no database or Redis I/O in the
caller. A burst of mutations collapses into one refresh per one-second interval
on that API; a mutation during a refresh leaves one latest refresh pending.

The producer sends one `queue_positions_changed` invalidation on the existing
Redis `bifrost:queue:positions` channel. Each API's existing pub/sub listener
requests a local, non-announcing refresh. This does not create another durable
job, queue, container, endpoint, or browser polling loop. It belongs to the
shared WebSocket connection manager and is cancelled during API shutdown before
Redis and database teardown. Its task has an empty context so it cannot inherit
an execution's credentials or remote-owner fence.

An API with no local execution viewers performs no queue query. An API with
viewers ranks the canonical queue in SQL and returns only watched execution
IDs. It delivers `execution_queue_update` messages locally through the existing
per-message authorization dispatcher. Individual queue positions are never
republished through Redis. Query/delivery work consequently depends on active
viewers and a bounded refresh cadence, rather than all queued executions times
the number of queue mutations.

Under healthy transport and database conditions, a change waits at most one
second for its producer interval and one second for the viewer API interval,
plus query/delivery time. These are internal UI freshness constants, not new
installation settings. Delivery remains best-effort: failures are logged, and
the next queue change or subscription requests a fresh snapshot. A new or
reconnected execution subscription requests a snapshot even if no new work
arrives. A producer receiving its own invalidation may perform one additional
local refresh; it never announces that invalidation again.

Queue advisories carry position and wait reason without an authoritative
execution status. The client routes them separately from status, history, and
completion callbacks. They can initialize an unconfirmed stream as queued or
update a pending stream; they cannot turn a confirmed running, cancelling, or
terminal execution back into pending. Normal status changes clear queue fields.
A run genuinely handed back before child start sends its own authoritative
Pending update, preserving that transition independently of queue metadata.

Verification covers canonical ranking including unwatched predecessors, local
authorization-mediated delivery, no query without viewers, burst coalescing,
changes during a refresh, context isolation, shutdown, subsequent refresh after
failure, and cancellation/hand-back. The live transport test creates no workflow
jobs: it creates assigned durable queue rows, subscribes through the real API,
and observes a second process's Redis invalidation advancing a position. Client
tests cover callback separation, queue metadata, subscription cleanup, and stale
advisories after status and completion changes.
