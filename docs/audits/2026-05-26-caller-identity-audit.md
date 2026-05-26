# Caller-Identity & Engine-Sentinel Audit (2026-05-26)

## Executive Summary

Bifrost uses a **unified sentinel + caller pattern** for engine-initiated executions, partially matching the user's mental model with important deviations. A single fixed system user (`SYSTEM_USER_ID = "00000000-0000-0000-0000-000000000001"`) acts as the engine sentinel, authenticating to the API via a superuser JWT. Each request carries a **`Caller` object** (user_id, email, name) + **org scope**, not as a separate HTTP header but baked into the `ExecutionContext` passed through the process pool and SDK. **MCP does NOT follow this pattern**—MCP authenticates as the user directly, bypassing the sentinel entirely. **Non-user callers (schedules, webhooks) are correctly scoped**, all using the same system user identity with the target workflow's org_id. However, **ExecutionContext exists in two forms** (FastAPI's `core/auth.py` + SDK's `_execution_context.py`), creating subtle collapsing of sentinel and caller semantics at different layers.

---

## 1. The Sentinel Identity

### Definition & Authentication

**The sentinel is the Bifrost Engine itself**, a fixed system user:
- **User ID**: `"00000000-0000-0000-0000-000000000001"` (string) / `SYSTEM_USER_UUID` (UUID)
- **Email**: `"system@internal.gobifrost.com"`
- **Location**: `/api/src/core/constants.py:11–13`
- **Authentication method**: Long-lived superuser JWT, refreshed in worker processes
- **Issued by**: `api/src/core/security.py:405–451` (`authenticate_engine()`)
- **Token properties**: `is_superuser=True`, `org_id=None` (global scope)
- **Token lifetime**: 30 days (line 435)
- **Credential storage**: `~/.bifrost/credentials.json` (managed by SDK)

### How the API Detects Engine Requests

The API **does not explicitly detect** a request as "from the engine" via a marker or header. Instead:
1. The engine authenticates as `SYSTEM_USER_ID` (the JWT `sub` claim).
2. The JWT is verified on every request by `get_current_user_optional()` (line 120–227 in `core/auth.py`).
3. The `UserPrincipal` extracted from the JWT has `is_superuser=True, organization_id=None`.
4. This identity is the **authenticated principal**, **not** a separate "sentinel marker."

**Key distinction**: There is **no separate API-side check for "this request came from the engine."** Instead, the engine's superuser privilege is treated the same as any platform admin's privilege. The separation happens at the execution-context level (see section 2).

### Is There a Single Sentinel or Multiple?

**Single sentinel identity**, but with a crucial subtlety:
- All scheduled jobs, webhooks, and system-triggered executions use `SYSTEM_USER_ID` (single identity).
- However, when the engine calls an API endpoint on behalf of a user (e.g., CLI endpoints via `_get_cli_org_id`), the caller passed to downstream execution is the **actual user** (from `current_user.user_id`), not the system user.
- **When execution runs in a worker process**, the `ExecutionContext.user_id` is set to the actual caller (line 264–266 in `async_executor.py` for system execution; line 155–158 in `worker.py` for reconstruction).

**Threat model implication**: If anyone acquires the `SYSTEM_USER_ID` + superuser JWT, they can impersonate the engine and initiate any execution. The sentinel credential is unforgeable (JWT is HS256-signed server-side).

---

## 2. ExecutionContext

### Two Distinct ExecutionContext Classes

**Location 1: FastAPI layer** (`api/src/core/auth.py:82–118`)
- **Dataclass**: `ExecutionContext(user: UserPrincipal, org_id: UUID | None, db: AsyncSession)`
- **Purpose**: Dependency injection for HTTP request handlers
- **Org scope rules** (lines 88–93):
  - Regular users: `org_id = user.organization_id` (their home org)
  - System user: `org_id = workflow.organization_id` (for org-scoped workflows)
- **Properties**: `scope`, `user_id`, `is_global_scope`, `is_platform_admin`
- **Construction**: `get_execution_context()` (line 318–355), depends on `UserPrincipal` from JWT

**Location 2: SDK layer** (`api/bifrost/_execution_context.py:74–300`)
- **Dataclass**: `ExecutionContext(user_id: str, email: str, name: str, scope: str, organization: Organization, is_platform_admin: bool, is_function_key: bool, execution_id: str, ...)`
- **Purpose**: Unified context passed to all user code (workflows, scripts, data providers)
- **Includes `Caller` object** (line 44–48): separate `Caller(user_id, email, name)` dataclass
- **Org scope rules** (line 99): `scope` is "GLOBAL" or org UUID string
- **Properties**: `org_id`, `org_name`, `is_global_scope`, `set_scope()` (override for providers)
- **Construction**: Built in the worker process (line 263–275 in `async_executor.py`)

