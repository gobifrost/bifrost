# Quality Execution Authorization Correction

Owner: primary agent. Executor: existing native worker `recorded_api_plan` (worker role).
Worktree: `/home/jack/GitHub/bifrost/.worktrees/durable-agent-platform-backend`.
Baseline: current dirty feature branch; preserve all existing work. No commits or UI changes.

## Defect and approved behavior

Synthetic execution detail, results, cancellation, and batch access currently check only tenant membership. Admission checks agent access; subsequent operations must also enforce current access to the execution's baseline agent. This correction is required before exposing per-operation usage.

- Non-admin access to null-organization Studio records is denied, including callers with null organization.
- Require the execution's original baseline agent to exist and remain accessible for non-admins. Use the execution baseline identity, never mutable suite agent identity. Missing/deleted identities fail closed. Admin historical access remains available.
- Apply the shared check before execution reads, result reads, and cancellation.
- Batch reads and cancellation must authorize every member before revealing an aggregate or cancelling any member. No partial cancellation before authorization completes. Inspect missing membership references and fail closed rather than silently dropping inaccessible members.
- Do not introduce historical tenant-only access as a fallback. Preserve existing admitted shared/global agent access semantics where the tenant-owned execution has a live authorized agent.

## Ownership and tests

Own `api/src/routers/agent_evaluations.py` and `api/tests/e2e/api/test_agent_evaluation_authorization.py`; use a shared module for substantive new business logic if necessary. Notify owner before touching other files. No accounting/reporting source edits.

Reproduce same-tenant private/role-denied reads/results/cancel, deleted baseline, null-org denial, admin historical access, and mixed-authority batch cancellation. Assert denial causes no mutation. Keep real endpoint tests simple with owned fixture cleanup. Run focused authorization and existing evaluation API tests through `./test.sh` when primary releases the test stack. Return diff summary and exact test results; do not declare broad feature completion.

Primary currently owns the test stack for B2/R1 independent verification. Coordinate before running tests. Report through native agent messages; no user notifications.
