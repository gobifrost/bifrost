"""
Unit tests for MCP Tools.

Tests the MCP tools for the Bifrost platform:
- get_docs: Returns unified platform documentation
- bifrost_form_list: Lists Forms visible to the caller
- search_knowledge: Searches the knowledge base

Uses mocked database access for fast, isolated testing.

Note: The MCP tools are implemented as decorated functions in src/services/mcp/tools/*.py.
We test the tool functions directly.

``bifrost_workflow_list``/``get``/``validate``/``register``/``execute``/``update``/
``delete``/``role_grant``/``role_revoke`` are thin REST wrappers as of RBAC R1b
batch 5 — their coverage lives in ``test_mcp_thin_wrapper.py`` (ORM-import guard)
and ``tests/e2e/mcp/test_mcp_parity.py`` (live REST round-trip), not here.
"""

from datetime import datetime, timezone
from typing import Annotated, Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from pydantic import Field

from src.core.principal import UserPrincipal
from src.services.authorization.context import AuthorizationContext
from src.services.authorization.enforce import Caller
from src.services.mcp_server.server import MCPContext
from src.services.mcp_server.tools.knowledge import search_knowledge


# ==================== Fixtures ====================


@pytest.fixture(autouse=True)
def _mcp_caller(monkeypatch):
    async def load(_db, context):
        return Caller(
            UserPrincipal(user_id=context.user_id, email=context.user_email, organization_id=context.org_id),
            AuthorizationContext(user_id=context.user_id, home_organization_id=context.org_id, base_role_id=uuid4(), is_external=False, base_permissions=frozenset({"knowledge.read"})),
        )

    monkeypatch.setattr("src.services.mcp_server.authorization.load_mcp_caller", AsyncMock(side_effect=load))


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
                "src.services.embeddings.get_embedding_client", new_callable=AsyncMock
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
        # Compact default: excerpt served, full content withheld.
        assert data["compact"] is True
        assert data["results"][0]["excerpt"] == "This is documentation about the SDK"
        assert data["results"][0]["title"] == "SDK Guide"
        assert data["results"][0]["confidence"] == 0.85
        assert "content" not in data["results"][0]
        assert data["count"] == 1
        assert mock_repo.search.await_args.kwargs["query_text"] == "SDK documentation"

    @pytest.mark.asyncio
    async def test_full_content_requires_explicit_flag(
        self, org_user_context, mock_knowledge_document
    ):
        """include_full_content=true returns the full document content."""
        org_user_context.accessible_namespaces = ["test-namespace"]

        with patch("src.core.database.get_db_context") as mock_db_ctx:
            mock_session = AsyncMock()
            mock_db_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_db_ctx.return_value.__aexit__ = AsyncMock(return_value=None)

            with patch(
                "src.services.embeddings.get_embedding_client", new_callable=AsyncMock
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
                        org_user_context,
                        "SDK documentation",
                        include_full_content=True,
                    )

        data = result.structured_content
        assert data["results"][0]["content"] == "This is documentation about the SDK"

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
                "src.services.embeddings.get_embedding_client", new_callable=AsyncMock
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
                "src.services.embeddings.get_embedding_client", new_callable=AsyncMock
            ) as mock_embed_client:
                mock_embed_client.side_effect = Exception("Embedding service error")

                result = await search_knowledge(org_user_context, "test query")

        data = result.structured_content
        assert "error" in data
        assert "Error searching knowledge" in data["error"]


# ==================== BifrostMCPServer Tests ====================


class TestBifrostMCPServer:
    """Tests for the BifrostMCPServer class."""

    def test_creates_server_with_context(self, org_user_context):
        """Should create server with context."""
        from src.services.mcp_server.server import BifrostMCPServer

        server = BifrostMCPServer(org_user_context)
        assert server.context == org_user_context


@pytest.mark.asyncio
async def test_sdk_generator_keeps_json_alias_in_schema_and_calls_python_parameter():
    """The SDK passes aliases to wrappers, while handlers use Python names."""
    from src.services.mcp_server.generators.fastmcp_generator import (
        register_tool_with_context,
    )
    from src.services.mcp_server.tool_result import direct_result

    seen: dict[str, Any] = {}

    async def aliased_tool(
        context: Any,
        async_: Annotated[bool | None, Field(alias="async")] = None,
    ):
        seen["context"] = context
        seen["async_"] = async_
        return direct_result({"ok": True})

    mcp = FastMCP("test")
    context = object()
    register_tool_with_context(
        mcp,
        aliased_tool,
        "aliased_tool",
        "Test alias forwarding.",
        lambda: context,
    )

    tool = mcp._tool_manager.get_tool("aliased_tool")
    assert tool is not None
    assert "async" in tool.parameters["properties"]
    assert "async_" not in tool.parameters["properties"]

    await tool.run({"async": True}, convert_result=True)

    assert seen == {"context": context, "async_": True}

