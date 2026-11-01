# Portal Baseline Replay and Capacity Benchmark Completion

Status: baseline and retention rehearsal saved; constrained capacity, soak, recovery and hosting report
are incomplete. Accepting the roughly 277 MiB idle worker measurement does not
freeze a memory target or end optimization.

## Objective and Decision Boundary

The deliverable answers what a namespace can handle, how its resource and storage
use change with demand, and which assumptions are needed to budget hosting and
margins. It consists of reproducible baseline records, one evidence-backed card
per resource envelope, and a prioritized proposal for improvements. A valid
failure is a result; a blocked capacity claim is not a reason to change the
platform during measurement.

The user clarified the scope on October 31: testing and findings first; discuss
platform changes and their plan before implementation or merging. No new PR,
platform implementation, merge or production deployment is part of the remaining
benchmark work. PR #1164 remains an open draft with auto-merge disabled. Its
experimental-image results remain separate from the deployed-platform baseline.
The terminal-delivery fix and activation had already merged before this scope
correction; the original portal replay records their resulting behavior.

Remaining deployed-platform measurements use the exact captured production image
`ghcr.io/gobifrost/bifrost-api@sha256:a15c4761740ae3ac918c2e0280464f25756d08fa8d03485b2e155c6d387bb692`
(source `e611d31bbba3d7601980271c774cad5e821b1e97`). This is a frozen baseline,
not a claim about whichever build is newest later. Benchmark harness revisions
and runtime revisions are recorded separately.

The sequence is: original-issue replay; constrained correctness/capacity and
browser timing; conditional sustained-load/storage and recovery evidence;
namespace/cost cards; proposed fixes for discussion. Stop a scenario at its
declared failure gate, preserve its raw evidence, and record dependent gates as
blocked or unexercised. Do not retry until green, substitute an experimental
runtime, relax acceptance thresholds, or implement a platform fix to continue.

Worker scaling is an explicit part of constrained capacity: hold per-worker
CPU/memory, shared-service allocations, runtime and workload fixed, vary the
worker count, and report successful-throughput speedup, scaling efficiency,
latency and total namespace resource/cost changes. Measure where gains flatten
and identify the limiting component. The adaptive 4 GB versus 8 GiB comparison
cannot establish this because it changes multiple allocations and actual worker
counts. The fixed-count series needs its own declared profiles and gates; a
failed representative correctness prerequisite blocks its capacity claim.

Scale-from-zero is a separate required comparison: zero versus one warm worker
at the same maximum worker count, per-worker resources and shared-service
allocations. Record first-request and burst latency, worker launch/readiness,
queue drain, time to usable capacity, return to zero after demand stops, and
idle/startup/loaded namespace resource use. Confirm the source and deployment
defaults rather than assuming zero warm workers is the deployed policy. A warm
steady-state throughput result cannot stand in for a scale-from-zero result.

The bounded proposed series keeps the 4 GB profile's fixed components at
2,600m CPU and 1,568 MiB, with 650m CPU and 1,024 MiB per worker:

| Worker Ceiling | Total CPU Requests/Limits | Total Memory Requests/Limits | Static Arm (Warm, Max) | Elastic Arms (Warm, Max) |
|---:|---:|---:|---|---|
| 1 | 3,250m | 2,592 MiB | (1, 1) | (0, 1) versus (1, 1) |
| 2 | 3,900m | 3,616 MiB | (2, 2) | (0, 2) versus (1, 2) |
| 4 | 5,200m | 5,664 MiB | (4, 4) | (0, 4) versus (1, 4) |

The four-worker arm requires a larger declared quota and is not a 4 GB result.
Each worker can run multiple executions. In the elastic arms the maximum is a
ceiling, not proof that demand required or the controller launched that many
workers. Declare the burst before running, retain the observed worker/active-run
timeline, and report an unreached ceiling without increasing the burst until the
desired worker count appears. Static arms establish the fixed-count comparison.
Before static timing, observe all N workers ready. Before cold timing, observe
zero reserved/starting/ready/draining workers and zero active executions after
setup. Keep images cached and label the measurement pod/process-cold, not
uncached-node provisioning. Keep the idle observation longer than the configured
cooldown and record the actual return to zero; the existing 120-second idle
window cannot prove this with the 300-second source default.
Read back the effective worker policy, including idle seconds, before and after
each experiment; profile labels and source defaults do not prove the stored
runtime setting. Preserve the worker lifetime after bursts and distinguish
traffic inside versus beyond that cooldown when estimating idle cost.

