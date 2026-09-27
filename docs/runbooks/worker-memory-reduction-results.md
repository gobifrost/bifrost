# Worker memory reduction: local results

This report preserves the first-pass measurements, supervisor pass, and final
launcher/import pass. See the final section for the current implementation and validation.

**Current result:** 155.0 MiB settled idle after simple and SDK workloads, down
from 321.4 MiB (52%). This is about 163 MB in decimal units; the 100 MB target
remains unmet. Customer code and dependencies are additional.

## Scope and user impact

This worktree reduces eager imports and duplicate startup work in the existing
worker. It preserves the supervisor, a single preloaded template, and a fresh
isolated child for each execution. Existing installations receive these changes
with the application update; no Settings option or Kubernetes policy is needed.
The 100 MB target is not yet achieved. Measurements below use MiB (1,048,576
bytes), and include the complete worker container rather than just one process.

## What changed

- Model and repository package exports resolve their requested module on demand.
  Explicit ORM access still registers every table and mapper.
- SDK facades keep their public class identities while loading clients and models
  when needed. Common execution SDK modules remain preloaded in the template.
- The template no longer preloads SQLAlchemy or requests, or repeats dependency
  installation already performed by the supervisor before startup/recycle.
- A small shared module-cache contract lets execution imports avoid loading the
  asynchronous storage implementation and S3 clients.
- Synchronous inline jobs no longer enter the UI pending queue, matching the
  workflow producer and consumer. They still store execution context, publish to
  RabbitMQ, persist results, and return through their private result channel.

The earlier 123 MB application and 111 MB database figures were **cumulative
process footprints in separate import probes**, not additive memory buckets or
source-code sizes. They included Python and all transitive imports. They must not
be added together to explain the worker total.

## Comparison method

The baseline is `bifrost-elastic-spike:769-final` plus only the synchronous inline
queue fix, tagged `bifrost-elastic-spike:769-memory-baseline`. The slimmer image is
`bifrost-elastic-spike:769-memory-final`. Both run in the same disposable Kind
cluster with the same dependencies, configuration and sequential workloads.
No user requirements or workspace code are installed.

An initial timing comparison was invalid: synchronous inline jobs accumulated in
`bifrost:queue:pending`, and subsequent submissions spent increasing time
broadcasting obsolete queue positions. The isolated cluster contained 303 stale
entries. After completed workloads stopped, that local key was cleared, and both
images were compared with the same producer fix. Earlier warm latency results in
the original spike report remain historical observations, not evidence of an
import-related performance change.

The measurement is cgroup current minus inactive file pages. A short Python
measurement process adds overhead. Immediate post-run snapshots can include
exiting children; settled idle snapshots report the supervisor, tracker and
template only. Peak is historical per container and includes startup/workloads.

## Measured results

| Whole worker | Baseline | Slimmer worker |
| --- | ---: | ---: |
| Before sequential workload (after harness warmup) | 319.2 MiB | 198.2 MiB |
| Settled idle after simple + SDK workloads | 321.4 MiB | 200.4 MiB |
| Historical peak through those workloads | 371.8 MiB | 242.6 MiB |

The slimmer worker measured 197.1 MiB before any workload. Settled idle fell
121 MiB, approximately **38%**. It is still about 210 MB in decimal units,
roughly twice the desired 100 MB target.

Settled process PSS attribution (shared pages apportioned, not double-counted):

| Process | Baseline | Slimmer worker |
| --- | ---: | ---: |
| Supervisor | 164.7 MiB | 133.0 MiB |
| Execution template | 139.8 MiB | 50.5 MiB |
| Multiprocessing tracker | 12.2 MiB | 12.3 MiB |

These process figures do not sum exactly to the container working set because
kernel memory, accounting and the measurement process differ.

Each timing sample below contains 100 successful sequential jobs, with both
Redis result receipt and durable status checked. Both images have the queue fix.

| Workload | Baseline p50 / p95 / p99 | Slimmer p50 / p95 / p99 |
| --- | --- | --- |
| Simple inline | 44.7 / 56.3 / 63.0 ms | 36.2 / 48.7 / 55.6 ms |
| Real organizations SDK call | 68.6 / 89.8 / 98.7 ms | 89.1 / 201.8 / 267.0 ms |

Simple execution improved in this sample, while SDK execution was slower and
more variable. This is not evidence of performance parity: one local sample per
image cannot isolate changes from host/API/database load. Database-recorded execution duration also rose (SDK p50 38 → 54 ms;
p95 48 → 97 ms), so producer overhead alone does not explain it. The cause
was not isolated by this sample. The user accepted that small local timing
differences require broader evidence before treating them as a regression. Common
SDK models and client code remain preloaded, so the reduced idle footprint is
not achieved by rebuilding those modules for each job. The pending queue had
zero entries after both clean comparisons.

