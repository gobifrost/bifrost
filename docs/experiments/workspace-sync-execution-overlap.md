# Workspace Sync execution overlap

## Change

A worker with a completely empty Git working directory now loads the persisted checkout before applying the existing storage-fingerprint check. Existing local files are never overwritten by this bootstrap. Stale or untrusted working copies and writes during hydration still fail closed, preserving warnings about unsynced local commits and unfinished merges.

This is two runtime lines in `GitRepoManager.ensure_storage_unchanged`, under the caller's repository lock. It does not implement worker affinity, automatic refetch of stale checkouts, atomic workspace releases, or immutable enqueue-time snapshots.

## Real acceptance scenario

`api/tests/e2e/platform/test_workspace_sync_execution_overlap.py` uses one baseline Fetch, one native Sync, and two workflow executions in the disposable Docker platform:

1. Start A on v1 and wait for its SDK-written table barrier after its eager import.
2. Temporarily gate workflow admission in the disposable DB, enqueue B through HTTP, and prove its durable dispatch exists and its public status is Pending. Git jobs remain eligible.
3. Push v2 to the local bare Git fixture and queue native Sync through HTTP. Poll the actual platform job and compare all three published S3 files against v2 source bytes.
4. Confirm A remains Running and B Pending after publication, release A, restore admission, and inspect both actual execution results.
5. Restore handler admission, cancel active test executions if necessary, and restore fixture resources, config, and storage on failure too.

A pull-only Sync may legitimately omit `commit_sha`, since no commit was pushed. Acceptance therefore checks published bytes rather than relying on that optional push receipt.

## Observed result on the original base

Runtime-fix commit `3f5b08323c1eb080e6f82f2f4afeefffcde3cfb4`, based on upstream `1fab1d0db69ffcc88968cc51b7b3ee097443de96`: **154 tests passed in 63.38 seconds**.

Native Fetch job `341adca9-b51e-433a-9de8-f5294be986b1` and Sync job `9da8215f-6f98-4d3e-8e0b-98666527a137` succeeded on their first attempts. The fixture's v2 commit was `728b4027bd1cff74a2bcefc6698c6c3124579a0a`.

| Requirement | Concrete check | Observed result |
| --- | --- | --- |
| A loads v1 before publication | Real HTTP execution and SDK table barrier | Passed |
| B is queued before publication | Real HTTP enqueue, durable WorkDispatch row, public Pending status | Passed |
| Native Sync publishes v2 | Actual platform job success and byte-exact comparison of three S3 files | Passed |
| Publication overlaps A and B | Public A Running and B Pending after Sync | Passed |
| A retains loaded entry/eager code | Actual result `entry=v1`, `eager=v1` | Passed |
| A late-imports updated code | Actual result `lazy=v2` | Passed |
| B starts on updated code | Actual result `entry=v2`, `eager=v2`, `lazy=v2` | Passed |
| Empty worker can bootstrap | New repository-manager unit test plus native overlap path | Passed |
| Concurrent write during bootstrap is refused | New before/after fingerprint unit test | Passed |
| Nonempty checkout is not overwritten | New unit test asserts no hydration and unchanged local file | Passed |
| Missing persisted Git state is refused | New unit test | Passed |
| Existing safety contracts remain | Entire repository-manager unit file, manifest JIT tests, all local Git tests, real-storage freshness tests, module-isolation tests | Passed |

The real-storage tests separately cover editor writes after Fetch, discard/search-index consistency, credentials staying off disk/storage, disconnect cleanup, merge conflict refusal, and discard after abort. Unit/service tests are additional boundary checks, not substitutes for the native overlap scenario.

## Historical failure and limits

Before the repair, native Fetch succeeded on one container and Sync on another failed because its local baseline was missing. The final pre-repair suite had one failure and ten passes in 32.21 seconds. The regression was preserved, not skipped or retried until favorable worker placement. An initial post-repair run exposed the test's incorrect assumption about optional pushed SHA, which was corrected to verify actual published bytes.

This host's Docker AppArmor profile denies Unix socket creation. Tests use the unchanged canonical Dockerfile and an isolated Compose project with a temporary, uncommitted AppArmor exception. Host policy and staging configuration are unchanged, and harness selectors are restored after validation. No production-hardening or full-global-suite qualification is claimed.

This narrowly repairs fresh-worker bootstrap. A worker with an existing stale checkout still requires explicit Fetch. Distributed ownership of unsynced local commits and unresolved merges is not redesigned. Running functions can still mix loaded v1 code with late-imported v2 modules, as the test demonstrates.

## Current-upstream compatibility

After integrating upstream `16d2e45a5d23639353e40e7fa416b43b990f29c8`, exact merge candidate `ee7ee6baf5d1d7e9ec75e7b0b711b3997647519f` passed **156 tests in 58.88 seconds** with the newly rebuilt, unchanged canonical Dockerfile and current pinned dependencies. Image ID: `sha256:4c75360d299cb030728d90f8db0dd3c68a93f7daf6b3742b97764e00160bf8c7`. Docker Ruff also passed. Runtime source is unchanged after this run. A later test-only review cleanup is validated separately below.

Fetch job `4700fbbc-90ce-45b7-af31-342564d320b5` and Sync job `23a12637-06e5-40c3-a7b9-d85e684c592a` both succeeded on attempt one. All matrix checks above passed again. Fixture commit `4015f78b6491a6a9d847d53a5fc2055f50629693` produced A's `v1/v1/v2` and B's `v2/v2/v2` results. The two additional cases come from current upstream's local Git consumer suite.

