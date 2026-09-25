# Engine SDK local transport

## Goal and ownership

Owner/reviewer: Codex. Executor: OpenCode, one bounded stage at a time, using the
locally discovered `opencode-go/muse-spark-1.3-contributor` model. Worktree:
`/home/jack/GitHub/bifrost/.claude/worktrees/sdk-engine-local`; branch:
`codex/sdk-engine-local`; baseline: `origin/main` at `74b8dd5ab`.

The Python SDK must keep one public behavior for external callers and engine
children. Fixed SDK operations should execute in the worker parent through a
local transport, sharing the operation service with their HTTP handlers. The
child must not own PostgreSQL connections. Neither the current main checkout's
`opencode.json` edit nor its untracked backup belongs to this work.

No stage is merged to main until the complete fixed-operation coverage and
focused verification are accepted. The owner reviews each stage before the
next. OpenCode makes no commit or push and does not modify configuration,
credentials, dependencies, or unrelated files.

## Transport contract

- Engine startup injects the transport. No user-controlled `engine` flag.
- Use dedicated child-parent channels, not terminal result or work frames.
- Parent dispatches an explicit allowlist of named, versioned SDK operations.
- Parent reconstructs principal, execution, organization, Solution and app
  scope from its own dispatch context. It does not trust caller-supplied scope
  claims or actor identity in the request payload.
- Child-origin frames have bounded, non-pickle serialization. Include request
  IDs, method, payload size limits, deadlines, cancellation, and concurrent
  request support. Binary payloads need bounded chunking/backpressure.
- Parent uses its pooled database engine, with one short session per operation
  and bounded concurrent requests. Never share a connection across processes.
- The HTTP handler and local dispatcher call the same business service. SDK
  response parsing and public exceptions remain identical.
- A failed local request does not automatically retry over HTTP: a write may
  already have committed. Preserve existing idempotency semantics explicitly.
- Child exit, parent shutdown, template recycle, timeout and long-lived
  `@service` stop must cancel requests and close descriptors cleanly.

## SDK coverage

| Domain | Fixed SDK methods | Local service boundary |
| --- | --- | --- |
| Config | get, set, list, delete | Config resolution, secret handling, cache updates |
| Integrations | get, mappings CRUD/list, OAuth refresh | Mapping, declared connection, token rotation |
| Tables | definition create/list/delete, document CRUD, batch, query, count | Scope, policies, attribution, transaction, broadcasts |
| Files | read/write text and bytes, list/delete/stat/exists/search/signed URL | File policy, versioning, storage, events |
| Knowledge | store, store_many, search, delete, namespace/list/get | Embedding, org scope, vector queries |
| Workflows/executions | list/execute/cancel/get, execution list/get | Scheduling/queue and history services |
| Agents | enqueue/get_run/run/wait | Agent queue/status, deadlines, paused outcome |
| Events/forms | emit, form list/get | Event transaction/delivery, form access |
| Identity | organization/user/role CRUD and role assignments | Auth, invites, audit, cache invalidation |
| Artifacts | write/render/generate/read/list/download URL, video job status | Workspace, object storage, durable platform jobs |
| AI | complete, stream, model info | Provider service, usage, streaming backpressure |
| Engine Python imports | cold-cache module fetch and name resolution | Redis/S3 source lookup, signed Solution scope, synchronous import hook |
| Client context metadata | `BifrostClient.context` and its `user`/`organization`/`default_parameters` views | Parent execution context, external `/api/sdk/context` response |

`ai.create_image` and `ai.create_video` delegate to artifact methods. The
hidden `OAuthCredentials.refresh()` call is included. `context`, decorators,
service controls, and current execution logs are already local or direct Redis.
The raw `bifrost.api.*` escape hatch remains HTTP because its target is
arbitrary. Direct signed storage URLs remain storage calls. CLI-only reference
resolution and the browser SDK remain HTTP.

### Exhaustive public-method checklist

Each name below needs an explicit local disposition and HTTP/local parity
check. Names joined with a slash share a server operation but remain distinct
SDK entry points.