## Remaining work toward 100 MB

The supervisor still owns database persistence, broker consumers, package
management and coordination. The template keeps Python and common execution/SDK
libraries warm. Reaching 100 MB is not demonstrated by this pass.

The follow-up candidate identified after the first pass was running package installation and package-status
inspection in a short-lived helper so their storage/client imports do not stay
in the supervisor. The helper would exit before readiness; this was subsequently implemented and measured in the supervisor pass below. Larger reductions likely require narrowing the
supervisor's responsibilities and validating its persistence and lifecycle
contracts. Do not lower production memory limits to the idle figures: active
jobs and user dependencies add memory.

## Verification

Passed on the final runtime source (before the isolated harness rendering change):

```bash
./test.sh tests/unit/execution/test_template_import_boundary.py tests/unit/test_model_import_boundaries.py tests/unit/test_repository_import_boundaries.py tests/unit/test_sdk_import_boundaries.py tests/unit/sdk tests/unit/core/test_module_cache_import_boundary.py tests/unit/execution/test_process_pool.py tests/unit/execution/test_template_process.py tests/unit/test_sdk_package.py tests/unit/test_sdk_package_fingerprint.py tests/unit/test_contract_version.py tests/unit/test_import_hygiene.py tests/e2e/platform/test_fork_pool.py tests/e2e/security/test_child_env_isolation.py -q
# 210 passed, including 8 live E2E tests; before the separate inline queue fix.

./test.sh tests/unit/services/execution/test_async_executor.py tests/unit/execution/test_template_import_boundary.py tests/unit/test_elastic_runtime_spike.py tests/unit/test_elastic_runtime_memory.py tests/unit/test_kubernetes_spike_manifests.py -q
# 22 passed after the inline queue fix, before the harness rendering change.

./test.sh quality api
# 0 type errors/warnings; ruff passed after final runtime and queue changes.
```

An earlier wider targeted run passed 291 tests covering the module-cache,
requirements-cache, virtual-import fallback, worker startup and consumer-session
boundaries as well. The final common-SDK template preloads were then covered by
the 210-test run above. Python 3.11 standalone SDK import/public-identity checks
passed in `python:3.11-slim`. Production Docker builds passed.

Full backend, browser and pre-PR suites were not run for this memory pass. No PR
or commit was created. API/SDK import contracts and representative live paths
were exercised; this does not establish compatibility with every customer
workflow/dependency combination.

The local harness had a separate double-rollout defect: it first applied a new
image and then patched the revision annotation, creating two pod templates. The
first baseline rollout left an idle terminating pod that required a second
SIGTERM in the disposable cluster. The harness now renders image and revision
in one apply. Its renderer test parses actual output and checks all three
Deployments, including an all-numeric revision remaining a YAML string. An
obsolete source-text assertion failed after this change and was replaced by
that output-level contract; it was not a worker runtime failure.

## Final live checks and artifacts

All passed against `bifrost-elastic-spike:769-memory-final`:

```bash
./test.sh kubernetes experiment warm --output /tmp/bifrost-769-clean-final-warm
./test.sh kubernetes experiment sdk --output /tmp/bifrost-769-clean-final-sdk
./test.sh kubernetes experiment drain --output /tmp/bifrost-769-clean-final-drain
./test.sh kubernetes experiment deadline --output /tmp/bifrost-769-clean-final-deadline
./test.sh kubernetes experiment load --output /tmp/bifrost-769-clean-final-load
./test.sh kubernetes experiment cold --output /tmp/bifrost-769-clean-final-cold
./test.sh tests/unit/test_kubernetes_spike_manifests.py -q
# 8 passed, including actual renderer output.
bash -n scripts/kubernetes/local-kind.sh
git diff --check
```

- Graceful removal: a 15-second active execution returned durable Success.
- One-second drain deadline: expected durable `WorkerShutdown`, received at 1.41 s.
- Load: 96/96 four-second executions completed across three distinct workers, with
  four ready replicas reached;
  historical per-container peaks ranged from 220.3 to 316.6 MiB. Idle memory is
  not an active-workload memory budget.
- Zero-worker activation: successful durable result in 5.15 s (one sample).
- No production cluster or customer data was involved. The local cluster remains
  available with the final image; KEDA retains operator-configured replica ownership.

