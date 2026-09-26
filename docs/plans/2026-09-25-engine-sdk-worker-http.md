# Engine SDK over worker-local HTTP

**Status: implemented and locally verified at `2012f87f2` (2026-09-26); PR #810 awaits CI and merge.** The custom SDK, import, and stream channels have been deleted. Fixed SDK operations use ordinary HTTP to the existing FastAPI routes served by the worker parent on a private Unix socket. External SDK callers keep the network HTTP path. This document records the current design, extension pattern, and local acceptance evidence.

## Decision

Replace PR #810's custom SDK, import, and stream channels with ordinary HTTP over a Unix-domain socket hosted by the worker parent. Keep the external SDK on its current network HTTP path. Keep decorators, current execution logs, and warm Redis module reads direct; client context metadata uses the socket. The worker owns pooled PostgreSQL and protected storage/provider credentials; execution children continue to receive neither.

This decision was conditional on a proof that the worker can mount the **existing** SDK-facing FastAPI routes and their dependencies without starting the complete API application or copying route business logic. That proof and the full replacement are complete (see Final implementation and Acceptance evidence). A second set of worker-only SDK handlers was not needed and was not created.

Owner and final reviewer: Codex. Contributor: OpenCode `opencode-go/deepseek-v4.1-flash`, one bounded assignment at a time. Worktree `/home/jack/GitHub/bifrost/.claude/worktrees/sdk-engine-local`; branch `codex/sdk-engine-local`; existing open PR #810. The contributor does not commit, push, merge, alter credentials or config, or clean unrelated worktrees.

## Required behavior

1. Engine startup supplies the worker socket path to each child. The SDK chooses the transport from that trusted injection, with no developer-set `engine` flag and no HTTP fallback after a local failure. External SDK initialization keeps the same URL, request formatting, response parsing, public exceptions, and timeout behavior.
2. The worker serves only the existing routes needed by SDK calls. Use their normal authentication, execution/Solution scope, validation, service functions, status codes, and side effects. Preserve the process-scoped engine token; the socket is a transport, not an authorization bypass. Restrict socket access to the worker/children and remove it during worker shutdown.
3. The worker reuses its initialized database engine and session factory. A child never opens PostgreSQL or receives DB, S3, provider, or signing credentials. A local HTTP request must not reach an API container.
4. Sync module imports and client context access must work while an async SDK call is pending. AI streaming and long-running workflow/agent calls use HTTP streaming/request semantics and keep their existing public timeouts. File bodies and table batches must avoid base64 channel wrappers and be tested with realistic large payloads.
5. Remove obsolete channel code, per-operation local adapters, pumps, and tests once each replacement is verified. Do not retain dead compatibility paths. Preserve genuinely direct Redis and context operations.

## Final implementation

The engine parent forks each execution child with one trusted value: the path to the worker-local Unix socket (`sdk_socket_path`). `BifrostClient.engine_request`, `BifrostClient.engine_request_sync`, and `BifrostClient.engine_stream` resolve the transport once from that injection — the worker socket when a path was injected, the ordinary external HTTP client otherwise. The choice is made before the request is sent, and a local attempt never falls back to the network API after a socket failure; the request fails instead.

The worker-local ASGI app in `api/src/services/execution/worker_sdk_http.py` selects the **original** `APIRoute` objects out of the real routers by path, and by method where a path carries several routes, and appends them unchanged. The endpoint functions, the ordinary engine bearer-token authentication dependency, the request/response DTOs, the shared services, the status codes, the side effects, and the worker's already-initialized database engine are all the API's. No handler, service, or route is copied; no full API app is started; the server runs with `lifespan="off"` and needs no API `app.state`.

To add a fixed SDK operation, add its route (and method, where the path is shared) to the explicit allowlist constants in that module. `build_worker_sdk_app()` fails loudly if an expected route is missing, so an incomplete mount cannot silently fall through to the network. Nothing is mounted implicitly.

The parent owns PostgreSQL and protected credentials; a child opens neither. Raw `bifrost.api.*` requests remain external HTTP only. Warm module reads are direct Redis. Cold module imports and `BifrostClient.context` go through the socket.

