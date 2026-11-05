# SDK Query Amplification: Scoped Repair Comparison

The three repairs reduced repeated work and nearly doubled correct completion throughput under the 8/s offered load: 1.36/s to 2.61/s. They did not resolve overload: the single API process still consumed a full CPU core, and only 66/725 measured arrivals met their 60-second deadline. This is an incremental efficiency gain, not a completed fix for API saturation or a certified hosting capacity.

The approved changes reuse enforced access decisions when recording access, cache empty config views and read one requested config field, and route Redis wake hints to the relevant work family. Database pool settings and authorization epoch/run-liveness checks remain unchanged. The implementation and evidence stay local for review; no PR, push, merge or production deployment was performed.

## Revisions and Experiment

Control source is current main `457134dffb8ec7af12b0a9be437e66a3bc7b7400`; candidate source is `4747fc458a7ae5af064c58bbc4f6618086d61129`. Both images overlay the exact archived API source on the same frozen runtime dependency base and use the same parameter-free SQL/pool counter hook. Every archived source hash was verified under UID 1000 with networking disabled; the database module imported successfully. A removed source module is absent from both images. This isolates the candidate from the many intervening main changes since the earlier `02c0c1543` baseline.

The frozen capacity harness is `2e3e9ca2bb1c72052098c915d001fc9602a1c082`. Four windows were declared before load: control/candidate at 4/s, then control/candidate at 8/s. Each has 30 seconds warm-up, 90 seconds measurement and 60 seconds drain. The SQL counter covers approximately 60 seconds starting 45 seconds after the load action starts. A bounded additional drain separates arms; no measured window is replayed.

All arms have one API process and six warm workers. API resources are 1250m CPU/640 MiB, each worker 650m/1024 MiB, Postgres 1000m/768 MiB, Redis 150m/64 MiB and client 100m/32 MiB. The tenant quota is 6500m CPU/7776 MiB; this is not a 4 GiB hosting envelope. API pools remain main 5 retained + 10 overflow, dispatch 4 + 6, and liveness 1. The theoretical simultaneous API connection ceiling remains 26 and idle retention capacity remains 10. The database target is direct PostgreSQL throughout the measured windows, with no PgBouncer.

Each workflow reads five absent config keys, queries 20 documents and inserts one document; it makes no AI or external Halo call. The setup records 1,100 seeded workflows, 701 tools and 50 data providers, plus eight benchmark functions: 1,859 registrations before load-generator preparation. The table fixture has 200 documents. The scoped write table is precreated empty through the API before load. Catalog and execution history persist between the ordered arms. Both API and all six ready worker images are verified against each arm's immutable image; idle worker Jobs are replaced between arms so publishers and listeners both exercise the intended wake contract.

Helper-only resource amendments match the prior experiment: unused fake API/LLM replicas are zero, the load-driver request is 100m with its 2-core limit retained, and SeaweedFS is 300m. These amendments admit the six-worker fixture on the local 8-core Kind node without altering tenant resources.

## Results

| Offered Rate | Revision | Correct Completions/s During Measurement | Measured Arrivals Correct Within 60s | Median / p95 Send-to-Correct-Terminal | Validity |
|---|---|---:|---:|---|---|
| 4/s | Control | 3.82 | 347/347 | 3.00s / 3.70s, all 347 observed | No flags |
| 4/s | Candidate | 3.89 | 347/347 | 1.02s / 3.74s, all 347 observed | Invalid: external CPU above 1.5 cores in 2/17 intervals |
| 8/s | Control | 1.36 | 0/725 | 115.60s / 125.31s, only 273/725 observed | No flags |
| 8/s | Candidate | 2.61 | 66/725 | 85.99s / 97.03s, only 448/725 observed | No flags |

The 8/s event throughput gain is 92.6%. Both arms still failed the benchmark's performance verdict. The 452/277 unfinished outcomes at the saved reconciliation point make the 8/s latency samples incomplete; their reported p95 values are not whole-cohort percentiles. Later bounded draining reached zero pending/running executions in each arm, but that does not turn late or unobserved outcomes into on-time correctness successes. No duplicate execution, terminal failure, restart, OOM, eviction or quota denial was observed during the recorded windows. That statement does not assert output correctness for outcomes absent at reconciliation.

At 4/s, median latency improved descriptively, while p95 did not. The candidate's external CPU flag and the small cohort prevent treating that arm as a clean latency certification. This result is retained without replay. The 8/s pair has no validity flags, observation failures or counter errors. All four arms observed six ready workers throughout their phase samples, with matching image evidence before and after.

