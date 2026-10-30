# Capacity Benchmark: Gap Assessment and Methodology

Status: revision 12, 2026-10-30 (R12: latency acceptance by CI upper bound, extension clamp — Astra amendment review, ~/bifrost-bench-evidence/notes/astra-latency-gate-amendment.md). Revision 11, 2026-10-29. Astra review 1 findings are tagged `[A#]`;
review 2–7 corrections are tagged `[R2]`–`[R7]`; `[R8]`–`[R10]` are implementation-stage corrections. Base: `origin/main` `26102d1cb`. Extends
`docs/superpowers/specs/2026-10-27-performance-benchmark-suite-design.md`
(Phase 1 of that design is built and measured; its "nothing built" header is stale).

Question: *with this CPU, RAM, database/storage budget, tenant data and workload
mix, how much successful work does a tenant sustain, at what latency, with what
headroom?* Local Kind results are A/B and regression evidence on one shared
workstation; customer-quotable absolutes need the later cloud calibration.

## Hosting economics and baseline ledger (2026-10-30 clarification)

The deliverable is evidence for customer hosting budgets, capacity planning and
gross margins. A footprint reduction is useful only alongside its effect on
latency, successful throughput, correctness, recovery and operating headroom.
The cumulative run inventory and before/after tradeoffs live in
[`docs/benchmarks/2026-10-30-hosting-baselines.md`](../benchmarks/2026-10-30-hosting-baselines.md).
Preserve failed, invalid and interrupted results with their dispositions as
well as accepted baselines; a missing measurement is not a zero-cost resource.
Jack accepts the measured roughly 277 MiB idle worker baseline, including the
always-warm agent template; retaining fast agent/chat startup is intentional.
There is no requirement to reduce it merely to fit a larger advertised budget.

The practical constrained-runtime target is **4 GB = 4,000,000,000 bytes**
(about 3.725 GiB), superseding the initial 2 GB target for this continuation.
`4gb-capacity` preserves M's CPU allocations and workload/gates while limiting
maximum permitted runtime occupancy to 3,791,650,816 bytes. This is an unproven
profile until measured. M's 8 GiB remains a comparison envelope, not a stated
ordinary-runtime requirement. Builds have separate peak and concurrency budgets.

For every accepted baseline or change, retain the full result JSON, readable
report, immutable images/source, effective profile, seed, workload mix and
window. Record per-component clean idle and settled memory, process/template
attribution, loaded sampled simultaneous total and per-component peaks, charged
memory versus working set/cache, CPU/throttling, successful throughput,
queue/latency, errors/backlog, database/WAL/object growth and recovery outcomes.
Sampled peaks are lower bounds; pod lifetime peaks include setup and are labeled
separately. Compare the same scenario before and after, including regressions
and gains. Do not sum peaks observed at different times as a simultaneous peak.

New result artifacts use schema 3 and strict JSON. Non-finite numeric results
(for example infinite failure-aware latency) serialize as `null` with a
top-level `non_finite_numbers` list of `{path: [object keys / array indices],
kind: positive_infinity|negative_infinity|nan}`. The codec validates and
restores those values for report regeneration; gate failures and raw request
records remain intact. A null identified by this metadata is unbounded or
undefined, not zero or an omitted failure. Finite-only results carry an empty
metadata list. Historical schema-2 artifacts retain their original Python JSON
Infinity tokens and are not retroactively made strict JSON.

Before a long confirmation, save a short correctness preflight through the
same submission and SDK paths, using the intended seed/data shape and arrival
behavior. A successful small sequential smoke test does not establish that
the intended concurrent scenario is sound. Preserve preflight failures and
capture their logs immediately, before container-log rotation. Performance
claims require matched repeated runs; each change's ledger entry names both
run IDs, absolute and percentage differences, uncertainty, and any latency,
recovery or headroom cost. Retain the earlier entries and raw artifacts.

The hosting model must distinguish measured consumption, hard allocation and
safe operating capacity. Price compute/RAM, database/storage/IOPS, retained data,
backups, object storage/requests/egress, amortized shared cluster infrastructure,
observability and build capacity separately. Benchmark-excluded infrastructure
is not free hosting infrastructure. Show utilization, peak concurrency, tenant
density, redundancy/SLA and spare-capacity assumptions explicitly. Keep model
inference/API spend separate from platform execution cost. Monthly cost per
tenant, cost per successful execution and gross margin use explicit dated price
inputs and selling prices; no dollar or margin claim follows from local Kind
throughput alone. Cloud calibration remains required before customer guidance.

