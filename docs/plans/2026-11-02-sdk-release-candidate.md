# SDK Performance Release Candidate

The approved release scope is the three measured SDK query repairs, automatic reuse of the existing bounded main-pool allowance, and the concurrent first-table-creation defect exposed by the benchmark. Further audit, document-query, and authentication-transaction optimizations remain separate hypotheses. The user authorized merging after verification and Astra review, with no additional installation knobs, and requested a comparison against the saved hosting envelopes.

## Connection Policy

`get_engine` owns the main-pool reuse policy for every process. For positive pool size and nonnegative overflow, retain their sum and set actual engine overflow to zero. Defaults remain expressed as 5+10; the engine reuses up to 15 lazily opened connections. A size-only or overflow-only override keeps exactly its previous combined peak. Size 0 and overflow -1 retain SQLAlchemy's unlimited semantics. Settings are never mutated.

Dispatch remains 4 retained + 6 overflow; liveness remains 1. One API process therefore still allows at most 26 simultaneous connections with defaults. All-pool idle retention can rise from 10 to 20 after a burst. This is a real database resource cost, not extra peak admission or a promise that arbitrary process counts fit any database. Additional API processes and DB-owning children still require database capacity. Existing worker 1+2 settings retain the same three connections as before; their duplicate worker-local conversion is removed in favor of the shared policy.

No environment variable, mandatory pooler, Uvicorn process count, or customer tuning step is introduced. The policy uses SQLAlchemy's ordinary process-lifetime pool, with request-scoped sessions borrowing and returning connections. Pools open lazily and retain the existing recycle and disconnect behavior. Astra's read-only review approved the three SDK repairs and recommended this bounded normalization, including explicit unlimited-sentinel tests.

SQLAlchemy's [official pooling documentation](https://docs.sqlalchemy.org/en/20/core/pooling.html) describes cross-request connection reuse, lazy growth, overflow disposal, and the unlimited sentinels. This change uses that existing pool machinery rather than introducing another pool implementation.

## Table-Creation Repair

The benchmark exposed missing ad-hoc table uniqueness indexes after the migration that dropped `orphaned_at`. Two independent sessions can both create the same name in one organization. A deterministic regression test reproduced two successful creates instead of one create and one ordinary conflict. Restore the intended global and organization constraints in a forward migration and preserve the separate Solution namespace. Existing duplicate data must receive an explicit non-destructive disposition; the migration must never delete, merge, or rename customer tables.

A read-only check of the current production database on 2026-11-02 UTC found both ad-hoc indexes absent, the Solution index present, and zero duplicate ad-hoc `(organization_id, name)` groups. Its Alembic revision was `20261031_app_sdk_binding`. No production schema or data was changed. Other installations still need the migration's duplicate preflight.

## Hosting Comparison

Declare eight serial diagnostic windows before offering load: `4gb-capacity` and `m-capacity`, each with current-main control and the exact verified candidate, each at 2/s and 4/s. Use the frozen harness, common dependency base/counter hook, unchanged canonical profile resources and PgBouncer, and explicitly set two warm/two maximum workers for steady-state comparison. Each window has 30 seconds warm-up, 120 seconds measurement, 60 seconds drain, and at most 120 seconds additional recovery. Do not search or replay until a rate passes.

Control is current main `457134dffb8ec7af12b0a9be437e66a3bc7b7400`, not an older historical optimized runtime. Start each profile with a fresh seed and run control before candidate so the candidate's migration only moves forward. Precreate the same synthetic write table in both arms to isolate SDK throughput from the separately tested first-create race. Verify API, init-container, and worker images; do not leave workers on the other side of the typed wake contract. Stop the owned Docker test stack before timed load.

`4gb-capacity` is a four-CPU, four-decimal-GB namespace envelope, not a commercial 4 GiB/two-CPU node. M has four CPUs and 8 GiB. Both have one API and at most two workers, with their distinct saved component limits. Object storage, image builds, load generation, and cluster services are outside these namespace allocations and must remain separately budgeted. There are no canonical commercial 2/4/8 GiB plans to invent here.

