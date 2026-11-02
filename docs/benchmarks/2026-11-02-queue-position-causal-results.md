# Queue-Position Causal Results — 2026-11-02

The synchronous whole-queue position refresh is the primary cause of the severe
N2/Redis-100m throughput collapse. Removing that operation in an experimental
image restored all 2,444 correct, on-time completions at 8 offered workflows/sec,
with the same resources. Astra independently reviewed the source and evidence
and agreed with this conclusion.

The candidate still misses the app latency targets at 8/sec. Its hot owner API
reaches its CPU limit while the other API, Redis, and workers retain capacity.
This is a causal diagnostic, not a shipping fix or a capacity certificate.

## Same Workload, Same Resources

| At 8 offered workflows/sec | Original control | Redis-only contrast | Queue refresh disabled |
|---|---:|---:|---:|
| Redis CPU allocation | 100m | 500m | 100m |
| Correct completions during 300-second window/sec | 3.187 | 6.347 | **8.127** |
| Arrival cohort correct by deadline | 802 / 2,444 | 1,812 / 2,444 | **2,444 / 2,444** |
| Arrival cohort unfinished after drain | 1,317 | 167 | **0** |
| Admission p95 | 29.130 s | 0.525 s | **0.105 s** |
| Observed completion p95 | 238.499 s | 79.606 s | **4.463 s** |
| Queue p95 | 239.085 s | 79.314 s | **4.037 s** |
| Redis commands/sec over storage snapshot interval | 4,856 | 2,871 | **681** |
| Overall latency-aware result | Fail | Fail | **Fail** |

Window throughput counts completion events during the measurement window.
The arrival cohort counts the same scheduled requests through their deadlines
and drain. These denominators differ. Overloaded control latency excludes
unfinished requests; the failure-aware cohort gates still count them. Redis
command rates cover their recorded warmup/measurement/drain snapshot intervals,
not precisely the 300-second measurement window, and include all commands.

Removing the refresh yielded 2.55 times the observed completion throughput,
about 53 times lower observed completion p95, and about 277 times lower admission
p95. It also used less Redis CPU than increasing Redis's CPU allocation fivefold.
The experiment disables both queue enumeration and publication, so these results
do not isolate Redis PUBLISH cost from the query/materialization cost.

The candidate passed 2/sec and 4/sec. At 4/sec, both control and candidate
completed 1,179/1,179 correctly by deadline; completion p95 improved from
0.574 to 0.482 seconds. The candidate's 4/sec arm retains the warning
`possible external memory pressure (unproven)`. Its decisive 8/sec arm has no
validity warnings or invalidations, and no duplicate executions, OOMs, quota
denials, evictions, restarts, or terminal failures.

At 8/sec the candidate passes the existing throughput gate (0.997545 versus
0.995 required), but fails completion p95 <=1.5 seconds and queue p95 <=2 seconds.
The backlog-drift bootstrap is inconclusive. Consequently the highest tested
rate meeting the existing overall targets remains **4/sec**; 8/sec is not a
validated comfortable operating point. There was no 16/sec trial or wider matrix.

## Why the Collapse Happens

In the frozen runtime, `publish_queue_positions()` selects all pending async
executions and serially awaits a publication for each one. Admission waits for
this before waking dispatch; the consumer also waits for it after claiming work
and before child routing. As backlog grows, each acceptance/claim does more
notification work, which delays useful work and grows backlog further.

Relevant source paths in control runtime `3603b2d3bd172eadf30a8516cc8fa6afc14f1ad5`:

- `api/src/services/execution/queue_tracker.py`: full pending snapshot and serial fanout.
- `api/src/services/execution/workflow_dispatch.py`: awaited publication before wake.
- `api/src/jobs/consumers/workflow_execution.py`: awaited publication before remote routing.
- `api/src/core/pubsub.py`: per-execution Redis publication.

The Redis-only contrast establishes that more Redis CPU mitigates the problem.
The no-op contrast establishes that the entire critical-path refresh operation
causes the severe collapse at the original resource allocation. There is no
matched historical runtime comparison proving which commit or migration
introduced it.

## Remaining Constraint and Ownership

Candidate 8/sec mean CPU consumption:

| Component | Observed CPU cores | Allocated cores |
|---|---:|---:|
| Two APIs combined | 1.084 | 1.500 |
| Two workers combined | 0.565 | 1.300 |
| Redis | 0.040 | 0.100 |
| PostgreSQL | 0.156 | 0.750 |

