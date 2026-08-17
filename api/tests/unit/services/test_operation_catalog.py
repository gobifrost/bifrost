"""Canonical operation identity and vertical-slice tripwires."""

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

FORM_OPERATIONS = {
    "forms.list": ("GET", "/api/forms", ("forms", "list"), "bifrost_list_forms"),
    "forms.get": (
        "GET",
        "/api/forms/{form_id}",
        ("forms", "get"),
        "bifrost_get_form",
    ),
    "forms.create": (
        "POST",
        "/api/forms",
        ("forms", "create"),
        "bifrost_create_form",
    ),
    "forms.update": (
        "PATCH",
        "/api/forms/{form_id}",
        ("forms", "update"),
        "bifrost_update_form",
    ),
    "forms.delete": (
        "DELETE",
        "/api/forms/{form_id}",
        ("forms", "delete"),
        "bifrost_delete_form",
    ),
}

TABLE_OPERATIONS = {
    "tables.list": ("GET", "/api/tables", ("tables", "list"), "bifrost_list_tables"),
    "tables.get": (
        "GET",
        "/api/tables/{table_id}",
        ("tables", "get"),
        "bifrost_get_table",
    ),
    "tables.create": (
        "POST",
        "/api/tables",
        ("tables", "create"),
        "bifrost_create_table",
    ),
    "tables.update": (
        "PATCH",
        "/api/tables/{table_id}",
        ("tables", "update"),
        "bifrost_update_table",
    ),
    "tables.delete": (
        "DELETE",
        "/api/tables/{table_id}",
        ("tables", "delete"),
        "bifrost_delete_table",
    ),
}

CANONICAL_OPERATIONS = {
    **AGENT_OPERATIONS,
    **FORM_OPERATIONS,
    **TABLE_OPERATIONS,
}


def test_canonical_vertical_slices_have_stable_surface_bindings() -> None:
    assert {operation.operation_id for operation in OPERATION_CATALOG} == set(
        CANONICAL_OPERATIONS
    )
    for operation_id, (
        method,
        path,
        cli_path,
        mcp_name,
    ) in CANONICAL_OPERATIONS.items():
        operation = get_operation(operation_id)
        assert (operation.rest.method, operation.rest.path) == (method, path)
        assert operation.cli is not None and operation.cli.path == cli_path
        assert operation.mcp is not None and operation.mcp.name == mcp_name
        assert operation.native_builder is True


def test_catalog_routes_publish_identity_in_openapi() -> None:
    schema = app.openapi()
    for operation_id, (
        method,
        path,
        cli_path,
        mcp_name,
    ) in CANONICAL_OPERATIONS.items():
        route = schema["paths"][path][method.lower()]
        assert route["operationId"] == operation_id
        extension = route["x-bifrost-operation"]
        assert extension["id"] == operation_id
        assert extension["cli"] == list(cli_path)
        assert extension["mcp"] == mcp_name


def test_mcp_registration_uses_only_catalog_names() -> None:
    from src.services.mcp_server.server import (
        get_system_tool_function,
        get_system_tools,
    )

    registered = {tool["id"] for tool in get_system_tools()}
    catalog_names = {binding[3] for binding in CANONICAL_OPERATIONS.values()}
    legacy_names = {name.removeprefix("bifrost_") for name in catalog_names}

    assert catalog_names <= registered
    assert registered.isdisjoint(legacy_names)
    assert all(callable(get_system_tool_function(name)) for name in catalog_names)


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
