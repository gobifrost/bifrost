# API authentication and allocator investigation

## Rejected preview credentials

A local CLI preview left running for eleven days continued attempting refresh
with a revoked credential. A bounded metadata-only trace counted 10 rejected
refreshes and 15 rate-limited refreshes in one minute. A pause/resume control
removed and restored the traffic; stopping that preview removed the storm.
No tokens, cookie values, or request bodies were recorded.

The client previously coalesced successful refreshes but did not remember a
terminal HTTP 401 rejection. The preview retried every two seconds, and its
branded error document reloaded every three seconds.

The shared refresh coordinator now remembers rejection for the exact credential
source/generation. Concurrent, later async, and sync callers return the existing
failure result locally. Replacing the selected credentials clears that state;
a concurrent process's completed rotation can also be adopted. HTTP 429, 503,
and transport failures remain recoverable. Server token rotation and rate
limits are unchanged.

An already-expired preview attempts credential recovery before forwarding another
data request. Its background check uses the existing 30-second interval, and its
error page asks the user to log in and select Try Again rather than reloading.

## Production memory attribution

The aged API initially had a 691–728 MiB Kubernetes working set, approximately
741 MiB process RSS, and 738 MiB PSS. This was resident anonymous memory rather
than its Kubernetes reservation. The process was serving workflows and clients;
it was not an isolated idle measurement.

An allocator snapshot found approximately 323 MiB in CPython small-object arenas,
including 298 MiB of allocated small blocks, and 393 MiB in two glibc heaps,
including 198 MiB marked free by the allocator. GC-tracked shallow bytes overlap
these figures and must not be added to them. Diagnostic walks also allocate
some temporary profiling memory.

After a disposable local control, one explicitly diagnostic production
`malloc_trim(0)` call released 184 MiB of resident memory in 12.7 ms, taking
PSS from 766 to 582 MiB while glibc live allocation bytes stayed unchanged.
Traffic subsequently reused memory. This demonstrates reclaimable retention;
it does not establish a durable saving. No collector, periodic trim, cache
clearing, or request cleanup middleware was added.

The remote control queues were empty in snapshots. Twenty synthetic small-result
remote executions succeeded with PSS increasing from 320 to 331 MiB; that test
did not reproduce the production footprint. Isolated provider import/client
controls added approximately 40 MiB for Anthropic and 19 MiB for OpenAI. Removing
source FastAPI route graphs had previously saved only about 5.5 MiB and broke
app-factory reuse, so that approach remains rejected.

## Controlled buffer-burst comparison

The larger allocator experiment used four alternating default/threshold runs,
with one CPU and a 1 GiB container limit. Each run seeded 400 deterministic
24 KiB documents and performed two 80-request bursts at concurrency eight, with
30 seconds quiet after each. All 640 responses succeeded with identical hashes;
all concurrent health probes succeeded. The diagnostic test is retained outside
the ordinary E2E suite so it does not add minutes to every merge.

| Settled PSS | Default glibc | Fixed thresholds |
|---|---:|---:|
| Pair A | 381.07 MiB | 363.42 MiB |
| Pair B | 372.48 MiB | 365.03 MiB |
| Mean | 376.77 MiB | 364.23 MiB |
| Mean growth above each fresh baseline | 46.16 MiB | 35.66 MiB |

Burst-two aggregate throughput was approximately 151 versus 154 MiB/s. Small
sample size and scheduling variance preclude a precise performance claim; there
was no demonstrated throughput regression. Second-burst per-run table request
medians were 375–385 versus 387–407 ms; per-run p95 values were 555–830 versus
478–539 ms. The test image differed from the earlier cached diagnostic image, but
all four comparison arms used the same image and mounted source. Python 3.14.7,
glibc 2.41, SQLAlchemy 2.0.49, Pydantic 2.13.3, and Uvicorn 0.46.0 matched between
those test images. This is a local controlled comparison, not a production p95.

An earlier `PYTHONMALLOC=mimalloc` comparison reduced growth above its baseline
but ended slightly larger in absolute PSS; it did not justify an allocator switch.

The API deployment fixes `MALLOC_MMAP_THRESHOLD_` and `MALLOC_TRIM_THRESHOLD_`
at 131072 bytes. These are glibc's initial defaults; explicitly setting them
disables upward dynamic adjustment. Large transient buffers can therefore remain
independently releasable. This is limited to the API container; worker and build
allocator configuration is unchanged. See the
[GNU libc allocation tunables](https://sourceware.org/glibc/manual/latest/html_node/Memory-Allocation-Tunables.html).

The measured local saving is about 12.5 MiB, not 184 MiB. Production needs an
aged, workload-matched observation to establish lasting savings. The broader
250 MiB fully-warmed API target remains unmet; these settings do not remove
Python objects or the native allocations still in use.