Reproduction from the worktree, after building and starting its Docker test stack:

```bash
./test.sh tests/e2e/platform/test_workspace_sync_execution_overlap.py \
  tests/e2e/platform/test_git_sync_storage_freshness.py \
  tests/e2e/platform/test_git_sync_local.py \
  tests/unit/test_solution_module_isolation.py \
  tests/unit/services/test_git_repo_manager.py \
  tests/unit/services/test_manifest_jit.py -v -s
```

The initial published head `fd8614a0a` also passed upstream's full unit job: 10,597 passed, 65 deselected, 2,450 warnings in 364.88 seconds. Lint/type checks, both CodeQL analyses and Product Updates passed. Full E2E shards are reserved for the upstream merge queue, not ordinary PR events.

CodeQL left an inline warning about an implicit lambda return inside `finally`. Test-only follow-up `3b33f092715b6c4898e001b5fdb1ea3ba1b741b7` replaces that polling callback with `functools.partial`, with no exception-handling or product-runtime change. The complete selected suite passed again: **156 tests in 59.13 seconds**, plus Docker Ruff. Fetch `82121271-4fde-4988-bbe9-e600ff1424df` and Sync `cf34826f-fa77-4c40-a748-ba5af8d1ea5d` succeeded on attempt one. Fixture commit `7e65ab65a237c0e705df0baca7c5ef6a1def2665` again returned A=`v1/v1/v2`, B=`v2/v2/v2`. Documentation-only changes follow this exact tested source.

Receipts are local under `/root/.jcode/scratch/migration-20261029/validation/workspace-sync-overlap/repaired/`, including `exact-3f5b08323.log`, `current-upstream.log`, `final-review.log`, their JUnit XML files, and `upstream-unit-job.log`. No staging platform upgrade is part of this proposal.

## Merge-queue fixture isolation repair

The November 1 merge-queue candidate failed the overlap test because earlier
tests committed source-less workflows and left unlinked configs under
soft-deleted integrations. Fetch intentionally omits those records from its
workspace manifest, so Sync correctly requested deletion confirmation instead
of publishing the three fixture files. This was an order-dependent fixture
leak, reproduced with the resource-report test followed by the overlap test
(one passed, one failed).

The repair cleans up records in the fixtures that created them: the two report
workflows and their executions, the cross-org form's committed records, and
integration fixtures with their dependent configs and mappings. Cleanup uses
exact fixture IDs. The external-integration fixture's existing cleanup check
also verifies that config rows do not remain. Production code and
`confirm_deletes=False` remain unchanged.

The original failing order, extended to include the cross-org form test, passed
all three tests in 15.03 seconds after workflow-fixture cleanup.

A broader reproduction then passed 986 tests and exposed a second isolation
problem: Fetch refreshed one executor's checkout, but Sync reached another
executor's nonempty checkout left by an earlier Git test. Its existing
freshness guard correctly refused that stale checkout. The harness now exposes
each executor's separate `/tmp/git` through distinct fixture bind mounts. The
overlap test snapshots, clears, and restores those caches alongside its existing
storage restoration, so either executor can start from the persisted baseline.
It does not share caches between replicas or pin jobs to one executor.
Suite reset also clears both bind-mounted caches through the UID-1000 test
runner after stopping the executors, preserving the fresh-state contract
without depending on the host user's UID.

The preceding native Git-job test followed by the overlap scenario and harness
contracts passed **27 tests in 51.10 seconds**. Reproducing the original failed
CI shard's 114 files, plus the focused Git consumers and harness contracts,
then passed **1,012 tests in 515.96 seconds**, with zero failures, errors, or
skips in JUnit. This includes the overlap test after the leaking fixtures and
earlier native Git jobs.

The reset-script edit was accidentally made while that broad invocation was
running; Bash resumed at an obsolete file offset after pytest and exited 127.
The settled script was verified in a separate invocation: **25 harness tests
passed in 6.51 seconds**, command exit zero, and both replica cache directories
were empty after reset, including removal of hidden sentinel files planted in
each one. The post-pytest shell error was an in-flight harness-edit error, not
a failed test or an accepted intermittent failure.
Dockerized API quality checks also passed: Pyright reported zero errors or
warnings, and Ruff reported all checks passed. Bash syntax and Compose
configuration validation passed.

Validation receipts for this maintainer follow-up are under
`/tmp/review-1140-20261101-*.log` and their matching JUnit XML snapshots. Full
repository-wide acceptance remains the merge queue's responsibility.

The focused exposing sequence and settled reset checks were run with:

```bash
./test.sh tests/e2e/platform/test_thin_platform_jobs_build.py \
  tests/e2e/platform/test_workspace_sync_execution_overlap.py \
  tests/unit/test_compose_test_harness.py \
  tests/unit/test_remote_launcher_deployment_contract.py -v --durations=20
./test.sh tests/unit/test_compose_test_harness.py \
  tests/unit/test_remote_launcher_deployment_contract.py -v
./test.sh quality api
```

The original failed shard's file list is retained at
`/tmp/pr1140-original-shard3.txt`; the broad run added the five Git consumer
files from the earlier reproduction command and the two harness contract files.
Full local backend, client, and browser suites were not run.

The fork rejected the maintainer push because it is organization-owned. The
repair therefore continues in [PR #1170](https://github.com/gobifrost/bifrost/pull/1170),
preserving the original commits from Thomas's
[PR #1140](https://github.com/gobifrost/bifrost/pull/1140) and crediting Thomas Bray
as a co-author in the squash-commit body.
