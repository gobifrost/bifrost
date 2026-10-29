"""
Execution Logs Repository

PostgreSQL-based repository for execution log entries.
Replaces the Azure Table Storage implementation.
"""

import base64
import binascii
import json
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import and_, desc, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from src.models import ExecutionLog
from src.models.orm.executions import Execution
from src.models.orm.organizations import Organization
from src.services.authorization.reach import OrgReach


def encode_execution_log_cursor(timestamp: datetime, log_id: int) -> str:
    """Encode the last emitted log row for stable keyset pagination."""
    payload = {
        "v": 1,
        "t": timestamp.isoformat(),
        "i": log_id,
    }
    return base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()


def decode_execution_log_cursor(token: str) -> tuple[datetime, int] | None:
    """Decode a keyset cursor; return None for legacy numeric offset tokens."""
    try:
        payload = json.loads(base64.urlsafe_b64decode(token.encode()))
        if not isinstance(payload, dict) or payload.get("v") != 1:
            return None
        timestamp = datetime.fromisoformat(payload["t"])
        log_id = payload["i"]
        if timestamp.tzinfo is None or type(log_id) is not int or not 0 < log_id <= 2147483647:
            return None
        return timestamp.astimezone(timezone.utc), log_id
    except (
        ValueError,
        KeyError,
        TypeError,
        json.JSONDecodeError,
        UnicodeDecodeError,
        binascii.Error,
    ):
        return None


