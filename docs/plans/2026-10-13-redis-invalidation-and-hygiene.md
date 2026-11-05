# Redis invalidation and lifecycle follow-up

## Evidence and scope

This follows issue #982 and PR #983. That immediate repair reduces repeated
resolver scans to one traversal with COUNT 1000. PR #983 merged on 2026-10-13 as
`e20f3c235095017bd8fb7f8b28ffd171a54f8804`. This follow-up was rebased onto
that current main without conflicts; the predecessor added only its disposition
approval after this branch was created.
The follow-up changes the invalidation algorithm; it retains the predecessor's
exact-name, ancestor, cross-scope, and unrelated-key regression contracts while
updating their command expectations from one scan to zero scans.

The supplied read-only production investigation measured approximately 90,650
keys. A COUNT 10 traversal took 7,073 requests / 6.073 seconds; COUNT 1000 took
91 requests / 0.125 seconds. Publication of 75 Python files caused 209 traversals.
SCAN MATCH filters returned keys but traverses the complete keyspace. Memory was
approximately 69 MB / 438 MB with zero evictions; 97% of a 900-key TTL sample was
expiring. These observations support an invalidation latency repair, not a Redis
memory emergency. No production key identities or values are recorded here.

## Resolver decision

Source writes/deletes enter through `module_cache.set_module` / `invalidate_module`
from file operations, file-index updates, Git sync, and Solution publication.
`shared.sdk_modules.resolve_module_name` is the sole scoped metadata producer.
`module_cache_sync._get_cached_module_resolution` is the worker's direct Redis
consumer. Both share the stdlib-only `module_cache_contract`; the synchronous
import graph must not import the async storage stack. Executions retain their
existing local import snapshot; this repair changes shared Redis invalidation,
not Python's already-imported modules within an execution.

Two alternatives were considered:

| Approach | Invalidation | Lifecycle and concurrency cost |
| --- | --- | --- |
| Per-name scope index | Read indexed scope keys, then delete | Every write must atomically register membership; expired results leave members requiring pruning; a delayed lookup needs an additional fence |
| Per-name random generation (selected) | Rotate exact-name and ancestor tokens in one transaction | One expiring token per active name, no membership index; guarded cache writes fence late lookups |

Metadata uses a new v2 prefix. Each entry contains its captured generation and
result. An atomic Redis script reads the name token and scoped result together,
returning only matching generations. The API captures the token before storage
probes; its write script stores the result only if that token is still current.
Source updates rotate random tokens for the exact logical name and ancestors,
shared across every Solution/global-repository access scope. Descendant names
and similarly spelled siblings retain their generations, matching #983.

Tokens and positive results expire after 24 hours; negative results expire after
30 seconds. A missing token makes every prior result unreadable. Recreating a
token uses a fresh random value, avoiding generation reuse after expiration.
Invalidated results expire naturally; no persistent resolver index or routine
whole-keyspace scan remains. Explicit administrative `clear_module_cache` may
still scan the v2 metadata and generation namespaces with COUNT 1000.

## Agent-step lifecycle

`AutonomousAgentExecutor._record_step` is the stream producer. The SDK uses streams
while runs are queued/running/cancelling; successful, failed, and delegated run
finalization already attempts immediate deletion. The producer lacked the TTL
that cleanup comments assumed. Appends will commit XADD MAXLEN ~1000 and a
24-hour sliding expiry in the same transaction. Existing terminal cleanup stays;
run retention will remove streams for the exact AgentRun IDs it deletes.

A bounded dry-run inspector will compare stream UUIDs with AgentRun rows and
protect streams whose newest entry is less than 24 hours old. It returns counts
and a scan cursor, never identifiers or values, and does not mutate Redis. It is
an operational preview, not a new recurring job or deletion authorization.

## Publication observation

Use existing PlatformJob reports and logs. Pass observation callbacks through
the current deploy/install call chain, report coarse monotonic phases, and record
aggregate elapsed times for source/S3, file-index, Redis, app artifacts, and
activation. Time failed operations as well as successful ones. Avoid per-file
persistent progress/log writes and do not raise deadlines. Existing request and
WebSocket contracts remain unchanged.

## Proportionate shared-cache audit

