# CI reliability and latency analysis

**Date:** 2026-08-15

**Repository:** `gobifrost/bifrost`

**History window:** 2026-06-16 through 2026-08-15 (61 days)

## Decision

Ordinary code pull requests should run lint/type checks, backend unit tests, and
client unit tests. The complete two-shard E2E suite should run once on the
merge queue's synthetic commit. The full suite must continue to run for release
tags and manual full-suite invocations.

This is safe for the shared `:dev` images under the current topology:

1. the active default-branch ruleset has no bypass actors, requires the merge
   queue, and requires checks named `Lint & Type Check`, `Unit Tests`, and `E2E
   Tests`;
2. the merge queue emits `merge_group` for the exact synthetic commit that it
   proposes to place on `main`;
3. all three required checks, including the complete E2E matrix behind the
   aggregate `E2E Tests` check, run on that `merge_group` commit; and
4. `Build Dev Images` can run only for `push` to `refs/heads/main`.

The first implementation changes only the ordinary pull-request E2E behavior.
It does not weaken the merge-group suite, change repository settings, add a
retry, skip a test, increase a timeout, or change any release or deployment
configuration.

## What gates the shared dev image today

The gate is partly repository configuration and partly workflow source. Both
were inspected on 2026-08-15.

| Layer | Current behavior | Evidence |
| --- | --- | --- |
| Branch protection | Legacy protection reports the three checks above with `strict: true` | `gh api repos/gobifrost/bifrost/branches/main/protection` |
| Active ruleset | Default branch requires the same checks and `merge_queue`; no bypass actors are configured | `gh api repos/gobifrost/bifrost/rulesets/15329014` |
| Merge queue | Runs `CI` on `merge_group`, a synthetic ref containing `main` plus queued changes | `.github/workflows/ci.yml` |
| Full tests | Lint/type checks, client unit, backend unit, and both E2E shards run on `merge_group` | `.github/workflows/ci.yml` |
| E2E required check | `test-e2e-gate` reports exactly `E2E Tests` and fails unless the matrix succeeds | `.github/workflows/ci.yml` |
| Image publication | `build-dev` runs only when `event_name == 'push'` and the ref is `refs/heads/main` | `.github/workflows/ci.yml` |
| Dev rollout | `deploy-dev` needs successful `build-dev` | `.github/workflows/ci.yml` |
| Releases | API/client release-image jobs need unit, full E2E aggregate, and lint | `.github/workflows/ci.yml` |

Historical SHA correlation supports the source/configuration proof. All 150
merged PRs returned for the window had an in-window merge-group run whose
`head_sha` exactly equaled the PR's `merge_commit_sha`. The entire CI workflow
was green for 147 of those exact candidates. Three exact candidates had green
required contexts and a failed, non-required `Client Unit Tests` job, so the
ruleset allowed them to merge despite the overall workflow conclusion. Across
the cohort, 12 merged PRs encountered 13 failed queue workflows: nine later had
a wholly green candidate, while those three merged after all currently required
contexts passed.

That client-unit gap does not weaken the user's stated invariant because it is
not part of today's required suite, but it does contradict the stronger claim
that every job in the complete workflow is currently required. The first
implementation closes the gap without changing repository settings: both the
PR and merge-group `E2E Tests` required contexts also depend on successful
client unit tests. The resulting gate is objectively stronger than today's.

The companion `ci-noop.yml` has another feedback-quality caveat. GitHub `paths`
matches when any changed path is included, while `paths-ignore` skips only when
all changed paths are ignored. A mixed code-and-documentation PR can therefore
start both workflows and publish duplicate required-check names; a green no-op
check can visually satisfy a red same-named PR check. This cannot bypass the
current mandatory merge queue because `ci.yml` runs unfiltered on
`merge_group`, but it can hide useful pre-queue feedback. Consolidating path
classification into one workflow is a separate follow-up, not part of the
latency change.