| Facade | Methods |
| --- | --- |
| `config` | `get`, `set`, `list`, `delete` |
| `integrations` | `get`, `list_mappings`, `get_mapping`, `upsert_mapping`, `delete_mapping`; `OAuthCredentials.refresh()` |
| `tables` | `create`, `list`, `delete`, `insert`, `upsert`, `get`, `update`, `delete_document`, `insert_batch`, `upsert_batch`, `bulk_upsert`, `delete_batch`, `query`, `count` |
| `files` | `read`, `read_bytes`, `write`, `write_bytes`, `list`, `delete`, `stat`, `exists`, `search`, `get_signed_url` |
| `knowledge` | `store`, `store_many`, `search`, `delete`, `get`, `delete_namespace`, `list_namespaces` |
| `workflows` | `list`, `execute`, `cancel`, `get` (delegates to `executions.get`) |
| `executions` | `list`, `get`; `get_current_logs` is already a direct Redis read |
| `agents` | `enqueue`, `get_run`, `run` |
| `events` | `emit` |
| `forms` | `list`, `get` |
| `organizations` | `create`, `get`, `list`, `update`, `delete` |
| `roles` | `create`, `get`, `list`, `update`, `delete`, `list_users`, `list_forms`, `assign_users`, `assign_forms` |
| `users` | `list`, `create`, `get`, `update`, `delete` |
| `artifacts` | `write`, `create_document`, `create_spreadsheet`, `create_text`, `create_image`, `create_video`, `read`, `list`, `get_download_url` |
| `ai` | `complete`, `stream`, `get_model_info`; `create_image` and `create_video` delegate to artifacts |

Preserve existing composite semantics: table writes may create a missing table
outside Solution context, `bulk_upsert` retries 409 conflicts, filtered
`tables.count` delegates to `query`, `ai.complete(knowledge=...)` searches
knowledge first, and video generation polls a shared PlatformJob. The local
transport must preserve those behaviors without recursive HTTP requests.
`bifrost.api.get/post/put/patch/delete` and `BifrostClient` raw request
methods remain HTTP escape hatches because their paths are arbitrary.
`BifrostClient.context` is a fixed call even though it is not a root-exported
facade; its engine view belongs in the local coverage. External credential
refresh remains an HTTP authentication operation, while supervised engine
service credentials are renewed through the existing parent/Redis handoff.
`refs.py` is a CLI/reference utility, not an exported runtime facade.
The root-exported `bifrost.context` and decorators are already local.
The engine's internal synchronous import hook is a separate fixed path:
`get_module_sync()` reads Redis first, but on a miss calls
`GET /api/sdk/modules/{path}`; name resolution can call
`GET /api/sdk/modules-resolve`. Those cold-cache calls belong in the local
coverage, even though they are not exported from `bifrost.__init__`.

### Existing server seams

`ArtifactService`, artifact generation, event emission, workflow execution,
agent runtime, and repository-backed reads already supply useful shared
services. Config set/delete and table create still embed mutation rules in
SDK handlers; integrations and knowledge have repository primitives but need
an application service; file and identity routers contain the business
orchestration for their operations. Extract those rules into shared services
as each operation is migrated, and have both the HTTP handler and local
dispatcher call the same function.

## Delivery stages

1. Transport and `config.get`: dedicated channel, explicit injection, parent
   dispatcher, shared config-get service, HTTP/local parity and crash tests.
2. Remaining config and integration operations.
3. Table definitions and all document methods, including batch semantics.
4. Synchronous import-hook misses (module fetch and name resolution), then
   file/artifact binary transport and operations, then knowledge. Preserve
   signed Solution access and avoid deadlocking the child event loop during
   synchronous Python imports.
5. Workflow, execution, agent, event, form, and client context metadata
   operations.
6. Identity and role operations.
7. AI info, completion, and streaming with provider/usage parity.
8. Full coverage audit, removal of duplicate behavior, realistic concurrent
   workflow and supervised-service E2E, targeted quality checks, final review.

No traffic benchmark is a prerequisite. Each stage should record API request
counts by operation in its end-to-end test: a local call must make zero API
requests for that fixed operation while external SDK calls still use HTTP.

