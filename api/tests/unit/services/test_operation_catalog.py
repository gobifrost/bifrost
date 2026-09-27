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
    "agents.list": ("GET", "/api/agents", ("agents", "list"), "bifrost_agent_list"),
    "agents.get": (
        "GET",
        "/api/agents/{agent_id}",
        ("agents", "get"),
        "bifrost_agent_get",
    ),
    "agents.create": (
        "POST",
        "/api/agents",
        ("agents", "create"),
        "bifrost_agent_create",
    ),
    "agents.update": (
        "PUT",
        "/api/agents/{agent_id}",
        ("agents", "update"),
        "bifrost_agent_update",
    ),
    "agents.delete": (
        "DELETE",
        "/api/agents/{agent_id}",
        ("agents", "delete"),
        "bifrost_agent_delete",
    ),
}

FORM_OPERATIONS = {
    "forms.list": ("GET", "/api/forms", ("forms", "list"), "bifrost_form_list"),
    "forms.get": (
        "GET",
        "/api/forms/{form_id}",
        ("forms", "get"),
        "bifrost_form_get",
    ),
    "forms.create": (
        "POST",
        "/api/forms",
        ("forms", "create"),
        "bifrost_form_create",
    ),
    "forms.update": (
        "PATCH",
        "/api/forms/{form_id}",
        ("forms", "update"),
        "bifrost_form_update",
    ),
    "forms.delete": (
        "DELETE",
        "/api/forms/{form_id}",
        ("forms", "delete"),
        "bifrost_form_delete",
    ),
}

TABLE_OPERATIONS = {
    "tables.list": ("GET", "/api/tables", ("tables", "list"), "bifrost_table_list"),
    "tables.get": (
        "GET",
        "/api/tables/{table_id}",
        ("tables", "get"),
        "bifrost_table_get",
    ),
    "tables.create": (
        "POST",
        "/api/tables",
        ("tables", "create"),
        "bifrost_table_create",
    ),
    "tables.update": (
        "PATCH",
        "/api/tables/{table_id}",
        ("tables", "update"),
        "bifrost_table_update",
    ),
    "tables.delete": (
        "DELETE",
        "/api/tables/{table_id}",
        ("tables", "delete"),
        "bifrost_table_delete",
    ),
}

APP_OPERATIONS = {
    "apps.list": (
        "GET",
        "/api/applications",
        ("apps", "list"),
        "bifrost_app_list",
    ),
    "apps.get": (
        "GET",
        "/api/applications/{slug}",
        ("apps", "get"),
        "bifrost_app_get",
    ),
    "apps.create": (
        "POST",
        "/api/applications",
        ("apps", "create"),
        "bifrost_app_create",
    ),
    "apps.update": (
        "PATCH",
        "/api/applications/{app_id}",
        ("apps", "update"),
        "bifrost_app_update",
    ),
    "apps.delete": (
        "DELETE",
        "/api/applications/{app_id}",
        ("apps", "delete"),
        "bifrost_app_delete",
    ),
    "apps.dependencies.get": (
        "GET",
        "/api/applications/{app_id}/dependencies",
        ("apps", "get-dependencies"),
        "bifrost_app_dependencies_get",
    ),
    "apps.dependencies.update": (
        "PUT",
        "/api/applications/{app_id}/dependencies",
        ("apps", "update-dependencies"),
        "bifrost_app_dependencies_update",
    ),
    "apps.validate": (
        "POST",
        "/api/applications/{app_id}/validate",
        ("apps", "validate"),
        "bifrost_app_validate",
    ),
    "apps.publish": (
        "POST",
        "/api/applications/{app_id}/publish",
        ("apps", "publish"),
        "bifrost_app_publish",
    ),
    "apps.replace": (
        "POST",
        "/api/applications/{app_id}/replace",
        ("apps", "replace"),
        "bifrost_app_replace",
    ),
}