For 136 of 146 eligible merged PRs, the merge SHA also correlated to a
successful `Build Dev Images` job. The median interval from merge to completed
image build was 4.83 minutes (p90 6.28, p95 6.56). The ten missing correlations
were cancelled/in-progress push runs or collection-boundary gaps, not evidence
of an alternate image producer.

The dev-build job has no workflow-level `needs` dependency on tests. Its safety
therefore depends on required checks plus the mandatory merge queue. This report
does not propose changing that relationship. A future architecture could add a
signed test attestation or an explicit cross-workflow provenance gate, but that
is not required for this latency change and is not an immediate recommendation.

## Methodology

The analysis used the GitHub API through `gh`, not a new observation period:

- all runs for `ci.yml` and `ci-noop.yml` created in the 61-day window;
- every job and step for those runs using `filter=all`;
- failed-job logs retained by GitHub Actions;
- all merged PRs in the same period, plus active-window PRs used to associate
  branch runs with PR numbers;
- first-attempt metadata for native GitHub reruns; and
- a stratified sample of 12 successful logs each for unit, E2E shard 1, and E2E
  shard 2 to separate image setup, `test.sh` setup/reset/collection, and pytest.

The bulk JSON and logs were stored under `/tmp/bifrost-ci-history` and were not
added to git. The collection is reproducible with authenticated GitHub CLI
requests such as:

```bash
gh api --paginate \
  'repos/gobifrost/bifrost/actions/workflows/ci.yml/runs?per_page=100'
gh api --paginate \
  'repos/gobifrost/bifrost/actions/workflows/ci-noop.yml/runs?per_page=100'
gh api --paginate \
  'repos/gobifrost/bifrost/actions/runs/<run-id>/jobs?per_page=100&filter=all'
gh run view <run-id> --repo gobifrost/bifrost --job <job-id> --log
gh api --paginate \
  'repos/gobifrost/bifrost/pulls?state=closed&sort=updated&direction=desc&per_page=100'
```

Runs were filtered by `created_at >= 2026-06-16T00:00:00Z`. Durations are
`completed_at - started_at`. Quantiles use linear interpolation between sorted
observations. A PR run was associated by GitHub's linked PR metadata or by the
head branch during the PR lifetime. A merge-group run was associated using its
`gh-readonly-queue/main/pr-<number>-...` branch. Dev image completion was
associated by matching the PR merge SHA to a `push` run and its `Build Dev
Images` job.

Failed pytest nodes were extracted only from canonical `FAILED
path::test_node` summary lines, then counted once per job. Distinct-PR and
distinct-SHA counts prevent one repeatedly updated branch from looking like a
repository-wide problem. Root-cause categories were assigned after inspecting
the highest-frequency logs, current test/product code, and relevant change
history. A later green run was supporting evidence only; it was never itself a
reason to label a failure timing-dependent.

## Run reliability

The dataset contains 737 workflow runs: 600 from `ci.yml` and 137 documentation
no-op runs.

### First-attempt outcomes

Cancelled runs are shown but excluded from the decided-run pass-rate
denominator. `action_required` is a decided non-pass.

| Event/cohort | First-attempt success | Decided runs | Pass rate | Cancelled |
| --- | ---: | ---: | ---: | ---: |
| All pull requests, including documentation no-op | 349 | 426 | 81.9% | 2 |
| Code-bearing pull requests (`ci.yml`) | 212 | 289 | 73.4% | 2 |
| Exact merge candidates (`merge_group`) | 150 | 163 | 92.0% | 0 |

The no-op workflow explains the large gap between all-PR and code-PR pass
rates; it is not evidence that executable changes became more reliable.

### Retries and later-green behavior

Eight workflow run IDs used GitHub's native rerun mechanism and changed from a
non-green first attempt to green: seven PR runs and one tag run. No merge-group
run used a native rerun. At the PR lifecycle level, 32 PRs had a failed PR run
followed by a later successful PR run, usually on a new commit. Nine PRs had a
failed merge-group candidate followed by a later green queue candidate (10
failed candidate runs among those nine).