### How Caller Identity Flows

**HTTP Request → Execution Queueing:**
1. User initiates a workflow via API endpoint (e.g., `POST /api/cli/sessions/register`).
2. `CurrentUser` dependency extracts `UserPrincipal` from JWT (line 230–260 in `auth.py`).
3. `get_execution_context()` returns `FastAPI ExecutionContext(user, org_id=user.organization_id, db)` (line 351–355).
4. Endpoint calls `enqueue_workflow_execution(context, ...)` (line 277–282 in `async_executor.py`).

**Queueing → Redis:**
1. `enqueue_workflow_execution()` builds a context dict and pushes to Redis (implementation in `/api/src/services/execution/async_executor.py`).
2. Context dict includes:
   - `user_id`, `user_email`, `user_name` (from the actual user)
   - `org_id` (from `context.org_id`)
   - `is_platform_admin` (from `context.is_platform_admin`)
3. Published to RabbitMQ for worker consumption.

**Redis → Worker Process:**
1. Worker reads pending execution from Redis (line 102–108 in `worker.py`).
2. Reconstructs `Caller` object from `context_data["caller"]` (line 154–159):
   ```python
   caller = Caller(
       user_id=caller_data["user_id"],
       email=caller_data["email"],
       name=caller_data["name"]
   )
   ```
3. Reconstructs `Organization` from `context_data["organization"]` (line 143–151).
4. Builds **SDK-layer ExecutionContext** with user/caller/org fields (line 263–275 in `async_executor.py`):
   ```python
   context = ExecutionContext(
       user_id=SYSTEM_USER_ID,
       email=SYSTEM_USER_EMAIL,
       name=source,
       scope=f"ORG:{org_id}" if org_id else "GLOBAL",
       organization=None,
       is_platform_admin=True,
       ...
   )
   ```
5. **Critical observation**: For **system-triggered executions** (schedules, webhooks), the `user_id` is set to `SYSTEM_USER_ID`, **not** the caller. The actual caller identity is embedded in the `Caller` object and event context, separate from the principal executing the code.

### ExecutionContext Fields for Caller Identity

**Caller identity in FastAPI ExecutionContext**:
- `user: UserPrincipal` (includes `user_id`, `email`, `organization_id`)
- `org_id: UUID | None`

**Caller identity in SDK ExecutionContext**:
- `user_id: str` (the actual executor—`SYSTEM_USER_ID` for system executions)
- `email: str`
- `name: str`
- `scope: str` ("GLOBAL" or "ORG:{uuid}")
- `organization: Organization | None`
- `is_platform_admin: bool`
- **Separate `Caller` object** would need to be tracked outside ExecutionContext (NOT currently done in SDK context layer)

**Gap**: The SDK-layer `ExecutionContext` does **not** carry a separate "who is this being done for" field. The actual caller identity is in event metadata (`EventContext`) or implicit in the `SYSTEM_USER_ID` + org scope.

### Construction Sites & Triggering Events

1. **User click on form/workflow**: `POST /api/cli/sessions/register` → `enqueue_workflow_execution(context from current_user)` (line 277–282 in `async_executor.py`)
2. **API key call**: `POST /api/endpoint/{app_id}/call` → same as above, `current_user` is the API key identity
3. **Scheduled job**: `process_schedule_sources()` in cron_scheduler.py → `enqueue_system_workflow_execution()` (line 684–690 in processor.py) with `user_id=SYSTEM_USER_ID`
4. **Webhook event**: `EventProcessor.queue_event_deliveries()` → `enqueue_system_workflow_execution()` (line 684–690)
5. **Autonomous agent**: `AutonomousAgentExecutor` → `enqueue_system_workflow_execution()` (line 717–720 in `autonomous_agent_executor.py`)

---

## 3. API-Side Caller Extraction

### How Endpoints Get the Caller

**Pattern: Collapse into `current_user`**

Endpoints use the **`CurrentUser` FastAPI dependency** (line 359 in `auth.py`):
```python
CurrentUser = Annotated[UserPrincipal, Depends(get_current_user)]
```

When the endpoint is called:
- JWT is validated.
- `UserPrincipal` is extracted (line 213–227).
- Endpoint receives this as the "caller" for data-access decisions.