WORKFLOW_OPERATIONS = {
    "workflows.list": (
        "GET",
        "/api/workflows",
        ("workflows", "list"),
        "bifrost_workflow_list",
    ),
    "workflows.validate": (
        "POST",
        "/api/workflows/validate",
        ("workflows", "validate"),
        "bifrost_workflow_validate",
    ),
    "workflows.register": (
        "POST",
        "/api/workflows/register",
        ("workflows", "register"),
        "bifrost_workflow_register",
    ),
    "workflows.execute": (
        "POST",
        "/api/workflows/execute",
        ("workflows", "execute"),
        "bifrost_workflow_execute",
    ),
    "workflows.update": (
        "PATCH",
        "/api/workflows/{workflow_id}",
        ("workflows", "update"),
        "bifrost_workflow_update",
    ),
    "workflows.delete": (
        "DELETE",
        "/api/workflows/{workflow_id}",
        ("workflows", "delete"),
        "bifrost_workflow_delete",
    ),
    "workflows.roles.grant": (
        "POST",
        "/api/workflows/{workflow_id}/roles",
        ("workflows", "grant-role"),
        "bifrost_workflow_role_grant",
    ),
    "workflows.roles.revoke": (
        "DELETE",
        "/api/workflows/{workflow_id}/roles/{role_id}",
        ("workflows", "revoke-role"),
        "bifrost_workflow_role_revoke",
    ),
}

EXECUTION_OPERATIONS = {
    "executions.list": (
        "GET",
        "/api/executions",
        ("workflows", "list-executions"),
        "bifrost_execution_list",
    ),
    "executions.get": (
        "GET",
        "/api/executions/{execution_id}",
        ("workflows", "get-execution"),
        "bifrost_execution_get",
    ),
}

KNOWLEDGE_OPERATIONS = {
    "knowledge.namespaces.list": (
        "GET",
        "/api/knowledge-sources",
        ("knowledge", "list-namespaces"),
        "bifrost_knowledge_namespace_list",
        True,
    ),
    "knowledge.documents.list": (
        "GET",
        "/api/knowledge-sources/documents",
        ("knowledge", "list-documents"),
        "bifrost_knowledge_document_list",
        True,
    ),
    "knowledge.documents.get": (
        "GET",
        "/api/knowledge-sources/{namespace}/documents/{doc_id}",
        ("knowledge", "get-document"),
        "bifrost_knowledge_document_get",
        True,
    ),
    "knowledge.documents.create": (
        "POST",
        "/api/knowledge-sources/{namespace}/documents",
        ("knowledge", "create-document"),
        "bifrost_knowledge_document_create",
        True,
    ),
    "knowledge.documents.update": (
        "PUT",
        "/api/knowledge-sources/{namespace}/documents/{doc_id}",
        ("knowledge", "update-document"),
        "bifrost_knowledge_document_update",
        True,
    ),
    "knowledge.documents.delete": (
        "DELETE",
        "/api/knowledge-sources/{namespace}/documents/{doc_id}",
        ("knowledge", "delete-document"),
        "bifrost_knowledge_document_delete",
        True,
    ),
}

PLATFORM_JOB_OPERATIONS = {
    "platform.jobs.get": (
        "GET",
        "/api/platform-jobs/{job_id}",
        ("platform-jobs", "get"),
        "bifrost_platform_job_get",
    ),
}