These counts intentionally distinguish “rerun the same run” from “push a fix”
and “re-form the queue candidate.” Three other exact candidates merged with a
failed overall workflow because only the non-required client unit job failed;
the three required contexts were green. None of these outcomes implies that a
failure was flaky.

### Time from first CI to delivery milestones

The latency cohort includes 146 merged PRs whose creation and first mapped CI
start were inside the window, avoiding left-censored PRs opened earlier.

| Interval | n | Median | p90 | p95 |
| --- | ---: | ---: | ---: | ---: |
| First CI start to merge | 146 | 42.68 min | 64.57 h | 86.63 h |
| First CI start to completed dev image | 136 | 44.15 min | 32.57 h | 86.28 h |
| Merge to completed dev image | 136 | 4.83 min | 6.28 min | 6.56 min |

The long first-CI tails include human review, Dependabot scheduling, author
pauses, and queue timing. They are elapsed delivery time, not runner time and
not solely caused by CI. Seven successful version-tag runs were available;
their median tag-CI-start to `Create Release` completion was 20.67 minutes.
A feature's first CI could not be causally joined to a later deliberately cut
release tag without inventing a mapping, so no first-CI-to-production statistic
is reported.

## Where failures occur

There were 83 failed PR or merge-group workflow runs. Failed-job counts exceed
failed workflows because one run can fail several jobs and the aggregate E2E
gate deliberately fails after a shard failure.

| Failed job/check | Failures |
| --- | ---: |
| E2E aggregate gate | 52 |
| E2E shard 2 | 42 |
| Unit Tests | 33 |
| E2E shard 1 | 24 |
| Lint & Type Check | 23 |
| Client Unit Tests | 21 |
| Build Dev Images | 2 |
| Deploy Dev | 1 |

By event, failed jobs numbered 172 on pull requests, 21 on merge groups, and 5
on pushes. The most frequent failing steps were the E2E aggregate check (43
among PR/MG failed workflows), E2E shard 2's test step (37), the backend unit
test step (27), E2E shard 1's test step (18), Vitest (18), and generated Codex
skill-mirror validation (13).

### Supported primary root-cause categories

Each failed PR/MG workflow was assigned one dominant cause after log review.
Multi-cause runs were counted once here.

| Root-cause category | Failed runs | Evidence pattern |
| --- | ---: | --- |
| Deterministic product, contract, build, or type regression | 53 | Failure follows the changed code and persists until a corrective commit |
| Deterministic generated-artifact drift | 15 | Generator/mirror check names the stale committed artifact |
| Timing, state-boundary, or isolation defect | 10 | Unsupported state transition, leaked cache, or raw clock wait is visible in logs/code |
| Harness authentication-expiry cascade | 3 | Long shard loses the session-scoped admin credential and many later tests return 401 |
| External infrastructure or harness construction | 2 | GitHub cache 504; missing locally expected test image |

No log in the window supported a resource-contention classification such as
disk exhaustion or competing shared services. The runners and E2E stacks are
isolated. One GitHub Actions cache export returned a 504 response, which is an
external infrastructure failure, not evidence that tests need longer timeouts.

## Recurring test nodes

The two breadth columns are as important as raw count. For example, one broken
migration-head assertion failed on every commit across four PRs, while a stale
generated file affected eight distinct PRs.