Record offered and correct completion rates, deadline correctness, latency and missing outcomes, backlog/drain, CPU/memory, SQL and physical connection events, database clients, storage growth, admission/health faults, and host contention. Preserve all validity flags and raw evidence. The 2/s window has approximately 240 measured arrivals, below the harness's 400-arrival capacity certification minimum: these are matched diagnostics, not certified maximum capacity or node-density claims. Storage deltas include all fixture, warm-up, measured, and drain work; do not multiply a short interval into an unsupported retention forecast.

## Release Gates

Run focused pool, worker lifecycle, SDK/audit/config/wake, table-concurrency, migration-upgrade, and relevant live-boundary tests plus API lint/type checks. Run the migration template reset once after the forward revision lands. Review the final source with Astra, record any known failure's cause and repair, and retain the exact verification commands. Complete-suite verification belongs to the merge queue; unrun suites are not green by inference.

Typed wakes remain delivery hints over durable database claims. During a mixed-version rollout, new listeners ignore old broadcast payloads and may wait up to the existing 15-second sweep for old producers. Roll API and worker images together to minimize that window; no execution is lost. No permanent legacy branch is added.

Create and attach the scoped PR after the candidate is reviewable, classify its Product Update, monitor checks/reviews, and merge only after the stated gates pass. Do not include speculative optimizations or silently expand resource limits to obtain a better result.

## Focused Verification

The existing query-repair commit has 381 distinct passing unit cases and nine live cases, with commands and evidence in [its report](../benchmarks/2026-11-01-sdk-query-repairs.md). The additional release work passed these focused commands:

```bash
./test.sh stack reset
./test.sh tests/unit/core/test_database_pools.py tests/unit/core/test_database_tls.py -v
./test.sh tests/unit/test_pool_main_runtime_seams.py tests/unit/test_pool_main_hosting.py tests/unit/test_pool_main_packages.py tests/unit/test_pool_main_summaries.py tests/unit/core/test_database_retry.py -v
./test.sh tests/unit/core/test_database_pools.py tests/unit/sdk/test_sdk_table_metadata.py tests/unit/sdk/test_sdk_tables_local.py tests/unit/sdk/test_sdk_tables_writes_local.py tests/unit/test_tables_sdk_solution_scope.py tests/unit/test_table_resolution_reach.py tests/unit/routers/test_tables_management_permissions.py tests/unit/routers/test_tables_policy_publish.py -v
./test.sh tests/e2e/platform/test_sdk_config_local.py tests/e2e/api/test_access_check_contracts.py tests/e2e/platform/test_sdk_tables_writes_local.py tests/e2e/platform/test_tables.py tests/e2e/platform/test_table_name_uniqueness_migration_rehearsal.py -v --durations=0
./test.sh tests/unit/test_table_name_uniqueness_migration.py -v
./test.sh quality api
./test.sh stack down
```

These runs passed 15, 57, 83, 52, and one case respectively; counts overlap and are not a distinct-test total. The live run took 35.87 seconds. Its two migration rehearsals took 3.36/3.49 seconds each, running direct disposable-database upgrades with no workflow jobs. The new write happy path reused one existing workflow job and took 0.38 seconds; the longer live timings were existing workflow setup/authorization checks. Six warnings came from Alembic's existing `prepend_sys_path` separator deprecation; no test was skipped or retried.

The initial deterministic concurrency test failed on the old schema with two successful creates, proving the missing constraint. After the repair it passes with one create, one ordinary conflict, and both documents on one table. Initial quality checks caught a new rehearsal helper's inferred `dict[bytes, bytes]` return and the removed worker helper's now-unused `Settings` import. Explicit string conversion and removing the unused import corrected them. The final API pyright/ruff check passed with zero errors or warnings, and the owned test stack was stopped before benchmark load. No failure was hidden. Astra's final read-only source review approved the pool policy, table constraints, narrow conflict mapping, locked duplicate preflight, tests, and rollout runbook. Full backend/client/browser suites were not run locally; no client or public DTO changed. The merge queue remains the complete-suite gate.

## Completed Hosting Comparison and Consolidation

