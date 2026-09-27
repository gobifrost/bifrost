"""
Agents E2E Tests.

Tests agent CRUD operations and role assignment.
"""

import logging
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

from src.models.orm.agents import Agent, AgentRole
from src.models.orm.agent_runs import AgentRun

logger = logging.getLogger(__name__)


class TestAgentsCRUD:
    """Test agent CRUD operations."""

    def test_list_agents_empty(
        self,
        e2e_client,
        platform_admin,
    ):
        """Test listing agents when none exist."""
        response = e2e_client.get(
            "/api/agents",
            headers=platform_admin.headers,
        )
        assert response.status_code == 200
        # May have pre-existing agents from other tests
        data = response.json()
        assert isinstance(data, list)

    def test_create_agent(
        self,
        e2e_client,
        platform_admin,
    ):
        """Test creating an agent."""
        response = e2e_client.post(
            "/api/agents",
            json={
                "name": "Test Assistant",
                "description": "A helpful test assistant",
                "system_prompt": "You are a helpful assistant for testing.",
                "channels": ["chat"],
                "access_level": "authenticated",
            },
            headers=platform_admin.headers,
        )
        assert response.status_code == 201, f"Create agent failed: {response.text}"

        data = response.json()
        assert data["name"] == "Test Assistant"
        assert data["description"] == "A helpful test assistant"
        assert data["system_prompt"] == "You are a helpful assistant for testing."
        assert data["is_active"] is True
        assert "id" in data

    def test_list_agent_summaries_include_assigned_role_ids(
        self,
        e2e_client,
        platform_admin,
    ):
        role_resp = e2e_client.post(
            "/api/roles",
            json={"name": f"Agent List Role {uuid4().hex[:8]}", "permissions": {}},
            headers=platform_admin.headers,
        )
        assert role_resp.status_code == 201, role_resp.text
        role_id = role_resp.json()["id"]

        create_resp = e2e_client.post(
            "/api/agents",
            json={
                "name": f"List Role Agent {uuid4().hex[:8]}",
                "description": "Agent list role regression test",
                "system_prompt": "You are a test assistant.",
                "channels": ["chat"],
                "access_level": "role_based",
                "role_ids": [role_id],
            },
            headers=platform_admin.headers,
        )
        assert create_resp.status_code == 201, create_resp.text
        agent_id = create_resp.json()["id"]

        try:
            list_resp = e2e_client.get(
                "/api/agents",
                headers=platform_admin.headers,
            )
            assert list_resp.status_code == 200, list_resp.text
            listed = next(item for item in list_resp.json() if item["id"] == agent_id)
            assert listed["role_ids"] == [role_id]
        finally:
            e2e_client.delete(f"/api/agents/{agent_id}", headers=platform_admin.headers)
            e2e_client.delete(f"/api/roles/{role_id}", headers=platform_admin.headers)

    def test_get_agent(
        self,
        e2e_client,
        platform_admin,
        test_agent,
    ):
        """Test getting an agent by ID."""
        response = e2e_client.get(
            f"/api/agents/{test_agent['id']}",
            headers=platform_admin.headers,
        )
        assert response.status_code == 200

        data = response.json()
        assert data["id"] == test_agent["id"]
        assert data["name"] == test_agent["name"]

    def test_update_agent(
        self,
        e2e_client,
        platform_admin,
        test_agent,
    ):
        """Test updating an agent."""
        response = e2e_client.put(
            f"/api/agents/{test_agent['id']}",
            json={
                "name": "Updated Assistant",
                "description": "An updated description",
            },
            headers=platform_admin.headers,
        )
        assert response.status_code == 200, f"Update agent failed: {response.text}"

        data = response.json()
        assert data["name"] == "Updated Assistant"
        assert data["description"] == "An updated description"

    @pytest.mark.asyncio
    async def test_update_agent_to_private_sets_owner_and_clears_roles(
        self,
        e2e_client,
        platform_admin,
        db_session,
    ):
        """Admin updates to private should leave an owner-only agent."""
        role_resp = e2e_client.post(
            "/api/roles",
            json={"name": f"Agent Private Role {uuid4().hex[:8]}", "permissions": {}},
            headers=platform_admin.headers,
        )
        assert role_resp.status_code == 201, role_resp.text
        role_id = role_resp.json()["id"]
        create_resp = e2e_client.post(
            "/api/agents",
            json={
                "name": f"Role Based Transition {uuid4().hex[:8]}",
                "system_prompt": "You are a role-based transition test agent.",
                "access_level": "role_based",
                "role_ids": [role_id],
            },
            headers=platform_admin.headers,
        )
        assert create_resp.status_code == 201, create_resp.text
        agent_id = create_resp.json()["id"]

        try:
            assert create_resp.json()["role_ids"] == [role_id]

            private_update = e2e_client.put(
                f"/api/agents/{agent_id}",
                json={"access_level": "private"},
                headers=platform_admin.headers,
            )
            assert private_update.status_code == 200, private_update.text
            body = private_update.json()
            assert body["access_level"] == "private"
            assert body["owner_user_id"] == str(platform_admin.user_id)
            assert body["role_ids"] == []

            db_session.expire_all()
            agent = (
                await db_session.execute(
                    select(Agent).where(Agent.id == UUID(agent_id))
                )
            ).scalar_one()
            role_count = await db_session.scalar(
                select(func.count())
                .select_from(AgentRole)
                .where(AgentRole.agent_id == UUID(agent_id))
            )
            assert agent.owner_user_id == platform_admin.user_id
            assert role_count == 0
        finally:
            e2e_client.delete(f"/api/agents/{agent_id}", headers=platform_admin.headers)
            e2e_client.delete(f"/api/roles/{role_id}", headers=platform_admin.headers)

    @pytest.mark.asyncio
    async def test_update_private_agent_to_shared_clears_owner(
        self,
        e2e_client,
        platform_admin,
        db_session,
    ):
        """Admin updates away from private should remove owner-only scoping."""
        create_resp = e2e_client.post(
            "/api/agents",
            json={
                "name": f"Private Transition {uuid4().hex[:8]}",
                "system_prompt": "You are a private transition test agent.",
                "access_level": "private",
            },
            headers=platform_admin.headers,
        )
        assert create_resp.status_code == 201, create_resp.text
        agent_id = create_resp.json()["id"]

        try:
            assert create_resp.json()["owner_user_id"] == str(platform_admin.user_id)

            shared_update = e2e_client.put(
                f"/api/agents/{agent_id}",
                json={"access_level": "authenticated"},
                headers=platform_admin.headers,
            )
            assert shared_update.status_code == 200, shared_update.text
            body = shared_update.json()
            assert body["access_level"] == "authenticated"
            assert body["owner_user_id"] is None

            db_session.expire_all()
            agent = (
                await db_session.execute(select(Agent).where(Agent.id == UUID(agent_id)))
            ).scalar_one()
            assert agent.owner_user_id is None
        finally:
            e2e_client.delete(f"/api/agents/{agent_id}", headers=platform_admin.headers)

    @pytest.mark.asyncio
    async def test_admin_edit_existing_private_agent_preserves_owner(
        self,
        e2e_client,
        platform_admin,
        org1_user,
    ):
        """Admin edits should not steal an already-private agent from its owner."""
        create_resp = e2e_client.post(
            "/api/agents",
            json={
                "name": f"Owner Preserve {uuid4().hex[:8]}",
                "system_prompt": "You are an owner preservation test agent.",
                "access_level": "private",
            },
            headers=org1_user.headers,
        )
        assert create_resp.status_code == 201, create_resp.text
        agent_id = create_resp.json()["id"]
        assert create_resp.json()["owner_user_id"] == str(org1_user.user_id)

        try:
            update_resp = e2e_client.put(
                f"/api/agents/{agent_id}",
                json={
                    "name": "Owner Preserve Edited",
                    "access_level": "private",
                    "role_ids": [],
                },
                headers=platform_admin.headers,
            )
            assert update_resp.status_code == 200, update_resp.text
            body = update_resp.json()
            assert body["name"] == "Owner Preserve Edited"
            assert body["access_level"] == "private"
            assert body["owner_user_id"] == str(org1_user.user_id)
            assert body["role_ids"] == []
        finally:
            e2e_client.delete(f"/api/agents/{agent_id}", headers=platform_admin.headers)

    def test_delete_agent(
        self,
        e2e_client,
        platform_admin,
        test_agent,
    ):
        """Test permanently deleting an agent."""
        response = e2e_client.delete(
            f"/api/agents/{test_agent['id']}",
            headers=platform_admin.headers,
        )
        assert response.status_code == 204

        # Verify the row no longer exists.
        response = e2e_client.get(
            f"/api/agents/{test_agent['id']}",
            headers=platform_admin.headers,
        )
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_agent_removes_run_history(
        self,
        e2e_client,
        platform_admin,
        db_session,
    ):
        """Permanent deletion cascades through the agent's run history."""
        create_response = e2e_client.post(
            "/api/agents",
            json={
                "name": f"Delete Cascade {uuid4().hex[:8]}",
                "system_prompt": "You are a deletion cascade test agent.",
                "access_level": "authenticated",
            },
            headers=platform_admin.headers,
        )
        assert create_response.status_code == 201, create_response.text
        agent_id = UUID(create_response.json()["id"])

        db_session.add(
            AgentRun(
                agent_id=agent_id,
                trigger_type="test",
                status="completed",
                iterations_used=1,
                tokens_used=10,
            )
        )
        await db_session.commit()

        try:
            delete_response = e2e_client.delete(
                f"/api/agents/{agent_id}",
                headers=platform_admin.headers,
            )
            assert delete_response.status_code == 204, delete_response.text

            agent_count = await db_session.scalar(
                select(func.count()).select_from(Agent).where(Agent.id == agent_id)
            )
            run_count = await db_session.scalar(
                select(func.count())
                .select_from(AgentRun)
                .where(AgentRun.agent_id == agent_id)
            )
            assert agent_count == 0
            assert run_count == 0
        finally:
            e2e_client.delete(
                f"/api/agents/{agent_id}",
                headers=platform_admin.headers,
            )

    def test_list_agents_excludes_inactive_by_default(
        self,
        e2e_client,
        platform_admin,
        test_agent,
    ):
        """Test that inactive agents are excluded from list by default."""
        # First deactivate the agent without deleting it.
        e2e_client.put(
            f"/api/agents/{test_agent['id']}",
            json={"is_active": False},
            headers=platform_admin.headers,
        )

        # List should not include it
        response = e2e_client.get(
            "/api/agents",
            headers=platform_admin.headers,
        )
        assert response.status_code == 200
        data = response.json()
        agent_ids = [a["id"] for a in data]
        assert test_agent["id"] not in agent_ids

    def test_get_agent_not_found(
        self,
        e2e_client,
        platform_admin,
    ):
        """Test getting non-existent agent returns 404."""
        import uuid
        fake_id = str(uuid.uuid4())
        response = e2e_client.get(
            f"/api/agents/{fake_id}",
            headers=platform_admin.headers,
        )
        assert response.status_code == 404


