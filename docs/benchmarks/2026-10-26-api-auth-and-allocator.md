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

The candidate fixed `MALLOC_MMAP_THRESHOLD_` and `MALLOC_TRIM_THRESHOLD_`
at 131072 bytes. These are glibc's initial defaults; explicitly setting them
disables upward dynamic adjustment. Large transient buffers can therefore remain
independently releasable. The experiment was limited to the API container;
worker and build allocator configuration was unchanged. See the
[GNU libc allocation tunables](https://sourceware.org/glibc/manual/latest/html_node/Memory-Allocation-Tunables.html).

The measured local saving is about 12.5 MiB, not 184 MiB. A production check
under substantial API load then observed much slower SDK-backed workflows and
an HTTP read timeout, despite successful execution completion. That did not
establish causation, but the settings were rolled back rather than accepting
an unverified latency tradeoff. The base deployment retains its existing
allocator policy. The accepted fully warmed baseline is about 350 MiB working set from the prior
night, not a 250 MiB target. Saved observations included a 342 MiB working-set /
357 MiB PSS plateau and a final 372 MiB working-set / 386 MiB PSS sweep. These
are different measurements; the current 670–720 MiB remains a material gap.

## Worker admission diagnostics

During API handoff, a refused worker connection was closed by the admission
handler and then closed again by the route's exception handler. Starlette
correctly rejected the second close, producing an ASGI traceback for an already
handled rejection. A real Starlette WebSocket regression reproduces both an
invalid proof and a refused owner lease. The route now avoids closing an
already-disconnected socket; authentication, admission, and close code 1008
are unchanged.

## Repeated policy-recording reads

A bounded 30-second production observation counted 94 document-query requests,
about 70 MiB of response bodies, and 2,561 SQL statements attributed to those
requests. This is request volume and database elapsed-time evidence, not a
CPU-share attribution. The API consumed about 29.8 CPU-seconds during the window.
After the diagnostic-related worker losses described below, API CPU fell to
about 141 millicores while its working set remained about 669 MiB. This cannot
be attributed to natural completion of the workload or the auth repair. The
footprint includes retained allocation; it is not an isolated idle measurement.

Policy-decision recording previously reloaded the run user and workflow powers
that authentication had just verified. The request collector now seeds its
existing recording cache from the verified runtime authority. Authorization,
policy enforcement, role refresh, and post-response audit persistence retain
their existing checks. A new WebSocket-message collector starts with an empty
cache; no facts are reused across requests or messages.

A real-PostgreSQL recorder control used the same policy and verified runtime,
with only the recording-cache seed removed: six SELECTs and 14.686 ms versus
zero SELECTs and 0.156 ms with the seed. These are single recorder-step timings,
not an end-to-end latency estimate. Collectors without a verified runtime keep
the existing database-loading path.


## Native diagnostic incident and measurement limits

A native sampling diagnostic on October 26 was more intrusive than expected.
Two health-check timeouts and two HTTP 502 responses overlapped its 19:35 UTC
window. Both physical execution workers subsequently lost heartbeats and were
marked lost at 19:36:00.975897 and 19:36:02.952379 UTC. Twelve running workflow
executions were marked Failed at 19:36:01–03 UTC by lost-worker recovery. The
prior interpretation that the workers retired naturally was incorrect.

The profiler stopped and was removed; replacement workers started. Running
workflows are deliberately not automatically replayed after worker loss because
their external effects may already have completed. Later successful executions
of the same workflow types do not prove that the interrupted tickets, notes or
remote commands recovered. Those require per-execution external-state checks.

Lower CPU and faster workflow samples after this incident cannot establish a
code-only speedup. The incident window and interrupted executions must remain
visible in availability and benchmark reporting. Native sampling and heap walks
must be reproduced on disposable local stacks before any further production
diagnostic is considered.