Artifacts are local `/tmp` files and may be removed by host cleanup. Baseline
counterparts are `/tmp/bifrost-769-clean-baseline-warm` and
`/tmp/bifrost-769-clean-baseline-sdk`. Settled process measurements are
`/tmp/bifrost-769-clean-baseline-idle.json` and
`/tmp/bifrost-769-clean-final-idle.json`; initial slim measurement is
`/tmp/bifrost-769-clean-final-startup.json`.

Image IDs:

- Baseline plus queue fix: `sha256:aca3a445e86cc46a3a674494b4e39d2011495608b59070392b7a5f6801f533bc`
- Final runtime: `sha256:227ae10f4240f5affe829dcbd777bc684a20e1938dc1f6720e15e6be3118f466`

The changes are uncommitted and are **not declared release-ready**. The SDK
latency difference warrants further measurement; it is not, by itself, proof of
a regression or an automatic release blocker. Full pre-PR verification is
also outstanding. The 100 MB target remains unmet.

## Supervisor startup isolation pass

Implemented in the same worktree after the user approved pursuing the measured
supervisor overhead:

- Requirements installation and requirements-status inspection now run in a
  short-lived helper process before template startup or recycle. The helper
  exits, leaving installed packages on the same filesystem. The parent retains
  the small structured result and publishes existing package-failure notices.
- The helper uses a strict JSON result contract. A failed helper cannot silently
  become an empty successful setup. Cancellation signals the complete process
  group and reaps the helper; remaining descendants are killed even if the group
  leader has already exited.
- The database solution write guard no longer imports FastAPI until an HTTP
  guard needs to raise its existing 409 error. ORM protection remains installed.
- Review exposed a pre-existing package-consumer bug: failed template restarts
  could be swallowed and reported as recycled. Failure now propagates to the
  existing progress reporter, which reports failed and never reports recycled.
  This consumer path does not call the new helper; the fix is a separate bounded
  correction discovered during review.

No worker roles, persistence protocol, database schema, SDK preload policy or
customer Settings option changed. The shared 95-table ORM registry remains:
agent and workflow consumers use it, and relationships cross domain boundaries.
Removing it safely would require a separate role/persistence design.

### Verification for the supervisor pass

```bash
./test.sh tests/unit/execution/test_requirements_setup_result.py tests/unit/execution/test_process_pool.py tests/unit/execution/test_simple_worker_install.py tests/unit/execution/test_template_import_boundary.py tests/unit/test_solution_guard_import_boundary.py tests/unit/test_solution_guard.py tests/unit/jobs/test_package_install_consumer.py tests/unit/test_worker_startup_lifecycle.py tests/e2e/platform/test_worker_requirements_setup.py tests/e2e/platform/test_fork_pool.py tests/e2e/security/test_child_env_isolation.py -q
# 101 passed before the bounded package-consumer failure-reporting fix.

./test.sh tests/unit/jobs/test_package_install_consumer.py -q
# Regression demonstration: 2 failed, 10 passed before that fix.

./test.sh tests/unit/jobs/test_package_install_consumer.py tests/unit/execution/test_process_pool.py tests/unit/execution/test_requirements_setup_result.py tests/unit/execution/test_simple_worker_install.py tests/e2e/platform/test_worker_requirements_setup.py -q
# 79 passed on the final source, including the failure-reporting fix.
```

The live helper test builds an offline fixture wheel, serves its requirements
through an isolated Redis logical database, installs into a temporary user site,
and imports it from fresh Python. It also verifies that the calling process
retains no requirements-cache or S3 modules. Fixture state is cleaned up.

### Final supervisor-pass measurements

Image: `bifrost-elastic-spike:769-helper-final`
(`sha256:1a2b38984b21b5f57c68d0869ad9a3a9ac1a3bc210a87d53c5062e3dc88dba0b`).

| Complete worker | Original baseline | First import pass | Supervisor pass |
| --- | ---: | ---: | ---: |
| Settled idle after simple + SDK jobs | 321.4 MiB | 200.4 MiB | 167.0 MiB |

The supervisor pass reduces settled idle another 33.4 MiB (17% from the first
pass), for a total reduction of 154.4 MiB (48% from the original baseline).
Before workloads it measured 162.8 MiB. Settled process PSS: supervisor 101.7 MiB,
template 50.4 MiB, tracker 12.2 MiB. Historical container peak through the
sequential workloads was 211.6 MiB; these are not production memory limits.
The complete worker is still above the user's 100 MB goal.

With builds, test runs and quality checking complete before the timing samples:

| 100 successful sequential jobs | Original baseline p50 / p95 / p99 | Supervisor pass p50 / p95 / p99 |
| --- | --- | --- |
| Simple inline | 44.7 / 56.3 / 63.0 ms | 39.0 / 55.0 / 62.0 ms |
| Organizations SDK call | 68.6 / 89.8 / 98.7 ms | 67.3 / 86.6 / 102.2 ms |

SDK timings are close to the original sample. This does not prove the cause of
the first-pass difference, but does not support a persistent SDK slowdown in
this sample either. Keep all samples; no stable production latency bound is
claimed. The pending queue contained zero entries after the workloads.

Final live checks:

- Graceful deletion preserved Success for the active 15-second job.
- The one-second deadline returned the expected durable WorkerShutdown at 1.45 s.
- Activation from zero workers completed successfully at 9.32 s. This single
  sample includes scaler polling/scheduling and is not a startup regression
  estimate against the earlier 5.15 s sample.

```bash
./test.sh quality api
# Passed: 0 type errors/warnings; all lint checks passed on final source.
./test.sh kubernetes experiment drain --output /tmp/bifrost-769-helper-final-drain
./test.sh kubernetes experiment deadline --output /tmp/bifrost-769-helper-final-deadline
./test.sh kubernetes experiment cold --output /tmp/bifrost-769-helper-final-cold
./test.sh kubernetes experiment warm --output /tmp/bifrost-769-helper-final-warm
./test.sh kubernetes experiment sdk --output /tmp/bifrost-769-helper-final-sdk
git diff --check
```

Settled and initial snapshots:
`/tmp/bifrost-769-helper-final-idle.json` and
`/tmp/bifrost-769-helper-final-startup.json`. Test and quality logs:
`/tmp/bifrost-769-helper-tests.log`,
`/tmp/bifrost-769-helper-final-tests.log`,
`/tmp/bifrost-769-helper-final-quality.log`.

The local cluster remains running with the final image. No changes were
committed, published or deployed outside the disposable environment. Full
backend/browser suites and the pre-PR gate were not run for this pass.

Final load check also passed:

```bash
./test.sh kubernetes experiment load --output /tmp/bifrost-769-helper-final-load
```

96/96 jobs completed across 4 distinct workers. Historical per-container
peaks ranged from 284.8 to 287.6 MiB. KEDA reached multiple ready
replicas, and the harness restored the configured scaling policy afterward.


## Final launcher and summary-import pass

The template now starts through a clean Python subprocess rather than
`multiprocessing.Process`. The old launch mechanism started a separate
approximately 12 MiB resource tracker, although the execution pipes did not
need it. The new launcher passes only its private control descriptor and sends
the multiprocessing authentication key over that pipe, preserving authenticated
descriptor transfer. It restores the previous spawn mode and process name,
keeps common SDK preloads, and still forks a fresh isolated child per execution.
Startup failure and shutdown explicitly close descriptors and reap processes.

Summary and tuning services now load when their respective messages arrive.
This is a deferred cost: a worker processing these jobs retains their modules
thereafter. It does not move common SDK initialization into each workflow.
Existing handler signatures, queue names, and failure behavior are preserved.

A forced full garbage collection produced no measurable supervisor saving;
removing the pool's notification imports also produced no measurable saving.
Neither speculative change was retained. Further substantial cuts require a
separate design for database ownership or worker responsibilities, with its own
persistence and lifecycle validation. The current shared ORM registry and warm
execution template are intentional remaining costs.

### Verification for the final launcher/import pass

```bash
./test.sh tests/unit/execution/test_template_process.py tests/unit/execution/test_template_import_boundary.py tests/unit/execution/test_process_pool.py tests/unit/test_import_hygiene.py tests/unit/test_worker_startup_signal.py tests/unit/test_worker_startup_lifecycle.py tests/unit/test_summarize_worker_import_boundary.py tests/unit/test_worker_message_types.py tests/unit/test_run_summarizer.py tests/unit/test_tuning_service.py tests/e2e/platform/test_fork_pool.py tests/e2e/security/test_child_env_isolation.py -q
# 122 passed; JUnit: 0 failures, 0 errors, 0 skipped.
```

The initial run had one test failure: an intentionally dead child reset its pipe
rather than returning EOF. The startup-failure test now accepts the concrete
closed-pipe outcomes while still verifying descriptor cleanup and wrapper reuse.
Shutdown also gained an explicit pipe close, tested through a retained reference.
The real fresh-interpreter test verifies no resource tracker after startup, fork,
and shutdown. Eight live E2E tests cover fork execution and credential isolation.

