# External Capacity Cleanup Acceptance

Thomas Bray's contribution in [#1160](https://github.com/gobifrost/bifrost/pull/1160)
adds external physical-worker capacity through the existing admission and execution
system. His follow-up candidate `6cc0d8693d7ffa9ea90e07930da07a6824441651`
retains external resource cleanup until deletion is confirmed, preserves pending
records through registry pruning, and handles delayed unbound preparations.
The original commits remain in the replacement branch's history. The replacement
is necessary because the company fork grants this maintainer read access only.

## Test Isolation Repair

The first local review run passed 192 of 193 focused tests. The failure also
reproduced alone: a startup `workspace.file_index_reconcile` job remained
`running` when the isolated unit harness stopped its executors. The worker-pool
fixture removed only queued platform jobs. The capacity planner correctly refused
to provision another worker for the same job type's occupied global concurrency
slot. PostgreSQL readback confirmed the stale active startup job.

The fixture now clears the complete platform-job table alongside its existing
dispatch and worker reset. This is confined to isolated pool tests; production
concurrency and lifecycle behavior are unchanged. Scheduler-diagnostic foreign
keys use `ON DELETE SET NULL`.

The existing platform-job demand test now seeds either a `running` or
`cancel_requested` startup job, performs the fixture reset, and verifies that
fresh queued work launches capacity. Both cases failed with the old queued-only
cleanup and pass with the repair. No assertion was weakened, and no retries,
skips, or timeout increases were added. An independent bounded review found no
actionable issue in the fixture repair.

## Verification

The branch includes upstream `6dde44b158e4de938db34c2cf129b0d12dacc4d2`.
Tests ran through the isolated Docker stack, with synthetic provider HTTP and
real PostgreSQL/Redis for capacity and cleanup boundaries.

```bash
./test.sh stack up
./test.sh tests/unit/services/worker_pool/test_worker_pool.py::test_a_platform_job_waiting_for_memory_launches_a_worker -q
```

Before the fixture repair: both seeded active-state cases failed as expected.

```bash
./test.sh tests/unit/services/worker_pool/test_external_capacity.py tests/unit/services/worker_pool/test_worker_pool.py tests/unit/services/worker_pool/test_external_policy.py tests/unit/services/worker_pool/test_platform_job_capacity.py tests/unit/services/worker_pool/test_worker_capacity_domains.py tests/unit/services/execution/test_summary_dispatch.py tests/unit/services/execution/test_backfill_tracker.py tests/unit/test_worker_network.py tests/unit/test_service_lifecycle.py tests/unit/services/test_git_repo_manager.py tests/unit/test_contract_version.py tests/unit/test_dto_flags.py -q
```

After the repair: **341 passed**, zero failures, errors, or skips, in 15.51 seconds.
This includes the prolonged provider outage, controller restart, pending-record
retention, delayed preparation, physical handler capacity, private transport,
summary/backfill ownership, service lifecycle, Git workspace, and DTO/CLI checks.

```bash
./test.sh tests/e2e/platform/test_agent_runtime_boundary.py tests/e2e/platform/test_service_worker_mode.py -q --durations=0
```

**4 passed**, zero failures, errors, or skips, in 55.88 seconds. These exercise real
agent dispatch/automatic summaries and supervised-service lifecycle/recovery.
The healthy service case spent 44.15 seconds in its running lifecycle, with 0.21
seconds of setup; the crash/recovery case spent 4.08 seconds in its lifecycle.
These are existing execution-boundary tests, not additional deployment jobs.

```bash
./test.sh client unit -- --run src/pages/Settings.test.tsx src/pages/settings/KubernetesSettings.test.tsx
(cd client && npm run tsc)
(cd client && npm run lint)
BIFROST_SKIP_BUILD=1 ./test.sh quality api
```

The two frontend files passed all **23 tests**. TypeScript passed. ESLint passed
with the existing console warning in `e2e/support/seed-review-pack.ts`.
The quality command reuses this worktree's built test image; source is mounted
from this worktree. **API Pyright and Ruff passed**, with zero type errors or
warnings. `git diff --check` also passed.

Manual review covered authenticated HTTPS provider requests without redirects,
immutable reservation/resource identity, terminal-state cleanup receipts,
non-destructive migration and retention behavior, physical handler admission,
and summary claim fencing. The cleanup delta introduces no credential or
organization access path. The upgrade notice retains the requirement to upgrade
physical launchers because APIs no longer execute summaries.

The complete backend/frontend/browser suites and a deployed-provider outage
rehearsal were not run locally. The merge queue owns complete-suite acceptance.
Thomas's documented provider/runtime-family qualification limits remain; this
repair does not claim a production deployment or new provider qualification.

## CodeQL Review Follow-Up

CodeQL's `py/ineffectual-statement` query reported the delayed-preparation test's
`await pending` cleanup statement on the first replacement candidate. The task
join is necessary: preparation must finish before the test inspects its resource.
The test now expresses that join as `await asyncio.gather(pending)`, preserving
completion and exception propagation while making the effect explicit to the
analyzer. Auto-merge was disabled while addressing the comment.

```bash
./test.sh tests/unit/services/worker_pool/test_external_capacity.py tests/unit/services/worker_pool/test_worker_pool.py -q
```

