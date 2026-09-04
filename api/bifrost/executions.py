"""
bifrost/executions.py - Execution history SDK (API-only)

Provides Python API for execution history operations (list, get, get_current_logs).
All operations go through HTTP API endpoints.
"""

from __future__ import annotations

from .client import get_client, raise_for_status_with_detail
from .models import ExecutionLog, WorkflowExecution


class ExecutionList(list[WorkflowExecution]):
    """List of workflow executions with optional pagination metadata."""

    continuation_token: str | None

    def __init__(
        self,
        executions: list[WorkflowExecution] | None = None,
        continuation_token: str | None = None,
    ) -> None:
        super().__init__(executions or [])
        self.continuation_token = continuation_token


class executions:
    """
    Execution history operations.

    Allows workflows to query execution history.

    All methods are async - await is required.
    """

    @staticmethod
    async def list(
        workflow_name: str | None = None,
        status: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        limit: int = 50,
        *,
        workflow_id: str | None = None,
        exclude_local: bool | None = None,
        continuation_token: str | None = None,
    ) -> ExecutionList:
        """
        List workflow executions with filtering.

        Platform admins see all executions in their scope.
        Regular users see only their own executions.

        Args:
            workflow_id: Filter by workflow UUID (optional)
            workflow_name: Filter by workflow name (optional)
            status: Filter by status (optional)
            start_date: Filter by start date in ISO format (optional)
            end_date: Filter by end date in ISO format (optional)
            exclude_local: Exclude local-only runs (optional)
            continuation_token: Pagination token for the next page (optional)
            limit: Maximum number of results (default: 50, max: 1000)

        Returns:
            ExecutionList: List-compatible execution SUMMARIES. Read its
            ``continuation_token`` attribute to request the next page. Summaries
            carry metadata only — ``input_data``, ``result``, ``logs``, and
            ``variables`` are always None here; call ``executions.get(id)``
            for a specific execution's payload. Summary attributes:
                - execution_id: str - Unique execution ID
                - workflow_name: str - Name of the workflow
                - org_id: str | None - Organization ID
                - form_id: str | None - Form ID if triggered by form
                - executed_by: str - User ID who executed
                - executed_by_name: str - Display name of user
                - status: str - Current status
                - result_type: str | None - How to render result
                - error_message: str | None - Error if failed
                - duration_ms: int | None - Execution duration
                - started_at, completed_at: datetime | None
                - session_id: str | None - CLI session ID
                - peak_memory_bytes, cpu_total_seconds: Resource metrics

        Raises:
            RuntimeError: If not authenticated

        Example:
            >>> from bifrost import executions
            >>> recent = await executions.list(limit=10)
            >>> for execution in recent:
            ...     print(f"{execution.workflow_name}: {execution.status}")
            >>> failed = await executions.list(status="Failed")
        """
        client = get_client()

        # Build query parameters
        params = {}
        if workflow_id:
            params["workflow_id"] = workflow_id
        elif workflow_name:
            params["workflow_name"] = workflow_name
        if status:
            params["status"] = status
        if start_date:
            params["start_date"] = start_date
        if end_date:
            params["end_date"] = end_date
        if exclude_local is not None:
            params["exclude_local"] = str(exclude_local).lower()
        if continuation_token:
            params["continuation_token"] = continuation_token
        params["limit"] = min(limit, 1000)

        response = await client.get("/api/executions", params=params)
        raise_for_status_with_detail(response)
        data = response.json()
        # API returns ExecutionsListResponse with executions array
        executions_data = data.get("executions", [])
        return ExecutionList(
            [
                WorkflowExecution.model_validate(exec_data)
                for exec_data in executions_data
            ],
            continuation_token=data.get("continuation_token"),
        )

    @staticmethod
    async def get(execution_id: str) -> WorkflowExecution:
        """
        Get execution details by ID.

        Platform admins can view any execution in their scope.
        Regular users can only view their own executions.

        Args:
            execution_id: Execution ID (UUID)

        Returns:
            WorkflowExecution: Execution details including logs

        Raises:
            ValueError: If execution not found
            PermissionError: If access denied
            RuntimeError: If not authenticated

        Example:
            >>> from bifrost import executions
            >>> exec_details = await executions.get("exec-123")
            >>> print(exec_details.status)
            >>> print(exec_details.result)
        """
        client = get_client()
        response = await client.get(f"/api/executions/{execution_id}")
        if response.status_code == 404:
            raise ValueError(f"Execution not found: {execution_id}")
        elif response.status_code == 403:
            raise PermissionError(f"Access denied to execution: {execution_id}")
        raise_for_status_with_detail(response)
        return WorkflowExecution.model_validate(response.json())

    @staticmethod
    async def get_current_logs(
        execution_id: str | None = None,
        start: str = "0",
        count: int = 100,
    ) -> "list[ExecutionLog]":
        """
        Get logs accumulated so far for the current (or specified) execution.

        This reads logs directly from Redis Stream, allowing workflows to
        retrieve their own logs accumulated during execution. Useful for
        debugging, progress tracking, or passing execution context to sub-workflows.

        Args:
            execution_id: Execution ID to get logs for. If not provided,
                uses the current execution context.
            start: Stream ID to start from (default: "0" = beginning)
            count: Maximum entries to read (default: 100)

        Returns:
            list[ExecutionLog]: List of log entries with attributes:
                - id: Stream entry ID
                - execution_id: Execution UUID
                - level: Log level (INFO, WARNING, ERROR, DEBUG, CRITICAL)
                - message: Log message text
                - metadata: Optional JSON metadata dict
                - timestamp: ISO timestamp string

        Raises:
            RuntimeError: If no execution_id provided and not in a workflow context

        Example:
            >>> from bifrost import executions
            >>> # Get logs for current execution
            >>> logs = await executions.get_current_logs()
            >>> for log in logs:
            ...     print(f"[{log.level}] {log.message}")
            >>> # Get logs for a specific execution
            >>> logs = await executions.get_current_logs(execution_id="exec-123")
        """
        from ._logging import read_logs_from_stream

        # If no execution_id provided, get from current context
        if execution_id is None:
            from ._context import get_execution_context
            ctx = get_execution_context()
            execution_id = ctx.execution_id

        return await read_logs_from_stream(
            execution_id=execution_id,
            start=start,
            count=count,
        )