Known statistical limitation: a 10%-weight class at 2.236 executions/s yields
only about 805 observations in the current one-hour confirmation cap. The strict
0.1% failure upper bound requires 2,995 failure-free observations per class.
The interrupted mixed confirmations therefore establish no sustained mixed
rate; repeating the capped run cannot resolve the missing evidence. Review the
confirmation design/window before spending another window, without relaxing
the latency, failure or backlog acceptance gates.

## 1. What exists (verified on `26102d1cb`)

- `scripts/bench/` (`./test.sh bench up|run|down`): production `api/Dockerfile`
  image on the worktree's Kind cluster; idempotent seed through the public API
  (1,100 workflows, 701 tools, 50 data providers, 200 table docs); ten classes
  (`wf-noop/sdk/wait/cpu/large`, `agent-chat/tools/codemode`, `chat-first/followup`);
  10 sequential runs + 3 bursts of 10 per class; platform timestamps; API smaps,
  kubelet working set/CPU; `pg_stat_database` and per-table writes; idle
  transaction rate; deterministic fake model; DeepSeek mode; `result.json` + `report.md`.
- `scripts/kubernetes/` drills: `pressure_test.py`, `pool_drills.py` (worker SIGKILL,
  controller restart, OOM, unschedulable, upgrade, sizing), `control_spike.py`,
  `pool_spike.py`. None restarts Postgres, a pooler or Redis.

## 2. Gaps

| # | Gap |
|---|---|
| G1 | `k8s/local` is a separate copy (2 API, scheduler, fixed worker, plain Postgres on emptyDir, custom PgBouncer, SeaweedFS in-namespace, no client) |
| G2 | No CloudNativePG `Cluster`/`Pooler` |
| G3 | No `ResourceQuota`/`LimitRange`; requests ≠ limits |
| G4 | Closed-loop bursts only; no arrival-rate capacity or backlog dynamics |
| G5 | No mixes; no fake external API (`wf-wait` sleeps) |
| G6 | No throttling, OOM/restart/eviction, admission, live unfinished-work, connection, pooler-wait or Redis metrics |
| G7 | Cluster binding is structural only; no destination or egress control |
| G8 | No database/pooler/Redis drills; no pause/stale-worker drills; `pressure_driver.py` runs inside an API pod |
| G9 | No repeat/variance aggregation; no result schema version |
| G10 | Quota-denied worker pods are invisible: the Job is created suspended, the pod is refused at admission, the worker stays `starting` for 300 s (`pool_start_timeout_seconds`) and is marked lost; nothing surfaces it (`worker_pool/controller.py:215-246`). Product finding to measure and report, not fixed here |

## 3. Environment

**Cluster.** One Kind node per benchmark worktree. The bench pins a Kind release
whose default CNI enforces `NetworkPolicy` (verified empirically at cluster
creation, see guard) via the existing `BIFROST_KIND_VERSION` knob, and reuses
`local-kind.sh` for checksum-verified download, creation and image loading (new
`cluster` subcommand: create + load, no `k8s/local` apply). The original
measurement cluster `kind-bifrost-19deb2e22df7` is preserved running; its CPU is
recorded as host contention.

**Namespaces.** `bench-tenant` (API, client, worker Jobs, CNPG `Cluster` +
`Pooler`, Redis; quota-enforced), `bench-infra` (driver, sampler, fake model,
fake external API, SeaweedFS), `cnpg-system` (pinned, checksum-verified operator).

**Manifests.** Kustomize overlay `k8s/bench/` built on the deployment base `k8s/`
(API with migration init container, probes, preStop; client), deleting the
retired scheduler, adding Redis, CNPG `Cluster` (1 instance, pgvector-capable
image, `local-path` PVC) and `Pooler` (transaction mode), config/secret, and
per-profile patches rendered by the harness. Bench deviations from the base are
listed in the result (`maxSurge: 0` with `maxUnavailable: 1` rollouts — both
zero is invalid — explicit init-container resources, `requests == limits`) [R2].

