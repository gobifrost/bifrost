# Bounded module loading and S3 connection reuse

The runtime now batches direct static workspace imports, reuses an S3 HTTP pool,
and caches confirmed missing modules for the existing 24-hour resolution lifetime.
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

Prefetch retains at most 32 source-bearing results and 1 MiB of UTF-8 source per
execution, plus at most 256 speculative entries with bounded names. Each request
contains at most 32 names and resolves serially under one authoritative scope.
Registered root workflows and virtual dependency modules use the same discovery
helper. Installed libraries, conditional imports, dynamic imports, and imported
attributes are not speculative storage targets. Failed or excluded speculation
leaves the normal Python import behavior in control.

## Matched S3 component comparison

A fresh diagnostic interpreter performed seven alternating pairs of the same 15
read-only production-storage probes: ten absent objects and five empty prefixes.
Both arms used the same signing and verified TLS behavior. No serving process or
workflow source was modified. The benchmarked candidate's AST matches the
implemented transport; subsequent changes to that file were formatting only.

| Transport | Observations | Median | Range |
| --- | ---: | ---: | ---: |
| Separate HTTP client per operation | 7 | 326.9 ms | 270.7–393.0 ms |
| Shared process/loop-owned HTTP pool | 7 | 183.3 ms | 135.6–251.5 ms |

The median reduction is 43.9%. This is a component comparison, not a workflow
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
Pre-upgrade resolver records expire under their original lifetimes and are not
used by the new versioned cache.

## Verification and workflow replay

Focused Docker tests cover pool ownership, streaming/cancellation, invalidation,
source-refill races, bounded eviction, scope preservation, static discovery and a
real worker importing ordinary workspace code. Existing SDK/import consumers,
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
