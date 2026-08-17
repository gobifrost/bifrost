"""Canonical Bifrost operation identities and transport bindings.

Agent CRUD is the first vertical slice.  Additional domains must enter this
catalog before gaining new CLI, MCP, or native Builder implementations.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from shared.authorization_scopes import is_valid_scope_key
from src.models.contracts.operation_catalog import (
    CliOperationBinding,
    ManifestOperationBinding,
    McpOperationBinding,
    OperationDefinition,
    OperationTargetKind,
    RestOperationBinding,
)


_AGENT_SDK_EXCLUSION = "Agent administration is not available to application SDKs."


OPERATION_CATALOG: tuple[OperationDefinition, ...] = (
    OperationDefinition(
        operation_id="agents.list",
        summary="List Agents visible to the caller",
        target_kind=OperationTargetKind.COLLECTION,
        rest=RestOperationBinding(
            method="GET",
            path="/api/agents",
            response_model="list[AgentSummary]",
        ),
        cli=CliOperationBinding(path=("agents", "list")),
        mcp=McpOperationBinding(name="bifrost_list_agents"),
        native_builder=True,
        action_scopes=("agents.read",),
        authorization_resolver="AgentRepository.list_agents",
        exclusions={
            "manifest": "Manifests reconcile Agent state; they do not perform collection reads.",
            "sdk": _AGENT_SDK_EXCLUSION,
        },
    ),
    OperationDefinition(
        operation_id="agents.get",
        summary="Get one Agent visible to the caller",
        target_kind=OperationTargetKind.RESOURCE,
        rest=RestOperationBinding(
            method="GET",
            path="/api/agents/{agent_id}",
            response_model="AgentPublic",
        ),
        cli=CliOperationBinding(path=("agents", "get")),
        mcp=McpOperationBinding(name="bifrost_get_agent"),
        native_builder=True,
        action_scopes=("agents.read",),
        authorization_resolver="AgentRepository.get_agent_with_access_check",
        exclusions={
            "manifest": "Manifests reconcile Agent state; they do not perform resource reads.",
            "sdk": _AGENT_SDK_EXCLUSION,
        },
    ),
    OperationDefinition(
        operation_id="agents.create",
        summary="Create an Agent in an allowed target",
        target_kind=OperationTargetKind.COLLECTION,
        rest=RestOperationBinding(
            method="POST",
            path="/api/agents",
            request_model="AgentCreate",
            response_model="AgentPublic",
        ),
        cli=CliOperationBinding(path=("agents", "create")),
        mcp=McpOperationBinding(name="bifrost_create_agent"),
        native_builder=True,
        manifest=ManifestOperationBinding(entity="agents"),
        action_scopes=("agents.write",),
        authorization_resolver="Agent create policy and target organization resolver",
        audit_event="agent.create",
        side_effects=(
            "persist Agent and relation grants",
            "synchronize Agent roles to referenced workflows",
            "write manifest change through RepoSyncWriter when applicable",
        ),
        exclusions={"sdk": _AGENT_SDK_EXCLUSION},
    ),
    OperationDefinition(
        operation_id="agents.update",
        summary="Update an Agent the caller may manage",
        target_kind=OperationTargetKind.RESOURCE,
        rest=RestOperationBinding(
            method="PUT",
            path="/api/agents/{agent_id}",
            request_model="AgentUpdate",
            response_model="AgentPublic",
        ),
        cli=CliOperationBinding(path=("agents", "update")),
        mcp=McpOperationBinding(name="bifrost_update_agent"),
        native_builder=True,
        manifest=ManifestOperationBinding(entity="agents"),
        action_scopes=("agents.write",),
        authorization_resolver="AgentRepository plus ownership and Solution-management guards",
        audit_event="agent.update",
        side_effects=(
            "replace selected Agent relation grants",
            "synchronize Agent roles to referenced workflows",
            "write manifest change through RepoSyncWriter when applicable",
        ),
        exclusions={"sdk": _AGENT_SDK_EXCLUSION},
    ),
    OperationDefinition(
        operation_id="agents.delete",
        summary="Delete an Agent the caller may manage",
        target_kind=OperationTargetKind.RESOURCE,
        rest=RestOperationBinding(
            method="DELETE",
            path="/api/agents/{agent_id}",
        ),
        cli=CliOperationBinding(path=("agents", "delete")),
        mcp=McpOperationBinding(name="bifrost_delete_agent"),
        native_builder=True,
        manifest=ManifestOperationBinding(entity="agents", behavior="remove"),
        action_scopes=("agents.write",),
        authorization_resolver="Agent ownership and Solution-management guards",
        audit_event="agent.delete",
        side_effects=(
            "delete Agent relation grants through database cascades",
            "remove manifest entry through RepoSyncWriter when applicable",
        ),
        exclusions={"sdk": _AGENT_SDK_EXCLUSION},
    ),
)


_BY_ID = {operation.operation_id: operation for operation in OPERATION_CATALOG}


def get_operation(operation_id: str) -> OperationDefinition:
    """Return one canonical operation or fail during import/startup."""

    try:
        return _BY_ID[operation_id]
    except KeyError as exc:
        raise KeyError(f"Unknown Bifrost operation: {operation_id}") from exc


def operation_route(operation_id: str) -> dict[str, Any]:
    """FastAPI decorator metadata for a catalog-backed REST route."""

    operation = get_operation(operation_id)
    return {
        "operation_id": operation.operation_id,
        "openapi_extra": {
            "x-bifrost-operation": {
                "id": operation.operation_id,
                "mcp": operation.mcp.name if operation.mcp else None,
                "cli": list(operation.cli.path) if operation.cli else None,
                "action_scopes": list(operation.action_scopes),
                "async_policy": operation.async_policy.value,
            }
        },
    }


def validate_operation_catalog(
    operations: Iterable[OperationDefinition] = OPERATION_CATALOG,
) -> None:
    """Fail fast on duplicate bindings or invalid scope/name conventions."""

    materialized = tuple(operations)

    def _duplicates(values: Iterable[object]) -> list[object]:
        items = [value for value in values if value is not None]
        return sorted({value for value in items if items.count(value) > 1})

    checks = {
        "operation ID": _duplicates(op.operation_id for op in materialized),
        "REST binding": _duplicates(
            (op.rest.method, op.rest.path) for op in materialized
        ),
        "CLI binding": _duplicates(
            op.cli.path if op.cli is not None else None for op in materialized
        ),
        "MCP binding": _duplicates(
            op.mcp.name if op.mcp is not None else None for op in materialized
        ),
    }
    errors = [
        f"duplicate {label}(s): {', '.join(map(str, duplicates))}"
        for label, duplicates in checks.items()
        if duplicates
    ]
    invalid_scopes = sorted(
        {
            scope
            for operation in materialized
            for scope in operation.action_scopes
            if not is_valid_scope_key(scope)
        }
    )
    if invalid_scopes:
        errors.append("invalid action scope(s): " + ", ".join(invalid_scopes))

    for operation in materialized:
        if operation.cli and operation.mcp:
            resource, verb = operation.cli.path[0], operation.cli.path[-1]
            noun = resource[:-1] if verb not in {"list", "search"} and resource.endswith("s") else resource
            expected = f"bifrost_{verb}_{noun}"
            if operation.mcp.name != expected:
                errors.append(
                    f"{operation.operation_id} maps {operation.cli.path!r} to "
                    f"{operation.mcp.name!r}; expected {expected!r}"
                )

    if errors:
        raise ValueError("; ".join(errors))


validate_operation_catalog()


__all__ = [
    "OPERATION_CATALOG",
    "get_operation",
    "operation_route",
    "validate_operation_catalog",
]
