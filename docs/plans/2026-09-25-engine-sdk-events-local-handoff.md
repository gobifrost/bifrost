# Event SDK engine-local transport

Executor: OpenCode. Reviewer: Codex. Work only in this worktree. Do not
commit, push, merge, or touch the primary checkout.

Implement engine-local transport for the fixed `bifrost.events.emit` call.
External SDK callers keep HTTP. The HTTP route and worker parent must both
call `api/shared/event_emission.py`. The child opens no DB connection or
API HTTP, and no failed local attempt falls back to HTTP (the event may
already have committed).

The parent supplies a token-equivalent trusted principal: workflow engine
superuser with verified execution/Solution claims, or supervised-service
non-superuser confined to its organization. `scope` and `solution` are
untrusted requested targets that the shared service validates. Do not
accept child actor, app ID, organization, or `caller_solution` identity
claims. If the shared request DTO carries `caller_solution`, replace it
with the parent-verified own Solution ID (or None) before calling the
service. Preserve topic validation, 403/400/404 precedence, inbound gate,
event actor, response ID/subscriber count, queue delivery, and public
exceptions. Ensure event enqueue happens once; no automatic retry.

Keep edits to event SDK, local transport/dispatcher, tests, and this
handoff. Do not touch other event endpoints, files, tables, identity,
artifacts, AI, or import transport. Add HTTP/local parity tests for success
and denial plus one real worker E2E with API HTTP blocked for `/events/emit`.
Run focused `./test.sh` tests and `./test.sh quality api`; diagnose failures
without blind reruns, retries, skips, or timeout increases. Report exact
commands/results.
