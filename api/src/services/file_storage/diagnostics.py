"""
Diagnostics service for tracking and notifying about file issues.

Scans Python files for SDK reference issues and other diagnostics,
creating platform admin notifications when issues are found.
"""

import logging
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.log_safety import log_safe
from src.services.sdk_reference_scanner import SDKReferenceScanner
from src.services.notification_service import get_notification_service
from src.models.contracts.notifications import (
    NotificationCreate,
    NotificationCategory,
    NotificationStatus,
)

logger = logging.getLogger(__name__)


@dataclass
class FileDiagnosticInfo:
    """A file-specific issue detected during save/indexing."""

    severity: str  # "error", "warning", "info"
    message: str
    line: int | None = None
    column: int | None = None
    source: str = "bifrost"  # e.g., "syntax", "indexing", "sdk"


class DiagnosticsService:
    """Service for scanning files and managing diagnostic notifications."""

    def __init__(self, db: AsyncSession):
        """
        Initialize diagnostics service.

        Args:
            db: Database session
        """
        self.db = db

    async def scan_for_sdk_issues(self, path: str, content: bytes) -> None:
        """
        Scan a Python file for missing SDK references and create notifications.

        Detects config.get("key") and integrations.get("name") calls where
        the key/name doesn't exist in the database. Creates platform admin
        notifications with links to the file and line number.

        Args:
            path: Relative file path
            content: File content as bytes
        """
        try:
            content_str = content.decode("utf-8", errors="replace")
        except Exception as e:
            logger.warning(f"Failed to decode content for SDK scan: {e}")
            return

        scanner = SDKReferenceScanner(self.db)
        issues = await scanner.scan_file(path, content_str)

        if not issues:
            # Clear any existing notification since issues are resolved
            await self.clear_sdk_issues_notification(path)
            return

        # Create platform admin notification
        service = get_notification_service()

        # Check for existing notification to avoid duplicates
        file_name = Path(path).name
        title = f"Missing SDK References: {file_name}"

        existing = await service.find_admin_notification_by_title(
            title=title,
            category=NotificationCategory.SYSTEM,
        )
        if existing:
            logger.debug(f"SDK notification already exists for {log_safe(path)}")
            return

        # Build description with first few issues
        issue_keys = [i.key for i in issues[:3]]
        description = f"{len(issues)} missing: {', '.join(issue_keys)}"
        if len(issues) > 3:
            description += "..."

        await service.create_notification(
            user_id="system",
            request=NotificationCreate(
                category=NotificationCategory.SYSTEM,
                title=title,
                description=description,
                metadata={
                    "action": "view_file",
                    "file_path": path,
                    "line_number": issues[0].line_number,
                    "issues": [
                        {
                            "type": i.issue_type,
                            "key": i.key,
                            "line": i.line_number,
                        }
                        for i in issues
                    ],
                },
            ),
            for_admins=True,
            initial_status=NotificationStatus.AWAITING_ACTION,
        )

        logger.info(
            f"Created SDK issues notification for {log_safe(path)}: "
            f"{len(issues)} issues"
        )

    async def clear_sdk_issues_notification(self, path: str) -> None:
        """
        Clear SDK issues notification for a file when issues are resolved.

        Called when a file is saved without SDK reference issues to remove
        any existing notification that was created for previous issues.

        Args:
            path: Relative file path
        """
        service = get_notification_service()

        # Match the title format used in scan_for_sdk_issues
        file_name = Path(path).name
        title = f"Missing SDK References: {file_name}"

        existing = await service.find_admin_notification_by_title(
            title=title,
            category=NotificationCategory.SYSTEM,
        )
        if existing:
            await service.dismiss_notification(existing.id, user_id="system")
            logger.info(f"Cleared SDK issues notification for {log_safe(path)}")

    async def create_diagnostic_notification(
        self, path: str, diagnostics: list[FileDiagnosticInfo]
    ) -> None:
        """
        Create a system notification for file diagnostics that contain errors.

        Called after file writes to ensure visibility when files have issues,
        regardless of the source (editor, git sync, MCP).

        Args:
            path: Relative file path
            diagnostics: List of file diagnostics
        """
        errors = [d for d in diagnostics if d.severity == "error"]
        if not errors:
            return

        service = get_notification_service()

        # Build title from file name
        file_name = Path(path).name
        title = f"File issues: {file_name}"

        # Check for existing notification to avoid duplicates
        existing = await service.find_admin_notification_by_title(
            title=title,
            category=NotificationCategory.SYSTEM,
        )
        if existing:
            logger.debug(
                f"Diagnostic notification already exists for {log_safe(path)}"
            )
            return

        # Build description from first few errors
        error_msgs = [e.message for e in errors[:3]]
        description = "; ".join(error_msgs)
        if len(errors) > 3:
            description += f"... (+{len(errors) - 3} more)"

        await service.create_notification(
            user_id="system",
            request=NotificationCreate(
                category=NotificationCategory.SYSTEM,
                title=title,
                description=description,
                metadata={
                    "action": "view_file",
                    "file_path": path,
                    "line_number": errors[0].line if errors[0].line else 1,
                    "diagnostics": [
                        {
                            "severity": d.severity,
                            "message": d.message,
                            "line": d.line,
                            "column": d.column,
                            "source": d.source,
                        }
                        for d in diagnostics
                    ],
                },
            ),
            for_admins=True,
            initial_status=NotificationStatus.AWAITING_ACTION,
        )

        logger.info(
            f"Created diagnostic notification for {log_safe(path)}: "
            f"{len(errors)} errors"
        )

    async def clear_diagnostic_notification(self, path: str) -> None:
        """
        Clear diagnostic notification for a file when issues are fixed.

        Called when a file is saved without errors to remove any existing
        diagnostic notification that was created for previous errors.

        Args:
            path: Relative file path
        """
        service = get_notification_service()

        # Match the title format used in create_diagnostic_notification
        file_name = Path(path).name
        title = f"File issues: {file_name}"

        existing = await service.find_admin_notification_by_title(
            title=title,
            category=NotificationCategory.SYSTEM,
        )
        if existing:
            await service.dismiss_notification(existing.id, user_id="system")
            logger.info(f"Cleared diagnostic notification for {log_safe(path)}")

    async def scan_for_unresolved_refs(
        self,
        path: str,
        entity_type: str,
        unresolved_refs: list[str],
    ) -> None:
        """
        Create or clear notification for unresolved workflow references.

        Called after file processing to notify about portable refs that
        couldn't be resolved to UUIDs.

        Args:
            path: Relative file path
            entity_type: Type of entity ("app_file", "form", "agent")
            unresolved_refs: List of unresolved portable refs
        """
        if not unresolved_refs:
            # Clear any existing notification since issues are resolved
            await self.clear_unresolved_refs_notification(path)
            return

        service = get_notification_service()

        # Build title from file name
        file_name = Path(path).name
        title = f"Unresolved Workflow Refs: {file_name}"

        # Check for existing notification to avoid duplicates
        existing = await service.find_admin_notification_by_title(
            title=title,
            category=NotificationCategory.SYSTEM,
        )
        if existing:
            logger.debug(
                f"Unresolved refs notification already exists for {log_safe(path)}"
            )
            return

        # Build description with first few refs
        ref_list = unresolved_refs[:3]
        description = f"{len(unresolved_refs)} unresolved: {', '.join(ref_list)}"
        if len(unresolved_refs) > 3:
            description += "..."

        await service.create_notification(
            user_id="system",
            request=NotificationCreate(
                category=NotificationCategory.SYSTEM,
                title=title,
                description=description,
                metadata={
                    "action": "view_file",
                    "file_path": path,
                    "entity_type": entity_type,
                    "unresolved_refs": unresolved_refs,
                },
            ),
            for_admins=True,
            initial_status=NotificationStatus.AWAITING_ACTION,
        )

        logger.info(
            f"Created unresolved refs notification for {log_safe(path)}: "
            f"{len(unresolved_refs)} refs"
        )

    async def clear_unresolved_refs_notification(self, path: str) -> None:
        """
        Clear unresolved refs notification for a file when issues are resolved.

        Called when a file is saved without unresolved refs to remove
        any existing notification that was created for previous issues.

        Args:
            path: Relative file path
        """
        service = get_notification_service()

        # Match the title format used in scan_for_unresolved_refs
        file_name = Path(path).name
        title = f"Unresolved Workflow Refs: {file_name}"

        existing = await service.find_admin_notification_by_title(
            title=title,
            category=NotificationCategory.SYSTEM,
        )
        if existing:
            await service.dismiss_notification(existing.id, user_id="system")
            logger.info(
                f"Cleared unresolved refs notification for {log_safe(path)}"
            )