class ExecutionLogRepository:
    """
    Repository for execution log entries.

    Logs are stored in the execution_logs table with:
    - execution_id: Foreign key to executions table
    - level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
    - message: Log message text
    - log_metadata: Optional JSONB metadata/data
    - timestamp: When the log was created
    """

    def __init__(self, session: AsyncSession):
        """Initialize with database session."""
        self.session = session

    async def append_log(
        self,
        execution_id: UUID,
        level: str,
        message: str,
        metadata: dict[str, Any] | None = None,
        timestamp: datetime | None = None,
    ) -> ExecutionLog:
        """
        Append a log entry for an execution.

        Args:
            execution_id: Execution UUID
            level: Log level (INFO, WARNING, ERROR, DEBUG, CRITICAL)
            message: Log message
            metadata: Optional structured metadata
            timestamp: Optional timestamp (defaults to now)

        Returns:
            Created log entry
        """
        log = ExecutionLog(
            execution_id=execution_id,
            level=level.upper(),
            message=message,
            log_metadata=metadata,
            timestamp=timestamp or datetime.now(timezone.utc),
        )
        self.session.add(log)
        await self.session.flush()
        return log

    async def append_logs_batch(
        self,
        execution_id: UUID,
        logs: list[dict[str, Any]],
    ) -> list[ExecutionLog]:
        """
        Append multiple log entries in a single batch.

        Args:
            execution_id: Execution UUID
            logs: List of log dicts with keys: level, message, data (optional), timestamp (optional)

        Returns:
            List of created log entries
        """
        log_entries = []
        for log_dict in logs:
            log = ExecutionLog(
                execution_id=execution_id,
                level=log_dict.get("level", "INFO").upper(),
                message=log_dict.get("message", ""),
                log_metadata=log_dict.get("data"),
                timestamp=log_dict.get("timestamp") or datetime.now(timezone.utc),
            )
            self.session.add(log)
            log_entries.append(log)

        await self.session.flush()
        return log_entries

    async def get_logs(
        self,
        execution_id: UUID,
        since_timestamp: datetime | None = None,
        level_filter: list[str] | None = None,
        limit: int = 5000,
    ) -> list[ExecutionLog]:
        """
        Get logs for an execution.

        Args:
            execution_id: Execution UUID
            since_timestamp: Only fetch logs after this timestamp
            level_filter: Only fetch logs with these levels
            limit: Maximum number of logs to return

        Returns:
            List of log entries (sorted by timestamp ascending)
        """
        query = (
            select(ExecutionLog)
            .where(ExecutionLog.execution_id == execution_id)
            .order_by(ExecutionLog.timestamp)
            .limit(limit)
        )

        if since_timestamp:
            query = query.where(ExecutionLog.timestamp > since_timestamp)

        if level_filter:
            query = query.where(ExecutionLog.level.in_([lvl.upper() for lvl in level_filter]))

        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def get_logs_as_dicts(
        self,
        execution_id: UUID,
        since_timestamp: datetime | None = None,
        exclude_levels: list[str] | None = None,
        limit: int = 5000,
    ) -> list[dict[str, Any]]:
        """
        Get logs as dictionaries (for API responses).

        Args:
            execution_id: Execution UUID
            since_timestamp: Only fetch logs after this timestamp
            exclude_levels: Exclude logs with these levels (e.g., ["DEBUG"])
            limit: Maximum number of logs to return

        Returns:
            List of log dicts with keys: id, timestamp, level, message, data
        """
        query = (
            select(ExecutionLog)
            .where(ExecutionLog.execution_id == execution_id)
            .order_by(ExecutionLog.timestamp)
            .limit(limit)
        )

        if since_timestamp:
            query = query.where(ExecutionLog.timestamp > since_timestamp)

        if exclude_levels:
            query = query.where(ExecutionLog.level.notin_([lvl.upper() for lvl in exclude_levels]))

        result = await self.session.execute(query)
        logs = result.scalars().all()

        return [
            {
                "id": log.id,
                "timestamp": log.timestamp.isoformat() if log.timestamp else None,
                "level": log.level,
                "message": log.message,
                "data": log.log_metadata,
            }
            for log in logs
        ]

    async def count_logs(self, execution_id: UUID) -> int:
        """
        Count total logs for an execution.

        Args:
            execution_id: Execution UUID

        Returns:
            Total number of logs
        """
        from sqlalchemy import func

        result = await self.session.execute(
            select(func.count(ExecutionLog.id)).where(
                ExecutionLog.execution_id == execution_id
            )
        )
        return result.scalar_one()

    async def delete_logs(self, execution_id: UUID) -> int:
        """
        Delete all logs for an execution.

        Note: This is typically handled by CASCADE delete on the execution.

        Args:
            execution_id: Execution UUID

        Returns:
            Number of logs deleted
        """
        from sqlalchemy import delete
        from sqlalchemy.engine import CursorResult

        result: CursorResult = await self.session.execute(  # type: ignore[assignment]
            delete(ExecutionLog).where(ExecutionLog.execution_id == execution_id)
        )
        await self.session.flush()
        return result.rowcount

    async def list_logs(
        self,
        organization_id: UUID | None = None,
        workflow_name: str | None = None,
        levels: list[str] | None = None,
        message_search: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        limit: int = 50,
        offset: int = 0,
        cursor: tuple[datetime, int] | None = None,
        workflow_id: UUID | None = None,
        global_only: bool = False,
        organization_reaches: tuple[OrgReach, ...] = (),
    ) -> tuple[list[dict[str, Any]], str | None]:
        """
        List logs across all executions with filtering and pagination.

        Args:
            organization_id: Filter by organization
            workflow_name: Filter by workflow name (partial match)
            workflow_id: Filter by exact workflow identity
            global_only: Include only executions without an organization
            organization_reaches: Every supplied reach is applied before
                pagination, preventing a permission from widening user reach.
            levels: Filter by log levels (e.g., ["ERROR", "WARNING"])
            message_search: Search in log messages (partial match)
            start_date: Filter logs from this date
            end_date: Filter logs until this date
            limit: Maximum number of logs to return
            offset: Number of logs to skip for legacy numeric tokens
            cursor: Stable keyset cursor as (timestamp, log id)

        Returns:
            Tuple of (logs_list, next_continuation_token).
            Token is a keyset cursor for the last returned row, or None if no
            more results.
        """
        # Build query with joins
        query = (
            select(ExecutionLog)
            .join(Execution, ExecutionLog.execution_id == Execution.id)
            .outerjoin(Organization, Execution.organization_id == Organization.id)
            .options(
                joinedload(ExecutionLog.execution).joinedload(Execution.organization)
            )
            .order_by(desc(ExecutionLog.timestamp), desc(ExecutionLog.id))
        )

        # Apply filters
        if organization_id:
            query = query.where(Execution.organization_id == organization_id)

        if global_only:
            query = query.where(Execution.organization_id.is_(None))

        for reach in organization_reaches:
            reach_filter = reach.where(Execution.organization_id)
            if reach_filter is not None:
                query = query.where(reach_filter)

        if workflow_id:
            query = query.where(Execution.workflow_id == workflow_id)

        if workflow_name:
            query = query.where(Execution.workflow_name.ilike(f"%{workflow_name}%"))

        if levels:
            query = query.where(ExecutionLog.level.in_([lvl.upper() for lvl in levels]))

        if message_search:
            query = query.where(ExecutionLog.message.ilike(f"%{message_search}%"))

        if start_date:
            query = query.where(ExecutionLog.timestamp >= start_date)

        if end_date:
            query = query.where(ExecutionLog.timestamp <= end_date)

        if cursor is not None:
            cursor_timestamp, cursor_id = cursor
            query = query.where(
                or_(
                    ExecutionLog.timestamp < cursor_timestamp,
                    and_(
                        ExecutionLog.timestamp == cursor_timestamp,
                        ExecutionLog.id < cursor_id,
                    ),
                )
            )
        elif offset:
            query = query.offset(offset)

        # Fetch limit+1 to check if there are more results
        query = query.limit(limit + 1)

        result = await self.session.execute(query)
        logs = result.scalars().unique().all()

        # Check if there are more results
        has_more = len(logs) > limit
        if has_more:
            logs = list(logs)[:limit]

        # Calculate next token from the last emitted row. A keyset token is
        # stable when new logs arrive before the next page and when multiple
        # rows share the same timestamp.
        next_token = (
            encode_execution_log_cursor(logs[-1].timestamp, logs[-1].id)
            if has_more and logs
            else None
        )

        # Convert to dicts with joined data
        return [
            {
                "id": log.id,
                "execution_id": str(log.execution_id),
                "organization_name": (
                    log.execution.organization.name
                    if log.execution.organization
                    else None
                ),
                "workflow_name": log.execution.workflow_name,
                "level": log.level,
                "message": log.message,
                "timestamp": log.timestamp,
            }
            for log in logs
        ], next_token


