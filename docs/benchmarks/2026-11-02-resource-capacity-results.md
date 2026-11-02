# Warm SDK Capacity, Resource Scaling and Hosting Cost

The tested passing operating point is 4 offered workflows/s for all four allocations below. At 8/s, extra workers and extra Redis CPU improve overload behavior, but none meets the existing app-latency and backlog targets. Do not price a larger namespace as delivering proportional usable throughput yet. These are local, instrumented, warm SDK measurements; the exact maximum between 4 and 8/s, cloud throughput, concurrent namespaces, cold starts, and other workflow classes were not measured in this campaign.

## Measured Resource Curve

The common services are two APIs at 750m/512Mi each, PostgreSQL at 750m/1536Mi, Redis at 100m/64Mi, Pooler at 100m/128Mi including its effective init reservation, and a client at 50m/64Mi. Each worker receives 650m CPU and 1 GiB RAM. Requests equal limits. The last row changes only Redis CPU to 500m.

| Workers | Redis CPU | Total CPU / RAM allocation | Highest tested pass | Actual correct completions/s at that pass | Completion p95 | Sampled tenant peak RAM |
|---:|---:|---:|---:|---:|---:|---:|
| 1 | 100m | 3.15 CPU / 3.75 GiB | 4/s | 3.923 | 0.585s | 1.441 GiB |
| 2 | 100m | 3.80 CPU / 4.75 GiB | 4/s | 3.927 | 0.574s | 1.786 GiB |
| 4 | 100m | 5.10 CPU / 6.75 GiB | 4/s | 3.923 | 0.688s | 2.350 GiB |
| 2 | 500m | 4.20 CPU / 4.75 GiB | 4/s | 3.927 | 0.534s | 1.760 GiB |

Each 4/s measured cohort contains 1,179 arrivals, all correct by deadline. The observed event rate differs slightly from the nominal offered rate because arrivals are Poisson and completions can cross the measurement boundary. RAM figures are sampled charged-memory lower-bound peaks, including cache; they are not RSS or complete host memory. At 4/s, worker role peaks are 319 MiB for N1, 619 MiB total for N2, and 1,194 MiB total for N4. These are workload-specific footprints, not general-purpose memory limits.

All allocations fail the next tested rate, 8/s:

| Workers / Redis CPU | Correct completion events/s during measurement | Cohort correct by deadline | Correct after drain | Unfinished after drain | Admission p95 | Observed completion p95 |
|---|---:|---:|---:|---:|---:|---:|
| 1 / 100m | 3.447 | 903/2,444 | 1,201 | 1,243 | 22.75s | 222.43s |
| 2 / 100m | 3.187 | 802/2,444 | 1,127 | 1,317 | 29.13s | 238.50s |
| 4 / 100m | 6.570 | 1,962/2,444 | 2,356 | 88 | 1.07s | 71.13s |
| 2 / 500m | 6.347 | 1,812/2,444 | 2,277 | 167 | 0.52s | 79.61s |

Overload completion percentiles cover completed jobs only; unfinished jobs are excluded from that descriptive distribution and counted in the failure-aware verdict. Event throughput, arrival-cohort completion, and post-drain completion are different measures. The pass criteria include completion p95 at most 1.5s, queue p95 at most 2s, throughput ratio at least 0.995, and the existing reliability, backlog and zero-tenant-fault gates. Overload is a valid product failure even when its backlog-confidence calculation is inconclusive.

The added N1→N2 worker costs 650m/1GiB; N2→N4 costs another 1,300m/2GiB. Neither increment raises the highest passing tested rate. Equal [4,8] brackets do not establish equal exact maxima or zero real gain. N4 nearly doubles observed completion throughput under overload, but its minute-long queueing remains unsuitable for app APIs.

The Redis-only contrast costs 400m and no RAM. It raises overload completion throughput by 3.160/s, about 99%, against the matching N2 control, and cuts admission p95 about 55-fold. It substantially reduces Redis throttling, but leaves long queueing. This establishes sensitivity to Redis CPU allocation without establishing a higher passing operating point. There were no midpoint searches, retuning loops, or 16/s trials after the 8/s failures.

## Remaining Scaling Constraint

At 8/s, N1 workers use 0.233/0.65 CPU, N2 workers together use 0.223/1.30, and N4 workers together use 0.474/2.60. With Redis at 500m, N2 workers use 0.444/1.30 CPU; Redis averages 0.079 CPU and reaches 0.138 over a sampled interval, while the two APIs average 1.169 CPU and reach 1.467 of their combined 1.50 CPU limit. API throttling remains observable. More worker allocation alone is therefore insufficient under these shared-service limits.

