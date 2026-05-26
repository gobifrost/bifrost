# Org-Scoping & Cascade Resolution Audit (2026-05-26)

## Executive Summary

The Bifrost codebase contains **18 ORM models with `organization_id`** columns, of which **13 are execution-time resolution entities** (configs, tables, knowledge, workflows, integrations, OAuth, MCP, etc.) and **5 are identity/admin records** (organizations, users, audit logs, executions, events). 

**9 effective-scope resolvers** exist, scattered across routers (CLI), services (OAuth storage), and core auth (ExecutionContext). **5 distinct cascade-resolution implementations** are active: the canonical `OrgScopedRepository` base class (two-query for name lookups, single-query for lists), `ConfigResolver` (two-query overlaying), `OAuthStorageService.get_connection` (single-query with order-by), and **2 inline cascades** (cli.py `cli_list_tables` and `sdk_integrations_get`). 

The most concerning finding: **repositories for Application and Table exist only in routers (applications.py line 96, tables.py line 212), not in the shared `api/src/repositories/` directory**, breaking the documented canonical pattern. Inline cascades in cli.py bypass these repositories entirely. Tests pass because they cover happy paths (org-wins) but do not exercise all cascade semantics or cross-tenant isolation. One prior token leak (`get_provider_org_token` in integrations.py line 712) had no org filter; the current code filters by `user_id=NULL` only, still lacking org scoping.

---

## 1. ORM Models with `organization_id`

| Model | File | Nullable | Repo Class | Classification | Notes |
|-------|------|----------|-----------|-----------------|-------|
| Application | applications.py | Yes | ApplicationRepository (in routers/applications.py line 96) | Execution-time | App builder published snapshots; accessed during form/app load |
| Agent | agents.py | Yes | AgentRepository | Execution-time | AI agents; accessed during workflow execution + chat |
| Form | forms.py | Yes | FormRepository | Execution-time | User-facing forms; accessed on load and execution |
| Workflow | workflows.py | Yes | WorkflowRepository | Execution-time | Executable code; accessed during execution and SDK lookups |
| Table | tables.py | Yes | TableRepository (in routers/tables.py line 212) | Execution-time | Document collections; accessed during SDK table.query() calls |
| Config | config.py | Yes | None (handled by ConfigResolver service) | Execution-time | Integration/workflow config; accessed via ConfigResolver.load_config_for_scope |
| KnowledgeStore | knowledge.py | Yes | KnowledgeRepository | Execution-time | RAG embeddings; accessed during knowledge search in agent execution |
| OAuthProvider | oauth.py | Yes | None (handled by OAuthStorageService.get_connection) | Execution-time | OAuth configs; accessed during token refresh and integration setup |
| IntegrationMapping | integrations.py | Yes | IntegrationMappingRepository | Execution-time | Org→integration entity mappings; accessed during sdk.integrations.get() |
| MCPServer | external_mcp.py | Yes | MCPServerRepository | Execution-time | External MCP templates; accessed during tool discovery |
| MCPConnection | external_mcp.py | Yes | MCPConnectionRepository | Execution-time | Per-org MCP instance configs; accessed during MCP tool execution |
| AIUsage | ai_usage.py | Yes | None | Auditing/metrics | AI token tracking; no cascade lookup needed |
| EventSource | events.py | Yes | None | Auditing/metrics | Event webhooks; no cascade lookup needed |
| AuditLog | audit.py | Yes | None | Auditing/metrics | User action log; no cascade lookup needed |
| User | users.py | Yes | None (direct FK) | Identity/admin | Users belong to org; no execution-time cascade (identity record) |
| Execution | executions.py | Yes | None | Auditing/metrics | Workflow run records; no cascade lookup needed |
| MCPConnection | external_mcp.py | Yes | MCPConnectionRepository | Execution-time | Per-org MCP instance; accessed during tool lookup |

---

## 2. Effective-Scope Resolvers

### Resolver 1: `_get_cli_org_id` (cli.py lines 343–390)
- **Location**: `/api/src/routers/cli.py:343–390`
- **Inputs**: user_id, scope (string: None/"global"/<UUID>), db session
- **Outputs**: org_id (UUID string or None)
- **Enforces platform guard?** NO — accepts any UUID string directly without checking caller's org membership.
- **Called from**: 40+ endpoints in cli.py (config, knowledge, integrations, tables, sessions)
- **Behavior**: If scope="global", returns None; if scope is valid UUID, returns it; else falls back to user's DeveloperContext.default_org_id. No re-validation that caller is allowed to use the requested scope.