SOLUTION_OPERATIONS = {
    "solutions.list": (
        "GET",
        "/api/solutions",
        None,
        "bifrost_solution_list",
        True,
    ),
    "solutions.get": (
        "GET",
        "/api/solutions/{solution_id}",
        None,
        "bifrost_solution_get",
        True,
    ),
    "solutions.create": (
        "POST",
        "/api/solutions",
        ("solution", "create"),
        "bifrost_solution_create",
        True,
    ),
    "solutions.update": (
        "PATCH",
        "/api/solutions/{solution_id}",
        None,
        "bifrost_solution_update",
        True,
    ),
    "solutions.delete": (
        "DELETE",
        "/api/solutions/{solution_id}",
        None,
        "bifrost_solution_delete",
        True,
    ),
    "solutions.sync": (
        "POST",
        "/api/solutions/{solution_id}/sync",
        None,
        "bifrost_solution_sync",
        True,
    ),
    "solutions.export": (
        "POST",
        "/api/solutions/{solution_id}/export",
        ("solution", "export"),
        None,
        False,
    ),
    "solutions.deploy": (
        "POST",
        "/api/solutions/{solution_id}/deploy",
        ("solution", "deploy"),
        None,
        False,
    ),
    "solutions.install": (
        "POST",
        "/api/solutions/install",
        ("solution", "install"),
        None,
        False,
    ),
    "solutions.capture": (
        "POST",
        "/api/solutions/{solution_id}/capture",
        ("solution", "capture"),
        None,
        False,
    ),
}

ROLE_OPERATIONS = {
    "roles.list": (
        "GET",
        "/api/roles",
        ("roles", "list"),
        "bifrost_role_list",
    ),
    "roles.get": (
        "GET",
        "/api/roles/{role_id}",
        ("roles", "get"),
        "bifrost_role_get",
    ),
    "roles.create": (
        "POST",
        "/api/roles",
        ("roles", "create"),
        "bifrost_role_create",
    ),
    "roles.update": (
        "PATCH",
        "/api/roles/{role_id}",
        ("roles", "update"),
        "bifrost_role_update",
    ),
    "roles.delete": (
        "DELETE",
        "/api/roles/{role_id}",
        ("roles", "delete"),
        "bifrost_role_delete",
    ),
}

ROLE_USER_ASSIGNMENT_OPERATIONS = {
    "roles.users.list": (
        "GET",
        "/api/roles/{role_id}/users",
        None,
        None,
        False,
    ),
    "roles.users.assign": (
        "POST",
        "/api/roles/{role_id}/users",
        None,
        None,
        False,
    ),
    "roles.users.remove": (
        "DELETE",
        "/api/roles/{role_id}/users/{user_id}",
        None,
        None,
        False,
    ),
    "roles.users.bulk_remove": (
        "DELETE",
        "/api/roles/{role_id}/users",
        None,
        None,
        False,
    ),
}


USER_ADMIN_OPERATIONS = {
    "users.list": ("GET", "/api/users", None, None, False),
    "users.get": ("GET", "/api/users/{user_id}", None, None, False),
    "users.create": ("POST", "/api/users", None, None, False),
    "users.update": ("PATCH", "/api/users/{user_id}", None, None, False),
    "users.delete": ("DELETE", "/api/users/{user_id}", None, None, False),
    "users.bulk_update": ("PATCH", "/api/users/bulk", None, None, False),
    "users.invites.resend": (
        "POST",
        "/api/users/{user_id}/invite/resend",
        None,
        None,
        False,
    ),
    "users.invites.send": (
        "POST",
        "/api/users/{user_id}/invite/send",
        None,
        None,
        False,
    ),
    "users.invites.regenerate": (
        "POST",
        "/api/users/{user_id}/invite/regenerate",
        None,
        None,
        False,
    ),
    "users.invites.revoke": (
        "DELETE",
        "/api/users/{user_id}/invite",
        None,
        None,
        False,
    ),
    "users.roles.list": (
        "GET",
        "/api/users/{user_id}/roles",
        None,
        None,
        False,
    ),
    "users.forms.list": (
        "GET",
        "/api/users/{user_id}/forms",
        None,
        None,
        False,
    ),
}