| Category | Lifetime / cleanup | Disposition |
| --- | --- | --- |
| Config merged views | Five-minute hashes; one persistent global version token (`cache/keys.py`, `repositories/config.py`) | Constant-size persistent control state is intentional; no change |
| Module source | 24-hour values; persistent path SET; explicit deletes remove membership (`module_cache.py`, `module_cache_sync.py`) | SET can retain expired path history; track cardinality separately, then consider a scored expiry index if material |
| Agent steps | MAXLEN streams; previously no expiry | Repair in this PR; historical orphans require separately approved cleanup |
| Pending/active execution state | One-hour TTLs refreshed while live; terminal delete (`redis_client.py`, `process_pool.py`) | No missing-expiry repair identified |
| Pending execution queue | PostgreSQL `WorkDispatch` joined to Pending executions; queue positions are published from committed rows (`queue_tracker.py`) | Replaced the Redis sorted-set index after a claim/publication race left completed executions in the queue; no separate expiry or cleanup loop |
| Auth | Refresh JTIs seven days / delete on use; OAuth state ten minutes / single-use; device pairs five minutes (`routers/auth.py`, `oauth_sso.py`) | No values inspected; no changes to authorization or revocation |
| Data-provider results / locks | Five minutes / ten seconds (`cache/data_provider_cache.py`) | Broad invalidation still scans; separate optimization if cardinality warrants |
| User roles / forms | Role payloads one hour; form/user caches use shared bounded TTLs (`shared/role_cache.py`, `cache/invalidation.py`) | Role and form mutation paths still scan; monitor mutation cost before a separate algorithm change |

Source review found no basis for a blanket flush or generic TTL on every index.
Role/form mutation, auth revoke-all, organization/endpoint bulk invalidation,
worker discovery, and explicit module reset still have SCAN paths. A MATCH pattern
does not bound their traversal time. These are recorded for separate measurement;
this change removes the measured publication amplification rather than changing
unmeasured authorization caches. The module path SET serves only explicit full
cache clearing, not ordinary resolver reads. A TTL refreshed on every insertion
would not prune historical members under continuous traffic, so no misleading
blanket index expiry is added here.

## Verification and rollout

Use this worktree's isolated Docker test stack; serialize state-resetting test
commands. Cover real Redis token expiry, late-write fencing, scoped synchronous
reads, a large unrelated keyspace, stream TTL and refresh, orphan preview, and
retention deletion. Run focused import-boundary, SDK resolver, publication,
retention, and executor tests plus API lint/type checks. Retain one existing
Solution deploy happy path rather than introducing additional full deploy jobs.

With #983 landed, roll out API, worker, and scheduler together after
draining in-flight executions/publications: older processes use unversioned
metadata and do not observe v2 generations. Do not leave old and new writers or
readers serving concurrently. Existing unversioned metadata expires within its
existing 24-hour bound; it need not be flushed. Rollback requires a coordinated
restart and a reviewed resolver-cache cold reset if old metadata could still be
present. Newly added stream TTLs do not retroactively expire historical streams.

## Separate operational cleanup recommendation

After rollout, take a bounded read-only orphan preview in small cursor pages.
Record aggregate counts, cursor, truncation, and observation time. Protect any
stream with an AgentRun row, a recent newest entry, invalid identity, or unknown
Redis type. SCAN is not a snapshot; repeat observation before proposing deletion.
For the historical persistent orphan population, prepare a separately reviewed
small-batch deletion or expiry operation that rechecks DB absence and newest
entry immediately before mutation. It requires explicit production approval.
Do not flush Redis, bulk-delete by glob, or treat missing rows alone as sufficient
for deleting potentially active streams. This PR authorizes no production cleanup.

The read-only operational entry point, inside an API container, is:

```bash
python scripts/inspect_agent_run_step_streams.py --cursor 0 --limit 100
```

Continue with the returned `next_cursor`; zero means the traversal ended. Each
call performs one SCAN and inspects at most 100 keys. A nonzero `uninspected_keys`
means the page was truncated: counts are incomplete, and advancing the returned
cursor skips those uninspected keys. Treat such a run as a bounded sample, not a
complete inventory. Manual retention dry-runs include one page of these counts.
No preview result is a deletion plan that can be applied automatically.

## Focused verification commands

Run in `/home/jack/workspace/bifrost-redis-hygiene` using project
`bifrost-test-3393dc7d`:

```bash
./test.sh tests/unit/core/test_module_cache.py tests/unit/core/test_module_cache_import_boundary.py tests/unit/routers/test_sdk_modules.py tests/unit/test_virtual_import_s3_fallback.py tests/unit/execution/test_sdk_import_cache.py tests/unit/services/test_agent_run_step_streams.py tests/unit/jobs/test_run_retention_job.py tests/unit/jobs/test_solution_deploy_platform_job.py tests/unit/test_file_index_writers.py tests/unit/test_solution_deploy_s3_after_commit.py tests/unit/services/test_autonomous_agent_executor.py tests/unit/routers/test_solution_deploy_jobs.py -q
./test.sh tests/e2e/platform/test_module_resolution_generations.py tests/e2e/platform/test_virtual_import_integration.py -q --durations=0
./test.sh tests/e2e/platform/test_agent_run_step_stream_lifecycle.py tests/e2e/platform/test_run_retention_deleter.py tests/e2e/platform/test_solution_deploy_async.py::test_async_deploy_completes -q --durations=0
./test.sh quality api
```

