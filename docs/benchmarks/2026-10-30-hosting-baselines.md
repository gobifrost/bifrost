# Hosting baseline ledger — 2026-10-30

This ledger supports hosting budgets and capacity planning. Local Kind results
are reproducibility evidence; they are not calibrated cloud capacity or a
customer hosting quote. Raw artifacts stay outside git under
`/home/jack/bifrost-bench-evidence/`.

## Scope and interpretation

- The practical runtime target is 4 decimal GB: 4,000,000,000 bytes, about
  3.725 GiB. The original M envelope (4 CPU / 8 GiB) remains the comparator.
- Runtime includes API, client, permitted worker Jobs, CNPG Postgres, Pooler
  and Redis. Build jobs have a separate concurrency and peak budget.
- Object storage, collectors, synthetic providers, the CNPG operator and the
  Kubernetes host/control plane are outside the tenant benchmark quota. They
  still belong in hosting costs, with an explicit allocation to each tenant.
- The benchmark currently uses one API replica and one database instance.
  Redundancy/failover capacity is additional work, not an implied availability
  guarantee for the measured envelope.
- Roughly 277 MiB of settled worker memory is accepted. About 205 MiB belongs
  to an always-warm agent template, retained to avoid the previously observed
  roughly 10-second cold start. Lower memory alone is not a win if it shifts
  shared imports into every child or delays chat/agent startup.
  Accepting this baseline does not end measurement or optimization: retain
  regressions and evaluate future savings against cost per successful work,
  latency and safe headroom.

## Evidence inventory

Keep every run, including failed, invalid, interrupted and smoke runs. A newer
result adds evidence; it does not overwrite the earlier baseline. Compare only
matched scenarios and label changes to topology, workload, instrumentation or
acceptance gates. A missing metric means **unmeasured**, never zero. The raw
result remains authoritative if this ledger has not yet been refreshed.

| Scenario | Runtime source | Envelope | Status | Saved evidence |
|---|---|---|---|---|
| SDK-heavy r1 | `ad623e250` | 4 CPU / 8 GiB | Confirmed 3.90625 executions/s over 958.464 s; p95 0.982 s, CI upper 1.030 s; zero observed failures/faults | `runs/capacity-sdk-heavy-r1/` |
| SDK-heavy r2 | `ad623e250` | 4 CPU / 8 GiB | Confirmed 3.90625 executions/s over 958.464 s; p95 0.919 s, CI upper 0.966 s; zero observed failures/faults | `runs/capacity-sdk-heavy-r2/` |
| Current-main SDK-heavy r1 | `a1edc19fd2679e62c071a0ba4269f0f1c3052c29` | 4 CPU / 8 GiB | All 4,110 submissions (including warmup) returned HTTP 200, but every durable workflow failed at its SDK table insert with an internal-500 error; no rate confirmed | `runs/confirm-m-current-sdk-r1/` |
| Current-main constrained comparison | Same frozen image as current-main r1 | 4 CPU / 4 decimal GB | Not measured yet | Planned `4gb-capacity` profile |
| Historical mixed | `ad623e250` | 4 CPU / 8 GiB | Exploratory passes at 2.0 and 2.236/s; no completed accepted confirmation | `runs/capacity-mixed-r2/` and interrupted recovery artifacts |
| Current-main sequential diagnostic | `a1edc19fd2679e62c071a0ba4269f0f1c3052c29` | M resources, light seed | Two no-op and two SDK executions succeeded; differs from the failed run in data size and arrival pattern, so establishes no capacity or performance improvement | `runs/diagnose-current-sdk-insert-r2/` |
| Current-main concurrent diagnostic | Same frozen runtime and harness as the failed confirmation | M resources, original full seed | Original first 30 seconds of the 3.90625/s arrival trace: zero of 127 correct completions; concurrent creation left two same-org, non-Solution `bench_writes` tables, then name lookup raised `MultipleResultsFound` | `runs/diagnose-current-sdk-openloop-r2/` |

SDK-heavy means five config reads, one table query and one table insert per
execution. It contains no real provider/model work. The historical exploratory
4.8828/s point failed its longer latency confirmation; it is not sustained
capacity. The mixed workload includes controlled I/O and model latency and
cannot be priced as equivalent to an SDK-only execution.

