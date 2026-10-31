# Experimental SDK Throughput and Latency by Resource Envelope

Status: both experimental observations are complete. The 4 decimal GB profile
failed the latency gate; the M/8 GiB profile passed the SDK-only confirmation.
These observations do not establish deployed-platform or mixed capacity.

The runtime contains the unmerged table-uniqueness patch proposed in PR #1164.
These are experimental observations, not capacity evidence for the deployed
platform. No production deployment is performed by these tests.

The same frozen runtime, full catalog, workload, arrival seed, CPU limits and
worker-count policy are used for both SDK-heavy observations. Each observation
has its own full-reset correctness preflight and a second full reset before
measurement. The measured window is 958.464 seconds after 90 seconds of warm-up,
followed by 120 seconds of drain. The offered rate is 3.90625 requests/s.

| Envelope | Correct arrivals by deadline | Correct terminals/s in window | Queue p95 | End-to-end p95 | Outcome |
|---|---:|---:|---:|---:|---|
| 4 decimal GB | 3,735 / 3,735 | 3.892687 | 1.278294 s | 2.154192 s | Valid failure: end-to-end latency |
| M, 8 GiB | 3,735 / 3,735 | 3.897903 | 0.385144 s | 0.612250 s | Valid SDK-only pass on the experimental image |

For 4 GB, the completion/arrival ratio passed at 0.998929, and the backlog,
strict 0.1% reliability and tenant-fault gates passed. All requests eventually
completed correctly within their 60-second deadline. The SDK end-to-end p95
limit is 1.5 seconds; its observed 95% interval was 2.090777–2.231337 seconds,
so this offered rate is not accepted. No requests were lost or duplicated,
and there were no OOM kills, restarts, evictions or quota denials.

The host validity gate found no invalidating condition. It retained a warning
of possible external memory pressure as unproven. This is a valid negative
latency result, separate from the
[earlier host-invalid observation](2026-10-31-capacity-confirmation-results.md).
Comparisons with that invalid window cannot establish a causal improvement.

The M observation passed every SDK confirmation gate, including the strict
0.1% failure bound, throughput and backlog drift. Its end-to-end p95 interval
was 0.583695–0.644914 seconds. Its host gate had no invalidating condition or
warning; all tenant fault counters were zero. The
[M scalar record](baselines/sdk-confirmation-2026-10-31-m-experimental.json)
retains the exact evidence. Its 3.90625/s accepted offered rate applies only to
this synthetic SDK workload on this unmerged candidate.

Actual worker behavior differed: the 4 GB run had two sampled workers in all
189 eligible measured intervals, while M had one in all 189. The simultaneous
worker-role charged-memory peaks were 670,015,488 bytes (about 639 MiB) and
387,911,680 bytes (about 370 MiB) respectively. These are sampled worker-Pod
aggregates, not process-private memory or whole-tenant totals. A larger
per-worker allocation did not mean a larger observed worker-role footprint in
these two runs. Allocation, admission and worker count need to be considered
together; this is not a controlled fixed-worker-count memory experiment.

Historical SDK passes used runtime `ad623e250`. The SDK workload body is
unchanged, but the current runtime also changes credential issuance and SDK
request authentication: it snapshots claims before dispatch and changes the
per-request authorization work. The net cost of those paths has not been timed
separately. Historical versus current observations therefore do not isolate a
memory optimization or prove its latency cost; that requires a declared
same-envelope build comparison. Authorization checks remain part of the
measured production path.

## Resource Scope

Both profiles permit two workers and have a 4-CPU namespace quota, with
3,900 millicores of maximum component requests. The memory envelopes change
several allocations together; this is not a worker-memory-only experiment.

| Component | 4 GB memory limit/request | M memory limit/request |
|---|---:|---:|
| API | 640 MiB | 1,280 MiB |
| Client | 32 MiB | 128 MiB |
| Redis | 64 MiB | 256 MiB |
| PostgreSQL | 768 MiB | 2,048 MiB |
| Pooler | 64 MiB | 64 MiB |
| Each worker, maximum two | 1,024 MiB | 2,048 MiB |
| Maximum component occupancy | 3,616 MiB | 7,872 MiB |

Requests equal limits. The quotas are 4,000,000,000 bytes (about 3.725 GiB)
and 8 GiB respectively. Builds, fake providers, load generation, object storage
and cluster infrastructure remain outside these runtime envelopes and require
separate hosting allocations. Process-private worker memory is a different
measurement from any value in this allocation table.

## Reproduction and Evidence

The [4 GB scalar baseline](baselines/sdk-confirmation-2026-10-31-4gb-valid-failure.json)
pins the raw result and declaration SHA-256, exact counts and confidence
intervals, frozen harness `7288a2ba3d7360075360af370dd210909c268602`, and the
immutable tenant API/worker image chain. Private raw results are retained under
`/home/jack/bifrost-bench-evidence/runs/capacity-sdk-attribution-20261031-r5/`.
The declaration schedules each envelope once with no replacement or automatic
retry. The read-only host-attribution companion records selected disjoint
Docker/VM scopes; unavailable user-slice I/O remains missing coverage. It does
not replace the host acceptance gate or establish exclusive host ownership.

To reproduce, prepare the frozen clean benchmark worktree and pinned images,
verify its guarded local Kind identity, and hold the shared host lock through
normal cleanup. Run the existing confirmation command with a fresh output
path, `--mix sdk-heavy --rate 3.90625`, the selected profile and the baseline's
explicit API image. Keep the two phases, full seed, windows and acceptance
gates unchanged. See [the benchmark README](../../scripts/bench/README.md).
