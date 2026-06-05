"""
bifrost/workflows.py - Workflows SDK (API-only)

Provides Python API for workflow operations (list, get status, execute).
All operations go through HTTP API endpoints.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .client import get_client, raise_for_status_with_detail
from .executions import WorkflowExecution, executions
from .models import WorkflowMetadata


class workflows:
    """
    Workflow operations.

    Allows workflows to query available workflows and execution status.

    All methods are async - await is required.
    """

    @staticmethod
    async def list() -> list[WorkflowMetadata]:
        """
        List all available workflows.

        Returns:
            list[WorkflowMetadata]: List of workflow metadata with attributes:
                - id: str - Workflow UUID
                - name: str - MCP tool name (defaults to function name)
                - description: str | None - Human-readable description
                - category: str - Category for organization
                - tags: list[str] - Tags for categorization
                - parameters: dict - Workflow parameters
                - execution_mode: str - Execution mode
                - timeout_seconds: int - Max execution time
                - retry_policy: dict | None - Retry configuration
                - endpoint_enabled: bool - Whether exposed as HTTP endpoint
                - allowed_methods: list[str] | None - Allowed HTTP methods
                - disable_global_key: bool - Whether global API key is disabled
                - public_endpoint: bool - Whether endpoint is public
                - is_tool: bool - Whether available as AI tool
                - tool_description: str | None - AI tool description
                - time_saved: int - Minutes saved per execution
                - source_file_path: str | None - Full file path
                - relative_file_path: str | None - Workspace-relative path

        Raises:
            RuntimeError: If not authenticated

        Example:
            >>> from bifrost import workflows
            >>> wf_list = await workflows.list()
            >>> for wf in wf_list:
            ...     print(f"{wf.name}: {wf.description}")
        """
        client = get_client()
        response = await client.get("/api/workflows")
        raise_for_status_with_detail(response)
        data = response.json()
        return [WorkflowMetadata.model_validate(wf) for wf in data]

    @staticmethod
    async def execute(
        workflow: str,
        input_data: dict[str, Any] | None = None,
        *,
        org_id: str | None = None,
        run_as: str | None = None,
        scheduled_at: datetime | None = None,
        delay_seconds: int | None = None,
    ) -> str:
        """
        Execute a workflow (fire-and-forget).

        Triggers the workflow and returns immediately with the execution ID.
        Use workflows.get() to check status later.

        Args:
            workflow: Workflow UUID or path::function_name ref
            input_data: Input parameters for the workflow
            org_id: Override execution org context (admin only).
                     Like `bifrost run --org <org_id>`.
            run_as: Execute as this user UUID (admin only).
                    The execution will run under this user's identity.
            scheduled_at: Run at this timezone-aware datetime (ISO-8601 in
                the payload). Must be strictly in the future and within 1
                year of now. Mutually exclusive with ``delay_seconds``.
            delay_seconds: Run this many seconds from now (>= 1, <= 1 year).
                Mutually exclusive with ``scheduled_at``.

        Returns:
            str: The execution ID

        Raises:
            ValueError: If ``scheduled_at`` and ``delay_seconds`` are both
                provided, or if ``scheduled_at`` is naive (no tzinfo).
            httpx.HTTPStatusError: If the request fails (403 for non-admin
                using org_id/run_as, 404 for workflow not found, etc.)
            RuntimeError: If not authenticated

        Example:
            >>> from bifrost import workflows
            >>> eid = await workflows.execute("workflows/onboard.py::onboard_user", {"user_id": "abc"})
            >>> print(f"Started: {eid}")
            >>> # Check later:
            >>> execution = await workflows.get(eid)
            >>> print(execution.status)
        """
        if scheduled_at is not None and delay_seconds is not None:
            raise ValueError(
                "'scheduled_at' and 'delay_seconds' are mutually exclusive"
            )
        if scheduled_at is not None and scheduled_at.tzinfo is None:
            raise ValueError("'scheduled_at' must be timezone-aware")

        from ._context import get_default_scope

        # Auto-include org_id from execution context if not explicitly provided,
        # same as tables, config, etc.
        if org_id is None:
            org_id = get_default_scope()

        client = get_client()
        payload: dict[str, Any] = {
            "workflow_id": workflow,
            "input_data": input_data or {},
            "sync": False,
        }
        if org_id is not None:
            payload["org_id"] = org_id
        if run_as is not None:
            payload["run_as"] = run_as
        if scheduled_at is not None:
            payload["scheduled_at"] = scheduled_at.isoformat()
        if delay_seconds is not None:
            payload["delay_seconds"] = delay_seconds
        response = await client.post("/api/workflows/execute", json=payload)
        raise_for_status_with_detail(response)
        return response.json()["execution_id"]

    @staticmethod
    async def cancel(execution_id: str) -> None:
        """Cancel a Scheduled workflow execution.

        Args:
            execution_id: The execution ID to cancel.

        Raises:
            httpx.HTTPStatusError: 409 if the execution is not Scheduled,
                404 if not found, 403 if forbidden.
            RuntimeError: If not authenticated.

        Example:
            >>> from bifrost import workflows
            >>> await workflows.cancel("exec-123")
        """
        client = get_client()
        response = await client.post(
            f"/api/workflows/executions/{execution_id}/cancel"
        )
        raise_for_status_with_detail(response)

    @staticmethod
    async def get(execution_id: str) -> WorkflowExecution:
        """
        Get execution details for a workflow.

        Args:
            execution_id: Execution ID

        Returns:
            WorkflowExecution: Execution details including status, result, logs

        Raises:
            ValueError: If execution not found
            RuntimeError: If not authenticated

        Example:
            >>> from bifrost import workflows
            >>> execution = await workflows.get("exec-123")
            >>> print(execution.status)
        """
        # Delegate to executions SDK
        return await executions.get(execution_id)
