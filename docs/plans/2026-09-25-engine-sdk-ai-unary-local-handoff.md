# AI completion and model-info SDK local transport

Executor: OpenCode. Reviewer: Codex. Work only in this worktree; no commit,
push, main merge, or primary checkout edit. Read AGENTS.md and the aggregate
SDK transport plan.

Migrate `bifrost.ai.complete` and `ai.get_model_info` to named engine-local
operations, using `api/shared/sdk_ai.py` exactly as the HTTP routes do.
External callers retain HTTP. `ai.complete(knowledge=...)` first searches
through the already local knowledge facade and then completes locally; keep
that composition and its public `AIResponse`/structured output behavior.
`ai.create_image` and `create_video` delegate to artifact methods and are
handled in those slices. Validate the same request DTOs and preserve model,
profile, response format, tool usage, input file, and error semantics.

Build the actor and usage `execution_id` only from
`LocalDispatchPrincipal`, never from child claims. Pass a requested `org_id`
as an untrusted scope to the shared service's existing authorization and
best-effort usage rule; do not treat it as a trusted actor org.
`ai.complete` currently
wraps HTTP errors as `RuntimeError("AI completion failed: ...")`; preserve
that public exception text for local service errors too. The shared service
releases its DB connection during the provider
call and reopens briefly for usage; preserve this when dispatching. Align
parent and child deadlines with the existing HTTP provider timeout, including
cancellation and parent shutdown. Do not buffer large binary input in an
unbounded single frame, retry a failed local completion over HTTP, or leave a
connection checked out for provider latency.

Reviewer audit: `complete_sdk_ai()` records usage with `flush` only. The
HTTP `get_db` dependency commits after the route returns; local `_run_short`
does not commit, so explicitly commit after the shared completion returns or
usage silently rolls back. Build the token-equivalent caller with
`_table_user_for_principal(principal)`. Pass `request.org_id` to the service
as an untrusted requested scope without `_resolve_frame_scope` beforehand:
HTTP completion treats an invalid or denied usage scope as best effort and
still returns the provider response. Translate local status errors to the
same public `RuntimeError` text as the HTTP facade. Model-info errors use
`RuntimeError("Failed to get AI model info: ...")`.

The generic parent `_run_short` deadline is 25 seconds, while the SDK's
completion timeout defaults to 30 seconds and accepts overrides. Derive
matching child and parent deadlines from the requested timeout, with the
parent expiring first so it can return an error frame. Keep knowledge
composition, structured-output instructions, and input-file encoding on the
child before transport selection; send the composed messages and already
encoded files through `CLIAICompleteRequest` and the existing chunked
request protocol. The parent must derive usage `execution_id` from its own
principal, even if a child frame supplies another one. Map `SdkAIError`
through its status rather than a generic 500. Model-info is a separate
unary operation with no profile override.

Test HTTP/local response and error parity, profile and org scope, knowledge
composition, usage persistence and attribution, provider failure, public
model-info shape, and one real engine fork with fixed AI HTTP disabled.
Use fake providers; no paid external API. Run focused `./test.sh` and
`./test.sh quality api`; diagnose failures without retries, skips, or
timeout inflation.