class TestAgentsAccessControl:
    """Test agent access control."""

    def test_org_user_cannot_create_agent(
        self,
        e2e_client,
        org1_user,
    ):
        """Test that org users cannot create agents."""
        response = e2e_client.post(
            "/api/agents",
            json={
                "name": "Unauthorized Agent",
                "system_prompt": "Test prompt",
                "channels": ["chat"],
                "access_level": "authenticated",
            },
            headers=org1_user.headers,
        )
        assert response.status_code in [401, 403]

    def test_org_user_cannot_update_agent(
        self,
        e2e_client,
        org1_user,
        test_agent,
    ):
        """Test that org users cannot update agents."""
        response = e2e_client.put(
            f"/api/agents/{test_agent['id']}",
            json={"name": "Hacked Name"},
            headers=org1_user.headers,
        )
        assert response.status_code in [401, 403]

    def test_org_user_cannot_delete_agent(
        self,
        e2e_client,
        org1_user,
        test_agent,
    ):
        """Test that org users cannot delete agents."""
        response = e2e_client.delete(
            f"/api/agents/{test_agent['id']}",
            headers=org1_user.headers,
        )
        assert response.status_code in [401, 403]

    def test_org_user_can_list_authenticated_agents(
        self,
        e2e_client,
        org1_user,
    ):
        """Test that org users can list authenticated agents."""
        response = e2e_client.get(
            "/api/agents",
            headers=org1_user.headers,
        )
        # Should succeed - access control filters results
        assert response.status_code == 200