ROLE_RESOURCE_ASSIGNMENT_OPERATIONS = {
    "roles.forms.list": (
        "GET",
        "/api/roles/{role_id}/forms",
        None,
        None,
        False,
    ),
    "roles.forms.assign": (
        "POST",
        "/api/roles/{role_id}/forms",
        None,
        None,
        False,
    ),
    "roles.forms.remove": (
        "DELETE",
        "/api/roles/{role_id}/forms/{form_id}",
        None,
        None,
        False,
    ),
    "roles.forms.bulk_remove": (
        "DELETE",
        "/api/roles/{role_id}/forms",
        None,
        None,
        False,
    ),
    "roles.agents.list": (
        "GET",
        "/api/roles/{role_id}/agents",
        None,
        None,
        False,
    ),
    "roles.agents.assign": (
        "POST",
        "/api/roles/{role_id}/agents",
        None,
        None,
        False,
    ),
    "roles.agents.remove": (
        "DELETE",
        "/api/roles/{role_id}/agents/{agent_id}",
        None,
        None,
        False,
    ),
    "roles.agents.bulk_remove": (
        "DELETE",
        "/api/roles/{role_id}/agents",
        None,
        None,
        False,
    ),
    "roles.apps.list": (
        "GET",
        "/api/roles/{role_id}/apps",
        None,
        None,
        False,
    ),
    "roles.apps.assign": (
        "POST",
        "/api/roles/{role_id}/apps",
        None,
        None,
        False,
    ),
    "roles.apps.bulk_remove": (
        "DELETE",
        "/api/roles/{role_id}/apps",
        None,
        None,
        False,
    ),
    "roles.workflows.list": (
        "GET",
        "/api/roles/{role_id}/workflows",
        None,
        None,
        False,
    ),
    "roles.workflows.assign": (
        "POST",
        "/api/roles/{role_id}/workflows",
        None,
        None,
        False,
    ),
    "roles.workflows.bulk_remove": (
        "DELETE",
        "/api/roles/{role_id}/workflows",
        None,
        None,
        False,
    ),
    "roles.knowledge.list": (
        "GET",
        "/api/roles/{role_id}/knowledge",
        None,
        None,
        False,
    ),
    "roles.knowledge.assign": (
        "POST",
        "/api/roles/{role_id}/knowledge",
        None,
        None,
        False,
    ),
    "roles.knowledge.bulk_remove": (
        "DELETE",
        "/api/roles/{role_id}/knowledge",
        None,
        None,
        False,
    ),
}

POLICY_RULE_OPERATIONS = {
    "policy.rules.list": (
        "GET",
        "/api/policy-rules",
        ("policy-rules", "list"),
        "bifrost_policy_rule_list",
    ),
    "policy.rules.create": (
        "POST",
        "/api/policy-rules",
        ("policy-rules", "create"),
        "bifrost_policy_rule_create",
    ),
    "policy.rules.update": (
        "PUT",
        "/api/policy-rules/{domain}/{name}",
        ("policy-rules", "update"),
        "bifrost_policy_rule_update",
    ),
    "policy.rules.delete": (
        "DELETE",
        "/api/policy-rules/{domain}/{name}",
        ("policy-rules", "delete"),
        "bifrost_policy_rule_delete",
    ),
    "policy.rules.list_usages": (
        "GET",
        "/api/policy-rules/{domain}/{name}/usages",
        ("policy-rules", "list-usages"),
        "bifrost_policy_rule_usage_list",
    ),
}

CONFIG_OPERATIONS = {
    "configs.list": (
        "GET",
        "/api/config",
        ("configs", "list"),
        "bifrost_config_list",
    ),
    "configs.create": (
        "POST",
        "/api/config",
        ("configs", "create"),
        "bifrost_config_create",
    ),
    "configs.update": (
        "PUT",
        "/api/config/{config_id}",
        ("configs", "update"),
        "bifrost_config_update",
    ),
    "configs.delete": (
        "DELETE",
        "/api/config/{config_id}",
        ("configs", "delete"),
        "bifrost_config_delete",
    ),
}

