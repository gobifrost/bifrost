# Knowledge SDK engine-local transport

Executor: OpenCode. Reviewer: Codex. Work only in this worktree. Do not
commit, push, merge, or touch the primary checkout.

Implement local dispatch for all seven fixed `bifrost.knowledge` methods:
`store`, `store_many`, `search`, `delete`, `delete_namespace`,
`list_namespaces`, and `get`. External SDK callers keep HTTP. Both paths
must use the existing `api/shared/sdk_knowledge.py` service. Derive actor,
organization and capability in the worker parent from trusted execution
state, with the same engine versus supervised-service token authority as
HTTP; child `scope` and Solution targets remain untrusted inputs validated
by the shared scope rules. No DB connection or API HTTP in the child, and
no fallback to HTTP after local failure.

Preserve DTO validation, return values, errors/statuses, external-user
denial, org and namespace scope, embedding generation, vector queries,
batch semantics, and storage side effects. Ensure external provider work
does not hold a pooled DB connection needlessly; any transaction change
must be explicit and tested. Do not touch files, tables, artifacts, identity,
AI, or import transport.

Use focused unit tests for each method, HTTP/local parity and scope denial;
add a real worker-process E2E with API HTTP disabled for the fixed calls.
Run relevant `./test.sh` tests and `./test.sh quality api`. Diagnose failures
without blind reruns, retries, skips, or timeout increases; report exact
commands/results.