@pytest.mark.e2e
class TestAgentScopeFiltering:
    """Test agent scope filtering works correctly."""

    @pytest.fixture
    def scoped_agents(self, e2e_client, platform_admin, org1, org2):
        """Create agents in different scopes for testing."""
        agents = {}

        # Create global agent (no organization_id)
        response = e2e_client.post(
            "/api/agents",
            json={
                "name": "Global Agent",
                "description": "A global agent for testing",
                "system_prompt": "You are a global test assistant.",
                "channels": ["chat"],
                "access_level": "authenticated",
                "organization_id": None,
            },
            headers=platform_admin.headers,
        )
        assert response.status_code == 201, f"Failed to create global agent: {response.text}"
        agents["global"] = response.json()

        # Create org1 agent
        response = e2e_client.post(
            "/api/agents",
            json={
                "name": "Org1 Agent",
                "description": "An org1 agent for testing",
                "system_prompt": "You are an org1 test assistant.",
                "channels": ["chat"],
                "access_level": "authenticated",
                "organization_id": org1["id"],
            },
            headers=platform_admin.headers,
        )
        assert response.status_code == 201, f"Failed to create org1 agent: {response.text}"
        agents["org1"] = response.json()

        # Create org2 agent
        response = e2e_client.post(
            "/api/agents",
            json={
                "name": "Org2 Agent",
                "description": "An org2 agent for testing",
                "system_prompt": "You are an org2 test assistant.",
                "channels": ["chat"],
                "access_level": "authenticated",
                "organization_id": org2["id"],
            },
            headers=platform_admin.headers,
        )
        assert response.status_code == 201, f"Failed to create org2 agent: {response.text}"
        agents["org2"] = response.json()

        yield agents

        # Cleanup
        for key, agent in agents.items():
            try:
                e2e_client.delete(
                    f"/api/agents/{agent['id']}",
                    headers=platform_admin.headers,
                )
            except Exception as e:
                # Best-effort fixture cleanup; teardown shouldn't fail the test
                logger.debug(f"fixture cleanup error: {e}")

    def test_platform_admin_no_scope_sees_all(
        self, e2e_client, platform_admin, scoped_agents
    ):
        """Platform admin with no scope sees ALL agents."""
        response = e2e_client.get(
            "/api/agents",
            headers=platform_admin.headers,
        )
        assert response.status_code == 200
        agent_ids = [a["id"] for a in response.json()]

        assert scoped_agents["global"]["id"] in agent_ids, "Should see global agent"
        assert scoped_agents["org1"]["id"] in agent_ids, "Should see org1 agent"
        assert scoped_agents["org2"]["id"] in agent_ids, "Should see org2 agent"

    def test_platform_admin_chat_discovery_does_not_expand_for_admin_status(
        self, e2e_client, platform_admin, scoped_agents
    ):
        """Chat discovery uses normal scope even for a platform admin."""
        response = e2e_client.get(
            "/api/agents",
            params={"discovery_only": True},
            headers=platform_admin.headers,
        )
        assert response.status_code == 200
        agent_ids = {agent["id"] for agent in response.json()}

        assert scoped_agents["global"]["id"] in agent_ids
        assert scoped_agents["org1"]["id"] not in agent_ids
        assert scoped_agents["org2"]["id"] not in agent_ids

    def test_provider_chat_discovery_does_not_expand_for_impersonation(
        self, e2e_client, provider_org_user, scoped_agents
    ):
        """Provider impersonation is explicit, not a discovery entitlement."""
        response = e2e_client.get(
            "/api/agents",
            params={"discovery_only": True},
            headers=provider_org_user.headers,
        )
        assert response.status_code == 200
        agent_ids = {agent["id"] for agent in response.json()}

        assert scoped_agents["global"]["id"] in agent_ids
        assert scoped_agents["org1"]["id"] not in agent_ids
        assert scoped_agents["org2"]["id"] not in agent_ids

    def test_platform_admin_scope_global_sees_only_global(
        self, e2e_client, platform_admin, scoped_agents
    ):
        """Platform admin with scope=global sees ONLY global agents."""
        response = e2e_client.get(
            "/api/agents",
            params={"scope": "global"},
            headers=platform_admin.headers,
        )
        assert response.status_code == 200
        agent_ids = [a["id"] for a in response.json()]

        assert scoped_agents["global"]["id"] in agent_ids, "Should see global agent"
        assert scoped_agents["org1"]["id"] not in agent_ids, "Should NOT see org1 agent"
        assert scoped_agents["org2"]["id"] not in agent_ids, "Should NOT see org2 agent"

    def test_platform_admin_scope_org_sees_only_that_org(
        self, e2e_client, platform_admin, org1, scoped_agents
    ):
        """Platform admin with scope={org1} sees ONLY org1 agents (NOT global)."""
        response = e2e_client.get(
            "/api/agents",
            params={"scope": org1["id"]},
            headers=platform_admin.headers,
        )
        assert response.status_code == 200
        agent_ids = [a["id"] for a in response.json()]

        # KEY ASSERTION: Global should NOT be included when filtering by org
        assert scoped_agents["global"]["id"] not in agent_ids, "Should NOT see global agent"
        assert scoped_agents["org1"]["id"] in agent_ids, "Should see org1 agent"
        assert scoped_agents["org2"]["id"] not in agent_ids, "Should NOT see org2 agent"

    def test_org_user_sees_own_org_plus_global(
        self, e2e_client, org1_user, scoped_agents
    ):
        """Org user (no scope param) sees their org + global."""
        response = e2e_client.get(
            "/api/agents",
            headers=org1_user.headers,
        )
        assert response.status_code == 200
        agent_ids = [a["id"] for a in response.json()]

        assert scoped_agents["global"]["id"] in agent_ids, "Should see global agent"
        assert scoped_agents["org1"]["id"] in agent_ids, "Should see org1 agent"
        assert scoped_agents["org2"]["id"] not in agent_ids, "Should NOT see org2 agent"

    def test_platform_admin_can_get_cross_org_agent(
        self, e2e_client, platform_admin, scoped_agents
    ):
        """Platform admin GET /agents/{id} must succeed for a cross-org agent.

        Regression for the bug where ``get_agent_with_access_check`` scoped the
        lookup to the admin's own org and only consulted ``is_superuser`` AFTER
        finding the entity. A cross-org agent shows up in LIST and can be
        updated via PUT, but GET would 404 — inconsistent and surprising.
        """
        org2_agent_id = scoped_agents["org2"]["id"]
        response = e2e_client.get(
            f"/api/agents/{org2_agent_id}",
            headers=platform_admin.headers,
        )
        assert response.status_code == 200, (
            f"Platform admin should get 200 for cross-org agent "
            f"{org2_agent_id}, got {response.status_code}: {response.text}"
        )
        data = response.json()
        assert data["id"] == org2_agent_id
        # The response must include the eager-loaded relations the handler has
        # historically returned (tools, delegated agents, roles, owner).
        assert "tools" in data or "tool_ids" in data
        assert "roles" in data or "role_ids" in data

    def test_provider_user_can_explicitly_get_cross_org_agent(
        self, e2e_client, provider_org_user, scoped_agents
    ):
        """An exact agent ID activates the provider caller's scope bypass."""
        org2_agent_id = scoped_agents["org2"]["id"]
        response = e2e_client.get(
            f"/api/agents/{org2_agent_id}",
            headers=provider_org_user.headers,
        )
        assert response.status_code == 200, response.text
        assert response.json()["id"] == org2_agent_id

    def test_org_user_cannot_get_cross_org_agent(
        self, e2e_client, org1_user, scoped_agents
    ):
        """Non-admin org user must NOT be able to GET a cross-org agent.

        This is the counter-case to the admin test above — the fix must keep
        regular users scoped to their own org and global agents.
        """
        org2_agent_id = scoped_agents["org2"]["id"]
        response = e2e_client.get(
            f"/api/agents/{org2_agent_id}",
            headers=org1_user.headers,
        )
        assert response.status_code == 404, (
            f"Org1 user should get 404 for Org2 agent {org2_agent_id}, "
            f"got {response.status_code}"
        )

    def test_provider_org_non_admin_cannot_get_other_users_private_agent(
        self, e2e_client, org1_user, provider_org_user
    ):
        """Private is owner-only regardless of org/provider scope bypass —
        a provider-org non-admin's scope bypass does not extend to another
        user's private agent."""
        create_resp = e2e_client.post(
            "/api/agents",
            json={
                "name": f"Private Owner Only {uuid4().hex[:8]}",
                "system_prompt": "Private agent for owner-only test.",
                "access_level": "private",
            },
            headers=org1_user.headers,
        )
        assert create_resp.status_code == 201, create_resp.text
        agent_id = create_resp.json()["id"]

        try:
            own_read = e2e_client.get(
                f"/api/agents/{agent_id}", headers=org1_user.headers
            )
            assert own_read.status_code == 200, own_read.text

            provider_read = e2e_client.get(
                f"/api/agents/{agent_id}", headers=provider_org_user.headers
            )
            assert provider_read.status_code == 404, provider_read.text

            provider_stats = e2e_client.get(
                f"/api/agents/{agent_id}/stats", headers=provider_org_user.headers
            )
            assert provider_stats.status_code == 404, provider_stats.text
        finally:
            e2e_client.delete(f"/api/agents/{agent_id}", headers=org1_user.headers)