## Delivery gates

All four gates are complete. The channel, import, and stream paths this plan replaced have been removed.

### Gate A: route-reuse proof

Completed. The existing routes are mounted in a minimal worker-local ASGI app served on a Unix socket within the worker lifecycle. Engine calls hit the socket; external calls still use API HTTP. Same result/error shape, same auth/scope behavior, zero API-container requests, no child DB credential, worker pool handles DB access, clean socket shutdown. No route needed a copied handler or a full API instance.

### Gate B: shared HTTP transport

Completed. Transport selection lives in one client-level HTTPX configuration for synchronous and asynchronous requests (`BifrostClient.engine_request` / `engine_request_sync` / `engine_stream`). Per-method SDK code is transport-agnostic.

### Gate C: full route coverage

Completed. Every fixed method in the published checklist has a disposition: worker-local HTTP, or the genuinely direct/ external-only cases (warm Redis reads, raw `bifrost.api.*`, signed storage URLs). The worker app mounts the real route modules with their shared services and dependencies.

### Gate D: removal and acceptance

Completed. The custom channels and duplicate dispatch code were deleted at `2012f87f2`. Both transports are covered end to end, including service children, auth denial, large batches/files, cold imports, AI streaming, cancellation, worker restart, and malformed/missing sockets. Acceptance evidence is recorded below. This document scopes to the code at `2012f87f2`.

## Acceptance evidence

### Transport benchmark

`./test.sh tests/performance/test_sdk_config_transport.py -s -v` runs `config.set/get/list/delete` through both paths in one forked engine child, alternating HTTP and worker-socket blocks, warming each block. There are 60 measured calls per operation and path (240 per path, 480 overall), with fixed-operation API request counts and result-parity assertions.

- External HTTP: **240** API requests.
- Worker socket: **0** engine API requests.
- Median latency (ms, **HTTP / worker socket**): `set` **7.339 / 4.670**, `get` **6.733 / 4.645**, `list` **4.281 / 2.678**, `delete` **7.820 / 4.646**.

The request count is the transport property that matters for API load. The latency figures are a single workstation test-stack diagnostic, not a production load or API CPU measurement; rerun on deployment-like hardware before using the ratios for capacity planning.

### Focused test evidence

- **84** worker-socket route tests (`api/tests/unit/services/test_worker_sdk_http.py`): real-route selection by identity, per-domain exact-selection, socket lifecycle, transport selection, and no-network-fallback checks.
- **23** execution-context validator tests (`api/tests/unit/test_execution_context.py`): the shared `validate_execution_context` contract used by HTTP and the worker socket.
- **8** socket-fork tests across `api/tests/unit/execution/*_local_fork.py` and `test_worker_sdk_http_fork.py`: a real forked child reaches the worker socket.
- **32** process/service/import tests: process pool, service fast path, template boundary, and cold-import module resolution.
- **3** live files/artifacts/knowledge E2E tests (`api/tests/e2e/platform/test_sdk_files_local.py`, `test_sdk_artifacts_local.py`, `test_sdk_knowledge_local.py`): engine children use the socket while the external HTTP path still works, including large payloads.
- **2** live AI/tables-writes E2E tests (`api/tests/e2e/platform/test_sdk_ai_local.py`, `test_sdk_tables_writes_local.py`): unary AI and table writes over the socket. AI streaming is covered by the fork tests.
- **109** retained SDK unit tests: the SDK unit selection kept after channel removal, exercising the shared client transport and facades.

### Broader gates

The full backend E2E and browser suites were **not** run for this work. CI and the merge queue are the broader gate and run the complete backend E2E and browser-smoke suites on the queued candidate.

## Historical first assignment

The original first bounded assignment (Gate A only) is recorded here for process history: own a small worker-local server module, its startup/shutdown integration, the narrow SDK transport hook, and focused unit/E2E tests, preferring to mount the existing `src.routers.cli.router` by route selection over copying a config handler. Gate A completed on those terms and the remaining gates followed.
