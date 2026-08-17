"""
Unit tests for MCP Tools.

Tests the MCP tools for the Bifrost platform:
- get_docs: Returns unified platform documentation
- bifrost_list_workflows: Lists registered workflows
- bifrost_list_forms: Lists forms through the canonical REST API
- bifrost_list_tables: Lists table definitions through the canonical REST API
- search_knowledge: Searches the knowledge base
- list_integrations: Lists available integrations
- bifrost_execute_workflow: Executes workflows and returns an execution envelope

Uses mocked database access for fast, isolated testing.

Note: The MCP tools are implemented as decorated functions in src/services/mcp/tools/*.py.
We test the tool functions directly.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest

from src.services.mcp_server.server import MCPContext
from src.services.mcp_server.tools.forms import bifrost_list_forms
from src.services.mcp_server.tools.integrations import list_integrations
from src.services.mcp_server.tools.knowledge import search_knowledge
from src.services.mcp_server.tools.tables import bifrost_list_tables
from src.services.mcp_server.tools.workflow import (
    bifrost_execute_workflow,
    bifrost_list_workflows,
)


# ==================== Fixtures ====================


@pytest.fixture
def platform_admin_context() -> MCPContext:
    """Create an MCPContext for a platform admin user."""
    return MCPContext(
        user_id=uuid4(),
        org_id=None,  # Platform admin has no org scope
        is_platform_admin=True,
        user_email="admin@platform.local",
        user_name="Platform Admin",
    )


@pytest.fixture
def org_user_context() -> MCPContext:
    """Create an MCPContext for a regular org user."""
    return MCPContext(
        user_id=uuid4(),
        org_id=uuid4(),
        is_platform_admin=False,
        user_email="user@org.local",
        user_name="Org User",
    )


@pytest.fixture
def mock_knowledge_document():
    """Create a mock knowledge document."""
    from src.repositories.knowledge import KnowledgeDocument

    return KnowledgeDocument(
        id=str(uuid4()),
        namespace="bifrost_docs",
        content="This is documentation about the SDK",
        metadata={"source": "docs", "title": "SDK Guide"},
        score=0.85,
        organization_id=None,
        key="sdk-guide",
        created_at=datetime.now(timezone.utc),
    )


# ==================== canonical Workflow list wrapper ====================


class TestListWorkflows:
    @pytest.mark.asyncio
    async def test_forwards_filters_to_rest(self, org_user_context):
        workflows = [{"id": str(uuid4()), "name": "test_workflow"}]
        with (
            patch(
                "src.services.mcp_server.tools.workflow._resolve_scope",
                new=AsyncMock(return_value="00000000-0000-0000-0000-000000000002"),
            ),
            patch(
                "src.services.mcp_server.tools.workflow.call_rest",
                new=AsyncMock(return_value=(200, workflows)),
            ) as call_rest,
        ):
            result = await bifrost_list_workflows(
                org_user_context,
                query="test",
                category="automation",
                type="workflow",
                scope="Customer",
            )

        assert result.structured_content == {
            "workflows": workflows,
            "count": 1,
        }
        call_rest.assert_awaited_once_with(
            org_user_context,
            "GET",
            "/api/workflows",
            params={
                "query": "test",
                "category": "automation",
                "type": "workflow",
                "scope": "00000000-0000-0000-0000-000000000002",
            },
        )


# ==================== list_forms Tests ====================


class TestListForms:
    """Tests for the canonical Form list MCP tool."""

    @pytest.mark.asyncio
    async def test_lists_forms_for_org_user(self, org_user_context):
        """Should preserve the REST response for an organization user."""
        forms = [
            {
                "id": str(uuid4()),
                "name": "Test Form",
                "description": "A test form",
            }
        ]
        with patch(
            "src.services.mcp_server.tools.forms.call_rest",
            new=AsyncMock(return_value=(200, forms)),
        ) as call_rest:
            result = await bifrost_list_forms(org_user_context)

        data = result.structured_content
        assert data["forms"] == forms
        assert data["forms"][0]["name"] == "Test Form"
        assert data["forms"][0]["description"] == "A test form"
        assert data["count"] == 1
        call_rest.assert_awaited_once_with(
            org_user_context,
            "GET",
            "/api/forms",
            params=None,
        )

    @pytest.mark.asyncio
    async def test_forwards_scope_for_platform_admin(self, platform_admin_context):
        """Should let the REST API resolve a platform-admin scope."""
        forms = [{"id": str(uuid4()), "name": "Test Form"}]
        with patch(
            "src.services.mcp_server.tools.forms.call_rest",
            new=AsyncMock(return_value=(200, forms)),
        ) as call_rest:
            result = await bifrost_list_forms(platform_admin_context, scope="Acme")

        data = result.structured_content
        assert data["forms"] == forms
        call_rest.assert_awaited_once_with(
            platform_admin_context,
            "GET",
            "/api/forms",
            params={"scope": "Acme"},
        )


# ==================== bifrost_list_tables Tests ====================


class TestListTables:
    """Tests for the canonical Table metadata list MCP tool."""

    @pytest.mark.asyncio
    async def test_preserves_wrapped_rest_response(self, platform_admin_context):
        tables = [
            {
                "id": str(uuid4()),
                "name": "tickets",
                "description": "Support tickets",
            }
        ]
        with patch(
            "src.services.mcp_server.tools.tables.call_rest",
            new=AsyncMock(return_value=(200, {"tables": tables, "total": 1})),
        ) as call_rest:
            result = await bifrost_list_tables(platform_admin_context)

        data = result.structured_content
        assert data == {"tables": tables, "count": 1}
        call_rest.assert_awaited_once_with(
            platform_admin_context,
            "GET",
            "/api/tables",
            params=None,
        )

    @pytest.mark.asyncio
    async def test_forwards_scope(self, platform_admin_context):
        with patch(
            "src.services.mcp_server.tools.tables.call_rest",
            new=AsyncMock(return_value=(200, {"tables": [], "total": 0})),
        ) as call_rest:
            result = await bifrost_list_tables(
                platform_admin_context,
                scope="global",
            )

        assert result.structured_content == {"tables": [], "count": 0}
        call_rest.assert_awaited_once_with(
            platform_admin_context,
            "GET",
            "/api/tables",
            params={"scope": "global"},
        )

    @pytest.mark.asyncio
    async def test_returns_empty_list(self, org_user_context):
        """Should return empty list when no forms found."""
        with patch(
            "src.services.mcp_server.tools.forms.call_rest",
            new=AsyncMock(return_value=(200, [])),
        ):
            result = await bifrost_list_forms(org_user_context)

        data = result.structured_content
        assert data["forms"] == []
        assert data["count"] == 0

    @pytest.mark.asyncio
    async def test_handles_database_error(self, org_user_context):
        """Should preserve REST failure details."""
        with patch(
            "src.services.mcp_server.tools.forms.call_rest",
            new=AsyncMock(
                return_value=(503, {"detail": "Database connection failed"})
            ),
        ):
            result = await bifrost_list_forms(org_user_context)

        data = result.structured_content
        assert "error" in data
        assert data["error"] == "Database connection failed"
        assert data["status_code"] == 503


# ==================== search_knowledge Tests ====================


class TestSearchKnowledge:
    """Tests for the search_knowledge MCP tool."""

    @pytest.mark.asyncio
    async def test_searches_knowledge_base(
        self, org_user_context, mock_knowledge_document
    ):
        """Should search knowledge base and return results."""
        # Add accessible namespaces to allow knowledge search
        org_user_context.accessible_namespaces = ["test-namespace"]

        with patch("src.core.database.get_db_context") as mock_db_ctx:
            mock_session = AsyncMock()
            mock_db_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_db_ctx.return_value.__aexit__ = AsyncMock(return_value=None)

            with patch(
                "src.services.embeddings.get_embedding_client"
            ) as mock_embed_client:
                mock_client = AsyncMock()
                mock_client.embed_single = AsyncMock(return_value=[0.1, 0.2, 0.3])
                mock_embed_client.return_value = mock_client

                with patch(
                    "src.repositories.knowledge.KnowledgeRepository"
                ) as mock_repo_cls:
                    mock_repo = MagicMock()
                    mock_repo.search = AsyncMock(return_value=[mock_knowledge_document])
                    mock_repo_cls.return_value = mock_repo

                    result = await search_knowledge(
                        org_user_context, "SDK documentation"
                    )

        # Result is a ToolResult with structured_content
        data = result.structured_content
        assert "results" in data
        assert len(data["results"]) == 1
        assert data["results"][0]["content"] == "This is documentation about the SDK"
        assert data["count"] == 1
        assert mock_repo.search.await_args.kwargs["query_text"] == "SDK documentation"

    @pytest.mark.asyncio
    async def test_returns_no_results_message(self, org_user_context):
        """Should return message when no results found."""
        # Add accessible namespaces to allow knowledge search
        org_user_context.accessible_namespaces = ["test-namespace"]

        with patch("src.core.database.get_db_context") as mock_db_ctx:
            mock_session = AsyncMock()
            mock_db_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_db_ctx.return_value.__aexit__ = AsyncMock(return_value=None)

            with patch(
                "src.services.embeddings.get_embedding_client"
            ) as mock_embed_client:
                mock_client = AsyncMock()
                mock_client.embed_single = AsyncMock(return_value=[0.1, 0.2, 0.3])
                mock_embed_client.return_value = mock_client

                with patch(
                    "src.repositories.knowledge.KnowledgeRepository"
                ) as mock_repo_cls:
                    mock_repo = MagicMock()
                    mock_repo.search = AsyncMock(return_value=[])
                    mock_repo_cls.return_value = mock_repo

                    result = await search_knowledge(
                        org_user_context, "nonexistent topic"
                    )

        data = result.structured_content
        assert data["results"] == []
        assert data["count"] == 0
        assert "No results found" in data["message"]

    @pytest.mark.asyncio
    async def test_handles_missing_query(self, org_user_context):
        """Should return error when query is empty."""
        result = await search_knowledge(org_user_context, "")
        data = result.structured_content
        assert "error" in data
        assert "query is required" in data["error"]

    @pytest.mark.asyncio
    async def test_handles_embedding_error(self, org_user_context):
        """Should return error when embedding service fails."""
        # Add accessible namespaces to allow knowledge search
        org_user_context.accessible_namespaces = ["test-namespace"]

        with patch("src.core.database.get_db_context") as mock_db_ctx:
            mock_session = AsyncMock()
            mock_db_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_db_ctx.return_value.__aexit__ = AsyncMock(return_value=None)

            with patch(
                "src.services.embeddings.get_embedding_client"
            ) as mock_embed_client:
                mock_embed_client.side_effect = Exception("Embedding service error")

                result = await search_knowledge(org_user_context, "test query")

        data = result.structured_content
        assert "error" in data
        assert "Error searching knowledge" in data["error"]


# ==================== list_integrations Tests ====================


@pytest.fixture
def mock_integration():
    """Create a mock integration ORM object."""
    mock = MagicMock()
    mock.id = uuid4()
    mock.name = "Microsoft Graph"
    mock.is_deleted = False
    mock.has_oauth_config = True
    mock.entity_id_name = "Tenant ID"
    return mock


class TestListIntegrations:
    """Tests for the list_integrations MCP tool."""

    @pytest.mark.asyncio
    async def test_lists_integrations_for_platform_admin(
        self, platform_admin_context, mock_integration
    ):
        """Should list all active integrations for platform admin."""
        with patch("src.core.database.get_db_context") as mock_db_ctx:
            mock_session = AsyncMock()
            mock_result = MagicMock()
            mock_result.scalars.return_value.all.return_value = [mock_integration]
            mock_session.execute = AsyncMock(return_value=mock_result)
            mock_db_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_db_ctx.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await list_integrations(platform_admin_context)

        # Result is a ToolResult with structured_content
        data = result.structured_content
        assert "integrations" in data
        assert len(data["integrations"]) == 1
        assert data["integrations"][0]["name"] == "Microsoft Graph"
        assert data["integrations"][0]["has_oauth"] is True
        assert data["integrations"][0]["entity_id_name"] == "Tenant ID"
        assert data["count"] == 1

    @pytest.mark.asyncio
    async def test_lists_integrations_for_org_user(
        self, org_user_context, mock_integration
    ):
        """Should list org-mapped integrations for org user."""
        with patch("src.core.database.get_db_context") as mock_db_ctx:
            mock_session = AsyncMock()
            mock_result = MagicMock()
            mock_result.scalars.return_value.all.return_value = [mock_integration]
            mock_session.execute = AsyncMock(return_value=mock_result)
            mock_db_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_db_ctx.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await list_integrations(org_user_context)

        data = result.structured_content
        assert "integrations" in data
        assert data["integrations"][0]["name"] == "Microsoft Graph"

    @pytest.mark.asyncio
    async def test_returns_empty_list(self, org_user_context):
        """Should return empty list when no integrations found."""
        with patch("src.core.database.get_db_context") as mock_db_ctx:
            mock_session = AsyncMock()
            mock_result = MagicMock()
            mock_result.scalars.return_value.all.return_value = []
            mock_session.execute = AsyncMock(return_value=mock_result)
            mock_db_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_db_ctx.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await list_integrations(org_user_context)

        data = result.structured_content
        assert data["integrations"] == []
        assert data["count"] == 0

    @pytest.mark.asyncio
    async def test_handles_database_error(self, org_user_context):
        """Should return error message on database failure."""
        with patch("src.core.database.get_db_context") as mock_db_ctx:
            mock_db_ctx.return_value.__aenter__ = AsyncMock(
                side_effect=Exception("Database connection failed")
            )
            mock_db_ctx.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await list_integrations(org_user_context)

        data = result.structured_content
        assert "error" in data
        assert "Error listing integrations" in data["error"]


# ==================== canonical Workflow execution wrapper ====================


class TestExecuteWorkflow:
    @pytest.mark.asyncio
    async def test_returns_async_execution_envelope(self, org_user_context):
        execution = {
            "execution_id": str(uuid4()),
            "workflow_id": str(uuid4()),
            "workflow_name": "test_workflow",
            "status": "Pending",
        }
        resolver = AsyncMock()
        resolver.resolve = AsyncMock(return_value=execution["workflow_id"])
        http = AsyncMock()
        http.__aenter__ = AsyncMock(return_value=MagicMock())
        http.__aexit__ = AsyncMock(return_value=None)
        with (
            patch(
                "src.services.mcp_server.tools.workflow.rest_client",
                return_value=http,
            ),
            patch("bifrost.refs.RefResolver", return_value=resolver),
            patch(
                "src.services.mcp_server.tools.workflow.call_rest",
                new=AsyncMock(return_value=(200, execution)),
            ) as call_rest,
        ):
            result = await bifrost_execute_workflow(
                org_user_context,
                "test_workflow",
                input_data={"key": "value"},
            )

        assert result.structured_content == execution
        call_rest.assert_awaited_once_with(
            org_user_context,
            "POST",
            "/api/workflows/execute",
            json_body={
                "workflow_id": execution["workflow_id"],
                "input_data": {"key": "value"},
                "sync": False,
            },
        )

    @pytest.mark.asyncio
    async def test_rejects_missing_workflow_ref(self, org_user_context):
        result = await bifrost_execute_workflow(org_user_context, "")
        assert result.structured_content is not None
        assert result.structured_content["error"] == "workflow_ref is required"


# ==================== BifrostMCPServer Tests ====================


class TestBifrostMCPServer:
    """Tests for the BifrostMCPServer class."""

    def test_creates_server_with_context(self, org_user_context):
        """Should create server with context."""
        from src.services.mcp_server.server import BifrostMCPServer

        server = BifrostMCPServer(org_user_context)
        assert server.context == org_user_context

# ==================== get_system_tool_ids Tests ====================


class TestGetSystemToolIds:
    """Tests for the get_system_tool_ids helper function."""

    def test_returns_all_system_tool_ids(self):
        """Should return IDs for all system tools."""
        from src.routers.tools import get_system_tool_ids, get_system_tools

        tool_ids = get_system_tool_ids()

        # Should return same number as SYSTEM_TOOLS
        assert len(tool_ids) == len(get_system_tools())

        # Should contain all expected IDs
        expected_ids = {tool.id for tool in get_system_tools()}
        assert set(tool_ids) == expected_ids

    def test_contains_expected_tools(self):
        """Should contain the expected system tool IDs."""
        from src.routers.tools import get_system_tool_ids

        tool_ids = get_system_tool_ids()

        # These are some of the core system tools (not exhaustive)
        expected = [
            "bifrost_execute_workflow",
            "bifrost_list_workflows",
            "list_integrations",
            "bifrost_list_forms",
            "get_docs",
            "search_knowledge",
        ]

        for tool_id in expected:
            assert tool_id in tool_ids, f"Missing expected tool: {tool_id}"

    def test_returns_list_not_generator(self):
        """Should return a list, not a generator or other iterable."""
        from src.routers.tools import get_system_tool_ids

        tool_ids = get_system_tool_ids()

        assert isinstance(tool_ids, list)


# ==================== MCPConfigService Tests ====================


class TestMCPConfigService:
    """Tests for the MCP configuration service."""

    @pytest.fixture
    def mock_session(self):
        """Create a mock database session."""
        return AsyncMock()

    @pytest.mark.asyncio
    async def test_get_config_returns_defaults_when_not_configured(self, mock_session):
        """Should return default config when no config exists."""
        from src.services.mcp_server.config_service import MCPConfigService

        # Mock no config found
        mock_result = MagicMock()
        mock_result.scalars.return_value.first.return_value = None
        mock_session.execute = AsyncMock(return_value=mock_result)

        service = MCPConfigService(mock_session)
        config = await service.get_config()

        assert config.enabled is True
        assert config.allowed_tool_ids is None
        assert config.blocked_tool_ids is None
        assert config.is_configured is False

    @pytest.mark.asyncio
    async def test_get_config_returns_stored_config(self, mock_session):
        """Should return stored config values."""
        from src.services.mcp_server.config_service import MCPConfigService

        # Mock config found
        mock_config = MagicMock()
        mock_config.value_json = {
            "enabled": False,
            "allowed_tool_ids": ["bifrost_execute_workflow", "bifrost_list_workflows"],
            "blocked_tool_ids": ["search_knowledge"],
        }
        mock_config.updated_at = datetime.now(timezone.utc)
        mock_config.updated_by = "admin@test.com"

        mock_result = MagicMock()
        mock_result.scalars.return_value.first.return_value = mock_config
        mock_session.execute = AsyncMock(return_value=mock_result)

        service = MCPConfigService(mock_session)
        config = await service.get_config()

        assert config.enabled is False
        assert config.allowed_tool_ids == [
            "bifrost_execute_workflow",
            "bifrost_list_workflows",
        ]
        assert config.blocked_tool_ids == ["search_knowledge"]
        assert config.is_configured is True
        assert config.configured_by == "admin@test.com"

    @pytest.mark.asyncio
    async def test_save_config_creates_new_config(self, mock_session):
        """Should create new config when none exists."""
        from src.services.mcp_server.config_service import MCPConfigService

        # Mock no existing config
        mock_result = MagicMock()
        mock_result.scalars.return_value.first.return_value = None
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.add = MagicMock()

        service = MCPConfigService(mock_session)
        config = await service.save_config(
            enabled=False,
            allowed_tool_ids=None,
            blocked_tool_ids=["search_knowledge"],
            updated_by="admin@test.com",
        )

        mock_session.add.assert_called_once()
        assert config.enabled is False
        assert config.blocked_tool_ids == ["search_knowledge"]

    @pytest.mark.asyncio
    async def test_save_config_updates_existing_config(self, mock_session):
        """Should update existing config."""
        from src.services.mcp_server.config_service import MCPConfigService

        # Mock existing config
        mock_config = MagicMock()
        mock_config.value_json = {"enabled": True}
        mock_result = MagicMock()
        mock_result.scalars.return_value.first.return_value = mock_config
        mock_session.execute = AsyncMock(return_value=mock_result)

        service = MCPConfigService(mock_session)
        config = await service.save_config(
            enabled=False,
            allowed_tool_ids=["bifrost_execute_workflow"],
            blocked_tool_ids=[],
            updated_by="admin@test.com",
        )

        assert config.enabled is False
        assert mock_config.value_json["enabled"] is False
        assert mock_config.updated_by == "admin@test.com"

    @pytest.mark.asyncio
    async def test_delete_config_removes_existing(self, mock_session):
        """Should delete existing config via bulk DELETE."""
        from src.services.mcp_server.config_service import MCPConfigService

        # Mock bulk DELETE returning rowcount=1
        mock_result = MagicMock()
        mock_result.rowcount = 1
        mock_session.execute = AsyncMock(return_value=mock_result)

        service = MCPConfigService(mock_session)
        deleted = await service.delete_config()

        assert deleted is True
        mock_session.execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_delete_config_returns_false_when_none_exists(self, mock_session):
        """Should return False when no config to delete (rowcount=0)."""
        from src.services.mcp_server.config_service import MCPConfigService

        # Mock bulk DELETE returning rowcount=0
        mock_result = MagicMock()
        mock_result.rowcount = 0
        mock_session.execute = AsyncMock(return_value=mock_result)

        service = MCPConfigService(mock_session)
        deleted = await service.delete_config()

        assert deleted is False


class TestMCPConfigCache:
    """Tests for the MCP config caching."""

    @pytest.mark.asyncio
    async def test_invalidate_cache_clears_cached_values(self):
        """Should clear cached config on invalidation."""
        from src.services.mcp_server.config_service import (
            invalidate_mcp_config_cache,
        )

        # Call invalidate
        invalidate_mcp_config_cache()

        # Import again to check values
        from src.services.mcp_server import config_service

        assert config_service._cached_config is None
        assert config_service._cache_time is None


# ==================== Tool Result Display Text Tests ====================


class TestToolResultDisplayText:
    """Tests that structured data is embedded in display text for older MCP clients."""

    def test_success_result_includes_json_in_content(self):
        """success_result should append JSON data to display text."""
        from src.services.mcp_server.tool_result import success_result

        result = success_result("Found 2 items", {"items": [{"id": "abc"}], "count": 2})

        text = result.content[0].text
        # Display text should contain the human summary
        assert "Found 2 items" in text
        # Display text should also contain the structured data as JSON
        assert '"items"' in text
        assert '"abc"' in text
        assert '"count": 2' in text
        # structured_content should still be set
        assert result.structured_content == {"items": [{"id": "abc"}], "count": 2}

    def test_success_result_without_data(self):
        """success_result without data should only have display text."""
        from src.services.mcp_server.tool_result import success_result

        result = success_result("No data here")

        assert result.content[0].text == "No data here"
        assert result.structured_content is None

    def test_error_result_includes_json_in_content(self):
        """error_result should append JSON error data to display text."""
        from src.services.mcp_server.tool_result import error_result

        result = error_result("Something failed", {"details": "db timeout"})

        text = result.content[0].text
        assert "Error: Something failed" in text
        assert '"error": "Something failed"' in text
        assert '"details": "db timeout"' in text
        assert result.structured_content["error"] == "Something failed"

    def test_workflow_ids_visible_in_content(self):
        """Workflow IDs should be visible in display text for LLM parsing."""
        from src.services.mcp_server.tool_result import success_result

        wf_id = str(uuid4())
        result = success_result(
            "Found 1 workflow(s):",
            {"workflows": [{"id": wf_id, "name": "my_wf"}], "count": 1},
        )

        text = result.content[0].text
        # The UUID should be in the content text
        assert wf_id in text
        assert "my_wf" in text


# ==================== bifrost_register_workflow Tests ====================


class TestRegisterWorkflow:
    """Input guards for the canonical register wrapper."""

    @pytest.mark.asyncio
    async def test_rejects_missing_path(self, platform_admin_context):
        from src.services.mcp_server.tools.workflow import bifrost_register_workflow

        result = await bifrost_register_workflow(
            platform_admin_context, "", "my_function"
        )
        data = result.structured_content
        assert "error" in data
        assert "path is required" in data["error"]

    @pytest.mark.asyncio
    async def test_rejects_missing_function_name(self, platform_admin_context):
        from src.services.mcp_server.tools.workflow import bifrost_register_workflow

        result = await bifrost_register_workflow(
            platform_admin_context, "workflows/test.py", ""
        )
        data = result.structured_content
        assert "error" in data
        assert "function_name is required" in data["error"]


class TestMCPContextInputCoercion:
    """JWT claims arrive at MCPContext as strings (`token.claims.get("org_id")`
    returns `str` because auth.py wraps `str(user.organization_id)` when
    minting the token). Downstream callers pass these to OrgScopedRepository,
    which compares against UUID-typed ORM columns. `UUID == str` is False
    in Python — that mismatch caused every non-admin MCP tool execution to
    fail with 'Workflow not found'. Coerce at the boundary."""

    def test_string_org_id_coerced_to_uuid(self):
        org = UUID("00000000-0000-0000-0000-000000000002")
        ctx = MCPContext(user_id=str(uuid4()), org_id=str(org))
        assert ctx.org_id == org
        assert isinstance(ctx.org_id, UUID)

    def test_string_user_id_coerced_to_uuid(self):
        user = uuid4()
        ctx = MCPContext(user_id=str(user))
        assert ctx.user_id == user
        assert isinstance(ctx.user_id, UUID)

    def test_uuid_inputs_passed_through(self):
        user, org = uuid4(), uuid4()
        ctx = MCPContext(user_id=user, org_id=org)
        assert ctx.user_id is user
        assert ctx.org_id is org

    def test_none_org_id_remains_none(self):
        ctx = MCPContext(user_id=uuid4(), org_id=None)
        assert ctx.org_id is None
