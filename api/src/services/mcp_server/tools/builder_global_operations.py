"""Hidden Global Builder tools for reviewed loose-resource operation changes."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastmcp.tools import ToolResult

from src.core.database import get_db_context
from src.services.builder.global_operation_changes import (
    GlobalOperationChangeError,
    discard_staged_global_operation_change,
    global_operation_inventory,
    list_staged_global_operation_changes,
    stage_global_operation_change as stage_global_operation_change_service,
    validate_staged_global_operation_changes,
)
from src.services.mcp_server.tool_result import error_result, success_result

STAGE_GLOBAL_OPERATION_TOOL_ID = "stage_global_operation_change"
LIST_GLOBAL_OPERATIONS_TOOL_ID = "list_global_operation_changes"
VALIDATE_GLOBAL_OPERATIONS_TOOL_ID = "validate_global_operation_changes"
DISCARD_GLOBAL_OPERATION_TOOL_ID = "discard_global_operation_change"
GLOBAL_OPERATION_INVENTORY_TOOL_ID = "global_operation_changeset_inventory"

BUILDER_GLOBAL_OPERATION_TOOL_IDS = frozenset(
    {
        STAGE_GLOBAL_OPERATION_TOOL_ID,
        LIST_GLOBAL_OPERATIONS_TOOL_ID,
        VALIDATE_GLOBAL_OPERATIONS_TOOL_ID,
        DISCARD_GLOBAL_OPERATION_TOOL_ID,
        GLOBAL_OPERATION_INVENTORY_TOOL_ID,
    }
)
HIDDEN_TOOL_IDS = BUILDER_GLOBAL_OPERATION_TOOL_IDS


def _global_solution_id(context: Any) -> UUID:
    solution_id = getattr(context, "agent_solution_id", None)
    if isinstance(solution_id, UUID):
        return solution_id
    if isinstance(solution_id, str) and solution_id:
        return UUID(solution_id)
    raise GlobalOperationChangeError(
        "Global operation changes are unavailable outside a Builder session"
    )


async def global_operation_changeset_inventory(context: Any) -> ToolResult:
    _global_solution_id(context)
    return success_result(
        "Loaded Global operation changeset inventory.",
        global_operation_inventory(),
    )


async def stage_global_operation_change(
    context: Any,
    operation_id: str,
    payload: dict[str, Any],
    resource_id: str | None = None,
) -> ToolResult:
    """Stage one reviewed Global operation without changing live resources."""

    try:
        solution_id = _global_solution_id(context)
        async with get_db_context() as db:
            result = await stage_global_operation_change_service(
                db,
                solution_id=solution_id,
                context=context,
                operation_id=operation_id,
                payload=payload,
                resource_id=resource_id,
                created_by=getattr(context, "user_id", None),
            )
    except GlobalOperationChangeError as exc:
        return error_result(str(exc))
    return success_result(
        f"Staged {operation_id} for review.",
        {
            "change_id": str(result.id),
            "operation_id": result.operation_id,
            "resource_type": result.resource_type,
            "resource_id": result.resource_id,
            "state": result.state,
            "validation_errors": result.validation_errors,
        },
    )


async def list_global_operation_changes(context: Any) -> ToolResult:
    """List staged Global operation changes awaiting review/apply."""

    try:
        solution_id = _global_solution_id(context)
        async with get_db_context() as db:
            results = await list_staged_global_operation_changes(
                db,
                solution_id=solution_id,
            )
    except GlobalOperationChangeError as exc:
        return error_result(str(exc))
    return success_result(
        f"Found {len(results)} staged Global operation change(s).",
        {
            "changes": [
                {
                    "change_id": str(result.id),
                    "operation_id": result.operation_id,
                    "resource_type": result.resource_type,
                    "resource_id": result.resource_id,
                    "state": result.state,
                    "validation_errors": result.validation_errors,
                    "before": result.before_state,
                    "after": result.payload,
                }
                for result in results
            ]
        },
    )


async def validate_global_operation_changes(context: Any) -> ToolResult:
    """Validate staged Global operation changes without applying them."""

    try:
        solution_id = _global_solution_id(context)
        async with get_db_context() as db:
            errors = await validate_staged_global_operation_changes(
                db,
                solution_id=solution_id,
            )
    except GlobalOperationChangeError as exc:
        return error_result(str(exc))
    return success_result(
        "Global operation changes are valid." if not errors else "Validation failed.",
        {"valid": not errors, "errors": errors},
    )


async def discard_global_operation_change(context: Any, change_id: str) -> ToolResult:
    """Discard one staged Global operation change before human apply."""

    try:
        solution_id = _global_solution_id(context)
        async with get_db_context() as db:
            result = await discard_staged_global_operation_change(
                db,
                solution_id=solution_id,
                change_id=UUID(change_id),
                requested_by=UUID(str(getattr(context, "user_id"))),
            )
    except (ValueError, GlobalOperationChangeError) as exc:
        return error_result(str(exc))
    return success_result(
        f"Discarded staged Global operation change {change_id}.",
        {
            "change_id": str(result.id),
            "operation_id": result.operation_id,
            "state": result.state,
        },
    )


TOOLS = [
    (
        STAGE_GLOBAL_OPERATION_TOOL_ID,
        "Stage Global Operation Change",
        (
            "Stage a reviewed create/update/delete operation for a Global loose "
            "resource. Does not mutate live resources."
        ),
    ),
    (
        LIST_GLOBAL_OPERATIONS_TOOL_ID,
        "List Global Operation Changes",
        "List staged Global operation changes awaiting review.",
    ),
    (
        VALIDATE_GLOBAL_OPERATIONS_TOOL_ID,
        "Validate Global Operation Changes",
        "Validate staged Global operation changes without applying them.",
    ),
    (
        DISCARD_GLOBAL_OPERATION_TOOL_ID,
        "Discard Global Operation Change",
        "Discard one staged Global operation change before human apply.",
    ),
    (
        GLOBAL_OPERATION_INVENTORY_TOOL_ID,
        "Global Operation Changeset Inventory",
        "Describe implemented and fail-closed Global operation changeset domains.",
    ),
]


def register_tools(mcp: Any, get_context_fn: Any) -> None:
    from src.services.mcp_server.generators.fastmcp_generator import (
        register_tool_with_context,
    )

    functions = {
        STAGE_GLOBAL_OPERATION_TOOL_ID: stage_global_operation_change,
        LIST_GLOBAL_OPERATIONS_TOOL_ID: list_global_operation_changes,
        VALIDATE_GLOBAL_OPERATIONS_TOOL_ID: validate_global_operation_changes,
        DISCARD_GLOBAL_OPERATION_TOOL_ID: discard_global_operation_change,
        GLOBAL_OPERATION_INVENTORY_TOOL_ID: global_operation_changeset_inventory,
    }
    descriptions = {tool_id: description for tool_id, _, description in TOOLS}
    for tool_id, function in functions.items():
        register_tool_with_context(
            mcp,
            function,
            tool_id,
            descriptions[tool_id],
            get_context_fn,
        )
