"""Unit tests for ``resolve_agent_tools`` MCP-tool integration.

Phase 3 of the external-MCP-client feature wires per-org MCP connections
into the agent toolset surfaced to the LLM. The new behavior we test:

- MCP tools are namespaced ``mcp__<connection_id>__<tool_name>`` so two
  servers exposing the same tool name don't collide.
- Chat callers (``caller_user_id`` not None) see all enabled MCP tools in
  the agent's org. Token resolution happens at dispatch, not planning, so
  a user without per-user creds still sees the tool listed.
- Autonomous callers (``caller_user_id is None``) only see MCP tools
  whose connection has ``available_to_autonomous=True`` AND a usable
  service token. The ``MisconfigError`` path 5 in
  ``mcp_client.auth_resolution`` exists to catch planner bugs, not as a
  normal-operation outcome.
- Disabled tools are filtered regardless of caller.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.security import encrypt_secret
from src.models.enums import AgentAccessLevel
from src.models.orm.agents import Agent
from src.models.orm.external_mcp import (
    AgentMCPConnection,
    MCPConnection,
    MCPConnectionTool,
    MCPServer,
)
from src.models.orm.oauth import OAuthProvider, OAuthToken
from src.models.orm.organizations import Organization
from src.services.execution.agent_helpers import (
    MCP_TOOL_PREFIX,
    parse_mcp_tool_name,
    resolve_agent_tools,
)


def _enc(value: str) -> bytes:
    return encrypt_secret(value).encode()


async def _make_org(db: AsyncSession) -> Organization:
    org = Organization(
        id=uuid4(),
        name=f"mcp-helpers-org-{uuid4().hex[:8]}",
        is_active=True,
        created_by="test@example.com",
    )
    db.add(org)
    await db.flush()
    return org


async def _make_provider(db: AsyncSession) -> OAuthProvider:
    provider = OAuthProvider(
        id=uuid4(),
        provider_name=f"mcp-prov-{uuid4().hex[:8]}",
        oauth_flow_type="client_credentials",
        client_id="cid",
        encrypted_client_secret=encrypt_secret("csec").encode(),
        token_url="https://example.com/oauth/token",
    )
    db.add(provider)
    await db.flush()
    return provider


async def _make_server(db: AsyncSession) -> MCPServer:
    server = MCPServer(
        id=uuid4(),
        name=f"mcp-srv-{uuid4().hex[:8]}",
        server_url="https://vendor.example.com/mcp",
        is_active=True,
    )
    db.add(server)
    await db.flush()
    return server


async def _make_oauth_token(
    db: AsyncSession,
    provider: OAuthProvider,
    *,
    expires_at: datetime | None = None,
) -> OAuthToken:
    if expires_at is None:
        expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
    token = OAuthToken(
        id=uuid4(),
        provider_id=provider.id,
        encrypted_access_token=_enc("access"),
        encrypted_refresh_token=_enc("refresh"),
        expires_at=expires_at,
        scopes=["read"],
    )
    db.add(token)
    await db.flush()
    return token


async def _make_connection(
    db: AsyncSession,
    server: MCPServer,
    org: Organization,
    *,
    available_in_chat: bool = False,
    available_to_autonomous: bool = False,
    service_oauth_token: OAuthToken | None = None,
) -> MCPConnection:
    conn = MCPConnection(
        id=uuid4(),
        server_id=server.id,
        organization_id=org.id,
        client_id="conn-cid",
        encrypted_client_secret="enc-csec",
        available_in_chat=available_in_chat,
        available_to_autonomous=available_to_autonomous,
        service_oauth_token_id=(
            service_oauth_token.id if service_oauth_token else None
        ),
    )
    db.add(conn)
    await db.flush()
    return conn


async def _make_tool(
    db: AsyncSession,
    connection: MCPConnection,
    *,
    tool_name: str,
    enabled: bool = True,
    description: str | None = None,
    input_schema: dict | None = None,
) -> MCPConnectionTool:
    schema: dict = {}
    if description is not None:
        schema["description"] = description
    if input_schema is not None:
        schema["inputSchema"] = input_schema

    tool = MCPConnectionTool(
        id=uuid4(),
        connection_id=connection.id,
        tool_name=tool_name,
        tool_schema=schema,
        enabled=enabled,
    )
    db.add(tool)
    await db.flush()
    return tool


async def _make_agent(
    db: AsyncSession,
    org: Organization | None,
    *,
    granted_connections: "list[MCPConnection] | None" = None,
) -> Agent:
    agent = Agent(
        id=uuid4(),
        name=f"agent-{uuid4().hex[:8]}",
        description="test agent",
        system_prompt="Be a test agent.",
        channels=["chat"],
        access_level=AgentAccessLevel.ROLE_BASED,
        organization_id=org.id if org else None,
        is_active=True,
        knowledge_sources=[],
        system_tools=[],
        created_by="test@example.com",
    )
    db.add(agent)
    await db.flush()
    # New agents are deny-by-default for MCP connections — every test
    # that wants the agent to see MCP tools must grant them explicitly.
    # ``granted_connections=None`` keeps that strict default; pass a list
    # to bind grants for connections the test plans to surface.
    for conn in granted_connections or []:
        db.add(AgentMCPConnection(agent_id=agent.id, connection_id=conn.id))
    await db.flush()
    # ``resolve_agent_tools`` reads ``agent.tools`` and ``agent.delegated_agents``;
    # default lazy loading on those collections triggers a sync IO call
    # inside the async resolve_agent_tools function, so we load them
    # eagerly here.
    await db.refresh(
        agent,
        attribute_names=["tools", "delegated_agents", "mcp_connections"],
    )
    return agent


# ---------------------------------------------------------------------------
# parse_mcp_tool_name
# ---------------------------------------------------------------------------


def test_parse_mcp_tool_name_round_trip():
    """A well-formed MCP qualified name parses back to (UUID, tool_name)."""
    cid = uuid4()
    qualified = f"{MCP_TOOL_PREFIX}{cid}__graph_search"
    parsed = parse_mcp_tool_name(qualified)
    assert parsed is not None
    parsed_id, parsed_name = parsed
    assert parsed_id == cid
    assert parsed_name == "graph_search"


def test_parse_mcp_tool_name_rejects_non_mcp_prefix():
    assert parse_mcp_tool_name("delegate_to_reporter") is None
    assert parse_mcp_tool_name("search_knowledge") is None


def test_parse_mcp_tool_name_rejects_malformed_uuid():
    assert parse_mcp_tool_name(f"{MCP_TOOL_PREFIX}not-a-uuid__tool") is None


def test_parse_mcp_tool_name_rejects_missing_tool_name():
    cid = uuid4()
    # No double-underscore separator after the UUID — entire payload is one
    # segment and parse should reject.
    assert parse_mcp_tool_name(f"{MCP_TOOL_PREFIX}{cid}") is None


def test_parse_mcp_tool_name_handles_underscore_in_tool_name():
    """Tool names with underscores split only on the first ``__``."""
    cid = uuid4()
    qualified = f"{MCP_TOOL_PREFIX}{cid}__halopsa_list_tickets"
    parsed = parse_mcp_tool_name(qualified)
    assert parsed is not None
    _, parsed_name = parsed
    assert parsed_name == "halopsa_list_tickets"


# ---------------------------------------------------------------------------
# resolve_agent_tools — chat caller
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chat_includes_all_enabled_mcp_tools(
    db_session: AsyncSession, seed_user
):
    """A chat caller sees all enabled MCP tools, regardless of which auth
    flag the connection has set. Disabled tools are filtered.

    Each connection in this test uses a distinct server template because
    of the ``UNIQUE(server_id, organization_id)`` constraint on
    ``mcp_connections`` — multiple connections per (server, org) is
    explicitly rejected by the schema."""
    org = await _make_org(db_session)
    provider = await _make_provider(db_session)
    server_chat = await _make_server(db_session)
    server_both = await _make_server(db_session)
    server_auto = await _make_server(db_session)

    # Connection 1: chat-flag only, no service token. Three tools — one
    # enabled-with-schema, one enabled-without-schema, one disabled.
    conn_chat = await _make_connection(
        db_session,
        server_chat,
        org,
        available_in_chat=True,
        available_to_autonomous=False,
    )
    await _make_tool(
        db_session,
        conn_chat,
        tool_name="chat_only_tool",
        description="A chat-only tool",
        input_schema={"type": "object", "properties": {"q": {"type": "string"}}},
    )
    await _make_tool(
        db_session,
        conn_chat,
        tool_name="chat_only_no_schema_tool",
    )
    await _make_tool(
        db_session,
        conn_chat,
        tool_name="disabled_tool",
        enabled=False,
    )

    # Connection 2: both flags + healthy service token, two enabled tools
    service_token = await _make_oauth_token(db_session, provider)
    conn_both = await _make_connection(
        db_session,
        server_both,
        org,
        available_in_chat=True,
        available_to_autonomous=True,
        service_oauth_token=service_token,
    )
    await _make_tool(db_session, conn_both, tool_name="always_on_tool_a")
    await _make_tool(db_session, conn_both, tool_name="always_on_tool_b")

    # Connection 3: autonomous-only flag. Chat caller still sees its
    # tools — token resolution happens at dispatch.
    conn_auto = await _make_connection(
        db_session,
        server_auto,
        org,
        available_in_chat=False,
        available_to_autonomous=True,
        service_oauth_token=service_token,
    )
    await _make_tool(db_session, conn_auto, tool_name="autonomous_only_tool")

    agent = await _make_agent(
        db_session,
        org,
        granted_connections=[conn_chat, conn_both, conn_auto],
    )

    tools, id_map = await resolve_agent_tools(
        agent, db_session, caller_user_id=seed_user.id
    )

    names = {t.name for t in tools}

    # Disabled tool absent
    assert all("disabled_tool" not in n for n in names)

    # All five enabled tools present, namespaced by connection id
    chat_only_qual = f"{MCP_TOOL_PREFIX}{conn_chat.id}__chat_only_tool"
    chat_only_no_schema_qual = (
        f"{MCP_TOOL_PREFIX}{conn_chat.id}__chat_only_no_schema_tool"
    )
    always_on_a_qual = f"{MCP_TOOL_PREFIX}{conn_both.id}__always_on_tool_a"
    always_on_b_qual = f"{MCP_TOOL_PREFIX}{conn_both.id}__always_on_tool_b"
    autonomous_qual = f"{MCP_TOOL_PREFIX}{conn_auto.id}__autonomous_only_tool"

    for q in (
        chat_only_qual,
        chat_only_no_schema_qual,
        always_on_a_qual,
        always_on_b_qual,
        autonomous_qual,
    ):
        assert q in names, f"expected {q} in tool names"

    # id_map maps qualified name -> connection id (NOT workflow id)
    assert id_map[chat_only_qual] == conn_chat.id
    assert id_map[always_on_a_qual] == conn_both.id
    assert id_map[autonomous_qual] == conn_auto.id

    # Description from schema flows through
    chat_only_def = next(t for t in tools if t.name == chat_only_qual)
    assert chat_only_def.description == "A chat-only tool"
    assert chat_only_def.parameters == {
        "type": "object",
        "properties": {"q": {"type": "string"}},
    }

    # Default empty schema when tool_schema lacks inputSchema
    chat_only_no_schema_def = next(
        t for t in tools if t.name == chat_only_no_schema_qual
    )
    assert chat_only_no_schema_def.parameters == {
        "type": "object",
        "properties": {},
    }


# ---------------------------------------------------------------------------
# resolve_agent_tools — autonomous caller
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_autonomous_filters_to_flag_and_healthy_service_token(
    db_session: AsyncSession,
):
    """An autonomous run sees only tools whose connection has
    ``available_to_autonomous=True`` AND a usable service token.

    Set up four connections:
    - Auto flag + healthy service token  → tool included
    - Auto flag + NO service token       → tool excluded (planner filter)
    - Chat flag only                     → tool excluded
    - Auto flag + expired service token (way past refresh window) → excluded
    """
    org = await _make_org(db_session)
    provider = await _make_provider(db_session)
    server_healthy = await _make_server(db_session)
    server_no_token = await _make_server(db_session)
    server_chat = await _make_server(db_session)
    server_expired = await _make_server(db_session)

    # 1. Healthy autonomous connection
    healthy_token = await _make_oauth_token(db_session, provider)
    conn_healthy = await _make_connection(
        db_session,
        server_healthy,
        org,
        available_to_autonomous=True,
        service_oauth_token=healthy_token,
    )
    await _make_tool(db_session, conn_healthy, tool_name="healthy_auto_tool")

    # 2. Autonomous flag but no service token
    conn_no_token = await _make_connection(
        db_session,
        server_no_token,
        org,
        available_to_autonomous=True,
        service_oauth_token=None,
    )
    await _make_tool(db_session, conn_no_token, tool_name="no_token_auto_tool")

    # 3. Chat-only connection
    conn_chat_only = await _make_connection(
        db_session,
        server_chat,
        org,
        available_in_chat=True,
        available_to_autonomous=False,
        service_oauth_token=healthy_token,
    )
    await _make_tool(db_session, conn_chat_only, tool_name="chat_only_tool")

    # 4. Autonomous flag with hard-expired service token
    expired_token = await _make_oauth_token(
        db_session,
        provider,
        expires_at=datetime.now(timezone.utc) - timedelta(hours=2),
    )
    conn_expired = await _make_connection(
        db_session,
        server_expired,
        org,
        available_to_autonomous=True,
        service_oauth_token=expired_token,
    )
    await _make_tool(db_session, conn_expired, tool_name="expired_auto_tool")

    agent = await _make_agent(
        db_session,
        org,
        granted_connections=[
            conn_healthy,
            conn_no_token,
            conn_chat_only,
            conn_expired,
        ],
    )

    tools, id_map = await resolve_agent_tools(
        agent, db_session, caller_user_id=None
    )

    names = {t.name for t in tools}

    # Only the healthy connection's tool should be present
    healthy_qual = f"{MCP_TOOL_PREFIX}{conn_healthy.id}__healthy_auto_tool"
    assert healthy_qual in names
    assert id_map[healthy_qual] == conn_healthy.id

    # All other connections' tools filtered out
    no_token_qual = f"{MCP_TOOL_PREFIX}{conn_no_token.id}__no_token_auto_tool"
    chat_only_qual = f"{MCP_TOOL_PREFIX}{conn_chat_only.id}__chat_only_tool"
    expired_qual = f"{MCP_TOOL_PREFIX}{conn_expired.id}__expired_auto_tool"

    assert no_token_qual not in names
    assert chat_only_qual not in names
    assert expired_qual not in names


@pytest.mark.asyncio
async def test_autonomous_includes_tool_with_recently_expired_token(
    db_session: AsyncSession,
):
    """A token that's only just expired (within the refresh window) is
    still considered usable at planning time. Dispatch will refresh it.

    The hard-expiry filter only excludes connections whose token expired
    more than ``_AUTONOMOUS_TOKEN_HARD_EXPIRY`` (5 minutes) in the past.
    """
    org = await _make_org(db_session)
    provider = await _make_provider(db_session)
    server = await _make_server(db_session)

    just_expired = await _make_oauth_token(
        db_session,
        provider,
        expires_at=datetime.now(timezone.utc) - timedelta(seconds=30),
    )
    conn = await _make_connection(
        db_session,
        server,
        org,
        available_to_autonomous=True,
        service_oauth_token=just_expired,
    )
    await _make_tool(db_session, conn, tool_name="recent_expiry_tool")

    agent = await _make_agent(
        db_session, org, granted_connections=[conn]
    )
    tools, _ = await resolve_agent_tools(agent, db_session, caller_user_id=None)

    qual = f"{MCP_TOOL_PREFIX}{conn.id}__recent_expiry_tool"
    assert qual in {t.name for t in tools}


# ---------------------------------------------------------------------------
# resolve_agent_tools — org scoping
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mcp_tools_org_scoped(db_session: AsyncSession, seed_user):
    """An agent in org A does NOT see MCP tools from org B."""
    org_a = await _make_org(db_session)
    org_b = await _make_org(db_session)
    server = await _make_server(db_session)

    # Connection in org A, tool in org A
    conn_a = await _make_connection(
        db_session, server, org_a, available_in_chat=True
    )
    await _make_tool(db_session, conn_a, tool_name="org_a_tool")

    # Connection in org B (same server template, different org)
    conn_b = await _make_connection(
        db_session, server, org_b, available_in_chat=True
    )
    await _make_tool(db_session, conn_b, tool_name="org_b_tool")

    # Grant both connections to test that the org filter still applies even
    # when an agent is granted access to a connection from a different org
    # — the join's WHERE clause on agent.organization_id should still drop
    # the cross-org connection.
    agent_a = await _make_agent(
        db_session, org_a, granted_connections=[conn_a, conn_b]
    )
    tools, _ = await resolve_agent_tools(
        agent_a, db_session, caller_user_id=seed_user.id
    )
    names = {t.name for t in tools}

    # Org A's tool present, org B's tool absent
    assert any("org_a_tool" in n for n in names)
    assert all("org_b_tool" not in n for n in names)


@pytest.mark.asyncio
async def test_platform_level_agent_gets_no_mcp_tools(
    db_session: AsyncSession, seed_user
):
    """An agent without an organization_id sees no MCP tools — MCP
    connections are strictly per-org per the spec."""
    org = await _make_org(db_session)
    server = await _make_server(db_session)
    conn = await _make_connection(
        db_session, server, org, available_in_chat=True
    )
    await _make_tool(db_session, conn, tool_name="some_mcp_tool")

    # Even attempting to grant the platform agent access to the connection
    # is a no-op — set_mcp_connection_grants refuses when the agent has no
    # organization_id, and the helper here drops the AgentMCPConnection
    # row entirely. We pass the connection anyway to confirm the spec:
    # platform-level agents NEVER see MCP tools.
    platform_agent = await _make_agent(
        db_session, org=None, granted_connections=[conn]
    )
    tools, _ = await resolve_agent_tools(
        platform_agent, db_session, caller_user_id=seed_user.id
    )

    assert all(not t.name.startswith(MCP_TOOL_PREFIX) for t in tools)


# ---------------------------------------------------------------------------
# resolve_agent_tools — per-agent grant filter (deny-by-default)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_agent_with_no_grants_sees_no_mcp_tools(
    db_session: AsyncSession, seed_user
):
    """An agent in an org with enabled MCP connections sees zero MCP tools
    when no grants have been issued. The deny-by-default semantics mean a
    new agent must be explicitly bound to a connection before its tools
    surface."""
    org = await _make_org(db_session)
    server = await _make_server(db_session)
    conn = await _make_connection(
        db_session,
        server,
        org,
        available_in_chat=True,
        available_to_autonomous=True,
    )
    await _make_tool(db_session, conn, tool_name="visible_tool")
    await _make_tool(db_session, conn, tool_name="another_visible_tool")

    # Agent in the same org but with no grants.
    agent = await _make_agent(db_session, org)
    assert agent.mcp_connections == []

    tools, id_map = await resolve_agent_tools(
        agent, db_session, caller_user_id=seed_user.id
    )

    assert all(not t.name.startswith(MCP_TOOL_PREFIX) for t in tools)
    assert all(
        not name.startswith(MCP_TOOL_PREFIX) for name in id_map
    )


@pytest.mark.asyncio
async def test_grant_for_one_connection_filters_out_other_connections(
    db_session: AsyncSession, seed_user
):
    """An agent granted access to connection A sees A's tools but NOT
    connection B's tools, even when B is in the same org and otherwise
    enabled. This is the core "Tech Support gets HaloPSA, Marketing AI
    doesn't" guarantee the join table is supposed to deliver."""
    org = await _make_org(db_session)
    server_a = await _make_server(db_session)
    server_b = await _make_server(db_session)

    conn_a = await _make_connection(
        db_session,
        server_a,
        org,
        available_in_chat=True,
        available_to_autonomous=True,
    )
    await _make_tool(db_session, conn_a, tool_name="a_only_tool")

    conn_b = await _make_connection(
        db_session,
        server_b,
        org,
        available_in_chat=True,
        available_to_autonomous=True,
    )
    await _make_tool(db_session, conn_b, tool_name="b_only_tool")

    # Grant ONLY connection A; B is intentionally not bound.
    agent = await _make_agent(
        db_session, org, granted_connections=[conn_a]
    )

    tools, _ = await resolve_agent_tools(
        agent, db_session, caller_user_id=seed_user.id
    )
    names = {t.name for t in tools}

    a_qual = f"{MCP_TOOL_PREFIX}{conn_a.id}__a_only_tool"
    b_qual = f"{MCP_TOOL_PREFIX}{conn_b.id}__b_only_tool"
    assert a_qual in names
    assert b_qual not in names