This series is a test proposal, not completed evidence. Existing five-second
resource samples and request timestamps support consumption/latency reporting;
worker state/readiness, active work, admission reservations and pod-lifetime
resource accounting need additional benchmark observation before claiming
precise time to capacity or scaling cost. No platform instrumentation change is
authorized by this proposal. The source defaults at captured `e611d31bb` are
one warm worker, maximum six, 2,048 MiB standard workers, 8,192 MiB largest
workers, and 300 idle seconds; the effective live policy was not obtained in
the optional read-only check and remains unknown.

## Saved Baseline

The versioned [baseline record](baselines/portal-2026-10-31.json) retains two
distinct measurements:

| Measurement | Saved result | Interpretation |
|---|---|---|
| October 31 live portal recheck | Three original read-only inputs, once sequentially and once together; six successes, identical caller/input hashes, stable pods | Operational CLI/API/WebSocket latency. Provider, caches and background conditions remain uncontrolled. One observation per cell cannot establish percentiles, significance or capacity. |
| October 30 controlled import matrix | Two frozen runtime images × 650m/2-CPU worker profiles; 100 correct traced executions per cell | Local import/concurrency sensitivity. Excludes the original workflow body, Halo calls and browser render. Two passes are not independent randomized repetitions. |

The [cumulative ledger](2026-10-30-hosting-baselines.md) retains earlier slow
observations, failures and memory/latency tradeoffs. Neither new result certifies
namespace throughput, tenant density or hosting cost.

Private inputs, customer source and full results stay outside git. A separate
5 MB evidence bundle at
`/home/jack/bifrost-bench-evidence/baselines/portal-2026-10-31-v1/` contains the
original inputs, caller/input hashes, six raw terminal records and transport
traces, runtime resources, clock evidence, original tracer/runner and reusable
replay runner. It also includes the captured 20-file import fixture, all four
result/report/analysis sets, declarations, build receipts and frozen supervisor.
`files.json` inventories all 66 artifacts by SHA-256; its identity is pinned
in the versioned record. Directory/file permissions are 0700/0600.

The two import runtime images and matching client image are additionally saved
in `portal-2026-10-31-v1-images.tar` alongside the bundle. The adjacent
`portal-2026-10-31-v1-images.json` pins image IDs and the archive hash. The
original cluster, images, source commits and evidence remain intact. These are
local retained copies; an off-host backup is not established here.

## Verify and Replay

This verifies every saved artifact and input without authentication, Kubernetes
access or workflow execution:

```bash
python3 /home/jack/bifrost-bench-evidence/baselines/portal-2026-10-31-v1/replay.py \
  --bundle /home/jack/bifrost-bench-evidence/baselines/portal-2026-10-31-v1 \
  --check-only
```

Public scalar metrics were reconciled against all six original terminal records
and all four raw result hashes. Five offline tests exercise tampering,
caller/input identity, phase arithmetic, descriptive regression deltas, six-call
replay and refusal to overwrite evidence. They use mocked CLI processes with
container networking disabled; they add no provider traffic or capacity result.

For an authorized live operational recheck, use the normal authenticated,
API-matched CLI. The retained environment is
`/tmp/bifrost-portal-recheck-20261031/.venv/`, version
`1.4.2-dev.1793422955`. Preserve or recreate a compatible authenticated CLI;
the bundle contains neither credentials nor a portable virtual environment.
Record any CLI version change as another comparison variable. Inspect the
intended build with an explicit Kubernetes context and supply its immutable
digest. For the saved build:

```bash
cd /tmp/bifrost-portal-recheck-20261031
flock /tmp/bifrost-capacity-host.lock \
  .venv/bin/python /home/jack/bifrost-bench-evidence/baselines/portal-2026-10-31-v1/replay.py \
  --bundle /home/jack/bifrost-bench-evidence/baselines/portal-2026-10-31-v1 \
  --python /tmp/bifrost-portal-recheck-20261031/.venv/bin/python \
  --expected-image-digest sha256:a15c4761740ae3ac918c2e0280464f25756d08fa8d03485b2e155c6d387bb692 \
  --out /home/jack/bifrost-bench-evidence/runs/portal-next-declared-run
```

The private runner checks every artifact hash, uses exactly the saved three
read-only cases sequentially then together, and reconciles original
caller/input/workflow identity with durable results. It preserves the existing
CLI POST/WebSocket/GET behavior. Each declared experiment needs a new output
directory; failed/partial attempts are retained, with no automatic retry. API
and worker images are checked before, between and after groups; pod changes
invalidate a stable-runtime comparison. The result saves raw records and
absolute/percentage deltas for wall time, engine time, outside-engine residual,
CLI time and execution CPU/memory. These are descriptive deltas, not causal or
statistical claims. Explain changed workflow/dependency sources or provider
result counts before attributing a difference to platform performance.

