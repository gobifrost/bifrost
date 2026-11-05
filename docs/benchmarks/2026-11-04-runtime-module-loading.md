# Bounded module loading and S3 connection reuse

The runtime reuses an S3 HTTP pool and caches confirmed missing modules for the
existing 24-hour resolution lifetime. Imports resolve on demand; speculative
execution-local batching has been removed from this PR.
The cache change includes write invalidation and source-refill race fixes; a TTL
change alone would have retained incorrect absence or stale executable source.

## Bounds and invalidation

Resolver metadata and logical-name generations each retain at most 10,000 entries.
Cache keys have a 512-byte ceiling and encoded metadata has a 2,048-byte ceiling.
The two indexes expire after 24 hours without activity. Eviction removes the token
needed to authorize a cached result or in-flight refill; a fresh UUID prevents an
old writer from reviving an entry after eviction. Oversized names still resolve
normally through storage without allocating resolver metadata.

Python source writes/deletions and generation changes are atomic within Redis.
A storage read started before a mutation may return its prior snapshot to that
caller, but cannot refill the shared cache afterward. Git refresh removes cached
workspace source absent from the refreshed tree. File moves invalidate both paths
independently of search-index content, including failed or cancelled moves.
Executable cache updates precede fallible search-index maintenance; Solution hard
deletions invalidate their Python source too. Storage and decoding failures do not
become cached missing modules.

The final scope retains ordinary demand imports. Earlier batching and validation
prototypes are deferred: the fast speculative cache could hide intervening source
mutations, and tested validation designs did not preserve its speedup. No new
module-resolution API or authorization surface remains in this change.

## Matched S3 component comparison

A fresh diagnostic interpreter performed seven alternating pairs of the same 15
read-only production-storage probes: ten absent objects and five empty prefixes.
Both arms used the same signing and verified TLS behavior. No serving process or
workflow source was modified. The measured pool-ownership and connection-reuse functions match the retained
implementation, including cross-thread publication and fork-lock reset.

| Transport | Observations | Median | Range |
| --- | ---: | ---: | ---: |
| Separate HTTP client per operation | 7 | 302.5 ms | 254.1–512.3 ms |
| Shared process/loop-owned HTTP pool | 7 | 182.4 ms | 138.6–411.1 ms |

The median reduction is 39.7%. This is a component comparison, not a workflow
latency or capacity claim. Provider and network timing were uncontrolled, and the
sample is too small for a tail-latency claim. Injected clients remain externally
owned; foreign loops and forked processes cannot borrow another owner's pool.
API and launcher shutdown close the shared pool after their work drains.

## Redis memory probe

The application functions and Lua scripts ran against an initially empty, separate
Redis database in the isolated worktree test stack. Measurements use Redis
`MEMORY USAGE`, including both indexes but excluding server allocator and global
key-dictionary overhead. Source content is not part of these resolver records.

| Distinct names attempted | Retained metadata | Retained generations | Keys and indexes |
| ---: | ---: | ---: | ---: |
| 5 | 5 | 5 | 5,832 bytes |
| 1,000 | 1,000 | 1,000 | 813,248 bytes |
| 10,000 | 10,000 | 10,000 | 8,346,880 bytes |
| 12,000 | 10,000 | 10,000 | 8,247,808 bytes |

A further 1,000 reads of retained names added no keys or measured bytes. Actual
memory depends on names and scopes; these observations demonstrate eviction and
stable repeated reads, rather than a universal byte total for every installation.
Pre-upgrade resolver metadata expires under its original lifetime and is not
used by the new versioned metadata cache. The generation namespace is shared
across versions so writes invalidate both readers during rolling upgrades.
Existing generation keys enter the bounded index on use; untouched old keys
expire under their prior TTL. The byte measurements predate that final namespace
choice and are historical observations, not a deployment memory guarantee.

## Verification and workflow replay

Focused Docker tests cover pool ownership, streaming/cancellation, invalidation,
source-refill races, bounded eviction, scope preservation, a real worker importing ordinary workspace code. Existing SDK/import consumers,
permission inventory, DTO/CLI tripwires and API/client static checks are included.
The merge queue supplies the complete suites; full local backend, Vitest and
Playwright suites are not claimed.

A fresh six-call production baseline used the saved shallow-site, contact-picker
and site-detail cases, sequentially and concurrently. All calls succeeded with the
original inputs/caller and stable serving pods. Final production replay follows
the accepted image rollout. Engine duration, worker wall time and CLI time must
remain separate: their difference includes imports and other work outside the
workflow body, and is not a direct measurement of pure engine overhead.

