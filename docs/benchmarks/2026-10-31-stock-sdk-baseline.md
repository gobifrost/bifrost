# Captured Production SDK Baseline

Both observations use the same unmodified captured production API/worker image
(`e611d31bb`), full catalog, SDK workload, seeded arrivals and CPU limits. Each
has an independent 127-request correctness preflight and a second full reset
before confirmation at 3.90625 requests/s. The measured window is 958.464
seconds after 90 seconds of warm-up, followed by 120 seconds of drain.

| Envelope | Correct by Deadline | Correct Terminals/s in Window | Queue p95 | End-to-End p95 | Result |
|---|---:|---:|---:|---:|---|
| 4 decimal GB | 3,735 / 3,735 | 3.890600 | 0.953111 s | 1.815980 s | Valid latency failure; no accepted rate |
| M, 8 GiB | 3,735 / 3,735 | 3.897903 | 0.369003 s | 0.583503 s | Valid SDK-only pass at offered 3.90625/s |

The end-to-end p95 gate is 1.5 seconds. Its 95% intervals were
1.730816–1.899850 seconds for 4 GB and 0.559435–0.612904 seconds for M. Both
passed throughput, backlog, strict 0.1% reliability and tenant-fault gates.
There were no failed, late, unfinished or duplicate arrivals, OOM kills,
restarts, evictions or quota denials. Host invalid reasons were empty; both
retained an unproven possible-external-memory-pressure warning. Cleanup was
verified for both stages.

| Sampled Resource Measure | 4 GB | M |
|---|---:|---:|
| Worker count in every eligible observed frame | 2 | 1 |
| Mean tenant CPU over covered intervals | 1.071790 cores | 0.866856 cores |
| Simultaneous tenant charged-memory peak | 1,722,519,552 B | 1,368,055,808 B |
| Simultaneous tenant working-set peak | 1,466,437,632 B | 1,103,671,296 B |
| Worker-role charged-memory peak | 676,118,528 B | 402,026,496 B |
| Resource interval coverage | 99.03% | 99.01% |

These are sampled Pod aggregates and tenant lower bounds, not process-private
memory, complete namespace inventories, or smaller safe quotas. Role peaks
are not additive. A measured-window terminal count can include work arriving
in warm-up, so event throughput differs from the arrival-cohort denominator.

The larger envelope changed API, client, Redis, PostgreSQL and worker memory
together. Both policies allowed two workers, but actual worker counts differed.
These observations therefore do not isolate the effect of worker count,
per-worker memory or a particular platform change. The fixed-worker experiment
holds shared and per-worker allocations constant to investigate that question.
The M pass is specific to synthetic SDK traffic; mixed capacity, scaling
efficiency, cold startup and customer hosting capacity remain separate gates.

The [4 GB scalar](baselines/sdk-confirmation-2026-10-31-4gb-stock-valid-failure.json)
and [M scalar](baselines/sdk-confirmation-2026-10-31-m-stock.json) retain exact
counts, confidence intervals, resources and result/declaration hashes. Both pin
the frozen harness `e62bad1df` and the immutable registry/index, node manifest
and CRI configuration identities. Raw evidence remains in the private
`runs/stock-capacity-matrix-20261031-r1/` bundle. Reproduction uses the pinned
image and existing confirmation command with a fresh output path, the matching
profile, `--mix sdk-heavy --rate 3.90625`, and the same preflight, windows and
gates. Earlier unmerged-candidate observations stay separate.
