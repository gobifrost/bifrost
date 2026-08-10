"""
Agents E2E Tests.

Tests agent CRUD operations and role assignment.
"""

import logging

import pytest

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

    def test_delete_agent(
        self,
        e2e_client,
        platform_admin,
        test_agent,
    ):
        """Test soft deleting an agent."""
        response = e2e_client.delete(
            f"/api/agents/{test_agent['id']}",
            headers=platform_admin.headers,
        )
        assert response.status_code == 204

        # Verify it's inactive
        response = e2e_client.get(
            f"/api/agents/{test_agent['id']}",
            headers=platform_admin.headers,
        )
        assert response.status_code == 200
        assert response.json()["is_active"] is False

    def test_list_agents_excludes_inactive_by_default(
        self,
        e2e_client,
        platform_admin,
        test_agent,
    ):
        """Test that inactive agents are excluded from list by default."""
        # First delete the agent
        e2e_client.delete(
            f"/api/agents/{test_agent['id']}",
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