CLAIM_OPERATIONS = {
    "claims.list": (
        "GET",
        "/api/claims",
        ("claims", "list"),
        "bifrost_claim_list",
    ),
    "claims.get": (
        "GET",
        "/api/claims/{name}",
        ("claims", "get"),
        "bifrost_claim_get",
    ),
    "claims.create": (
        "POST",
        "/api/claims",
        ("claims", "create"),
        "bifrost_claim_create",
    ),
    "claims.update": (
        "PATCH",
        "/api/claims/{name}",
        ("claims", "update"),
        "bifrost_claim_update",
    ),
    "claims.delete": (
        "DELETE",
        "/api/claims/{name}",
        ("claims", "delete"),
        "bifrost_claim_delete",
    ),
}

FILE_POLICY_OPERATIONS = {
    "files.policies.list": (
        "GET",
        "/api/files/policies",
        ("files", "policies", "list"),
        "bifrost_file_policy_list",
    ),
    "files.policies.get": (
        "GET",
        "/api/files/policies/{policy_path}",
        ("files", "policies", "get"),
        "bifrost_file_policy_get",
    ),
    "files.policies.set": (
        "PUT",
        "/api/files/policies/{policy_path}",
        ("files", "policies", "set"),
        "bifrost_file_policy_set",
    ),
    "files.policies.delete": (
        "DELETE",
        "/api/files/policies/{policy_path}",
        ("files", "policies", "delete"),
        "bifrost_file_policy_delete",
    ),
    "files.policies.test": (
        "POST",
        "/api/files/policies/test",
        None,
        None,
        False,
    ),
    "files.structure.list": (
        "POST",
        "/api/files/structure",
        None,
        None,
        False,
    ),
}

EVENT_OPERATIONS = {
    "events.sources.list": (
        "GET",
        "/api/events/sources",
        ("events", "list-sources"),
        "bifrost_event_source_list",
    ),
    "events.sources.get": (
        "GET",
        "/api/events/sources/{source_id}",
        ("events", "get-source"),
        "bifrost_event_source_get",
    ),
    "events.sources.create": (
        "POST",
        "/api/events/sources",
        ("events", "create-source"),
        "bifrost_event_source_create",
    ),
    "events.sources.update": (
        "PATCH",
        "/api/events/sources/{source_id}",
        ("events", "update-source"),
        "bifrost_event_source_update",
    ),
    "events.sources.delete": (
        "DELETE",
        "/api/events/sources/{source_id}",
        ("events", "delete-source"),
        "bifrost_event_source_delete",
    ),
    "events.subscriptions.list": (
        "GET",
        "/api/events/sources/{source_id}/subscriptions",
        ("events", "list-subscriptions"),
        "bifrost_event_subscription_list",
    ),
    "events.subscriptions.create": (
        "POST",
        "/api/events/sources/{source_id}/subscriptions",
        ("events", "create-subscription"),
        "bifrost_event_subscription_create",
    ),
    "events.subscriptions.update": (
        "PATCH",
        "/api/events/sources/{source_id}/subscriptions/{subscription_id}",
        ("events", "update-subscription"),
        "bifrost_event_subscription_update",
    ),
    "events.subscriptions.delete": (
        "DELETE",
        "/api/events/sources/{source_id}/subscriptions/{subscription_id}",
        ("events", "delete-subscription"),
        "bifrost_event_subscription_delete",
    ),
    "events.webhook_adapters.list": (
        "GET",
        "/api/events/adapters",
        ("events", "list-webhook-adapters"),
        "bifrost_event_webhook_adapter_list",
    ),
}

ORGANIZATION_OPERATIONS = {
    "organizations.list": (
        "GET",
        "/api/organizations",
        ("organizations", "list"),
        "bifrost_organization_list",
    ),
    "organizations.get": (
        "GET",
        "/api/organizations/{org_id}",
        ("organizations", "get"),
        "bifrost_organization_get",
    ),
    "organizations.create": (
        "POST",
        "/api/organizations",
        ("organizations", "create"),
        "bifrost_organization_create",
    ),
    "organizations.update": (
        "PATCH",
        "/api/organizations/{org_id}",
        ("organizations", "update"),
        "bifrost_organization_update",
    ),
    "organizations.delete": (
        "DELETE",
        "/api/organizations/{org_id}",
        ("organizations", "delete"),
        "bifrost_organization_delete",
    ),
}