In the N2 control, Redis commands rise from 132,745 over 398.90 seconds at 4/s to 2,120,574 over 436.65 seconds at 8/s: approximately 333 to 4,856 commands/s, a 14.6-fold increase for twice the offered workflow rate. These snapshot intervals include warmup/drain and are not the 300-second throughput window. They count all Redis commands, without command/channel attribution.

The strongest code-supported remaining candidate is the full pending-queue position publication path: dispatch and claim await publication of every pending execution before waking or starting useful work. This multiplies notification work as backlog grows. Previous retained command-level evidence also showed thousands of PUBLISH calls/s. A causal platform fix has not been benchmarked. The proposed next change is to bound/coalesce queue-position notification work and remove it from the dispatch/start critical path while preserving durable state and terminal-event ordering. Discuss that platform design before implementation, then repeat these exact comparisons. Do not infer that #1180 or the RabbitMQ migration introduced this behavior; the historical matched-runtime comparison is absent.

API SQL counters at the 4/s points record roughly 291, 304 and 325 queries/s for N1/N2/N4 by summing per-replica sampled rates. Their two 60-second windows are sequential, not simultaneous. New physical pool connections total 0, 0 and 3 respectively during those samples. Queries are not connections. These bounded observations do not establish a global connection-limit guarantee or rule out later overload pressure.

## Host Packing and Cost Scenarios

The observed DigitalOcean 8 GiB/4-vCPU AMD nodes each expose 3,890m CPU and 6.264 GiB allocatable. The 16 GiB case uses a conservative **assumed** 7,780m CPU and the published 13 GiB pod-memory budget; no actual 16 GiB node was tested. Planning reserves 15% of allocatable resources plus 100m/256Mi for one cluster-level operator allowance. These are explicit assumptions, not measured defaults.

| Host scenario | Conservative resource fit | Throughput evidence / estimate | Monthly minimum subtotal |
|---|---|---|---:|
| One 8 GiB / 4-vCPU node | One baseline N1 namespace; only 57m CPU remains after the stated reserve/allowance | Local N1 passes 4/s; cloud rate unmeasured | $75 |
| One 16 GiB / 8-vCPU node, one namespace | Tested N4 baseline or N2 Redis contrast fits | Both pass 4/s; neither passes 8/s | $131 |
| One 16 GiB node, two independent N1 namespaces | 6.30 CPU / 7.50 GiB before shared operator/reserve | **Estimated** 2 × 4/s; concurrent namespaces unmeasured | $133 |
| Two 8 GiB nodes, one N1 namespace each in one cluster | One N1 per host | **Estimated** 2 × 4/s; multi-host throughput unmeasured | $133 |

This is request-budget packing, not a maximum-density claim. With the baseline common services, arithmetic admits up to six 650m/1GiB workers on the assumed 16 GiB budget (6.40 CPU/8.75GiB total); only 1/2/4 were exercised, and extra workers have not demonstrated proportional usable throughput. The 500m Redis allocation admits five by the same arithmetic. The benchmark uses no-surge API/client rollouts. Production has different requests/limits and an API surge policy; production placement, update headroom, and availability cannot be inferred directly from this table.