All **128 tests passed** in 10.89 seconds, including delayed preparation, cleanup,
and both active-startup fixture regressions. This is test code only; the prior
341-test, four live-runtime, and frontend checks cover unchanged product source.

## Queue-Exposed AgentIndexer Transaction Isolation

Queue run [41339255384](https://github.com/gobifrost/bifrost/actions/runs/41339255384)
passed its unit suites, lint/types, image builds, browser smoke and two backend
E2E shards. Shard 3 found an additional fixture leak: the three AgentIndexer
database tests left `test_wf_tools_alias` and `test_wf_tool_ids` workflow rows
without workspace source. The later native overlap Sync correctly stopped for
deletion confirmation. The deletion guard remains unchanged.

`AgentIndexer` only reads and writes through its supplied session. These tests
flush but never commit. Their autouse `cleanup_agents` fixture deleted only part
of the test graph and then committed, accidentally persisting the workflows.
Removing that fixture restores the standard `db_session` rollback, which owns
the agents, junctions, profiles, providers and workflows as one transaction.
There is no external storage or independent-session side effect to clean up.
Independent review confirmed that transaction boundary.

```bash
./test.sh tests/e2e/platform/test_agent_indexer.py tests/e2e/platform/test_workspace_sync_execution_overlap.py -q --durations=0
```

Before the repair: **three passed, one failed** in 18.77 seconds, reproducing the
same two pending workflow deletions as CI. The overlap test alone passed after
a fresh reset (**one passed** in 16.21 seconds), confirming the exposing test
order rather than dismissing the failure as intermittent. No retries, skips,
timeout increases or delete confirmations were added.

After removing the committing cleanup fixture, the same ordered invocation
passed all **four tests** in 15.34 seconds. The overlap case continues to run
the real Fetch, Sync and worker-import boundaries with delete confirmation off.
An independent PostgreSQL readback found zero owned workflow, agent, profile
and provider rows afterward. Dockerized API Pyright and Ruff passed again.

## Current-Main Integration

Merged `c167b594d` (#1171) without conflicts, preserving Thomas's commits and
the fixture repairs. Repeated the 341-test focused backend command above:
**341 passed** in 18.69 seconds, with no failures, errors or skips. TypeScript
and ESLint passed again, with the same existing seed-helper warning.

```bash
./test.sh tests/e2e/platform/test_agent_indexer.py tests/e2e/platform/test_workspace_sync_execution_overlap.py tests/e2e/platform/test_agent_runtime_boundary.py tests/e2e/platform/test_service_worker_mode.py tests/e2e/api/test_workflow_mutations_service.py::test_execution_only_user_reads_current_workflow_parameters tests/e2e/api/test_workflow_mutations_service.py::test_parameter_read_respects_workflow_sharing tests/e2e/api/test_workflow_mutations_service.py::test_parameter_read_requires_workflow_execute_permission tests/e2e/api/test_workflow_mutations_service.py::test_parameter_read_respects_original_user_organization_reach tests/e2e/api/test_workflow_mutations_service.py::test_parameter_read_respects_solution_inbound_scope -q --durations=0
```

**13 passed** in 79.55 seconds, with no failures, errors or skips. The ordered
indexer/Sync regression, real agent/summary dispatch and service recovery remain
green alongside #1171's workflow parameter authorization boundaries. Existing
service lifecycle and Sync calls account for 45.11 and 12.27 seconds; these are
live process boundaries, with no additional deployment tests introduced.

`BIFROST_SKIP_BUILD=1 ./test.sh quality api` passed on the integrated tree:
Pyright reported zero errors/warnings and Ruff was clean. Broader local suites
remain unrun; the native merge queue is the complete-suite gate.

## Landed Acceptance And Feed Activation

PR #1172 landed as `13f807f4dfac7b0e68ed6f5de2e0544efd25a578` after all ten
jobs in [queue run 41343135108](https://github.com/gobifrost/bifrost/actions/runs/41343135108)
passed, including all three backend E2E shards and browser smoke. GitHub's
commit-author metadata recognizes `MTG-Thomas` as a co-author on that exact
landed commit. Original PR #1160 is closed as superseded, retaining its body.
This source acceptance does not broaden the documented provider qualification.

The activation refresh retains the cumulative inventory and verifies Thomas's
landed co-author identity for both #1170 and #1172 before recording their
contribution evidence. The prepared image feed includes the approved external
capacity highlight with Thomas as its sole visible contributor, its required
launcher upgrade notice and the verified Workers Settings link. All 36 prior
announcements and all 313 prior PR records remain intact; the new bundle has
38 announcements and the refreshed inventory has 317 PRs.

```bash
python3 scripts/product_updates.py validate --content-dir product-updates --inventory product-updates/inventory.json --dispositions product-updates/dispositions.json --target 13f807f4dfac7b0e68ed6f5de2e0544efd25a578
python3 -m scripts.prepare_product_updates_image --target 13f807f4dfac7b0e68ed6f5de2e0544efd25a578
python3 -m unittest scripts.test_product_updates
```

Validation and image preparation passed; all 44 toolchain tests passed.
Generated packaging output is excluded from this source change. Backend and
browser suites were not repeated locally for the metadata-only activation;
its merge queue remains the complete-suite gate. No formal release is cut.
