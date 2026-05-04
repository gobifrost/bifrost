"""ORM relationship and round-trip tests for the external MCP client tables.

Touches the live test database via ``db_session`` so the migration's FK + ON
DELETE / ON UPDATE behavior is actually exercised, not just the Python
relationship() declarations.
"""
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from src.models.enums import AgentAccessLevel
from src.models.orm.agents import Agent
from src.models.orm.external_mcp import (
    AgentMCPConnection,
    MCPConnection,
    MCPConnectionTool,
    MCPServer,
    UserMCPCredential,
)
from src.models.orm.oauth import OAuthProvider, OAuthToken
from src.models.orm.organizations import Organization


@pytest.mark.asyncio
async def test_mcp_server_connection_tool_relationships(
    db_session, seed_user
):
    """Create a server + connection + tool, assert all relationships traverse."""
    org = Organization(
        id=uuid4(),
        name=f"mcp-test-org-{uuid4().hex[:8]}",
        is_active=True,
        created_by="test@example.com",
    )
    db_session.add(org)
    await db_session.flush()

    server = MCPServer(
        id=uuid4(),
        name=f"test-mcp-server-{uuid4().hex[:8]}",
        server_url="https://example.com/mcp",
        organization_id=None,
        is_active=True,
    )
    db_session.add(server)
    await db_session.flush()

    connection = MCPConnection(
        id=uuid4(),
        server_id=server.id,
        organization_id=org.id,
        client_id="test-client",
        encrypted_client_secret="encrypted-blob",
        available_in_chat=True,
        available_to_autonomous=False,
    )
    db_session.add(connection)
    await db_session.flush()

    tool = MCPConnectionTool(
        id=uuid4(),
        connection_id=connection.id,
        tool_name="search",
        tool_schema={"type": "object", "properties": {}},
        enabled=True,
        last_seen_at=datetime.now(timezone.utc),
    )
    db_session.add(tool)
    await db_session.flush()

    # Reload server with eager-loaded connections + tools and verify the
    # full graph hangs together.
    result = await db_session.execute(
        select(MCPServer)
        .where(MCPServer.id == server.id)
        .options(selectinload(MCPServer.connections).selectinload(MCPConnection.tools))
    )
    reloaded = result.scalar_one()
    assert len(reloaded.connections) == 1
    loaded_conn = reloaded.connections[0]
    assert loaded_conn.id == connection.id
    assert len(loaded_conn.tools) == 1
    assert loaded_conn.tools[0].tool_name == "search"
    # Reverse direction: connection.server back-populates
    assert loaded_conn.server.id == server.id


@pytest.mark.asyncio
async def test_user_mcp_credential_relationships(db_session, seed_user):
    """Create a user credential row and verify FKs to user / connection / token."""
    org = Organization(
        id=uuid4(),
        name=f"mcp-cred-org-{uuid4().hex[:8]}",
        is_active=True,
        created_by="test@example.com",
    )
    db_session.add(org)
    await db_session.flush()

    server = MCPServer(
        id=uuid4(),
        name=f"cred-mcp-server-{uuid4().hex[:8]}",
        server_url="https://example.com/mcp",
        is_active=True,
    )
    db_session.add(server)
    await db_session.flush()

    provider = OAuthProvider(
        id=uuid4(),
        provider_name=f"mcp-provider-{uuid4().hex[:8]}",
        oauth_flow_type="authorization_code",
        client_id="client-id",
        encrypted_client_secret=b"x",
        token_url="https://example.com/token",
    )
    db_session.add(provider)
    await db_session.flush()

    connection = MCPConnection(
        id=uuid4(),
        server_id=server.id,
        organization_id=org.id,
        client_id="conn-client",
        encrypted_client_secret="encrypted-blob",
    )
    db_session.add(connection)
    await db_session.flush()

    token = OAuthToken(
        id=uuid4(),
        provider_id=provider.id,
        user_id=seed_user.id,
        encrypted_access_token=b"access",
        scopes=["read"],
    )
    db_session.add(token)
    await db_session.flush()

    cred = UserMCPCredential(
        id=uuid4(),
        user_id=seed_user.id,
        connection_id=connection.id,
        oauth_token_id=token.id,
        consent_granted_at=datetime.now(timezone.utc),
        granted_scopes=["read", "offline_access"],
    )
    db_session.add(cred)
    await db_session.flush()

    result = await db_session.execute(
        select(UserMCPCredential).where(UserMCPCredential.id == cred.id)
    )
    reloaded = result.scalar_one()
    assert reloaded.user.id == seed_user.id
    assert reloaded.connection.id == connection.id
    assert reloaded.oauth_token.id == token.id
    assert reloaded.granted_scopes == ["read", "offline_access"]