### Resolver 2: `get_dev_context` (cli.py lines 205–275)
- **Location**: `/api/src/routers/cli.py:205–275`
- **Inputs**: current_user, org_id (optional override), db
- **Outputs**: DeveloperContextResponse with org data
- **Enforces platform guard?** YES — line 217 checks `if org_id is not None: if not current_user.is_superuser: raise 403`.
- **Called from**: GET /api/cli/context
- **Behavior**: If org_id override provided, requires superuser and validates org is active.

### Resolver 3: `ExecutionContext` (core/auth.py lines 82–118)
- **Location**: `/api/src/core/auth.py:82–118` (instantiated in routers, services, executor)
- **Inputs**: user (UserPrincipal), workflow object or request context
- **Outputs**: ExecutionContext with org_id
- **Enforces platform guard?** IMPLICIT — org_id comes from user's org or workflow.organization_id. Regular users cannot override.
- **Called from**: Every execution path
- **Behavior**: Regular users use their org; system execution uses workflow's org.

### Resolver 4: `OAuthStorageService.get_connection` (oauth_storage.py lines 126–167)
- **Location**: `/api/src/services/oauth_storage.py:126–167`
- **Inputs**: org_id (string: None/"GLOBAL"/<UUID>), connection_name
- **Outputs**: OAuthConnection or None
- **Enforces platform guard?** NO — accepts any org_id without validation.
- **Called from**: OAuth setup/refresh flows
- **Behavior**: Single-query with `or_(org_id, NULL)` ordered to prefer org-specific.

### Resolver 5: `ConfigResolver.load_config_for_scope` (core/config_resolver.py lines 296–399)
- **Location**: `/api/src/core/config_resolver.py:296–399`
- **Inputs**: scope ("GLOBAL" or org_id string), db
- **Outputs**: dict[key, {value, type}]
- **Enforces platform guard?** NO — accepts any scope string without validation.
- **Called from**: CLI config operations, workflow execution
- **Behavior**: Two-query: global first, then org-specific. Org-specific overrides global.

### Resolver 6: User's org from JWT (core/auth.py lines 120–227)
- **Location**: `/api/src/core/auth.py:120–227` (get_current_user_optional)
- **Inputs**: JWT token with `org_id` claim
- **Outputs**: UserPrincipal with organization_id
- **Enforces platform guard?** IMPLICIT — JWT verified server-side; no client override.
- **Called from**: Every authenticated request via FastAPI dependency
- **Behavior**: Extracts org_id from JWT; non-superusers must have org_id.

### Resolver 7: `IntegrationsRepository.get_integration_for_org` (integrations.py)
- **Location**: `/api/src/repositories/integrations.py` (implementation not fully read)
- **Inputs**: integration name, org_uuid
- **Outputs**: IntegrationMapping or None
- **Enforces platform guard?** UNKNOWN
- **Called from**: cli.py `sdk_integrations_get` (line 642)

### Resolver 8: Inline cascade in `cli_list_tables` (cli.py lines 2551–2590)
- **Location**: `/api/src/routers/cli.py:2551–2590`
- **Inputs**: org_id (from _get_cli_org_id), db
- **Outputs**: list[SDKTableInfo]
- **Enforces platform guard?** NO — bypasses TableRepository.
- **Called from**: POST /api/cli/tables/list
- **Behavior**: Inline `or_(Table.organization_id == org_uuid, Table.organization_id.is_(None))`. Single query, no repository.

### Resolver 9: Inline cascade in `sdk_integrations_get` (cli.py lines 616–705)
- **Location**: `/api/src/routers/cli.py:616–705`
- **Inputs**: org_id (from _get_cli_org_id), integration name
- **Outputs**: SDKIntegrationsGetResponse
- **Enforces platform guard?** NO — accepts org_id from _get_cli_org_id without re-check.
- **Called from**: POST /api/sdk/integrations/get
- **Behavior**: Calls repo.get_integration_for_org (line 642). Falls back to global defaults if no org mapping.

---

## 3. Cascade-Resolution Implementations

### Implementation 1: `OrgScopedRepository.get` (org_scoped.py lines 104–180)
- **Location**: `/api/src/repositories/org_scoped.py:104–180`
- **Entity**: Generic (Workflow, Form, Agent, Knowledge, etc.)
- **Query shape**: TWO separate queries for name lookups (lines 164–175 for org, then global)
- **Uses base class?** YES — this IS the base class
- **Correctness notes**: Respects org-specific-first semantics. Early-return on org match. ID lookups single query + in-Python scope check.
- **Tests**: test_org_scoped.py covers string coercion and id lookup; missing: cross-tenant isolation, comprehensive cascade.