All eight declared hosting windows completed and drained, with cleanup verified. The clean 4/s pairs reduced p95 from 3.178s to 0.567s in 4 GB and 2.412s to 0.548s in M; API CPU fell 27.5% and 26.2%. Both arms completed 472/472 measured arrivals within deadline. Worker charged memory stayed essentially unchanged; namespace charged peaks rose by about 36/56 MiB. The 4 GB candidate 2/s observation retains its external-CPU flag. Short-window drift remains inconclusive, so these are efficiency baselines, not maximum-capacity certificates. The [complete result report](../benchmarks/2026-11-02-sdk-hosting.md) and machine-readable baseline retain all windows, resource/storage measurements, validity, source/image proof and raw hashes.

An older draft, PR #1164, already addressed the table race. Its useful repository savepoint/duplicate-error contract was consolidated into this candidate, preserving one forward migration and one upgrade notice. Astra required the repository's concurrent duplicate path to match its ordinary `ValueError` contract while retaining a usable caller transaction; the redundant REST-specific integrity mapper was removed. The older draft will be closed as superseded after this source PR merges.

The measured image remains immutable at source `960856144`. Final runtime source `e5675cc43` differs only in metadata creation/error handling in the tables repository and create route; all other archived runtime hashes match. None of the eight captured API logs contains a metadata-create request because both arms used the declared precreated table. Astra approved verifying that creation-only delta separately. After benchmark cleanup, the following passed 36 unit/live cases, followed by API Pyright/Ruff with zero errors or warnings:

```bash
./test.sh tests/unit/sdk/test_sdk_table_metadata.py tests/unit/routers/test_tables_policy_publish.py tests/unit/routers/test_tables_management_permissions.py tests/e2e/platform/test_sdk_tables_writes_local.py -v --durations=0
./test.sh quality api
```

## CI Access-Inventory Repair

The source PR's first broad backend unit run passed 11,251 cases and failed the Redis-read inventory tripwire. The new organization invalidation-generation read and config point read lacked inventory classifications, and the conservative scanner also counts two ordinary dictionary completion-marker lookups in the merged reader. The registry now identifies each read and explains the dictionary matches; no scanner exclusion or runtime change was added.

```bash
./test.sh tests/unit/test_access_list.py tests/unit/test_sdk_config_single_key.py -v --durations=0
./test.sh quality api
```

The focused inventory/config run passed all 75 cases in 69.20 seconds, including the exact CI failure and the scanner's own regression coverage. API Pyright/Ruff also passed with zero errors or warnings. This is a test-inventory repair; the measured runtime sources and hosting results remain unchanged.

## Queue Authentication-Fixture Repair

The first merge-group run passed backend/client unit tests, two of three backend integration shards, lint/type checks, both candidate builds and browser smoke. Shard 3 exposed a pre-existing cross-process fixture leak: the ephemeral CLI subprocess test copied the shared REST user's single-use refresh pair. A child-side authority-epoch refresh consumed that pair without updating the parent fixture, causing the later shared REST requests to fail authentication.

A deterministic reproduction forced a committed authority-epoch change before the child request and then refreshed the parent. The child succeeded but the parent failed to refresh (one failure in 4.37 seconds), proving the leak without a timing dependency. The existing test now inherits the independent CLI fixture's environment credentials and retains both the forced child refresh and the parent-refresh assertion. No runtime authentication, retries, timeouts, pool settings or benchmark sources changed. Read-only review confirmed the session isolation and the observed CI failure sequence.

```bash
./test.sh tests/e2e/platform/test_cli_ephemeral_login.py tests/e2e/platform/test_cli_events.py tests/e2e/platform/test_cli_push_pull.py tests/unit/test_e2e_fixture_refresh.py -v --durations=0
./test.sh quality api
```

The repaired test and its downstream CLI event/file consumers passed all 27 selected cases in 9.80 seconds; API Pyright/Ruff passed with zero errors or warnings. The new regression's call took 0.83 seconds and fixture setup 0.31 seconds. It adds no workflow/deploy/build job: the only new operations are the committed epoch bump and explicit parent refresh.
