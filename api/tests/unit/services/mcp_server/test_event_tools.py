"""
Unit tests for Events MCP Tools.

Tests the event source, subscription, and webhook adapter tools:
- list_event_sources
- create_event_source
- get_event_source
- update_event_source
- delete_event_source
- list_event_subscriptions
- create_event_subscription
- update_event_subscription
- delete_event_subscription
- list_webhook_adapters
"""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastmcp.tools import ToolResult

from src.services.mcp_server.server import MCPContext


def is_error_result(result: ToolResult) -> bool:
    """Check if a ToolResult represents an error."""
    if result.structured_content and "error" in result.structured_content:
        return True
    content = result.content
    if isinstance(content, list):
        content = content[0].text if content else ""
    if content and isinstance(content, str) and content.startswith("Error:"):
        return True
    return False


def get_content_text(result: ToolResult) -> str:
    """Extract text content from a ToolResult."""
    content = result.content
    if isinstance(content, list):
        return content[0].text if content else ""
    return content or ""


# ==================== Fixtures ====================


@pytest.fixture
def context():
    """Create an MCPContext for testing."""
    return MCPContext(
        user_id=str(uuid4()),
        org_id=str(uuid4()),
        is_platform_admin=True,
        user_email="admin@example.com",
        user_name="Admin User",
    )


@pytest.fixture
def org_user_context():
    """Create an MCPContext for a regular organization user."""
    return MCPContext(
        user_id=str(uuid4()),
        org_id=str(uuid4()),
        is_platform_admin=False,
        user_email="user@example.com",
        user_name="Org User",
    )


# ==================== Event Source Tool Tests ====================


class TestEventToolAuthorization:
    """Event source and subscription MCP tools are platform-admin only."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("tool_name", "args"),
        [
            ("list_event_sources", ()),
            ("create_event_source", ("source", "webhook")),
            ("get_event_source", (str(uuid4()),)),
            ("update_event_source", (str(uuid4()),)),
            ("delete_event_source", (str(uuid4()),)),
            ("list_event_subscriptions", (str(uuid4()),)),
            ("create_event_subscription", (str(uuid4()), str(uuid4()))),
            ("update_event_subscription", (str(uuid4()), str(uuid4()))),
            ("delete_event_subscription", (str(uuid4()), str(uuid4()))),
            ("list_webhook_adapters", ()),
        ],
    )
    async def test_non_admin_event_tools_return_error(self, org_user_context, tool_name, args):
        import src.services.mcp_server.tools.events as events

        result = await getattr(events, tool_name)(org_user_context, *args)

        assert is_error_result(result)
        assert "Platform administrator privileges are required" in result.structured_content["error"]


class TestListEventSources:
    """Tests for list_event_sources tool."""

    @pytest.mark.asyncio
    async def test_invalid_source_type_returns_error(self, context):
        """Should return error for invalid source_type."""
        from src.services.mcp_server.tools.events import list_event_sources

        result = await list_event_sources(context, source_type="invalid_type")
        assert is_error_result(result)
        assert "Invalid source_type" in result.structured_content["error"]

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_sources(self, context):
        """Should return empty list when no sources exist."""
        from src.services.mcp_server.tools.events import list_event_sources

        mock_repo = MagicMock()
        mock_repo.get_by_organization = AsyncMock(return_value=[])

        with patch("src.core.database.get_db_context") as mock_db:
            mock_session = AsyncMock()
            mock_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_db.return_value.__aexit__ = AsyncMock(return_value=False)

            with patch(
                "src.repositories.events.EventSourceRepository",
                return_value=mock_repo,
            ):
                result = await list_event_sources(context)
                assert not is_error_result(result)
                assert result.structured_content["sources"] == []
                assert result.structured_content["count"] == 0


class TestCreateEventSource:
    """Tests for create_event_source tool."""

    @pytest.mark.asyncio
    async def test_invalid_source_type_returns_error(self, context):
        """Should return error for invalid source_type."""
        from src.services.mcp_server.tools.events import create_event_source

        result = await create_event_source(context, name="test", source_type="bogus")
        assert is_error_result(result)
        assert "Invalid source_type" in result.structured_content["error"]

    @pytest.mark.asyncio
    async def test_schedule_without_cron_returns_error(self, context):
        """Should return error when schedule source missing cron_expression."""
        from src.services.mcp_server.tools.events import create_event_source

        result = await create_event_source(
            context, name="test", source_type="schedule", cron_expression=None
        )
        assert is_error_result(result)
        assert "cron_expression is required" in result.structured_content["error"]


class TestGetEventSource:
    """Tests for get_event_source tool."""

    @pytest.mark.asyncio
    async def test_returns_error_when_id_empty(self, context):
        """Should return error when source_id is empty."""
        from src.services.mcp_server.tools.events import get_event_source

        result = await get_event_source(context, "")
        assert is_error_result(result)
        assert "source_id is required" in result.structured_content["error"]

    @pytest.mark.asyncio
    async def test_returns_error_when_source_not_found(self, context):
        """Should return error when source doesn't exist."""
        from src.services.mcp_server.tools.events import get_event_source

        mock_repo = MagicMock()
        mock_repo.get_by_id_with_details = AsyncMock(return_value=None)

        with patch("src.core.database.get_db_context") as mock_db:
            mock_session = AsyncMock()
            mock_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_db.return_value.__aexit__ = AsyncMock(return_value=False)

            with patch(
                "src.repositories.events.EventSourceRepository",
                return_value=mock_repo,
            ):
                source_id = str(uuid4())
                result = await get_event_source(context, source_id)
                assert is_error_result(result)
                assert "not found" in result.structured_content["error"]


