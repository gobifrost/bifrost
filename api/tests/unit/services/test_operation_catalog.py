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

APP_OPERATIONS = {
    "apps.list": (
        "GET",
        "/api/applications",
        ("apps", "list"),
        "bifrost_list_apps",
    ),
    "apps.get": (
        "GET",
        "/api/applications/{slug}",
        ("apps", "get"),
        "bifrost_get_app",
    ),
    "apps.create": (
        "POST",
        "/api/applications",
        ("apps", "create"),
        "bifrost_create_app",
    ),
    "apps.update": (
        "PATCH",
        "/api/applications/{app_id}",
        ("apps", "update"),
        "bifrost_update_app",
    ),
    "apps.delete": (
        "DELETE",
        "/api/applications/{app_id}",
        ("apps", "delete"),
        "bifrost_delete_app",
    ),
    "apps.dependencies.get": (
        "GET",
        "/api/applications/{app_id}/dependencies",
        ("apps", "get-dependencies"),
        "bifrost_get_app_dependencies",
    ),
    "apps.dependencies.update": (
        "PUT",
        "/api/applications/{app_id}/dependencies",
        ("apps", "update-dependencies"),
        "bifrost_update_app_dependencies",
    ),
    "apps.validate": (
        "POST",
        "/api/applications/{app_id}/validate",
        ("apps", "validate"),
        "bifrost_validate_app",
    ),
    "apps.publish": (
        "POST",
        "/api/applications/{app_id}/publish",
        ("apps", "publish"),
        "bifrost_publish_app",
    ),
    "apps.replace": (
        "POST",
        "/api/applications/{app_id}/replace",
        ("apps", "replace"),
        "bifrost_replace_app",
    ),
}

WORKFLOW_OPERATIONS = {
    "workflows.list": (
        "GET",
        "/api/workflows",
        ("workflows", "list"),
        "bifrost_list_workflows",
    ),
    "workflows.get": (
        "GET",
        "/api/workflows/{workflow_id}",
        ("workflows", "get"),
        "bifrost_get_workflow",
    ),
    "workflows.validate": (
        "POST",
        "/api/workflows/validate",
        ("workflows", "validate"),
        "bifrost_validate_workflow",
    ),
    "workflows.register": (
        "POST",
        "/api/workflows/register",
        ("workflows", "register"),
        "bifrost_register_workflow",
    ),
    "workflows.execute": (
        "POST",
        "/api/workflows/execute",
        ("workflows", "execute"),
        "bifrost_execute_workflow",
    ),
    "workflows.update": (
        "PATCH",
        "/api/workflows/{workflow_id}",
        ("workflows", "update"),
        "bifrost_update_workflow",
    ),
    "workflows.delete": (
        "DELETE",
        "/api/workflows/{workflow_id}",
        ("workflows", "delete"),
        "bifrost_delete_workflow",
    ),
    "workflows.roles.grant": (
        "POST",
        "/api/workflows/{workflow_id}/roles",
        ("workflows", "grant-role"),
        "bifrost_grant_workflow_role",
    ),
    "workflows.roles.revoke": (
        "DELETE",
        "/api/workflows/{workflow_id}/roles/{role_id}",
        ("workflows", "revoke-role"),
        "bifrost_revoke_workflow_role",
    ),
}

EXECUTION_OPERATIONS = {
    "executions.list": (
        "GET",
        "/api/executions",
        ("workflows", "list-executions"),
        "bifrost_list_workflow_executions",
    ),
    "executions.get": (
        "GET",
        "/api/executions/{execution_id}",
        ("workflows", "get-execution"),
        "bifrost_get_workflow_execution",
    ),
}

KNOWLEDGE_OPERATIONS = {
    "knowledge.search": (
        "POST",
        "/api/knowledge/search",
        ("knowledge", "search"),
        "bifrost_search_knowledge",
    ),
}

PLATFORM_JOB_OPERATIONS = {
    "platform.jobs.get": (
        "GET",
        "/api/platform-jobs/{job_id}",
        ("platform-jobs", "get"),
        "bifrost_get_platform_job",
    ),
}

ROLE_OPERATIONS = {
    "roles.list": (
        "GET",
        "/api/roles",
        ("roles", "list"),
        "bifrost_list_roles",
    ),
    "roles.get": (
        "GET",
        "/api/roles/{role_id}",
        ("roles", "get"),
        "bifrost_get_role",
    ),
    "roles.create": (
        "POST",
        "/api/roles",
        ("roles", "create"),
        "bifrost_create_role",
    ),
    "roles.update": (
        "PATCH",
        "/api/roles/{role_id}",
        ("roles", "update"),
        "bifrost_update_role",
    ),
    "roles.delete": (
        "DELETE",
        "/api/roles/{role_id}",
        ("roles", "delete"),
        "bifrost_delete_role",
    ),
}

