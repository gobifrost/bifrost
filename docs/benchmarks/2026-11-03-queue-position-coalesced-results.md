# UI-Preserving Queue Refresh Results — 2026-11-03

The finished queue-position fix retains the major gain established by the no-op
experiment. At the same N2/Redis-100m allocation, it completed **2,444/2,444
workflows correctly by deadline**, sustained **8.12 correct completions/sec**,
and reduced observed completion p95 from **238.50 to 3.38 seconds**. Astra
reviewed both the implementation and completed results and approved the direction.

The fix is an incremental release candidate. The remaining API ownership/CPU
constraint prevents promising this profile as an 8/sec offering meeting our
1.5-second completion target; it does not invalidate this improvement.
No production deployment, PR, or merge was made.

## Comparison at 8 Offered Workflows/Sec

| Arm | Correct events/sec | Arrival cohort correct by deadline | Unfinished | Admission p95 | Queue p95 | Observed completion p95 | Redis commands/sec |
|---|---:|---:|---:|---:|---:|---:|---:|
| Original, Redis 100m | 3.187 | 802/2,444 | 1317 | 29.130 s | 239.085 s | 238.499 s | 4,856 |
| Redis 500m only | 6.347 | 1812/2,444 | 167 | 0.525 s | 79.314 s | 79.606 s | 2,871 |
| Experimental no-op | 8.127 | 2444/2,444 | 0 | 0.105 s | 4.037 s | 4.463 s | 681 |
| UI-preserving fix | 8.120 | 2444/2,444 | 0 | 0.101 s | 2.942 s | 3.381 s | 682 |

The finished fix provides **2.55 times the observed completion throughput**,
about **71 times lower observed completion p95**, and **86% fewer Redis commands/sec**
than the original control. Neither workers nor shared resources were increased.
The 500m Redis contrast alone improved overload but retained the defective path.

Event throughput counts completions during the 300-second window. The arrival
cohort tracks the same scheduled requests through their deadlines and drain.
Control latency excludes unfinished requests, which the cohort gates still
count. Redis rates cover their recorded warmup/window/drain snapshot intervals,
not precisely the measurement window. These are separate, single diagnostic
runs. The real fix's 3.38-second p95 versus the no-op's 4.46 seconds is not evidence
of an additional causal improvement; both demonstrate removal of the same cost.

## What Changed

Queue-position announcements no longer hold up accepting or starting workflows.
Committed changes request a synchronous, zero-I/O signal. One API-owned task
coalesces changes at a one-second cadence and sends a shared invalidation using
the existing Redis transport. Each API ranks the canonical durable queue in SQL
and delivers metadata only to its local execution viewers through the existing
per-message authorization dispatcher. With no viewers there is no queue query.
No per-execution queue-position PUBLISH remains.

Normal execution status and completion paths remain intact. Queue metadata uses
a separate advisory event, so a delayed snapshot cannot turn a confirmed running
or finished workflow back into Pending. A genuine pre-start hand-back still
publishes its authoritative Pending status. The client hook also now retains the
queue fields it previously discarded, so the existing badge can show positions.

Healthy UI freshness is at most two one-second coalescing intervals plus
query/delivery time. This uses no new installation settings, durable jobs, queues,
containers, or browser polling. See the source worktree's
`docs/architecture/queue-positions.md` for lifecycle and failure behavior.

## Gates and Remaining Constraint

The finished candidate passed 2/sec (**598/598**, completion p95 **0.325s**) and
4/sec (**1,179/1,179**, **0.486s**). All three candidate cohorts have zero invalidations,
validity warnings, duplicates, evictions, OOMs, quota denials, restarts, or terminal
workflow failures. At 8/sec, none was late or unfinished.

At 8/sec the throughput and diagnostic 1% reliability gates pass. Completion p95
**3.381s** exceeds **1.5s**, queue p95 **2.942s** exceeds **2s**, and the backlog
drift bootstrap remains inconclusive. The overall 8/sec verdict is therefore
**fail** under the existing latency-aware gates. The highest tested rate meeting
all overall targets remains **4/sec**, with tested pass/fail points 4 and 8.
These do not locate the exact maximum. There was no 16/sec trial or further matrix.

Both workers shared the same API owner during 2/sec and 8/sec. The 8/sec SQL
windows show about **0.678 CPU core** on the owner API versus **0.374** on the
other; each API has a **0.750-core cap**. Maximum sampled API throttled-period
fraction was **0.98**. That is not CPU utilization or a percentage of request time.
The layout and CPU distribution closely match the no-op experiment.

| Component at 8/sec | Mean CPU cores | Allocated cores |
|---|---:|---:|
| Two APIs | 1.085 | 1.500 |
| Two workers | 0.565 | 1.300 |
| Redis | 0.040 | 0.100 |
| PostgreSQL | 0.156 | 0.750 |

Aggregate API headroom hides concentration on the owner API. This is the next
observed scaling constraint; Redis and workers retain headroom. Balanced ownership
has not been tested here and is not promised to meet the latency target.
The original control lacks owner IDs, although its lower-rate fenced-query
counts strongly match the co-owned layout. HTTP-admission connection affinity is
not directly instrumented.

