# Role SDK shared service

Executor: OpenCode. Reviewer: Codex. Work only in this worktree. Do not commit,
push, merge, or touch primary checkout.

Extract the business behavior used by `api/bifrost/roles.py` from the matching
HTTP endpoints in `api/src/routers/roles.py` into a shared service callable by
both HTTP handlers and a future engine-local dispatcher. Scope: SDK methods
`create`, `get`, `list`, `update`, `delete`, `list_users`, `list_forms`,
`assign_users`, `assign_forms`. Preserve current DTOs, response fields, HTTP
statuses/error precedence, permission and org checks, side effects, audit,
cache invalidation, and assignment transaction behavior. The SDK's assignment
facades may be composite; identify the exact HTTP calls each makes before
extracting. Do not change MCP tools, which remain thin HTTP wrappers.

Put operation logic in `api/shared/sdk_roles.py` (or an existing appropriate
shared module), with explicit trusted DB/principal/context parameters rather
than HTTP request objects. Keep route handlers thin. Do not add the local
transport in this stage. This is deliberately limited to the nine methods
above; leave other role endpoints alone except reusable helpers they share.

Add focused service unit tests and adjust router tests to mock the new
boundary. Run relevant existing role unit/E2E tests via `./test.sh`, DTO and
contract tripwires if a DTO changes, and `./test.sh quality api`. Report
exact commands/results and any failures. Do not use retries/skips/xfail to
mask issues. Do not edit local dispatcher, import transport, process pool,
forms, tables, or other domains.