An opt-in live-worker transport benchmark is available for the implemented
config slice: `./test.sh tests/performance/test_sdk_config_transport.py -s -v`.
It runs `config.set/get/list/delete` through both paths in one forked engine
child, alternates HTTP and local blocks, warms each block, and prints p50/p95
latency and fixed-operation HTTP call counts. It asserts parity and request
counts, but has no machine-dependent timing threshold. On the 2026-09-24 test
stack, 60 measured calls per operation and path yielded these medians (ms):
set 50.093 HTTP / 31.950 local; get 51.431 / 30.702; list 36.381 / 15.740;
delete 50.164 / 27.826. The measured fixed-operation HTTP count was 240 / 0.
This is one workstation test-stack result, not a production load or API CPU
measurement; rerun on deployment-like hardware before using the latency
ratios for capacity planning.

## Stage acceptance and verification

For every migrated operation, test the same inputs via HTTP and local service:
result, status/error, committed state, authorization, and side effects. Include
negative tests for cross-org/Solution scope, malformed payloads, child crash,
parent shutdown, deadlines, concurrent calls, and payload limits. Run focused
unit and E2E tests through `./test.sh`, relevant contract tripwires, API
quality, and a live workflow and supervised-service path when their boundary
changes. Do not use retries or skips to mask failing tests. The owner runs
independent focused verification and reports broader suites not run. The
merge queue remains the complete-suite gate.

## Stage 1 handoff state

Implemented in the isolated worktree, with no commit, push, or merge. The
dedicated child-parent channel uses bounded JSON frames and lazy chunked
responses; the parent uses short sessions from its pooled engine. `config.get`
has one shared scope resolver and value service for HTTP and local calls.
Forked workflows and supervised `@service` processes install the local
transport; external SDK use remains HTTP. A failed local request never retries
over HTTP.

OpenCode session: `ses_f2a877120ffesNavhMgne4IKYW`, followed by a fresh
bounded reviewer-correction run. Focused verification reported: 45 config and
transport unit tests, the scope-resolver tripwire, five real-fork tests
(including large response, crash, and supervised service), two live E2E tests,
and API pyright/ruff all passed. A preceding full unit run had 6,770 passing
tests and one source-inspection tripwire failure; that tripwire now recognizes
the shared resolver and passes in a focused rerun. Do not infer a post-fix
full-suite result from the focused rerun. Reviewer verification:
`./test.sh tests/unit/sdk/test_sdk_config_local.py tests/unit/execution/test_sdk_local_dispatch.py tests/unit/execution/test_sdk_local_fork.py tests/unit/execution/test_process_pool.py tests/unit/test_org_scoping_enforcement.py -v`
passed 118 tests. Reviewer disposition: stage 1 accepted for continuation;
final product acceptance and main merge remain pending all fixed operations.

## Stage 2a handoff state

Implemented and reviewed in the same isolated worktree. `config.set`,
`config.list`, and `config.delete` now use the parent-local channel in engine
children, with HTTP and local dispatch calling the same shared config
services. The transport handles large request values and list results as
bounded, sequential frames. Workflow and supervised-service audit identities
match their existing HTTP tokens; service cross-org bypass checks provider
membership live in the parent. Config requests use the HTTP DTOs for field
validation. No partial stage has been merged or pushed.

Reviewer verification:

- `./test.sh tests/e2e/platform/test_sdk_config_local.py::TestSdkConfigMutationLiveE2E -v`: 1 passed (queued worker, local operations with HTTP disabled, external HTTP state check).
- `./test.sh tests/unit/sdk/test_sdk_config_local.py tests/unit/execution/test_sdk_local_dispatch.py tests/unit/execution/test_sdk_local_fork.py tests/unit/execution/test_process_pool.py tests/unit/test_org_scoping_enforcement.py tests/unit/test_contract_version.py tests/unit/test_dto_flags.py -v`: 226 passed after the cache-aware fork fixture fix.
- `./test.sh tests/unit/execution/test_sdk_local_dispatch.py tests/unit/sdk/test_sdk_config_local.py -v`: 86 passed in the exposing order after the list helper was made chunk-aware.
- `./test.sh quality api`: 0 Pyright errors and Ruff passed after final cleanup.

The broader full backend and browser suites were not run after stage 2a.
Earlier failures in the new live test import, the test stack reset, the
fork fixture's Redis cache setup, and the single-frame list assertion were
diagnosed and corrected. Stage 2a is accepted for continuation; the complete
SDK product remains unfinished.

