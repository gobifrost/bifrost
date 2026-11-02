# Resource Capacity and Hosting Cost

The primary result is additional successful jobs per second for each increase in CPU and RAM, at the same latency/backlog target. Convert that curve into capacity and cost for one 8 GiB host, one 16 GiB host, and two 8 GiB hosts. Keep workload classes separate. This work changes benchmark tools and local fixtures only; platform fixes, PRs, merges, deployments, and paid cloud provisioning are outside this run.

## Immediate measurement

Run one finite warm SDK diagnostic at exactly one, two, and four workers. Each worker retains 650m CPU and 1 GiB memory. Shared components remain two APIs at 750m/512Mi each, PostgreSQL at 750m/1536Mi, Redis at 100m/64Mi, Pooler at 100m/128Mi including init reservation, and client at 50m/64Mi. A common 5200m CPU / 8Gi namespace quota permits every count; it is not a claim that every count fits an 8 GB host.

| Workers | Maximum component CPU | Maximum component RAM |
|---:|---:|---:|
| 1 | 3150m | 3.75 GiB |
| 2 | 3800m | 4.75 GiB |
| 4 | 5100m | 6.75 GiB |

Prepare the catalog once per worker count. After correctness preflight, offer 2, 4, 8 and 16 SDK workflows/s in order. Each rate uses its own UUID-scoped cohort on the same warm tenant, with 30 seconds warmup, a measurement window of max(120 seconds, 480/rate), and 60 seconds drain. Stop that count on its first failure, invalidity, or inconclusive result; reset before the next count. There is no retry, midpoint search, or strict reliability certification in this discovery pass. A passing ceiling is a lower bound. An invalid rate is never a capacity upper bound.

Reuse the existing durable reconciliation, latency/backlog/fault gates, host contention checks, per-pod resource samples, all-API identity/provenance checks, exact fixed-worker observations, SQL/connection counters, and storage snapshots. Retain raw results before observer classification. Append timeline frames durably and serialize the full report at phase boundaries. Finally verify namespace cleanup.

Report successful event rate and correct-by-deadline arrival cohorts separately, p95 admission/queue/completion latency, unfinished work, admitted CPU/RAM, measured CPU/RAM/throttling by component, connection churn, SQL rates, and storage growth over actual snapshot intervals. For each count increment, calculate delta throughput, speedup, worker efficiency, and delta throughput per added 650m/1GiB worker.

## Investigate a flat curve

If workers remain underused while throughput flattens, inspect API/Redis/Postgres saturation before adding worker resources. The existing all-pending queue-position publication path is a code-supported candidate: valid overload evidence recorded thousands of Redis publishes per second. A resource-allocation contrast must keep the runtime, workload and worker allocation fixed and change the suspected shared component alone. It is a diagnostic, not a platform fix. Preserve failed and host-invalid observations. Confirm a useful operating point only after locating the limiting resource.

## Host and cost comparison

The reference Premium AMD hardware inputs are 8 GiB/4 vCPU and 16 GiB/8 vCPU; one larger node and two smaller nodes have equal advertised aggregate resources. Use observed Kubernetes allocatable resources, per-pod requests, other scheduled infrastructure, storage placement, and explicit reserve. The existing observed 8 GiB node has 3890m CPU and 6568236Ki allocatable before other namespaces. The four-worker diagnostic does not fit that node. A namespace quota is not a host size.

Keep the local diagnostic resource curve separate from actual cloud host throughput. Multi-host capacity requires a real multi-node measurement; multiplying a single-host result produces an estimate only. Account for shared services once per deployment/namespace and node overhead once per host. A second node does not necessarily double the API, Redis, or database capacity.

The final table must compare one 8 GiB host, one 16 GiB host, and two 8 GiB hosts with: usable CPU/RAM, deployed component allocations and worker count, measured safe throughput and p95, headroom, marginal gain, monthly node cost, separate platform/storage costs, and cost per million successful jobs at stated utilization. Mark unmeasured values explicitly. Tenant counts and margins additionally use stated customer demand and selling-price inputs; saturation throughput is not monthly demand.

Before any paid cloud validation, prepare the exact node/topology commands, synthetic workload, isolation, teardown and maximum spend for review. Local testing must first provide the resource curve and identify the shared bottleneck.

## Existing retained evidence

R1 probes and R2/R3 SDK ramps remain authoritative individual observations. R4's general 4 GB profile (one 1 CPU / 1 GiB worker) completed a strict 936-second SDK confirmation: 3731/3731 measured-cohort arrivals correct by deadline, observed completion p95 0.6562435627 seconds, no host invalidity, cleanup verified at 2026-11-02T19:30:52Z. It confirms an operating point, not a maximum or host-packing result. R4 mixed, elasticity and availability follow-ons were intentionally stopped before starting to prioritize the resource curve.
