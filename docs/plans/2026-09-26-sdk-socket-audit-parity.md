# Worker SDK socket: audit attribution and error parity (follow-up to #810)

**Owner:** Claude (design, review, verification). **Executor:** OpenCode `opencode-go/deepseek-v4.1-flash`.
**Worktree:** `.claude/worktrees/sdk-socket-parity`, branch `fix/sdk-socket-audit-parity`, base `origin/main` @ `e2be9c4c8`.

## Problem

#810 serves SDK routes to execution children from a bare `FastAPI()` app on a
worker-local Unix socket (`api/src/services/execution/worker_sdk_http.py::build_worker_sdk_app`).
The main API app (`api/src/main.py`) wraps the same routes with:

1. global exception handlers (`main.py` ~342-470: RequestValidationError/PydanticValidationError→422,
   IntegrityError→409, NoResultFound→404, ValueError→422, asyncio.TimeoutError→504,
   OperationalError→503, Exception→structured 500);
2. `request_context_middleware` (`main.py` ~509-575) that sets `set_request_user`,
   `set_request_session_id`, and the audit `ActorContext` via `set_actor`.

The socket app has neither, so:
- `emit_audit` (`api/src/services/audit.py`) sees no actor and **silently skips** — user/role/org
  mutations and file/table policy denials made from workflows are no longer audited.
- IntegrityError/ValueError/etc. become unstructured 500s (e.g. concurrent table auto-create:
  SDK treats 409 as success in `api/bifrost/tables.py`, but gets 500 over the socket).

Also, before #810 these audit rows were attributed to the engine sentinel, not the person whose
workflow ran. Jack wants the **context's user**.

## Design (decided — do not change)

### A. Caller claims in the signed engine token
- `mint_engine_token` (`api/src/core/security.py`) gains keyword-only optional params
  `caller_user_id: str | None = None`, `caller_organization_id: str | None = None`,
  `caller_email: str | None = None`, `caller_name: str | None = None`, emitted as claims
  `engine_caller_user_id`, `engine_caller_org_id`, `engine_caller_email`, `engine_caller_name`
  (omit a claim when its value is None).
- The single call site `api/src/jobs/consumers/workflow_execution.py` (~line 980) passes the
  caller it already has (`user_id`, `user_email`, `user_name`, and the execution org id `org_id`).
- These claims are **audit attribution only**. They must NOT change `sub`, `is_superuser`,
  `UserPrincipal`, or any authorization decision. Do not read them anywhere except the actor builder in B.
- `mint_service_token` is unchanged (services have no human caller).

### B. One shared actor builder
New module `api/src/core/request_actor.py` with a pure function:

```python
def actor_from_token_payload(payload: dict | None, *, ip_address: str | None,
                             user_agent: str | None) -> ActorContext | None
```
Rules:
- `payload is None` → return `None`.
- Human token (no `engine_execution_id` claim) → exactly today's behavior: user_id from `sub`,
  organization_id from `org_id`, email/name from token, `source="http"`, `execution_id=None`.
- Service token (`service_id` claim present) → user_id = SYSTEM_USER_UUID, organization_id from
  `org_id`, email/name from token, `source="service"`, `execution_id` = `engine_execution_id`.
- Engine token with `engine_caller_user_id` present and != `SYSTEM_USER_ID` → user_id = caller,
  organization_id = `engine_caller_org_id`, email/name = caller claims, `source="workflow"`,
  `execution_id` = `engine_execution_id`.
- Engine token without a human caller → user_id = SYSTEM_USER_UUID, organization_id =
  `engine_caller_org_id` (may be None), email/name from token, `source="workflow"`,
  `execution_id` = `engine_execution_id`.
- Invalid UUID strings → treat that field as None (never raise).

`ActorContext` (`api/src/services/audit_context.py`) gains `execution_id: UUID | None = None`.

### C. One shared app wiring used by BOTH apps
New module `api/src/core/app_wiring.py`:
- `register_exception_handlers(app)` — move the handlers out of `main.py` verbatim (same status
  codes, same `ErrorResponse` bodies, same logging).
- `install_request_context_middleware(app)` — move `request_context_middleware` out of `main.py`;
  it decodes the token as today and builds the actor with `actor_from_token_payload`. IP comes
  from `get_client_ip(request)` but must tolerate `request.client is None` (Unix socket) → None.
  Keep the reset/clear in `finally`.
- `main.py` calls both instead of defining them inline (behavior identical for the API).
- `build_worker_sdk_app()` calls both. Do NOT add CORS, CSRF, EmbedScope, or body-limit
  middleware to the socket app; add one docstring sentence saying why (engine bearer tokens only,
  no browser, no embed tokens, no uploads on mounted routes).

