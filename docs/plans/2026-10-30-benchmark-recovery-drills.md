# D3/D4 benchmark recovery drills

## Scope and invariants

This plan adds D3 (CNPG primary deletion) and D4 (an empty Redis restart) to
the existing benchmark fault harness.  It reuses `scripts/bench/drills.py`,
`search.run_step`, the existing benchmark deployment/reset path, and the
`guard` identity checks.  It does not introduce a runner, job system, retry
loop, or capacity gate.  D1 and D2 keep their target selection, timing,
classification, and acceptance criteria unchanged.

The fixed drill rate is a prerequisite, not an assumption: do not run D3/D4
until a confirmed sustainable mixed rate exists, then use its predeclared 50%.
The current plan does not infer a sustainable rate.  If an earlier
correctness-only trial is explicitly approved, its supplied offered rate is
recorded as `not_50_percent_confirmed` and is never presented as a capacity
result.  The regular `run_step` verdict remains evidence, rather than being
replaced by a recovery-specific success definition.  The correlated variant
below is a correctness-only workload, not a new capacity point.  A fault result is separately labelled
`d3-cnpg-primary-delete` or `d4-redis-empty-restart`, with its workload
version, target UID, readiness evidence, injector result, and raw request
reconciliation retained in the result directory.

## Common execution contract

1. Call `cluster.verify()` before discovering a target and again immediately
   before the destructive `kubectl delete`.  All Kubernetes calls pass
   `-n bench-tenant`; a selector returning anything other than one live target
   is a preflight failure, not a choice among pods.  `guard.check_fault_target`
   verifies the tenant namespace and the required labels.  Neither drill pulls,
   loads, or mutates an image.
2. Start the injector after the existing warm-up plus `FAULT_AT_S`, while
   `search.run_step` owns arrival, drain, outcome reconciliation, normal
   correctness gates, and host sampling.  The injector runs once.  It returns
   a structured result through an exception-capturing channel; a thrown delete,
   selector, or readiness error, no recorded delete, or more than one injection
   marks the drill invalid and is saved.  A daemon-thread exception must never
   be mistaken for a passed fault.
3. Capture the target name/UID, deletion response and timestamp, fault time,
   replacement name/UID, ready time, and each probe.  The replacement UID must
   differ from the deleted UID.  This proves one intended pod was deleted and
   a replacement actually recovered; it does not infer a fault from ordinary
   load errors.
4. Reconcile every generated `bench_request_id` from the durable execution and
   agent-run tables after the normal drain and again at its class deadline.  A
   response is **submitted** when the load generator attempted it.  It is
   **accepted** when the server returned 2xx and durable lookup finds exactly
   one matching entity, or when the response was lost/ambiguous but that lookup
   finds exactly one entity.  An
   accepted request must have exactly one durable entity and reach a terminal
   status by its existing class deadline, measured from send time.  Submitted
   requests that were never accepted are reported by HTTP/error class; they are
   not silently converted into accepted work and are not required to succeed.

The terminal check records `Success`, `Failed`, `Timeout`, `Stuck`,
`CompletedWithErrors`, or `Cancelled` for workflows, and a non-active final
agent status.  It reports deadline misses, missing durable entities, duplicate
execution/agent rows, and unfinished work separately.  It does not retry a
request or relax an existing capacity gate.

For D3, retain that normal, send-time deadline result and add the methodology's
recovery clock: every accepted request still active when the database becomes
ready must become terminal no later than `db_ready_at + deadlines_s[class]`.
Requests accepted after readiness retain their ordinary send-time deadline.
Both clocks are persisted; the recovery clock does not overwrite a missed
normal capacity deadline.

## Correlated side-effect evidence

Current `loadgen.outcomes` detects duplicate execution/agent rows only.  It
cannot detect two writes made inside one execution: `bench_wf_sdk` writes an
untagged `bench_writes` document and `bench_tool_note` writes an untagged
`bench_notes` document.  Therefore D3 must not claim exactly-once effects
until the following deliberately correlated workload exists.

Add a separately named correctness-only recovery workload version, initially
`recovery-effects-v1`, rather than changing the existing capacity workload.
Its workflow and tool inputs carry `bench_request_id`, `workload_version`, and
one deterministic `effect_token`.  Every committed effect appends one document
to a benchmark-only Bifrost table with those values, the class, and its effect
index.  The table is deliberately append-only: there is no unique key,
`ON CONFLICT`, upsert, or idempotency guard.  A replay must leave a second
document that the benchmark can count, rather than being hidden by the ledger.

The recovery result queries every appended document for only this run's request
IDs and records all rows, distinct tokens, and counts per request/effect index.
It retains the complete `(terminal status, effect count, token set)` pair for
each accepted request.  This is necessary because an SDK call can visibly fail
when the database disappears: an accepted request can fail before an effect, or
commit an effect and later report a failure.  The benchmark must not relabel
either case as successful through the outage.