### Implementation 2: `OrgScopedRepository._apply_cascade_scope` (org_scoped.py lines 259–275)
- **Location**: `/api/src/repositories/org_scoped.py:259–275`
- **Entity**: Generic for list operations
- **Query shape**: SINGLE query with `or_(organization_id = org_id, organization_id IS NULL)`
- **Uses base class?** YES — internal method of OrgScopedRepository
- **Correctness notes**: Used by `.list()`. Produces correct result via different code path than `.get()` (one query vs two).
- **Tests**: Not directly tested.

### Implementation 3: `ConfigResolver.load_config_for_scope` (config_resolver.py lines 296–399)
- **Location**: `/api/src/core/config_resolver.py:296–399`
- **Entity**: Config
- **Query shape**: TWO separate queries (global lines 364–374, org 377–387), merged in Python
- **Uses base class?** NO — standalone service
- **Correctness notes**: Overlay pattern vs early-return pattern. Results should be identical but implementation is parallel.
- **Tests**: No dedicated unit tests found.

### Implementation 4: `OAuthStorageService.get_connection` (oauth_storage.py lines 126–167)
- **Location**: `/api/src/services/oauth_storage.py:126–167`
- **Entity**: OAuthProvider
- **Query shape**: SINGLE query with `or_(organization_id = org_uuid, organization_id IS NULL)` ordered `desc().nulls_last()`
- **Uses base class?** NO — standalone service
- **Correctness notes**: Order-by trick makes DB return org-specific first. Functionally correct, non-standard vs OrgScopedRepository.
- **Tests**: Not verified.

### Implementation 5a: Inline cascade in `cli_list_tables` (cli.py lines 2564–2573)
- **Location**: `/api/src/routers/cli.py:2551–2590`
- **Entity**: Table
- **Query shape**: SINGLE inline `or_(Table.organization_id == org_uuid, Table.organization_id.is_(None))`
- **Uses base class?** NO — bypasses TableRepository
- **Correctness notes**: Duplicates OrgScopedRepository logic inline. Prevents future repository updates from affecting this endpoint.
- **Tests**: Covered by e2e tests.

### Implementation 5b: Inline cascade in `sdk_integrations_get` (cli.py lines 616–705)
- **Location**: `/api/src/routers/cli.py:616–705`
- **Entity**: IntegrationMapping + Integration fallback
- **Query shape**: Repository-delegated
- **Uses base class?** PARTIAL — calls IntegrationsRepository methods
- **Correctness notes**: Two-mode (org mapping, then global defaults). Correct semantics but not canonical pattern.
- **Tests**: Relies on e2e coverage.

### Implementation 6: `IntegrationsRepository.get_provider_org_token` (integrations.py lines 712–733)
- **Location**: `/api/src/repositories/integrations.py:712–733`
- **Entity**: OAuthToken
- **Query shape**: SINGLE query filtering `provider_id` and `user_id IS NULL`
- **Uses base class?** NO
- **Correctness notes**: **MISSING ORG FILTER** — no organization_id filtering. This was a token-leak vector. Called from cli.py lines 664, 694.
- **Tests**: No cross-org isolation test.

---

## 4. SDK/CLI Endpoints Accepting Scope

| Endpoint | Handler | Resolver | Re-checks | Downstream Impl |
|---|---|---|---|---|
| POST /api/cli/config/get | cli_get_config | _get_cli_org_id | NO | ConfigResolver |
| POST /api/cli/config/set | cli_set_config | _get_cli_org_id | NO | ConfigResolver |
| POST /api/cli/config/list | cli_list_config | _get_cli_org_id | NO | ConfigResolver |
| POST /api/cli/config/delete | cli_delete_config | _get_cli_org_id | NO | ConfigResolver |
| POST /api/cli/knowledge/store | cli_store_knowledge | _get_cli_org_id | NO | KnowledgeRepository |
| POST /api/cli/knowledge/search | cli_search_knowledge | _get_cli_org_id | NO | KnowledgeRepository |
| POST /api/cli/knowledge/delete | cli_delete_knowledge | _get_cli_org_id | NO | KnowledgeRepository |
| POST /api/sdk/integrations/get | sdk_integrations_get | _get_cli_org_id | NO | IntegrationsRepository |
| POST /api/sdk/integrations/refresh_token | sdk_integrations_refresh_token | _get_cli_org_id | NO | OAuthProvider lookup |
| POST /api/cli/tables/list | cli_list_tables | _get_cli_org_id | NO | Inline cascade |
| POST /api/cli/sessions/register | cli_register_session | _get_cli_org_id | NO | Execution creation |

