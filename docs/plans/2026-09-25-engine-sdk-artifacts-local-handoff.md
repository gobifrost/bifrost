# Artifact core engine-local transport

Executor: OpenCode. Reviewer: Codex. Work only in this worktree. Do not commit,
push, merge, or touch the primary checkout.

Implement engine-local transport for exactly four fixed Python SDK calls:
`artifacts.write`, `artifacts.read`, `artifacts.list`, and
`artifacts.get_download_url`. External SDK callers must retain their HTTP
path. In an engine child, use the existing parent local channel and call the
shared `api/shared/sdk_artifacts.py` service in the worker process. Do not
create a DB connection in the child or call API HTTP from the local path.
The parent must derive user, organization, and capability from trusted
execution state and distinguish workflow engine tokens from supervised
service tokens. Never trust actor or org claims in the child payload.

Preserve the exact Python return types, bytes, versioned refs, error/status
behavior, workspace scope, signed URL semantics, and request/response limits.
Follow the existing local transport protocol and allowlist patterns; do not
add a fallback to HTTP when local dispatch fails. Do not touch generation or
AI methods yet. Keep edits to artifact SDK/dispatcher/transport, tests, and
this handoff. If a necessary shared-service adjustment is found, keep it
minimal and explain it.

Tests must cover all four operations over the local channel, HTTP parity for
both success and errors, true worker-process execution with API HTTP disabled,
and the service-token org boundary. Use deterministic fixtures and the
`./test.sh` stack. Run focused unit and E2E tests plus `./test.sh quality api`.
Report exact commands/results and diagnose any failure without blind reruns,
retries, skips, or timeout increases.