async def _make_mcp_connection(db_session, organization_id: UUID) -> UUID:
    """Insert a minimal MCPServer + MCPConnection row for reference tests."""
    from src.models.orm.external_mcp import MCPConnection, MCPServer

    server = MCPServer(
        id=uuid4(),
        name=f"agent-write-policy-server-{uuid4().hex[:8]}",
        server_url="https://example.com/mcp",
        organization_id=None,
        is_active=True,
    )
    db_session.add(server)
    await db_session.flush()

    connection = MCPConnection(
        id=uuid4(),
        server_id=server.id,
        organization_id=organization_id,
        client_id="test-client",
        encrypted_client_secret="encrypted-blob",
        available_in_chat=True,
        available_to_autonomous=False,
    )
    db_session.add(connection)
    # Commit (not just flush) — the API request goes through a separate DB
    # session/connection, which must see this row.
    await db_session.commit()
    return connection.id


class TestAgentWritePolicyGates:
    """A field is applied or explicitly refused (403/422) — never dropped.

    Covers the RBAC R1b non-admin privileged/budget/org gates on create and
    update, plus the admin-and-everyone reference-validation 422s.
    """

    # ---- create: privileged fields --------------------------------------

    @pytest.mark.parametrize(
        "field,value",
        [
            ("system_tools", ["some_system_tool"]),
            ("knowledge_sources", ["some-namespace"]),
            ("delegated_agent_ids", [str(uuid4())]),
            ("role_ids", [str(uuid4())]),
            ("mcp_connection_ids", [str(uuid4())]),
        ],
    )
    def test_non_admin_create_with_privileged_field_non_empty_403(
        self, e2e_client, org1_user, field, value
    ):
        response = e2e_client.post(
            "/api/agents",
            json={
                "name": f"Privileged Create {uuid4().hex[:8]}",
                "system_prompt": "Test prompt.",
                "access_level": "private",
                field: value,
            },
            headers=org1_user.headers,
        )
        assert response.status_code == 403, response.text
        assert field in response.json()["detail"]

    def test_non_admin_create_with_privileged_fields_empty_201(
        self, e2e_client, org1_user
    ):
        response = e2e_client.post(
            "/api/agents",
            json={
                "name": f"Privileged Create Empty {uuid4().hex[:8]}",
                "system_prompt": "Test prompt.",
                "access_level": "private",
                "system_tools": [],
                "knowledge_sources": [],
                "delegated_agent_ids": [],
                "role_ids": [],
                "mcp_connection_ids": [],
            },
            headers=org1_user.headers,
        )
        assert response.status_code == 201, response.text
        e2e_client.delete(
            f"/api/agents/{response.json()['id']}", headers=org1_user.headers
        )

    def test_non_admin_create_with_budget_field_403(self, e2e_client, org1_user):
        response = e2e_client.post(
            "/api/agents",
            json={
                "name": f"Budget Create {uuid4().hex[:8]}",
                "system_prompt": "Test prompt.",
                "access_level": "private",
                "llm_max_tokens": 500,
            },
            headers=org1_user.headers,
        )
        assert response.status_code == 403, response.text
        assert "llm_max_tokens" in response.json()["detail"]

    # ---- create: organization_id ------------------------------------------

    def test_non_admin_create_with_foreign_org_403(
        self, e2e_client, org1_user, org2
    ):
        response = e2e_client.post(
            "/api/agents",
            json={
                "name": f"Foreign Org Create {uuid4().hex[:8]}",
                "system_prompt": "Test prompt.",
                "access_level": "private",
                "organization_id": org2["id"],
            },
            headers=org1_user.headers,
        )
        assert response.status_code == 403, response.text

    def test_non_admin_create_with_explicit_null_org_403(
        self, e2e_client, org1_user
    ):
        response = e2e_client.post(
            "/api/agents",
            json={
                "name": f"Global Org Create {uuid4().hex[:8]}",
                "system_prompt": "Test prompt.",
                "access_level": "private",
                "organization_id": None,
            },
            headers=org1_user.headers,
        )
        assert response.status_code == 403, response.text

    def test_non_admin_create_omitted_org_defaults_to_own(
        self, e2e_client, org1_user
    ):
        response = e2e_client.post(
            "/api/agents",
            json={
                "name": f"Own Org Create {uuid4().hex[:8]}",
                "system_prompt": "Test prompt.",
                "access_level": "private",
            },
            headers=org1_user.headers,
        )
        assert response.status_code == 201, response.text
        assert response.json()["organization_id"] == str(org1_user.organization_id)
        e2e_client.delete(
            f"/api/agents/{response.json()['id']}", headers=org1_user.headers
        )

    # ---- update: privileged fields + clear_roles --------------------------

    @pytest.fixture
    def own_private_agent(self, e2e_client, org1_user):
        create_resp = e2e_client.post(
            "/api/agents",
            json={
                "name": f"Own Private {uuid4().hex[:8]}",
                "system_prompt": "Test prompt.",
                "access_level": "private",
            },
            headers=org1_user.headers,
        )
        assert create_resp.status_code == 201, create_resp.text
        agent_id = create_resp.json()["id"]
        yield agent_id
        e2e_client.delete(f"/api/agents/{agent_id}", headers=org1_user.headers)

    @pytest.mark.parametrize(
        "body",
        [
            {"system_tools": []},
            {"knowledge_sources": []},
            {"delegated_agent_ids": []},
            {"role_ids": []},
            {"clear_roles": True},
        ],
    )
    def test_non_admin_update_with_privileged_field_403(
        self, e2e_client, org1_user, own_private_agent, body
    ):
        response = e2e_client.put(
            f"/api/agents/{own_private_agent}",
            json=body,
            headers=org1_user.headers,
        )
        assert response.status_code == 403, response.text

    def test_non_admin_update_moving_org_403(
        self, e2e_client, org1_user, own_private_agent, org2
    ):
        response = e2e_client.put(
            f"/api/agents/{own_private_agent}",
            json={"organization_id": org2["id"]},
            headers=org1_user.headers,
        )
        assert response.status_code == 403, response.text

    def test_non_admin_update_same_org_200(
        self, e2e_client, org1_user, own_private_agent
    ):
        response = e2e_client.put(
            f"/api/agents/{own_private_agent}",
            json={"organization_id": str(org1_user.organization_id)},
            headers=org1_user.headers,
        )
        assert response.status_code == 200, response.text

    # ---- admin reference validation (422, everyone including admins) ------

    @pytest.fixture
    def admin_agent(self, e2e_client, platform_admin, org1):
        create_resp = e2e_client.post(
            "/api/agents",
            json={
                "name": f"Admin Reference Test {uuid4().hex[:8]}",
                "system_prompt": "Test prompt.",
                "access_level": "role_based",
                "organization_id": org1["id"],
            },
            headers=platform_admin.headers,
        )
        assert create_resp.status_code == 201, create_resp.text
        agent_id = create_resp.json()["id"]
        yield agent_id
        e2e_client.delete(f"/api/agents/{agent_id}", headers=platform_admin.headers)

    def test_admin_update_nonexistent_role_id_422(
        self, e2e_client, platform_admin, admin_agent
    ):
        response = e2e_client.put(
            f"/api/agents/{admin_agent}",
            json={"role_ids": [str(uuid4())]},
            headers=platform_admin.headers,
        )
        assert response.status_code == 422, response.text

    @pytest.mark.asyncio
    async def test_admin_update_cross_org_mcp_connection_422(
        self, e2e_client, platform_admin, admin_agent, org2, db_session
    ):
        connection_id = await _make_mcp_connection(db_session, UUID(org2["id"]))
        response = e2e_client.put(
            f"/api/agents/{admin_agent}",
            json={"mcp_connection_ids": [str(connection_id)]},
            headers=platform_admin.headers,
        )
        assert response.status_code == 422, response.text

    @pytest.mark.asyncio
    async def test_admin_update_global_agent_with_connection_422(
        self, e2e_client, platform_admin, org1, db_session
    ):
        create_resp = e2e_client.post(
            "/api/agents",
            json={
                "name": f"Global Agent Connection {uuid4().hex[:8]}",
                "system_prompt": "Test prompt.",
                "access_level": "authenticated",
                "organization_id": None,
            },
            headers=platform_admin.headers,
        )
        assert create_resp.status_code == 201, create_resp.text
        agent_id = create_resp.json()["id"]
        try:
            connection_id = await _make_mcp_connection(db_session, UUID(org1["id"]))
            response = e2e_client.put(
                f"/api/agents/{agent_id}",
                json={"mcp_connection_ids": [str(connection_id)]},
                headers=platform_admin.headers,
            )
            assert response.status_code == 422, response.text
        finally:
            e2e_client.delete(
                f"/api/agents/{agent_id}", headers=platform_admin.headers
            )

    def test_admin_update_duplicate_role_ids_422(
        self, e2e_client, platform_admin, admin_agent
    ):
        """Duplicate detection runs before existence checks — a repeated
        (even nonexistent) UUID is enough to trip the 422."""
        duplicate = str(uuid4())
        response = e2e_client.put(
            f"/api/agents/{admin_agent}",
            json={"role_ids": [duplicate, duplicate]},
            headers=platform_admin.headers,
        )
        assert response.status_code == 422, response.text

    def test_admin_update_clear_roles_with_role_ids_422(
        self, e2e_client, platform_admin, admin_agent
    ):
        response = e2e_client.put(
            f"/api/agents/{admin_agent}",
            json={"clear_roles": True, "role_ids": [str(uuid4())]},
            headers=platform_admin.headers,
        )
        assert response.status_code == 422, response.text

    @pytest.mark.asyncio
    async def test_admin_rescope_with_connections_and_no_list_422(
        self, e2e_client, platform_admin, org1, org2, db_session
    ):
        create_resp = e2e_client.post(
            "/api/agents",
            json={
                "name": f"Rescope Connection {uuid4().hex[:8]}",
                "system_prompt": "Test prompt.",
                "access_level": "role_based",
                "organization_id": org1["id"],
            },
            headers=platform_admin.headers,
        )
        assert create_resp.status_code == 201, create_resp.text
        agent_id = create_resp.json()["id"]
        try:
            connection_id = await _make_mcp_connection(db_session, UUID(org1["id"]))
            grant_resp = e2e_client.put(
                f"/api/agents/{agent_id}",
                json={"mcp_connection_ids": [str(connection_id)]},
                headers=platform_admin.headers,
            )
            assert grant_resp.status_code == 200, grant_resp.text

            response = e2e_client.put(
                f"/api/agents/{agent_id}",
                json={"organization_id": org2["id"]},
                headers=platform_admin.headers,
            )
            assert response.status_code == 422, response.text
        finally:
            e2e_client.delete(
                f"/api/agents/{agent_id}", headers=platform_admin.headers
            )


