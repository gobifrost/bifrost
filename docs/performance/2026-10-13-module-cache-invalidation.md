# Resolver invalidation performance: PR #983

On 2026-10-13, the actual baseline and fixed `set_module()` implementations were
compared against the same isolated Compose Redis instance. The workload contains
90,000 unrelated keys, 324 scoped resolver keys, and the 75 Python source paths
from Microsoft CSP 0.1.21. Source and index keys are preseeded so both versions
start with the same keyspace. No production keys were written or deleted.

| Cache publication workload | Baseline | PR #983 |
| --- | ---: | ---: |
| Source updates | 75 | 75 |
| Full keyspace traversals | 209 | 75 |
| Redis SCAN requests | 1,824,465 | 6,825 |
| Completed elapsed times | 257.0327 s | 3.1572 / 4.3896 / 5.0835 s |
| Median elapsed time | 257.0327 s (one sample) | 4.3896 s |

The fixed path is 51–81 times faster in these runs, with 267 times fewer SCAN
requests. All completed runs verified that affected resolver entries across four
scopes disappeared, the unrelated resolver entries remained, unrelated data
survived, and source data was written. Cleanup left DB 15 empty.

One paired comparison completed. Additional baseline repetitions were stopped
intentionally after that pair to avoid repeating the four-minute workload; no
second baseline timing is included. Two fixed-only repetitions then completed
normally. This is a workload measurement with one baseline sample, not a
statistical latency guarantee. Raw completed results are in
[the results file](2026-10-13-module-cache-invalidation-results.json).

## Expected production outcome

Earlier read-only production measurements found about 90,650 keys and a default
COUNT-10 traversal taking 6.073 seconds. COUNT 1000 took 0.125 seconds for a full
traversal. Applied to this package's traversal counts, the scanning component is
estimated to fall from `209 × 6.073 = 1,269 seconds` (21.2 minutes) to
`75 × 0.125 = 9.4 seconds`. These are projections from individual traversals,
not observed production deployment durations. Keyspace shape, resolver density,
network latency, and concurrent activity affect them.

The evidence supports removing the scan-driven 15-minute timeout. Total deploy
latency still includes app builds, S3 writes, database indexing and finalization.
The platform patch must land and be released before retrying CSP and measuring
its complete deployment. Routine invalidation still scans once per source file;
the separate Redis lifecycle follow-up removes that remaining scaling dependency.

## Reproduce

Use the PR worktree's test stack. `./test.sh stack status` identifies its Compose
project; set `BENCHMARK_API_CONTAINER` to that project's API container name.
The benchmark always connects to the Compose service `redis`, DB 15, refuses a
nonempty DB, uses expiring fixture data, and deletes only its own keys afterward.
Do not run it in production or alongside another benchmark using DB 15.

```bash
./test.sh stack up
BENCHMARK_API_CONTAINER=bifrost-test-6a8ebf61-api-1

git show 602c58813:api/src/core/module_cache.py > /tmp/module-cache-baseline.py
docker cp /tmp/module-cache-baseline.py "$BENCHMARK_API_CONTAINER":/tmp/module-cache-baseline.py
docker cp scripts/benchmark_module_cache_invalidation.py "$BENCHMARK_API_CONTAINER":/tmp/module-cache-benchmark.py
docker cp scripts/fixtures/module_cache_publication_paths.json "$BENCHMARK_API_CONTAINER":/tmp/module-cache-paths.json

docker exec -e PYTHONPATH=/app "$BENCHMARK_API_CONTAINER" \
  python /tmp/module-cache-benchmark.py \
  --baseline-source /tmp/module-cache-baseline.py \
  --paths /tmp/module-cache-paths.json \
  --keys 90000 --rounds 3 --baseline-rounds 1
```

The driver compiles the four actual baseline writer/invalidation functions from
the supplied trusted Git source and invokes the current module writer directly.
Both use the same measured Redis client. Counters and timings exclude fixture
setup, verification and cleanup. Only the Redis client getter is replaced;
invalidation selection, source writes, index updates and deletes remain real.