### SQL and Connection Work

These are approximately 60-second API counter windows, distinct from the 90-second arrival cohorts above. They include controllers, submissions, SDK requests and after-response work.

| Counter Metric | Control 4/s | Candidate 4/s | Control 8/s | Candidate 8/s |
|---|---:|---:|---:|---:|
| Counter duration | 60.37s | 60.15s | 60.15s | 60.12s |
| Total SQL statements | 27,393 | 19,450 | 15,471 | 15,874 |
| Correct workflow completions in the same counter window | 235 | 230 | 90 | 168 |
| Aggregate SQL / observed correct completion | 116.57 | 84.57 | 171.90 | 94.49 |
| Config-value SQL statements | 2,331 | 0 | 865 | 0 |
| Package-change SQL statements | 1,330 | 24 | 2,146 | 23 |
| Identity/role/authority relation statements | 7,770 | 5,393 | 4,064 | 4,782 |
| New main-pool connections | 668 | 463 | 675 | 382 |
| API process CPU cores | 0.991 | 0.809 | 0.999 | 0.999 |
| Sampled peak Bifrost-role DB clients, over the longer DB observation | 20 | 19 | 21 | 22 |

At 8/s, raw SQL count rose 2.6% while the counter window observed 86.7% more correct completions. Aggregate statements per observed completion fell 45.0%, and main-pool connection creation fell 43.4%. This ratio describes the process's work during that interval; it is not a per-execution query attribution. SQL shapes are partitioned by their first relation rather than call stack. Increased identity SQL at 8/s accompanies increased work and is not evidence that the token's live epoch/liveness checks should be cached away.

The empty-view cache eliminates repeated config-value SQL in both candidate counter windows. Package checks fall to the existing periodic sweep rate instead of following ordinary workflow wakes. The audit repair removes redundant authority rebuilding while preserving actual evidence writes. The combined intervention does not quantify each repair's separate contribution to throughput.

The allowed connection count and retention capacity did not increase. The observed 8/s DB-client peak did increase by one, 21 to 22, within the unchanged 26/API theoretical ceiling. These sampled peaks are not connection-limit guarantees or post-drain idle-retention measurements. Instrumentation clients use the separate Postgres role and are excluded from these Bifrost-role counts. No connection-exhaustion failure was observed with direct Postgres in these arms; other installation sizes and DB-using agent/platform-job child modes are not covered.

### Resource and Storage Baseline

| Sampled 8/s Metric | Control | Candidate |
|---|---:|---:|
| API mean CPU, cores | 1.000 | 1.000 |
| Postgres mean CPU, cores | 0.325 | 0.270 |
| Redis mean CPU, cores | 0.056 | 0.064 |
| Six workers combined mean CPU, cores | 0.132 | 0.233 |
| API charged peak, MiB | 350.3 | 337.4 |
| Postgres charged peak, MiB | 419.6 | 430.5 |
| Redis charged peak, MiB | 9.6 | 11.9 |
| Six workers combined charged peak, MiB | 1842.4 | 1846.0 |

The API remained busy while the six workers used relatively little CPU collectively. Worker CPU rose with completed work; Redis CPU/memory also rose, while Postgres CPU fell. Memory values are sampled lower-bound peaks, with history retained between arms, so these differences are not isolated memory regressions or per-worker idle baselines. The machine-readable artifact also preserves storage snapshots, quota/fault evidence, backlog series, pool occupancy, host validity and resource coverage. This short run does not estimate long-term storage growth or hosting prices.

The next scoped investigation should profile the remaining API CPU and connection-open/close work under overload. Main-pool churn still occurs hundreds of times per minute. This evidence does not justify declaring 8/s sustainable or scaling workers alone as the remedy, and this task changes no pool settings or deployment defaults.

## Behavior and Compatibility

- Captured enforced decisions retain their original subject/trace. Owned-object notes, coverage gaps, Run As and undecided notes still load current authority. Audit user foreign keys are checked only when an actual row is inserted, preserving deleted-user attribution and existing deduplication.
- One config read uses the complete-view marker and the requested JSON-encoded field in one HMGET, after checking global/org generations. A cold internal read fills the complete merged view once, including an empty view. External reads retain their org-only database path. Atomic snapshot writes, generation changes and commit-before-invalidation ordering prevent partial or stale refill state. Secret authorization and redaction stay intact.
- Wake hints retain the existing Redis channel, durable PostgreSQL dispatch records, claim fencing and periodic reconciliation. Workflow hints no longer wake package/platform/service loops. During a mixed old/new rollout, new listeners ignore legacy `"1"` messages; existing sweeps still claim durable work, but wake latency can temporarily rise until all producers are updated. Old listeners accept the new payloads. The benchmark uses matching images and does not measure this transition.

