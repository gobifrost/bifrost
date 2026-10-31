# Portal Baseline Replay and Capacity Benchmark Completion

Status: baseline saved; constrained capacity, soak, recovery and hosting report
are incomplete. Accepting the roughly 277 MiB idle worker measurement does not
freeze a memory target or end optimization.

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

## Remaining Completion Gates

These finish the existing deliverable; further memory reduction is not a gate.

1. **Correctness and 4 GB capacity.** Reconcile the outstanding concurrent SDK
   table-creation repair with current main, then freeze the candidate. The
   earlier current-runtime confirmation had zero correct durable SDK results.
   Run a fresh full-seed concurrent preflight and matched 8 GiB/4 decimal GB
   capacity measurements. The first 4 GB attempt was invalid before traffic;
   its startup-policy fix is source-verified, not a measured pass. Preserve
   correctness/latency/reliability/backlog gates and measure the whole tenant,
   including Postgres, Pooler and Redis.
2. **Sustained mixed load.** Execute the immutable five-segment confirmation
   after its representative preflight. The planned 2.236/s is a historical
   candidate, not certified current capacity. A changed rate/design requires
   a new declared campaign. Keep every segment and per-class reliability
   evidence; incomplete samples cannot pass as capacity.
3. **Interactive app latency.** Finish the planned deterministic fake-provider
   fixture through the shared V2 result path. Exercise broad imports and
   shallow/detailed reads under concurrency, from action through rendered
   result. Retain the import matrix and live canary as separate controls.
4. **Soak and recovery.** Run the implemented two-hour soak at 70% of a passing
   mixed rate and complete the declared recovery evidence. Report memory and
   connection trends, database/generated-WAL/object growth, accepted work,
   duplicate/lost effects and recovery bounds. Resolve the outstanding
   database-effect guarantee and per-pool evidence before claiming those
   drills passed. HA correctness remains a separate availability tier.
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