@pytest.mark.asyncio
async def test_sdk_refresh_rebinds_a_reused_workflow_tool_name(monkeypatch, org_user_context):
    """A name reassigned during refresh must call its current workflow."""
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from uuid import uuid4

    from src.services.mcp_server import server

    @asynccontextmanager
    async def database():
        yield object()

    old_id, current_id = uuid4(), uuid4()
    workflows = [SimpleNamespace(
        id=old_id, name="Reused Tool", description="Old description",
        parameters_schema=[],
    )]
    current = SimpleNamespace(
        id=current_id, name="Reused Tool", description="Current description",
        parameters_schema=[],
    )
    registry = AsyncMock(side_effect=lambda: workflows)
    execute = AsyncMock(return_value={"ok": True})
    monkeypatch.setattr("src.core.database.get_db_context", database)
    monkeypatch.setattr("src.services.tool_registry.ToolRegistry.get_all_tools", registry)
    monkeypatch.setattr(server, "_fastmcp_instance", None)
    monkeypatch.setattr(server, "_WORKFLOW_ID_TO_TOOL_NAME", {})
    monkeypatch.setattr(server, "_get_context_from_token", lambda: org_user_context)
    monkeypatch.setattr(server, "_execute_workflow_tool_impl", execute)
    mcp = FastMCP("refresh-test")

    assert await server._register_workflow_tools(mcp) == 1
    original = mcp._tool_manager.get_tool("reused_tool")
    assert original is not None
    workflows[:] = [current]
    assert await server.refresh_workflow_tools() == 1
    tool = mcp._tool_manager.get_tool("reused_tool")
    assert tool is not None
    assert tool.fn is not original.fn
    await tool.run({})

    execute.assert_awaited_once_with(
        org_user_context, str(current_id), current.name,
    )
    assert tool.description == current.description


@pytest.mark.asyncio
async def test_sdk_workflow_tools_share_metadata_and_keep_dynamic_schema(
    monkeypatch, org_user_context
):
    """Workflow schemas remain per-tool while wrapper metadata is shared."""
    from contextlib import asynccontextmanager
    from types import SimpleNamespace

    from src.services.mcp_server import server

    @asynccontextmanager
    async def database():
        yield object()

    send_id, count_id = uuid4(), uuid4()
    workflows = [
        SimpleNamespace(
            id=send_id,
            name="Send Message",
            description="Send one message.",
            parameters_schema=[{"name": "message", "type": "string", "required": True}],
        ),
        SimpleNamespace(
            id=count_id,
            name="Count Items",
            description="Count one collection.",
            parameters_schema=[{"name": "count", "type": "integer", "required": True}],
        ),
    ]
    execute = AsyncMock(return_value={"ok": True})
    monkeypatch.setattr("src.core.database.get_db_context", database)
    monkeypatch.setattr(
        "src.services.tool_registry.ToolRegistry.get_all_tools",
        AsyncMock(return_value=workflows),
    )
    monkeypatch.setattr(server, "_get_context_from_token", lambda: org_user_context)
    monkeypatch.setattr(server, "_execute_workflow_tool_impl", execute)
    mcp = FastMCP("metadata-test")

    assert await server._register_workflow_tools(mcp) == 2
    listed = {tool.name: tool for tool in mcp._tool_manager.list_tools()}
    send, count = listed["send_message"], listed["count_items"]

    assert send.parameters == {
        "type": "object",
        "properties": {"message": {"type": "string"}},
        "additionalProperties": False,
        "required": ["message"],
    }
    assert count.parameters == {
        "type": "object",
        "properties": {"count": {"type": "integer"}},
        "additionalProperties": False,
        "required": ["count"],
    }
    assert send.fn_metadata is count.fn_metadata
    assert send.fn_metadata.output_schema is None

    await send.run({"message": "hello"})
    execute.assert_awaited_once_with(
        org_user_context, str(send_id), "Send Message", message="hello"
    )
    with pytest.raises(ToolError, match="Invalid arguments for count_items"):
        await count.run({"count": "not-an-integer"})
    execute.assert_awaited_once()


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
            "bifrost_workflow_execute",
            "bifrost_workflow_list",
            "bifrost_integration_list",
            "bifrost_form_list",
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
            "allowed_tool_ids": ["bifrost_workflow_execute", "bifrost_workflow_list"],
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
        assert config.allowed_tool_ids == ["bifrost_workflow_execute", "bifrost_workflow_list"]
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
            allowed_tool_ids=["bifrost_workflow_execute"],
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
