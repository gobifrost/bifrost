# Worker Memory Reduction Implementation Plan

> **For agentic workers:** Use subagent-driven-development for disjoint import boundaries; root owns integration, lifecycle changes, and live measurements.

**Goal:** Reduce the complete idle worker from approximately 318 MiB toward the user's 100 MB target before user code/dependencies, while retaining isolation, SDK behavior, graceful shutdown and warm performance.

**Architecture:** First remove eager model and SDK imports from process boundaries. Preserve explicit complete ORM registration for database consumers. Then trim template preloading using measured import closures; retain the single-threaded template and fresh per-execution children. Any remaining gap is measured rather than hidden by moving work elsewhere or changing measurement definitions.

**Tech Stack:** Python, SQLAlchemy, Pydantic, existing process pool, Docker, local Kind/KEDA harness.

## Work and ownership

- [x] Model-boundary agent: fresh-interpreter regression tests in `api/tests/unit/test_model_import_boundaries.py`; demand-loaded public exports in `api/src/models/__init__.py` and `api/src/models/contracts/__init__.py`; preserve explicit ORM registration and API exports.
- [x] SDK-boundary agent: fresh-interpreter regression tests in `api/tests/unit/test_sdk_import_boundaries.py`; demand-loaded SDK exports preserving singleton/type identities in `api/bifrost/__init__.py` and model definitions only if needed.
- [x] Runtime review agent: identify remaining template/supervisor dependency costs; root handles `api/src/services/execution/template_process.py` and any independently demonstrated eager imports with regression tests first.
- [x] Root serializes `./test.sh` invocations: demonstrate failing boundary tests, then targeted import/model/SDK/pool/startup tests; run Python quality and CLI contract tripwires if model exposure changes.
- [x] Rebuild production image, run the same local Kubernetes warm/drain/deadline experiments, record actual cgroup working set and process attribution. Exercise a real SDK call as well as inline jobs to detect merely deferred initialization errors.
- [x] Record before/after measurements, latency comparison, regression evidence, and remaining blockers to the 100 MB target in the results runbook. Do not claim a production memory ceiling or full-suite validation from scoped tests.

## Measurement contract

Compare the same workload, worker concurrency, image dependencies and cgroup working-set calculation as the spike. Capture startup and post-workload idle figures plus load peak. Preserve the baseline image `bifrost-elastic-spike:769-final` and its artifacts. No customer secrets, production cluster changes, or PR are required for this worktree experiment.

## Outcome and unresolved acceptance criteria

Implementation and local checks are recorded in
[the results runbook](../../runbooks/worker-memory-reduction-results.md).
Settled whole-worker memory fell from 321.4 to 200.4 MiB (38%). The 100 MB target
is not met. SDK latency was slower in that first comparison, so that sample
did not establish warm performance parity. Later measurements and the user's
accepted interpretation are recorded below.
The completed checklist records work performed, not satisfaction of those two
acceptance criteria. No PR or release was prepared.

## Follow-up: supervisor startup isolation

User approved pursuing the measured supervisor costs on 2026-09-15.

- [x] Run requirements installation and status inspection in a temporary helper;
  preserve package results, notifications, heartbeat counts and template ordering.
- [x] Verify helper failure and cancellation reap its subprocess group.
- [x] Exercise actual package installation and workflow execution locally.
- [x] Rebuild and measure complete worker idle/peak memory and SDK timing.
- [x] Assess whether database imports can safely narrow without introducing
  unrequested worker roles or changing persistence guarantees.

The previous single-sample SDK timing difference is a diagnostic concern, not an
automatic release blocker. Preserve the samples and evaluate evidence rather
than requiring a particular millisecond result from another local run.

Supervisor-pass outcome: settled idle 167.0 MiB; original baseline 321.4 MiB.
SDK median/p95 now 67.3/86.6 ms versus original 68.6/89.8 ms in local samples.
Broad ORM narrowing was assessed and deferred because shared relationships and
other consumers require the registry; only the independent FastAPI import was
removed from database-session initialization. Full pre-PR verification remains
outstanding; there is no PR. See the updated results runbook for exact checks.

## Final bounded pass

User asked to take the work as far as reasonable while preserving behavior.

- [x] Evaluate remaining eager imports; retain the simple message-level lazy boundary,
  document its deferred cost, and reject changes without measurable benefit.
- [x] Replace multiprocessing-spawn template launch with a clean Python exec if
  private pipe authentication and descriptor transfer remain correct, removing
  the otherwise-unused resource tracker. Preserve credential scrub and fork
  isolation. No alternate/fallback launcher.
- [x] Exercise actual template lifecycle, descriptor transfer, repeated forks,
  failure cleanup, live credentials/SDK paths and Kubernetes drain/activation.
- [x] Measure whole-container idle/peak and execution timings; document why
  remaining larger cuts would require a separate architecture change.

A full collection in a fresh constructed supervisor reclaimed 35 objects but
changed neither RSS nor PSS; no forced-GC optimization is planned.

Final outcome: 155.0 MiB settled whole-worker memory after 200 simple/SDK jobs
(52% below the original baseline); no resource tracker. A summary-queue fixture
then measured 155.6 MiB. All 122 selected tests and API quality checks pass;
all six final Kubernetes experiments pass, including 96 jobs across four pods.
The full pre-PR gate is not run and no PR is prepared. Stop here: further large
savings require an explicit database/worker-responsibility design. See the
results runbook for exact commands, timing distributions, artifacts and limits.
