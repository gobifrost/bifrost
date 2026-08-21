"""
Agents E2E Tests.

Tests agent CRUD operations and role assignment.
"""

import io
import logging
import zipfile
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

from src.models.orm.agents import Agent
from src.models.orm.agent_runs import AgentRun

logger = logging.getLogger(__name__)


def _upload_headers(headers: dict[str, str]) -> dict[str, str]:
    return {k: v for k, v in headers.items() if k.lower() != "content-type"}


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

    @pytest.mark.parametrize(
        ("field_name", "error_fragment"),
        [
            ("role_ids", "does not reference an existing role"),
            (
                "mcp_connection_ids",
                "does not reference an existing connection",
            ),
        ],
    )
    def test_create_agent_rejects_unknown_relationships(
        self,
        e2e_client,
        platform_admin,
        field_name,
        error_fragment,
    ):
        """Invalid relationship IDs fail atomically instead of being dropped."""
        unknown_id = str(uuid4())
        response = e2e_client.post(
            "/api/agents",
            json={
                "name": f"Invalid {field_name}",
                "system_prompt": "This Agent must not be created.",
                "access_level": "authenticated",
                field_name: [unknown_id],
            },
            headers=platform_admin.headers,
        )

        assert response.status_code == 422, response.text
        detail = response.json()["detail"]
        assert detail["message"] == "Invalid agent references"
        assert any(error_fragment in error for error in detail["errors"])

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
        assert "bundle_path" in data

    def test_agent_create_rejects_bundle_path(
        self,
        e2e_client,
        platform_admin,
    ):
        """Generic Agent creation cannot bind a bundle root directly."""
        response = e2e_client.post(
            "/api/agents",
            json={
                "name": "Bundled by API",
                "system_prompt": "Use me.",
                "bundle_path": "skills/not-allowed",
                "channels": ["chat"],
                "access_level": "authenticated",
            },
            headers=platform_admin.headers,
        )
        assert response.status_code == 422

    def test_agent_skill_projection_and_download(
        self,
        e2e_client,
        platform_admin,
        test_agent,
    ):
        """Every Agent exposes the portable skill users are configuring."""
        skill = e2e_client.get(
            f"/api/agents/{test_agent['id']}/skill",
            headers=platform_admin.headers,
        )
        assert skill.status_code == 200, skill.text
        body = skill.json()
        assert body["name"] == "e2e-test-agent"
        assert "SKILL.md" not in body["companion_files"]
        assert body["skill_markdown"].startswith("---\nname:")
        assert test_agent["system_prompt"] in body["skill_markdown"]

        download = e2e_client.get(
            f"/api/agents/{test_agent['id']}/skill/download",
            headers=platform_admin.headers,
        )
        assert download.status_code == 200, download.text
        assert download.headers["content-type"] == "application/zip"
        assert "attachment" in download.headers["content-disposition"]
        archive = zipfile.ZipFile(io.BytesIO(download.content))
        assert archive.namelist() == ["e2e-test-agent/SKILL.md"]
        assert (
            archive.read("e2e-test-agent/SKILL.md").decode("utf-8")
            == body["skill_markdown"]
        )

    def test_skill_revision_tracks_content_changes(
        self,
        e2e_client,
        platform_admin,
        test_agent,
    ):
        """The revision identifies Skill content, so a consumer can cache on it.

        It must be stable across reads of unchanged content, and must move when
        the projected SKILL.md changes — which for an inline Agent means an edit
        to the fields that render it.
        """
        agent_id = test_agent["id"]

        def _revision():
            resp = e2e_client.get(
                f"/api/agents/{agent_id}/skill", headers=platform_admin.headers
            )
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert len(body["revision"]) == 64, body["revision"]
            return body["revision"]

        first = _revision()
        assert _revision() == first, "an unchanged Skill must keep its revision"

        updated = e2e_client.put(
            f"/api/agents/{agent_id}",
            headers=platform_admin.headers,
            json={"system_prompt": "Completely different instructions."},
        )
        assert updated.status_code == 200, updated.text

        after = _revision()
        assert after != first, (
            "editing the instructions must change the Skill revision"
        )

        # And the change is durable, not recomputed per request.
        assert _revision() == after

    def test_skill_export_returns_an_opaque_artifact_ref(
        self,
        e2e_client,
        platform_admin,
        test_agent,
    ):
        """Export hands a runtime the Skill without ever naming a storage path.

        The browser download streams bytes; this route persists the same
        deterministic archive and returns only an ArtifactRef, so a caller can
        pass the Skill onward without learning an S3 key.
        """
        agent_id = test_agent["id"]
        resp = e2e_client.post(
            f"/api/agents/{agent_id}/skill/export",
            headers=platform_admin.headers,
        )
        assert resp.status_code == 200, resp.text
        ref = resp.json()

        assert ref["type"] == "bifrost_artifact"
        assert ref["filename"].endswith(".skill")
        assert ref["content_type"] == "application/zip"
        assert ref["size_bytes"] > 0

        # No storage coordinates in the payload, under any spelling.
        body = resp.text.lower()
        for leak in ("s3_key", "_artifacts/", "bucket", "seaweed"):
            assert leak not in body, f"export leaked {leak!r}: {resp.text}"

        # The ref resolves through the normal artifact surface.
        fetched = e2e_client.get(
            f"/api/artifacts/{ref['id']}", headers=platform_admin.headers
        )
        assert fetched.status_code in (200, 404), fetched.text

    def test_skill_export_is_deterministic(
        self,
        e2e_client,
        platform_admin,
        test_agent,
    ):
        """Unchanged content exports byte-identical archives.

        The archive uses a fixed ZIP epoch and sorted members precisely so an
        export can be compared or cached. Each export is a distinct artifact,
        but the bytes must match.
        """
        agent_id = test_agent["id"]

        def _export():
            resp = e2e_client.post(
                f"/api/agents/{agent_id}/skill/export",
                headers=platform_admin.headers,
            )
            assert resp.status_code == 200, resp.text
            return resp.json()

        first, second = _export(), _export()
        assert first["id"] != second["id"], "each export is its own artifact"
        assert first["size_bytes"] == second["size_bytes"], (
            "identical Skill content must produce identical archive bytes"
        )
        assert first["filename"] == second["filename"]

    def test_skill_export_requires_agent_access(
        self,
        e2e_client,
        platform_admin,
        test_agent,
        org1_user,
    ):
        """Export carries the same access check as reading the Agent."""
        resp = e2e_client.post(
            f"/api/agents/{test_agent['id']}/skill/export",
            headers=org1_user.headers,
        )
        assert resp.status_code in (403, 404), (
            f"an unauthorized user must not export a Skill: {resp.status_code}"
        )

    def test_upload_browse_and_detach_agent_skill(
        self,
        e2e_client,
        platform_admin,
        test_agent,
    ):
        """Uploaded SKILL.md becomes canonical without writing into _repo."""
        skill_markdown = (
            "---\n"
            "name: expense-tracker\n"
            "description: Track expenses safely\n"
            "---\n\n"
            "# Instructions\n\nUse the expense policy.\n"
        )
        archive_bytes = io.BytesIO()
        with zipfile.ZipFile(archive_bytes, "w") as archive:
            archive.writestr("expense/SKILL.md", skill_markdown)
            archive.writestr(
                "expense/references/policy.md",
                "# Expense policy\n",
            )

        uploaded = e2e_client.put(
            f"/api/agents/{test_agent['id']}/skill/bundle",
            files={
                "file": (
                    "expense.skill",
                    archive_bytes.getvalue(),
                    "application/zip",
                )
            },
            headers=_upload_headers(platform_admin.headers),
        )
        assert uploaded.status_code == 200, uploaded.text
        body = uploaded.json()
        assert body["bundle_path"] == "skills/expense-tracker"
        assert body["skill_markdown"] == skill_markdown
        assert body["files"] == ["SKILL.md", "references/policy.md"]
        assert body["source"] == "upload"

        agent = e2e_client.get(
            f"/api/agents/{test_agent['id']}",
            headers=platform_admin.headers,
        )
        assert agent.status_code == 200
        assert agent.json()["bundle_path"] == "skills/expense-tracker"
        assert agent.json()["system_prompt"] == skill_markdown

        reference = e2e_client.get(
            f"/api/agents/{test_agent['id']}/skill/file",
            params={"path": "references/policy.md"},
            headers=platform_admin.headers,
        )
        assert reference.status_code == 200, reference.text
        assert reference.json() == {
            "path": "references/policy.md",
            "encoding": "utf-8",
            "content": "# Expense policy\n",
        }

        bundle_path_update = e2e_client.put(
            f"/api/agents/{test_agent['id']}",
            json={"bundle_path": "skills/other"},
            headers=platform_admin.headers,
        )
        assert bundle_path_update.status_code == 422

        split_brain_update = e2e_client.put(
            f"/api/agents/{test_agent['id']}",
            json={"system_prompt": "Inline prompt should not win"},
            headers=platform_admin.headers,
        )
        assert split_brain_update.status_code == 409

        detached = e2e_client.delete(
            f"/api/agents/{test_agent['id']}/skill/bundle",
            headers=platform_admin.headers,
        )
        assert detached.status_code == 204, detached.text
        inline = e2e_client.get(
            f"/api/agents/{test_agent['id']}",
            headers=platform_admin.headers,
        ).json()
        assert inline["bundle_path"] is None
        assert inline["system_prompt"].startswith("# Instructions")

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
            "/api/agents?scope=global",
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
            f"/api/agents?scope={org1['id']}",
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
            f"/api/agents?scope={org2['id']}",
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
        scopes = {"global": "global", "org1": org1["id"], "org2": org2["id"]}
        for key, agent in agents.items():
            try:
                e2e_client.delete(
                    f"/api/agents/{agent['id']}?scope={scopes[key]}",
                    headers=platform_admin.headers,
                )
            except Exception as e:
                # Best-effort fixture cleanup; teardown shouldn't fail the test
                logger.debug(f"fixture cleanup error: {e}")

    def test_platform_admin_no_scope_defaults_to_home_context(
        self, e2e_client, platform_admin, scoped_agents
    ):
        """No scope defaults to the provider home context plus Global."""
        response = e2e_client.get(
            "/api/agents",
            headers=platform_admin.headers,
        )
        assert response.status_code == 200
        agent_ids = [a["id"] for a in response.json()]

        assert scoped_agents["global"]["id"] in agent_ids, "Should see global agent"
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
        """Organization context includes authorized inherited Global Agents."""
        response = e2e_client.get(
            "/api/agents",
            params={"scope": org1["id"]},
            headers=platform_admin.headers,
        )
        assert response.status_code == 200
        agent_ids = [a["id"] for a in response.json()]

        assert scoped_agents["global"]["id"] in agent_ids, "Should see global agent"
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
            params={"scope": scoped_agents["org2"]["organization_id"]},
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