| Test node | Failures | Distinct PRs | Distinct SHAs | Supported classification |
| --- | ---: | ---: | ---: | --- |
| `test_withdrawn_builder_migrations.py::test_fresh_database_does_not_install_withdrawn_builder_schema` | 14 | 4 | 14 | Overcomplicated/brittle test contract |
| `test_skill_appendix_fresh.py::test_appendices_are_fresh_and_present` | 11 | 8 | 11 | Deterministic generated drift |
| `test_agent_delegation_lifecycle.py::test_chat_executor_receives_durable_child_callback` | 8 | 3 | 8 | Real provider-selection product regression |
| `test_events.py::TestDeliveryRetry::test_cannot_retry_pending_delivery` | 6 | 5 | 6 | Product/test race across delivery state |
| `test_form_execute_rbac.py::test_execute_form_rbac` | 6 | 2 | 6 | Victim of shared auth-expiry cascade |
| `test_two_installs_same_path_resolve_own_workflow_via_app_id` | 4 | 3 | 4 | Redis fixture leaks module cache |
| `test_startup_handle_is_authoritative_bound_and_consumed` | 2 | 2 | 2 | Missing synchronization for Redis-to-PostgreSQL transition |

### Root-cause findings

**Withdrawn builder migrations.** The test asserted an exact Alembic head,
`20260815_optional_agent_limits`, before checking the behavior in its name: that
withdrawn builder tables and roles are absent. A legitimate later
`chat_attachments` migration caused 14 failures until another migration changed
the expected head again. Exact global-head equality is redundant with migration
infrastructure and makes an unrelated migration fail this E2E test. Preserve
the absence assertions and remove the exact-head assertion.

**Generated skill appendix.** Logs explicitly reported `generated/* is stale`
and `STALE: openapi-digest.md`, with the generator command to run. This is a
deterministic authoring error, not a timing failure. Move the cheap freshness
tripwire earlier in local/PR feedback while retaining the authoritative gate.

**Agent delegation lifecycle.** The callback content was empty after `Agent
execution error: LLM provider is not configured`. The later product repair
preserved the OpenRouter provider during runtime initialization. This was a
real regression caught by the complete suite and is direct evidence for keeping
the full merge-group gate.

**Pending delivery retry.** The test reads a delivery, then attempts a retry if
it still appears pending. The worker can transition it to failed between the
GET and retry POST, making the API correctly queue the retry and return 200
instead of the test's expected 400. The test also skips when it observes another
state, so it neither owns nor deterministically establishes its precondition.
Create the pending delivery in a controlled state with the worker disabled, or
move the state-machine contract to a lower-level test, and remove the skip.

**Redis module-cache isolation.** `isolate_redis_module_cache` scans many keys
and calls `await redis.delete(*keys)`, but `RedisClient.delete` accepts one key.
The fixture catches the `TypeError` as best-effort cleanup and continues. Logs
showed `RedisClient.delete() takes 2 positional arguments but 32 were given`
before same-path solution lookup returned 404. Delete each key through the
wrapper (or add a tested bulk API), fail loudly when isolation cannot be
established, and add an order-sensitive regression test.

**Authentication cascade.** Three long July shard runs produced roughly
164–171 downstream failures dominated by `401 Not authenticated`. The early
tests passed; failures spread after the session-scoped platform-admin token
aged during a run exceeding 30 minutes. Refresh or recreate authentication at a
test/module boundary. Extending token lifetime would hide rather than repair
the harness assumption.

**Form embed startup handle.** This is the already repaired example in PR
#566. Async execution returns a pending handle from Redis before the worker has
created the PostgreSQL execution row. The test
`test_startup_handle_is_authoritative_bound_and_consumed` had lost its bounded
poll and immediately requested the row, intermittently receiving 404. The
repair restored a bounded wait for the documented cross-store transition. It
passed locally in 12.53 seconds after the stack was ready; the corresponding
broad PR/queue runs still consumed roughly 25 and 21 minutes.

**WebSocket policy revocation.** Another recurring pattern uses raw
`asyncio.wait_for(ws.recv(), timeout=3.0)` after a policy change. It should
synchronize on the policy-change acknowledgement/event contract and assert
revocation after that causal boundary. Increasing three seconds is not a fix.

## Shard duration, imbalance, and setup cost