Private, content-hashed fixtures and raw observations are retained in the benchmark
evidence collection under `supervision/runtime-final-20261104/`. It contains the
paired S3 script/results, cache memory script/results, source snapshots and AST
comparison, original workflow traces, JUnit reports and quality logs. Private
workflow inputs and production identities are deliberately not copied here.

### Earlier Investigation Command Ledger

The following records include batching experiments that were removed from the
final scope. They describe earlier investigation, not the final verification set.

Commands below ran through `./test.sh` against this worktree's Docker stack:

```sh
./test.sh tests/unit/core/test_s3_transport.py tests/unit/scheduler/test_api_host.py tests/unit/test_worker_network.py -q
./test.sh tests/unit/core/test_module_cache.py tests/unit/routers/test_sdk_modules.py tests/unit/services/test_file_ops_read.py tests/unit/core/test_module_cache_import_boundary.py tests/unit/execution/test_sdk_import_cache.py tests/unit/test_virtual_import_s3_fallback.py tests/unit/services/test_worker_sdk_http.py -v
./test.sh tests/unit/services/test_file_ops_read.py tests/unit/test_access_list.py -q
./test.sh tests/unit/routers/test_sdk_modules.py tests/unit/services/test_sdk_module_batch.py tests/unit/execution/test_module_import_prefetch.py tests/unit/test_contract_version.py tests/unit/test_dto_flags.py -q
./test.sh tests/unit/services/test_file_ops_read.py tests/unit/services/test_solution_python_cache_order.py -v
./test.sh tests/unit/test_solution_source_artifact_lifecycle.py tests/unit/test_solution_deploy_s3_after_commit.py -v --durations=0
./test.sh tests/unit/execution/test_module_import_prefetch.py tests/unit/core/test_module_cache_import_boundary.py -v
./test.sh tests/unit/execution/test_module_import_prefetch.py tests/unit/test_worker_s3_code_loading.py -q
./test.sh tests/e2e/platform/test_module_resolution_generations.py tests/e2e/platform/test_virtual_import_integration.py tests/unit/test_solution_source_artifact_lifecycle.py -q --durations=0
./test.sh quality api
```

The S3/lifecycle run passed 70 tests, including concurrent native-thread loops and
a real fork with the parent mutex locked. The first 174-test cache/consumer run
passed 173 tests and exposed a new mock fixture that indexed an iterator. The
fixture was corrected, then corrected again to inject its failure at the intended
DB operation; the final file-operation run passed. Permission verification exposed
two new Redis read sites and a stale generated route inventory. Both were recorded
and regenerated; all 83 file-operation/permission checks then passed. The final
batch/DTO/CLI run passed 99 tests, write-order tests passed 12, Solution-storage
checks passed five, and root-loading/worker-source checks passed 19.

After `main` updated MCP, an already-created quality container still had the old
dependency image and reported missing MCP imports. The rebuilt image was verified
to contain MCP 2.3.0 and its required module, then tagged separately for the final
quality and live runs. Pyright reported zero errors/warnings and Ruff passed.
These were diagnosed iteration failures, not skipped tests or unexplained reruns.

OpenAPI types were generated from the running test API's schema. In a Node 26.13.0
container, `npm run generate:types` (with `OPENAPI_URL=/evidence/openapi.json`),
`npm run tsc`, and `npm run lint` passed. Lint retained one existing console warning
in the review-pack seed helper. The real-fork test intentionally exercises a fork
from the threaded test process and emits Python's corresponding deprecation
warning; neither warning is silenced.

### PR review follow-up

The three CodeQL quality comments on PR #1238 were fixed in source. File-move
cleanup now uses `try/finally` instead of catching and storing `BaseException`.
Storage errors and cancellation propagate naturally; cleanup failures preserve an
existing storage failure and still propagate after otherwise successful storage.
Three new unit cases verify that error precedence. The two flagged test modules
now use one module import style with qualified member access.

```sh
./test.sh tests/unit/core/test_module_cache.py tests/unit/services/test_file_ops_read.py -q
./test.sh tests/e2e/platform/test_module_resolution_generations.py -q --durations=0
./test.sh quality api
```

The focused unit run passed 39 tests, the real-Redis run passed four, and Pyright
and Ruff passed after rebuilding for the intervening dependency update on main.
OpenAPI generation against that updated API produced no further type changes.
The complete protected checks remain the merge gate.