@pytest.mark.asyncio
async def test_mcp_connection_unique_per_server_and_org(db_session):
    """The (server_id, organization_id) unique constraint blocks duplicates."""
    from sqlalchemy.exc import IntegrityError

    org = Organization(
        id=uuid4(),
        name=f"mcp-unique-org-{uuid4().hex[:8]}",
        is_active=True,
        created_by="test@example.com",
    )
    db_session.add(org)
    await db_session.flush()

    server = MCPServer(
        id=uuid4(),
        name=f"unique-mcp-server-{uuid4().hex[:8]}",
        server_url="https://example.com/mcp",
        is_active=True,
    )
    db_session.add(server)
    await db_session.flush()

    db_session.add(MCPConnection(
        id=uuid4(),
        server_id=server.id,
        organization_id=org.id,
        client_id="first",
        encrypted_client_secret="enc",
    ))
    await db_session.flush()

    db_session.add(MCPConnection(
        id=uuid4(),
        server_id=server.id,
        organization_id=org.id,
        client_id="second",
        encrypted_client_secret="enc",
    ))
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_agent_mcp_connection_relationship(db_session):
    """Create an agent + connection + grant, assert the agent's
    ``mcp_connections`` relationship surfaces the bound connection."""
    org = Organization(
        id=uuid4(),
        name=f"agent-mcp-org-{uuid4().hex[:8]}",
        is_active=True,
        created_by="test@example.com",
    )
    db_session.add(org)
    await db_session.flush()

    server = MCPServer(
        id=uuid4(),
        name=f"agent-mcp-srv-{uuid4().hex[:8]}",
        server_url="https://example.com/mcp",
        is_active=True,
    )
    db_session.add(server)
    await db_session.flush()

    connection = MCPConnection(
        id=uuid4(),
        server_id=server.id,
        organization_id=org.id,
        client_id="grant-client",
        encrypted_client_secret="encrypted-blob",
    )
    db_session.add(connection)
    await db_session.flush()

    agent = Agent(
        id=uuid4(),
        name=f"grant-agent-{uuid4().hex[:8]}",
        system_prompt="Be a test agent.",
        channels=["chat"],
        access_level=AgentAccessLevel.ROLE_BASED,
        organization_id=org.id,
        is_active=True,
        knowledge_sources=[],
        system_tools=[],
        created_by="test@example.com",
    )
    db_session.add(agent)
    await db_session.flush()

    # Initially no grants → empty list.
    await db_session.refresh(agent, attribute_names=["mcp_connections"])
    assert agent.mcp_connections == []

    db_session.add(
        AgentMCPConnection(agent_id=agent.id, connection_id=connection.id)
    )
    await db_session.flush()
    db_session.expire(agent, ["mcp_connections"])

    # Reload via secondary relationship and verify it's surfaced.
    result = await db_session.execute(
        select(Agent)
        .where(Agent.id == agent.id)
        .options(selectinload(Agent.mcp_connections))
    )
    reloaded_agent = result.scalar_one()
    assert len(reloaded_agent.mcp_connections) == 1
    assert reloaded_agent.mcp_connections[0].id == connection.id

    # And the reverse: query through the join table directly. There's
    # intentionally no MCPConnection.agents back-relationship — closing
    # that cycle creates a module-level import loop CodeQL flags.
    result = await db_session.execute(
        select(AgentMCPConnection.agent_id)
        .where(AgentMCPConnection.connection_id == connection.id)
    )
    bound_agent_ids = [row[0] for row in result.all()]
    assert bound_agent_ids == [agent.id]


@pytest.mark.asyncio
async def test_agent_mcp_grant_cascade_on_agent_delete(db_session):
    """Deleting an agent cascades to its grant rows so the join table doesn't
    accumulate orphaned bindings."""
    from sqlalchemy import delete as sa_delete, select as sa_select

    org = Organization(
        id=uuid4(),
        name=f"cascade-org-{uuid4().hex[:8]}",
        is_active=True,
        created_by="test@example.com",
    )
    db_session.add(org)
    server = MCPServer(
        id=uuid4(),
        name=f"cascade-srv-{uuid4().hex[:8]}",
        server_url="https://example.com/mcp",
        is_active=True,
    )
    db_session.add(server)
    await db_session.flush()

    connection = MCPConnection(
        id=uuid4(),
        server_id=server.id,
        organization_id=org.id,
        client_id="cascade-client",
        encrypted_client_secret="enc",
    )
    db_session.add(connection)

    agent = Agent(
        id=uuid4(),
        name=f"cascade-agent-{uuid4().hex[:8]}",
        system_prompt="x",
        channels=["chat"],
        access_level=AgentAccessLevel.ROLE_BASED,
        organization_id=org.id,
        is_active=True,
        knowledge_sources=[],
        system_tools=[],
        created_by="test@example.com",
    )
    db_session.add(agent)
    await db_session.flush()

    db_session.add(
        AgentMCPConnection(agent_id=agent.id, connection_id=connection.id)
    )
    await db_session.flush()

    # Delete the agent and confirm the grant goes with it.
    await db_session.execute(sa_delete(Agent).where(Agent.id == agent.id))
    await db_session.flush()

    remaining = await db_session.execute(
        sa_select(AgentMCPConnection).where(
            AgentMCPConnection.agent_id == agent.id
        )
    )
    assert remaining.scalars().first() is None


