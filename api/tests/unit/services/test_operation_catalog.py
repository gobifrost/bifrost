"""Canonical operation identity and Agent vertical-slice tripwires."""

from copy import deepcopy

import pytest

from src.main import app
from src.models.contracts.operation_catalog import OperationDefinition
from src.services.operation_catalog import (
    OPERATION_CATALOG,
    get_operation,
    validate_operation_catalog,
)


AGENT_OPERATIONS = {
    "agents.list": ("GET", "/api/agents", ("agents", "list"), "bifrost_list_agents"),
    "agents.get": (
        "GET",
        "/api/agents/{agent_id}",
        ("agents", "get"),
        "bifrost_get_agent",
    ),
    "agents.create": (
        "POST",
        "/api/agents",
        ("agents", "create"),
        "bifrost_create_agent",
    ),
    "agents.update": (
        "PUT",
        "/api/agents/{agent_id}",
        ("agents", "update"),
        "bifrost_update_agent",
    ),
    "agents.delete": (
        "DELETE",
        "/api/agents/{agent_id}",
        ("agents", "delete"),
        "bifrost_delete_agent",
    ),
}


def test_agent_vertical_slice_has_stable_surface_bindings() -> None:
    assert {operation.operation_id for operation in OPERATION_CATALOG} == set(
        AGENT_OPERATIONS
    )
    for operation_id, (method, path, cli_path, mcp_name) in AGENT_OPERATIONS.items():
        operation = get_operation(operation_id)
        assert (operation.rest.method, operation.rest.path) == (method, path)
        assert operation.cli is not None and operation.cli.path == cli_path
        assert operation.mcp is not None and operation.mcp.name == mcp_name
        assert operation.native_builder is True


def test_agent_routes_publish_catalog_identity_in_openapi() -> None:
    schema = app.openapi()
    for operation_id, (method, path, cli_path, mcp_name) in AGENT_OPERATIONS.items():
        route = schema["paths"][path][method.lower()]
        assert route["operationId"] == operation_id
        extension = route["x-bifrost-operation"]
        assert extension["id"] == operation_id
        assert extension["cli"] == list(cli_path)
        assert extension["mcp"] == mcp_name


def test_catalog_rejects_duplicate_operation_ids() -> None:
    duplicate = OperationDefinition.model_validate(
        deepcopy(OPERATION_CATALOG[0].model_dump())
    )
    with pytest.raises(ValueError, match="duplicate operation ID"):
        validate_operation_catalog((*OPERATION_CATALOG, duplicate))


def test_catalog_requires_valid_graph_inspired_action_scopes() -> None:
    invalid = OPERATION_CATALOG[0].model_copy(
        update={
            "operation_id": "agents.invalid",
            "rest": OPERATION_CATALOG[0].rest.model_copy(
                update={"path": "/api/agents-invalid"}
            ),
            "cli": OPERATION_CATALOG[0].cli.model_copy(
                update={"path": ("agents", "invalid")}
            ),
            "mcp": OPERATION_CATALOG[0].mcp.model_copy(
                update={"name": "bifrost_invalid_agent"}
            ),
            "action_scopes": ("Agents.Write",),
        }
    )
    with pytest.raises(ValueError, match="invalid action scope"):
        validate_operation_catalog((*OPERATION_CATALOG, invalid))