### Generated endpoint records follow-up

The full CI unit job on `ca63610` passed 11,467 tests and failed three
freshness/count checks: the new batch endpoint was absent from the generated
operation inventory, its expected REST count, and the OpenAPI skill digest.
Regenerated the inventory and canonical/public skill digests and updated the
REST count to 696. No runtime behavior changed in this correction.

```sh
./test.sh tests/unit/services/test_operation_inventory.py tests/unit/test_skill_appendix_fresh.py -v
python3 scripts/check_skill_mirrors.py
```

All 12 focused tests passed, and the skill mirror check passed. The full local
unit suite was not repeated; protected CI remains the complete-suite gate.

### Final Scope

Jack authorized proceeding with the recommended narrower scope: bounded shared
cache, invalidation/source-refill correctness, and S3 pool reuse. Batching and its
API models, route, client helpers and dedicated tests were removed together.
The existing on-demand import boundary remains authoritative. Create, update and
delete before first import are checked against workspace and Solution storage.
Matched prototype timings are not a whole-workflow performance claim.

### Final Verification After Narrowing

Against main `418d8ca97`, 323 focused unit/contract checks and 19 live-service
checks passed. A final Git Discard consumer fix then passed all 36 Git sync unit
checks: executable-source refresh now precedes fallible search maintenance.
Pyright reported zero errors/warnings and Ruff passed on the final source.
OpenAPI types, operation/access inventories and both skill mirrors were
regenerated; the final schema has no net change from main. An intervening
source-branch merge was preserved without changing the verified source tree.

```sh
./test.sh tests/unit/core/test_module_cache.py tests/unit/core/test_s3_transport.py tests/unit/scheduler/test_api_host.py tests/unit/test_worker_network.py tests/unit/core/test_module_cache_import_boundary.py tests/unit/execution/test_sdk_import_cache.py tests/unit/routers/test_sdk_modules.py tests/unit/services/test_file_ops_read.py tests/unit/services/test_solution_python_cache_order.py tests/unit/test_virtual_import_s3_fallback.py tests/unit/test_worker_s3_code_loading.py tests/unit/test_contract_version.py tests/unit/test_dto_flags.py tests/unit/test_access_list.py tests/unit/services/test_operation_inventory.py tests/unit/test_skill_appendix_fresh.py tests/unit/test_solution_source_artifact_lifecycle.py tests/unit/test_solution_deploy_s3_after_commit.py -q
./test.sh tests/e2e/platform/test_module_resolution_generations.py tests/e2e/platform/test_virtual_import_integration.py -q
./test.sh tests/unit/services/test_github_sync.py -q
./test.sh quality api
```

All service/build checks ran inside the shared heavy lease; each lifecycle
stopped only this worktree's stack before releasing it. Full backend suites,
Vitest and Playwright were not run locally; protected checks remain the complete
merge gate. Regeneration initially lacked its source PYTHONPATH, corrected
before freshness verification. The new rolling-upgrade test also needed an
Awaitable annotation for Redis's union return stub; the runtime test passed and
the corrected annotation passed final static checks. No failure was retried or
suppressed without fixing its cause.

### Merge-Queue Consent Fixture Repair

Merge group `dc831bae8cf9ee378da5807ce91aeb87efda9d40` failed E2E shard
2 (`126925612570`); aggregate E2E job `126933323828` reported that same failure.
The first MCP approval test expected a signed-in browser but sent a spent
refresh cookie. A prior authority change made its access token stale;
challenge-driven access refresh rotated the fixture's refresh token while its
explicit Cookie header retained the old token. The existing consent guard
correctly rejected that spent cookie. This was test credential setup, not a
reason to weaken the runtime guard or rerun the queue unchanged.

The MCP browser-header fixture now uses the existing human minter before
snapshotting the matching access/cookie pair. The approval cases deliberately
advance authority state first, reproducing the exposing condition. A separate
live check confirms that a spent refresh cookie remains rejected even when the
access token is valid. All eight MCP consent E2E cases and API Pyright/Ruff
passed under the shared heavy lease; owned services were stopped before release.

```sh
./test.sh tests/e2e/api/test_mcp_app_consent.py -q
./test.sh quality api
```

Main `ad8f0f7a2` was integrated to reproduce the exact failed candidate. Its
runtime source tree is unchanged by this repair; only the test file and this
verification/disposition evidence differ. All other merge-group jobs had
passed. Full combined checks remain the protected merge gate after this fix.