class TestUpdateEventSource:
    """Tests for update_event_source tool."""

    @pytest.mark.asyncio
    async def test_returns_error_when_id_empty(self, context):
        """Should return error when source_id is empty."""
        from src.services.mcp_server.tools.events import update_event_source

        result = await update_event_source(context, "")
        assert is_error_result(result)
        assert "source_id is required" in result.structured_content["error"]

    @pytest.mark.asyncio
    async def test_returns_error_when_source_not_found(self, context):
        """Should return error when source doesn't exist."""
        from src.services.mcp_server.tools.events import update_event_source

        mock_repo = MagicMock()
        mock_repo.get_by_id_with_details = AsyncMock(return_value=None)

        with patch("src.core.database.get_db_context") as mock_db:
            mock_session = AsyncMock()
            mock_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_db.return_value.__aexit__ = AsyncMock(return_value=False)

            with patch(
                "src.repositories.events.EventSourceRepository",
                return_value=mock_repo,
            ):
                source_id = str(uuid4())
                result = await update_event_source(context, source_id, name="new name")
                assert is_error_result(result)
                assert "not found" in result.structured_content["error"]


class TestDeleteEventSource:
    """Tests for delete_event_source tool."""

    @pytest.mark.asyncio
    async def test_returns_error_when_id_empty(self, context):
        """Should return error when source_id is empty."""
        from src.services.mcp_server.tools.events import delete_event_source

        result = await delete_event_source(context, "")
        assert is_error_result(result)
        assert "source_id is required" in result.structured_content["error"]

    @pytest.mark.asyncio
    async def test_returns_error_when_source_not_found(self, context):
        """Should return error when source doesn't exist."""
        from src.services.mcp_server.tools.events import delete_event_source

        mock_repo = MagicMock()
        mock_repo.get_by_id_with_details = AsyncMock(return_value=None)

        with patch("src.core.database.get_db_context") as mock_db:
            mock_session = AsyncMock()
            mock_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_db.return_value.__aexit__ = AsyncMock(return_value=False)

            with patch(
                "src.repositories.events.EventSourceRepository",
                return_value=mock_repo,
            ):
                source_id = str(uuid4())
                result = await delete_event_source(context, source_id)
                assert is_error_result(result)
                assert "not found" in result.structured_content["error"]

    @pytest.mark.asyncio
    async def test_deletes_source_row(self, context):
        """Should permanently delete the event source."""
        from src.models.enums import EventSourceType
        from src.services.mcp_server.tools.events import delete_event_source

        source_id = str(uuid4())
        mock_source = MagicMock()
        mock_source.id = source_id
        mock_source.name = "Test source"
        mock_source.source_type = EventSourceType.INTERNAL
        mock_source.webhook_source = None

        mock_repo = MagicMock()
        mock_repo.get_by_id_with_details = AsyncMock(return_value=mock_source)

        with patch("src.core.database.get_db_context") as mock_db:
            mock_session = AsyncMock()
            mock_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_db.return_value.__aexit__ = AsyncMock(return_value=False)

            with patch(
                "src.repositories.events.EventSourceRepository",
                return_value=mock_repo,
            ):
                result = await delete_event_source(context, source_id)

        assert not is_error_result(result)
        mock_session.delete.assert_awaited_once_with(mock_source)
        mock_session.flush.assert_awaited_once()