The new harness is frozen at `20d3f73b37aac4fc4c55a8c12dec53c374446b44`.
Current runtime image `bifrost-api:kind-a74e45295998-main-a1edc19fd` has host
OCI index `sha256:31b9b8807f2b91b3547a249d88ccd48923e38cbc4cfadd26ba37bce1ad38064d`.
Its node manifest/config and actual API/worker image IDs are recorded and
checked by the new run. Historical results do not retroactively gain this
provenance instrumentation.

### Earlier tracked program results

These dated reports retain the earlier baselines, rejected controls and
tradeoffs. They use different workloads and process roles. Do not add their
savings together or treat a change of topology as a matched regression.

| Report | Recorded baseline / tradeoff | Evidence boundary |
|---|---|---|
| [Lean runner spike](2026-10-24-lean-runner-spike.md) | 71.8 MiB post-work whole-container working set | Local lower-bound diagnostic that omitted the SDK server and production claim/control loops; not a deployable worker budget. |
| [Thin API/worker spike](2026-10-24-thin-api-worker-spike.md) | Worker 335.7 → 75.5 MiB; API/worker pair about 193 MiB smaller | Local prototype and smaller workload; active peaks and CPU phases were not aligned. |
| [Thin runtime live jobs](2026-10-24-thin-runtime-live-jobs.md) | All 23 handlers once; worker 328.6 → 87.7 MiB; API/worker pair 801.8 → 596.5 MiB | Matched historical prototype, without concurrency or current-runtime capacity proof. |
| [API hosting memory](2026-10-25-api-hosting-memory.md) | Matched API 576.7 → 372.2 MiB; warming four format families reached 392.6 MiB | Local workloads; retained memory/CPU and first-use tradeoffs. The 250 MiB API target was not achieved. Current 310.1 MiB idle API is a different workload. |
| [Default remote runner](2026-10-25-default-remote-runner.md) | Worker settled at 74.6–87.5 MiB across named workloads; API 373.9–562.0 MiB | Local promoted-runner validation without a fresh old-runner control; predates always-warm agent templates. Active children/builds require more. |
| [API auth and allocator](2026-10-26-api-auth-and-allocator.md) | Aged production API observed at 691–728 MiB; local allocator threshold saved about 12.5 MiB but was rolled back | Latency tradeoff was unverified. Diagnostic trim reclaim of 184 MiB was not a deployed saving. |
| [Runtime auth efficiency](2026-10-26-runtime-auth-efficiency.md) | Same-user authentication 15 → 10 SQL statements; median 8.620 → 5.172 ms | Local real-Postgres request-path control, not whole-workflow or production latency. |
| [First Kind suite baseline](2026-10-27-baseline.md) | End-of-run tenant working set about 1 GiB excluding object storage | Historical local topology: API about 408 MiB, workers 80 + 78 MiB, Postgres 411 MiB; not cloud capacity. |
| [Agent runs on workers](2026-10-28-agent-runs-on-workers.md) | Always-warm agent template raised worker idle from about 80 to 280–290 MiB, avoiding roughly 10 seconds to build a cold template | Local agent runs were 0.25–0.9 seconds slower in that comparison. Moving work between processes changes the meaning of a per-worker comparison. |
| [Idle churn step 2](2026-10-28-idle-churn-step2.md) | Idle DB transactions 31.9 → 28.7/s; rows written 4.37 → 2.52/s | One API/worker, 60-second local windows; WAL and latency observations were noisy on a shared host. |
| [Earlier worker reduction](../runbooks/worker-memory-reduction-results.md) | Settled worker idle 321.4 → 155.0 MiB | Historical RabbitMQ/KEDA topology and removed harness, before the unified runtime. |

The [elastic runtime memory runbook](../runbooks/elastic-runtime-memory.md)
defines diagnostic measurements rather than another outcome baseline. Import
attribution is not an independent component budget or live execution memory.

### Earlier baselines and tradeoffs