INTEGRATION_OPERATIONS = {
    "integrations.list": (
        "GET",
        "/api/integrations",
        ("integrations", "list"),
        "bifrost_integration_list",
    ),
    "integrations.get": (
        "GET",
        "/api/integrations/{integration_id}",
        ("integrations", "get"),
        "bifrost_integration_get",
    ),
    "integrations.create": (
        "POST",
        "/api/integrations",
        ("integrations", "create"),
        "bifrost_integration_create",
    ),
    "integrations.update": (
        "PUT",
        "/api/integrations/{integration_id}",
        ("integrations", "update"),
        "bifrost_integration_update",
    ),
    "integrations.delete": (
        "DELETE",
        "/api/integrations/{integration_id}",
        None,
        None,
        False,
    ),
    "integrations.mappings.list": (
        "GET",
        "/api/integrations/{integration_id}/mappings",
        None,
        None,
        False,
    ),
    "integrations.mappings.get": (
        "GET",
        "/api/integrations/{integration_id}/mappings/{mapping_id}",
        None,
        None,
        False,
    ),
    "integrations.mappings.get_by_org": (
        "GET",
        "/api/integrations/{integration_id}/mappings/by-org/{org_id}",
        None,
        None,
        False,
    ),
    "integrations.mappings.create": (
        "POST",
        "/api/integrations/{integration_id}/mappings",
        ("integrations", "create-mapping"),
        "bifrost_integration_mapping_create",
    ),
    "integrations.mappings.update": (
        "PUT",
        "/api/integrations/{integration_id}/mappings/{mapping_id}",
        ("integrations", "update-mapping"),
        "bifrost_integration_mapping_update",
    ),
    "integrations.config.get": (
        "GET",
        "/api/integrations/{integration_id}/config",
        None,
        None,
        False,
    ),
    "integrations.config.update": (
        "PUT",
        "/api/integrations/{integration_id}/config",
        None,
        None,
        False,
    ),
    "integrations.mappings.batch": (
        "POST",
        "/api/integrations/{integration_id}/mappings/batch",
        None,
        None,
        False,
    ),
    "integrations.mappings.delete": (
        "DELETE",
        "/api/integrations/{integration_id}/mappings/{mapping_id}",
        None,
        None,
        False,
    ),
    "integrations.mappings.authorize": (
        "POST",
        "/api/integrations/{integration_id}/mappings/{mapping_id}/oauth/authorize",
        None,
        None,
        False,
    ),
    "integrations.mappings.disconnect": (
        "POST",
        "/api/integrations/{integration_id}/mappings/{mapping_id}/oauth/disconnect",
        None,
        None,
        False,
    ),
    "integrations.mappings.refresh": (
        "POST",
        "/api/integrations/{integration_id}/mappings/{mapping_id}/oauth/refresh",
        None,
        None,
        False,
    ),
    "integrations.oauth.get": (
        "GET",
        "/api/integrations/{integration_id}/oauth",
        None,
        None,
        False,
    ),
    "integrations.oauth.authorize": (
        "GET",
        "/api/integrations/{integration_id}/oauth/authorize",
        None,
        None,
        False,
    ),
    "integrations.oauth.entity_id_source.update": (
        "PATCH",
        "/api/integrations/{integration_id}/oauth/entity_id_source",
        None,
        None,
        False,
    ),
    "integrations.oauth.entity_id_source.delete": (
        "DELETE",
        "/api/integrations/{integration_id}/oauth/entity_id_source",
        None,
        None,
        False,
    ),
    "integrations.test": (
        "POST",
        "/api/integrations/{integration_id}/test",
        None,
        None,
        False,
    ),
    "integrations.generate_sdk": (
        "POST",
        "/api/integrations/{integration_id}/generate-sdk",
        None,
        None,
        False,
    ),
}