# ==================== Subscription Tool Tests ====================


class TestListEventSubscriptions:
    """Tests for list_event_subscriptions tool."""

    @pytest.mark.asyncio
    async def test_returns_error_when_source_id_empty(self, context):
        """Should return error when source_id is empty."""
        from src.services.mcp_server.tools.events import list_event_subscriptions

        result = await list_event_subscriptions(context, "")
        assert is_error_result(result)
        assert "source_id is required" in result.structured_content["error"]

    @pytest.mark.asyncio
    async def test_returns_error_when_source_not_found(self, context):
        """Should return error when source doesn't exist."""
        from src.services.mcp_server.tools.events import list_event_subscriptions

        mock_source_repo = MagicMock()
        mock_source_repo.get_by_id = AsyncMock(return_value=None)

        with patch("src.core.database.get_db_context") as mock_db:
            mock_session = AsyncMock()
            mock_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_db.return_value.__aexit__ = AsyncMock(return_value=False)

            with patch(
                "src.repositories.events.EventSourceRepository",
                return_value=mock_source_repo,
            ):
                source_id = str(uuid4())
                result = await list_event_subscriptions(context, source_id)
                assert is_error_result(result)
                assert "not found" in result.structured_content["error"]


class TestCreateEventSubscription:
    """Tests for create_event_subscription tool."""

    @pytest.mark.asyncio
    async def test_returns_error_when_source_id_empty(self, context):
        """Should return error when source_id is empty."""
        from src.services.mcp_server.tools.events import create_event_subscription

        result = await create_event_subscription(context, "", str(uuid4()))
        assert is_error_result(result)
        assert "source_id is required" in result.structured_content["error"]

    @pytest.mark.asyncio
    async def test_returns_error_when_workflow_id_empty(self, context):
        """Should return error when workflow_id is empty."""
        from src.services.mcp_server.tools.events import create_event_subscription

        result = await create_event_subscription(context, str(uuid4()), "")
        assert is_error_result(result)
        assert "workflow_id is required" in result.structured_content["error"]

    @pytest.mark.asyncio
    async def test_returns_error_when_source_not_found(self, context):
        """Should return error when source doesn't exist."""
        from src.services.mcp_server.tools.events import create_event_subscription

        mock_source_repo = MagicMock()
        mock_source_repo.get_by_id = AsyncMock(return_value=None)

        with patch("src.core.database.get_db_context") as mock_db:
            mock_session = AsyncMock()
            mock_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_db.return_value.__aexit__ = AsyncMock(return_value=False)

            with patch(
                "src.repositories.events.EventSourceRepository",
                return_value=mock_source_repo,
            ):
                result = await create_event_subscription(
                    context, str(uuid4()), str(uuid4())
                )
                assert is_error_result(result)
                assert "not found" in result.structured_content["error"]


class TestUpdateEventSubscription:
    """Tests for update_event_subscription tool."""

    @pytest.mark.asyncio
    async def test_returns_error_when_ids_empty(self, context):
        """Should return error when source_id or subscription_id is empty."""
        from src.services.mcp_server.tools.events import update_event_subscription

        result = await update_event_subscription(context, "", str(uuid4()))
        assert is_error_result(result)
        assert "required" in result.structured_content["error"]

        result = await update_event_subscription(context, str(uuid4()), "")
        assert is_error_result(result)
        assert "required" in result.structured_content["error"]