Aggregate API headroom conceals replica imbalance. A read-only DB snapshot
during 8/sec proved both workers share one `remote_owner_id`. Their long-lived
control WebSockets pin remote-executor/RPC work to that API. The hot API reaches
approximately 0.752 core against its 0.750 limit; its maximum sampled throttled
period fraction is 0.981. This fraction is not CPU utilization or time spent
waiting. The SQL capture windows show approximately 0.378 and 0.677 process CPU
cores on the two APIs. This is not a balanced two-API capacity measurement.

The original control lacks direct worker-owner IDs. However its per-replica
remote-owner fence SELECT counts closely match the candidate at lower rates:
150/1,121 versus 149/1,122 at 2/sec, and 162/2,042 versus 159/2,051 at 4/sec.
Astra judged owner placement a small residual caveat, not a credible alternative
explanation for the large intervention effect. API identities remained stable
through the candidate. HTTP admission connection affinity is not directly measured.

## Proposed Platform Change for Discussion

Preserve durable queue state, authorization, normal Pending/Running/terminal
events, and the UI's queued indication. Replace the synchronous full refresh
on every acceptance/claim with a coalesced refresh outside admission and child
start. Give queue-position freshness an explicit bound and bound publication to
actual UI need. Use the existing shared transports rather than a separate job
or queue subsystem. No new installation tuning knobs are proposed.

The benchmark no-op removes live queue-position publication and must not ship
as-is. Agree the UI-preserving change before implementation, then repeat this
exact N2/100m comparison. Investigate owner API placement as the next remaining
scaling constraint; do not claim that worker additions alone now scale linearly.

## Reproduction, Provenance, and Cleanup

Use the command in [the test declaration](2026-11-02-queue-position-causal-test.md).
It fixes N2 workers at 650m/1GiB each, two APIs at 750m/512MiB each, PostgreSQL
at 750m/1536MiB, and Redis at 100m/64MiB. Total tenant requests/limits are
3.8 CPU / 4.75 GiB. The profile exactly matches the control. Both use the same
2/4/8 sequence, seeds, 30-second warmup, 300-second windows, 60-second drain,
validity gates, and first-nonpass stop. Providers/load generation and object
storage are outside the tenant budget. This local Kind result does not emulate
a literal 8 GiB cloud host or establish multi-namespace hosting capacity.

Only `publish_queue_positions()` and its now-unused imports differ in the
candidate runtime. Its function becomes an async no-op; the source of
`get_queued_executions()` stays unchanged, but its call within publication is
skipped. Dispatch, wake, RPC, normal status paths, and dependencies are unchanged.
The candidate retains every base-image layer and adds one COPY layer. Image
verification checked 1,317 runtime files, allowing only this module to differ,
and successfully imported/called the no-op with networking disabled.

- Control runtime: `3603b2d3bd172eadf30a8516cc8fa6afc14f1ad5`.
- Candidate runtime: `604138a5bb8132f1cad92bd386088d8555a5c134`.
- Harness at launch: `66de262da82bf8d9a1bae452c79dd55328b64d36`.
- Control image ID: `sha256:babc52f1c0ac8c5482488c60511d945b220ae154ea56789d7dbe068ee5fc9e09`.
- Candidate image ID: `sha256:3e7e8fd00a171512b36d127f5013d8208623d26da935414acfbbcb633a5e22ff`.
- Control result SHA256: `e870f5e9c90b6dc51dcbe6dd5528987dd2e82b0760c79cfd1de46ede525302d7`.
- Candidate result SHA256: `89734d0c4e9bdb0c96eaf17371b20d747169afb54d4f2709876f56960ef87808`.

Raw evidence: `/home/jack/bifrost-bench-evidence/runs/queue-position-causal-20261102-r1`.
Support, archives, owner snapshot, and completion receipt:
`/home/jack/bifrost-bench-evidence/supervision/queue-position-causal-20261102-r1`.
The accompanying scalar JSON preserves all three N2 comparisons, source hashes,
SQL-window counts without raw statements, resources, and storage deltas.

The run finished at **2026-11-02T23:35:48Z**, with root and count cleanup verified.
The process exited 0. A separate guarded check found both owned namespaces absent
and the host lock available; other clusters/stacks were left intact.
No production changes, deployments, PRs, or merges were made.

Focused harness verification: 14 tests passed with
`python3 -m unittest scripts.bench.tests.test_queue_position_causal scripts.bench.tests.test_resource_curve -v`;
`ruff check scripts/bench/queue_position_causal.py scripts/bench/tests/test_queue_position_causal.py`
and `git diff --check` passed. Runtime verification comprises the image integrity
check, no-op smoke check, and live preflight/cohorts. Full backend/frontend suites
were not run for this benchmark-only intervention; they do not validate a
UI-preserving production implementation.