The first combined unit run found incorrect Redis pipeline mocks in two executor
tests (unawaited coroutine warnings). The test doubles were corrected to queue
commands synchronously and execute asynchronously, matching redis-py. An earlier
resolver-only run found one obsolete GET-call expectation; it now checks the
atomic metadata read plus the same scoped source lookup behavior. No retries,
timeout increases, skips, or warning filters were added.

The 10,000-unrelated-key resolver regression took 0.40 seconds of test call time.
The resolver endpoint case's 3.83-second setup dominated its 0.52-second call;
these tests launch no deployment jobs. The retained Solution deploy happy path
launches one source-only deploy job; stream/retention edge cases use direct
Redis/database services without additional queued jobs or app builds.

Full backend, full client/Vitest, full Playwright, `pre-pr`, and `pr-preflight`
suites are not part of this focused local run. No production benchmark after
implementation, production mutation, deployment, or merge is performed.

## Isolated publication benchmark

The predecessor added a reusable benchmark while this work was in progress.
Its fixture and correctness check now use the shared resolver writer/reader so
both physical deletion and generation invalidation are checked by visible
results. Its owned cleanup includes generation tokens. PR #983's branch and its
recorded benchmark results remain untouched.

One paired sample on this worktree's isolated Compose Redis DB 15:

| 75 source files, 90,000 unrelated keys, 324 scoped resolver entries | PR #983 | Generations |
| --- | ---: | ---: |
| Cache publication elapsed | 3.0699 seconds | 0.0411 seconds |
| SCAN requests during publication | 6,825 | 0 |
| Invalidation correctness | Passed | Passed |

Cleanup left zero keys in that isolated database. This is approximately 75 times
faster in one local cache-only sample, not a production latency guarantee.
No deployment, S3 writes, database indexing, or app builds occur in this benchmark.
Raw evidence is in
[`2026-10-13-resolver-generation-results.json`](../performance/2026-10-13-resolver-generation-results.json).

Exact reproduction commands used:

```bash
git show 5e77242c0:api/src/core/module_cache.py > /tmp/bifrost-redis-hygiene-baseline.py
docker cp /tmp/bifrost-redis-hygiene-baseline.py bifrost-test-3393dc7d-api-1:/tmp/module-cache-baseline.py
docker cp scripts/benchmark_module_cache_invalidation.py bifrost-test-3393dc7d-api-1:/tmp/module-cache-benchmark.py
docker cp scripts/fixtures/module_cache_publication_paths.json bifrost-test-3393dc7d-api-1:/tmp/module-cache-paths.json
docker exec -e PYTHONPATH=/app bifrost-test-3393dc7d-api-1 python /tmp/module-cache-benchmark.py --baseline-source /tmp/module-cache-baseline.py --paths /tmp/module-cache-paths.json --keys 90000 --rounds 1 --baseline-rounds 1
```

Additional focused verification after correcting pipeline test doubles:

```bash
./test.sh tests/unit/services/test_autonomous_agent_executor.py tests/unit/services/test_agent_run_step_streams.py -q
```

All 62 cases passed without coroutine warnings. The existing harness emitted
28 dependency deprecation warnings; they were not suppressed.
The retained source-deploy job took 10.25 seconds of call time and 3.65 seconds
of setup; the stream/retention integration batch's 10 cases passed in 17.23 seconds.


Final API verification: `./test.sh quality api` passed pyright with zero errors,
warnings, or informational diagnostics, and ruff reported all checks passed.
A final reporter-only run covers capped/monotonic progress and non-fatal diagnostic
logging in addition to the deploy adapter and app timing contracts:

```bash
./test.sh tests/unit/jobs/test_solution_deploy_platform_job.py -q
```

Final reporter run: 4 passed (6.67 seconds). Across the combined unit run and
this additional reporter case, 171 distinct focused cases were exercised.
Integration coverage comprised 19 distinct passing cases across the two batches.