**There is NO separate "CallerContext" dependency.** The authenticated user IS the caller.

### Scope Resolution for CLI/SDK Endpoints

Endpoints that accept a `scope` parameter use `_get_cli_org_id()` (line 343–390 in `cli.py`):
```python
org_id = await _get_cli_org_id(current_user.user_id, request.scope, db)
```

This function:
1. If `scope="global"`, returns `None`.
2. If `scope=<UUID>`, returns that UUID **without validating** that `current_user` is a member (line 373–376 is a re-validation that only superusers can override). **This is a platform guard, not a caller-org validation.**
3. Otherwise, returns `current_user.organization_id` (default user org).

**Threat model gap**: A non-superuser can request `scope=<other-org-uuid>`, and `_get_cli_org_id()` will return it, **but** the platform guard in `get_dev_context()` (line 217) checks `if org_id is not None: if not is_superuser: raise 403`. This guard is **endpoint-specific**, not applied to all scope-accepting endpoints.

### Is Caller Distinct from Authenticated Principal?

**No explicit distinction** at the API layer. The `current_user` (from JWT) **is** the caller. For system-triggered executions (schedules, webhooks), the caller is passed as part of the execution context in Redis, **not** as a separate HTTP header or dependency.

---

## 4. Non-User Callers

### Scheduled Jobs

**Caller identity**: `SYSTEM_USER_ID` + org scope from **workflow's `organization_id`**
**How determined**:
1. `process_schedule_sources()` creates a `schedule.fired` Event (line 157–169 in `cron_scheduler.py`).
2. `EventProcessor.queue_event_deliveries()` calls `_queue_workflow_execution()` (line 614–703 in `processor.py`).
3. Line 684–690: `enqueue_system_workflow_execution(workflow_id=..., org_id=str(workflow.organization_id), event=event_context)`
4. `enqueue_system_workflow_execution()` sets `user_id=SYSTEM_USER_ID, org_id=<workflow.org>, is_platform_admin=True` (line 263–275 in `async_executor.py`).

**Implicit scope rule** (line 88–93 in `core/auth.py`): "System user execution uses workflow's organization."

### Webhooks / Event System

**Caller identity**: Same as scheduled jobs—`SYSTEM_USER_ID` + workflow's org
**How determined**:
1. Webhook event arrives at `POST /api/webhooks/{source_id}`.
2. `EventProcessor.process_webhook()` validates signature and creates Event record (line 217–236 in `processor.py`).
3. `queue_event_deliveries()` → `_queue_workflow_execution()` → `enqueue_system_workflow_execution()` (line 684–690).
4. Same result: `SYSTEM_USER_ID` + workflow's org.

**Event metadata carried**: `EventContext(id, type, data, organization_id, received_at)` (line 674–680 in `processor.py`), injected into execution context (line 732–275 in `async_executor.py`).

### Agent-to-Agent Execution

**Chain semantics**: Each agent execution **retains the original caller** if triggered by an API call, or **uses SYSTEM_USER_ID** if triggered by an event/schedule.

Looking at `AutonomousAgentExecutor.queue_agent_tool_execution()`:
- Lines 717–720 queue with `user_id=SYSTEM_USER_ID` when called from within an agent run.
- **Does NOT forward the parent agent's caller.** The parent agent's caller is lost in the chain.

---

## 5. MCP — Confirms NO Sentinel Pattern

### Authentication Method

**MCP authenticates the user directly**, not as the engine impersonating:
- `/api/mcp/status` endpoint (line 61–105 in `routers/mcp.py`) uses `CurrentActiveUser` dependency.
- The user's JWT is validated directly.
- Returns tools based on `current_user.roles`, `current_user.is_superuser`, `current_user.organization_id` (line 88–93).

### Tool Execution

MCP tools **do NOT create a separate ExecutionContext**. They:
1. Authenticate as the end user.
2. Execute immediately in the request handler (no worker process).
3. Receive the user's identity directly from the JWT.

**Quote from code** (line 12–17 in `routers/mcp.py`):
> "Users authenticate through Bifrost's normal login flow (UI or CLI) and use their access token as a Bearer token for MCP requests."

### Implication for Org Scoping

MCP tools **must not** call `resolve_effective_scope()` with a separate "caller" parameter. They should use:
```python
context.org_id = current_user.organization_id
```

If MCP tools need to support org overrides (for providers), that logic must be **within the tool**, not in a generic resolver expecting a sentinel + caller pair.

---

## 6. Drift / Gotchas

### Gotcha 1: Sentinel Identity ≠ "Current User" at Execution Time