WORKSPACE_FILE_OPERATIONS = {
    "workspace.files.list": (
        "POST",
        "/api/files/list",
        ("files", "list"),
        "bifrost_file_list",
    ),
    "workspace.files.search": (
        "POST",
        "/api/files/search",
        ("files", "search"),
        "bifrost_file_search",
    ),
    "workspace.files.read": (
        "POST",
        "/api/files/read",
        ("files", "read"),
        "bifrost_file_read",
    ),
    "workspace.files.stat": (
        "POST",
        "/api/files/stat",
        ("files", "stat"),
        "bifrost_file_stat",
    ),
    "workspace.files.exists": (
        "POST",
        "/api/files/exists",
        ("files", "exists"),
        "bifrost_file_exists",
    ),
    "workspace.files.write": (
        "POST",
        "/api/files/write",
        ("files", "write"),
        "bifrost_file_write",
    ),
    "workspace.files.delete": (
        "POST",
        "/api/files/delete",
        ("files", "delete"),
        "bifrost_file_delete",
    ),
    "workspace.files.pull": (
        "POST",
        "/api/files/pull",
        None,
        None,
        False,
    ),
    "workspace.files.manifest": (
        "GET",
        "/api/files/manifest",
        None,
        None,
        False,
    ),
    "workspace.files.watch": (
        "POST",
        "/api/files/watch",
        None,
        None,
        False,
    ),
    "workspace.files.watchers": (
        "GET",
        "/api/files/watchers",
        None,
        None,
        False,
    ),
    "workspace.files.editor.list": (
        "GET",
        "/api/files/editor",
        None,
        None,
        False,
    ),
    "workspace.files.editor.read": (
        "GET",
        "/api/files/editor/content",
        None,
        None,
        False,
    ),
    "workspace.files.editor.write": (
        "PUT",
        "/api/files/editor/content",
        None,
        None,
        False,
    ),
    "workspace.files.editor.folder.create": (
        "POST",
        "/api/files/editor/folder",
        None,
        None,
        False,
    ),
    "workspace.files.editor.delete": (
        "DELETE",
        "/api/files/editor",
        None,
        None,
        False,
    ),
    "workspace.files.editor.rename": (
        "POST",
        "/api/files/editor/rename",
        None,
        None,
        False,
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
    **ROLE_USER_ASSIGNMENT_OPERATIONS,
    **USER_ADMIN_OPERATIONS,
    **CLAIM_OPERATIONS,
    **CONFIG_OPERATIONS,
    **POLICY_RULE_OPERATIONS,
    **FILE_POLICY_OPERATIONS,
    **EVENT_OPERATIONS,
    **ORGANIZATION_OPERATIONS,
    **INTEGRATION_OPERATIONS,
    **WORKSPACE_FILE_OPERATIONS,
    **PLATFORM_JOB_OPERATIONS,
    **SOLUTION_OPERATIONS,
    **ROLE_RESOURCE_ASSIGNMENT_OPERATIONS,
}


def _expanded_binding(binding: tuple) -> tuple:
    """Add the optional native-Builder expectation used by newer surfaces."""

    if len(binding) == 4:
        return (*binding, None)
    return binding


def test_canonical_vertical_slices_have_stable_surface_bindings() -> None:
    assert {operation.operation_id for operation in OPERATION_CATALOG} == set(
        CANONICAL_OPERATIONS
    )
    for operation_id, binding in CANONICAL_OPERATIONS.items():
        method, path, cli_path, mcp_name, native_builder = _expanded_binding(binding)
        operation = get_operation(operation_id)
        assert (operation.rest.method, operation.rest.path) == (method, path)
        if cli_path is None:
            assert operation.cli is None
        else:
            assert operation.cli is not None and operation.cli.path == cli_path
        if mcp_name is None:
            assert operation.mcp is None
        else:
            assert operation.mcp is not None and operation.mcp.name == mcp_name
        if native_builder is not None:
            assert operation.native_builder is native_builder


def test_catalog_routes_publish_identity_in_openapi() -> None:
    schema = app.openapi()
    for operation_id, binding in CANONICAL_OPERATIONS.items():
        expanded = _expanded_binding(binding)
        method, path = expanded[:2]
        cli_path, mcp_name = expanded[2:4]
        route = schema["paths"][path][method.lower()]
        assert route["operationId"] == operation_id
        extension = route["x-bifrost-operation"]
        assert extension["id"] == operation_id
        assert extension.get("cli") == (
            list(cli_path) if cli_path is not None else None
        )
        assert extension.get("mcp") == mcp_name


def test_catalog_rejects_duplicate_operation_ids() -> None:
    duplicate = OperationDefinition.model_validate(
        deepcopy(OPERATION_CATALOG[0].model_dump())
    )
    with pytest.raises(ValueError, match="duplicate operation ID"):
        validate_operation_catalog((*OPERATION_CATALOG, duplicate))


def _with_mcp_name(operation: OperationDefinition, name: str) -> OperationDefinition:
    """Return a copy of ``operation`` with a different ``operation_id``/MCP name.

    A distinct ``operation_id`` avoids tripping the duplicate-operation-ID
    check, which would otherwise mask the MCP-naming assertion under test.
    """
    dumped = deepcopy(operation.model_dump())
    dumped["operation_id"] = f"{dumped['operation_id']}.test_variant"
    dumped["mcp"]["name"] = name
    return OperationDefinition.model_validate(dumped)


def test_catalog_rejects_verb_first_mcp_names() -> None:
    """MCP names are noun-first, verb-last — ``bifrost_list_agents`` is rejected."""
    bad = _with_mcp_name(get_operation("agents.list"), "bifrost_list_agents")
    with pytest.raises(ValueError, match="bifrost_list_agents"):
        validate_operation_catalog((*OPERATION_CATALOG, bad))


def test_catalog_rejects_mcp_names_with_unknown_trailing_verb() -> None:
    bad = _with_mcp_name(get_operation("agents.list"), "bifrost_agent_frobnicate")
    with pytest.raises(ValueError, match="frobnicate"):
        validate_operation_catalog((*OPERATION_CATALOG, bad))


def test_catalog_rejects_mcp_names_over_length_limit() -> None:
    bad = _with_mcp_name(get_operation("agents.list"), "bifrost_" + "a" * 60 + "_list")
    with pytest.raises(ValueError, match="does not match"):
        validate_operation_catalog((*OPERATION_CATALOG, bad))


def test_canonical_catalog_mcp_names_are_all_valid() -> None:
    """No rewiring needed — the real catalog already satisfies its own rule."""
    validate_operation_catalog(OPERATION_CATALOG)


# Domains whose registered MCP tool ids are already reconciled with the
# catalog's ``bifrost_<noun>..._<verb>`` names (RBAC R1b: agents; later
# batches add their domain here as each is migrated to a thin wrapper).
CANONICAL_MCP_DOMAINS = {"agents"}


def test_registered_agent_mcp_tool_ids_match_the_catalog() -> None:
    """Every registered Agent MCP tool id equals its catalog MCP name.

    Reads the actually-registered tool ids from the ``agents`` tool module's
    ``TOOLS`` list (the same list ``register_tools`` feeds into FastMCP),
    not just the catalog metadata — this is the tripwire that would catch a
    tool module rename drifting from the catalog rename.
    """
    from src.services.mcp_server.tools import agents as agents_mod

    registered_ids = {tool_id for tool_id, _name, _description in agents_mod.TOOLS}
    catalog_names = {
        operation.mcp.name
        for operation in OPERATION_CATALOG
        if operation.operation_id.split(".")[0] in CANONICAL_MCP_DOMAINS
        and operation.mcp is not None
    }
    assert registered_ids == catalog_names


