# Forms and client context engine-local SDK calls

Executor: OpenCode. Reviewer: Codex. Work only in this worktree. Do not commit,
push, merge, or touch the primary checkout. Read AGENTS.md and the aggregate
transport plan first.

Implement parent-local operations for `bifrost.forms.list`, `forms.get`,
`BifrostClient.context`, and its `user`, `organization`, and
`default_parameters` views. External SDK callers retain HTTP. Both HTTP and
local paths must use `api/shared/sdk_forms.py` and `api/shared/sdk_context.py`
without copying access rules into the dispatcher. Derive identity and org
scope only from `LocalDispatchPrincipal` and parent state; never trust child
identity claims. Keep the SDK's current `ValueError`/`PermissionError` mapping
for form get and its `FormPublic` shape, including logo fields.

`BifrostClient.context` is a synchronous property. A normal async SDK channel
call from it can deadlock a running child event loop, so use the existing
independent synchronous pipe (currently used for import misses) or a clearly
equivalent separate sync channel. If extending that pipe, give context an
explicit named allowlisted operation, preserve import safety, bounded frames,
deadlines, and cancellation/shutdown behavior, and test a synchronous context
call while an async SDK call is in flight. `_fetch_context()` must use the
local path too. Preserve the client's one-time context cache behavior and the
external HTTP behavior.

Test HTTP/local result, 404/403, cross-org access, engine and service scope,
no fixed-operation HTTP in a real fork, and context property access from both
synchronous code and inside an active event loop. Run focused tests with
`./test.sh` and `./test.sh quality api`. Diagnose failures; no retries,
timeouts, skips, or broad unrelated edits.
