"""
Execution MCP Tools

Tools for listing and viewing workflow execution history.
"""

import logging
from typing import Any
from uuid import UUID

from fastmcp.tools import ToolResult

from src.core.principal import UserPrincipal
from src.services.mcp_server.tool_result import error_result, success_result
from src.services.mcp_server.tools.db import get_tool_db

# MCPContext is imported where needed to avoid circular imports

logger = logging.getLogger(__name__)


def _context_to_user_principal(context: Any) -> UserPrincipal:
    """Convert MCPContext to UserPrincipal for repository calls."""
    user_id = context.user_id
    if isinstance(user_id, str):
        user_id = UUID(user_id)

    org_id = context.org_id
    if isinstance(org_id, str):
        org_id = UUID(org_id)

    return UserPrincipal(
        user_id=user_id,
        email=getattr(context, "user_email", ""),
        organization_id=org_id,
        name=getattr(context, "user_name", ""),
        is_superuser=getattr(context, "is_platform_admin", False),
    )


async def list_executions(
    context: Any,
    workflow_name: str | None = None,
    status: str | None = None,
    limit: int = 20,
) -> ToolResult:
    """List recent workflow executions."""
    from src.repositories.executions import ExecutionRepository

    logger.info(f"MCP list_executions called with workflow={workflow_name}, status={status}")

    try:
        async with get_tool_db(context) as db:
            repo = ExecutionRepository(db)

            # Convert context to UserPrincipal
            user = _context_to_user_principal(context)

            # Get org_id as UUID if present
            org_id = None
            if context.org_id:
                org_id = UUID(str(context.org_id)) if isinstance(context.org_id, str) else context.org_id

            # Call repository with correct signature
            executions, _ = await repo.list_executions(
                user=user,
                org_id=org_id,
                workflow_name=workflow_name,
                status_filter=status,
                limit=limit,
            )

            if not executions:
                return success_result("No executions found", {"executions": [], "count": 0})

            execution_list = []
            for ex in executions:
                execution_data = {
                    "id": ex.execution_id,
                    "workflow_name": ex.workflow_name or "Unknown",
                    "status": ex.status.value if hasattr(ex.status, "value") else ex.status,
                    "duration_ms": ex.duration_ms,
                    "created_at": ex.started_at.isoformat() if ex.started_at else None,
                    "error": ex.error_message[:100] + "..." if ex.error_message and len(ex.error_message) > 100 else ex.error_message,
                }
                execution_list.append(execution_data)

            display_text = f"Found {len(execution_list)} execution(s)"
            return success_result(display_text, {"executions": execution_list, "count": len(execution_list)})

    except Exception as e:
        logger.exception(f"Error listing executions via MCP: {e}")
        return error_result(f"Error listing executions: {str(e)}")


async def get_execution(context: Any, execution_id: str) -> ToolResult:
    """Get details and logs for a specific workflow execution."""
    from src.repositories.executions import ExecutionRepository

    logger.info(f"MCP get_execution called with id={execution_id}")

    if not execution_id:
        return error_result("execution_id is required")

    try:
        async with get_tool_db(context) as db:
            repo = ExecutionRepository(db)

            # Convert context to UserPrincipal
            user = _context_to_user_principal(context)

            # Get execution with authorization check built in
            execution, error_code = await repo.get_execution(
                execution_id=UUID(execution_id),
                user=user,
            )

            if error_code == "NotFound":
                return error_result(f"Execution not found: {execution_id}")
            if error_code == "Forbidden":
                return error_result("Access denied")
            if not execution:
                return error_result(f"Execution not found: {execution_id}")

            # Build execution data from WorkflowExecution pydantic model
            execution_data: dict[str, Any] = {
                "id": execution.execution_id,
                "workflow_name": execution.workflow_name or "Unknown",
                "status": execution.status.value if hasattr(execution.status, "value") else execution.status,
                "duration_ms": execution.duration_ms,
                "created_at": execution.started_at.isoformat() if execution.started_at else None,
                "completed_at": execution.completed_at.isoformat() if execution.completed_at else None,
                "error": execution.error_message,
                "result": execution.result,
            }

            # Logs are included in the execution response from get_execution
            if execution.logs:
                execution_data["logs"] = [
                    {"level": log.get("level", "info"), "message": log.get("message", "")}
                    for log in execution.logs[-20:]  # Last 20 logs
                ]
            else:
                execution_data["logs"] = []

            workflow_name = execution.workflow_name or "Unknown"
            status_str = execution.status.value if hasattr(execution.status, "value") else str(execution.status)
            display_text = f"Execution: {workflow_name} ({status_str})"
            return success_result(display_text, execution_data)

    except Exception as e:
        logger.exception(f"Error getting execution via MCP: {e}")
        return error_result(f"Error getting execution: {str(e)}")


# Tool metadata for registration
TOOLS = [
    ("list_executions", "List Executions", "List recent workflow executions."),
    ("get_execution", "Get Execution", "Get details and logs for a specific workflow execution."),
]


def register_tools(mcp: Any, get_context_fn: Any) -> None:
    """Register all execution tools with FastMCP."""
    from src.services.mcp_server.generators.fastmcp_generator import register_tool_with_context

    tool_funcs = {
        "list_executions": list_executions,
        "get_execution": get_execution,
    }

    for tool_id, name, description in TOOLS:
        register_tool_with_context(mcp, tool_funcs[tool_id], tool_id, description, get_context_fn)