## Focused Verification

381 distinct unit cases and nine live cases passed. API pyright and ruff passed. Full backend, client and browser suites were not run; no client code or public DTO changed.

Commands, run from the implementation worktree:

```bash
./test.sh tests/unit/services/test_access_check_writer.py tests/unit/services/test_work_dispatch_wake.py tests/unit/test_pool_main_runtime_seams.py tests/unit/services/test_package_generation.py tests/unit/services/test_platform_jobs.py tests/unit/services/worker_pool/test_worker_pool.py tests/unit/test_kubernetes_build_controller.py tests/unit/test_service_lifecycle.py tests/unit/test_service_discovery.py tests/unit/services/test_agent_run_service.py -v
./test.sh tests/unit/test_config_merged_external.py tests/unit/test_sdk_config_single_key.py tests/unit/cache/test_keys.py tests/unit/cache/test_invalidation.py tests/unit/test_access_check_secrets.py tests/unit/routers/test_cli_config_secret.py tests/unit/routers/test_config_update_cache.py tests/unit/test_service_discovery.py::test_indexer_creates_missing_definition -v
./test.sh tests/unit/routers/test_config_update_cache.py tests/unit/cache/test_invalidation.py -v
./test.sh tests/e2e/platform/test_sdk_config_local.py tests/e2e/api/test_access_check_contracts.py -v --durations=0
./test.sh quality api
./test.sh stack down
```

The first wake/audit batch passed 294 cases. The config review batch passed 87 with one new invalid-UUID fixture failure; the corrected router/invalidation batch passed all 31. Earlier cold-load mock failures were repaired to reflect the intended complete-view fill rather than point-query fixtures. Synchronous Redis pipeline/session-add mocks were corrected. Quality found a removed `json` import still required by organization-cache writes; it was restored and quality passed. No failure was skipped or suppressed.

The nine live cases took 20.75 seconds collectively. The new direct cache-mutation case took 0.29 seconds and launches no job. Existing worker config-read setup took 7.09 seconds; access-contract setup took 7.52 seconds and shares two workflow runs across the policy/secret assertions. The separate existing legacy-admin-rule run took 4.16 seconds. These cross-process fixtures prove the worker/API and enforced-evidence boundaries; they were not expanded with duplicate background jobs.

## Evidence and Reproduction

[Machine-readable baseline](baselines/sdk-query-amplification-2026-11-01.json) records immutable revisions/images, complete counter fingerprints/statements, observed rates and outcomes, resource/storage snapshots, validity flags, verification and raw evidence hashes. Private raw directory: `/home/jack/bifrost-bench-evidence/runs/sdk-query-amplification-20261102-r4`. Supervisor, image manifests, test JUnit/logs, source hashes and reducer are under `/home/jack/bifrost-bench-evidence/supervision/sdk-query-amplification-20261102`; the raw directory's `support/` also archives reproduction and verification inputs. All four windows completed, the supervisor exited successfully, and both benchmark namespaces plus the owned test stack were removed.

The saved supervisor requires the clean frozen harness checkout, its guarded local Kind cluster, both immutable images loaded into that cluster, the recorded fixture-helper hash and a passing candidate verification receipt. It refuses occupied benchmark namespaces. To repeat, use a new output directory, retain the declaration, verify source/image provenance and run the saved supervisor from the frozen harness checkout. Never use an unqualified kubectl context: this workstation's default context is production. The helper-only scheduling amendment and direct-database transition are part of the saved supervisor, not installation defaults.

Three preparation failures preceded offered load: a missing report metadata field and two reproductions of a non-root overlay file-permission defect. They produced no workload measurements. The permissions were repaired identically for both images and checked at UID 1000 before this run. Failure receipts and cleanup evidence are preserved; preparation failures are not platform performance results.

The comparison measures a combined intervention, with one ordered pair per rate and retained history. It does not isolate each repair's causal share, certify a maximum rate or worker-count scaling, retest production Halo workflows, or measure cold scaling and long-duration storage growth. The 4/s cohorts contain fewer than the harness's 400-observation statistical minimum: their p95 values are descriptive and do not certify the latency SLO. Sampled resource peaks are lower bounds. Counter-window SQL and 90-second arrival cohorts have different bounds; aggregate SQL per completion in the same counter window is an efficiency ratio, not the exact query cost of one execution.