### D. Persist execution_id on audit rows
- Migration `api/alembic/versions/20260926_audit_execution_id.py`, `revision = "20260926_audit_execution_id"`,
  `down_revision = "20260925_opencode_go_wire_api"` (confirm it is the single head; stop and report if not).
  Adds nullable `audit_logs.execution_id UUID` (no FK — service attempt ids are not executions and
  executions are retention-deleted) plus index `ix_audit_logs_execution_id`. Downgrade drops both.
- `AuditLog` ORM (`api/src/models/orm/audit.py`) gets the column; `emit_audit` passes
  `actor.execution_id` through `AuditLogRepository.create` (add the kwarg there).
- Audit list endpoint (`api/src/routers/audit.py`) response includes `execution_id`, and accepts an
  optional `execution_id: UUID | None` query filter alongside the existing `user_id` filter.
  Update the contract model in `api/src/models/contracts/audit.py`.
- Do NOT touch `client/` (the owner regenerates TypeScript types).

### E. Small cleanups found in review
- `api/shared/sdk_knowledge.py`: delete the uncalled helpers (`load_knowledge_embedder`,
  `embed_content_chunks`, `embed_query_text`, `store_*_preembedded`, `search_knowledge_with_embedding`,
  ~lines 702-861) and fix the module docstring so it describes the code that remains. Grep first to
  prove zero callers (including tests); if any caller exists, stop and report.
- `api/shared/sdk_ai.py` (~161-166): restore pre-#810 behavior — any exception from resolving the LLM
  client yields HTTP 500 with detail "AI completion failed. See server logs for details." and
  `logger.exception(...)`. Keep the existing ValueError handling if it maps to a different status.
- OAuth: keep the external-caller org-only provider lookup in `api/shared/sdk_integrations.py`.
  Add a unit test proving an external caller refreshing a connection that exists only as a GLOBAL
  provider gets 404 (skip adding if an equivalent test already exists — report which).

## Tests (required)
- `api/tests/unit/core/test_request_actor.py`: all five branches of B, including invalid UUIDs.
- `api/tests/unit/core/test_security.py` (or existing token test file): `mint_engine_token` emits
  caller claims when given and omits them when None; `sub`/`is_superuser` unchanged.
- Socket parity in `api/tests/unit/services/test_worker_sdk_http.py` (or a new sibling file) using the
  real `build_worker_sdk_app()` with `httpx.ASGITransport`:
  1. a route raising `ValueError` → 422 structured body; `IntegrityError` → 409 (use a tiny
     test-only route added to the built app, or patch a mounted handler — do not add production routes);
  2. the request-context middleware sets `current_actor()` during a socket request with an engine
     token carrying caller claims (`source="workflow"`, caller user id, execution_id).
- An e2e/DB-backed test (place under `api/tests/e2e/platform/` following existing SDK-local tests)
  that performs a real role create through the socket app with a minted engine token carrying caller
  claims and asserts one `audit_logs` row: user_id = caller, source = "workflow",
  execution_id = the token's execution id.
- Migration: whatever existing migration-head/metadata tests cover new columns must pass.

## Verification commands (executor runs these)
```bash
./test.sh stack up
./test.sh tests/unit/core/test_request_actor.py tests/unit/services/test_worker_sdk_http.py -v
./test.sh tests/unit/routers/test_sdk_modules.py tests/unit/test_contract_version.py -v
./test.sh <the new e2e test path> -v
./test.sh quality api
```
If the contract-version tripwire fails because the audit response DTO changed, this is an additive
change: refresh only `EXPECTED_CONTRACT_FINGERPRINT` as the test instructs. Report it.

## Boundaries
Allowed paths: `api/src/core/{security.py,request_actor.py,app_wiring.py}`, `api/src/main.py`,
`api/src/services/audit.py`, `api/src/services/audit_context.py`, `api/src/repositories/audit*.py`,
`api/src/models/orm/audit.py`, `api/src/models/contracts/audit.py`, `api/src/routers/audit.py`,
`api/src/services/execution/worker_sdk_http.py`, `api/src/jobs/consumers/workflow_execution.py`,
`api/shared/sdk_knowledge.py`, `api/shared/sdk_ai.py`, `api/alembic/versions/20260926_audit_execution_id.py`,
`api/tests/**` (new/updated tests only), `api/shared/version.py` or the contract test fingerprint only if
the tripwire requires it. Anything else → stop and report.

No commits, pushes, installs, dependency or config changes, `client/` edits, retries/skips/xfail,
or unrelated cleanup. Report: changed files, tests run with results, tests not run, open questions.