def get_execution_logs_repository() -> Any:
    """
    Factory function for backward compatibility.

    Note: In the new architecture, repositories are instantiated with a database
    session. This function exists for compatibility with code that expects
    a no-argument factory.

    New code should use dependency injection with DbSession instead.
    """
    from src.core.database import get_session_factory

    class _CompatExecutionLogRepository:
        """Wrapper that creates session on demand."""

        def __init__(self):
            self._session_factory = get_session_factory()
            self._session = None
            self._repo = None

        async def _ensure_session(self) -> ExecutionLogRepository:
            if self._session is None:
                self._session = self._session_factory()
                self._repo = ExecutionLogRepository(self._session)
            assert self._repo is not None  # Always set when session is created
            return self._repo

        async def append_log(self, execution_id, level, message, metadata=None, timestamp=None, source=None):
            repo = await self._ensure_session()
            exec_uuid = UUID(execution_id) if isinstance(execution_id, str) else execution_id
            # Include source in metadata if provided
            if source and metadata is None:
                metadata = {"source": source}
            elif source and metadata:
                metadata = {**metadata, "source": source}
            return await repo.append_log(exec_uuid, level, message, metadata, timestamp)

        async def get_logs(self, execution_id, since_timestamp=None, level_filter=None, limit=5000):
            repo = await self._ensure_session()
            exec_uuid = UUID(execution_id) if isinstance(execution_id, str) else execution_id
            return await repo.get_logs(exec_uuid, since_timestamp, level_filter, limit)

        async def get_logs_as_dicts(self, execution_id, since_timestamp=None, exclude_levels=None, limit=5000):
            repo = await self._ensure_session()
            exec_uuid = UUID(execution_id) if isinstance(execution_id, str) else execution_id
            return await repo.get_logs_as_dicts(exec_uuid, since_timestamp, exclude_levels, limit)

        async def close(self):
            if self._session:
                await self._session.close()
                self._session = None
                self._repo = None

    return _CompatExecutionLogRepository()