POLICY_RULE_OPERATIONS = {
    "policy.rules.list": (
        "GET",
        "/api/policy-rules",
        ("policy-rules", "list"),
        "bifrost_list_policy_rules",
    ),
    "policy.rules.get": (
        "GET",
        "/api/policy-rules/{domain}/{name}",
        ("policy-rules", "get"),
        "bifrost_get_policy_rule",
    ),
    "policy.rules.create": (
        "POST",
        "/api/policy-rules",
        ("policy-rules", "create"),
        "bifrost_create_policy_rule",
    ),
    "policy.rules.update": (
        "PUT",
        "/api/policy-rules/{domain}/{name}",
        ("policy-rules", "update"),
        "bifrost_update_policy_rule",
    ),
    "policy.rules.delete": (
        "DELETE",
        "/api/policy-rules/{domain}/{name}",
        ("policy-rules", "delete"),
        "bifrost_delete_policy_rule",
    ),
    "policy.rules.list_usages": (
        "GET",
        "/api/policy-rules/{domain}/{name}/usages",
        ("policy-rules", "list-usages"),
        "bifrost_list_policy_rule_usages",
    ),
}

CONFIG_OPERATIONS = {
    "configs.list": (
        "GET",
        "/api/config",
        ("configs", "list"),
        "bifrost_list_configs",
    ),
    "configs.get": (
        "GET",
        "/api/config/{config_id}",
        ("configs", "get"),
        "bifrost_get_config",
    ),
    "configs.create": (
        "POST",
        "/api/config",
        ("configs", "create"),
        "bifrost_create_config",
    ),
    "configs.update": (
        "PUT",
        "/api/config/{config_id}",
        ("configs", "update"),
        "bifrost_update_config",
    ),
    "configs.delete": (
        "DELETE",
        "/api/config/{config_id}",
        ("configs", "delete"),
        "bifrost_delete_config",
    ),
}

CLAIM_OPERATIONS = {
    "claims.list": (
        "GET",
        "/api/claims",
        ("claims", "list"),
        "bifrost_list_claims",
    ),
    "claims.get": (
        "GET",
        "/api/claims/{name}",
        ("claims", "get"),
        "bifrost_get_claim",
    ),
    "claims.create": (
        "POST",
        "/api/claims",
        ("claims", "create"),
        "bifrost_create_claim",
    ),
    "claims.update": (
        "PATCH",
        "/api/claims/{name}",
        ("claims", "update"),
        "bifrost_update_claim",
    ),
    "claims.delete": (
        "DELETE",
        "/api/claims/{name}",
        ("claims", "delete"),
        "bifrost_delete_claim",
    ),
}

FILE_POLICY_OPERATIONS = {
    "files.policies.list": (
        "GET",
        "/api/files/policies",
        ("files", "policies", "list"),
        "bifrost_list_file_policies",
    ),
    "files.policies.get": (
        "GET",
        "/api/files/policies/{policy_path}",
        ("files", "policies", "get"),
        "bifrost_get_file_policy",
    ),
    "files.policies.set": (
        "PUT",
        "/api/files/policies/{policy_path}",
        ("files", "policies", "set"),
        "bifrost_set_file_policy",
    ),
    "files.policies.delete": (
        "DELETE",
        "/api/files/policies/{policy_path}",
        ("files", "policies", "delete"),
        "bifrost_delete_file_policy",
    ),
}

EVENT_OPERATIONS = {
    "events.sources.list": (
        "GET",
        "/api/events/sources",
        ("events", "list-sources"),
        "bifrost_list_event_sources",
    ),
    "events.sources.get": (
        "GET",
        "/api/events/sources/{source_id}",
        ("events", "get-source"),
        "bifrost_get_event_source",
    ),
    "events.sources.create": (
        "POST",
        "/api/events/sources",
        ("events", "create-source"),
        "bifrost_create_event_source",
    ),
    "events.sources.update": (
        "PATCH",
        "/api/events/sources/{source_id}",
        ("events", "update-source"),
        "bifrost_update_event_source",
    ),
    "events.sources.delete": (
        "DELETE",
        "/api/events/sources/{source_id}",
        ("events", "delete-source"),
        "bifrost_delete_event_source",
    ),
    "events.subscriptions.list": (
        "GET",
        "/api/events/sources/{source_id}/subscriptions",
        ("events", "list-subscriptions"),
        "bifrost_list_event_subscriptions",
    ),
    "events.subscriptions.get": (
        "GET",
        "/api/events/sources/{source_id}/subscriptions/{subscription_id}",
        ("events", "get-subscription"),
        "bifrost_get_event_subscription",
    ),
    "events.subscriptions.create": (
        "POST",
        "/api/events/sources/{source_id}/subscriptions",
        ("events", "create-subscription"),
        "bifrost_create_event_subscription",
    ),
    "events.subscriptions.update": (
        "PATCH",
        "/api/events/sources/{source_id}/subscriptions/{subscription_id}",
        ("events", "update-subscription"),
        "bifrost_update_event_subscription",
    ),
    "events.subscriptions.delete": (
        "DELETE",
        "/api/events/sources/{source_id}/subscriptions/{subscription_id}",
        ("events", "delete-subscription"),
        "bifrost_delete_event_subscription",
    ),
    "events.webhook_adapters.list": (
        "GET",
        "/api/events/adapters",
        ("events", "list-webhook-adapters"),
        "bifrost_list_event_webhook_adapters",
    ),
}