| Job | n | Median | p90 | p95 |
| --- | ---: | ---: | ---: | ---: |
| Lint & Type Check | 605 | 2.88 min | 3.17 | 3.25 |
| Unit Tests | 605 | 6.85 min | 8.29 | 8.60 |
| Client Unit Tests | 605 | 2.37 min | 2.83 | 2.88 |
| E2E shard 1 | 467 | 12.12 min | 18.44 | 19.72 |
| E2E shard 2 | 466 | 15.05 min | 21.98 | 23.96 |

The most recent 30 days were slower: shard 1 median/p95 was 14.01/20.79
minutes, and shard 2 was 15.69/24.55. Across 452 paired shard runs, the absolute
duration gap had a median of 3.23 minutes, p90 5.82, and p95 6.56. The user did
not request more sharding, and imbalance is not the first-change target.

The stratified successful-log sample separates costs as follows:

| Job | Median total | Image builds | Test step | Pytest | `test.sh` setup/reset/collection |
| --- | ---: | ---: | ---: | ---: | ---: |
| Unit | 7.43 min | 3.79 | 3.38 | 1.78 | 1.59 |
| E2E shard 1 | 12.59 min | 3.63 | 8.26 | 6.69 | 1.54 |
| E2E shard 2 | 14.51 min | 3.53 | 11.15 | 9.62 | 1.54 |

The sample shows material fixed cost in each runner, but collapsing stacks or
adding shards would change failure isolation and is outside this safe first
step.

### Longest-test remediation implemented on 2026-08-15

The first duration pass targeted repeated work and artificial waits, not lower
timeouts or fewer assertions. Exact local timings used the canonical Docker
stack and `--durations`; the complete merge-candidate suite remains required.

| Contract | Before | After | Durable disposition | Saved |
| --- | ---: | ---: | --- | ---: |
| MCP unreachable discovery | 10.06 s | 0.03 s | Use a refused loopback port instead of a TEST-NET address that exhausts two five-second HTTP timeouts. The same `httpx` connection-failure path and router response remain covered. | 10.03 s |
| sequential large-file memory bound | 16.94 s | 9.19 s | Keep the original exposing condition (three 4 MiB writes, which retained 300+ MiB before the repair); delete a second five-file loop that asserted the same bounded-memory contract with smaller inputs. | 7.75 s |
| full data restore into a fresh org | 15.68 s | folded into collision lifecycle | Assert the restored row after the collision test's already-required first install, then continue through refusal and wholesale replacement. | 15.68 s |
| package requirements S3/Redis E2Es | 0.32 s + 8.21 s | isolated unit contracts | Remove two extra real `humanize` mutations. A router unit contract proves save-before-broadcast ordering, and the existing `save_requirements` contract proves the S3 plus Redis write-through. One real install/recycle/execution E2E remains and passed in 14.56 s. | 8.53 s |
| full secret restore into an empty slot | 9.10 s | folded into collision lifecycle | Assert the setup slot is set after the collision test's already-required first encrypted install, then continue through refusal and replacement. | 9.10 s |

The directly measured pytest reduction is 51.09 seconds per complete suite.
For the initial three-test benchmark, the selected set fell from 79.07 seconds
to 45.72 seconds while retaining every observable contract. Removing the two
extra package mutations also closes a poisoning mechanism: both old tests
started asynchronous uninstall/recycle work without waiting for the worker
filesystem and process pool to settle before later tests.

CI also used to boot a brand-new empty Compose project and immediately perform
the full state reset before its only suite. A one-shot clean-boot marker now
lets fresh hosted jobs consume that objectively equivalent state once; any
second suite still executes the canonical reset. Local runs continue to reset
by default. This removes the observed roughly 45--60 second redundant restart
from each backend/browser job and is statically guarded in both the harness and
workflow topology tests. Combined with the test reductions, the expected
merge-group savings are about two to three runner-minutes and roughly one
minute on the critical path (the two E2E shards still run in parallel).