## Resource and Storage Baseline

The candidate exactly matches the control's declared N2 profile and arrival
seeds: two 650m/1GiB workers, two 750m/512MiB APIs, PostgreSQL 750m/1536MiB,
Redis 100m/64MiB, with the same client/pooler. Total tenant requests/limits are
**3.8 CPU / 4.75 GiB**. Observed tenant mean CPU was **1.929 cores**, sampled
charged-memory peak **1.978 GiB**, and working-set peak **1.609 GiB**.
Providers, driver, and object storage are outside that budget. This local Kind
allocation does not emulate a literal cloud host or establish multi-host capacity.

Over the candidate 8/sec storage snapshot interval (**398.227s**, including
warmup/drain), PostgreSQL database size grew **15,949,824 bytes** across **2,687
new execution rows**. Retained WAL grew **67,108,864 bytes** and retained container
logs **16,545,749 bytes**. These are retained-size deltas, not WAL generation or
retention-age tests. S3 payload/count did not grow in this fixed-fixture workload,
which does not exercise general workflow artifact writes. The accompanying
scalar JSON retains all four arms' 2/4/8 storage, resource, outcome, and gate data.

## Verification and Reproduction

Implementation source: `/home/jack/.codex/worktrees/coalesced-queue-positions/bifrost`,
branch `fix/coalesced-queue-positions`, commit `b882167c264d93d69d839a6d7417b22c8ff0fe33`.

Focused verification passed:

- `./test.sh tests/unit/execution/test_queue_tracker.py tests/unit/execution/test_queue_position_refresher.py tests/unit/test_pubsub_connection_manager.py tests/unit/services/execution/test_workflow_dispatch.py tests/unit/jobs/consumers/test_workflow_execution_lineage.py tests/unit/services/test_legacy_broker_drain.py tests/unit/services/worker_pool/test_worker_pool.py tests/unit/routers/test_executions_initiator_reads.py -v --durations=0` — **161 passed**.
- `./test.sh tests/e2e/api/test_execution_queue_positions.py tests/e2e/api/test_execution_completion_delivery.py -v --durations=0` — **2 passed**.
- `./test.sh quality api` — Pyright and Ruff passed.
- With Node 26: `./test.sh client unit src/services/websocket.test.ts src/hooks/useExecutionStream.test.tsx src/stores/executionStreamStore.test.ts src/components/execution/RunStatusBadge.test.tsx` — **52 passed**; `npm run tsc` and `npm run lint` passed.
- Harness: **17 tests passed**, Ruff and diff check passed.

The live queue test launched zero workflow jobs; it exercised durable queue state,
real API subscription/authorization, cross-process Redis invalidation and local
queue delivery. Its 3.29-second call time is transport/coalescing; initial shared
E2E setup was 3.26 seconds. Completion-before-bookkeeping remained covered by the
existing live test. The new test initially referenced a module-local fixture;
using the shared human credential fixture fixed the harness error. Full backend,
full frontend, and browser suites were not run; the focused coverage above was.

Use [the declaration](2026-11-02-queue-position-coalesced-test.md) to reproduce the
same bounded 2/4/8 run, retaining 30-second warmup, 300-second windows, 60-second
drain, gates, and first-nonpass stop. The frozen matched image overlays only the
six reviewed backend files onto the original control image. All 926 Python
runtime files were checked, allowing only those six to differ; all base layers
remain and one COPY layer is added. Networking-disabled import/shutdown smoke
verification passed. Frontend behavior was verified separately; the throughput
run is an SDK workload, not a UI-load, agent, or AI capacity benchmark.

- Control runtime: `3603b2d3bd172eadf30a8516cc8fa6afc14f1ad5`.
- Control image: `sha256:babc52f1c0ac8c5482488c60511d945b220ae154ea56789d7dbe068ee5fc9e09`.
- Candidate image: `sha256:8ffa15a9310a49af0ce7f50f589c0ca1e35f147ba3e9353ced68a7db79370919`.
- Frozen harness: `dfb41adb4b945fd45f6b30b7a8be984978850be0`.
- Candidate raw result SHA256: `08974b974b1a814d5f9e5fd738ff37cecdcc0accd8bc0a003d72db5e1ea1808a`.
- Raw evidence: `/home/jack/bifrost-bench-evidence/runs/queue-position-coalesced-20261102-r1`.
- Support, source/image archives, tests, owner snapshots, and completion receipt:
  `/home/jack/bifrost-bench-evidence/supervision/queue-position-coalesced-20261102-r1`.

The run finished **2026-11-03T00:31:25+00:00**. Both root/count cleanup are verified,
the process exited 0, both owned namespaces are absent, and the host lock is
released. The implementation test stack was removed before measurement; foreign
stacks/clusters were left intact. Source and harness were frozen during the run.

The queue-refresh fix is ready for incremental review. API ownership balancing
is a separate proposed improvement; it was not changed in this implementation.