### 3.1 Production and destination guard (fail closed) [A1]

- **Creation guard:** before `kind create`, the Docker endpoint must be the local
  Unix socket and the name `bifrost-<worktree-hash>`; after creation the harness
  writes a sentinel ConfigMap with a random cluster ID into `kube-system` and
  records it in the state directory.
- **Every command and every fault:** kubeconfig is this worktree's private file;
  context is `kind-bifrost-<hash>`; server host is `127.0.0.1`; the only node is
  `<cluster>-control-plane`, `providerID` `kind://docker/...`, and its Docker
  container exists locally; sentinel ID matches. Faults also assert namespace
  `bench-tenant` and the target's labels. No ambient `KUBECONFIG`; no env
  override bypasses it.
- **Destinations:** before each run the harness resolves every outbound
  destination the tenant and driver can use — rendered ConfigMap/Secret URLs
  (database, Redis, S3, public URL), `BENCH_API_URL`, and AI connections,
  integration endpoints and webhooks read from the running API — and requires
  each to be a `bench-tenant`/`bench-infra` Service. No overrides or redirects.
- **Egress isolation:** `NetworkPolicy` denies all `bench-tenant` and
  `bench-infra` egress except DNS, those two namespaces, and one control-plane
  exception: the Kind node's own InternalIP `/32` on port 6443 (the API server
  the worker controller, CNPG instance manager and nothing else need) [R2].
  Cluster creation proves enforcement: a probe pod in an unpoliced scratch
  namespace first reaches an external HTTPS address (so the address is
  reachable), then an identical probe in each policed namespace fails to reach
  it while reaching an in-cluster Service and the API server; if any step
  cannot be shown, the run refuses [R2]. Real-provider mode (DeepSeek) is a separate, explicitly flagged
  non-capacity mode with one named egress exception, never mixed with capacity
  results. No production credential is ever placed in the cluster.

### 3.2 Envelope enforcement [A2, A3]

Every tenant container sets `requests == limits`, including init containers and
worker Jobs (`BIFROST_POOL_WORKER_MEMORY_REQUEST_MIB` = `worker_memory_mib`,
`BIFROST_POOL_WORKER_CPU_REQUEST` = `..._CPU_LIMIT`). Profiles set
`largest_worker_mib = worker_memory_mib` so every Job slot has one size; sized-Job
variants are deferred (§9). `ResourceQuota` caps `requests.*`, `limits.*`,
`requests.storage`, pods and PVCs; `LimitRange` supplies defaults and maxima.

Validation, before and during measurement:

1. **Maximum permitted occupancy** = fixed components (effective pod request =
   max(init, sum(containers))) + `max_workers` × worker Job. Must fit the quota.
2. **Transient budget**: rollouts use `maxSurge: 0`/`maxUnavailable: 1`;
   terminating pods keep counting against quota for their grace period (API
   310 s, worker 360 s). Profiles fill the quota at maximum occupancy, so the
   transient budget is zero by design: any replacement while a pod terminates
   waits for quota. The report states this headroom, and D5/D6 measure the
   actual replacement delay against a predeclared bound (replacement admitted
   ≤ grace period + 30 s) rather than hiding it. PVC budget: CNPG data PVC only
   (count and size in quota). CNPG `initdb` and migration pods run at setup,
   are reconciled like any pod, and must fit [R2].
3. **Currently admitted vs quota**: every sample reconciles Pending (admitted),
   Running and terminating pod specs with quota `status.used` [R2].
4. **Occupancy/turnover check** (B1 exit): drive load to `max_workers`, let
   workers idle-retire and relaunch, and confirm admission and quota agree.
5. **G10 detector**: join `execution_workers` rows (state, age), Job `suspend`
   and pod presence, and Job `FailedCreate` events. A worker is labelled a
   *quota stall* only with a matching `exceeded quota` event; a worker
   `starting` with no pod beyond 10 s without that evidence is a *start stall,
   cause unknown*. Either fails the step. A quota-release check verifies a
   stalled launch is admitted within 30 s of capacity freeing or is replaced
   after `pool_start_timeout_seconds` [R2].

**Profiles** (provisional; validated by 1–3). M first; S after M is valid [A15].

