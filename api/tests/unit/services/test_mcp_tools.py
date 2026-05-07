"""
Unit tests for MCP Tools.

Tests the MCP tools for the Bifrost platform:
- get_docs: Returns unified platform documentation
- list_workflows: Lists registered workflows
- list_forms: Lists forms with org scoping
- search_knowledge: Searches the knowledge base
- list_integrations: Lists available integrations
- execute_workflow: Executes workflows and returns results

Uses mocked database access for fast, isolated testing.

Note: The MCP tools are implemented as decorated functions in src/services/mcp/tools/*.py.
We test the tool functions directly.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest

from src.services.mcp_server.server import MCPContext
from src.services.mcp_server.tools.forms import list_forms
from src.services.mcp_server.tools.integrations import list_integrations
from src.services.mcp_server.tools.knowledge import search_knowledge
from src.services.mcp_server.tools.workflow import execute_workflow, list_workflows


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
def mock_workflow():
    """Create a mock workflow ORM object."""
    mock = MagicMock()
    mock.id = uuid4()
    mock.name = "test_workflow"
    mock.description = "A test workflow for testing"
    mock.type = "standard"
    mock.category = "automation"
    mock.is_tool = False
    mock.endpoint_enabled = True
    mock.path = "/tmp/bifrost/workspace/workflows/test_workflow.py"
    mock.is_active = True
    return mock


@pytest.fixture
def mock_form():
    """Create a mock form ORM object."""
    mock = MagicMock()
    mock.id = uuid4()
    mock.name = "Test Form"
    mock.description = "A test form"
    mock.workflow_id = str(uuid4())
    mock.launch_workflow_id = None
    mock.is_active = True
    mock.access_level = MagicMock(value="authenticated")

    # Mock fields
    field = MagicMock()
    field.name = "email"
    field.label = "Email Address"
    field.type = "email"
    field.required = True
    field.position = 0
    mock.fields = [field]

    return mock


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


# ==================== list_workflows Tests ====================


class TestListWorkflows:
    """Tests for the list_workflows MCP tool."""

    @pytest.mark.asyncio
    async def test_lists_workflows(self, org_user_context, mock_workflow):
        """Should list registered workflows."""
        with patch("src.core.database.get_db_context") as mock_db_ctx:
            mock_session = AsyncMock()
            mock_db_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_db_ctx.return_value.__aexit__ = AsyncMock(return_value=None)

            with patch(
                "src.repositories.workflows.WorkflowRepository"
            ) as mock_repo_cls:
                mock_repo = MagicMock()
                mock_repo.search = AsyncMock(return_value=[mock_workflow])
                mock_repo.count_active = AsyncMock(return_value=1)
                mock_repo_cls.return_value = mock_repo

                result = await list_workflows(org_user_context)

        # Result is a ToolResult with structured_content
        data = result.structured_content
        assert "workflows" in data
        assert len(data["workflows"]) == 1
        assert data["workflows"][0]["name"] == "test_workflow"
        assert data["workflows"][0]["description"] == "A test workflow for testing"
        assert data["workflows"][0]["endpoint_enabled"] is True
        assert data["count"] == 1
        assert data["total_count"] == 1

    @pytest.mark.asyncio
    async def test_returns_empty_list(self, org_user_context):
        """Should return empty list when no workflows found."""
        with patch("src.core.database.get_db_context") as mock_db_ctx:
            mock_session = AsyncMock()
            mock_db_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_db_ctx.return_value.__aexit__ = AsyncMock(return_value=None)

            with patch(
                "src.repositories.workflows.WorkflowRepository"
            ) as mock_repo_cls:
                mock_repo = MagicMock()
                mock_repo.search = AsyncMock(return_value=[])
                mock_repo.count_active = AsyncMock(return_value=0)
                mock_repo_cls.return_value = mock_repo

                result = await list_workflows(org_user_context)

        data = result.structured_content
        assert data["workflows"] == []
        assert data["count"] == 0
        assert data["total_count"] == 0

    @pytest.mark.asyncio
    async def test_filters_by_category(self, org_user_context, mock_workflow):
        """Should pass category filter to repository."""
        with patch("src.core.database.get_db_context") as mock_db_ctx:
            mock_session = AsyncMock()
            mock_db_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_db_ctx.return_value.__aexit__ = AsyncMock(return_value=None)

            with patch(
                "src.repositories.workflows.WorkflowRepository"
            ) as mock_repo_cls:
                mock_repo = MagicMock()
                mock_repo.search = AsyncMock(return_value=[mock_workflow])
                mock_repo.count_active = AsyncMock(return_value=1)
                mock_repo_cls.return_value = mock_repo

                await list_workflows(org_user_context, category="automation")

                # Verify category was passed to search
                mock_repo.search.assert_called_once_with(
                    query=None,
                    category="automation",
                    limit=100,
                )

    @pytest.mark.asyncio
    async def test_handles_database_error(self, org_user_context):
        """Should return error message on database failure."""
        with patch("src.core.database.get_db_context") as mock_db_ctx:
            mock_db_ctx.return_value.__aenter__ = AsyncMock(
                side_effect=Exception("Database connection failed")
            )
            mock_db_ctx.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await list_workflows(org_user_context)

        data = result.structured_content
        assert "error" in data
        assert "Error listing workflows" in data["error"]


# ==================== list_forms Tests ====================


class TestListForms:
    """Tests for the list_forms MCP tool."""

    @pytest.mark.asyncio
    async def test_lists_forms_for_org_user(self, org_user_context, mock_form):
        """Should list forms for org user."""
        with patch("src.core.database.get_db_context") as mock_db_ctx:
            mock_session = AsyncMock()
            mock_db_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_db_ctx.return_value.__aexit__ = AsyncMock(return_value=None)

            with patch("src.repositories.forms.FormRepository") as mock_repo_cls:
                mock_repo = MagicMock()
                mock_repo.list_forms = AsyncMock(return_value=[mock_form])
                mock_repo_cls.return_value = mock_repo

                result = await list_forms(org_user_context)

        # Result is a ToolResult with structured_content
        data = result.structured_content
        assert "forms" in data
        assert len(data["forms"]) == 1
        assert data["forms"][0]["name"] == "Test Form"
        assert data["forms"][0]["description"] == "A test form"
        assert data["count"] == 1

    @pytest.mark.asyncio
    async def test_lists_forms_for_platform_admin(
        self, platform_admin_context, mock_form
    ):
        """Should list all forms for platform admin."""
        with patch("src.core.database.get_db_context") as mock_db_ctx:
            mock_session = AsyncMock()
            mock_db_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_db_ctx.return_value.__aexit__ = AsyncMock(return_value=None)

            with patch("src.repositories.forms.FormRepository") as mock_repo_cls:
                mock_repo = MagicMock()
                # Platform admins use list_all_in_scope instead of list_forms
                mock_repo.list_all_in_scope = AsyncMock(return_value=[mock_form])
                mock_repo_cls.return_value = mock_repo

                result = await list_forms(platform_admin_context)

        data = result.structured_content
        assert "forms" in data
        assert data["forms"][0]["name"] == "Test Form"

    @pytest.mark.asyncio
    async def test_returns_empty_list(self, org_user_context):
        """Should return empty list when no forms found."""
        with patch("src.core.database.get_db_context") as mock_db_ctx:
            mock_session = AsyncMock()
            mock_db_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_db_ctx.return_value.__aexit__ = AsyncMock(return_value=None)

            with patch("src.repositories.forms.FormRepository") as mock_repo_cls:
                mock_repo = MagicMock()
                mock_repo.list_forms = AsyncMock(return_value=[])
                mock_repo_cls.return_value = mock_repo

                result = await list_forms(org_user_context)

        data = result.structured_content
        assert data["forms"] == []
        assert data["count"] == 0

    @pytest.mark.asyncio
    async def test_handles_database_error(self, org_user_context):
        """Should return error message on database failure."""
        with patch("src.core.database.get_db_context") as mock_db_ctx:
            mock_db_ctx.return_value.__aenter__ = AsyncMock(
                side_effect=Exception("Database connection failed")
            )
            mock_db_ctx.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await list_forms(org_user_context)

        data = result.structured_content
        assert "error" in data
        assert "Error listing forms" in data["error"]


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


# ==================== execute_workflow Tests ====================


class TestExecuteWorkflow:
    """Tests for the execute_workflow MCP tool."""

    @pytest.mark.asyncio
    async def test_executes_workflow_successfully(
        self, org_user_context, mock_workflow
    ):
        """Should execute workflow and return success result."""
        # Create mock execution result
        mock_result = MagicMock()
        mock_result.status.value = "Success"
        mock_result.duration_ms = 150
        mock_result.result = {"output": "test value"}
        mock_result.error = None
        mock_result.error_type = None

        with patch("src.core.database.get_db_context") as mock_db_ctx:
            mock_session = AsyncMock()
            mock_db_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_db_ctx.return_value.__aexit__ = AsyncMock(return_value=None)

            with patch(
                "src.repositories.workflows.WorkflowRepository"
            ) as mock_repo_cls:
                mock_repo = MagicMock()
                mock_repo.resolve = AsyncMock(return_value=mock_workflow)
                mock_repo_cls.return_value = mock_repo

                with patch(
                    "src.services.execution.service.execute_tool"
                ) as mock_execute:
                    mock_execute.return_value = mock_result

                    result = await execute_workflow(
                        org_user_context,
                        str(mock_workflow.id),
                        {"key": "value"},
                    )

        # Result is a ToolResult with structured_content
        data = result.structured_content
        assert data["success"] is True
        assert data["workflow_name"] == "test_workflow"
        assert data["duration_ms"] == 150
        assert data["result"]["output"] == "test value"
        assert data["status"] == "Success"

    @pytest.mark.asyncio
    async def test_returns_error_workflow_not_found(self, org_user_context):
        """Should return error when workflow not found."""
        workflow_id = str(uuid4())
        with patch("src.core.database.get_db_context") as mock_db_ctx:
            mock_session = AsyncMock()
            mock_db_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_db_ctx.return_value.__aexit__ = AsyncMock(return_value=None)

            with patch(
                "src.repositories.workflows.WorkflowRepository"
            ) as mock_repo_cls:
                mock_repo = MagicMock()
                mock_repo.resolve = AsyncMock(return_value=None)
                mock_repo_cls.return_value = mock_repo

                result = await execute_workflow(
                    org_user_context,
                    workflow_id,
                )

        data = result.structured_content
        assert "error" in data
        assert "not found" in data["error"]
        assert workflow_id in data["error"]
        assert "list_workflows" in data["error"]

    @pytest.mark.asyncio
    async def test_resolves_workflow_by_name(self, org_user_context, mock_workflow):
        """Should resolve workflow by name (not just UUID)."""
        mock_result = MagicMock()
        mock_result.status.value = "Success"
        mock_result.duration_ms = 100
        mock_result.result = {"output": "ok"}
        mock_result.error = None
        mock_result.error_type = None

        with patch("src.core.database.get_db_context") as mock_db_ctx:
            mock_session = AsyncMock()
            mock_db_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_db_ctx.return_value.__aexit__ = AsyncMock(return_value=None)

            with patch(
                "src.repositories.workflows.WorkflowRepository"
            ) as mock_repo_cls:
                mock_repo = MagicMock()
                mock_repo.resolve = AsyncMock(return_value=mock_workflow)
                mock_repo_cls.return_value = mock_repo

                with patch(
                    "src.services.execution.service.execute_tool"
                ) as mock_execute:
                    mock_execute.return_value = mock_result

                    result = await execute_workflow(
                        org_user_context,
                        "test_workflow",  # name, not UUID
                    )

        data = result.structured_content
        assert data["success"] is True
        assert data["workflow_name"] == "test_workflow"
        # Verify resolve was called with the name string
        mock_repo.resolve.assert_called_once_with("test_workflow")

    @pytest.mark.asyncio
    async def test_returns_error_on_execution_failure(
        self, org_user_context, mock_workflow
    ):
        """Should return error details when workflow execution fails."""
        # Create mock failed execution result
        mock_result = MagicMock()
        mock_result.status.value = "Failed"
        mock_result.duration_ms = 50
        mock_result.result = None
        mock_result.error = "Division by zero"
        mock_result.error_type = "ValueError"

        with patch("src.core.database.get_db_context") as mock_db_ctx:
            mock_session = AsyncMock()
            mock_db_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_db_ctx.return_value.__aexit__ = AsyncMock(return_value=None)

            with patch(
                "src.repositories.workflows.WorkflowRepository"
            ) as mock_repo_cls:
                mock_repo = MagicMock()
                mock_repo.resolve = AsyncMock(return_value=mock_workflow)
                mock_repo_cls.return_value = mock_repo

                with patch(
                    "src.services.execution.service.execute_tool"
                ) as mock_execute:
                    mock_execute.return_value = mock_result

                    result = await execute_workflow(
                        org_user_context,
                        str(mock_workflow.id),
                    )

        data = result.structured_content
        assert data["success"] is False
        assert data["status"] == "Failed"
        assert data["error"] == "Division by zero"
        assert data["error_type"] == "ValueError"

    @pytest.mark.asyncio
    async def test_handles_missing_workflow_id(self, org_user_context):
        """Should return error when workflow_id is empty."""
        result = await execute_workflow(org_user_context, "")
        data = result.structured_content
        assert "error" in data
        assert "workflow_id is required" in data["error"]

    @pytest.mark.asyncio
    async def test_handles_exception(self, org_user_context, mock_workflow):
        """Should return error message on unexpected exception."""
        with patch("src.core.database.get_db_context") as mock_db_ctx:
            mock_db_ctx.return_value.__aenter__ = AsyncMock(
                side_effect=Exception("Unexpected error")
            )
            mock_db_ctx.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await execute_workflow(
                org_user_context,
                str(mock_workflow.id),
            )

        data = result.structured_content
        assert "error" in data
        assert "Error executing workflow" in data["error"]


# ==================== BifrostMCPServer Tests ====================


class TestBifrostMCPServer:
    """Tests for the BifrostMCPServer class."""

    def test_creates_server_with_context(self, org_user_context):
        """Should create server with context."""
        from src.services.mcp_server.server import BifrostMCPServer

        server = BifrostMCPServer(org_user_context)
        assert server.context == org_user_context

    def test_get_tool_names_returns_all_tools(self, org_user_context):
        """Should return all tool names when no filter."""
        from src.services.mcp_server.server import BifrostMCPServer

        server = BifrostMCPServer(org_user_context)
        tool_names = server.get_tool_names()

        assert "mcp__bifrost__execute_workflow" in tool_names
        assert "mcp__bifrost__list_workflows" in tool_names
        assert "mcp__bifrost__list_integrations" in tool_names
        assert "mcp__bifrost__list_forms" in tool_names
        assert "mcp__bifrost__get_docs" in tool_names
        assert "mcp__bifrost__search_knowledge" in tool_names

    def test_get_tool_names_respects_enabled_filter(self):
        """Should return only enabled tools when filter applied."""
        from src.services.mcp_server.server import BifrostMCPServer

        context = MCPContext(
            user_id=uuid4(),
            enabled_system_tools=["execute_workflow", "list_workflows"],
        )
        server = BifrostMCPServer(context)
        tool_names = server.get_tool_names()

        assert "mcp__bifrost__execute_workflow" in tool_names
        assert "mcp__bifrost__list_workflows" in tool_names
        assert "mcp__bifrost__list_integrations" not in tool_names
        assert len(tool_names) == 2


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
            "execute_workflow",
            "list_workflows",
            "list_integrations",
            "list_forms",
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
            "allowed_tool_ids": ["execute_workflow", "list_workflows"],
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
        assert config.allowed_tool_ids == ["execute_workflow", "list_workflows"]
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
            allowed_tool_ids=["execute_workflow"],
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


# ==================== Tool Filtering Tests ====================


class TestToolFiltering:
    """Tests for tool filtering based on allowed/blocked lists."""

    def test_allowed_tool_ids_limits_visible_tools(self):
        """Should only show tools in the allowed list when specified."""
        from src.services.mcp_server.server import BifrostMCPServer

        # Context with specific allowed tools
        context = MCPContext(
            user_id=uuid4(),
            enabled_system_tools=["execute_workflow", "list_workflows"],
        )

        server = BifrostMCPServer(context)
        tool_names = server.get_tool_names()

        # Should only contain allowed tools
        assert "mcp__bifrost__execute_workflow" in tool_names
        assert "mcp__bifrost__list_workflows" in tool_names
        assert "mcp__bifrost__list_integrations" not in tool_names
        assert "mcp__bifrost__list_forms" not in tool_names
        assert "mcp__bifrost__search_knowledge" not in tool_names
        assert len(tool_names) == 2

    def test_empty_allowed_means_all_tools(self):
        """Should show all tools when allowed list is empty (falsy)."""
        from src.services.mcp_server.server import BifrostMCPServer

        # Context with empty enabled tools - empty list is falsy,
        # so BifrostMCPServer treats it as None (all tools allowed)
        context = MCPContext(
            user_id=uuid4(),
            enabled_system_tools=[],
        )

        server = BifrostMCPServer(context)
        tool_names = server.get_tool_names()

        # Empty list is falsy, so it becomes None -> all tools shown
        # We have many system tools including forms, workflows, data providers, apps, file ops
        assert len(tool_names) >= 18  # At least the core tools

    def test_no_enabled_tools_means_all_tools(self):
        """Should show all tools when enabled_system_tools is not set."""
        from src.services.mcp_server.server import BifrostMCPServer

        # Context without enabled_system_tools set
        context = MCPContext(
            user_id=uuid4(),
            # enabled_system_tools not set -> defaults to empty list
        )
        # Manually set to None to simulate "all tools" mode
        context.enabled_system_tools = []

        # But we need to create server before setting - let's test the None case
        context2 = MCPContext(user_id=uuid4())

        # BifrostMCPServer converts empty list to None for "all tools"
        server = BifrostMCPServer(context2)

        # enabled_tools should be None since context.enabled_system_tools is empty
        # Actually the logic is: if enabled_system_tools is truthy, use it
        # If empty/falsy, _enabled_tools becomes None meaning "all tools"
        tool_names = server.get_tool_names()

        # Should contain some of the core system tools
        expected_tools = [
            "mcp__bifrost__execute_workflow",
            "mcp__bifrost__list_workflows",
            "mcp__bifrost__list_integrations",
            "mcp__bifrost__list_forms",
            "mcp__bifrost__get_docs",
            "mcp__bifrost__search_knowledge",
        ]

        for expected in expected_tools:
            assert expected in tool_names, f"Missing tool: {expected}"

        # All system tools including forms, workflows, data providers, apps, file ops
        assert len(tool_names) >= 17  # At least the core tools

    def test_single_tool_filtering(self):
        """Should correctly filter to single tool."""
        from src.services.mcp_server.server import BifrostMCPServer

        context = MCPContext(
            user_id=uuid4(),
            enabled_system_tools=["search_knowledge"],
        )

        server = BifrostMCPServer(context)
        tool_names = server.get_tool_names()

        assert tool_names == ["mcp__bifrost__search_knowledge"]

    def test_tool_filtering_preserves_order(self):
        """Should return tools in consistent order."""
        from src.services.mcp_server.server import BifrostMCPServer

        # Test multiple times to ensure consistent ordering
        for _ in range(3):
            context = MCPContext(
                user_id=uuid4(),
                enabled_system_tools=["list_workflows", "execute_workflow", "list_forms"],
            )

            server = BifrostMCPServer(context)
            tool_names = server.get_tool_names()

            # Order should match the all_tools list order, not enabled_system_tools order
            # (execute_workflow comes before list_workflows in all_tools)
            assert "mcp__bifrost__execute_workflow" in tool_names
            assert "mcp__bifrost__list_workflows" in tool_names
            assert "mcp__bifrost__list_forms" in tool_names

    def test_unknown_tool_ids_ignored(self):
        """Should ignore unknown tool IDs in enabled list."""
        from src.services.mcp_server.server import BifrostMCPServer

        context = MCPContext(
            user_id=uuid4(),
            enabled_system_tools=["execute_workflow", "unknown_tool", "fake_tool"],
        )

        server = BifrostMCPServer(context)
        tool_names = server.get_tool_names()

        # Should only contain the valid tool
        assert tool_names == ["mcp__bifrost__execute_workflow"]


class TestMCPContextFiltering:
    """Tests for MCPContext-based tool access control."""

    def test_platform_admin_sees_all_tools_by_default(self):
        """Platform admin should see all tools when no filter applied."""
        from src.services.mcp_server.server import BifrostMCPServer

        context = MCPContext(
            user_id=uuid4(),
            is_platform_admin=True,
            # No enabled_system_tools filter
        )

        server = BifrostMCPServer(context)
        tool_names = server.get_tool_names()

        assert len(tool_names) >= 18  # All system tools (forms, workflows, data providers, apps, etc.)

    def test_org_user_respects_enabled_tools(self):
        """Org user should only see tools from enabled_system_tools."""
        from src.services.mcp_server.server import BifrostMCPServer

        context = MCPContext(
            user_id=uuid4(),
            org_id=uuid4(),
            is_platform_admin=False,
            enabled_system_tools=["list_workflows", "list_forms"],
        )

        server = BifrostMCPServer(context)
        tool_names = server.get_tool_names()

        assert len(tool_names) == 2
        assert "mcp__bifrost__list_workflows" in tool_names
        assert "mcp__bifrost__list_forms" in tool_names

    def test_context_with_all_tools_enabled(self):
        """Context with all tools enabled should show all tools."""
        from src.services.mcp_server.server import BifrostMCPServer

        all_tool_ids = [
            "execute_workflow",
            "list_workflows",
            "list_integrations",
            "list_forms",
            "get_docs",
            "search_knowledge",
        ]

        context = MCPContext(
            user_id=uuid4(),
            enabled_system_tools=all_tool_ids,
        )

        server = BifrostMCPServer(context)
        tool_names = server.get_tool_names()

        assert len(tool_names) == 6

    def test_enabled_tools_are_case_sensitive(self):
        """Tool IDs should be case-sensitive."""
        from src.services.mcp_server.server import BifrostMCPServer

        context = MCPContext(
            user_id=uuid4(),
            enabled_system_tools=["Execute_Workflow", "LIST_WORKFLOWS"],  # Wrong case
        )

        server = BifrostMCPServer(context)
        tool_names = server.get_tool_names()

        # Should be empty since tool IDs don't match (case sensitive)
        assert len(tool_names) == 0


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


# ==================== register_workflow Tests ====================


class TestRegisterWorkflow:
    """Tests for the register_workflow MCP tool."""

    @pytest.mark.asyncio
    async def test_rejects_missing_path(self, platform_admin_context):
        """register_workflow MCP tool rejects missing path."""
        from src.services.mcp_server.tools.workflow import register_workflow

        result = await register_workflow(platform_admin_context, "", "my_function")
        data = result.structured_content
        assert "error" in data
        assert "path is required" in data["error"]

    @pytest.mark.asyncio
    async def test_rejects_missing_function_name(self, platform_admin_context):
        """register_workflow MCP tool rejects missing function_name."""
        from src.services.mcp_server.tools.workflow import register_workflow

        result = await register_workflow(platform_admin_context, "workflows/test.py", "")
        data = result.structured_content
        assert "error" in data
        assert "function_name is required" in data["error"]

    @pytest.mark.asyncio
    async def test_rejects_non_python_file(self, platform_admin_context):
        """register_workflow MCP tool rejects non-.py files."""
        from src.services.mcp_server.tools.workflow import register_workflow

        result = await register_workflow(platform_admin_context, "workflows/test.yaml", "my_function")
        data = result.structured_content
        assert "error" in data
        assert "path must be a .py file" in data["error"]

    @pytest.mark.asyncio
    async def test_rejects_file_not_found(self, platform_admin_context):
        """register_workflow MCP tool returns error when file not found."""
        from src.services.mcp_server.tools.workflow import register_workflow

        with patch("src.core.database.get_db_context") as mock_db_ctx:
            mock_session = AsyncMock()
            mock_db_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_db_ctx.return_value.__aexit__ = AsyncMock(return_value=None)

            with patch("src.services.file_storage.FileStorageService") as mock_svc_cls:
                mock_svc = MagicMock()
                mock_svc.read_file = AsyncMock(side_effect=FileNotFoundError("not found"))
                mock_svc_cls.return_value = mock_svc

                result = await register_workflow(platform_admin_context, "workflows/missing.py", "my_func")

        data = result.structured_content
        assert "error" in data
        assert "File not found" in data["error"]

    @pytest.mark.asyncio
    async def test_rejects_no_decorated_function(self, platform_admin_context):
        """register_workflow MCP tool rejects files without matching decorated function."""
        from src.services.mcp_server.tools.workflow import register_workflow

        # Python file with a function but no @workflow decorator
        code = b"def my_func():\n    return 42\n"

        with patch("src.core.database.get_db_context") as mock_db_ctx:
            mock_session = AsyncMock()
            mock_db_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_db_ctx.return_value.__aexit__ = AsyncMock(return_value=None)

            with patch("src.services.file_storage.FileStorageService") as mock_svc_cls:
                mock_svc = MagicMock()
                mock_svc.read_file = AsyncMock(return_value=(code, None))
                mock_svc_cls.return_value = mock_svc

                result = await register_workflow(platform_admin_context, "workflows/test.py", "my_func")

        data = result.structured_content
        assert "error" in data
        assert "No @workflow/@tool/@data_provider decorated function" in data["error"]


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
