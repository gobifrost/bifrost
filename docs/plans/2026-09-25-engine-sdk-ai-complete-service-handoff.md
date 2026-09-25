# AI complete and model-info shared service

Executor: OpenCode. Reviewer: Codex. Work only in this worktree. Do not
commit, push, merge, or touch the primary checkout.

Extract the fixed Python SDK operations `ai.complete` and
`ai.get_model_info` from `api/src/routers/cli.py` into shared application
service code under `api/shared/`, used by the existing HTTP handlers and a
future engine-local dispatcher. Keep the HTTP handlers thin. `ai.stream`
is a separate later stage; do not change it except if moving a scope helper
requires redirecting its existing import.

The current `_resolve_sdk_org_id` in `cli.py` handles generic SDK scope
and is needed by local dispatch. Move that rule to a shared service with a
transport-neutral error, have the HTTP helper call it, and make AI usage
resolution use the shared rule. Preserve exact UNSET/global/UUID parsing,
provider-org bypass, 403/422 precedence, and current consumers.

Preserve completion request DTOs, input-file decoding, user-message
requirement, selected profile/model, max tokens, provider fallback chain,
response content/tokens/model, error mapping (including 401 provider auth,
503 ValueError and sanitized 500), usage attribution and best-effort usage
recording. Both HTTP and future local paths must call the same operation
service with a trusted principal and session, never a child actor claim.

Release the DB connection after `get_llm_client`/profile lookup and before
awaiting the external provider. Usage recording may reacquire the same
session afterward. Do not hold a checked-out connection during provider
latency. Preserve transaction semantics of the usage write; do not add a
retry, fallback, or new provider. `get_model_info` remains a short DB read.

Add focused tests for scope, file inputs, response, provider auth/ValueError,
usage failures, and the connection release boundary. Preserve HTTP tests.
Run relevant `./test.sh` unit/E2E tests and `./test.sh quality api`; report
exact commands/results and diagnose failures without blind reruns,
retries, skips, or timeout increases. Do not touch local transport,
artifacts, video jobs, agent runs, or unrelated CLI endpoints.
