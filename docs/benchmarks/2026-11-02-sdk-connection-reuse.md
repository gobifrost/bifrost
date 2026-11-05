# Connection Reuse With the SDK Query Repairs

Retaining the existing main-pool allowance removed its measured connection churn: 388 new main connections became zero in the approximately one-minute counter window. Across all API pools, openings fell from 389 to three. The allowed simultaneous count stayed at 26/API. The cost is up to ten additional idle sessions retained per API after demand has used them.

The same-image comparison observed 3.12/s versus 6.39/s correct completions under an 8/s offered load. The retained arm completed all 725 measured arrivals correctly within 60 seconds; the current-setting arm completed 181 within that deadline. The current arm has a host-contention validity flag, so the precise throughput/latency ratio is descriptive. This is strong evidence for testing and reviewing retention with the query repairs, without claiming a certified capacity ceiling or changing installation defaults.

No application source changed in this experiment. The three SDK repairs remain local and unmerged; their verified image was used in both arms. Pool values changed only in the guarded local benchmark deployment. No PR, push, merge or production change occurred, and neither a merge nor a production rollout is required to continue local comparisons.

## Comparison

Both arms use application revision `4747fc458a7ae5af064c58bbc4f6618086d61129`, image `bifrost-api:kind-a74e45295998-sdk-query-candidate-r1`, host image ID `sha256:18e934ba05788c6c0bcfd88ba9c2dde5bc63a8ce2a3cd61cfe8d0fa7b220d38f` and image config `sha256:2109b993a56f76dfa62c9a704dbaeeceee22f072cdcb38fab7055ba90e3ac9ab`. API and worker identities were verified through the local Kind OCI chain before and after both arms. The frozen harness remains `2e3e9ca2bb1c72052098c915d001fc9602a1c082`.

Exactly two windows were declared: current 5 retained + 10 overflow, then 15 retained + zero overflow, both at 8/s. Dispatch remains 4+6, liveness 1. One API process, six warm workers, direct PostgreSQL, resource limits, catalog, workload and counter hook match the [query-repair experiment](2026-11-01-sdk-query-repairs.md). Each arm has 30s warm-up, 90s measurement, 60s drain and at most 120s additional recovery. Counters start 45s after the action starts and run for about 60s. A separate 125s database observer records client occupancy. History persists between the ordered arms; idle worker Jobs are replaced symmetrically before each arm.

Each workflow performs five absent config reads, one query returning 20 documents and one document insert. It performs no AI or external Halo call. Both arms retain the same live token epoch/run checks and access evidence.

| Metric | Current 5+10 | Retained 15+0 |
|---|---:|---:|
| Main-pool peak allowance | 15 | 15 |
| All API pools peak allowance | 26 | 26 |
| Main-pool idle retention capacity | 5 | 15 |
| All API pools idle retention capacity | 10 | 20 |
| New main connections in counter window | 388 | **0** |
| New dispatch connections in counter window | 1 | 3 |
| New liveness connections in counter window | 0 | 0 |
| Total API connections opened in counter window | 389 | **3** |
| Correct completions/s during the 90s measurement | 3.12 | 6.39 |
| Correct within 60s / measured arrivals | 181/725 | 725/725 |
| Correct completed by saved reconciliation | 555/725 | 725/725 |
| Unfinished at saved reconciliation | 170 | 0 |
| Send-to-correct-terminal p50 / p95 | 71.22s / 82.28s, only 555/725 observed | 15.69s / 24.13s, all 725 observed |
| Sampled peak Bifrost-role DB clients | 19 | 21 |
| Host validity | External CPU above 1.5 cores in 4/17 intervals | No flags |

The counter windows lasted 60.12s and 60.15s. Total physical openings fell 99.2%, from roughly 6.47/s to 0.05/s, after warm-up. Initial pool establishment, API startup and eventual recycling are outside this counter window; the result does not mean connections are never created. Three dispatch openings per minute are a small remaining cost. This experiment does not increase dispatch retention to remove that residual.

All phase samples observed six ready workers. Neither arm recorded duplicate execution, terminal failure among reconciled measured arrivals, restart, OOM, eviction, quota denial, counter error or probe failure. Both reached zero pending/running work before proceeding/cleanup. The current arm's unfinished outcomes at reconciliation remain missing rather than being reclassified as timely successes after drain. No timed window was replayed.

## Why Reuse Changes the Result

The API already owns a process-global engine and pool. Request-scoped sessions borrow connections from it. In the current configuration, the main pool's five retained slots are smaller than its allowed concurrency of 15; returned excess connections are closed and later reopened as demand continues. SQLAlchemy documents both [overflow disposal and retained capacity](https://docs.sqlalchemy.org/en/20/core/pooling.html#sqlalchemy.pool.QueuePool.params.max_overflow). Pools grow on demand; changing retained capacity does not eagerly open all slots at startup.

The current counter records 393 main closes and 388 opens; five more were open when the counter started than when it ended. The retained counter records zero new main connections after warm-up. This removes repeated establishment within the same allowed concurrency rather than admitting additional simultaneous main work.