class TestDeleteEventSubscription:
    """Tests for delete_event_subscription tool."""

    @pytest.mark.asyncio
    async def test_returns_error_when_ids_empty(self, context):
        """Should return error when source_id or subscription_id is empty."""
        from src.services.mcp_server.tools.events import delete_event_subscription

        result = await delete_event_subscription(context, "", str(uuid4()))
        assert is_error_result(result)
        assert "required" in result.structured_content["error"]

        result = await delete_event_subscription(context, str(uuid4()), "")
        assert is_error_result(result)
        assert "required" in result.structured_content["error"]

    @pytest.mark.asyncio
    async def test_deletes_subscription_row(self, context):
        """Should permanently delete the event subscription."""
        from src.services.mcp_server.tools.events import delete_event_subscription

        source_id = str(uuid4())
        subscription_id = str(uuid4())
        mock_subscription = MagicMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_subscription

        with patch("src.core.database.get_db_context") as mock_db:
            mock_session = AsyncMock()
            mock_session.execute = AsyncMock(return_value=mock_result)
            mock_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_db.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await delete_event_subscription(
                context, source_id, subscription_id
            )

        assert not is_error_result(result)
        mock_session.execute.assert_awaited_once()
        mock_session.delete.assert_awaited_once_with(mock_subscription)
        mock_session.flush.assert_awaited_once()


# ==================== Webhook Adapter Tests ====================


class TestListWebhookAdapters:
    """Tests for list_webhook_adapters tool."""

    @pytest.mark.asyncio
    async def test_returns_adapter_list(self, context):
        """Should return list of adapters from registry."""
        from src.services.mcp_server.tools.events import list_webhook_adapters

        mock_registry = MagicMock()
        mock_registry.list_adapters.return_value = [
            {
                "name": "generic",
                "display_name": "Generic Webhook",
                "description": "Generic webhook adapter",
                "requires_integration": None,
                "supports_renewal": False,
            },
            {
                "name": "microsoft_graph",
                "display_name": "Microsoft Graph",
                "description": "Graph subscriptions",
                "requires_integration": "Microsoft",
                "supports_renewal": True,
            },
        ]

        with patch(
            "src.services.webhooks.registry.get_adapter_registry",
            return_value=mock_registry,
        ):
            result = await list_webhook_adapters(context)
            assert not is_error_result(result)
            assert result.structured_content["count"] == 2
            adapters = result.structured_content["adapters"]
            assert len(adapters) == 2
            assert adapters[0]["name"] == "generic"
            assert adapters[1]["name"] == "microsoft_graph"
            assert adapters[1]["requires_integration"] == "Microsoft"


# ==================== Registration Tests ====================


class TestEventToolsRegistration:
    """Tests for event tools registration."""

    def test_all_tools_have_matching_functions(self):
        """Every tool in TOOLS should have a corresponding function."""
        from src.services.mcp_server.tools.events import TOOLS

        import src.services.mcp_server.tools.events as events_module

        for tool_id, _name, _description in TOOLS:
            assert hasattr(events_module, tool_id), f"Missing function: {tool_id}"
            func = getattr(events_module, tool_id)
            assert callable(func), f"{tool_id} is not callable"

    def test_tool_count_is_ten(self):
        """Should have exactly 10 tools registered."""
        from src.services.mcp_server.tools.events import TOOLS

        assert len(TOOLS) == 10

    def test_all_tool_ids_unique(self):
        """All tool IDs should be unique."""
        from src.services.mcp_server.tools.events import TOOLS

        tool_ids = [t[0] for t in TOOLS]
        assert len(tool_ids) == len(set(tool_ids)), "Duplicate tool IDs found"

    def test_register_tools_calls_register_for_each(self):
        """register_tools should register all 10 tools."""
        from src.services.mcp_server.tools.events import register_tools

        mock_mcp = MagicMock()
        mock_get_context = MagicMock()

        with patch(
            "src.services.mcp_server.generators.fastmcp_generator.register_tool_with_context"
        ) as mock_register:
            register_tools(mock_mcp, mock_get_context)
            assert mock_register.call_count == 10
