# MCP Agent Gateway

Issue: [#528](https://github.com/gobifrost/bifrost/issues/528)

## Problem

The unscoped `/mcp` endpoint currently flattens and deduplicates every tool
available through every agent the caller can access. A caller with dozens of
agents can receive more than one hundred native tool definitions during
`tools/list`.

That has four undesirable consequences:

1. Tool schemas consume model context before the user asks a question.
2. The flattened catalog loses the agent instructions and provenance that
   explain how and why a tool should be used.
3. Agent prompt changes are visible only after MCP initialization runs again.
4. Workflow schema changes depend on both process-local FastMCP refreshes and
   client-side tool-list refreshes.

The agent-scoped `/mcp/{agent_id}` endpoint does not have the same catalog
scaling problem. It should remain the native, schema-rich MCP surface.

## Decision

Replace the unscoped `/mcp` tool surface with four stable gateway tools:

- `bifrost_find_agents`
- `bifrost_get_agent`
- `bifrost_get_tool_schema`
- `bifrost_execute_tool`

Keep `/mcp/{agent_id}` behavior unchanged.

This is progressive discovery implemented by the Bifrost MCP server. The wire
protocol remains MCP: the gateway operations are normal MCP tools, and every
result uses the existing `ToolResult` structured-content envelope.

## Non-goals

- Do not remove or change the agent-scoped endpoint.
- Do not dynamically mutate the MCP client's registered tool list.
- Do not run an extra model inside agent discovery.
- Do not add an `expected_revision` handshake in the proof of concept.
- Do not silently coerce invalid tool arguments.
- Do not retain the old unscoped aggregate tool surface under the default URL.

## Endpoint behavior

The existing `AgentScopeMCPMiddleware` continues to identify
`/mcp/{agent_id}` and rewrite it internally to `/mcp`.

`ToolFilterMiddleware` branches on that request scope:

| Request | `initialize.instructions` | `tools/list` | `tools/call` authorization |
|---|---|---|---|
| `/mcp` | Static gateway workflow instructions | Exactly the four gateway tools | Only the four gateway tools |
| `/mcp/{agent_id}` | Shared conditional server instructions | Existing native agent tool list | Existing per-agent tool authorization |

All built-in and workflow tools remain registered in FastMCP because the same
server still serves agent-scoped URLs. They are not visible or directly
callable through unscoped `/mcp`.

FastMCP freezes initialization options before MCP middleware runs, so a single
mounted server cannot safely serialize different instruction strings per
request path. The shared text explicitly applies only when the four gateway
tools are present. Agent-scoped clients retain their native tool surface; the
default gateway obtains live agent instructions through `bifrost_get_agent`.

## Gateway workflow

### 1. Find agents

`bifrost_find_agents(query: str | None = None, limit: int = 10)`

- Loads active agents through `AgentRepository`, preserving canonical
  organization, private-owner, external-user, and role access behavior.
- A platform administrator searches all active agents.
- An empty query returns the first accessible agents in deterministic name
  order.
- A query ranks accessible agents with deterministic lexical scoring over
  name, description, and instructions. Instructions affect ranking but are not
  returned by this operation.
- Results contain only `id`, `name`, and `description`.
- `limit` is bounded to prevent a second catalog explosion.

### 2. Get an agent

`bifrost_get_agent(agent_id: str)`

- Repeats the canonical agent access check.
- Reads current agent instructions on every call.
- Resolves the current agent tool catalog through `resolve_agent_tools`.
- Applies the platform MCP allowlist/blocklist to underlying tool names.
- Returns:
  - agent identity and description;
  - live instructions;
  - compact tool entries containing `tool_ref`, name, description, and source;
  - no full tool schemas.

### 3. Get one tool schema

`bifrost_get_tool_schema(agent_id: str, tool_ref: str)`

- Repeats agent access and live tool resolution.
- Locates the current tool represented by the agent-bound reference.
- Returns its name, source, description, and exact input JSON Schema.

### 4. Execute one tool

`bifrost_execute_tool(agent_id: str, tool_ref: str, arguments: dict)`

- Repeats agent access and live tool resolution.
- Rejects missing or stale references.
- Validates `arguments` against the current JSON Schema before dispatch.
- Dispatches according to the resolved source:
  - system and knowledge tools: existing registered system-tool function;
  - workflow tools: `execution.service.execute_tool`;
  - delegation tools: `AutonomousAgentExecutor`;
  - external MCP tools: `mcp_client.dispatch.invoke`.
- Uses the authenticated MCP caller identity for downstream execution and
  OAuth resolution.
- Returns resolved agent/tool provenance, result, and duration.

The gateway logs the authenticated caller, resolved agent, resolved tool name,
source, success/failure, and duration. Existing workflow, agent-run, and
external-MCP execution records continue to provide source-specific audit data.

## Tool references

Tool references are deterministic UUIDv5 values derived from:

`gateway namespace + agent UUID + canonical source identity`

Canonical source identities are:

- system/knowledge: system tool name;
- workflow: workflow UUID;
- delegation: delegated agent UUID;
- external MCP: connection UUID plus remote tool name.

The reference is a lookup key, not a bearer capability. Every schema lookup and
execution still performs full live authorization.

This keeps references stable across process restarts and across ordinary
workflow or delegated-agent renames. Removing the grant or changing the remote
tool identity makes the old reference unresolvable.

## Access model

Agent discovery and lookup use `AgentRepository`, not the older MCP
`MCPToolAccessService` access implementation. This preserves private-owner
access and avoids cloning role logic.

Access to an agent is the gateway capability boundary: its configured tool
grants define the catalog available through that agent. Each underlying
dispatcher still enforces its own organization, policy, OAuth, and remote-server
constraints.

The platform-wide MCP configuration remains authoritative:

- `enabled=false` prevents MCP authentication as it does today;
- `allowed_tool_ids` limits underlying gateway tools;
- `blocked_tool_ids` removes underlying gateway tools.

The four gateway operations themselves stay present whenever MCP is enabled.

## Validation and errors

Add `jsonschema` as a direct runtime dependency and select the appropriate
validator for each schema.

Argument validation failures return structured content:

```json
{
  "code": "INVALID_ARGUMENTS",
  "message": "Arguments do not match the live tool schema.",
  "agent_id": "...",
  "tool_ref": "...",
  "tool_name": "...",
  "retryable": true,
  "issues": [
    {
      "path": "/start_date",
      "message": "'today' is not a 'date'",
      "validator": "format",
      "expected": "date"
    }
  ],
  "input_schema": {}
}
```

Other stable error codes:

- `AGENT_NOT_FOUND_OR_FORBIDDEN`
- `TOOL_NOT_FOUND_OR_FORBIDDEN`
- `TOOL_SCHEMA_INVALID`
- `TOOL_EXECUTION_FAILED`
- `NEEDS_REAUTH`

No input coercion or automatic retry occurs in the server.

## Implementation layout

- `api/src/services/mcp_server/gateway.py`
  - accessible-agent loading and search;
  - live tool resolution and reference construction;
  - schema validation;
  - source-specific dispatch orchestration.
- `api/src/services/mcp_server/tools/gateway.py`
  - four thin FastMCP-to-REST wrappers and their public descriptions.
- `api/src/services/mcp_server/tools/__init__.py`
  - registers gateway tools separately from agent-assignable system tools.
- `api/src/services/mcp_server/middleware.py`
  - global gateway versus scoped-native list/call behavior.
- `api/src/services/mcp_server/server.py`
  - registers the gateway alongside native tools and supplies conditional
    initialization instructions.
- `api/src/routers/mcp.py`
  - canonical authenticated gateway REST endpoints;
  - reports the new default gateway surface accurately.

The gateway tools are intentionally not added to `get_system_tools`; agents
cannot grant the discovery gateway to themselves.

## Test plan

### Unit tests

Add focused tests for:

- deterministic and agent-bound tool references;
- search ranking and result limits;
- canonical access denial;
- tool-source classification;
- MCP allowlist/blocklist application;
- valid and invalid JSON-Schema arguments;
- system, workflow, delegation, and external-MCP dispatch routing;
- structured external-MCP reauthentication errors;
- global middleware listing/call restrictions;
- unchanged per-agent middleware behavior.

### MCP end-to-end tests

Create active agents and drive JSON-RPC over Streamable HTTP:

1. `/mcp tools/list` returns exactly the four gateway tools.
2. `/mcp/{agent_id} tools/list` still returns that agent's native tools and no
   gateway tools.
3. Agent search returns a relevant accessible agent and hides an inaccessible
   agent.
4. `get_agent` returns live instructions and compact references.
5. Updating an agent prompt is visible on the next `get_agent` without a new
   MCP initialization.
6. `get_tool_schema` returns the selected live schema.
7. `execute_tool` successfully invokes a harmless system tool.
8. Invalid arguments produce `INVALID_ARGUMENTS` with JSON-pointer paths.
9. Guessing another agent's ID/reference cannot execute its tool.

### Regression and quality checks

- Targeted gateway unit and MCP E2E tests.
- Existing MCP protocol, scoped lookup, access-matrix, and knowledge-scoping
  tests.
- Full backend unit and E2E suite.
- API pyright and ruff.
- OpenAPI/type generation followed by client TypeScript and lint checks.
- Client unit suite; no Playwright run is required because the feature has no
  UI change.

## Live model proof

The isolated worktree debug stack was seeded with an arithmetic agent whose
single workflow accepts two required integers. An external host loaded the
server's `initialize` result and `tools/list`, then gave those four live schemas
to OpenRouter model `deepseek/deepseek-v4-pro`.

Task: add 17 and 25 using the Bifrost capability gateway.

Observed model-selected sequence:

1. `bifrost_find_agents(query="add two numbers arithmetic addition")`
2. `bifrost_get_agent(agent_id=...)`
3. `bifrost_get_tool_schema(agent_id=..., tool_ref=...)`
4. `bifrost_execute_tool(..., arguments={"a": 17, "b": 25})`

The model saw exactly four tools, selected the intended agent without its name
being provided, inspected the schema, executed the stable agent-bound
reference, and reported `42`.

Run metrics:

- prompt tokens: 1,987 (1,024 cached);
- completion tokens: 217 (16 reasoning);
- total tokens: 2,204;
- reported OpenRouter cost: `$0.002098984`;
- gateway calls: 4;
- schema-validation retries: 0.

A separate call on the same live stack passed `b="twenty-five"` and received
`INVALID_ARGUMENTS` with the current schema; retrying with `b=25` returned
`42`. The automated wire E2E test pins that repairable error contract and live
revocation behavior.

## Rollout

This change intentionally breaks the default endpoint. Existing clients that
call native tool names through `/mcp` must adopt the gateway flow or move to an
agent-scoped URL.

The per-agent endpoint remains the compatibility and high-fidelity path. No
legacy unscoped aggregate alias is introduced in this change.