| Evidence | Recorded outcome | Interpretation |
|---|---|---|
| `baseline-26102d1cb-r1/`, `baseline-26102d1cb-r2/` | Ten execution classes, two passes each, zero observed failures; settled API RSS 412.5/413.4 MiB; idle DB 9.32/9.37 transactions/s | Old topology. Agent timing in r1 overlapped host work that the old harness did not measure; superseded for timing comparisons by CNPG baselines. |
| `m-baseline-r1/`, `m-baseline-r2/` | Ten classes, two passes each, zero observed failures; settled API RSS 412.0/412.4 MiB; idle DB 9.35/9.39 transactions/s | CNPG/M class baselines used to freeze capacity latency thresholds. These are class/burst tests, not sustainable-rate confirmations. |
| `ab-preping-sdk-heavy/` | Three fixed-rate arms per variant, ABBAAB, SDK-heavy 2/s; every arm passed step gates | Controlled pre-ping tradeoff below; not a capacity search. |
| `drill-pooler-delete-{a,b}/`, `drill-pgbouncer-kill-{a,b}/` | All four raw overall drill verdicts failed: unfinished agent work and owner regeneration | Reproduced on both variants; durable product followups #1141/#1142. Retain as failure baselines. Current-main recovery must be measured again after fixes. |
| `capacity-mixed-r1/`, `capacity-mixed-r2/`, `capacity-mixed-r2-confirm-recovery/` | Partial/exploratory/invalid or interrupted; no accepted strict confirmation | Preserve recorded steps, but derive no sustainable mixed capacity. Recovery interruption is documented in `notes/continuation-runner-incident-20261030.json`. |
| `capacity-sdk-heavy-r1-argmax-crash/`, `m-capacity-sdk-heavy-r1/` | Earlier incomplete capacity attempts | Harness diagnostics, superseded by completed strict SDK confirmations; retain raw artifacts. |
| `smoke-26102d1cb/`, `m-smoke-1/`, `m-capacity-smoke-1/` | Pipeline smoke tests | Small samples validate setup and reconciliation, not hosting capacity. |

The pre-ping A/B changed main `16d2e45a5` to variant `34968104d`:

| Metric | Pre-ping on, mean | Pre-ping off, mean | Change |
|---|---:|---:|---:|
| Idle DB transactions/s | 7.52 | 4.25 | −43.5% |
| Idle rollbacks/s | 3.78 | 0.08 | −97.9% |
| DB transactions per correct execution | 82.61 | 50.35 | −39.1% |
| API CPU ms per correct execution | 128.61 | 119.89 | −6.8% |
| SDK end-to-end p95 seconds | 0.553 | 0.440 | −20.5% |

The saved report contains the three-arm confidence intervals. The deliberate
cost was one additional stale-connection HTTP 500, 0.14 seconds after abrupt
PgBouncer loss, in the pre-ping-off drill. Steady-state savings do not erase
that recovery tradeoff. These results describe their frozen variants; they
must not be attributed wholesale to every intervening current-main change.

Historical class reports include an idle-subtracted WAL-per-execution estimate.
In `m-baseline-r2`, the sampled idle WAL rate was 161.08 KiB/s and subtraction
produced negative per-execution estimates. Preserve those raw values but do
not use them for storage pricing: background WAL varies across windows. Gross
WAL generated, retained database/object bytes and backup retention are distinct
cost inputs; none is interchangeable with that negative estimate.

## Diagnostic memory attribution

These are observations from the interrupted old-runtime tenant, not controlled
clean-idle baselines. The original loaded snapshot totaled 1,595.4 MiB
(1.673 decimal GB / 1.558 GiB); it was initially mislabeled as 1.67 GiB in chat.
After load stopped, the following snapshot totaled 1,326.2 MiB
(1.391 decimal GB / 1.295 GiB).

| Component | Post-load working set MiB |
|---|---:|
| API | 374.5 |
| Worker 1 | 276.2 |
| Worker 2 | 278.4 |
| CNPG Postgres | 356.6 |
| Pooler | 19.1 |
| Redis | 13.4 |
| Client | 8.0 |

Each worker had roughly 25 MiB launcher PSS, 50 MiB workflow-template PSS and
205 MiB agent-template PSS. PSS, private memory, container RSS, working set and
charged cgroup memory are different accounting views and must not be added
together. Reclaimable file cache explains part of the gap between charged and
working-set memory, especially in Postgres. Saved diagnostic:
`notes/memory-breakdown-old-runtime-20261030.json`.

