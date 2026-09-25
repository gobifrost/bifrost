# User SDK shared service

Executor: OpenCode. Reviewer: Codex. Work only in this worktree. Do not
commit, push, merge, or touch the primary checkout.

Extract the business behavior of the five fixed Python SDK user operations
(`list`, `create`, `get`, `update`, `delete`) from
`api/src/routers/users.py` into a shared application service under
`api/shared/`. The HTTP handlers must call that same service, preserving
their exact DTOs, query parameters, status/error precedence, pagination,
invite creation and event, audit, role changes, self/system protection, and
transaction behavior. The service takes trusted explicit session and actor
arguments, never Request, JWT, or child claims. Keep existing authorization
for HTTP (`CurrentSuperuser`). The future local dispatcher will enforce
token-equivalent superuser authority before calling it. For SDK list, note
the existing `org_id` versus HTTP `scope` query mismatch; preserve current
HTTP behavior in this extraction and document the mismatch for a joint
HTTP/local correction stage.

Move only helpers exclusively needed by these five operations. Do not change
invite-only endpoints, bulk operations, roles, organizations, any local
transport or execution dispatcher, or the public Python SDK yet. Keep the
router thin for the five operations and avoid a duplicate SQL path.

Add focused service tests for successful behavior and permission-sensitive
errors, and preserve HTTP endpoint coverage. Run relevant `./test.sh` unit
and E2E tests plus `./test.sh quality api`. Report exact commands/results;
diagnose failures without blind reruns, retries, skips, or timeout increases.
