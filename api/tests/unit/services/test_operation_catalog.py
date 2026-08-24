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
    "knowledge.namespaces.list": (
        "GET",
        "/api/knowledge-sources",
        ("knowledge", "list-namespaces"),
        "bifrost_list_knowledge_namespaces",
        True,
    ),
    "knowledge.documents.list": (
        "GET",
        "/api/knowledge-sources/documents",
        ("knowledge", "list-documents"),
        "bifrost_list_knowledge_documents",
        True,
    ),
    "knowledge.documents.get": (
        "GET",
        "/api/knowledge-sources/{namespace}/documents/{doc_id}",
        ("knowledge", "get-document"),
        "bifrost_get_knowledge_document",
        True,
    ),
    "knowledge.documents.create": (
        "POST",
        "/api/knowledge-sources/{namespace}/documents",
        ("knowledge", "create-document"),
        "bifrost_create_knowledge_document",
        True,
    ),
    "knowledge.documents.update": (
        "PUT",
        "/api/knowledge-sources/{namespace}/documents/{doc_id}",
        ("knowledge", "update-document"),
        "bifrost_update_knowledge_document",
        True,
    ),
    "knowledge.documents.delete": (
        "DELETE",
        "/api/knowledge-sources/{namespace}/documents/{doc_id}",
        ("knowledge", "delete-document"),
        "bifrost_delete_knowledge_document",
        True,
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

SOLUTION_OPERATIONS = {
    "solutions.list": (
        "GET",
        "/api/solutions",
        None,
        "bifrost_list_solutions",
        True,
    ),
    "solutions.get": (
        "GET",
        "/api/solutions/{solution_id}",
        None,
        "bifrost_get_solution",
        True,
    ),
    "solutions.create": (
        "POST",
        "/api/solutions",
        ("solution", "create"),
        "bifrost_create_solution",
        True,
    ),
    "solutions.update": (
        "PATCH",
        "/api/solutions/{solution_id}",
        None,
        "bifrost_update_solution",
        True,
    ),
    "solutions.delete": (
        "DELETE",
        "/api/solutions/{solution_id}",
        None,
        "bifrost_delete_solution",
        True,
    ),
    "solutions.sync": (
        "POST",
        "/api/solutions/{solution_id}/sync",
        None,
        "bifrost_sync_solution",
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
        "bifrost_create_integration_mapping",
    ),
    "integrations.mappings.update": (
        "PUT",
        "/api/integrations/{integration_id}/mappings/{mapping_id}",
        ("integrations", "update-mapping"),
        "bifrost_update_integration_mapping",
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


def test_mcp_registration_uses_only_catalog_names() -> None:
    from src.services.mcp_server.server import (
        get_system_tool_function,
        get_system_tools,
    )

    registered = {tool["id"] for tool in get_system_tools()}
    catalog_names = {
        binding[3] for binding in CANONICAL_OPERATIONS.values() if binding[3]
    }
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