@pytest.mark.asyncio
async def test_migration_backfill_grants_org_matched_pairs(db_session):
    """Replay the migration's backfill SQL: every existing (agent,
    connection) pair whose orgs match should be granted, with
    ``granted_by = NULL`` so the audit log can distinguish backfilled
    rows from explicit ones.

    Cross-org pairs and platform-level agents (org_id IS NULL) must be
    skipped, even when other connections exist.
    """
    from sqlalchemy import select as sa_select

    org_a = Organization(
        id=uuid4(),
        name=f"backfill-a-{uuid4().hex[:8]}",
        is_active=True,
        created_by="test@example.com",
    )
    org_b = Organization(
        id=uuid4(),
        name=f"backfill-b-{uuid4().hex[:8]}",
        is_active=True,
        created_by="test@example.com",
    )
    db_session.add_all([org_a, org_b])
    server = MCPServer(
        id=uuid4(),
        name=f"backfill-srv-{uuid4().hex[:8]}",
        server_url="https://example.com/mcp",
        is_active=True,
    )
    db_session.add(server)
    server_2 = MCPServer(
        id=uuid4(),
        name=f"backfill-srv-2-{uuid4().hex[:8]}",
        server_url="https://example.com/mcp",
        is_active=True,
    )
    db_session.add(server_2)
    await db_session.flush()

    conn_a1 = MCPConnection(
        id=uuid4(),
        server_id=server.id,
        organization_id=org_a.id,
        client_id="a1",
        encrypted_client_secret="enc",
    )
    conn_a2 = MCPConnection(
        id=uuid4(),
        server_id=server_2.id,
        organization_id=org_a.id,
        client_id="a2",
        encrypted_client_secret="enc",
    )
    conn_b1 = MCPConnection(
        id=uuid4(),
        server_id=server.id,
        organization_id=org_b.id,
        client_id="b1",
        encrypted_client_secret="enc",
    )
    db_session.add_all([conn_a1, conn_a2, conn_b1])

    agent_a = Agent(
        id=uuid4(),
        name=f"backfill-agent-a-{uuid4().hex[:8]}",
        system_prompt="x",
        channels=["chat"],
        access_level=AgentAccessLevel.ROLE_BASED,
        organization_id=org_a.id,
        is_active=True,
        knowledge_sources=[],
        system_tools=[],
        created_by="test@example.com",
    )
    agent_b = Agent(
        id=uuid4(),
        name=f"backfill-agent-b-{uuid4().hex[:8]}",
        system_prompt="x",
        channels=["chat"],
        access_level=AgentAccessLevel.ROLE_BASED,
        organization_id=org_b.id,
        is_active=True,
        knowledge_sources=[],
        system_tools=[],
        created_by="test@example.com",
    )
    agent_global = Agent(
        id=uuid4(),
        name=f"backfill-agent-global-{uuid4().hex[:8]}",
        system_prompt="x",
        channels=["chat"],
        access_level=AgentAccessLevel.ROLE_BASED,
        organization_id=None,
        is_active=True,
        knowledge_sources=[],
        system_tools=[],
        created_by="test@example.com",
    )
    db_session.add_all([agent_a, agent_b, agent_global])
    await db_session.flush()

    # Replay the migration's backfill SQL against the live test DB.
    # Scoped to the agents we just added so this test doesn't conflict
    # with any other rows already in the DB.
    from sqlalchemy import text

    await db_session.execute(
        text(
            """
            INSERT INTO agent_mcp_connections (
                agent_id, connection_id, granted_at, granted_by
            )
            SELECT a.id, c.id, NOW(), NULL
            FROM agents a
            JOIN mcp_connections c
              ON c.organization_id = a.organization_id
            WHERE a.organization_id IS NOT NULL
              AND a.id IN (:agent_a, :agent_b, :agent_global)
              AND c.id IN (:conn_a1, :conn_a2, :conn_b1)
            ON CONFLICT (agent_id, connection_id) DO NOTHING
            """
        ),
        {
            "agent_a": agent_a.id,
            "agent_b": agent_b.id,
            "agent_global": agent_global.id,
            "conn_a1": conn_a1.id,
            "conn_a2": conn_a2.id,
            "conn_b1": conn_b1.id,
        },
    )

    # Agent A: granted both org-A connections, neither in org B
    grants_a = await db_session.execute(
        sa_select(AgentMCPConnection).where(
            AgentMCPConnection.agent_id == agent_a.id
        )
    )
    a_rows = list(grants_a.scalars().all())
    assert {r.connection_id for r in a_rows} == {conn_a1.id, conn_a2.id}
    # All backfilled rows have granted_by = NULL.
    assert all(r.granted_by is None for r in a_rows)

    # Agent B: only its own org's connection.
    grants_b = await db_session.execute(
        sa_select(AgentMCPConnection).where(
            AgentMCPConnection.agent_id == agent_b.id
        )
    )
    b_rows = list(grants_b.scalars().all())
    assert {r.connection_id for r in b_rows} == {conn_b1.id}

    # Platform-level agent: NEVER backfilled.
    grants_global = await db_session.execute(
        sa_select(AgentMCPConnection).where(
            AgentMCPConnection.agent_id == agent_global.id
        )
    )
    assert grants_global.scalars().first() is None