Review: [PR #984](https://github.com/gobifrost/bifrost/pull/984). Jack authorized
queueing after #983 merges. Product Updates classification is `other` / `fix`
with approval evidence recording that instruction.

The operational script smoke check passed with aggregate-only output. The local
API test image does not mount `/app/scripts` (the production image packages it),
so the scratch smoke check copied the script into that container before running:

```bash
docker cp api/scripts/inspect_agent_run_step_streams.py bifrost-test-3393dc7d-api-1:/tmp/inspect-agent-step-streams.py
docker exec bifrost-test-3393dc7d-api-1 python /tmp/inspect-agent-step-streams.py --cursor 0 --limit 1
python3 scripts/product_updates.py validate --content-dir product-updates --inventory product-updates/inventory.json --dispositions product-updates/dispositions.json --target $(python3 -c 'import json; print(json.load(open("product-updates/inventory.json"))["target_ref"])')
```

Product Updates structural validation passed after adding the PR-numbered
disposition. No known test/check failures remain. No production cleanup or rollout
was executed.

## Post-dependency verification

After #983 merged, rebased onto current `origin/main` without conflicts. The
combined focused unit command listed above passed all 171 cases (13.76 seconds),
and the combined five-file integration command passed all 19 cases (20.25 seconds):

```bash
./test.sh tests/e2e/platform/test_module_resolution_generations.py tests/e2e/platform/test_virtual_import_integration.py tests/e2e/platform/test_agent_run_step_stream_lifecycle.py tests/e2e/platform/test_run_retention_deleter.py tests/e2e/platform/test_solution_deploy_async.py::test_async_deploy_completes -q --durations=0
./test.sh quality api
```

JUnit reported zero failures/errors/skips in both runs. API pyright reported zero
errors/warnings/information and ruff passed. Product Updates validation and
`git diff --check` passed. The original broader-suite limitations still apply;
the merge queue supplies the complete CI gate. No production mutation occurred.

## CI failure disposition

The first main-target preliminary Unit Tests gate ran 9,106 cases: 9,104 passed,
two failed. Both were reproducible changes in test coverage missed by the initial
focused set, not intermittent product failures:

- The session-management test double expected direct `XADD` and made
  `pipeline()` async. It now queues synchronous pipeline commands and asserts
  each buffered step executes a Redis transaction, retaining the no-DB-write
  and no-session-during-LLM contracts.
- The Redis-read inventory did not recognize Lua reads. It now detects
  `eval`/`evalsha` on Redis receivers, keeps resolver scripts classified as
  authority reads, and classifies aggregate orphan diagnostics as plain reads.
  This also exposed existing chat sequencing and audit-claim settlement scripts;
  their ordering/diagnostic reads are classified explicitly after source review.

```bash
./test.sh tests/unit/services/test_agent_executor_session.py tests/unit/test_access_list.py::TestElevatedInventories::test_every_redis_read_is_classified -q
./test.sh tests/unit/services/test_agent_executor_session.py tests/unit/test_access_list.py tests/unit/services/test_autonomous_agent_executor.py tests/unit/services/test_agent_run_step_streams.py -q
./test.sh tests/unit/test_access_list.py::TestElevatedInventories::test_every_redis_read_is_classified tests/unit/test_access_list.py::TestScannerBlindSpots::test_b10_redis_reads_are_recognised_by_receiver tests/unit/services/test_agent_executor_session.py -q
```

The first command reproduced both CI failures. The second exercised 137 cases:
136 passed and the expanded inventory exposed the two existing unregistered Lua
owners. After classifying those scripts, the final six focused cases passed
(13.88 seconds; JUnit zero errors/failures/skips). Session pipeline coroutine
warnings are eliminated. No runtime source, retries, skips, timeout increases,
or warning filters were changed to resolve these failures.

API quality checks passed again after the CI corrections: pyright zero
errors/warnings/information and ruff clean.

## Merge-queue E2E failure disposition

The first synthetic queue candidate failed one case in E2E shard 3/3 (703
passed; three existing skips): the new orphan test assumed a single SCAN page
would include its three matching streams. A populated shared keyspace exposed
an empty first page. The production inspector correctly returns one bounded
page and its continuation cursor; this was a test-harness assumption.

Reproduced locally by seeding 1,000 owned unrelated keys and limiting inspection
to one key per page: the old assertion failed with the same zero scanned count.
The corrected test follows continuation cursors through one complete traversal
using bounded pages, requires an empty intermediate page, verifies all three
lifecycle classifications, and verifies no stream was deleted. The fixture
deletes only its owned keys in `finally`; it never flushes Redis.

```bash
./test.sh tests/e2e/platform/test_agent_run_step_stream_lifecycle.py::test_orphan_inspection_uses_agent_run_rows_and_never_deletes -q
./test.sh tests/e2e/platform/test_agent_run_step_stream_lifecycle.py tests/e2e/platform/test_run_retention_deleter.py -q --durations=0
./test.sh quality api
```

The first command reproduced the failure before correction. After the fix, all
nine integration cases passed (3.24 seconds, JUnit zero errors/failures/skips).
The inspector's runtime behavior and bounded operational contract are unchanged;
there are no retries, skips, or longer timeouts.

API quality checks passed after this E2E correction: pyright zero
errors/warnings/information and ruff clean. Main had not advanced.