ORGANIZATION_OPERATIONS = {
    "organizations.list": (
        "GET",
        "/api/organizations",
        ("organizations", "list"),
        "bifrost_list_organizations",
    ),
    "organizations.get": (
        "GET",
        "/api/organizations/{org_id}",
        ("organizations", "get"),
        "bifrost_get_organization",
    ),
    "organizations.create": (
        "POST",
        "/api/organizations",
        ("organizations", "create"),
        "bifrost_create_organization",
    ),
    "organizations.update": (
        "PATCH",
        "/api/organizations/{org_id}",
        ("organizations", "update"),
        "bifrost_update_organization",
    ),
    "organizations.delete": (
        "DELETE",
        "/api/organizations/{org_id}",
        ("organizations", "delete"),
        "bifrost_delete_organization",
    ),
}

INTEGRATION_OPERATIONS = {
    "integrations.list": (
        "GET",
        "/api/integrations",
        ("integrations", "list"),
        "bifrost_list_integrations",
    ),
    "integrations.get": (
        "GET",
        "/api/integrations/{integration_id}",
        ("integrations", "get"),
        "bifrost_get_integration",
    ),
    "integrations.create": (
        "POST",
        "/api/integrations",
        ("integrations", "create"),
        "bifrost_create_integration",
    ),
    "integrations.update": (
        "PUT",
        "/api/integrations/{integration_id}",
        ("integrations", "update"),
        "bifrost_update_integration",
    ),
    "integrations.mappings.create": (
        "POST",
        "/api/integrations/{integration_id}/mappings",
        ("integrations", "create-mapping"),
        "bifrost_create_integration_mapping",
    ),
    "integrations.mappings.update": (
        "PUT",
        "/api/integrations/{integration_id}/mappings/{mapping_id}",
        ("integrations", "update-mapping"),
        "bifrost_update_integration_mapping",
    ),
}

WORKSPACE_FILE_OPERATIONS = {
    "workspace.files.list": (
        "POST",
        "/api/files/list",
        ("files", "list"),
        "bifrost_list_files",
    ),
    "workspace.files.search": (
        "POST",
        "/api/files/search",
        ("files", "search"),
        "bifrost_search_files",
    ),
    "workspace.files.read": (
        "POST",
        "/api/files/read",
        ("files", "read"),
        "bifrost_read_file",
    ),
    "workspace.files.stat": (
        "POST",
        "/api/files/stat",
        ("files", "stat"),
        "bifrost_stat_file",
    ),
    "workspace.files.exists": (
        "POST",
        "/api/files/exists",
        ("files", "exists"),
        "bifrost_exists_file",
    ),
    "workspace.files.write": (
        "POST",
        "/api/files/write",
        ("files", "write"),
        "bifrost_write_file",
    ),
    "workspace.files.patch": (
        "POST",
        "/api/files/patch",
        ("files", "patch"),
        "bifrost_patch_file",
    ),
    "workspace.files.delete": (
        "POST",
        "/api/files/delete",
        ("files", "delete"),
        "bifrost_delete_file",
    ),
}

CANONICAL_OPERATIONS = {
    **AGENT_OPERATIONS,
    **FORM_OPERATIONS,
    **TABLE_OPERATIONS,
    **APP_OPERATIONS,
    **WORKFLOW_OPERATIONS,
    **EXECUTION_OPERATIONS,
    **KNOWLEDGE_OPERATIONS,
    **ROLE_OPERATIONS,
    **CLAIM_OPERATIONS,
    **CONFIG_OPERATIONS,
    **POLICY_RULE_OPERATIONS,
    **FILE_POLICY_OPERATIONS,
    **EVENT_OPERATIONS,
    **ORGANIZATION_OPERATIONS,
    **INTEGRATION_OPERATIONS,
    **WORKSPACE_FILE_OPERATIONS,
    **PLATFORM_JOB_OPERATIONS,
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
        assert operation.native_builder is (
            operation_id not in ORGANIZATION_OPERATIONS
        )


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
    builder_workspace_names = {
        "delete_file",
        "list_files",
        "read_file",
        "write_file",
    }

    assert catalog_names <= registered
    assert registered.isdisjoint(legacy_names - builder_workspace_names)
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