The first quality run passed type checking but caught a missing explicit
re-export alias for a queue constant. The alias was added, preserving the public
import while satisfying lint. Logs for this pass are
`/tmp/bifrost-769-lean-final-tests.log` and
`/tmp/bifrost-769-lean-final-quality.log`.

Final production image: `bifrost-elastic-spike:769-lean-verified`,
`sha256:858741235e60178f9848bfa6a4c0d2ee986f77b5e557c948bcf5df36a19a3100`.
The build and local deployment logs are
`/tmp/bifrost-769-lean-verified-build.log` and
`/tmp/bifrost-769-lean-verified-up.log`.


### Final measurements and stopping point

| Complete worker, settled after simple + SDK jobs | MiB |
| --- | ---: |
| Original baseline | 321.4 |
| First import pass | 200.4 |
| Supervisor helper pass | 167.0 |
| Final launcher/import pass | **155.0** |

Total reduction: 166.4 MiB (52%). The final step saved 12.0 MiB after workloads,
mostly by removing the tracker. Before jobs, two startup snapshots measured
148.2–149.2 MiB; two others included a concurrent readiness probe and measured
161.4–162.5 MiB. All four snapshots are retained, rather than silently dropping
probe overhead. The recurring readiness probe starts a short-lived Python
process; it is not another retained worker process.

After workloads, process PSS was supervisor 100.6 MiB plus warm template
50.6 MiB, with no tracker. Historical whole-container peak through sequential
workloads was 199.4 MiB. These figures are not recommended production limits.

The deferred summary/tuning import probe saved about 6.4 MiB at construction,
but that entire saving does not survive real workflow traffic: other paths also
load shared dependencies. A real summary-queue message for a nonexistent run
was acknowledged successfully and the settled container then measured 155.6 MiB.
That fixture exits before contacting an LLM; it proves activation of the lazy
handler, not a full provider-backed summary. Tuning and summary behavior are
also covered by the targeted tests. This is deferred initialization, not a
promise that every worker role permanently saves another 6.4 MiB.

Builds, targeted tests, and quality checks finished before these timing samples:

| 100 successful sequential jobs | Original p50 / p95 / p99 | Final p50 / p95 / p99 |
| --- | --- | --- |
| Simple inline | 44.7 / 56.3 / 63.0 ms | 39.2 / 47.8 / 49.8 ms |
| Organizations SDK call | 68.6 / 89.8 / 98.7 ms | 73.4 / 116.4 / 149.6 ms |

The SDK median is close; its tail is higher in this sample. The preceding
supervisor sample had 67.3 / 86.6 / 102.2 ms. Local samples vary and do not
establish a causal slowdown or a stable latency guarantee. No retry or chosen
best-of run was used to obtain these final samples. The UI pending queue was
empty afterward.

Graceful deletion completed the running 15-second job successfully. A one-second
drain deadline produced the expected durable WorkerShutdown failure at 1.41 s.
Activation from zero completed at 8.86 s, including scaler polling/scheduling.

```bash
./test.sh quality api
# Passed: 0 type errors/warnings; all lint checks passed.
./test.sh kubernetes experiment drain --output /tmp/bifrost-769-lean-drain
./test.sh kubernetes experiment deadline --output /tmp/bifrost-769-lean-deadline
./test.sh kubernetes experiment cold --output /tmp/bifrost-769-lean-cold
./test.sh kubernetes experiment warm --output /tmp/bifrost-769-lean-warm
./test.sh kubernetes experiment sdk --output /tmp/bifrost-769-lean-sdk
```

Memory snapshots: `/tmp/bifrost-769-lean-startup.json`,
`/tmp/bifrost-769-lean-idle.json`, and
`/tmp/bifrost-769-lean-summary-idle.json`.


Final load check passed: 96 concurrent submissions completed across four
worker pods, with four ready replicas observed. Per-container historical peaks
were 233.9–276.8 MiB. The UI pending queue remained empty.

```bash
./test.sh kubernetes experiment load --output /tmp/bifrost-769-lean-load
git diff --check
```

This is the reasonable endpoint for the current cleanup: approximately 163 MB
(decimal) whole-worker idle, preserving existing roles, warm SDK execution,
isolation and database persistence. A credible path toward 100 MB requires a
separate architectural change, not another unmeasured import shuffle. Updating
Bifrost delivers these memory changes without a customer configuration step;
Kubernetes autoscaling setup remains a separate deployment concern documented
in the local spike runbook.

All work remains uncommitted in the existing worktree. Full backend, frontend,
browser, and pre-PR suites were not run for this pass. No known failures remain
in the selected checks. Nothing was published or deployed outside local Kind.