**Finding**: For scheduled/webhook executions, the `ExecutionContext.user_id` is set to `SYSTEM_USER_ID`, but the **actual caller identity is embedded in the event context or passed via `Caller` object**, which is **separate** from the executing user.

**Risk**: User workflows checking `context.user_id` or `context.executed_by` will see `SYSTEM_USER_ID` instead of the real trigger (e.g., the schedule name, webhook source, or original user). This is **intentional by design** (the system is the executor), but code that expects `user_id` to reflect "who initiated this" will be confused.

**Code path**: `worker.py:155–159` reconstructs the `Caller` object **separately** from the `ExecutionContext`, suggesting awareness of the distinction, but the SDK-layer `ExecutionContext` does NOT expose the `Caller` object to user code. User workflows cannot access "who really triggered this."

### Gotcha 2: FastAPI ExecutionContext vs. SDK ExecutionContext Semantic Mismatch

**Finding**: The two `ExecutionContext` classes have overlapping names but different fields:
- **FastAPI `ExecutionContext`** (`core/auth.py`): Carries `user: UserPrincipal` (the authenticated principal).
- **SDK `ExecutionContext`** (`_execution_context.py`): Carries `user_id: str, email: str, name: str` (the executor, which may be `SYSTEM_USER_ID` for system executions).

**Risk**: Code migrating between layers (e.g., an endpoint that constructs a `FastAPI ExecutionContext` and passes it to a function expecting `SDK ExecutionContext`) will silently get the wrong identity. The `user_id` in the SDK context is the **executor identity**, not the **authenticated principal identity**.

**Example**: If an endpoint calls a service function expecting `context.user_id` to be the caller's ID, but the context was built for a system execution, it will receive `SYSTEM_USER_ID` instead. This is **not a bug** (both contexts are correct for their layer), but it's a **semantic hazard**.

### Gotcha 3: System User Privilege Collapse

**Finding**: `SYSTEM_USER_ID` has `is_superuser=True, org_id=None` in the JWT (line 266 in `security.py`). When the FastAPI layer receives a request from the engine (or an embed iframe as `SYSTEM_USER_ID`), it grants full superuser privilege.

**Risk**: If **any endpoint uses the authenticated `current_user.is_superuser` without checking whether the call is expected to be a system execution**, the endpoint can be bypassed. Example:

```python
async def sensitive_operation(current_user: CurrentUser):
    if not current_user.is_superuser:
        raise 403  # Only admins can do this
    # ... do dangerous thing ...
```

If the engine (via `authenticate_engine()`) calls this endpoint with a `SYSTEM_USER_ID` token, the check passes, **even though the endpoint may not expect system-originated calls.** The threat depends on the endpoint's logic.

**Code path**: `core/auth.py:305–310` (`get_current_superuser()`) checks `is_superuser`, which is set from the JWT. No distinction between "real platform admin" and "system engine."

### Gotcha 4: Caller Identity Payload Trusted Without Re-Verification

**Finding**: In the worker process, the `Caller` object is reconstructed entirely from the Redis-stored `context_data["caller"]` (line 154–159 in `worker.py`). **There is no re-verification** that the caller identity matches the execution request or that the requesting user actually has the right to trigger this execution.

**Risk**: If Redis is compromised or an actor can modify the pending execution record, they can spoof the caller identity. The worker will execute workflows on behalf of a false caller.

**Mitigation assumption**: Redis is internal/protected, and only the API (which has already validated the user) can write to it. But there is **no cryptographic signature** on the context data.

**Code path**: `redis_client.py:98–166` (`set_pending_execution()`) stores caller data in plaintext JSON in Redis. Line 138–155 builds the `PendingExecution` dict with no MAC or signature.

### Gotcha 5: `is_platform_admin` Passed But Not Re-Checked in Worker

**Finding**: The `is_platform_admin` flag is computed by the API and passed through Redis to the worker (line 112 in `redis_client.py`, line 65 in `redis_client.py`).

**Risk**: If the API incorrectly computes `is_platform_admin=True` for a non-admin user, or if Redis is modified, the worker will execute with elevated privilege. **There is no re-verification** in the worker that the user is actually a platform admin.

**Code path**: 
- `redis_client.py:112`: `is_platform_admin` stored in Redis.
- `processor.py:684`: Passed to `enqueue_system_workflow_execution()`.
- `async_executor.py:269`: Set in SDK ExecutionContext.
- Worker receives it without re-checking.

### Gotcha 6: Caller Identity Missing from Org Scope Resolution

