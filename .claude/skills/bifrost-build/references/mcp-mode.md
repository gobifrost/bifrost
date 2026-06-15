# MCP-Only Mode

MCP-only mode applies when there is no local source workspace — for example, when Claude is assisting a user who has connected Bifrost via MCP without a local repo clone, or when building inside a cloud agent without file access.

In MCP-only mode, all entity creation and content editing happens through MCP tools. There is no `bifrost` CLI available and no workspace to write `.py` files to.

---

## When MCP-Only Applies

- No local source checkout (no `workflows/` or `apps/` directory available)
- Operating as a cloud agent connected to Bifrost via MCP
- Building or modifying forms, agents, tables, and configs that do not require custom Python code

For content that requires Python logic (custom `@workflow` functions), the user must have a local workspace. MCP-mode can scaffold and register once the user provides the workspace path.

---

## Discovery Flow

Before creating anything, discover what exists:

1. `list_workflows` — see registered workflows (potential form targets)
2. `list_forms` — see existing forms
3. `list_agents` — see existing agents
4. `list_tables` — see existing tables
5. `list_configs` — see existing configs
6. `list_apps` — see existing apps

---

## Verified MCP Tool Names

These are the real tool names as registered in `api/src/services/mcp_server/tools/`. Do not invent or guess tool names.

### Workflows
- `list_workflows` — list registered workflows
- `get_workflow` — get a single workflow by UUID or name
- `register_workflow` — register a decorated function from a workspace path
- `execute_workflow` — execute a workflow by ID or name
- `validate_workflow` — validate a workflow file
- `update_workflow` — update workflow metadata (thin HTTP wrapper)
- `delete_workflow` — delete a workflow (thin HTTP wrapper)
- `grant_workflow_role` — grant a role access (thin HTTP wrapper)
- `revoke_workflow_role` — revoke a role's access (thin HTTP wrapper)

### Forms
- `list_forms` — list all forms
- `get_form` — get a form with full field detail
- `create_form` — create a form with fields linked to a workflow
- `update_form` — update a form's properties or fields

### Agents
- `list_agents` — list agents
- `get_agent` — get an agent's full config
- `create_agent` — create an agent with tools/delegations/knowledge
- `update_agent` — update an agent (full replacement of tool_ids/delegated_agent_ids lists)
- `delete_agent` — delete an agent

### Apps
- `list_apps` — list apps with file summaries
- `get_app` — get an app's full config
- `create_app` — create an app
- `update_app` — update app metadata
- `publish_app` — publish an app (makes the draft live)
- `replace_app` — repoint an app's source directory
- `validate_app` — validate an app's source before publish
- `push_files` — push source files into an app
- `get_app_dependencies` — get app npm dependencies
- `update_app_dependencies` — update app npm dependencies

### Content Editing (code editor tools)
- `list_content` — list files in the workspace
- `search_content` — search file content
- `read_content_lines` — read specific lines from a file
- `get_content` — get a file's full content
- `patch_content` — apply a patch to a file (preferred for edits)
- `replace_content` — replace a file's full content
- `delete_content` — delete a file

### Tables
- `list_tables` — list tables
- `get_table` — get a table's config and schema
- `create_table` — create a table
- `update_table` — update a table
- `delete_table` — delete a table

### Configs
- `list_configs` — list configs
- `get_config` — get a config value
- `create_config` — create a config entry
- `update_config` — update a config entry
- `delete_config` — delete a config entry

### Integrations
- `list_integrations` — list integrations
- `get_integration` — get an integration
- `create_integration` — create an integration
- `update_integration` — update an integration
- `add_integration_mapping` — add a per-org mapping
- `update_integration_mapping` — update a per-org mapping

### Roles
- `list_roles`, `get_role`, `create_role`, `update_role`, `delete_role`

### Organizations
- `list_organizations`, `get_organization`, `create_organization`, `update_organization`, `delete_organization`

### Claims
- `list_claims`, `get_claim`, `create_claim`, `update_claim`, `delete_claim`

### Events
- `list_event_sources`, `get_event_source`, `create_event_source`, `update_event_source`, `delete_event_source`
- `list_event_subscriptions`, `create_event_subscription`, `update_event_subscription`, `delete_event_subscription`
- `list_webhook_adapters`

### Executions
- `list_executions` — list executions with filters
- `get_execution` — get a specific execution record

### Knowledge
- `search_knowledge` — semantic search in the knowledge store

---

## Important Caveats

**Existing drift:** The form, agent, table, app, and event MCP tools predate the thin-HTTP-wrapper pattern and contain diverged logic (different permission models, missing side effects). The roles, configs, integrations, organizations, and workflow lifecycle tools (`update_workflow` etc.) are thin wrappers that call the REST endpoints and are always safe. See `docs/plans/2026-04-18-mcp-router-reconciliation.md` for the catalog.

**New tools must be thin wrappers.** Any new MCP tool must call the REST endpoints via `_http_bridge.call_rest` — no direct ORM access, no repository imports. Enforced by `api/tests/unit/test_mcp_thin_wrapper.py`.

**MCP authenticates as the user directly** and does not follow the engine-sentinel pattern.
