# Engine SDK over worker-local HTTP

## Decision

Replace PR #810's custom SDK, import, and stream channels with ordinary HTTP over a Unix-domain socket hosted by the worker parent. Keep the external SDK on its current network HTTP path. Keep calls that are already safe and scoped in the execution child (context, decorators, current execution logs, warm Redis module reads) direct. The worker owns pooled PostgreSQL and protected storage/provider credentials; execution children continue to receive neither.

This decision is conditional on a proof that the worker can mount the **existing** SDK-facing FastAPI routes and their dependencies without starting the complete API application or copying route business logic. If that proof fails, stop and report the specific blocker before replacing more code. A second set of worker-only SDK handlers is not an acceptable substitute.

Owner and final reviewer: Codex. Contributor: OpenCode `opencode-go/deepseek-v4.1-flash`, one bounded assignment at a time. Worktree `/home/jack/GitHub/bifrost/.claude/worktrees/sdk-engine-local`; branch `codex/sdk-engine-local`; existing open PR #810. The contributor does not commit, push, merge, alter credentials or config, or clean unrelated worktrees. Keep the PR unmerged until all fixed SDK methods and validation are complete.

## Required behavior

1. Engine startup supplies the worker socket path to each child. The SDK chooses the transport from that trusted injection, with no developer-set `engine` flag and no HTTP fallback after a local failure. External SDK initialization keeps the same URL, request formatting, response parsing, public exceptions, and timeout behavior.
2. The worker serves only the existing routes needed by SDK calls. Use their normal authentication, execution/Solution scope, validation, service functions, status codes, and side effects. Preserve the process-scoped engine token; the socket is a transport, not an authorization bypass. Restrict socket access to the worker/children and remove it during worker shutdown.
3. The worker reuses its initialized database engine and session factory. A child never opens PostgreSQL or receives DB, S3, provider, or signing credentials. A local HTTP request must not reach an API container.
4. Sync module imports and client context access must work while an async SDK call is pending. AI streaming and long-running workflow/agent calls use HTTP streaming/request semantics and keep their existing public timeouts. File bodies and table batches must avoid base64 channel wrappers and be tested with realistic large payloads.
5. Remove obsolete channel code, per-operation local adapters, pumps, and tests once each replacement is verified. Do not retain dead compatibility paths. Preserve genuinely direct Redis and context operations.

## Delivery gates

### Gate A: route-reuse proof

Mount the existing config routes in a minimal worker-local ASGI app and serve it on a Unix socket within the worker's lifecycle. Point one engine child SDK config operation at that socket using the existing HTTP request body and engine token. An external SDK config call still uses API HTTP. Prove: same result/error shape, same auth/scope behavior, zero API-container requests for the engine call, child holds no DB credential, worker pool handles DB access, and clean socket shutdown. Document any routes that require full API lifespan/app state. Stop if route reuse needs copied handlers or a full API instance.

### Gate B: shared HTTP transport

Move the SDK's engine transport decision into one client-level HTTPX configuration for synchronous and asynchronous requests. Cover module imports, client context, normal responses, streaming, cancellation, connection loss, and lifecycle. Keep per-method SDK code transport-agnostic. Compare HTTP and Unix-socket behavior on representative methods before migrating more routes.

### Gate C: full route coverage

Use the exhaustive fixed-method checklist in `docs/plans/2026-09-24-engine-sdk-local-transport.md` as the coverage ledger. Mount their existing route modules in the worker app, reusing shared services and dependencies. Migrate in small reviewed groups: config/integrations; tables; files/artifacts/knowledge; workflows/executions/agents/events/forms; identity; AI and streaming; imports/context. Record every method's direct, worker-local HTTP, or external-only disposition. Arbitrary `bifrost.api.*` remains network HTTP unless separately designed.

### Gate D: removal and acceptance

Delete the custom channels and duplicate dispatch code after all fixed methods use the new path. Test both transports end to end, including service children, auth denial, large batches/files, cold imports, AI stream, cancellation, worker restart, and malformed/missing socket. Re-run the existing before/after transport benchmark and record API-container request counts and latency. Run focused tests and applicable Python quality checks through `./test.sh`; use CI as the broader gate. Review the complete diff for unchanged SDK contracts and no orphan code. Keep PR #810 open and unqueued until the whole replacement is reviewed.

## First OpenCode assignment

Only Gate A. Own a small worker-local server module, its startup/shutdown integration, the narrow SDK config transport hook, and focused unit/E2E tests. Prefer mounting the existing `src.routers.cli.router` with a route selection mechanism over copying a config handler. Report any route or lifespan dependency that prevents this. Do not migrate other SDK methods or delete channels in this assignment.

OpenCode reports changed paths, test commands/results, unrun checks, blockers, and its session ID. Codex reviews the diff and independently verifies the proof before assigning Gate B.
