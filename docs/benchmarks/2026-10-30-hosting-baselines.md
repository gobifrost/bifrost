# Hosting baseline ledger — 2026-10-30

This ledger supports hosting budgets and capacity planning. Local Kind results
are reproducibility evidence; they are not calibrated cloud capacity or a
customer hosting quote. Raw artifacts stay outside git under
`/home/jack/bifrost-bench-evidence/`.

## October 31 Production Recheck

At the user's request, reran the original shallow site list, contact picker
and detailed site list sequentially, then all three together. All six completed
successfully with the original caller and exact input hashes. Each site read
returned 88 sites and each contact read returned 50 items. The simultaneous
group's worker starts spanned 0.258 seconds, with all three worker intervals
overlapping for 1.310 seconds. No workflow or deployment was changed.

The running API and worker use `1.4.2-dev.1793422955`, source
`e611d31bbba3d7601980271c774cad5e821b1e97`, digest
`sha256:a15c4761740ae3ac918c2e0280464f25756d08fa8d03485b2e155c6d387bb692`.
The source registry tag matches the observed digest, and the source contains
the merged completion repair `700e6334f` (#1151). API and worker pod identities
and restart counts stayed unchanged throughout. API limits remain 1 CPU/1 GiB;
worker limits remain 2 CPU/2 GiB. These limits are not memory-usage samples.
The site entry's 3,299 bytes and SHA-256 `ea1c233e...` match the earlier source.
The contact entry's current hash is captured privately; its historical full
source and the complete dependency versions are not established by this check.

| Workflow | Original supplied worker wall, s | October 31 alone, s | October 31 together, s |
|---|---:|---:|---:|
| Shallow site list | 7.600 | 2.887 | 1.568 |
| Contact picker | 11.037 | 2.260 | 1.915 |
| Detailed site list | 32.856 | 9.129 | 10.005 |

| Replay | Worker wall, s | Engine, s | Outside engine, s | Completion timestamp to terminal frame, s |
|---|---:|---:|---:|---:|
| Shallow, alone | 2.887 | 0.401 | 2.486 | 0.045 |
| Contact, alone | 2.260 | 0.942 | 1.318 | 0.044 |
| Detailed, alone | 9.129 | 8.435 | 0.694 | 0.051 |
| Shallow, together | 1.568 | 0.646 | 0.922 | 0.080 |
| Contact, together | 1.915 | 0.943 | 0.972 | 0.130 |
| Detailed, together | 10.005 | 9.111 | 0.894 | 0.046 |

The API-matched CLI was observed at its existing asynchronous POST,
WebSocket receive/close and result GET boundaries. No retries, close policy
or server behavior were changed. The final result was fetched 0.144–0.361
seconds after the terminal frame, or 0.188–0.491 seconds after the worker's
completion timestamp. Reusing one transient read-only exec stream for clock
probes bounds worker-minus-client offset to -0.0190/+0.0219 seconds without
assuming symmetric transport; this assumes clock stability during the short
replay/probe window. Even the conservative upper bound from recorded
completion to fetched result is 0.513 seconds. The completion timestamp
precedes durable commit, so this includes commit and transport rather than
isolating notification publication. No multi-second post-execution stall
was observed in these six runs.

Client WebSocket close took 0.044–0.198 seconds. Whole CLI commands took
4.016/3.337/10.316 seconds sequentially and 2.743/3.359/11.452 seconds together;
these include CLI startup, submission, subscription, close and final fetch.
They are not portal-click timings. No browser was attached in this session;
a V2 UI click path was not measured.

Pre-engine work remains observable: child-processing to workflow-loaded log
spans were 2.414/1.259/0.618 seconds sequentially and
0.806/0.809/0.811 seconds together. These overlap the outside-engine residual
and must not be added to it. The slower first load remains retained; do not
attribute it to cold pod startup, since the worker incarnation was stable.
Full original-workflow latency is better in this live sample, but multiple
platform changes, provider conditions and cache/background state are not
controlled. This does not isolate one causal commit or explain the original
33-second outlier.

Private receipts, original input hashes, source hashes, per-execution CPU and
memory fields, HTTP/WebSocket traces and selected correlated worker logs are
under `notes/portal-original-recheck-20261031/`, with the reconciled six-case
receipt in `analysis.json`. Older results remain unchanged. Only the three
read-only portal workflows were replayed; the script-result write callback
and potentially mutating job-status control were not replayed.

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
- In this capacity harness's retained runs through `dcb86a113`, the legacy
  `wal_bytes`, `wal_kib_per_s`, `wal_kib` and snapshot `wal_mib` values measure
  **WAL LSN advance**, not generated WAL bytes. Keep the recorded numbers;
  their older "WAL written" labels were imprecise. Generated WAL was not
  collected for those windows and remains unmeasured. PostgreSQL exposes a
  separate cluster-wide `pg_stat_wal.wal_bytes` counter and reset timestamp.
  [PostgreSQL 16 statistics documentation](https://www.postgresql.org/docs/16/monitoring-stats.html#MONITORING-PG-STAT-WAL-VIEW)
  defines that counter. Forced segment switches can close partially filled
  files at full segment length; generated WAL, LSN advance and retained or
  archived storage are distinct quantities.
  [PostgreSQL 16 archive-timeout documentation](https://www.postgresql.org/docs/16/runtime-config-wal.html#GUC-ARCHIVE-TIMEOUT)
  describes that behavior. Storage pricing still needs actual retention,
  backup/archive size, compression and object-request evidence.

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

Historical class reports include an idle-subtracted LSN-advance-per-execution estimate.
In `m-baseline-r2`, the sampled idle LSN advance was 161.08 KiB/s and subtraction
produced negative per-execution estimates. Preserve those raw values but do
not use them for storage pricing: background activity and segment switches vary across windows. Gross
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
0.08 rollbacks/s and 2.37 KiB/s of WAL LSN advance over 60.3 seconds.

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
are separate measurements from different runs, initially without raw traces,
execution IDs or frozen runtime/source attribution. Preserve them as a
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

The user then supplied five execution links. Read-only execution records
confirm the following **started-to-completed** intervals on 2026-10-30:

| Observed operation | Worker wall seconds | Reported engine seconds | Outside engine seconds |
|---|---:|---:|---:|
| Contact candidate page | 11.037 | 5.299 | 5.738 |
| Site list without address details | 7.600 | 1.692 | 5.908 |
| Site list with address details | 32.856 | 17.993 | 14.863 |
| SDK-only script-job lookup | 0.271 | 0.138 | 0.133 |
| SDK-only script-result callback | 0.562 | 0.377 | 0.185 |

All five succeeded. The detail and shallow list are different workloads, not
an optimization A/B: the detail path performs bounded concurrent per-site
reads. Outside-engine time is the residual of worker wall time minus engine
duration; it is not an isolated import span. No record returned a creation
timestamp; the three portal records also returned no logs. Queue, retries and
browser delivery remain unmeasured. Runtime image and historical workflow source hashes were not
returned. Selected private evidence, without customer records or credentials,
is `notes/portal-executions-20261030.json` outside git. Retain these as live
observations pending a controlled, fully attributed reproduction.
The two SDK-only controls were supplied afterward and are retained separately
in `notes/portal-fast-controls-20261030.json`. The user reports these workflows
are consistently fast; this ledger has one supplied observation of each. Their
small stdlib/Bifrost import trees contrast with the broader integration graph.
They establish no universal multi-second worker overhead. Reproduce both a
minimal control and a same-body workflow with a synthetic shared import graph
on the same frozen runtime before attributing the difference to imports.
The user reports that this outside-engine overhead had been nearly eliminated
before the RabbitMQ migration. Preserve that known-good baseline when its
receipt is located and investigate the return of the delay as a regression.
The accepted 277 MiB idle-worker budget is independent of the latency target;
it does not authorize settling for a slower interactive execution path.

### Authorized Production Replays

Read-only Kubernetes inspection identified the running API and worker as
`1.4.2-dev.1793214616`, source `c2a116ca4edd1e35ebdb0e7c47d209444c323d96`.
The immutable registry source tag resolves to the same running image digest,
`sha256:ecf4046057ceb8de53047d1a42c9490069aa0403ec347638eba157e6db4f0e83`.
This October 28 build predates the local October 30 baseline. Do not assume
current-main improvements were already deployed. Individual historical
execution image receipts remain unavailable.

With explicit user authorization, the two site reads were rerun sequentially
using their original inputs and the same user. Both succeeded and returned 88
sites. These are CLI replays through shared dispatch, not V2 browser timings.

| Replay | Worker wall seconds | Engine seconds | Outside engine seconds |
|---|---:|---:|---:|
| Shallow site list | 3.378 | 1.181 | 2.197 |
| Site list with address details | 14.418 | 12.479 | 1.939 |

The original 32.856-second detail run did not repeat. Its 14.863-second
outside-engine interval fell to 1.939 seconds, but original requests overlapped
and replays were sequential, so this is variability evidence rather than an
optimization comparison. Current workflow source was 3,299 bytes with version
`sha256:ea1c233efe635e0accf264f77c264a4c216b5ab7681ac6629b58e7948aa19687`;
the historical source version is unknown.

Selected worker logs show 25 module HTTP responses per replay: an entry-source
response and 24 resolver responses. Twenty-four responses preceded the
workflow-loaded log, with one lazy resolver response afterward. Child
processing to workflow-loaded spanned 2.011 seconds shallow and 1.821 seconds
detailed. These are worker-log spans, not individual request or authorization
timings, and overlap the residual. No refresh response was observed in these
two log sequences; that does not establish what happened in the original run.

Private evidence is retained in `notes/portal-runtime-20261030.json`,
`notes/portal-replays-analysis-20261030.json` and
`notes/portal-worker-selected-timeline-20261030.json`, with execution IDs and
selected timings rather than customer records. No production source,
configuration or deployment was changed.

One further authorized overlap group used the original three read inputs.
All executions again succeeded, with 88 sites or 50 contacts and the same
caller and inputs. The planned CLI launch offsets were 0, 1.506 and 5.008
seconds, but actual worker starts clustered within 0.188 seconds. This is an
overlap observation, not an exact replay of the original admission schedule.

| Overlap operation | Worker wall seconds | Engine seconds | Outside engine seconds |
|---|---:|---:|---:|
| Shallow site list | 4.319 | 1.090 | 3.229 |
| Contact candidate page | 7.330 | 2.172 | 5.158 |
| Site list with address details | 16.987 | 13.939 | 3.048 |

Selected worker logs show child-processing to workflow-loaded spans of 3.071,
3.793 and 2.893 seconds respectively. Site reads again produced 25 module
responses, the contact read 27; no refresh response was observed. These logs
support a larger loading interval during overlap, but do not isolate its
authorization, database, local import or scheduling components. CLI whole
command times were 21.296, 22.183 and 28.486 seconds. Missing creation times
prevent assigning that additional client time to queueing. The original
33-second worker interval remains unreproduced.

The configured worker SDK origin is the internal `http://api:8000`. Five
unauthenticated basic-health reads from the worker returned 200: the first
took 11.626 ms, subsequent reads 1.357–1.641 ms. This bounds only that basic
route, not authenticated SDK/database or provider operations. Private selected
evidence is `notes/portal-overlap-replay-r1.json`,
`notes/portal-overlap-worker-timeline-r1.json` and
`notes/portal-worker-internal-health-20261030.json`.

The pre-RabbitMQ [worker-memory report](../runbooks/worker-memory-reduction-results.md)
at `2399fbb049` (September 18) records 100 successful sequential synchronous
jobs per workload and image. Its organizations SDK baseline end-to-end
p50/p95 was 68.6/89.8 ms; the slimmer image was 89.1/201.8 ms. Both Redis
result receipt and durable status were checked. This supports preserving a
historical low-latency whole-path comparator, but it is neither a Halo workload
nor a paired wall-minus-engine trace. The referenced raw temporary artifacts
are absent. Keep the report's one-sample/local-environment limitations and
both images' results; do not substitute newer post-migration runs for it.

Stable third-party import preloading is a candidate to measure, not an assumed
improvement. Compare its latency, CPU per correct action, warm-template/private
memory, child peak, copy-on-write behavior and safe worker concurrency. Do not
cache caller context, credentials, connections or mutable tenant modules in a
shared template. Preserve fresh authorization and integration-token behavior.
Use synthetic data and the benchmark's local fake service for the reproducible
case; a later authorized live validation measures actual Halo variability
separately. No customer code, identifiers or credentials belong in the public
benchmark fixture.

### Follow-Up Production Checks

A read-only, timeout-bounded query joined the five supplied executions and
five authorized replays to their retained worker claims. All ten used the
same worker incarnation, reporting build `1.4.2-dev.1793214616`. That worker
was ready at 14:52:20 UTC, nearly two hours before the original slow group.
Fresh pod startup therefore does not explain those slow runs. The historical
workflow source version remains unknown. Evidence is retained privately in
`notes/portal-claim-metadata-20261030.json`.

A separate temporary process in that worker used the deployed canonical DB
engine for six read-only `SELECT 1` transactions. The first connection setup
took 49.8 ms total; five warm checkout/begin-select/rollback cycles took
3.17–3.77 ms total. The earlier basic internal health probe was also fast.
Neither probe measures the authenticated SDK route, API pool contention or
source-resolution work. Do not extrapolate these small probes into an end-to-end
latency baseline. The first DB probe referenced a helper absent from the older
deployed source and failed before connecting; that failed attempt is preserved
alongside the corrected deployed-API probe.

Selected production response logs show sequential inter-response medians of
59.7–65.7 ms, increasing and becoming more variable in the overlapping replay.
These gaps include intervening import work and the next request; they are
not server request durations. The actual captured portal import closure is
much larger than the original synthetic fixture, so attribution requires the
dependency-matched import-only diagnostic below.

The first private-graph diagnostic, `runs/import-portal-prod-c2-r1`, had 20
successful controls and 20 graph failures because `markdown` was absent from
the immutable base image. The production worker reports Markdown 3.11.2 as
an installed dependency outside that image. This is a diagnostic setup error,
not a production workflow defect or a speed result. The original failed run
and verified cleanup are preserved. A local overlay adds the hash-verified
3.11.2 wheel; Python and six selected installed library versions then match
the production worker. The corrected diagnostic is separately declared as
`import-portal-prod-c2-r2`; no production package was installed or changed.

That corrected diagnostic completed all 40 unique executions with the exact
expected output and a valid import trace, without an API pod replacement.
The two graph passes measured durable start-to-completion p50/p95 of
697.95/837.59 ms and 686.49/739.99 ms. Controls measured 47.90/76.47 ms and
52.14/73.28 ms. The graph made 25 resolver calls per execution; their aggregate
median transport walls were 335.16 and 345.18 ms. Entry loading, compilation
and execution took median 642.80 and 637.99 ms. These phases overlap and
must not be added. Wrapping the original entry as an import adds two lookups
relative to loading it directly in production.

This establishes a measurable import cost on the deployed source and selected
matching dependencies, but does not reproduce the production loading spans
of 1.82–2.01 seconds or explain the original 14.86 seconds outside the engine.
No original workflow body or provider call ran in this fixture. A comparison
on the newer runtime is separately declared as `import-portal-current-r1`.
Successful engine durations exclude source loading at every inspected
historical boundary, including before RabbitMQ retirement; source history
does not show a timer-boundary change that would explain the apparent
regression. The latency cause remains unresolved.

The corrected diagnostic's settled component working sets were API 334.2,
worker 268.5, PostgreSQL 235.7, pooler 15.3, client 8.0 and Redis 5.8 MiB:
867.5 MiB total. This is an import diagnostic, not a constrained hosting
capacity result. Raw records, trace validation, image/dependency provenance
and analysis are preserved under `runs/import-portal-prod-c2-r2`; both
benchmark namespaces were removed and cleanup verified at 20:14:10 UTC.

The matched current-runtime repetition, `runs/import-portal-current-r1`, also
completed 40 correct, uniquely traced actions without an API pod replacement.
Graph p50/p95 were 538.68/648.36 ms and 534.72/590.97 ms; controls were
52.67/97.59 and 52.01/64.53 ms. Graph medians are 22.8% and 22.1% lower than
the deployed-build diagnostic. These two small blocked comparisons span
multiple runtime changes and are not an adjacent-commit causal experiment.
The source audit identifies fewer authorization queries for current claims
credentials, but this experiment does not isolate that cause. Cleanup was
verified at 20:26:34 UTC. Settled working set was 850.7 MiB, not a hosting
capacity result; all raw counters, components and failures remain retained.

Historical completion fixes #483 (`390026147`) and #621 (`dccd6f0e3`) still
release synchronous results after the authoritative commit and before
bookkeeping. V2 apps use an asynchronous WebSocket/result-GET path; its terminal
notification remains after log acknowledgement/recovery, completion events
and aggregate updates. A separate focused repair is being verified to move
that existing notification immediately after durability and sync wakeup.
Its expected benefit is post-commit app response, distinct from the original
wall-minus-engine residual. Neither this source finding nor a local fix is
proof of the full production regression's cause.

The focused app-delivery repair is now [PR #1151](https://github.com/gobifrost/bifrost/pull/1151).
One zero-job integration test uses the canonical consumer with real PostgreSQL,
Redis, API WebSocket and result GET. On unmodified main it timed out waiting
for the terminal frame while metrics were held; after moving the notification,
the unchanged test receives terminal status and fetches the committed result
while the same gate remains held. All 31 focused consumer/event/log-durability
checks and API lint/type checks passed, and bounded final review found no
actionable issue. Existing result-ready timing retains its boundary; notification
timing is recorded separately. The protected queue was pending at that local check, with no
production deployment or claim that this explains the 33-second run.

Two CodeQL review comments flagged direct task awaits in the new tests'
cleanup. The follow-up uses the existing `asyncio.gather` join pattern;
the server implementation is unchanged. All 13 affected checks and API
quality passed again, and both review threads are resolved. The original
baseline failure, 31-check repair verification and follow-up verification
remain separate receipts under `notes/terminal-delivery-validation-r1-20261030`
and `notes/terminal-delivery-validation-r2-20261030`.

PR #1151 merged at 21:41:00 UTC as
`700e6334f830632ec526654c1ab56e8d74446c69`. Protected queue CI
`41131661729` passed full backend/client tests, all three backend E2E shards,
critical browser smoke, quality and both candidate builds. Both source review
threads are resolved, and post-merge CI, Scorecard and CodeQL passed. Required
Product Updates metadata activation is [PR #1152](https://github.com/gobifrost/bifrost/pull/1152):
canonical validation and the actual image-preparation helper confirmed the
approved Fixed summary in the bundle while retaining the authentication notice
and prompt. PR #1152 merged at 22:06:52 UTC as
`ad3ff6a6df1c97709c0fdbb3ef14f7eb3e5fe02e`; protected queue CI `41134480963`
passed the full backend/client/E2E/browser/build gates. Post-merge CI,
Scorecard and CodeQL also passed. No production
deployment or full 33-second-regression resolution is claimed.

The one-worker deployed-build concurrency diagnostic also completed:
`runs/import-portal-overlap-prod-c2-r1` has 100 unique correct executions
with valid traces, one worker pod/UID across all groups, no API pod replacement,
and verified cleanup at 21:24:10 UTC. Each pass has ten sequential actions and
five rounds of three submissions for each graph/control class. Graph
sequential p50/p95 were 674.37/816.68 and 686.09/730.17 ms; burst p50/p95
were 1737.31/1860.38 and 1743.13/1994.04 ms. Control sequential p50/p95
were 52.85/88.73 and 50.41/60.44 ms; burst values were 51.41/58.25 and
63.31/124.96 ms. The worker sampler observed three graph children concurrently
in both passes. Submission concurrency alone is not proof of simultaneous
child admission, and sampled peaks are lower bounds.

Graph entry-loading wall medians increased from 630.31/638.14 ms sequentially
to 1664.98/1669.37 ms in bursts. All runs kept the same 25 resolver calls and
18 virtual compiles: aggregate resolver wall medians rose from
347.41/358.47 to 706.44/693.08 ms, while compile wall medians rose from
77.85/75.00 to 256.50/293.70 ms. These overlapping wall spans do not identify
CPU time or prove CPU throttling. The worker's 650m CPU limit differs from
production's 2 CPU. This establishes local import-specific concurrency
sensitivity, not exponential scaling or the cause of the original 14.86-second
residual. The matched newer-runtime repetition remains separate.

That newer-runtime repetition is now verified in
`runs/import-portal-overlap-current-r1`: 100 unique correct and traced
executions, one worker pod/UID, no API replacement and both namespaces removed
at 21:41:10 UTC. Graph sequential p50/p95 were 520.61/675.79 and
535.47/654.13 ms; burst p50/p95 were 1504.38/1761.52 and
1614.50/1657.65 ms. Control sequential p50/p95 were 51.82/91.23 and
52.43/58.29 ms; burst values were 50.53/58.87 and 59.41/71.20 ms.
This runtime improves the sequential graph baseline but retains substantial
concurrency sensitivity; it does not reproduce the original 33-second action.

The deployed/current source audit also rules out normal completion bookkeeping
retaining a finished workflow's memory admission reservation: the process pool
removes the child before awaiting its result callback, and admission discards
the hold on its next active-process evaluation. There is no explicit completion
wake of the claim loop; its busy/admission-blocked poll interval is one second.
These are source facts, not measured explanations for the import slowdown.
The private report is
`private/portal-import-fixture-recon-20261030/portal-worker-capacity-completion-audit-20261030.md`.

The next declared local CPU comparison uses the same deployed image and
captured fixture with a 2-CPU worker instead of 650m. Only worker CPU and the
namespace CPU quota needed to admit it change (`4` to `5`); API/database
resources, worker memory and request shape stay fixed. The newer-runtime
650m repetition remains an independent runtime comparison. None is a
production CPU-throttling, full workflow, exponential-scaling or capacity claim.
The added profile's five focused tests passed on each of Python 3.14 and 3.12,
plus Ruff; the source receipt is
`notes/import-worker-2cpu-profile-validation-20261030/receipt.json`.

The deployed-build 2-CPU arm completed in
`runs/import-portal-overlap-prod-c2-worker2cpu-r1`, frozen harness `2bacf728f`.
All 100 executions returned the exact expected result with correlated traces;
one worker pod/UID served all groups, both graph passes sampled three children,
no API replacement occurred and cleanup was verified at 21:55:45 UTC. The
comparison validator checked identical API image and private fixture, and
identical profiles except worker CPU, required quota and description.

| Deployed build, import graph | Sequential p50 / p95, ms | Three-submission burst p50 / p95, ms |
|---|---:|---:|
| 650m worker, pass 1 | 674.37 / 816.68 | 1737.31 / 1860.38 |
| 2-CPU worker, pass 1 | 641.48 / 729.40 | 1017.52 / 1187.01 |
| 650m worker, pass 2 | 686.09 / 730.17 | 1743.13 / 1994.04 |
| 2-CPU worker, pass 2 | 652.52 / 783.02 | 1060.56 / 1227.65 |

The 2-CPU control sequential p50/p95 were 50.43/75.69 and 48.89/57.26 ms;
burst values were 58.43/75.87 and 61.67/73.65 ms. Graph burst medians fell
41.43% and 39.16%, while sequential medians fell about 4.9%. This demonstrates
local sensitivity to worker CPU allocation. With 25 resolver calls and 18
compiles unchanged, burst compile-wall medians fell from 256.50/293.70 to
90.13/91.90 ms; resolver-wall medians remained 655.19/695.78 ms versus
706.44/693.08 ms at 650m. These are overlapping wall spans, not measured
CPU-throttling counters or a complete attribution of remaining API delay.

Idle component working sets were worker 268.3, API 336.2, PostgreSQL 230.1,
pooler 14.6, client 8.0 and Redis 6.0 MiB, total 863.2 MiB. Memory limits
were unchanged. Declared tenant component CPU reservation increased from
3250m to 4600m; the namespace CPU quota increased from 4 to 5. This is an
explicit compute-versus-latency baseline, not a code optimization, memory
improvement, node-packing result or cloud price. Full private comparison and
limitations are in `notes/portal-worker-cpu-comparison-20261030.json`.
The newer-runtime 2-CPU arm is also verified in
`runs/import-portal-overlap-current-worker2cpu-r1`: 100 unique correct, traced
actions, one worker pod/UID, three sampled graph children in both passes, no
API replacement and verified cleanup at 22:09:35 UTC. It uses the same frozen
profile and private fixture with newer runtime image `b46ffb19c`.

| Newer frozen build, import graph | Sequential p50 / p95, ms | Three-submission burst p50 / p95, ms |
|---|---:|---:|
| 650m worker, pass 1 | 520.61 / 675.79 | 1504.38 / 1761.52 |
| 2-CPU worker, pass 1 | 457.55 / 571.42 | 658.52 / 716.63 |
| 650m worker, pass 2 | 535.47 / 654.13 | 1614.50 / 1657.65 |
| 2-CPU worker, pass 2 | 466.89 / 531.10 | 639.37 / 698.04 |

Its 2-CPU control sequential p50/p95 were 52.10/85.77 and 52.81/62.98 ms;
burst values were 55.73/65.25 and 67.10/72.46 ms. Graph burst medians fell
56.23% and 60.40% relative to the same newer image at 650m; sequential medians
fell 12.11% and 12.81%. At 2 CPU, resolver-wall medians were 177.18/181.86 ms
sequentially and 288.14/281.86 ms in bursts, with 25 calls per execution.
Burst compile-wall medians were 84.79/84.87 ms, with 18 compiles unchanged.
The remaining concurrency penalty is smaller at the production worker's CPU
limit. This is not evidence of exponential growth or a full production replay.

Idle working sets were worker 270.4, API 312.2, PostgreSQL 227.0, pooler 14.3,
client 8.0 and Redis 6.1 MiB, total 838.0 MiB. The corresponding newer 650m
arm had worker 273.8, API 315.6, PostgreSQL 239.3, pooler 14.6, client 8.4 and
Redis 6.0 MiB, total 857.7 MiB. These idle samples do not establish a memory
optimization; limits and declared reservations remain unchanged between each
CPU pair. The two frozen runtimes also span multiple source changes, so their
difference cannot be assigned to one commit. Idle database observations and
all per-class CPU, RSS, working-set and private-child samples remain in the
raw records.

`notes/portal-current-worker-cpu-comparison-20261030.json` verifies the newer
pair. `notes/portal-concurrency-matrix-20261030.json` reconciles all four cells:
400 correct, traced actions; the same captured fixture; 25 lookups and 18
compiles for every graph execution; actual worker CPU requests/limits of 650m
or 2 with 2 GiB memory throughout; and verified cleanup. Each cell remains a
single local import-only diagnostic; observer cost was not independently
measured. The original workflow body, its SDK/provider fanout, browser
delivery and 33-second outlier remain outside this diagnostic.

### Initial Import Pipeline Diagnostic

`runs/import-smoke-r1` completed with 40 unique successful executions and one
valid correlated trace for each, across two blocked passes of ten requests per
class. Harness `dcb86a113`, runtime `b46ffb19c` (the frozen `a1edc19fd` runtime
plus table-index repair and opt-in tracing), and the selected image's
index/manifest/config identities are retained with raw results and logs.
Namespace teardown was verified. This reduced-seed pipeline has 65 seeded
definitions, 75 workflows total and a six-file, 1,745-byte synthetic fixture;
it is not a hosting-capacity or Halo-equivalence result.

| Observation | Pass 1 | Pass 2 |
|---|---:|---:|
| SDK/stdlib control worker-wall p50 / p95 | 59.805 / 113.242 ms | 52.720 / 72.789 ms |
| Import-graph worker-wall p50 / p95 | 255.004 / 395.653 ms | 265.392 / 319.082 ms |
| Graph entry-load median, before engine entry | 202.073 ms | 214.494 ms |
| Graph resolver calls per execution / aggregate median wall | 10 / 66.758 ms | 10 / 69.057 ms |

All fixture engine durations rounded to zero integer milliseconds. Resolver
and nested import walls overlap entry loading and must not be added to it.
The graph's ten resolver calls comprise three modules, one package, one
namespace and five missing optional dependencies probed by installed
`requests` 2.33.1 (`brotlicffi`, `brotli`, `chardet`, `simplejson`, `socks`).
The fixture constructs a request object and makes no provider call. This is
evidence of repeated import/probe work, not a reproduction of the live
5.7–14.9-second original outside-engine intervals. Production replays above
now identify the deployed image; trace observer overhead has not been
separately measured.

The idle snapshot after the declared 120-second wait is 770.9 MiB total
working set, including worker 279.3, API 312.6 and Postgres 153.0 MiB. The
earlier boot snapshot's worker is 678.9 MiB with an additional helper process;
retain that startup observation separately from settled idle. This smaller
catalog is not comparable to the full-M memory baseline as an optimization.
Per-class CPU counter windows and process samples are retained; the first
control window includes startup work and must not be described as steady CPU
per action. No workflow/library preload optimization was made in this run.

The full-catalog repetition `runs/import-m-current-r1` used the same frozen
harness, runtime image and six-file fixture, with 1,851 seeded definitions and
1,861 workflows total. All 40 executions had unique IDs, successful exact
outputs and one valid trace each; there were no API pod replacements and
namespace teardown was verified.

| Full-catalog observation | Pass 1 | Pass 2 |
|---|---:|---:|
| Control worker-wall p50 / p95 | 54.899 / 98.637 ms | 51.766 / 78.814 ms |
| Graph worker-wall p50 / p95 | 266.759 / 371.780 ms | 267.645 / 341.061 ms |
| Graph entry-load median | 215.864 ms | 215.649 ms |
| Graph resolver aggregate median wall | 65.933 ms | 69.884 ms |

Ten resolver requests per graph execution remained unchanged. Increasing the
catalog did not reproduce the production seconds-long delay in this small
fixture. Idle working set was 846.6 MiB total: worker 277.0, API 316.0,
Postgres 225.8, pooler 14.4, client 8.0 and Redis 5.4 MiB. This is an idle
diagnostic snapshot, not a validated capacity or hosting-cost result.
The generic report incorrectly displays these successful actions as failures
when their creation timestamp is absent; the import-specific validation and
timing table are correct. Preserve the original report and raw records, with
the display defect explicitly recorded in `analysis.json`.

### Exact Deployed-Build Comparison

`runs/import-m-prod-c2-r1` repeats the full-M diagnostic on the immutable
production base image, with only the same four opt-in trace files overlaid
from clean commit `f8bcf75ab`. It retains the deployed build's Python and
dependencies, including `requests` 2.33.1, and initializes its own schema and
worker image. Harness `dcb86a113`, catalog, fixture bytes/hash, profile and
blocked pass order match `import-m-current-r1`. All 40 actions had unique IDs,
correct successful output and valid traces; no API pod replacement occurred
and namespace teardown was verified.

| Observation | Deployed base pass 1 / 2 | Newer runtime pass 1 / 2 |
|---|---:|---:|
| Control worker-wall p50 | 52.159 / 53.265 ms | 54.899 / 51.766 ms |
| Graph worker-wall p50 | 321.217 / 317.552 ms | 266.759 / 267.645 ms |
| Graph worker-wall p95 | 380.309 / 368.448 ms | 371.780 / 341.061 ms |
| Graph resolver aggregate median | 134.688 / 136.994 ms | 65.933 / 69.884 ms |

The newer graph medians are 54.458 ms (17.0%) and 49.907 ms (15.7%) lower.
Each graph still makes ten resolver requests. Neither local runtime reproduces
the live multi-second loading interval. This is one diagnostic run per
runtime, not an independently replicated capacity comparison or a causal
attribution to a particular commit.

Settled working set was 880.4 MiB on the deployed base: worker 279.0, API
334.8, Postgres 233.9, pooler 15.2, client 9.6 and Redis 7.9 MiB. The newer
run was 846.6 MiB, including worker 277.0. These snapshots do not establish
tenant density or a confirmed memory saving. The declared 60-second idle DB
windows observed 20.05 transactions/s (10.17 rollbacks/s) on the deployed base
and 3.53 transactions/s (0.08 rollbacks/s) on the newer runtime; WAL LSN advance was
235.39 and 237.47 KiB/s. Session rollback counters are not workflow failures.
Preserve background work, latency and memory together when costing a runtime.
Raw results, image chain and limits are retained in both run directories and
`runs/import-current-versus-prod-c2-20261030.json`.

### Historical Socket-Based Comparison

`runs/import-m-pre-remote-a00-r1` used native base `a00bb09e00`, immediately
before the remote-launch change, with the four trace files from clean
`ada8cce650`. Its API, worker, schema and dependencies stayed on that base;
the frozen harness, full-M catalog and six-file fixture matched the other
diagnostics. All 40 actions had unique IDs, correct successful output and
one valid trace each. There were no API pod replacements, and namespace
teardown was verified at 19:21:34 UTC.

| Historical observation | Pass 1 | Pass 2 |
|---|---:|---:|
| Control worker-wall p50 / p95 | 81.007 / 970.028 ms | 59.292 / 81.369 ms |
| Graph worker-wall p50 / p95 | 453.369 / 1,030.995 ms | 450.769 / 874.029 ms |
| Graph entry-load median | 398.538 ms | 395.624 ms |
| Graph resolver aggregate median wall | 223.402 ms | 226.316 ms |

The graph still made ten resolver requests per execution. This older runtime
was slower on the small fixture than both later runtimes; it does not explain
the live portal regression. These are three architecture snapshots, not an
adjacent-commit experiment that isolates socket transport. The older SDK
server ran inside the worker's CPU budget, while the newer resolver ran in
the API's budget. No real Halo dependency graph or provider call was tested.

Historical idle working set was 1,019.1 MiB: worker 369.6, API 390.5,
Postgres 229.5, pooler 14.6, client 9.1 and Redis 5.8 MiB. The worker process
sample contained a 306.1 MiB private parent and a 47.9 MiB workflow template;
it had no warmed agent template. This differs from the newer process
topology and is not a like-for-like capacity saving. The idle DB window
recorded 17.18 transactions/s, 8.74 rollbacks/s and 216.21 KiB/s of LSN
advance. Generated and retained WAL were not measured. Raw records, source
and image identities, trace selection and these limits are retained in the
run's `analysis.json`; the original report's timestamp display defect remains
preserved.

### Required Hosting Evidence

The harness now captures `pg_stat_wal.wal_bytes` and its reset marker alongside
the unchanged legacy LSN counter. Counter windows reject missing evidence,
reset-marker changes and regressions while retaining raw before/after samples.
Snapshot reports also refuse generated-byte totals with unknown reset identity.
The SQL was verified as an ordinary app role against disposable PostgreSQL 16;
both byte counters arrived as exact JSON integers. Harness validation passed
253 stdlib tests on Python 3.12, focused run/report checks on Python 3.14,
and Ruff. Earlier failed fixture checks and their causal corrections are
preserved under `notes/wal-source-validation*-20261030/`. This verification
adds no capacity or storage-retention result.

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
normal operating target. Until realistic demand, region/SKU availability and
cloud calibration are supplied, these outputs remain unpriced with missing
inputs listed.

### Dated Provider Price References

Checked 2026-10-30, USD public catalog prices, before discounts or taxes.
NYC2 availability and the actual billable SKU remain unverified; these are
worksheet inputs, not a quote or a calibrated hosting result.

| Reference | Monthly price | Included resources / pricing basis |
|---|---:|---|
| DigitalOcean Basic, Regular | $24 | 2 shared vCPU, 4 GiB RAM, 80 GiB SSD |
| DigitalOcean Basic, Regular | $48 | 4 shared vCPU, 8 GiB RAM, 160 GiB SSD |
| DigitalOcean CPU-Optimized, Regular | $84 | 4 dedicated vCPU, 8 GiB RAM, 50 GiB SSD |

The [Droplet catalog](https://www.digitalocean.com/pricing/droplets) distinguishes
shared and dedicated CPU plans. A 4 GB memory envelope with a 4 CPU quota
does not establish fit or performance on the $24 two-vCPU plan. Kubernetes
system reserves also reduce node allocatable CPU and RAM below advertised
resources; the full envelope cannot be assumed to fit on a four-vCPU node.

[DOKS worker nodes](https://docs.digitalocean.com/products/kubernetes/details/pricing/)
use Droplet pricing, with a 672-hour monthly cap for bundled plans. Its
included control plane has an optional $40/month HA charge. A
[regional HTTP load balancer](https://docs.digitalocean.com/products/networking/load-balancers/details/pricing/)
starts at $12/month per node. These shared charges require an allocation
policy; they are not automatically incurred once per customer.

[Block volumes](https://docs.digitalocean.com/products/volumes/details/pricing/)
cost $0.10/GiB-month; allocated persistent capacity is charged while it exists.
[Spaces](https://www.digitalocean.com/pricing/spaces-object-storage) starts at
$5/month with 250 GiB storage and 1 TiB outbound transfer, then $0.02/GiB
additional storage and $0.01/GiB additional transfer. Archive retention,
backup copies and restore requirements must determine billable stored bytes;
the generated-WAL counter alone does not price them. No resource was provisioned.

Still missing before a complete hosting card: repeatable current constrained
results, confirmed mixed capacity with adequate class sample counts, long-run
storage/object growth and retention evidence, soak/recovery drills, and cloud
calibration. The 10%-weight mixed class yields only about 805 observations in
an hour at 2.236/s; the strict 0.1% failure bound needs 2,995 failure-free
observations per class. Ordinary independently capped confirmations cannot
prove it. A separately predeclared pooled campaign needs its own immutable
manifest and stated exchangeability assumption; see the
[campaign implementation](../plans/2026-10-30-mixed-confirmation-campaign.md).
Its focused unit verification is complete. The representative 4 GB preflight
on frozen harness `851fe8753` stopped during preparation: the admitted-worker
guard rejected unequal requests and limits, before seed or workload traffic.
The campaign remains invalid and its five segments remain unstarted. Both
namespaces were removed and cleanup verified at 19:41:39 UTC.

The harness had set the smaller worker request before startup but wrote the
worker-size policy only later through the API. A default-size warm worker
could therefore start outside the declared profile. The correction supplies
the same profile through the existing canonical startup policy import before
the controller starts, then retains the normal API policy write. It does not
relax the resource guard. The rejected pod's full specification was not saved
before teardown; exact rejected quantities are inferred from source rather
than claimed as captured pod evidence. The invalid result and disposition
remain in `runs/mixed-4gb-a865-campaign-r1` and
`notes/fourgb-preflight-r1-disposition-20261030.json`.

The import harness also accepts a validated private manifest through
`run --import-fixture`; fixture metadata and hashes are retained without
copying private source into the repository. Focused checks of that path,
default import consumers and profile bootstrap passed 67 tests on each of
Python 3.12 and 3.14, plus Ruff. The private portal source snapshot comprises
18 files and 410,707 bytes; the two constant-result diagnostic wrappers make
20 files and 411,390 bytes. The fixture imports the captured modules and
returns a constant; no captured workflow body or provider call executes.
Its SHA-256 is
`e4e59e45d8a23726666f285b4979bbc88e3a85deb5b73d8b34eb839e18263881`.

Admission rejections now retain their raw pod/quota evidence before preparation
fails and cleanup begins. The standalone observational soak also has source
coverage for immutable passing-campaign proof, 70% rate, two-hour duration,
raw checkpoints, terminal failures in its denominator, sampler/driver failures
and cleanup. A first source check found one incorrect test expectation: fake
database size decreased 100 to 90 bytes with one terminal outcome, so the
per-1,000 delta is -10,000, not -50,000 bytes. Only that expectation changed;
the original failure and disposition are retained. The corrected source passed
all 293 benchmark tests on each of Python 3.14 and 3.12 plus Ruff, under the
shared host lock. This verifies harness behavior, not live soak or capacity.
No fully passing mixed campaign exists yet, so the soak remains unstarted.