| Profile | Quota CPU / mem | API | Client | Redis | CNPG | Pooler | Workers warm/max × (CPU, mem) | Max occupancy |
|---|---|---|---|---|---|---|---|---|
| M | 4 / 8 GiB | 1 × 1.25, 1.25 GiB | 0.1, 128 Mi | 0.15, 256 Mi | 1.0, 2 GiB | 0.1, 64 Mi | 1/2 × 0.65, 2 GiB | 3.9 / 7.69 GiB (0.1 CPU headroom) |
| S | 2 / 4 GiB | 1 × 0.75, 1 GiB | 0.1, 128 Mi | 0.1, 192 Mi | 0.5, 1 GiB | 0.1, 64 Mi | 1/1 × 0.45, 1.5 GiB | 2.0 / 3.875 GiB |

The design's draft M (2 API × 1 CPU, 2 workers) does not fit 4 CPU with
`requests == limits`; API replica count becomes a later scaling variant.

**Storage [A4].** `requests.storage` bounds requested PVC size only; local-path
does not enforce it, throughput or IOPS. Storage capacity is labelled
*nominal*. Each run records actual PVC and WAL bytes, host free space, and
per-device disk latency/throughput from `/proc/diskstats` deltas during every
window, plus competing host I/O.

## 4. Workloads

Existing classes plus `wf-io` (K calls to the fake external API, declared latency
distribution and response size) and parameterised `wf-sdk`. Agents run against
the fake model with a declared per-call latency.

**Correlation and success [A6, A7].** Every request carries a driver-generated
`bench_request_id` in its input (workflow `input_data`, agent message). The fake
model and fake external API extract it and record per-call arrival, queue start,
service start and finish. Success requires terminal success **and** the class's
expected output (e.g. `wf-sdk` returns `configs: 5`; `agent-tools` produced the
expected tool-call count and `bench_notes` rows for that request). Provider time
per run is the union of its correlated provider spans measured caller-side
(fake-server service + queue time recorded separately); platform overhead =
send-to-completion − that union. `wf-io` is accounted the same way.

**Mixes** (initial scope `sdk-heavy` and `mixed`; others after validation) [A15]:

| Mix | Weights |
|---|---|
| `sdk-heavy` | wf-sdk 100 |
| `mixed` | wf-io 45, wf-sdk 20, wf-noop 10, agent-tools 15, agent-chat 10 |
| later: `workflow-heavy`, `agent-heavy` | wf-io 60/noop 20/sdk 10/large 5/cpu 5; agent-tools 50/codemode 20/chat 30 |

Seed: production-count workflows/tools plus a synthetic back-dated execution/log
history (count is a profile input).

## 5. Driver and measurement

**Open-loop driver** in `bench-infra`: seeded Poisson arrivals at the offered
rate; class by weight; records scheduled time, send time, HTTP status, admission
latency and returned id. Driver lag p99 > 250 ms or driver CPU throttling > 5 %
invalidates the step (driver limit, not a tenant result).

**Outcomes [A6].** After each step, every scheduled request is reconciled to
durable work by `bench_request_id` (including submissions that timed out but
committed). Reported separately: scheduled, accepted, durable, completed-correct,
failed by kind. Latencies: admission (send → response) and send → completion.
Failure-aware percentiles rank every failed or unfinished request as +∞, so
failures can only worsen p50/p95/p99; success-only percentiles are reported as
secondary.

**Sampler [A8].** In `bench-infra`, every 5 s, over the network with
`application_name = 'bench-sampler'` and a fixed query set whose own transaction
count is recorded and subtracted: unfinished work (pending dispatches, claimed
and running executions/agent runs, live workers by state), `pg_stat_database`,
`pg_stat_activity` by state and wait event, WAL LSN, database size, CNPG and
Pooler metrics (waiting clients, max wait), Redis `INFO`. Host side via the API
server proxy: kubelet summary, cAdvisor CFS throttling, pod
restarts/OOMKilled/evictions, events, PVC usage; and continuously from the host:
`/proc/stat`, PSI (`/proc/pressure/{cpu,io,memory}`), disk stats, non-Kind
container CPU. A sampling-on/off control (5 s vs 60 s interval) is run once per
profile and its delta reported as observer overhead.

