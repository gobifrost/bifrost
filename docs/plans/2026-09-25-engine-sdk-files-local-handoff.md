# File SDK engine-local transport

Executor: OpenCode. Reviewer: Codex. Work only in this worktree. Do not
commit, push, merge, or touch the primary checkout.

Implement engine-local dispatch for every fixed Python `bifrost.files` call:
`read`, `read_bytes`, `write`, `write_bytes`, `list`, `delete`, `stat`,
`exists`, `search`, and `get_signed_url`. External SDK callers keep HTTP.
The parent uses the existing shared `api/shared/sdk_files.py` service for
file operations and the existing shared search service for `search`;
both HTTP and local must call the same business behavior. If search still
has router-only orchestration, extract only that behavior to shared code.

Preserve parameters, mode/location behavior, scope and Solution gates,
trusted caller Solution identity, errors/statuses, return types and encoding,
side effects, event publication and transaction semantics. Derive actor,
organization and capability in the parent from execution state. Treat any
child `scope`, `solution`, or `caller_solution` as untrusted targets; never
accept a child actor claim. `mode="local"` must match the HTTP route's
filesystem semantics in the parent; do not silently switch to child CWD.
Never open a DB connection or call API HTTP in the engine child. No local
failure falls back to HTTP. Use bounded chunked transport for bytes.

Do not touch artifacts, tables, identity, AI, or import transport. Tests
must cover all ten methods, HTTP/local parity of success and errors,
cross-org/Solution denial, binary chunks, and a real worker-process path
with API HTTP disabled. Use `./test.sh` focused unit/E2E and
`./test.sh quality api`. Diagnose failures without blind reruns, retries,
skips, or timeout increases. Report exact commands/results.