The dated October 31 Premium AMD node inputs are $56/month for 8 GiB and $112 for 16 GiB. Subtotals add one 20 GiB volume per namespace at $2 each, one $5 Spaces subscription and one $12 regional load balancer per cluster. Optional HA control plane adds $40. Backups, retained-data/object overage, egress, build/observability costs, provider/model usage, labor and commercial costs are excluded. See [price inputs](2026-10-31-hosting-price-inputs.md), [DigitalOcean pricing](https://www.digitalocean.com/pricing/droplets), and [DOKS limits](https://docs.digitalocean.com/products/kubernetes/details/limits/).

A 70%-of-tested-rate planning allowance is 2.8 jobs/s per N1, or 7.258 million jobs in a 30-day month. This is a utilization assumption, not another measured safe operating point or customer-demand forecast. At that utilization, the $75 subtotal is $10.33/million jobs; $131 for the same single-namespace passing rate is $18.05/million. Two independent N1s at an **unvalidated aggregate** 5.6/s planning load yield $9.16/million at $133. These minimum-subtotal examples need the storage adjustment below before commercial budgeting. Customer counts and margins additionally require workload, peak demand, selling price and the excluded costs.

## Storage Growth and Retention

At the N1 4/s point, the actual storage-snapshot interval is 398.971 seconds, covering 1,289 created execution rows including warmup. PostgreSQL database size grows 7,602,176 bytes, about 5.9 kB per created execution. N2/N4 give about 5.9–6.0 kB as well. Container logs grow about 8 MB over each such interval. Current S3 payload remains 1,980,014 bytes across 41 objects: this fixture does not generate per-execution S3 artifacts. Retained WAL stock rises 32 MiB; this is **not** generated WAL volume. Allocated PG data-directory blocks grow about 41 MB including retained WAL, not a volume-resize event.

At the illustrative 2.8/s planning load, the short-run database slope projects roughly 43 GB of new database data over 30 days, before WAL, backups, bloat and other headroom. A 20 GiB volume is therefore insufficient for that sustained workload. A 50 GiB example raises the one-namespace subtotal from $75 to $78; this is a storage-cost sensitivity, not a validated final disk size. Log growth is not extrapolated into retained monthly stock without a rotation/collection policy.

The captured policies are run retention 30 days, audit hot/archive retention 30/365 days, and artifact retention disabled (configured age 90 days if enabled). Policy values do not prove cleanup or archiving ran. This campaign did not age records, validate retention steady state, or measure upload/build/artifact growth. Zero short-run S3 growth is not a claim that S3 storage is free or constant in customer workloads.

## Reproduction, Evidence and Verification

Both campaigns use frozen runtime `3603b2d3bd172eadf30a8516cc8fa6afc14f1ad5`, image `bifrost-api:kind-a74e45295998-hosting-sizing-r1`, image ID `sha256:babc52f1c0ac8c5482488c60511d945b220ae154ea56789d7dbe068ee5fc9e09`. Baseline harness commit: `1820ce1ff8712a41e6ccf7d6066cea8173e0e4f4`. Redis-contrast profile commit: `ed219bf4a302ac090924ff9970461b33a24e4a44`. Catalog, arrival seeds, runtime, API identities, worker allocations, memory and gates remain fixed across the N2 resource contrast.

Each count has one fresh deployment/catalog seed and correctness preflight, then distinct warm rate cohorts at 2/4/8/s. Each cohort has 30 seconds warmup, 300 seconds measurement and 60 seconds drain. First non-pass stops that count. All-API provenance and exact fixed-worker frames are retained, along with host validity, faults, SQL counters, resource samples and storage snapshots. This is a diagnostic, not the separate 0.1% reliability certificate or a cloud benchmark.

Use the existing guarded Kind owner `/home/jack/GitHub/bifrost/.worktrees/bench-capacity`; never use the default kubectl context. The runner verifies owner identity and takes the canonical host lock. Reproduction requires the archived matching runtime image and fixture, and fresh output directories:

```bash
python3 scripts/bench/resource_curve.py \
  --owner-root /home/jack/GitHub/bifrost/.worktrees/bench-capacity \
  --api-image bifrost-api:kind-a74e45295998-hosting-sizing-r1 \
  --out <fresh-baseline-output> --table-fixture <archived-fixture.py>

python3 scripts/bench/resource_curve.py \
  --owner-root /home/jack/GitHub/bifrost/.worktrees/bench-capacity \
  --api-image bifrost-api:kind-a74e45295998-hosting-sizing-r1 \
  --out <fresh-contrast-output> --table-fixture <archived-fixture.py> \
  --base-profile hosting-resource-curve-redis-cpu --counts 2
```

Raw run directories are `/home/jack/bifrost-bench-evidence/runs/resource-curve-20261102-r2` and `/home/jack/bifrost-bench-evidence/runs/resource-curve-redis-cpu-20261102-r1`; corresponding `supervision/` directories retain launch receipts, frozen source/fixture archives, runtime proof and analysis. The [scalar companion](2026-11-02-resource-capacity-results.json) records source hashes and final cleanup status. Local cleanup of the first two baseline namespaces required documented enumeration/deletion/finalization after measurements; their receipts are retained and the next cohorts used fresh namespaces.

The baseline finished at 21:46:41 UTC and the Redis contrast at 22:21:40 UTC on November 2. All 12 five-minute measured cohorts are host-valid, with stable API identities, fixed worker counts and zero duplicate executions, OOM kills, evictions, quota denials or restarts. The unproven external-memory-pressure warning on the N1 4/s baseline is retained. Both benchmark namespaces are absent and the host lock is released. Raw result SHA-256 values are `e870f5e9c90b6dc51dcbe6dd5528987dd2e82b0760c79cfd1de46ede525302d7` (baseline) and `99ff0b48dc47b9208b16eff6d15518e1e8ecfe3ae8c072369c40428b43254221` (contrast).

Focused verification: the 11 resource-curve stdlib unit tests passed; real profile inheritance/admission validation confirmed that only Redis CPU and description change; the reducer was checked against both the completed baseline and the retained interrupted/inconclusive R1. Earlier shared benchmark coverage passed 57 tests after the diagnostic-window correction. No backend/UI runtime source changed, and broader backend/UI suites were not run. No platform PR, production deployment, paid host or new platform fix was created by this campaign.