Retention can still affect a shared database budget after a burst: up to ten more backends per API remain idle and available for reuse. Loaded client peaks of 19/21 are sampled observations, not idle measurements or global-limit guarantees. The earlier unchanged-pool-factory budget probe measured 20 versus 40 retained sessions for two API-equivalent pool sets after a 40-connection burst, and demonstrated that either setting can exhaust an undersized server. This experiment does not repeat that budget probe. See the saved [connection-retention budget evidence](/home/jack/.codex/worktrees/runtime-memory/bifrost/docs/benchmarks/2026-11-01-api-db-policy-scaling.md).

## Work and Resource Baseline

| Metric | Current 5+10 | Retained 15+0 |
|---|---:|---:|
| SQL statements in counter window | 17,193 | 29,026 |
| Correct completions in the same counter window | 197 | 397 |
| Aggregate statements / observed correct completion | 87.27 | 73.11 |
| API mean CPU, cores | 0.998 | 0.998 |
| Postgres mean CPU, cores | 0.279 | 0.157 |
| Six workers combined mean CPU, cores | 0.275 | 0.546 |

Raw SQL rose with more completed work, while aggregate SQL per observed completion fell. That interval ratio is not the exact SQL cost of an individual execution. API CPU remained fully used while worker CPU rose with output and Postgres CPU fell. The API's remaining CPU work is the next performance target. Raw and machine-readable artifacts retain charged/working-set memory, resource coverage, storage, backlog, pool occupancy and all validity flags; sampled peaks are lower bounds and history remains a confounder for memory comparisons.

At an offered 8/s, retained completion events still ran below arrival events and queueing grew. The 24.13s p95 describes deliberate overload. Completing all arrivals within 60s does not establish a sustainable 8/s rate or pass the separate 1.5s p95 benchmark target. This finite comparison does not locate the maximum steady throughput and should not be presented as a production latency incident.

## Further Opportunities From Source Review

These are unimplemented hypotheses, separate from the measured retention result:

1. **Shorter read leases for warm config hits.** Runtime authentication performs its required live epoch/run check on the request's database session (`api/src/core/auth.py:336`, `api/src/services/authorization/tokens.py:444`). `cli_get_config` then awaits Redis-backed config reads on that same session (`api/src/routers/cli.py:238`, `api/src/repositories/config.py:431`) before function-scope teardown returns the connection. A carefully placed end to the read-only auth transaction could release the lease before Redis waits. Preserve the live check on every request; verify cold misses, authorization, commit ordering and all known route consumers. This could shorten concurrent lease time while adding a checkout on cold misses, so it requires measurement rather than assuming fewer checkouts or physical opens.
2. **Fewer audit round trips.** Each actual captured-decision write resolves the live user FK, flushes an audit insert and commits (`api/src/services/access_check_writer.py:256`, `api/src/repositories/audit_logs.py:205`). Investigate combining the FK resolution with the insert while retaining deleted-user attribution, trace/dedupe semantics and evidence. It needs an explicit deletion-versus-insert concurrency test; a naive scalar lookup must not be assumed to solve that race. The goal is fewer SQL round trips and shorter leases, not weaker recording.
3. **Document query work, only after plan review.** The document path can count and select rows separately (`api/shared/table_documents.py:408`). A combined query is only a candidate if it preserves exact totals on empty pages beyond the end and has suitable plans for selective/unselective JSON filters. A naive window-count replacement returning zero for an empty page would be incorrect. No document-query change was made or recommended for automatic implementation here.

The main retention change is the directly measured opportunity for physical opens. These source observations do not prove additional physical-connection savings after warm reuse is established. The next bounded implementation should be selected from measured API CPU and lease-duration evidence. Default settings and further shared-path changes remain for review.

## Evidence, Verification and Cleanup

[Machine-readable baseline](baselines/sdk-query-retention-combined-2026-11-02.json) preserves both arms, their settings, statement catalog/fingerprints, pool events, deadline and event metrics, validity, resources/storage, database observations, image provenance and raw hashes. Raw directory: `/home/jack/bifrost-bench-evidence/runs/sdk-query-retention-combined-20261102-r1`. Supervisor/reducer: `/home/jack/bifrost-bench-evidence/supervision/sdk-query-retention-combined-20261102`. The raw `support/` directory preserves verification and helper inputs. The saved supervisor requires the clean frozen harness, guarded local Kind context, the verified candidate image and passing prior verification receipt; it refuses occupied benchmark namespaces. Reproduction requires a new output directory and the unchanged declaration.

No runtime source changed, so the same immutable image's prior 381 unit cases, nine live cases and API pyright/ruff verification were reused; the complete suites were not rerun or represented as rerun. Assertions verify the same image in both arms, the exact 5+10/15+0 settings, six ready workers, complete counter sums and raw hashes. The supervisor exited successfully, both benchmark namespaces were removed and the owned test stack remains stopped. The implementation worktree remains separate from origin, with no merge needed to continue experiments.