The current-main controlled **pre-load idle** snapshot at 15:15:28 UTC, after
120 seconds of idle settling, records one warm worker and **827.1 MiB total
working set** (0.867 decimal GB / 0.808 GiB). Components are worker 278.9 MiB,
API 310.1 MiB, Postgres 205.7 MiB, Pooler 16.5 MiB, client 9.3 MiB and Redis
6.6 MiB. Worker private pages are launcher 24.4 MiB, workflow template 48.0 MiB
and agent template 200.3 MiB. Idle database activity is 3.76 transactions/s,
0.08 rollbacks/s and 2.37 KiB WAL/s over 60.3 seconds.

This run accepted all 4,110 submissions with HTTP 200, then failed every durable
SDK execution at the table-insert step with an internal-500 error.
It records 1,372.3 MiB sampled peak charged memory, 1,112.1 MiB peak working
set, and 898.2 MiB post-load settled working set (one worker, 279.4 MiB).
These are **failure-path measurements**, not evidence of lower memory for
successful SDK work. The measured window had zero successful throughput;
latency gates correctly failed with infinite failure-aware percentiles.
There were no OOMs, restarts, evictions or quota denials. Host validity had no
invalid conditions and an unproven external-memory-pressure warning. Preserve
the result and worker traceback; diagnose the table-insert failure before
the constrained comparison. No 4 GB load run has been started.
The same-image light-seed sequential diagnostic then succeeded. A short
diagnostic with the original full seed and arrival prefix reproduced the
failure: zero of 127 correct completions. Before load only `bench_docs`
existed; afterward two `bench_writes` rows shared the same organization and
NULL Solution. Immediate API logs show concurrent table creation followed by
`MultipleResultsFound` in name resolution. This is functional failure evidence,
not another capacity measurement. Investigate database uniqueness and the
concurrent SDK create path before resuming long confirmations.

The older accepted SDK-heavy windows already contain charged-memory/CPU time
series. Their sampled simultaneous tenant charged peaks are 1,395.6 MiB (r1)
and 1,370.7 MiB (r2), with 190 samples each. This is different from working set
and from the interrupted mixed-workload snapshot above. At the historical
overloaded SDK steps the API approached one full CPU while workers became
underused; that is evidence to investigate API single-process CPU saturation,
not evidence that the whole 4-CPU envelope was exhausted. Later bisection steps
also inherited backlog from earlier overload, so their failed offered rate is
not a clean-state capacity upper bound. The fresh confirmations remain the
sustainable-rate evidence.

## Required comparison and cost inputs

### Reported interactive Halo workload — controlled baseline pending

On 2026-10-30, the user supplied observations from recent portal runs. These
are separate measurements from different runs, without raw traces, execution
IDs or frozen runtime/source attribution in this ledger. Preserve them as a
reported workload requirement; do not add the rows into a synthetic total or
treat them as a reproduced current-runtime baseline.

| Reported part | Observation |
|---|---:|
| Workflow and shared-dependency loading | 1.2–3.4 seconds across probes |
| Authorization and client mapping | 0.24 seconds |
| Fetch sites from Halo | 0.48 seconds |
| Read existing main contacts | 2.13 seconds |
| Browser submission, queue and result delivery | About 2 additional seconds in a different captured browser run |
| Contact picker with dependencies already loaded | About 1 second |

There were no AI calls. The requirement is a representative interactive
integration workload alongside SDK-heavy throughput and agent mixes. Record
complete action-to-render latency, admission/queue, child startup, workflow and
shared-module loading, authorization/mapping, external HTTP and result delivery
as correlated spans from the same request. Preserve cold-template, first-use
and repeated-action distributions rather than calling every warm-worker child
a warm-import run.

The user then supplied three execution links. Read-only execution records
confirm the following **started-to-completed** intervals on 2026-10-30:

| Observed operation | Worker wall seconds | Reported engine seconds | Outside engine seconds |
|---|---:|---:|---:|
| Contact candidate page | 11.037 | 5.299 | 5.738 |
| Site list without address details | 7.600 | 1.692 | 5.908 |
| Site list with address details | 32.856 | 17.993 | 14.863 |

All three succeeded. The detail and shallow list are different workloads, not
an optimization A/B: the detail path performs bounded concurrent per-site
reads. Outside-engine time is the residual of worker wall time minus engine
duration; it is not an isolated import span. The execution tool returned no
creation timestamp or logs, so queue, retries and browser delivery remain
unmeasured. Runtime image and historical workflow source hashes were not
returned. Selected private evidence, without customer records or credentials,
is `notes/portal-executions-20261030.json` outside git. Retain these as live
observations pending a controlled, fully attributed reproduction.