Finally, the PR unit job's Codecov step was not measuring coverage: pytest did
not create `coverage.xml`, the action found zero reports, and the step still
reported success. The false upload is removed. Nightly now runs backend unit
tests with explicit `pytest-cov` sources, proves the XML exists, and configures
Codecov to fail on an upload error. This adds no PR or merge-candidate latency;
it establishes a truthful baseline before setting a ratchet. The first complete
local measurement was **57% line coverage** across `src`, `shared`, and
`bifrost` (5,648 passed, three explicit environment skips, 20 slow tests moved
to nightly; 134.25 seconds of pytest). Frontend coverage remains an explicit
follow-up.

## Duplicated work and expected saving

There were 132 merged code PRs with both a successful broad PR run and a
successful merge-group run that could be paired.

- Successful ordinary PR checks consumed about 5,369 runner-minutes (89.5
  runner-hours), followed by about 5,544 runner-minutes (92.4 runner-hours) on
  their successful merge candidates.
- The ordinary PR E2E shards alone consumed 3,706.8 runner-minutes (61.8
  runner-hours), with a median of 26.98 runner-minutes per paired PR and p90
  37.65.
- Lint, backend unit, and client unit checks retained on PRs consumed 1,662.2
  runner-minutes (27.7 runner-hours), median 12.35 runner-minutes per PR.
- Removing only ordinary-PR E2E shortens the PR critical path by a projected
  median 8.03 minutes, p90 15.44, and p95 17.10. Across the paired sample it
  removes 1,758 elapsed PR-wait minutes (29.3 hours) before review/queue entry.

These savings do not remove the full-suite queue wait. They remove an earlier
duplicate of that suite while preserving fast defect feedback on every code PR
and the complete exact-candidate decision before `main`.

PR #566 illustrates the present duplication: its successful PR E2E slow shard
took 25.38 minutes, then its successful merge-group slow shard took 20.97
minutes. The eventual main-push dev build took another 4.57 minutes. The first
change removes the first broad E2E run only.

## Ordered reliability backlog

The latency change is not a substitute for fixing tests. The following work is
ordered by failure frequency, breadth, and quality of the available root-cause
evidence.

1. **Make migration withdrawal coverage contract-focused.** Remove the exact
   global Alembic-head assertion; retain the withdrawn-table/role assertions.
   This would have prevented 14 late E2E failures across four PRs without
   losing unique behavior coverage.
2. **Own the pending state in delivery-retry coverage.** Disable the worker or
   test the state machine below HTTP, remove the conditional skip, and prove
   the pending precondition. This addresses six failures across five PRs.
3. **Repair Redis module-cache isolation.** Use the wrapper correctly, stop
   swallowing isolation failure, and add a test that runs conflicting installs
   in both orders. This addresses four observed failures across three PRs and a
   broader silent-leak risk.
4. **Refresh test authentication deterministically.** Replace the aging
   session-scoped token assumption with explicit reauthentication at a stable
   boundary. Validate with the formerly exposing long-shard order. This removes
   three large failure cascades without changing production token policy.
5. **Synchronize WebSocket revocation on a causal event.** Replace the raw
   three-second receive with acknowledgement/event-driven synchronization.
6. **Move generated-artifact feedback earlier.** Run the maintainable generator
   freshness checks as a cheap local/PR tripwire and keep the authoritative CI
   validation. This moves 15 deterministic failed runs earlier; it does not
   relabel or retry them.
7. **Keep product-regression coverage in the full merge-group gate.** Provider
   selection and similar failures are unique signal. Simplify fixtures where
   possible, but do not quarantine this coverage.
8. **Review redundant E2E coverage by contract.** For each slow scenario,
   identify its unique observable contract, keep one happy-path journey, move
   edge cases to unit/component coverage, and delete only demonstrably
   redundant tests. Measure after the fixes before changing shard topology.
9. **Remove duplicate-name ambiguity from documentation-only CI.** Replace the
   two-workflow pseudo-inverse with one authoritative path classification so a
   mixed PR cannot publish both real and no-op versions of a required check.
   Keep `merge_group` unfiltered and full throughout that change.