**Finding**: The org-scoping audit (docs/audits/2026-05-26-org-scoping-cascade-audit.md) noted that `resolve_effective_scope()` (not yet designed) must accept both an authenticated principal AND a caller object. **However, the current CLI/SDK endpoints do NOT pass a separate caller.**

**Risk**: CLI endpoints like `POST /api/cli/tables/list` use `_get_cli_org_id()` to resolve scope, which only knows about `current_user`. For **system-triggered executions**, there is no "current user" at the API layer (the engine is the authenticated principal). **When an SDK function makes a recursive call** (e.g., a workflow calls `sdk.tables.query()`), the caller identity is embedded in the worker's `ExecutionContext`, but the SDK call **authenticates as the engine** (using `authenticate_engine()`'s token).

**Implication**: The engine's token has `org_id=None` (global), so SDK calls from workflows **default to global scope unless explicitly scoped**. This is correct (workflows should only access their org's data, enforced by the `ExecutionContext` passed to the SDK). But if someone later refactors to use the engine token's org claim, data could leak.

---

## 7. Implications for resolve_effective_scope Design

The resolver being designed in the broader audit must account for:

1. **Two authenticated identities**:
   - The **HTTP authenticator**: `current_user` (from JWT), used to grant permission to initiate execution.
   - The **execution caller**: The actual user/system/event that is being acted upon, stored in Redis pending execution.

2. **Org scope can come from multiple sources**:
   - User's home org (`current_user.organization_id`).
   - Override scope in request (`scope` parameter in CLI endpoints).
   - Workflow's org (for system executions).
   - OAuth provider's org.

3. **Resolution must happen at two layers**:
   - **API layer**: `_get_cli_org_id()` today; should become a unified `resolve_effective_scope(current_user, request.scope, ...)`.
   - **Worker layer**: `ExecutionContext.scope` is already set, no re-resolution needed (immutable during execution).

4. **Platform guard must prevent unauthorized scope override**:
   - Only superusers can explicitly request a different org's scope.
   - Regular users' scope must always be their home org.
   - System executions must use the workflow's org.

5. **The resolver should NOT collapse sentinel and caller**:
   - It must accept the **authenticated principal** (who is calling the API) separately from the **execution caller** (who the work is being done for).
   - For HTTP requests, these are the same (`current_user` is the caller).
   - For system executions (schedules, webhooks), the authenticated principal is the engine's sentinel, but the caller is embedded in the execution context.

---

## 8. Open Questions

1. **Does `ExecutionContext` ever expose the real caller for user code?** The SDK-layer `ExecutionContext` does not carry a `Caller` object, only `user_id` (which is `SYSTEM_USER_ID` for system executions). Can user workflows access the real trigger identity (e.g., schedule name, webhook source, or original user)?

2. **Is the engine token's org scope (`org_id=None`) ever used incorrectly?** Lines 267, 423, 430 in code use global `org_id=None` for the sentinel. If an SDK call uses the engine token's claims directly instead of the `ExecutionContext`, it would bypass org scoping. Is there any code path where this happens?

3. **How is the caller identity propagated in agent-to-agent chains?** When Agent A calls Agent B, does B know who originally triggered A, or does B only know that A called it? (Current code: B only knows A, original trigger is lost.)

4. **What is the threat model for Redis compromise?** If an attacker can modify the pending execution record in Redis, they can spoof any caller identity. Is Redis protected by network isolation, encryption, or ACLs?

5. **Are there any workflows or webhooks that check `context.user_id` expecting it to be a real user?** If so, they will silently get `SYSTEM_USER_ID` for system-triggered executions. Is there a migration plan?

---

## Summary

| Finding | Status |
|---------|--------|
| Single fixed sentinel identity? | ✅ Yes—`SYSTEM_USER_ID` + superuser JWT |
| ExecutionContext carries caller identity? | ⚠️ Partial—FastAPI layer has it; SDK layer conflates with executor |
| API detects sentinel vs. caller? | ❌ No—sentinel is just another superuser |
| Non-user callers work (schedules/webhooks)? | ✅ Yes—same system user, workflow's org |
| MCP uses sentinel pattern? | ❌ No—direct user authentication |
| Sentinel privilege isolated? | ⚠️ Mostly—no code-level marker, but OAuth token's org=None provides implicit isolation |
| Caller identity re-verified in worker? | ❌ No—trusted from Redis without signature |
| Org scope re-checked at execution time? | ✅ Yes—SDK ExecutionContext sets immutable scope |