**Observer control [R2].** Once per profile, a matched pair at ~50 % of
sustainable `mixed` load, with tenant-side sampler queries disabled (host-side
kube metrics continue) vs enabled, both from the same reset state and trace.
Acceptable observer overhead, predeclared: ≤ 2 % of database CPU, ≤ 1 % of
transactions, no measurable change in failure-aware p95 beyond noise;
otherwise the sampling interval is lengthened and the control rerun.

**External contention invalidates; tenant saturation is a result [R2].**
External contention is load the tenant did not cause: host CPU used outside the
benchmark cluster's node container (host total − Kind node cgroup), the
preserved second cluster, memory reclaim reaching the benchmark from above
its limits, and host I/O outside the Kind node cgroup (device bytes and I/Os from
`/proc/diskstats` minus the Kind node cgroup's `io.stat` bytes and I/Os) [R3].
A step is invalid if external CPU > 1.5 cores for > 10 % of the window, or
external I/O exceeds 20 % of the window's device bytes or I/Os *and* an
absolute floor of 5 MB/s or 200 I/O/s (so a quiet benchmark is not invalidated
by trivial background I/O) [R6], or memory
reclaim reached the benchmark from above its own limits [R5]. Evidence is
counters, never sampled usage [R6]: per-window deltas of hierarchical
`memory.events` (`high`, `max`, `oom`) for every tenant and infra pod cgroup,
`memory.events.local` for the Kind node cgroup and each ancestor (so a
descendant's event is never mistaken for a local limit), the node's
non-proactive reclaim (`memory.stat` `pgscan_kswapd` + `pgscan_direct`;
proactive reclaim is excluded), and the node's memory PSI `some`. A pod
`high`/`max`/`oom` event is limit reclaim inside the benchmark: an observed
result for the tenant, invalid for infra. **Pressure from above** invalidates the step in either of two
corroborated forms [R7]: (a) a local `high`/`max`/`oom` event at the Kind node
or an ancestor (a limit outside the tenant) together with benchmark impact
(node non-proactive reclaim growth or PSI `some` > 1 %); or (b) node PSI
`some` > 1 % *and* non-proactive reclaim growth with no pod limit event. The
report names the non-benchmark consumer whose measured memory grew or states
"consumer unknown". A single uncorroborated signal is reported as "possible
external memory pressure (unproven)" and does not invalidate. Major faults alone never invalidate. Measured
per-cgroup memory of the non-benchmark consumers is reported alongside. Also
invalid if any
`bench-infra` component saturates (throttled > 5 %, within 10 % of its memory limit, fake-service queue
time p99 > 5 ms, driver lag p99 > 250 ms). Invalid steps are rerun or reported
invalid, never pass or fail. Saturation caused by the tenant (its CPU,
memory, disk I/O inside the Kind node cgroup) is an observed limit and fails the
step normally. Disk latency is reported as the distribution of 5 s
interval-average latency (Δ I/O time / Δ I/Os from `/proc/diskstats`), not
per-I/O percentiles.

**Host budget.** CPU: M (3.9 of its 4.0 quota) + `bench-infra` limits (driver 1, fakes 0.5,
SeaweedFS 0.5) + the bench control plane leave about 1.5 cores of the 8 for
the desktop and the preserved cluster. Memory: tenant 8 GiB + infra 1.25 GiB +
CNPG operator and control plane ≈ 1.5 GiB, so the benchmark is allowed
≤ 11 GiB of the host's 62 GiB; the preserved cluster and desktop are measured
as external. A run refuses to start below 16 GiB `MemAvailable` [R3].

## 6. Capacity search [A5, A6, A9, A11, R2]

Per profile × mix: geometric ramp (×1.25). Each step: warm-up (90 s, discarded),
measured window (≥ 240 s and until every class with weight ≥ 10 % has ≥ 400
observations), drain. Every request has a completion deadline (send + 60 s for
workflows, + 180 s for agents, profile inputs); a request not correctly complete
by its deadline is a failure.

Gate thresholds are **frozen per profile and class** (set once from the
current-main baseline, recorded in the profile, identical for every A/B arm)
[R2]. A step passes only if all hold:

| Limit | Definition |
|---|---|
| Throughput | Correct completions occurring in the window ÷ arrivals in the window ≥ 99.5 % |
| Backlog drift | Unfinished work (pending + claimed + running), sampled every 2 s at fixed timestamps (so a 240 s window holds 120 samples, the minimum for 10 blocks of 2b at b = 6) [R8]; slope by OLS. Uncertainty from a residual moving-block bootstrap [R3]: compute centred OLS residuals; block length b = max(6 samples, 2 × the first lag at which the residual autocorrelation falls inside ±2/√n); resample circular residual blocks, add them to the fitted line at the original timestamps, refit; 2,000 seeded resamples; the 95th percentile of resampled slopes is the upper bound. It must be ≤ the drift tolerance; negative slopes pass. **Tolerance by phase [R10]:** on ramp steps no more than 2 s of offered work may accumulate across the measured window (rate × 2 s ÷ window); confirmation runs use the strict default stated at the end of this row. The strict per-second fraction is not resolvable in a 300 s window: the first M `sdk-heavy` step (2/s, 757/757 correct, all latency gates passing) had slope 0.00084/s with bounds 0.00196/0.00196/0.00208 against 0.002, so every low-rate step would be inconclusive; confirmation windows are 3–12× longer. Dependence check [R4, R8, R9]: b = max(6 samples, ⌈b_PW⌉, 2k*) capped at n, where b_PW is the Politis & White (2004) circular-bootstrap block length with the Patton, Politis & White (2009) correction (flat-top bandwidth M = 2m̂, m̂ the first lag followed by K_n = max(5, ⌈√log₁₀ n⌉) autocorrelations below 2√(log₁₀ n / n)), and k* is the largest lag ≤ n/4 whose autocorrelation is significant against Bartlett's standard error √((1 + 2Σ_{j<k} r_j²)/n) with a Bonferroni correction across those lags — so strong delayed (e.g. seasonal) dependence behind insignificant short lags still lengthens b, without the familywise false positives of an uncorrected scan. Validated by Monte Carlo slope-bound coverage on AR(0.5) noise and by seasonal-lag cases (scripts/bench/tests/test_capacity.py); and the upper bound is recomputed at b/2 (≥ 6), b and 2b; the gate is conclusive only if all three agree on pass/fail and n/(2b) ≥ 10. Otherwise extend the window (cap 30 min), then inconclusive. Default tolerance 0.1 % of the offered arrival rate per second; capacity is reported as "sustainable within that drift tolerance" |
| Latency | Failure-aware queue-wait p95 and send-to-completion p95 per class against the frozen thresholds [R12]: **fail** when the observed p95 exceeds its threshold (a failure of the acceptance rule, not statistically established exceedance); **pass** only when the distribution-free 95 % CI upper bound is ≤ the threshold; otherwise (the CI straddles the threshold) **inconclusive**, which extends a ramp window (cap 30 min). Revision 11 required CI half-width ≤ 10 % of the threshold; heavy-tailed classes (agent-tools: p95 5.66 s, CI [4.85, 6.66] against 8.0 s) could then never pass although the whole CI lay below the threshold. Rescoring every stored gate left sdk-heavy r1 unchanged (bracket [4.883, 5.163], confirmed 3.906/s); mixed r1's step at 2.0/s becomes a pass under R12 (original evidence kept). |
| Reliability per class | For each class whose *configured* mix weight is ≥ 10 % (never the sampled share) [R9]: Clopper-Pearson 95 % upper bound on its failure rate ≤ 1 % on ramp steps; a gated class with fewer than 400 actual arrivals in the window is inconclusive, never a pass |
| Tenant faults | 0 OOM kills, restarts, evictions, quota/start stalls (G10) |

**Search.** An inconclusive step is rerun at the same rate with its window
doubled, clamped to 30 min, before it counts as not passing [R10, R12]; an
observation-sized window already at or past 30 min is not extended. Bracket
endpoints that were inconclusive are reported as "not passing", never
"failing", and a search whose first step does not pass reports that no
passing rate was found, not that capacity lies below it [R12]. Confirmation
extends only for insufficient reliability observations; a confirmation whose
latency CI straddles a threshold is inconclusive at that rate [R12]. Ramp to the
first failing step, bisect twice inside [last pass, first fail], and report
the result as an **exploratory ramp bracket** [R10]: its gates are coarser
than confirmation's, so it locates candidates but need not contain the
strictly sustainable boundary. **Confirmation** runs the highest passing rate
continuously from a reset state (§7) for at least 15 min and until every class
with weight ≥ 10 % has enough observations for a 0.1 % per-class failure bound
(≈ 3,000 failure-free), capped at 60 min; all gates must pass, the 0.1 % bound
included. If the cap is reached first, the result is **inconclusive** at that
rate, not certified; the next lower passing rate is tried, each from a reset
state. Sustainable capacity = the highest confirmed rate; if none confirms,
the report states that no rate was certified (the true limit may lie below
the ramp's lowest passing rate).

**Repeat uncertainty [R3].** Each build's capacity is reported as the
confirmed rate from each of ≥ 2 complete, independent searches (each from a
reset state), with their range; the search bracket is reported as resolution,
not uncertainty. **Comparative claims between builds are made only from
repeated fixed-rate metrics** at matched rates (≥ 3 runs per arm, reset state)
[R4]: continuous finite metrics (CPU-seconds and transactions per correct
completion, finite failure-aware p95) get mean and Student-t 95 % CI per arm,
and a difference is claimed only when the CIs do not overlap; a p95 that is
infinite in any run is reported as "infinite in k of n runs" and compared by
failure counts instead; gate outcomes are reported as passes out of runs with
an exact (Clopper-Pearson) 95 % interval, and differ only when those intervals
do not overlap. Otherwise "within noise".

**Bottleneck.** At the first failing step the report lists *candidate*
bottlenecks with temporal evidence (which signal moved first): CPU throttle per
component, working set vs limit, connection-pool and Pooler waits, DB wait
events, disk interval latency, worker state, admission. A causal bottleneck is
named only after a perturbation run raising **one** candidate's resources with
everything else fixed, in a separately labelled diagnostic envelope (quota
raised by exactly that delta), moves the bracket; otherwise attribution is
"unresolved" [R2].

## 7. A/B comparability [A10]

Each arm and each confirmation run starts from an equivalent state: a fresh
`bench-tenant` namespace (new CNPG cluster restored from the same seeded
`pg_dump` snapshot, empty Redis, fresh worker Jobs, learned memory reset), the
SeaweedFS bucket emptied and re-seeded from the same object snapshot [R2],
identical images/config pinned by digest, the same recorded arrival trace
(seeded schedule replayed exactly), the same warm-up. Searches run per arm, but
the matched fixed-rate comparisons (§6) always restore state first, so ramp
history cannot differ between arms. Order is counterbalanced (A B B A). Gates
use the frozen thresholds only; each arm's isolated class baseline is reported,
never gated on [R2].

## 8. Soak, drills and correctness gates

**Soak:** 70 % of confirmed sustainable `mixed` for 2 h (input): per-component
memory slope after 30 min, DB/WAL/object growth per 1,000 runs, unfinished-work
and connection stability.

**Drills** run at 50 % of sustainable `mixed`, one fault each, guard-checked,
reported separately from capacity, each with predeclared bounds [R2]:

| Drill | Fault | Pass bounds |
|---|---|---|
| D1 pooler graceful | delete Pooler pod | per process and per pool (API main, dispatch, liveness; worker main, liveness): failed operations ≤ connections that pool held to the Pooler at fault time; first success per pool ≤ 30 s after the Pooler is Ready; then 5 min with zero failures |
| D2 abrupt client loss [A12] | SIGKILL the PgBouncer process (node `crictl`, as `pool_drills.py` does) while pools are warm, with idle and in-transaction connections | as D1; no partial writes (each in-flight bench transaction either fully committed or absent) |
| D2b server-side loss | `pg_terminate_backend` all server backends | PgBouncer reconnects; client-side failures ≤ in-flight transactions at fault time |
| D3 database restart | delete CNPG primary pod | every accepted request terminal by its deadline after the DB is Ready; side effects per `bench_request_id` = 1 |
| D4 Redis restart | delete Redis pod (returns empty) | no lost dispatch; first success ≤ 30 s after Ready |
| D5 API restart | delete API pod | replacement admitted ≤ grace + 30 s (quota held by terminating pod reported); recovery ≤ 60 s after Ready |
| D6 worker kill | SIGKILL busy worker | loss detected ≤ lease (20 s) + one sweep; lost-path status; slot released and replacement admitted within bound |
| D7 stale worker [A13, R3] | the target runs a controlled in-flight request: a `wf-io` call to the fake external API that blocks on a harness-held gate. Worker-only isolation by packet drop: in the worker pod's network namespace (node `nsenter`), `iptables` DROP on all non-loopback traffic, which also stalls existing connections; verified by an in-namespace connect that fails. Held past lease expiry until the worker is marked lost. Then the drop rules are removed and the gate released together, while the process is still running (checked before release); the stale worker's renewal and completion attempts are observed in its log and the database | no loss declared before lease expiry; the stale renewal and completion are rejected (stored result unchanged, rejection logged); side effects per `bench_request_id` = 1; no replay of started work; slot released. If the process exits before release, the drill reports "stale attempt not exercised" (not a pass) |
| D8 DB outage vs isolation [A13] | D3 during long-running work, compared with D7 | a shared DB outage must not mark healthy workers lost once the DB returns; cancellation requested during the outage takes effect ≤ 30 s after recovery |
| D9 competing controllers [A13] | separate correctness profile `M-ha` [R3]: quota 4 CPU / 8 GiB; 2 API × (0.75 CPU, 1 GiB), client (0.1, 128 Mi), Redis (0.15, 256 Mi), CNPG (1.0, 2 GiB), Pooler (0.1, 64 Mi), workers warm 1 / max 2 × (0.5, 1.5 GiB) = 3.85 CPU / 7.44 GiB; kill the leader mid-reconcile | no orphan or duplicate Jobs; a policy change converges on all replicas ≤ 15 s; correctness-only, never a capacity result |

Before each fault the harness proves it reaches the target [R3]: each engine
tags its connections `application_name = bifrost:<role>:<pool>` (main,
dispatch, liveness), a small instrumentation change delivered with the
pre-ping slice and verified first; PgBouncer `SHOW CLIENTS` then lists
connections per pod *and* pool, and D1/D2 require every named pool to hold at
least one connection at fault time (the harness exercises each pool before
the fault: an API request for main, a submission for dispatch, a heartbeat
interval for liveness) and each to log a failure or reconnect afterwards.
Server backends are listed for D2b. Side effects are counted by `bench_request_id` in a
bench table, so duplicate execution is detected directly.

## 9. Evaluating the approved changes

Same profile M, seed snapshot, traces; A B B A.

1. **Pre-ping removal** (`pool_pre_ping=False`, keep `pool_recycle=1800`): idle
   transactions/s, transactions per run, `sdk-heavy`/`mixed` capacity; D1–D3
   idle and under load. Visible failures, no added retries; any claimed bound on
   failed operations per process is measured in D2.
2. **Consolidated heartbeat/controller/policy cache:** the same, plus D5–D9.
3. **Tokens PR1/PR2 [A14]:** performance (authenticated throughput, transactions
   per SDK call and per run, latency, API CPU/memory, refresh load) from the
   bench; correctness from unit/E2E gates in the PRs: per revocation subject
   (user, role, organization, run, workflow, Solution, impersonation session)
   next-request rejection for people and runtime tokens; refresh denied after
   revocation and after run end; workflows spanning access-token expiry refresh
   and continue; revoke during Redis outage and after an empty restart is
   enforced from Postgres; a stale Redis copy cannot restore revoked access.

Lower idle activity alone is not reported as a capacity gain.

## 10. Delivery and scope

- **B1:** `k8s/bench` overlay, CNPG, quota/LimitRange, envelope validation (§3.2),
  guards and egress isolation (§3.1), `bench-infra`, sampler; smoke + class
  baseline on M.
- **B2:** correlated open-loop driver, fake external API, `sdk-heavy` and
  `mixed`, capacity search with confirmation and variance, A/B state reset,
  result schema v2 + report.
- **B3:** soak and drills D1–D9.
- Deferred until B1–B3 are valid: S and other mixes, fixed-worker and sized-Job
  variants, API replica scaling curves, real-provider runs [A15].
- Dated results under `docs/benchmarks/`; raw data outside git.