Eliminating the 10 timing/state/isolation primary failures and three auth
cascades would remove at least 13 of the 83 failed gate episodes in this window
(15.7%). Moving generated drift earlier would improve feedback for another 15
episodes. Those numbers are conservative because one auth cascade can produce
hundreds of misleading failed nodes and because only primary causes were
counted.

## First implementation and invariant checks

The implementation in this change:

- retains lint/type, backend unit, and client unit on ordinary code PRs;
- replaces the ordinary-PR full E2E matrix with a fast check named `E2E Tests`
  that depends on the retained client unit suite, so the existing required
  context continues to report and client-unit failures cannot be bypassed;
- runs the complete unchanged matrix on `merge_group`, release tags, and
  `workflow_dispatch`, with the aggregate required context depending on both
  E2E and client unit success;
- leaves `build-dev`, `deploy-dev`, tag builds, and release dependencies intact;
  and
- adds source-level workflow contract tests covering the mutually exclusive PR
  and merge-group E2E gates, dev-image trigger, release dependencies, and the
  inverse documentation workflow paths/check names.

The source-level test cannot prove mutable GitHub settings. Before merging this
change, the active ruleset must still require the merge queue and the three
named contexts with no bypass actors, and the PR must enter that queue. The
resulting `merge_group` run must execute and pass lint/type, backend unit,
client unit, and both E2E shards. If any condition is absent, this change must
not merge.

### First queue validation incident

The first exact-candidate run exposed two deterministic harness defects. First,
`Critical Browser Smoke` failed during job setup because the pinned SHAs for
`actions/upload-artifact@v7.0.1` and `docker/build-push-action@v7.3.0` each
contained a one-character transcription error. GitHub could not resolve either
action, so the browser test never ran. This was not a Docker failure or a flaky
product test.

More importantly, the workflow had two mutually exclusive jobs named `E2E
Tests`: the real aggregate exact-candidate gate and the ordinary-PR reporting
job. On `merge_group`, GitHub marked the PR-only job skipped and accepted that
duplicate skipped context as satisfying the repository's required `E2E Tests`
name. It merged while both backend shards were still running and while browser
smoke had failed. The main workflow then promoted and rolled out the candidate
before its deploy smoke exposed a separate incorrect Kubernetes service name.
This violated the intended dev-image invariant. The last previously successful
main workflow was deliberately rerun to restore the shared `:dev` tags and
deployments to fully gated commit `f1b519f4e`.

The repair uses the official action tag SHAs everywhere and resolves every
readable version comment in the ordinary PR lint job. A single `E2E Tests` job
now handles both PR reporting and the full exact-candidate result, eliminating
the duplicate skipped context. Main promotion also queries GitHub for the exact
SHA and independently requires one successful instance of every merge-group
job (both backend shards, unit suites, lint, critical browser smoke, candidate
build, and aggregate gate) before it can mutate any shared tag. The deployment
smoke uses the actual `api` and `client` Kubernetes Service names. A new commit
must pass the corrected merge queue; the failed candidate is not rerun.

## Limitations

- GitHub retains current run/job metadata more reliably than every historical
  log; node-level counts include only failed logs available at collection time.
- Workflow behavior evolved during the 61-day window. Aggregate rates describe
  what contributors experienced, not a controlled experiment on one revision.
- Native rerun attempt metadata and later commits/requeues are separate; the
  analysis does not collapse them into one “retry” number.
- PR elapsed-time tails include review and author behavior. Runner and job
  duration metrics are reported separately.
- GitHub-hosted billing multipliers and cache storage cost were not estimated;
  runner-minutes here are wall-clock minutes summed across jobs.
- Release tags are deliberate, so first feature CI cannot be joined to
  production release without product/release metadata that does not exist.
- No branch-protection, merge-queue, release, production, or repository setting
  was mutated during this analysis.