class TestSolutionManagedAgentRest:
    """REST refuses to mutate a solution-managed Agent (criterion 6).

    Previously only covered by MCP unit tests exercising removed in-tool ORM
    logic (RBAC R1b deleted them — the MCP tools are now thin REST wrappers,
    so REST is the one place this guard needs direct coverage).
    """

    async def _managed_agent(self, db_session) -> str:
        from src.models.orm.agents import Agent
        from src.models.orm.solutions import Solution

        solution = Solution(
            id=uuid4(),
            slug=f"agent-write-policy-{uuid4().hex[:8]}",
            name="Agent Write Policy Solution",
            organization_id=None,
        )
        db_session.add(solution)
        await db_session.flush()

        agent_id = uuid4()
        db_session.add(
            Agent(
                id=agent_id,
                name=f"solution-managed-{uuid4().hex[:8]}",
                system_prompt="Managed agent.",
                organization_id=None,
                solution_id=solution.id,
                created_by="test",
            )
        )
        await db_session.commit()
        return str(agent_id)

    @pytest.mark.asyncio
    async def test_update_solution_managed_agent_409(
        self, e2e_client, platform_admin, db_session
    ):
        agent_id = await self._managed_agent(db_session)
        response = e2e_client.put(
            f"/api/agents/{agent_id}",
            json={"name": "hijacked"},
            headers=platform_admin.headers,
        )
        assert response.status_code == 409, response.text

    @pytest.mark.asyncio
    async def test_delete_solution_managed_agent_409(
        self, e2e_client, platform_admin, db_session
    ):
        agent_id = await self._managed_agent(db_session)
        response = e2e_client.delete(
            f"/api/agents/{agent_id}",
            headers=platform_admin.headers,
        )
        assert response.status_code == 409, response.text


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def test_agent(e2e_client, platform_admin):
    """Create a test agent for use in tests."""
    response = e2e_client.post(
        "/api/agents",
        json={
            "name": "E2E Test Agent",
            "description": "Agent for E2E testing",
            "system_prompt": "You are a test assistant.",
            "channels": ["chat"],
            "access_level": "authenticated",
        },
        headers=platform_admin.headers,
    )
    assert response.status_code == 201, f"Failed to create test agent: {response.text}"
    agent = response.json()

    yield agent

    # Cleanup - delete the agent
    try:
        e2e_client.delete(
            f"/api/agents/{agent['id']}",
            headers=platform_admin.headers,
        )
    except Exception as e:
        # Best-effort fixture cleanup; teardown shouldn't fail the test
        logger.debug(f"fixture cleanup error: {e}")