Report `terminal_and_loss` and `strict_effects` separately.  The former is the
accepted-request terminal/deadline and healthy-worker-loss result.  The latter
is the methodology's strict count: exactly one appended effect with the
expected token per accepted instrumented request; zero or more than one is a
strict-effect failure regardless of terminal status.  Never call D3 passed by
redefining a failed operation as success or by ignoring its effect.  Before
implementation, Astra must approve an amendment that states which D3 guarantee
is intended under an intentional no-retry SDK DB error: strict `= 1` for every
accepted request, or the separately reported terminal/loss and observed-effect
results.  Until that decision, retain both results and mark the overall D3
claim unresolved rather than weakening the published gate.

The result includes `workload_version: recovery-effects-v1`; capacity results
continue to name their current workload version.  The recovery variant may
share the mixed scheduler and fixed rate, but its added payload/ledger I/O is
never represented as the old capacity mix.

## D3: delete the CNPG primary

### Target and inject

The only eligible target is exactly one non-terminating pod in
`bench-tenant` selected by
`cnpg.io/cluster=bifrost-db,cnpg.io/instanceRole=primary`.  Its required labels
are exactly those two key/value pairs.  Preflight also reads
`Cluster.postgresql.cnpg.io/bifrost-db` in `bench-tenant` and requires a
healthy one-instance cluster and that the selected pod is its current primary.
If the installed CNPG version does not expose this selector/status shape, the
drill fails closed; it must not fall back to an ordinal, a name prefix, or an
arbitrary CNPG pod.

The injector issues one `kubectl delete pod <primary-name> --wait=false`.
After the old UID has deletion evidence, ready time is the first instant all
of these are true:

* `Cluster/bifrost-db` is Ready with one ready instance;
* exactly one non-terminating pod again matches the primary selector, is
  Kubernetes `Ready=True`, and has a different UID; and
* a read-only probe through the application's configured database/pooler route
  succeeds, so direct-primary readiness alone cannot declare recovery.

In addition to the common terminal/deadline and correlated-effect checks, D3
records the outage interval and any worker owner/loss transitions.  A healthy
worker marked lost after DB recovery is a drill failure.  This observation is
not a replacement for D6: D6 still has to test its 20-second lease plus sweep
against the 30-second liveness interval, whose different clocks must be
reported explicitly.

## D4: delete the empty Redis pod

### Target and inject

The only eligible target is `pod/redis-0` in `bench-tenant`, selected by
`app.kubernetes.io/name=redis`, with an owning `StatefulSet/redis`, a `redis`
container, no deletion timestamp, and exactly one selector result.  The guard
requires `{"app.kubernetes.io/name": "redis"}`.  This matches the tenant
overlay's single-replica `redis` StatefulSet rather than a caller-provided pod
name.

Before deletion, set a unique `d4-sentinel:<run-id>` key and record the Redis
`run_id`, `dbsize`, and the rendered StatefulSet data volume's `emptyDir`
shape.  Failure to observe the sentinel before deletion invalidates the drill.
The injector records the pod UID and issues one `kubectl delete pod redis-0
--wait=false`.  Ready time is the first instant a replacement `redis-0` has a
different UID, `Ready=True`, a ready `redis` container, and `redis-cli ping`
returns `PONG`.

After that ready point, require the StatefulSet's `data` volume still to be
`emptyDir`, Redis `run_id` to differ from the pre-delete value, and the unique
sentinel key to be absent.  Record `dbsize` but do not gate it: the API and
workers can repopulate Redis as they reconnect, so it is not reliable evidence
of an empty volume under load.  The changed run ID plus absent known sentinel
proves prior Redis state was lost without creating a second outage.

D4 passes only when the common reconciliation shows no lost dispatch (every
accepted request maps to one durable terminal entity by its deadline, with no
missing or duplicate mapping) and the first correct post-ready request arrives
within 30 seconds.  It records any new lost worker or owner generation as
fault-specific evidence; it does not declare ordinary submitted-but-unaccepted
outage errors to be lost dispatch.

## Interlocks and tests

D1/D2 tests remain unchanged.  New focused unit tests mock the cluster,
injector channel, clock, and `search.run_step` to prove the exact D3/D4
selectors, guard-before-delete ordering, one-delete/changed-UID evidence,
readiness predicates, injected-error invalidation, and accepted-versus-
submitted reconciliation.  D4 tests cover preseeded sentinel, changed Redis
run ID, preserved `emptyDir`, and absent sentinel while treating `dbsize` as
evidence only.  Effect tests cover one append, duplicate append, missing
append, and an append attached to a non-success terminal result.
No Docker stack, benchmark build, or workload is needed for these unit tests.

D8 is deliberately not folded into D3.  A cancellation write cannot be
accepted while the database is unavailable.  Its later design must establish
and durably accept the cancellation before the outage (or define a separately
durable external intent), then measure application of that already-accepted
intent within 30 seconds of DB recovery.  It must not describe a failed write
during the outage as an accepted cancellation.
