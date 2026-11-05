"""
Unit tests for unresolved reference notification helpers.

Tests scan_for_unresolved_refs and clear_unresolved_refs_notification.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.services.file_storage.diagnostics import (
    DiagnosticsService,
    _unresolved_refs_title,
)


class TestUnresolvedRefNotifications:
    """Tests for unresolved ref notification helpers."""

    @pytest.fixture
    def mock_db(self):
        """Create mock database session."""
        return AsyncMock()

    @pytest.fixture
    def diagnostics_service(self, mock_db):
        """Create diagnostics service with mock db."""
        return DiagnosticsService(mock_db)

    @pytest.mark.asyncio
    async def test_creates_notification_for_unresolved_refs(
        self, diagnostics_service
    ):
        """When unresolved refs exist, creates a notification."""
        unresolved = ["workflows/missing.py::func1", "workflows/gone.py::func2"]

        with patch(
            "src.services.file_storage.diagnostics.get_notification_service"
        ) as mock_get_service:
            mock_service = MagicMock()
            mock_service.find_admin_notification_by_title = AsyncMock(return_value=None)
            mock_service.create_notification = AsyncMock()
            mock_get_service.return_value = mock_service

            await diagnostics_service.scan_for_unresolved_refs(
                path="apps/my-app/pages/index.tsx",
                entity_type="app_file",
                unresolved_refs=unresolved,
            )

            mock_service.create_notification.assert_called_once()
            call_args = mock_service.create_notification.call_args
            assert "Unresolved Workflow Refs" in call_args.kwargs["request"].title
            assert "index.tsx" in call_args.kwargs["request"].title

    @pytest.mark.asyncio
    async def test_clears_notification_when_no_unresolved_refs(
        self, diagnostics_service
    ):
        """When no unresolved refs, clears any existing notification."""
        with patch(
            "src.services.file_storage.diagnostics.get_notification_service"
        ) as mock_get_service:
            mock_service = MagicMock()
            mock_service.find_admin_notification_by_title = AsyncMock(return_value=None)
            mock_get_service.return_value = mock_service

            await diagnostics_service.scan_for_unresolved_refs(
                path="apps/my-app/pages/index.tsx",
                entity_type="app_file",
                unresolved_refs=[],
            )

            # Should not create notification
            mock_service.create_notification.assert_not_called()

    @pytest.mark.asyncio
    async def test_clears_existing_notification(self, diagnostics_service):
        """clear_unresolved_refs_notification dismisses existing notification."""
        with patch(
            "src.services.file_storage.diagnostics.get_notification_service"
        ) as mock_get_service:
            mock_notification = MagicMock()
            mock_notification.id = "notif-123"

            mock_service = MagicMock()
            mock_service.find_admin_notification_by_title = AsyncMock(
                return_value=mock_notification
            )
            mock_service.dismiss_notification = AsyncMock()
            mock_get_service.return_value = mock_service

            await diagnostics_service.clear_unresolved_refs_notification(
                path="apps/my-app/pages/index.tsx"
            )

            mock_service.dismiss_notification.assert_called_once_with(
                "notif-123", user_id="system"
            )

    @pytest.mark.asyncio
    async def test_skips_duplicate_notification(self, diagnostics_service):
        """Does not create duplicate notification if one already exists."""
        unresolved = ["workflows/missing.py::func"]

        with patch(
            "src.services.file_storage.diagnostics.get_notification_service"
        ) as mock_get_service:
            mock_existing = MagicMock()

            mock_service = MagicMock()
            mock_service.find_admin_notification_by_title = AsyncMock(
                return_value=mock_existing
            )
            mock_service.create_notification = AsyncMock()
            mock_get_service.return_value = mock_service

            await diagnostics_service.scan_for_unresolved_refs(
                path="apps/my-app/pages/index.tsx",
                entity_type="app_file",
                unresolved_refs=unresolved,
            )

            # Should not create when existing
            mock_service.create_notification.assert_not_called()

    @pytest.mark.asyncio
    async def test_same_basename_paths_create_distinct_notifications(
        self, diagnostics_service
    ):
        """A notification for one index.tsx must not suppress another one."""
        with patch(
            "src.services.file_storage.diagnostics.get_notification_service"
        ) as mock_get_service:
            mock_service = MagicMock()
            mock_service.find_admin_notification_by_title = AsyncMock(return_value=None)
            mock_service.create_notification = AsyncMock()
            mock_get_service.return_value = mock_service

            await diagnostics_service.scan_for_unresolved_refs(
                path="apps/first/pages/index.tsx",
                entity_type="app_file",
                unresolved_refs=["workflows/missing.py::run"],
            )
            await diagnostics_service.scan_for_unresolved_refs(
                path="apps/second/pages/index.tsx",
                entity_type="app_file",
                unresolved_refs=["workflows/other.py::run"],
            )

        created_titles = [
            call.kwargs["request"].title
            for call in mock_service.create_notification.call_args_list
        ]
        assert len(created_titles) == 2
        assert created_titles[0] != created_titles[1]
        assert "apps/first/pages/index.tsx" in created_titles[0]
        assert "apps/second/pages/index.tsx" in created_titles[1]

    def test_path_qualified_title_is_bounded_for_long_paths(self):
        """Notification titles fit the contract without losing the path tail."""
        path = f"apps/{'nested/' * 40}pages/index.tsx"

        title = _unresolved_refs_title(path)

        assert len(title) <= 200
        assert "pages/index.tsx" in title
        assert title.endswith("]")

    @pytest.mark.asyncio
    async def test_does_not_dismiss_ambiguous_legacy_basename_notification(
        self, diagnostics_service
    ):
        """Legacy basename-only records need matching file_path metadata."""
        legacy = MagicMock()
        legacy.id = "legacy-123"
        legacy.metadata = {"file_path": "apps/other/pages/index.tsx"}

        with patch(
            "src.services.file_storage.diagnostics.get_notification_service"
        ) as mock_get_service:
            mock_service = MagicMock()
            mock_service.find_admin_notification_by_title = AsyncMock(
                side_effect=[None, legacy]
            )
            mock_service.dismiss_notification = AsyncMock()
            mock_get_service.return_value = mock_service

            await diagnostics_service.clear_unresolved_refs_notification(
                path="apps/current/pages/index.tsx"
            )

        mock_service.dismiss_notification.assert_not_awaited()