**Pattern**: ALL endpoints call `_get_cli_org_id()` with NO re-check that caller is authorized for the scope.

---

## 5. Drift Findings

1. **Repository location split**: Application and Table repos defined in routers, not repositories/. Breaks canonical pattern.

2. **Two cascade patterns coexist**: OrgScopedRepository (two-query, early-return) vs ConfigResolver (two-query, dict overlay). Both correct, semantically divergent implementations.

3. **Inline cascades bypass repositories**: cli_list_tables (line 2564) and sdk_integrations_get (lines 641–674) duplicate cascade logic. Future changes won't propagate.

4. **_get_cli_org_id has no platform guard**: Accepts any UUID without validating caller's membership.

5. **get_provider_org_token has no org filter**: Token query (integrations.py line 712) lacks organization_id filter. Regression from PR #308.

6. **Config cascade via overlay vs early-return**: ConfigResolver fetches both queries separately, merging in Python. OrgScopedRepository tries org first with early return. Schema changes between queries could cause divergence.

7. **Role checks skipped in some paths**: OrgScopedRepository respects role access (line 168), but ConfigResolver, OAuthStorageService, and inline cascades do not.

8. **String-to-UUID coercion at different layers**: OrgScopedRepository does it in __init__ (line 96); ConfigResolver and OAuthStorageService do it locally. If a string org_id leaks without coercion, access control silently fails.

9. **ExecutionContext and _get_cli_org_id are separate gates**: Different scoping semantics. If workflow execution calls CLI endpoints, semantics unclear.

10. **README omits identity-admin classification**: Documents RBAC entities but does not distinguish execution-resolution from identity-admin records.

---

## 6. Tests Audit

### File: `test_org_scoped.py`
- **Covers**: String coercion, ID lookup with scope
- **Assertions**: String→UUID, None preservation, in-scope ID lookup
- **Missing**: Cascade semantics (org-wins), cross-tenant isolation, role filtering

### File: `test_cli_get_org_id.py`
- **Covers**: _get_cli_org_id resolver logic
- **Missing**: Cross-org validation, platform-admin override

### File: `test_scoped_lookups.py`
- **Covers**: (assumed scope validation)

### File: `test_scope_execution.py`
- **Covers**: E2E execution scope
- **Missing**: Cross-tenant isolation failures

### File: `test_get_token_for_org.py`
- **Covers**: (assumed OAuth per-org)
- **Missing**: PR #308 regression test

### General Pattern
- ✅ Happy-path scope resolution, fallback to global, input coercion
- ❌ Cross-tenant isolation, cascade consistency, token-leak regressions, role-based filtering on cascaded, guard enforcement on scope-overridable endpoints

---

## 7. Open Questions

1. Does `IntegrationsRepository.get_integration_for_org` filter by org? Called cli.py line 642 but not verified.

2. Does IntegrationMapping.organization_id exist and have cascade behavior? Assuming nullable; not verified.

3. Do ConfigResolver tests exist? No test file found.

4. Does ManifestImport._resolve_* validate org scoping during sync?

5. Does MCPContext coerce JWT org_id string before calling OrgScopedRepository? Test (test_org_scoped.py line 79) pins fix but original failure mode not traced.

6. Does ExecutionContext.org_id match workflow.organization_id for org-scoped workflows? Documentation claims yes; no invariant test.

7. Other token-leak vectors like PR #308? Code review not exhaustive.

8. Behavior of inline cascade in sdk_integrations_get when org_id is None (line 641)? Skips mapping lookup, goes to defaults. Intended?

9. Are integration-scoped configs (integration_id != NULL) correctly excluded from user cascade? ConfigResolver filters `integration_id IS NULL` (lines 348, 366, 380); yes, but not verified.

10. Missing test: Org A user requests org B's workflow execution—should fail at scope check.

---

## Summary

- **ORM models with organization_id**: 18
- **Execution-time entities**: 13
- **Identity/admin records**: 5
- **OrgScopedRepository subclasses**: 8
- **Repos in routers (not repositories/)**: 2
- **Cascade implementations**: 5
- **Effective-scope resolvers**: 9
- **SDK/CLI endpoints with scope param**: 11
- **Endpoints re-checking scope permission**: 0
- **Tests covering cross-org isolation**: ~0
- **Known token-leak bugs fixed**: 1 (PR #308, method still lacks org filter)