The config E2E proves zero API requests for the fixed config methods by
disabling their HTTP client in the child. It still makes an internal module
fetch request on a cold Redis cache. Stage 4 must eliminate that engine
API hop before claiming the full SDK fast path is complete.

## Stage 2b handoff state

The three integration reads (`get`, `list_mappings`, `get_mapping`) now use a
shared service from HTTP and the engine parent. The child receives its
Solution identity from parent execution context, and the service preserves
the HTTP rule that a missing integration returns null before scope checking.
The live worker test disables the fixed-operation HTTP client and compares
results with external HTTP calls. Mapping writes and OAuth refresh remain in
the next stage.

Reviewer verification after the final scope-order correction:
`./test.sh tests/unit/execution/test_sdk_integrations_dispatch.py tests/unit/sdk/test_sdk_integrations_local.py -q`
passed 40 tests; `./test.sh tests/e2e/platform/test_sdk_integrations_local.py -v`
passed one live test after that correction; `./test.sh quality api` passed
with zero Pyright errors and Ruff clean. The broader backend and browser
suites have not been run. Stage 2b is accepted for continuation.

## Stage 2c handoff state

Implemented and self-verified in the same isolated worktree, with no
commit, push, or merge. `integrations.upsert_mapping`,
`integrations.delete_mapping`, and hidden `OAuthCredentials.refresh()`
now use the parent-local channel in engine children, with HTTP and
local dispatch calling the same new shared services in
`api/shared/sdk_integrations.py` (`upsert_sdk_integration_mapping`,
`delete_sdk_integration_mapping`, `refresh_sdk_oauth_token`). The
child selects local transport only when installed; external callers
keep HTTP. A failed local request never retries over HTTP.

Behavior preserved from the HTTP endpoints: missing-integration 404
before scope validation on upsert (delete returns
`{"deleted": False}`), global-scope 400 on upsert (`{"deleted":
False}` on delete), cross-org 403s via the mutation scope gate,
OAuth-link preservation on update, config-write/merged-echo semantics
with the external global-tier exclusion, locked token lookup
(`get_org_level_for_provider(..., for_update=True)`), refresh
context/rotation/persistence, and external org-only restrictions. The
child registers the fresh access token with its own secret scrubber,
and local upsert/refresh failures raise the same `RuntimeError`
shapes as their HTTP facades. The old
`test_mutations_and_refresh_stay_http_only` 404 assertion was replaced
with allowlist/guard coverage.

Focused verification reported (all green): 23 new unit tests
(`tests/unit/sdk/test_sdk_integration_mutations_local.py` — both
transports, refresh lock/rotation/persistence, external isolation,
org-scope mutations, child round trips with zero HTTP); 141 tests
across the integrations dispatch, integrations/config local, and
local-dispatch suites; 66 contract tripwire tests
(`test_contract_version.py`, `test_dto_flags.py` — no DTO changed);
`./test.sh quality api` (0 Pyright errors, Ruff clean); one new live
worker E2E (mutations with fixed-operation HTTP disabled, committed
state re-checked over external HTTP) plus the stage-2b live E2E; and
70 regression tests across `test_cli_integrations_external.py`,
`test_integrations.py`, and `test_org_scoping_scenarios.py`.
After reviewer corrections, the 23 new unit tests plus 22 dispatcher
tests passed, the tightened external/live E2E set passed 14 tests, and
13 OAuth router/scope tests passed after their stale scope-helper mocks
were updated to the shared service boundary.

Coverage limits: refresh success persistence is proven only in unit
tests (mocked provider HTTP — no real OAuth provider exists in the
test stack); the live workflow asserts the loud 404-shaped
`RuntimeError` for a missing provider with HTTP disabled. Broader
backend and browser suites were not run.

Reviewer correction: an external caller could resolve a global OAuth
provider through the generic name cascade and potentially mint a token
with global client credentials. The shared refresh service now uses an
org-only provider lookup for external callers. Unit tests cover both
OAuth flow types and require 404 before contacting the provider; the
external E2E now requires 404 as well. OAuth refresh uses a 30-second
parent deadline and a 35-second child deadline, allowing the provider
request and a returned error frame without an earlier local cutoff.