This is a private operational diagnostic, separate from guarded local capacity
traffic. It does not collect browser timing or a new clock bound. Capture fresh
clock probes before interpreting cross-machine completion-to-frame latency;
the saved October 31 bound cannot be reused.

For controlled local replay, the private `import/<run-id>/manifest.json` files
preserve the exact argv, fixture, profile and build receipt. Prepare the
declared clean harness in the existing isolated benchmark worktree under the
shared host lock: original 650m cells used `74faa9aab`; 2-CPU cells used
`2bacf728f1ec96f2d496f04dec492d9abce76217`. Never switch an active worktree.
Load the archived images if necessary. Copy a declaration to a new file and
change only its output path and matching `--out` argv value; repoint the fixture
to the saved copy if needed, preserving its hash. Run the existing frozen
supervisor from `/home/jack/GitHub/bifrost/.worktrees/bench-capacity` under
`flock /tmp/bifrost-capacity-host.lock`, through its verified cleanup. Within
that supervised window, the direct harness command for the newer 2-CPU cell is:

```bash
python3 scripts/bench/run.py run \
  --profile m-import-overlap-worker-2cpu --backend none \
  --api-image bifrost-api:kind-a74e45295998-importtrace-b46ffb19c-md3112 \
  --import-fixture /home/jack/bifrost-bench-evidence/baselines/portal-2026-10-31-v1/import/fixture.json \
  --out /home/jack/bifrost-bench-evidence/runs/import-next-declared-run
```

Use `m-import-overlap` for 650m and its declared image for the older-runtime
arm. Keep the full seed, one warm/max worker, ten sequential actions, five
bursts of three per class and two passes. Compare phases/passes separately.
The 2-CPU allocation also requires the recorded CPU quota increase. These are
import diagnostics, not the 4 GB hosting profile. New source/image experiments
use the existing harness/A-B path and record every changed variable.

## Storage Retention Rehearsal (October 31)

The [fresh local retention rehearsal](2026-10-31-storage-retention-results.md)
passed: the canonical job deleted all 42 aged fixture executions and their 329
log rows. PostgreSQL allocated bytes remained about 403 MiB immediately after
deletion. Equal-sized repetitive and high-entropy result payloads differed by
about 87 times in stored result-column size. These results establish fixture
storage and deletion behavior; sustained growth, backups and cloud billing
remain separate evidence.
The rehearsal used the unmerged table-uniqueness candidate; it is supporting
experimental storage evidence, not an unmodified production-runtime baseline.

## Remaining Completion Gates

### Captured Production API/Worker Results

These local observations use the stock captured `e611d31bb` API/worker image;
the frozen benchmark client and infrastructure are identified separately.

| Scenario | Result | Capacity Interpretation |
|---|---|---|
| 4 decimal GB, SDK-only, offered 3.90625/s | Correctness preflight 127/127; confirmation 3,735/3,735 correct by deadline; end-to-end p95 1.815980 s, above the 1.5 s gate | Valid negative result; this offered rate is not accepted. Queue, throughput, strict reliability, backlog and tenant-fault gates passed. Host invalid reasons were empty; possible external memory pressure remained an unproven warning. |
| M / 8 GiB SDK-only | Measurement sequence pending | No stock SDK capacity claim yet. |
| Mixed load and conditional soaks | Declared sequence pending | No stock mixed capacity or sustained growth claim yet. |
| Browser app path | 24/24 correct actions and durable records; verified cleanup | Descriptive timing only; see the omitted-timer finding below. |
| Stock retention rehearsal | Queued behind the same host lock | Earlier candidate storage evidence stays separate. |
| Fixed-count scaling and zero-warm elasticity | [Declared experiment](2026-10-31-worker-scaling-experiment.md); tooling prepared in a separate checkout, with Docker verification queued | Neither scaling efficiency nor cold-start capacity has been measured yet. |

The complete 4 GB SDK raw artifact is retained under
`runs/stock-capacity-matrix-20261031-r1/4gb-capacity/sdk-confirmation/`.
The [stock 4 GB scalar baseline](baselines/sdk-confirmation-2026-10-31-4gb-stock-valid-failure.json)
pins its result, declaration and immutable image identities for reproduction.
Its 95% end-to-end p95 interval was 1.730816–1.899850 seconds; all 3,735
arrivals reconciled correctly and all tenant fault counters were zero. Do not
attribute its difference from the experimental candidate to a platform fix:
the observations were not randomized matched repetitions.