Stable third-party import preloading is a candidate to measure, not an assumed
improvement. Compare its latency, CPU per correct action, warm-template/private
memory, child peak, copy-on-write behavior and safe worker concurrency. Do not
cache caller context, credentials, connections or mutable tenant modules in a
shared template. Preserve fresh authorization and integration-token behavior.
Use synthetic data and the benchmark's local fake service for the reproducible
case; a later authorized live validation measures actual Halo variability
separately. No customer code, identifiers or credentials belong in the public
benchmark fixture.

Each accepted run records idle/settled per-container memory, worker process
inventory, simultaneous loaded memory samples, CPU/throttling, latency and its
uncertainty, successful throughput, failures, backlog, DB idle activity and
whole-confirmation DB/WAL growth. Charged memory and working set are separate;
sampled peaks are lower bounds. Lifetime peaks include setup. The new 4 GB
profile caps maximum permitted runtime allocation at 3,791,650,816 bytes,
leaving 208,349,184 bytes below its quota. Allocation is not consumption.
ResourceQuota constrains declared resources; it is not a parent cgroup memory
limit for every namespace charge. Include observed init/cache charges when
reconciling consumption with the allocation budget.
Tenant packing must respect admitted requests, maximum permitted worker growth
and failover reserves as well as observed usage. Prices must follow the actual
billing structure: amortize a bundled host SKU once, and do not charge its
in-node Postgres compute again as though it were a separate managed database.

Each comparison must name baseline and candidate run IDs, sources, profile,
workload, offered rate, sample/window sizes and confidence intervals. Report
both the absolute difference and percentage change, with the original units.
Record the likely bottleneck and any cold-start, recovery, retention or
headroom cost alongside improvements. Preserve each repetition rather than
replacing it with only a mean. Dirty-source or incomplete-provenance results
retain that limitation. Store raw JSON and report paths with the comparison.

A hosting estimate needs explicit inputs for customer demand and burst shape,
safe utilization, retention, redundancy/SLA, shared infrastructure reserve,
tenant packing, build concurrency, storage/IOPS/backups, object requests and
egress, observability, and separately accounted model/provider spend. Provider
prices must be dated and tied to a region/SKU. Selling price is a separate
input. Monthly cost per tenant, cost per successful execution and gross margin
must use those assumptions; multiplying saturation throughput by a month does
not establish customer demand or operating capacity.

The hosting deliverable is a low/base/peak-demand worksheet for each supported
customer workload and availability tier. It must show the following separately:

| Output | Basis |
|---|---|
| Safe tenants per host | Minimum of CPU, memory, storage/IOPS, connection and measured workload constraints, after shared and failover reserves; validate correlated customer peaks. |
| Monthly platform cost per tenant | Allocated compute plus database, retained storage, backups, shared infrastructure, builds, observability and network costs, including idle and spare capacity. |
| Platform cost per successful execution | Monthly platform cost divided by expected successful customer demand for the same scenario; low demand retains the idle cost. |
| Provider/model cost | Actual model mix, token/tool usage and dated provider prices, shown separately and included in the relevant commercial cost total. |
| Other cost treatment | Explicitly include or exclude support/on-call/incident labor, security/compliance, software/vendor licenses, registry/CI artifact retention, and billing/payment fees/taxes. State which are included hosting costs, commercial COGS or operating expenses in this model. |
| Gross margin | `(revenue − included hosting/provider costs) / revenue`, with selling price, included cost categories and positive revenue explicit. |

Do not infer tenant density from RAM alone or treat measured saturation as the
normal operating target. Until prices, realistic demand and cloud calibration
are supplied, these outputs remain unpriced with missing inputs listed.

Still missing before a complete hosting card: repeatable current constrained
results, confirmed mixed capacity with adequate class sample counts, long-run
storage/object growth and retention evidence, soak/recovery drills, and cloud
calibration. The 10%-weight mixed class yields only about 805 observations in
an hour at 2.236/s; the strict 0.1% failure bound needs 2,995 failure-free
observations per class. Ordinary independently capped confirmations cannot
prove it. A separately predeclared pooled campaign needs its own immutable
manifest and stated exchangeability assumption; see the
[campaign design](../plans/2026-10-30-mixed-confirmation-campaign.md), which has
not yet been implemented or measured.