The same stock scalar retains component resource measurements: sampled tenant
CPU averaged 1.071790 cores over covered intervals; simultaneous charged-memory
and working-set peaks were 1,722,519,552 and 1,466,437,632 bytes. Interval
coverage was 99.03%. These are sampled tenant lower bounds for SDK-only work,
not a smaller safe quota or a mixed-workload capacity claim. Per-role memory
peaks are not additive. The Pooler showed throttled scheduling periods despite
modest mean CPU use; that is a candidate for a controlled resource comparison,
not proof of the latency cause.

### Collector Validation (October 31)

The frozen `df605bb5e` diagnostic used the 4 GB resource envelope and a reduced
65-workflow seed. All 129 measured-window submissions produced correct durable
results, including 28 SDK workflows; there were no duplicate executions,
restarts, OOMs, evictions or quota denials. All seven storage/environment probes
completed. Raw storage frames, event/cohort rates and component resource coverage
are retained in the private `runs/current-4gb-metrics-smoke-r5/` artifact.

This is collector and correctness evidence only. A separate development stack
was active, the observation window was 60 seconds, and its statistical gates
did not pass. It establishes neither a sustainable rate nor a full-seed
concurrency baseline. Earlier failed collector attempts are retained beside it:
unnecessary optional workflow requirements, a Pooler matching the database label,
and Redis returning configuration keys in a different order each received a
specific fix and regression coverage before the next diagnostic.

These finish the existing deliverable; further memory reduction is not a gate.

1. **Correctness and constrained capacity.** The
   [completed experimental SDK comparison](2026-10-31-sdk-resource-comparison.md)
   reconciled 3,735/3,735 arrivals correctly in each envelope. The 4 decimal GB
   result failed its 1.5-second end-to-end p95 gate at 2.154 seconds; M/8 GiB
   passed the SDK-only gate at 0.612 seconds. Neither establishes production or
   mixed capacity because the runtime includes unmerged PR #1164. The
   [earlier host-invalid observation](2026-10-31-capacity-confirmation-results.md)
   is retained separately. Run the declared checks on the unmodified captured
   production image next. Preserve
   correctness/latency/reliability/backlog gates and measure the whole tenant,
   including Postgres, Pooler and Redis.
2. **Sustained mixed load.** Execute the immutable five-segment confirmation
   after its representative preflight. The planned 2.236/s is a historical
   candidate, not certified current capacity. A changed rate/design requires
   a new declared campaign. Keep every segment and per-class reliability
   evidence; incomplete samples cannot pass as capacity.
3. **Interactive app latency.** The
   [stock R6 browser observation](2026-10-31-browser-setup-diagnostic.md#r6-stock-browser-observation)
   completed 24/24 actions with validated durable records and verified cleanup.
   It covers broad imports and shallow/detailed synthetic reads under concurrent
   app actions. First terminal observation to render was 18.7–44.8 ms; broader
   dependency cases still took several seconds overall. Three samples per cell
   establish descriptive timings, not capacity or a p95. Retain the import
   matrix and live canary as separate controls; the original 33-second outlier
   remains unattributed. Fixed-count scaling and scale-from-zero remain separate
   unmeasured scenarios.
   The shared-graph first-action cell also had a 5.043-second median residual
   outside its reported workflow timer; its stored running interval was about
   5.081 seconds versus 0.302 seconds reported by the engine. The omitted
   lifecycle time remains an investigation priority. The short terminal-to-render
   interval does not establish where that larger residual was spent.
4. **Soak and recovery.** Run the implemented two-hour soak at 70% of a passing
   mixed rate and complete the declared recovery evidence. Report memory and
   connection trends, database/generated-WAL/object growth, accepted work,
   duplicate/lost effects and recovery bounds. Unresolved database-effect
   guarantees and missing per-pool evidence prevent certification of the
   affected drills; report them as findings for discussion. HA correctness
   remains a separate availability tier.
5. **Namespace capacity and cost report.** Produce a card for each measured
   envelope: requests/limits, idle/loaded consumption, sustainable successful
   rate by workload, p50/p95 latency, safe operating rate/headroom, storage
   growth and recovery limits. Compare baselines with absolute/percentage
   changes and uncertainty. Complete the low/base/peak-demand worksheet with
   shared infrastructure, builds, retention/backups, network and provider/model
   spend. Customer-facing density, budgets and margins additionally require
   cloud calibration, dated actual SKU prices, demand, redundancy and selling
   price assumptions.

Local benchmark completion means reproducible valid results for the declared
scenarios and capacity cards explaining their limits. Customer hosting guidance
also requires cloud calibration and explicit cost inputs. Preloading, module
splitting and later memory reductions can then be judged against these retained
baselines without expanding this benchmark indefinitely.
