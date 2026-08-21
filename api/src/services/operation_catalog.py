"""Canonical Bifrost operation identities and transport bindings."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from shared.authorization_scopes import get_authorization_scope, is_valid_scope_key
from src.models.contracts.operation_catalog import (
    CliOperationBinding,
    ManifestOperationBinding,
    McpOperationBinding,
    OperationAsyncPolicy,
    OperationDefinition,
    OperationTargetKind,
    RestOperationBinding,
)


_AGENT_SDK_EXCLUSION = "Agent administration is not available to application SDKs."
_FORM_SDK_EXCLUSION = "Form administration is not available to application SDKs."
_TABLE_SDK_EXCLUSION = (
    "Table metadata administration is not available to application SDKs; "
    "SDK table methods operate on documents."
)
_APP_SDK_EXCLUSION = (
    "Application administration is not available to the in-app runtime SDK."
)
_EVENT_SDK_EXCLUSION = (
    "Event source and subscription administration is not available to application SDKs."
)
_WORKFLOW_SDK_EXCLUSION = (
    "Workflow catalog administration is not available to application SDKs."
)
_SOLUTION_SDK_EXCLUSION = (
    "Solution lifecycle administration is not available to application SDKs."
)
_SOLUTION_MANIFEST_EXCLUSION = (
    "A Solution bundle contains manifests; the installed Solution is not itself "
    "a workspace manifest entity."
)
_SOLUTION_ARTIFACT_MCP_EXCLUSION = (
    "Multipart Solution archive transfer will enter MCP through the canonical "
    "ArtifactRef contract rather than a transport-specific byte parameter."
)
_ORGANIZATION_SDK_EXCLUSION = (
    "Organization administration is not available to application SDKs."
)
_ORGANIZATION_BUILDER_EXCLUSION = (
    "Organization lifecycle belongs to platform Settings, not a coding target."
)
_INTEGRATION_SDK_EXCLUSION = "Integration administration is separate from the application SDK's runtime mapping lookup."
_ROLE_SDK_EXCLUSION = "Role administration is not available to application SDKs."
_USER_ADMIN_SURFACE_EXCLUSIONS = {
    "cli": "User lifecycle administration is available through the administrative UI and REST API, not the coding CLI.",
    "mcp": "User lifecycle administration is intentionally excluded from coding-harness MCP tools.",
    "native_builder": "User lifecycle administration is not a coding target.",
    "manifest": "Users and their Role assignments are deployment-local identities, not portable manifest content.",
    "sdk": "User lifecycle administration is not available to application SDKs.",
}
_ROLE_ASSIGNMENT_SURFACE_EXCLUSIONS = {
    "cli": "Role assignment administration is available through the administrative UI and REST API.",
    "mcp": "Role assignment administration is intentionally excluded from coding-harness MCP tools.",
    "native_builder": "Role assignment administration is not a coding target.",
    "manifest": "Role assignments and their boundaries are deployment-local authorization state.",
    "sdk": _ROLE_SDK_EXCLUSION,
}
_ROLE_RESOURCE_ASSIGNMENT_SURFACE_EXCLUSIONS = {
    **_ROLE_ASSIGNMENT_SURFACE_EXCLUSIONS,
    "cli": "Role resource-assignment administration is available through the administrative UI and REST API.",
    "mcp": "Role resource-assignment administration is intentionally excluded from coding-harness MCP tools.",
    "native_builder": "Role resource-assignment administration is not a coding target.",
    "manifest": "Role resource assignments are deployment-local authorization state.",
    "sdk": _ROLE_SDK_EXCLUSION,
}
_PLATFORM_JOB_SDK_EXCLUSION = "Platform-job status is a platform operation surface, not an application SDK binding."
_CLAIM_SDK_EXCLUSION = (
    "Custom Claim administration is not available to application SDKs."
)
_FILE_POLICY_SDK_EXCLUSION = (
    "File policy administration is not available to application SDKs; "
    "SDK file methods operate on content, not access rules."
)
_FILE_POLICY_MANIFEST_EXCLUSION = (
    "File policies are workspace/org administration, not a portable manifest entity."
)
_CONFIG_SDK_EXCLUSION = (
    "Config administration is separate from the application SDK's runtime value "
    "lookup (bifrost.config.get resolves a value by key with cascade)."
)
_POLICY_RULE_SDK_EXCLUSION = (
    "Policy rule administration is not available to application SDKs; SDK table "
    "and file methods operate under the access decisions these rules produce."
)
_POLICY_RULE_MANIFEST_EXCLUSION = (
    "Policy rules are workspace/org access administration, not a portable "
    "manifest entity."
)
_CONFIG_MANIFEST_EXCLUSION = (
    "Config values are serialized into .bifrost/configs.yaml for export, but git "
    "sync never reconciles them back: github_sync.py has no _resolve_config and "
    "writes no Config row. Declaring a manifest binding would assert a "
    "round-trip that does not exist "
    "(docs/plans/2026-08-18-manifest-binding-export-only-gap.md)."
)


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
        action_scopes=("agents.readwrite",),
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
        action_scopes=("agents.readwrite",),
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
        action_scopes=("agents.readwrite",),
        authorization_resolver="Agent ownership and Solution-management guards",
        audit_event="agent.delete",
        side_effects=(
            "delete Agent relation grants through database cascades",
            "remove manifest entry through RepoSyncWriter when applicable",
        ),
        exclusions={"sdk": _AGENT_SDK_EXCLUSION},
    ),
    OperationDefinition(
        operation_id="forms.list",
        summary="List Forms visible to the caller",
        target_kind=OperationTargetKind.COLLECTION,
        rest=RestOperationBinding(
            method="GET",
            path="/api/forms",
            response_model="list[FormPublic]",
        ),
        cli=CliOperationBinding(path=("forms", "list")),
        mcp=McpOperationBinding(name="bifrost_list_forms"),
        native_builder=True,
        action_scopes=("forms.read",),
        authorization_resolver="FormRepository.list_forms",
        exclusions={
            "manifest": "Manifests reconcile Form state; they do not perform collection reads.",
            "sdk": _FORM_SDK_EXCLUSION,
        },
    ),
    OperationDefinition(
        operation_id="forms.get",
        summary="Get one Form visible to the caller",
        target_kind=OperationTargetKind.RESOURCE,
        rest=RestOperationBinding(
            method="GET",
            path="/api/forms/{form_id}",
            response_model="FormPublic",
        ),
        cli=CliOperationBinding(path=("forms", "get")),
        mcp=McpOperationBinding(name="bifrost_get_form"),
        native_builder=True,
        action_scopes=("forms.read",),
        authorization_resolver="FormRepository plus form access policy",
        exclusions={
            "manifest": "Manifests reconcile Form state; they do not perform resource reads.",
            "sdk": _FORM_SDK_EXCLUSION,
        },
    ),
    OperationDefinition(
        operation_id="forms.create",
        summary="Create a Form in an allowed target",
        target_kind=OperationTargetKind.COLLECTION,
        rest=RestOperationBinding(
            method="POST",
            path="/api/forms",
            request_model="FormCreate",
            response_model="FormPublic",
        ),
        cli=CliOperationBinding(path=("forms", "create")),
        mcp=McpOperationBinding(name="bifrost_create_form"),
        native_builder=True,
        manifest=ManifestOperationBinding(entity="forms"),
        action_scopes=("forms.readwrite",),
        authorization_resolver="Platform-admin gate and target organization resolver",
        audit_event="form.create",
        side_effects=(
            "persist Form fields and role assignments",
            "synchronize Form roles to referenced workflows",
            "invalidate Form caches",
            "write manifest change through RepoSyncWriter when applicable",
        ),
        exclusions={"sdk": _FORM_SDK_EXCLUSION},
    ),
    OperationDefinition(
        operation_id="forms.update",
        summary="Update a Form the caller may manage",
        target_kind=OperationTargetKind.RESOURCE,
        rest=RestOperationBinding(
            method="PATCH",
            path="/api/forms/{form_id}",
            request_model="FormUpdate",
            response_model="FormPublic",
        ),
        cli=CliOperationBinding(path=("forms", "update")),
        mcp=McpOperationBinding(name="bifrost_update_form"),
        native_builder=True,
        manifest=ManifestOperationBinding(entity="forms"),
        action_scopes=("forms.readwrite",),
        authorization_resolver="Platform-admin and Solution-management guards",
        audit_event="form.update",
        side_effects=(
            "replace selected Form fields and role assignments",
            "synchronize Form roles to referenced workflows",
            "invalidate Form caches",
            "write manifest change through RepoSyncWriter when applicable",
        ),
        exclusions={"sdk": _FORM_SDK_EXCLUSION},
    ),
    OperationDefinition(
        operation_id="forms.delete",
        summary="Deactivate or purge a Form the caller may manage",
        target_kind=OperationTargetKind.RESOURCE,
        rest=RestOperationBinding(
            method="DELETE",
            path="/api/forms/{form_id}",
        ),
        cli=CliOperationBinding(path=("forms", "delete")),
        mcp=McpOperationBinding(name="bifrost_delete_form"),
        native_builder=True,
        manifest=ManifestOperationBinding(entity="forms", behavior="remove"),
        action_scopes=("forms.readwrite",),
        authorization_resolver="Platform-admin and Solution-management guards",
        audit_event="form.delete",
        side_effects=(
            "deactivate the Form or purge its persisted relations",
            "invalidate Form caches",
            "remove the active manifest entry through RepoSyncWriter",
        ),
        exclusions={"sdk": _FORM_SDK_EXCLUSION},
    ),
    OperationDefinition(
        operation_id="tables.list",
        summary="List Tables visible to the caller",
        target_kind=OperationTargetKind.COLLECTION,
        rest=RestOperationBinding(
            method="GET",
            path="/api/tables",
            response_model="TableListResponse",
        ),
        cli=CliOperationBinding(path=("tables", "list")),
        mcp=McpOperationBinding(name="bifrost_list_tables"),
        native_builder=True,
        action_scopes=("tables.read",),
        authorization_resolver="events.read and selected collection boundary",
        exclusions={
            "manifest": "Manifests reconcile Table state; they do not perform collection reads.",
            "sdk": _TABLE_SDK_EXCLUSION,
        },
    ),
    OperationDefinition(
        operation_id="tables.get",
        summary="Get one Table visible to the caller",
        target_kind=OperationTargetKind.RESOURCE,
        rest=RestOperationBinding(
            method="GET",
            path="/api/tables/{table_id}",
            response_model="TablePublic",
        ),
        cli=CliOperationBinding(path=("tables", "get")),
        mcp=McpOperationBinding(name="bifrost_get_table"),
        native_builder=True,
        action_scopes=("tables.read",),
        authorization_resolver="Table repository access in the selected boundary",
        exclusions={
            "manifest": "Manifests reconcile Table state; they do not perform resource reads.",
            "sdk": _TABLE_SDK_EXCLUSION,
        },
    ),
    OperationDefinition(
        operation_id="tables.create",
        summary="Create a Table in an allowed target",
        target_kind=OperationTargetKind.COLLECTION,
        rest=RestOperationBinding(
            method="POST",
            path="/api/tables",
            request_model="TableCreate",
            response_model="TablePublic",
        ),
        cli=CliOperationBinding(path=("tables", "create")),
        mcp=McpOperationBinding(name="bifrost_create_table"),
        native_builder=True,
        manifest=ManifestOperationBinding(entity="tables"),
        action_scopes=("tables.readwrite",),
        authorization_resolver="Platform-admin gate and target organization resolver",
        audit_event="table.create",
        side_effects=(
            "persist Table schema and row-access policies",
            "write manifest change through RepoSyncWriter",
        ),
        exclusions={"sdk": _TABLE_SDK_EXCLUSION},
    ),
    OperationDefinition(
        operation_id="tables.update",
        summary="Update a Table the caller may manage",
        target_kind=OperationTargetKind.RESOURCE,
        rest=RestOperationBinding(
            method="PATCH",
            path="/api/tables/{table_id}",
            request_model="TableUpdate",
            response_model="TablePublic",
        ),
        cli=CliOperationBinding(path=("tables", "update")),
        mcp=McpOperationBinding(name="bifrost_update_table"),
        native_builder=True,
        manifest=ManifestOperationBinding(entity="tables"),
        action_scopes=("tables.readwrite",),
        authorization_resolver="Platform-admin and Solution-management guards",
        audit_event="table.update",
        side_effects=(
            "replace selected Table metadata and row-access policies",
            "publish policy changes to connected runtimes",
            "write manifest change through RepoSyncWriter",
        ),
        exclusions={"sdk": _TABLE_SDK_EXCLUSION},
    ),
    OperationDefinition(
        operation_id="tables.delete",
        summary="Delete a Table the caller may manage",
        target_kind=OperationTargetKind.RESOURCE,
        rest=RestOperationBinding(
            method="DELETE",
            path="/api/tables/{table_id}",
        ),
        cli=CliOperationBinding(path=("tables", "delete")),
        mcp=McpOperationBinding(name="bifrost_delete_table"),
        native_builder=True,
        manifest=ManifestOperationBinding(entity="tables", behavior="remove"),
        action_scopes=("tables.readwrite",),
        authorization_resolver="Platform-admin and Solution-management guards",
        audit_event="table.delete",
        side_effects=(
            "delete the Table and its documents through database cascades",
            "remove the manifest entry through RepoSyncWriter",
        ),
        exclusions={"sdk": _TABLE_SDK_EXCLUSION},
    ),
    OperationDefinition(
        operation_id="apps.list",
        summary="List Applications visible to the caller",
        target_kind=OperationTargetKind.COLLECTION,
        rest=RestOperationBinding(
            method="GET",
            path="/api/applications",
            response_model="ApplicationListResponse",
        ),
        cli=CliOperationBinding(path=("apps", "list")),
        mcp=McpOperationBinding(name="bifrost_list_apps"),
        native_builder=True,
        action_scopes=("apps.read",),
        authorization_resolver="ApplicationRepository.list_applications",
        exclusions={
            "manifest": "Manifests reconcile Application state; they do not perform collection reads.",
            "sdk": _APP_SDK_EXCLUSION,
        },
    ),
    OperationDefinition(
        operation_id="apps.get",
        summary="Get one Application visible to the caller",
        target_kind=OperationTargetKind.RESOURCE,
        rest=RestOperationBinding(
            method="GET",
            path="/api/applications/{slug}",
            response_model="ApplicationPublic",
        ),
        cli=CliOperationBinding(path=("apps", "get")),
        mcp=McpOperationBinding(name="bifrost_get_app"),
        native_builder=True,
        action_scopes=("apps.read",),
        authorization_resolver="ApplicationRepository plus role and Solution visibility",
        exclusions={
            "manifest": "Manifests reconcile Application state; they do not perform resource reads.",
            "sdk": _APP_SDK_EXCLUSION,
        },
    ),
    OperationDefinition(
        operation_id="apps.create",
        summary="Create a loose Application in an allowed target",
        target_kind=OperationTargetKind.COLLECTION,
        rest=RestOperationBinding(
            method="POST",
            path="/api/applications",
            request_model="ApplicationCreate",
            response_model="ApplicationPublic",
        ),
        cli=CliOperationBinding(path=("apps", "create")),
        mcp=McpOperationBinding(name="bifrost_create_app"),
        native_builder=True,
        manifest=ManifestOperationBinding(entity="apps"),
        action_scopes=("apps.readwrite",),
        authorization_resolver="Application create and target-organization policy",
        audit_event="app.create",
        side_effects=(
            "persist Application metadata and role grants",
            "scaffold loose inline-v1 source when the target path is empty",
            "write manifest change through RepoSyncWriter",
        ),
        exclusions={"sdk": _APP_SDK_EXCLUSION},
    ),
    OperationDefinition(
        operation_id="apps.update",
        summary="Update an Application the caller may manage",
        target_kind=OperationTargetKind.RESOURCE,
        rest=RestOperationBinding(
            method="PATCH",
            path="/api/applications/{app_id}",
            request_model="ApplicationUpdate",
            response_model="ApplicationPublic",
        ),
        cli=CliOperationBinding(path=("apps", "update")),
        mcp=McpOperationBinding(name="bifrost_update_app"),
        native_builder=True,
        manifest=ManifestOperationBinding(entity="apps"),
        action_scopes=("apps.readwrite",),
        authorization_resolver="Application management and Solution-management guards",
        audit_event="app.update",
        side_effects=(
            "replace selected metadata and role grants",
            "publish Application draft metadata updates",
            "write manifest change through RepoSyncWriter",
        ),
        exclusions={"sdk": _APP_SDK_EXCLUSION},
    ),
    OperationDefinition(
        operation_id="apps.delete",
        summary="Delete an Application the caller may manage",
        target_kind=OperationTargetKind.RESOURCE,
        rest=RestOperationBinding(
            method="DELETE",
            path="/api/applications/{app_id}",
        ),
        cli=CliOperationBinding(path=("apps", "delete")),
        mcp=McpOperationBinding(name="bifrost_delete_app"),
        native_builder=True,
        manifest=ManifestOperationBinding(entity="apps", behavior="remove"),
        action_scopes=("apps.readwrite",),
        authorization_resolver="Application management and Solution-management guards",
        audit_event="app.delete",
        side_effects=(
            "delete Application metadata and relation grants",
            "remove the manifest entry through RepoSyncWriter",
        ),
        exclusions={"sdk": _APP_SDK_EXCLUSION},
    ),
    OperationDefinition(
        operation_id="apps.dependencies.get",
        summary="Get an Application's npm dependencies",
        target_kind=OperationTargetKind.RESOURCE,
        rest=RestOperationBinding(
            method="GET",
            path="/api/applications/{app_id}/dependencies",
            response_model="dict[str, str]",
        ),
        cli=CliOperationBinding(path=("apps", "get-dependencies")),
        mcp=McpOperationBinding(name="bifrost_get_app_dependencies"),
        native_builder=True,
        action_scopes=("apps.read",),
        authorization_resolver="ApplicationRepository access check",
        exclusions={
            "manifest": "The read does not mutate the Application manifest.",
            "sdk": _APP_SDK_EXCLUSION,
        },
    ),
    OperationDefinition(
        operation_id="apps.dependencies.update",
        summary="Replace an Application's npm dependencies",
        target_kind=OperationTargetKind.RESOURCE,
        rest=RestOperationBinding(
            method="PUT",
            path="/api/applications/{app_id}/dependencies",
            request_model="dict[str, str]",
            response_model="dict[str, str]",
        ),
        cli=CliOperationBinding(path=("apps", "update-dependencies")),
        mcp=McpOperationBinding(name="bifrost_update_app_dependencies"),
        native_builder=True,
        manifest=ManifestOperationBinding(entity="apps"),
        action_scopes=("apps.readwrite",),
        authorization_resolver="Application management and Solution-management guards",
        audit_event="app.dependencies.update",
        side_effects=(
            "replace dependency metadata",
            "invalidate the Application render cache",
            "write manifest change through RepoSyncWriter",
        ),
        exclusions={"sdk": _APP_SDK_EXCLUSION},
    ),
    OperationDefinition(
        operation_id="apps.validate",
        summary="Compile and statically validate Application source",
        target_kind=OperationTargetKind.RESOURCE,
        rest=RestOperationBinding(
            method="POST",
            path="/api/applications/{app_id}/validate",
            response_model="AppValidationResponse",
        ),
        cli=CliOperationBinding(path=("apps", "validate")),
        mcp=McpOperationBinding(name="bifrost_validate_app"),
        native_builder=True,
        action_scopes=("apps.read",),
        authorization_resolver="ApplicationRepository access check",
        exclusions={
            "manifest": "Validation does not mutate the Application manifest.",
            "sdk": _APP_SDK_EXCLUSION,
        },
    ),
    OperationDefinition(
        operation_id="apps.publish",
        summary="Queue a durable build and publish for a loose Application",
        target_kind=OperationTargetKind.RESOURCE,
        rest=RestOperationBinding(
            method="POST",
            path="/api/applications/{app_id}/publish",
            request_model="ApplicationPublishRequest",
            response_model="PlatformJobAccepted",
        ),
        cli=CliOperationBinding(path=("apps", "publish")),
        mcp=McpOperationBinding(name="bifrost_publish_app"),
        native_builder=True,
        action_scopes=("apps.deploy.execute",),
        authorization_resolver="Application management and Solution-management guards",
        audit_event="app.publish",
        side_effects=(
            "enqueue or reuse the canonical Application publish Platform Job",
        ),
        async_policy=OperationAsyncPolicy.PLATFORM_JOB,
        exclusions={
            "manifest": "Publishing does not change portable Application source metadata.",
            "sdk": _APP_SDK_EXCLUSION,
        },
    ),
    OperationDefinition(
        operation_id="platform.jobs.get",
        summary="Read durable status for one queued platform job",
        target_kind=OperationTargetKind.RESOURCE,
        rest=RestOperationBinding(
            method="GET",
            path="/api/platform-jobs/{job_id}",
            response_model="PlatformJobPublic",
        ),
        cli=CliOperationBinding(path=("platform-jobs", "get")),
        mcp=McpOperationBinding(name="bifrost_get_platform_job"),
        native_builder=True,
        action_scopes=(),
        authorization_resolver="Platform-job requester identity or platform administrator",
        exclusions={
            "manifest": "Reading job progress does not change manifest state.",
            "sdk": _PLATFORM_JOB_SDK_EXCLUSION,
        },
    ),
    OperationDefinition(
        operation_id="apps.replace",
        summary="Repoint a loose Application to a workspace source directory",
        target_kind=OperationTargetKind.RESOURCE,
        rest=RestOperationBinding(
            method="POST",
            path="/api/applications/{app_id}/replace",
            request_model="ApplicationReplaceRequest",
            response_model="ApplicationPublic",
        ),
        cli=CliOperationBinding(path=("apps", "replace")),
        mcp=McpOperationBinding(name="bifrost_replace_app"),
        native_builder=True,
        manifest=ManifestOperationBinding(entity="apps"),
        action_scopes=("apps.readwrite",),
        authorization_resolver="Application management and Solution-management guards",
        audit_event="app.replace",
        side_effects=("write the new source path to the Application manifest",),
        exclusions={"sdk": _APP_SDK_EXCLUSION},
    ),
    OperationDefinition(
        operation_id="solutions.list",
        summary="List shared Solution installs in the selected context",
        target_kind=OperationTargetKind.COLLECTION,
        rest=RestOperationBinding(
            method="GET",
            path="/api/solutions",
            response_model="SolutionsList",
        ),
        mcp=McpOperationBinding(name="bifrost_list_solutions"),
        native_builder=True,
        action_scopes=("solutions.read",),
        authorization_resolver="Selected Organization, Managed, or Platform collection filter",
        exclusions={
            "cli": "The Solution CLI resolves installs inside target-aware lifecycle commands.",
            "manifest": _SOLUTION_MANIFEST_EXCLUSION,
            "sdk": _SOLUTION_SDK_EXCLUSION,
        },
    ),
    OperationDefinition(
        operation_id="solutions.get",
        summary="Get one shared Solution install",
        target_kind=OperationTargetKind.RESOURCE,
        rest=RestOperationBinding(
            method="GET",
            path="/api/solutions/{solution_id}",
            response_model="Solution",
        ),
        mcp=McpOperationBinding(name="bifrost_get_solution"),
        native_builder=True,
        action_scopes=("solutions.read",),
        authorization_resolver="Solution capability and exact resource boundary",
        exclusions={
            "cli": "The Solution CLI resolves installs inside target-aware lifecycle commands.",
            "manifest": _SOLUTION_MANIFEST_EXCLUSION,
            "sdk": _SOLUTION_SDK_EXCLUSION,
        },
    ),
    OperationDefinition(
        operation_id="solutions.create",
        summary="Create a shared Solution install",
        target_kind=OperationTargetKind.COLLECTION,
        rest=RestOperationBinding(
            method="POST",
            path="/api/solutions",
            request_model="SolutionCreate",
            response_model="Solution",
        ),
        cli=CliOperationBinding(path=("solution", "create")),
        mcp=McpOperationBinding(name="bifrost_create_solution"),
        native_builder=True,
        action_scopes=("solutions.readwrite",),
        authorization_resolver="Solution capability and selected target boundary",
        audit_event="solution.create",
        side_effects=("create an empty shared Solution install",),
        exclusions={
            "manifest": _SOLUTION_MANIFEST_EXCLUSION,
            "sdk": _SOLUTION_SDK_EXCLUSION,
        },
    ),
    OperationDefinition(
        operation_id="solutions.update",
        summary="Update local fields on a shared Solution install",
        target_kind=OperationTargetKind.RESOURCE,
        rest=RestOperationBinding(
            method="PATCH",
            path="/api/solutions/{solution_id}",
            request_model="SolutionUpdate",
            response_model="Solution",
        ),
        mcp=McpOperationBinding(name="bifrost_update_solution"),
        native_builder=True,
        action_scopes=("solutions.readwrite",),
        authorization_resolver="Solution capability and exact source/destination boundaries",
        audit_event="solution.update",
        side_effects=("update install-local fields and re-home owned definitions",),
        exclusions={
            "cli": "Install-local editing is currently available through REST, MCP, and the UI.",
            "manifest": _SOLUTION_MANIFEST_EXCLUSION,
            "sdk": _SOLUTION_SDK_EXCLUSION,
        },
    ),
    OperationDefinition(
        operation_id="solutions.delete",
        summary="Permanently delete a shared Solution install",
        target_kind=OperationTargetKind.RESOURCE,
        rest=RestOperationBinding(
            method="DELETE",
            path="/api/solutions/{solution_id}",
            response_model="SolutionDeleteSummary",
        ),
        mcp=McpOperationBinding(name="bifrost_delete_solution"),
        native_builder=True,
        action_scopes=("solutions.readwrite", "solutions.deploy.execute"),
        authorization_resolver="Solution capability, exact resource boundary, and slug confirmation",
        audit_event="solution.delete",
        side_effects=("delete the install and all Solution-owned data",),
        exclusions={
            "cli": "Destructive install deletion is currently available through REST, MCP, and the UI.",
            "manifest": _SOLUTION_MANIFEST_EXCLUSION,
            "sdk": _SOLUTION_SDK_EXCLUSION,
        },
    ),
    OperationDefinition(
        operation_id="solutions.sync",
        summary="Sync a git-connected shared Solution install",
        target_kind=OperationTargetKind.RESOURCE,
        rest=RestOperationBinding(
            method="POST",
            path="/api/solutions/{solution_id}/sync",
        ),
        mcp=McpOperationBinding(name="bifrost_sync_solution"),
        native_builder=True,
        action_scopes=("solutions.readwrite", "solutions.deploy.execute"),
        authorization_resolver="Solution capability and exact resource boundary",
        audit_event="solution.sync",
        side_effects=("pull the configured repository and deploy its current bundle",),
        exclusions={
            "cli": "The Solution CLI deploy command operates from a local workspace; git sync is an installed-Solution operation.",
            "manifest": _SOLUTION_MANIFEST_EXCLUSION,
            "sdk": _SOLUTION_SDK_EXCLUSION,
        },
    ),
    OperationDefinition(
        operation_id="solutions.export",
        summary="Export a shared Solution archive",
        target_kind=OperationTargetKind.RESOURCE,
        rest=RestOperationBinding(
            method="POST",
            path="/api/solutions/{solution_id}/export",
        ),
        cli=CliOperationBinding(path=("solution", "export")),
        native_builder=False,
        action_scopes=("solutions.read", "solutions.build.execute"),
        authorization_resolver="Solution capability and exact resource boundary",
        audit_event="solution.export",
        side_effects=("materialize a shareable or encrypted backup archive",),
        exclusions={
            "mcp": _SOLUTION_ARTIFACT_MCP_EXCLUSION,
            "native_builder": _SOLUTION_ARTIFACT_MCP_EXCLUSION,
            "manifest": _SOLUTION_MANIFEST_EXCLUSION,
            "sdk": _SOLUTION_SDK_EXCLUSION,
        },
    ),
    OperationDefinition(
        operation_id="solutions.deploy",
        summary="Deploy a local Solution archive into an existing install",
        target_kind=OperationTargetKind.RESOURCE,
        rest=RestOperationBinding(
            method="POST",
            path="/api/solutions/{solution_id}/deploy",
            response_model="SolutionDeployEnqueued",
        ),
        cli=CliOperationBinding(path=("solution", "deploy")),
        native_builder=False,
        action_scopes=("solutions.readwrite", "solutions.deploy.execute"),
        authorization_resolver="Solution capability and exact resource boundary",
        audit_event="solution.deploy",
        async_policy=OperationAsyncPolicy.PLATFORM_JOB,
        side_effects=("enqueue a durable full-replace Solution deployment",),
        exclusions={
            "mcp": _SOLUTION_ARTIFACT_MCP_EXCLUSION,
            "native_builder": _SOLUTION_ARTIFACT_MCP_EXCLUSION,
            "manifest": _SOLUTION_MANIFEST_EXCLUSION,
            "sdk": _SOLUTION_SDK_EXCLUSION,
        },
    ),
    OperationDefinition(
        operation_id="solutions.install",
        summary="Install a local Solution archive",
        target_kind=OperationTargetKind.COLLECTION,
        rest=RestOperationBinding(
            method="POST",
            path="/api/solutions/install",
            response_model="SolutionDeployEnqueued",
        ),
        cli=CliOperationBinding(path=("solution", "install")),
        native_builder=False,
        action_scopes=("solutions.deploy.execute",),
        authorization_resolver="Selected target boundary and Solution deploy capability",
        audit_event="solution.install",
        async_policy=OperationAsyncPolicy.PLATFORM_JOB,
        side_effects=("enqueue a durable Solution installation",),
        exclusions={
            "mcp": _SOLUTION_ARTIFACT_MCP_EXCLUSION,
            "native_builder": _SOLUTION_ARTIFACT_MCP_EXCLUSION,
            "manifest": _SOLUTION_MANIFEST_EXCLUSION,
            "sdk": _SOLUTION_SDK_EXCLUSION,
        },
    ),
    OperationDefinition(
        operation_id="solutions.capture",
        summary="Capture loose resources into a shared Solution",
        target_kind=OperationTargetKind.RESOURCE,
        rest=RestOperationBinding(
            method="POST",
            path="/api/solutions/{solution_id}/capture",
            request_model="SolutionCaptureRequest",
            response_model="SolutionCaptureResponse",
        ),
        cli=CliOperationBinding(path=("solution", "capture")),
        native_builder=False,
        action_scopes=(
            "solutions.readwrite",
            "solutions.build.execute",
        ),
        authorization_resolver="Solution capability and exact resource boundary",
        audit_event="solution.capture",
        side_effects=("adopt selected loose resources into the Solution bundle",),
        exclusions={
            "mcp": "Capture reference-resolution parity is tracked with the remaining Solution artifact lifecycle tools.",
            "native_builder": "Native Builder authors Solution source directly and does not adopt existing loose resources.",
            "manifest": _SOLUTION_MANIFEST_EXCLUSION,
            "sdk": _SOLUTION_SDK_EXCLUSION,
        },
    ),
    OperationDefinition(
        operation_id="workflows.list",
        summary="List Workflows visible to the caller",
        target_kind=OperationTargetKind.COLLECTION,
        rest=RestOperationBinding(
            method="GET",
            path="/api/workflows",
            response_model="list[WorkflowMetadata]",
        ),
        cli=CliOperationBinding(path=("workflows", "list")),
        mcp=McpOperationBinding(name="bifrost_list_workflows"),
        native_builder=True,
        action_scopes=("workflows.read",),
        authorization_resolver="WorkflowRepository tenant, role, and private-Solution visibility",
        exclusions={
            "manifest": "Manifests reconcile Workflow state; they do not perform collection reads.",
            "sdk": _WORKFLOW_SDK_EXCLUSION,
        },
    ),
    OperationDefinition(
        operation_id="workflows.get",
        summary="Get one Workflow visible to the caller",
        target_kind=OperationTargetKind.RESOURCE,
        rest=RestOperationBinding(
            method="GET",
            path="/api/workflows/{workflow_id}",
            response_model="WorkflowMetadata",
        ),
        cli=CliOperationBinding(path=("workflows", "get")),
        mcp=McpOperationBinding(name="bifrost_get_workflow"),
        native_builder=True,
        action_scopes=("workflows.read",),
        authorization_resolver="WorkflowRepository tenant, role, and private-Solution visibility",
        exclusions={
            "manifest": "The read does not mutate the Workflow manifest.",
            "sdk": _WORKFLOW_SDK_EXCLUSION,
        },
    ),
    OperationDefinition(
        operation_id="workflows.validate",
        summary="Validate Workflow source",
        target_kind=OperationTargetKind.WORKSPACE,
        rest=RestOperationBinding(
            method="POST",
            path="/api/workflows/validate",
            request_model="WorkflowValidationRequest",
            response_model="WorkflowValidationResponse",
        ),
        cli=CliOperationBinding(path=("workflows", "validate")),
        mcp=McpOperationBinding(name="bifrost_validate_workflow"),
        native_builder=True,
        action_scopes=("workflows.read",),
        authorization_resolver="Authenticated caller and workspace file policy",
        exclusions={
            "manifest": "Validation is read-only and does not alter portable Workflow state.",
            "sdk": _WORKFLOW_SDK_EXCLUSION,
        },
    ),
    OperationDefinition(
        operation_id="workflows.register",
        summary="Register a decorated Workspace function as a Workflow",
        target_kind=OperationTargetKind.WORKSPACE,
        rest=RestOperationBinding(
            method="POST",
            path="/api/workflows/register",
            request_model="RegisterWorkflowRequest",
            response_model="RegisterWorkflowResponse",
        ),
        cli=CliOperationBinding(path=("workflows", "register")),
        mcp=McpOperationBinding(name="bifrost_register_workflow"),
        native_builder=True,
        manifest=ManifestOperationBinding(entity="workflows"),
        action_scopes=("workflows.readwrite", "repository.read"),
        authorization_resolver=(
            "Workflow target boundary plus read access to the platform repository"
        ),
        audit_event="workflow.register",
        side_effects=(
            "index decorated Workflow metadata from workspace source",
            "refresh dynamic Workflow MCP registrations",
            "write manifest change through RepoSyncWriter",
        ),
        exclusions={"sdk": _WORKFLOW_SDK_EXCLUSION},
    ),
    OperationDefinition(
        operation_id="workflows.execute",
        summary="Execute a Workflow",
        target_kind=OperationTargetKind.RESOURCE,
        rest=RestOperationBinding(
            method="POST",
            path="/api/workflows/execute",
            request_model="WorkflowExecutionRequest",
            response_model="WorkflowExecutionResponse",
        ),
        cli=CliOperationBinding(path=("workflows", "execute")),
        mcp=McpOperationBinding(name="bifrost_execute_workflow"),
        native_builder=True,
        action_scopes=("workflows.execute",),
        authorization_resolver="WorkflowRepository execution access and execution-context policy",
        side_effects=(
            "enqueue tracked execution work and publish shared execution progress",
        ),
        async_policy=OperationAsyncPolicy.EXECUTION_WORKER,
        exclusions={
            "manifest": "Execution does not mutate portable Workflow definitions.",
        },
    ),
    OperationDefinition(
        operation_id="workflows.update",
        summary="Update Workflow metadata and access",
        target_kind=OperationTargetKind.RESOURCE,
        rest=RestOperationBinding(
            method="PATCH",
            path="/api/workflows/{workflow_id}",
            request_model="WorkflowUpdateRequest",
            response_model="WorkflowMetadata",
        ),
        cli=CliOperationBinding(path=("workflows", "update")),
        mcp=McpOperationBinding(name="bifrost_update_workflow"),
        native_builder=True,
        manifest=ManifestOperationBinding(entity="workflows"),
        action_scopes=("workflows.readwrite",),
        authorization_resolver="Platform-admin and Solution-management guards",
        audit_event="workflow.update",
        side_effects=(
            "replace selected metadata and role assignments",
            "invalidate execution and endpoint caches",
            "refresh dynamic Workflow MCP registrations",
            "write manifest change through RepoSyncWriter",
        ),
        exclusions={"sdk": _WORKFLOW_SDK_EXCLUSION},
    ),
    OperationDefinition(
        operation_id="workflows.delete",
        summary="Delete a Workspace Workflow",
        target_kind=OperationTargetKind.RESOURCE,
        rest=RestOperationBinding(
            method="DELETE",
            path="/api/workflows/{workflow_id}",
            request_model="DeleteWorkflowRequest",
        ),
        cli=CliOperationBinding(path=("workflows", "delete")),
        mcp=McpOperationBinding(name="bifrost_delete_workflow"),
        native_builder=True,
        manifest=ManifestOperationBinding(entity="workflows", behavior="remove"),
        action_scopes=("workflows.readwrite", "repository.readwrite"),
        authorization_resolver="Platform-admin, dependency, and Solution-management guards",
        audit_event="workflow.delete",
        side_effects=(
            "remove the decorated function or its sole source file",
            "deactivate indexed Workflow metadata",
            "refresh dynamic Workflow MCP registrations",
            "remove the manifest entry through RepoSyncWriter",
        ),
        exclusions={"sdk": _WORKFLOW_SDK_EXCLUSION},
    ),
    OperationDefinition(
        operation_id="workflows.roles.grant",
        summary="Grant a Role access to a Workflow",
        target_kind=OperationTargetKind.RESOURCE,
        rest=RestOperationBinding(
            method="POST",
            path="/api/workflows/{workflow_id}/roles",
            request_model="AssignRolesToWorkflowRequest",
        ),
        cli=CliOperationBinding(path=("workflows", "grant-role")),
        mcp=McpOperationBinding(name="bifrost_grant_workflow_role"),
        native_builder=True,
        manifest=ManifestOperationBinding(entity="workflows"),
        action_scopes=("workflows.readwrite", "roles.readwrite"),
        authorization_resolver="Platform-admin and Solution-management guards",
        audit_event="workflow.roles.grant",
        side_effects=(
            "add idempotent Workflow role grants",
            "write manifest change through RepoSyncWriter",
        ),
        exclusions={"sdk": _WORKFLOW_SDK_EXCLUSION},
    ),
    OperationDefinition(
        operation_id="workflows.roles.revoke",
        summary="Revoke a Role from a Workflow",
        target_kind=OperationTargetKind.RESOURCE,
        rest=RestOperationBinding(
            method="DELETE",
            path="/api/workflows/{workflow_id}/roles/{role_id}",
        ),
        cli=CliOperationBinding(path=("workflows", "revoke-role")),
        mcp=McpOperationBinding(name="bifrost_revoke_workflow_role"),
        native_builder=True,
        manifest=ManifestOperationBinding(entity="workflows"),
        action_scopes=("workflows.readwrite", "roles.readwrite"),
        authorization_resolver="Platform-admin and Solution-management guards",
        audit_event="workflow.roles.revoke",
        side_effects=(
            "remove the exact Workflow role grant",
            "write manifest change through RepoSyncWriter",
        ),
        exclusions={"sdk": _WORKFLOW_SDK_EXCLUSION},
    ),
    OperationDefinition(
        operation_id="integrations.list",
        summary="List Integrations visible to the caller",
        target_kind=OperationTargetKind.COLLECTION,
        rest=RestOperationBinding(
            method="GET",
            path="/api/integrations",
            response_model="IntegrationListResponse",
        ),
        cli=CliOperationBinding(path=("integrations", "list")),
        mcp=McpOperationBinding(name="bifrost_list_integrations"),
        native_builder=True,
        action_scopes=("integrations.read",),
        authorization_resolver=(
            "selected Platform, exact Organization mapping, or Managed-customer mapping view"
        ),
        exclusions={
            "manifest": "Manifests reconcile Integration state; they do not perform collection reads.",
            "sdk": _INTEGRATION_SDK_EXCLUSION,
        },
    ),
    OperationDefinition(
        operation_id="integrations.get",
        summary="Get one Integration",
        target_kind=OperationTargetKind.RESOURCE,
        rest=RestOperationBinding(
            method="GET",
            path="/api/integrations/{integration_id}",
            response_model="IntegrationDetailResponse",
        ),
        cli=CliOperationBinding(path=("integrations", "get")),
        mcp=McpOperationBinding(name="bifrost_get_integration"),
        native_builder=True,
        action_scopes=("integrations.read",),
        authorization_resolver=(
            "integrations.read plus selected Platform or an admitted Organization mapping"
        ),
        exclusions={
            "manifest": "The read does not mutate the Integration manifest.",
            "sdk": _INTEGRATION_SDK_EXCLUSION,
        },
    ),
    OperationDefinition(
        operation_id="integrations.create",
        summary="Create an Integration",
        target_kind=OperationTargetKind.COLLECTION,
        rest=RestOperationBinding(
            method="POST",
            path="/api/integrations",
            request_model="IntegrationCreate",
            response_model="IntegrationResponse",
        ),
        cli=CliOperationBinding(path=("integrations", "create")),
        mcp=McpOperationBinding(name="bifrost_create_integration"),
        native_builder=True,
        manifest=ManifestOperationBinding(entity="integrations"),
        action_scopes=("integrations.readwrite",),
        authorization_resolver="integrations.readwrite at the explicit Platform boundary",
        audit_event="integration.create",
        side_effects=(
            "persist Integration metadata and config schema",
            "write the Integration manifest through RepoSyncWriter",
        ),
        exclusions={"sdk": _INTEGRATION_SDK_EXCLUSION},
    ),
    OperationDefinition(
        operation_id="integrations.update",
        summary="Update an Integration",
        target_kind=OperationTargetKind.RESOURCE,
        rest=RestOperationBinding(
            method="PUT",
            path="/api/integrations/{integration_id}",
            request_model="IntegrationUpdate",
            response_model="IntegrationResponse",
        ),
        cli=CliOperationBinding(path=("integrations", "update")),
        mcp=McpOperationBinding(name="bifrost_update_integration"),
        native_builder=True,
        manifest=ManifestOperationBinding(entity="integrations"),
        action_scopes=("integrations.readwrite",),
        authorization_resolver="integrations.readwrite at the explicit Platform boundary",
        audit_event="integration.update",
        side_effects=(
            "update Integration metadata and config schema",
            "require explicit confirmation before cascading removed config keys",
            "write the Integration manifest through RepoSyncWriter",
        ),
        exclusions={"sdk": _INTEGRATION_SDK_EXCLUSION},
    ),
    OperationDefinition(
        operation_id="integrations.delete",
        summary="Delete an Integration",
        target_kind=OperationTargetKind.RESOURCE,
        rest=RestOperationBinding(
            method="DELETE",
            path="/api/integrations/{integration_id}",
        ),
        native_builder=False,
        manifest=ManifestOperationBinding(entity="integrations"),
        action_scopes=("integrations.readwrite",),
        authorization_resolver=(
            "integrations.readwrite at the explicit Platform boundary"
        ),
        audit_event="integration.delete",
        side_effects=(
            "soft-delete the Integration",
            "write the Integration manifest through RepoSyncWriter",
        ),
        exclusions={
            "cli": "The CLI delete verb has not been implemented yet.",
            "mcp": "The MCP delete tool has not been implemented yet.",
            "native_builder": (
                "The Builder does not expose destructive Integration deletion."
            ),
            "sdk": _INTEGRATION_SDK_EXCLUSION,
        },
    ),
    OperationDefinition(
        operation_id="integrations.mappings.list",
        summary="List Integration mappings",
        target_kind=OperationTargetKind.COLLECTION,
        rest=RestOperationBinding(
            method="GET",
            path="/api/integrations/{integration_id}/mappings",
            response_model="IntegrationMappingListResponse",
        ),
        native_builder=False,
        action_scopes=("integrations.read",),
        authorization_resolver=(
            "integrations.read plus Platform, Managed organizations, or the "
            "exact mapped Organization boundary"
        ),
        exclusions={
            "cli": "Integration detail already returns visible mappings.",
            "mcp": "Integration detail already returns visible mappings.",
            "native_builder": "Integration detail already returns visible mappings.",
            "manifest": "The read does not mutate the Integration manifest.",
            "sdk": _INTEGRATION_SDK_EXCLUSION,
        },
    ),
    OperationDefinition(
        operation_id="integrations.mappings.get",
        summary="Get an Integration mapping",
        target_kind=OperationTargetKind.RESOURCE,
        rest=RestOperationBinding(
            method="GET",
            path="/api/integrations/{integration_id}/mappings/{mapping_id}",
            response_model="IntegrationMappingResponse",
        ),
        native_builder=False,
        action_scopes=("integrations.read",),
        authorization_resolver=(
            "integrations.read plus Platform, Managed organizations, or the "
            "exact persisted mapping Organization boundary"
        ),
        exclusions={
            "cli": "Integration detail already returns visible mappings.",
            "mcp": "Integration detail already returns visible mappings.",
            "native_builder": "Integration detail already returns visible mappings.",
            "manifest": "The read does not mutate the Integration manifest.",
            "sdk": _INTEGRATION_SDK_EXCLUSION,
        },
    ),
    OperationDefinition(
        operation_id="integrations.mappings.get_by_org",
        summary="Get an Integration mapping by Organization",
        target_kind=OperationTargetKind.RESOURCE,
        rest=RestOperationBinding(
            method="GET",
            path=("/api/integrations/{integration_id}/mappings/by-org/{org_id}"),
            response_model="IntegrationMappingResponse",
        ),
        native_builder=False,
        action_scopes=("integrations.read",),
        authorization_resolver=(
            "integrations.read plus Platform, Managed organizations, or the "
            "exact requested Organization boundary"
        ),
        exclusions={
            "cli": "Used internally by integrations update-mapping.",
            "mcp": "Used internally by bifrost_update_integration_mapping.",
            "native_builder": (
                "Used internally by the Integration mapping update operation."
            ),
            "manifest": "The read does not mutate the Integration manifest.",
            "sdk": _INTEGRATION_SDK_EXCLUSION,
        },
    ),
    OperationDefinition(
        operation_id="integrations.mappings.create",
        summary="Create an Integration mapping",
        target_kind=OperationTargetKind.RESOURCE,
        rest=RestOperationBinding(
            method="POST",
            path="/api/integrations/{integration_id}/mappings",
            request_model="IntegrationMappingCreate",
            response_model="IntegrationMappingResponse",
        ),
        cli=CliOperationBinding(path=("integrations", "create-mapping")),
        mcp=McpOperationBinding(name="bifrost_create_integration_mapping"),
        native_builder=True,
        manifest=ManifestOperationBinding(entity="integrations"),
        action_scopes=("integrations.readwrite",),
        authorization_resolver=(
            "integrations.readwrite plus the exact mapped Organization boundary"
        ),
        audit_event="integration_mapping.create",
        side_effects=(
            "persist the Organization mapping and non-OAuth config overrides",
            "write the Integration manifest through RepoSyncWriter",
        ),
        exclusions={"sdk": _INTEGRATION_SDK_EXCLUSION},
    ),
    OperationDefinition(
        operation_id="integrations.mappings.update",
        summary="Update an Integration mapping",
        target_kind=OperationTargetKind.RESOURCE,
        rest=RestOperationBinding(
            method="PUT",
            path="/api/integrations/{integration_id}/mappings/{mapping_id}",
            request_model="IntegrationMappingUpdate",
            response_model="IntegrationMappingResponse",
        ),
        cli=CliOperationBinding(path=("integrations", "update-mapping")),
        mcp=McpOperationBinding(name="bifrost_update_integration_mapping"),
        native_builder=True,
        manifest=ManifestOperationBinding(entity="integrations"),
        action_scopes=("integrations.readwrite",),
        authorization_resolver=(
            "integrations.readwrite plus the persisted mapping Organization boundary"
        ),
        audit_event="integration_mapping.update",
        side_effects=(
            "update the Organization mapping and non-OAuth config overrides",
            "write the Integration manifest through RepoSyncWriter",
        ),
        exclusions={"sdk": _INTEGRATION_SDK_EXCLUSION},
    ),
    OperationDefinition(
        operation_id="integrations.config.get",
        summary="Get Integration default config",
        target_kind=OperationTargetKind.RESOURCE,
        rest=RestOperationBinding(
            method="GET",
            path="/api/integrations/{integration_id}/config",
            response_model="IntegrationConfigResponse",
        ),
        native_builder=False,
        action_scopes=("integrations.read",),
        authorization_resolver=(
            "integrations.read plus the explicit Platform boundary"
        ),
        exclusions={
            "cli": "Integration config defaults are currently UI-only.",
            "mcp": "Integration config defaults are currently UI-only.",
            "native_builder": "Integration config defaults are currently an administrative tool.",
            "manifest": "Integration config reads do not reconcile portable manifest state.",
            "sdk": _INTEGRATION_SDK_EXCLUSION,
        },
    ),
    OperationDefinition(
        operation_id="integrations.config.update",
        summary="Update Integration default config",
        target_kind=OperationTargetKind.RESOURCE,
        rest=RestOperationBinding(
            method="PUT",
            path="/api/integrations/{integration_id}/config",
            request_model="IntegrationConfigUpdate",
            response_model="IntegrationConfigResponse",
        ),
        native_builder=False,
        action_scopes=("integrations.readwrite",),
        authorization_resolver=(
            "integrations.readwrite plus the explicit Platform boundary"
        ),
        exclusions={
            "cli": "Integration config defaults are currently UI-only.",
            "mcp": "Integration config defaults are currently UI-only.",
            "native_builder": "Integration config defaults are currently an administrative tool.",
            "manifest": "Integration config updates do not reconcile portable manifest state directly.",
            "sdk": _INTEGRATION_SDK_EXCLUSION,
        },
    ),
    OperationDefinition(
        operation_id="integrations.mappings.batch",
        summary="Batch upsert Integration mappings",
        target_kind=OperationTargetKind.COLLECTION,
        rest=RestOperationBinding(
            method="POST",
            path="/api/integrations/{integration_id}/mappings/batch",
            request_model="IntegrationMappingBatchRequest",
            response_model="IntegrationMappingBatchResponse",
        ),
        native_builder=False,
        action_scopes=("integrations.readwrite",),
        authorization_resolver=(
            "integrations.readwrite plus the exact mapped Organization boundary"
        ),
        exclusions={
            "cli": "Batch mapping administration is currently UI-only.",
            "mcp": "Batch mapping administration is currently UI-only.",
            "native_builder": "Batch mapping administration is currently an administrative tool.",
            "manifest": "Batch mapping administration does not have a dedicated manifest binding.",
            "sdk": _INTEGRATION_SDK_EXCLUSION,
        },
    ),
    OperationDefinition(
        operation_id="integrations.mappings.delete",
        summary="Delete an Integration mapping",
        target_kind=OperationTargetKind.RESOURCE,
        rest=RestOperationBinding(
            method="DELETE",
            path="/api/integrations/{integration_id}/mappings/{mapping_id}",
        ),
        native_builder=False,
        action_scopes=("integrations.readwrite",),
        authorization_resolver=(
            "integrations.readwrite plus the persisted mapping Organization boundary"
        ),
        exclusions={
            "cli": "Integration mapping deletion is currently UI-only.",
            "mcp": "Integration mapping deletion is currently UI-only.",
            "native_builder": "Integration mapping deletion is an administrative tool.",
            "manifest": "Integration mapping deletion does not have a dedicated manifest binding.",
            "sdk": _INTEGRATION_SDK_EXCLUSION,
        },
    ),
    OperationDefinition(
        operation_id="integrations.mappings.authorize",
        summary="Begin OAuth authorization for an Integration mapping",
        target_kind=OperationTargetKind.RESOURCE,
        rest=RestOperationBinding(
            method="POST",
            path="/api/integrations/{integration_id}/mappings/{mapping_id}/oauth/authorize",
            request_model="MappingAuthorizeRequest",
            response_model="MappingAuthorizeResponse",
        ),
        native_builder=False,
        action_scopes=("integrations.readwrite",),
        authorization_resolver=(
            "integrations.readwrite plus the exact mapped Organization boundary"
        ),
        exclusions={
            "cli": "Per-mapping OAuth authorization is currently UI-only.",
            "mcp": "Per-mapping OAuth authorization is currently UI-only.",
            "native_builder": "Per-mapping OAuth authorization is an administrative tool.",
            "manifest": "Per-mapping OAuth authorization does not reconcile portable manifest state.",
            "sdk": _INTEGRATION_SDK_EXCLUSION,
        },
    ),
    OperationDefinition(
        operation_id="integrations.mappings.disconnect",
        summary="Disconnect an Integration mapping's OAuth token",
        target_kind=OperationTargetKind.RESOURCE,
        rest=RestOperationBinding(
            method="POST",
            path="/api/integrations/{integration_id}/mappings/{mapping_id}/oauth/disconnect",
        ),
        native_builder=False,
        action_scopes=("integrations.readwrite",),
        authorization_resolver=(
            "integrations.readwrite plus the persisted mapping Organization boundary"
        ),
        exclusions={
            "cli": "Per-mapping OAuth disconnect is currently UI-only.",
            "mcp": "Per-mapping OAuth disconnect is currently UI-only.",
            "native_builder": "Per-mapping OAuth disconnect is an administrative tool.",
            "manifest": "Per-mapping OAuth disconnect does not reconcile portable manifest state.",
            "sdk": _INTEGRATION_SDK_EXCLUSION,
        },
    ),
    OperationDefinition(
        operation_id="integrations.mappings.refresh",
        summary="Refresh an Integration mapping's OAuth token",
        target_kind=OperationTargetKind.RESOURCE,
        rest=RestOperationBinding(
            method="POST",
            path="/api/integrations/{integration_id}/mappings/{mapping_id}/oauth/refresh",
            response_model="IntegrationMappingResponse",
        ),
        native_builder=False,
        action_scopes=("integrations.readwrite",),
        authorization_resolver=(
            "integrations.readwrite plus the persisted mapping Organization boundary"
        ),
        exclusions={
            "cli": "Per-mapping OAuth refresh is currently UI-only.",
            "mcp": "Per-mapping OAuth refresh is currently UI-only.",
            "native_builder": "Per-mapping OAuth refresh is an administrative tool.",
            "manifest": "Per-mapping OAuth refresh does not reconcile portable manifest state.",
            "sdk": _INTEGRATION_SDK_EXCLUSION,
        },
    ),
    OperationDefinition(
        operation_id="integrations.oauth.get",
        summary="Get Integration OAuth provider config",
        target_kind=OperationTargetKind.RESOURCE,
        rest=RestOperationBinding(
            method="GET",
            path="/api/integrations/{integration_id}/oauth",
            response_model="OAuthConfigResponse",
        ),
        native_builder=False,
        action_scopes=("integrations.read",),
        authorization_resolver="integrations.read plus the explicit Platform boundary",
        exclusions={
            "cli": "OAuth provider configuration is currently UI-only.",
            "mcp": "OAuth provider configuration is currently UI-only.",
            "native_builder": "OAuth provider configuration is an administrative tool.",
            "manifest": "OAuth provider configuration reads do not reconcile portable manifest state.",
            "sdk": _INTEGRATION_SDK_EXCLUSION,
        },
    ),
    OperationDefinition(
        operation_id="integrations.oauth.authorize",
        summary="Get Integration OAuth authorization URL",
        target_kind=OperationTargetKind.RESOURCE,
        rest=RestOperationBinding(
            method="GET",
            path="/api/integrations/{integration_id}/oauth/authorize",
            response_model="OAuthAuthorizeResponse",
        ),
        native_builder=False,
        action_scopes=("integrations.read",),
        authorization_resolver="integrations.read plus the explicit Platform boundary",
        exclusions={
            "cli": "OAuth authorization URL generation is currently UI-only.",
            "mcp": "OAuth authorization URL generation is currently UI-only.",
            "native_builder": "OAuth authorization URL generation is an administrative tool.",
            "manifest": "OAuth authorization URL generation does not reconcile portable manifest state.",
            "sdk": _INTEGRATION_SDK_EXCLUSION,
        },
    ),
    OperationDefinition(
        operation_id="integrations.oauth.entity_id_source.update",
        summary="Set Integration OAuth entity_id source",
        target_kind=OperationTargetKind.RESOURCE,
        rest=RestOperationBinding(
            method="PATCH",
            path="/api/integrations/{integration_id}/oauth/entity_id_source",
        ),
        native_builder=False,
        action_scopes=("integrations.readwrite",),
        authorization_resolver="integrations.readwrite plus the explicit Platform boundary",
        exclusions={
            "cli": "OAuth entity_id source administration is currently UI-only.",
            "mcp": "OAuth entity_id source administration is currently UI-only.",
            "native_builder": "OAuth entity_id source administration is an administrative tool.",
            "manifest": "OAuth entity_id source administration does not reconcile portable manifest state.",
            "sdk": _INTEGRATION_SDK_EXCLUSION,
        },
    ),
    OperationDefinition(
        operation_id="integrations.oauth.entity_id_source.delete",
        summary="Clear Integration OAuth entity_id source",
        target_kind=OperationTargetKind.RESOURCE,
        rest=RestOperationBinding(
            method="DELETE",
            path="/api/integrations/{integration_id}/oauth/entity_id_source",
        ),
        native_builder=False,
        action_scopes=("integrations.readwrite",),
        authorization_resolver="integrations.readwrite plus the explicit Platform boundary",
        exclusions={
            "cli": "OAuth entity_id source administration is currently UI-only.",
            "mcp": "OAuth entity_id source administration is currently UI-only.",
            "native_builder": "OAuth entity_id source administration is an administrative tool.",
            "manifest": "OAuth entity_id source administration does not reconcile portable manifest state.",
            "sdk": _INTEGRATION_SDK_EXCLUSION,
        },
    ),
    OperationDefinition(
        operation_id="integrations.test",
        summary="Test an Integration connection",
        target_kind=OperationTargetKind.RESOURCE,
        rest=RestOperationBinding(
            method="POST",
            path="/api/integrations/{integration_id}/test",
            request_model="IntegrationTestRequest",
            response_model="IntegrationTestResponse",
        ),
        native_builder=False,
        action_scopes=("integrations.read",),
        authorization_resolver=(
            "integrations.read plus the exact tested Organization or Platform boundary"
        ),
        exclusions={
            "cli": "Integration connection testing is currently UI-only.",
            "mcp": "Integration connection testing is currently UI-only.",
            "native_builder": "Integration connection testing is an administrative tool.",
            "manifest": "Integration connection testing does not reconcile portable manifest state.",
            "sdk": _INTEGRATION_SDK_EXCLUSION,
        },
    ),
    OperationDefinition(
        operation_id="integrations.generate_sdk",
        summary="Generate an SDK from an Integration's OpenAPI spec",
        target_kind=OperationTargetKind.RESOURCE,
        rest=RestOperationBinding(
            method="POST",
            path="/api/integrations/{integration_id}/generate-sdk",
            request_model="GenerateSDKRequest",
            response_model="GenerateSDKResponse",
        ),
        native_builder=False,
        action_scopes=("integrations.readwrite",),
        authorization_resolver="integrations.readwrite plus the explicit Platform boundary",
        exclusions={
            "cli": "Integration SDK generation is currently UI-only.",
            "mcp": "Integration SDK generation is currently UI-only.",
            "native_builder": "Integration SDK generation is an administrative tool.",
            "manifest": "Integration SDK generation does not reconcile portable manifest state.",
            "sdk": _INTEGRATION_SDK_EXCLUSION,
        },
    ),
    OperationDefinition(
        operation_id="executions.list",
        summary="List workflow execution history",
        target_kind=OperationTargetKind.COLLECTION,
        rest=RestOperationBinding(
            method="GET",
            path="/api/executions",
            response_model="ExecutionsListResponse",
        ),
        cli=CliOperationBinding(path=("workflows", "list-executions")),
        mcp=McpOperationBinding(name="bifrost_list_workflow_executions"),
        native_builder=True,
        action_scopes=("executions.read",),
        authorization_resolver="ExecutionRepository.list_executions and caller scope resolver",
        exclusions={
            "manifest": "Execution history is runtime state, not portable manifest content.",
            "sdk": "Application runtimes follow their own execution by ID rather than enumerate platform history.",
        },
    ),
    OperationDefinition(
        operation_id="executions.get",
        summary="Get one workflow execution",
        target_kind=OperationTargetKind.RESOURCE,
        rest=RestOperationBinding(
            method="GET",
            path="/api/executions/{execution_id}",
            response_model="WorkflowExecution",
        ),
        cli=CliOperationBinding(path=("workflows", "get-execution")),
        mcp=McpOperationBinding(name="bifrost_get_workflow_execution"),
        native_builder=True,
        action_scopes=("executions.read",),
        authorization_resolver="ExecutionRepository.get_execution access check",
        exclusions={
            "manifest": "Execution results are runtime state, not portable manifest content.",
            "sdk": "The v2 runtime uses an execution-ID wire binding with a transport-specific path parameter name.",
        },
    ),
    OperationDefinition(
        operation_id="knowledge.search",
        summary="Hybrid-search knowledge documents",
        target_kind=OperationTargetKind.COLLECTION,
        rest=RestOperationBinding(
            method="POST",
            path="/api/knowledge/search",
            request_model="KnowledgeSearchRequest",
            response_model="list[KnowledgeSearchResult]",
        ),
        cli=CliOperationBinding(path=("knowledge", "search")),
        mcp=McpOperationBinding(name="bifrost_search_knowledge"),
        native_builder=True,
        action_scopes=("knowledge.read",),
        authorization_resolver=(
            "User scope resolver or AgentRepository access plus Agent-bound namespace intersection"
        ),
        exclusions={
            "manifest": "Knowledge search reads runtime content; it does not reconcile portable state."
        },
    ),
    OperationDefinition(
        operation_id="knowledge.namespaces.list",
        summary="List Knowledge namespaces visible to the caller",
        target_kind=OperationTargetKind.COLLECTION,
        rest=RestOperationBinding(
            method="GET",
            path="/api/knowledge-sources",
            response_model="list[KnowledgeNamespaceInfo]",
        ),
        cli=CliOperationBinding(path=("knowledge", "list-namespaces")),
        mcp=McpOperationBinding(name="bifrost_list_knowledge_namespaces"),
        native_builder=True,
        action_scopes=("knowledge.read",),
        authorization_resolver="knowledge.read plus selected Platform, Organization, or Managed collection boundary",
        exclusions={
            "manifest": "Knowledge namespace listings read runtime content; they do not reconcile portable state.",
            "sdk": "Application SDK knowledge APIs operate on search/store primitives, not admin document listings.",
        },
    ),
    OperationDefinition(
        operation_id="knowledge.documents.list",
        summary="List Knowledge documents visible to the caller",
        target_kind=OperationTargetKind.COLLECTION,
        rest=RestOperationBinding(
            method="GET",
            path="/api/knowledge-sources/documents",
            response_model="list[KnowledgeDocumentSummary]",
        ),
        cli=CliOperationBinding(path=("knowledge", "list-documents")),
        mcp=McpOperationBinding(name="bifrost_list_knowledge_documents"),
        native_builder=True,
        action_scopes=("knowledge.read",),
        authorization_resolver="knowledge.read plus selected Platform, Organization, or Managed collection boundary",
        exclusions={
            "manifest": "Knowledge document listings read runtime content; they do not reconcile portable state.",
            "sdk": "Application SDK knowledge APIs operate on search/store primitives, not admin document listings.",
        },
    ),
    OperationDefinition(
        operation_id="knowledge.documents.get",
        summary="Get one Knowledge document",
        target_kind=OperationTargetKind.RESOURCE,
        rest=RestOperationBinding(
            method="GET",
            path="/api/knowledge-sources/{namespace}/documents/{doc_id}",
            response_model="KnowledgeDocumentPublic",
        ),
        cli=CliOperationBinding(path=("knowledge", "get-document")),
        mcp=McpOperationBinding(name="bifrost_get_knowledge_document"),
        native_builder=True,
        action_scopes=("knowledge.read",),
        authorization_resolver="knowledge.read plus selected visible resource boundary",
        exclusions={
            "manifest": "Knowledge document reads do not reconcile portable state.",
            "sdk": "Application SDK knowledge APIs operate on search/store primitives, not admin document reads.",
        },
    ),
    OperationDefinition(
        operation_id="knowledge.documents.create",
        summary="Create a Knowledge document",
        target_kind=OperationTargetKind.COLLECTION,
        rest=RestOperationBinding(
            method="POST",
            path="/api/knowledge-sources/{namespace}/documents",
            request_model="KnowledgeDocumentCreate",
            response_model="KnowledgeDocumentPublic",
        ),
        cli=CliOperationBinding(path=("knowledge", "create-document")),
        mcp=McpOperationBinding(name="bifrost_create_knowledge_document"),
        native_builder=True,
        action_scopes=("knowledge.readwrite",),
        authorization_resolver="knowledge.readwrite plus selected exact Platform or Organization boundary",
        audit_event="knowledge.document.create",
        side_effects=("chunk and embed submitted content", "persist Knowledge rows"),
        exclusions={
            "manifest": "Knowledge documents are runtime content, not manifest entities.",
            "sdk": "Application SDK knowledge store APIs provide runtime write primitives.",
        },
    ),
    OperationDefinition(
        operation_id="knowledge.documents.update",
        summary="Update a Knowledge document",
        target_kind=OperationTargetKind.RESOURCE,
        rest=RestOperationBinding(
            method="PUT",
            path="/api/knowledge-sources/{namespace}/documents/{doc_id}",
            request_model="KnowledgeDocumentUpdate",
            response_model="KnowledgeDocumentPublic",
        ),
        cli=CliOperationBinding(path=("knowledge", "update-document")),
        mcp=McpOperationBinding(name="bifrost_update_knowledge_document"),
        native_builder=True,
        action_scopes=("knowledge.readwrite",),
        authorization_resolver="knowledge.readwrite plus persisted and selected exact resource boundaries",
        audit_event="knowledge.document.update",
        side_effects=(
            "re-chunk and re-embed submitted content",
            "replace Knowledge rows",
        ),
        exclusions={
            "manifest": "Knowledge documents are runtime content, not manifest entities.",
            "sdk": "Application SDK knowledge store APIs provide runtime write primitives.",
        },
    ),
    OperationDefinition(
        operation_id="knowledge.documents.delete",
        summary="Delete a Knowledge document",
        target_kind=OperationTargetKind.RESOURCE,
        rest=RestOperationBinding(
            method="DELETE",
            path="/api/knowledge-sources/{namespace}/documents/{doc_id}",
        ),
        cli=CliOperationBinding(path=("knowledge", "delete-document")),
        mcp=McpOperationBinding(name="bifrost_delete_knowledge_document"),
        native_builder=True,
        action_scopes=("knowledge.readwrite",),
        authorization_resolver="knowledge.readwrite plus persisted exact resource boundary",
        audit_event="knowledge.document.delete",
        side_effects=("delete all chunk rows for the logical Knowledge document",),
        exclusions={
            "manifest": "Knowledge documents are runtime content, not manifest entities.",
            "sdk": "Application SDK knowledge store APIs provide runtime write primitives.",
        },
    ),
    OperationDefinition(
        operation_id="roles.list",
        summary="List platform Roles",
        target_kind=OperationTargetKind.PLATFORM,
        rest=RestOperationBinding(
            method="GET",
            path="/api/roles",
            response_model="list[RolePublic]",
        ),
        cli=CliOperationBinding(path=("roles", "list")),
        mcp=McpOperationBinding(name="bifrost_list_roles"),
        native_builder=True,
        action_scopes=("roles.read",),
        authorization_resolver="roles.read in the selected boundary",
        exclusions={
            "manifest": "Manifests reconcile Role state; they do not perform collection reads.",
            "sdk": _ROLE_SDK_EXCLUSION,
        },
    ),
    OperationDefinition(
        operation_id="roles.get",
        summary="Get one platform Role",
        target_kind=OperationTargetKind.RESOURCE,
        rest=RestOperationBinding(
            method="GET",
            path="/api/roles/{role_id}",
            response_model="RolePublic",
        ),
        cli=CliOperationBinding(path=("roles", "get")),
        mcp=McpOperationBinding(name="bifrost_get_role"),
        native_builder=True,
        action_scopes=("roles.read",),
        authorization_resolver="roles.read in the selected boundary",
        exclusions={
            "manifest": "Manifests reconcile Role state; they do not perform resource reads.",
            "sdk": _ROLE_SDK_EXCLUSION,
        },
    ),
    OperationDefinition(
        operation_id="roles.create",
        summary="Create a platform Role",
        target_kind=OperationTargetKind.PLATFORM,
        rest=RestOperationBinding(
            method="POST",
            path="/api/roles",
            request_model="RoleCreate",
            response_model="RolePublic",
        ),
        cli=CliOperationBinding(path=("roles", "create")),
        mcp=McpOperationBinding(name="bifrost_create_role"),
        native_builder=True,
        manifest=ManifestOperationBinding(entity="roles"),
        action_scopes=("roles.readwrite",),
        authorization_resolver="roles.readwrite in the Platform boundary",
        audit_event="role.create",
        side_effects=(
            "persist the Role definition",
            "invalidate Role caches",
            "publish the Role change for manifest synchronization",
        ),
        exclusions={"sdk": _ROLE_SDK_EXCLUSION},
    ),
    OperationDefinition(
        operation_id="roles.update",
        summary="Update a platform Role",
        target_kind=OperationTargetKind.RESOURCE,
        rest=RestOperationBinding(
            method="PATCH",
            path="/api/roles/{role_id}",
            request_model="RoleUpdate",
            response_model="RolePublic",
        ),
        cli=CliOperationBinding(path=("roles", "update")),
        mcp=McpOperationBinding(name="bifrost_update_role"),
        native_builder=True,
        manifest=ManifestOperationBinding(entity="roles"),
        action_scopes=("roles.readwrite",),
        authorization_resolver="Platform-admin gate plus built-in Role guard",
        audit_event="role.update",
        side_effects=(
            "update the Role definition",
            "invalidate Role and affected user-role caches",
            "publish the Role change for manifest synchronization",
        ),
        exclusions={"sdk": _ROLE_SDK_EXCLUSION},
    ),
    OperationDefinition(
        operation_id="roles.delete",
        summary="Delete a platform Role",
        target_kind=OperationTargetKind.RESOURCE,
        rest=RestOperationBinding(
            method="DELETE",
            path="/api/roles/{role_id}",
        ),
        cli=CliOperationBinding(path=("roles", "delete")),
        mcp=McpOperationBinding(name="bifrost_delete_role"),
        native_builder=True,
        manifest=ManifestOperationBinding(entity="roles", behavior="remove"),
        action_scopes=("roles.readwrite",),
        authorization_resolver=(
            "Platform-admin gate plus built-in and Solution-management guards"
        ),
        audit_event="role.delete",
        side_effects=(
            "delete Role assignments through database cascades",
            "invalidate Role and affected user-role caches",
            "publish the Role removal for manifest synchronization",
        ),
        exclusions={"sdk": _ROLE_SDK_EXCLUSION},
    ),
    OperationDefinition(
        operation_id="roles.users.list",
        summary="List users assigned to a Role in the active boundary",
        target_kind=OperationTargetKind.COLLECTION,
        rest=RestOperationBinding(
            method="GET",
            path="/api/roles/{role_id}/users",
            response_model="RoleUsersResponse",
        ),
        action_scopes=("roles.read",),
        authorization_resolver="Boundary-aware Role assignment visibility",
        exclusions=_ROLE_ASSIGNMENT_SURFACE_EXCLUSIONS,
    ),
    OperationDefinition(
        operation_id="roles.users.assign",
        summary="Create or replace boundary-aware Role assignments for users",
        target_kind=OperationTargetKind.RESOURCE,
        rest=RestOperationBinding(
            method="POST",
            path="/api/roles/{role_id}/users",
            request_model="AssignUsersToRoleRequest",
        ),
        action_scopes=("roles.readwrite",),
        authorization_resolver=(
            "Role assignment service requires roles.readwrite at every existing and requested boundary"
        ),
        audit_event="role.user_assigned",
        side_effects=(
            "validate every selected organization, group, Managed, or Platform boundary",
            "replace each user's Role assignment boundary set atomically",
            "invalidate affected user Role caches",
        ),
        exclusions=_ROLE_ASSIGNMENT_SURFACE_EXCLUSIONS,
    ),
    OperationDefinition(
        operation_id="roles.users.remove",
        summary="Remove one user's boundary-aware Role assignment",
        target_kind=OperationTargetKind.RESOURCE,
        rest=RestOperationBinding(
            method="DELETE",
            path="/api/roles/{role_id}/users/{user_id}",
        ),
        action_scopes=("roles.readwrite",),
        authorization_resolver=(
            "Role assignment service requires roles.readwrite at every existing boundary"
        ),
        audit_event="role.user_unassigned",
        side_effects=(
            "protect the final Platform Admin assignment",
            "delete the user's Role assignment and boundary rows",
            "invalidate the affected user's Role cache",
        ),
        exclusions=_ROLE_ASSIGNMENT_SURFACE_EXCLUSIONS,
    ),
    OperationDefinition(
        operation_id="roles.users.bulk_remove",
        summary="Remove a Role assignment from several users",
        target_kind=OperationTargetKind.COLLECTION,
        rest=RestOperationBinding(
            method="DELETE",
            path="/api/roles/{role_id}/users",
            request_model="UnassignUsersFromRoleRequest",
        ),
        action_scopes=("roles.readwrite",),
        authorization_resolver=(
            "Role assignment service requires roles.readwrite at every existing boundary"
        ),
        audit_event="role.users_bulk_unassigned",
        side_effects=(
            "protect the final Platform Admin assignment",
            "delete each admitted user's Role assignment and boundaries",
            "invalidate affected user Role caches",
        ),
        exclusions=_ROLE_ASSIGNMENT_SURFACE_EXCLUSIONS,
    ),
    OperationDefinition(
        operation_id="roles.forms.list",
        summary="List forms assigned to a Role",
        target_kind=OperationTargetKind.COLLECTION,
        rest=RestOperationBinding(
            method="GET",
            path="/api/roles/{role_id}/forms",
            response_model="RoleFormsResponse",
        ),
        action_scopes=("roles.read",),
        authorization_resolver="roles.read in the selected boundary and form organization filtering",
        exclusions=_ROLE_RESOURCE_ASSIGNMENT_SURFACE_EXCLUSIONS,
    ),
    OperationDefinition(
        operation_id="roles.forms.assign",
        summary="Assign forms to a Role",
        target_kind=OperationTargetKind.RESOURCE,
        rest=RestOperationBinding(
            method="POST",
            path="/api/roles/{role_id}/forms",
            request_model="AssignFormsToRoleRequest",
        ),
        action_scopes=("roles.readwrite",),
        authorization_resolver="roles.readwrite in the selected boundary, exact form boundary, and Solution-management guard",
        side_effects=(
            "verify each form exists before assignment",
            "reject solution-managed forms",
            "create role-form junction rows and invalidate role form caches",
        ),
        exclusions=_ROLE_RESOURCE_ASSIGNMENT_SURFACE_EXCLUSIONS,
    ),
    OperationDefinition(
        operation_id="roles.forms.remove",
        summary="Remove one form from a Role",
        target_kind=OperationTargetKind.RESOURCE,
        rest=RestOperationBinding(
            method="DELETE",
            path="/api/roles/{role_id}/forms/{form_id}",
        ),
        action_scopes=("roles.readwrite",),
        authorization_resolver="roles.readwrite in the selected boundary, exact form boundary, and Solution-management guard",
        side_effects=(
            "reject solution-managed forms",
            "delete the role-form junction row and invalidate role form caches",
        ),
        exclusions=_ROLE_RESOURCE_ASSIGNMENT_SURFACE_EXCLUSIONS,
    ),
    OperationDefinition(
        operation_id="roles.forms.bulk_remove",
        summary="Remove several forms from a Role",
        target_kind=OperationTargetKind.COLLECTION,
        rest=RestOperationBinding(
            method="DELETE",
            path="/api/roles/{role_id}/forms",
            request_model="UnassignFormsFromRoleRequest",
        ),
        action_scopes=("roles.readwrite",),
        authorization_resolver="roles.readwrite in the selected boundary, exact form boundaries, and Solution-management guard",
        side_effects=(
            "reject solution-managed forms",
            "delete role-form junction rows and invalidate role form caches",
        ),
        exclusions=_ROLE_RESOURCE_ASSIGNMENT_SURFACE_EXCLUSIONS,
    ),
    OperationDefinition(
        operation_id="roles.agents.list",
        summary="List agents assigned to a Role",
        target_kind=OperationTargetKind.COLLECTION,
        rest=RestOperationBinding(
            method="GET",
            path="/api/roles/{role_id}/agents",
            response_model="RoleAgentsResponse",
        ),
        action_scopes=("roles.read",),
        authorization_resolver="roles.read in the selected boundary and Agent organization filtering",
        exclusions=_ROLE_RESOURCE_ASSIGNMENT_SURFACE_EXCLUSIONS,
    ),
    OperationDefinition(
        operation_id="roles.agents.assign",
        summary="Assign agents to a Role",
        target_kind=OperationTargetKind.RESOURCE,
        rest=RestOperationBinding(
            method="POST",
            path="/api/roles/{role_id}/agents",
            request_model="AssignAgentsToRoleRequest",
        ),
        action_scopes=("roles.readwrite",),
        authorization_resolver="roles.readwrite in the selected boundary, exact Agent boundary, and Solution-management guard",
        side_effects=(
            "verify each agent exists before assignment",
            "reject solution-managed agents",
            "create role-agent junction rows and invalidate role agent caches",
        ),
        exclusions=_ROLE_RESOURCE_ASSIGNMENT_SURFACE_EXCLUSIONS,
    ),
    OperationDefinition(
        operation_id="roles.agents.remove",
        summary="Remove one agent from a Role",
        target_kind=OperationTargetKind.RESOURCE,
        rest=RestOperationBinding(
            method="DELETE",
            path="/api/roles/{role_id}/agents/{agent_id}",
        ),
        action_scopes=("roles.readwrite",),
        authorization_resolver="roles.readwrite in the selected boundary, exact Agent boundary, and Solution-management guard",
        side_effects=(
            "reject solution-managed agents",
            "delete the role-agent junction row and invalidate role agent caches",
        ),
        exclusions=_ROLE_RESOURCE_ASSIGNMENT_SURFACE_EXCLUSIONS,
    ),
    OperationDefinition(
        operation_id="roles.agents.bulk_remove",
        summary="Remove several agents from a Role",
        target_kind=OperationTargetKind.COLLECTION,
        rest=RestOperationBinding(
            method="DELETE",
            path="/api/roles/{role_id}/agents",
            request_model="UnassignAgentsFromRoleRequest",
        ),
        action_scopes=("roles.readwrite",),
        authorization_resolver="roles.readwrite in the selected boundary, exact Agent boundaries, and Solution-management guard",
        side_effects=(
            "reject solution-managed agents",
            "delete role-agent junction rows and invalidate role agent caches",
        ),
        exclusions=_ROLE_RESOURCE_ASSIGNMENT_SURFACE_EXCLUSIONS,
    ),
    OperationDefinition(
        operation_id="roles.apps.list",
        summary="List apps assigned to a Role",
        target_kind=OperationTargetKind.COLLECTION,
        rest=RestOperationBinding(
            method="GET",
            path="/api/roles/{role_id}/apps",
            response_model="RoleAppsResponse",
        ),
        action_scopes=("roles.read",),
        authorization_resolver="roles.read in the selected boundary and Application organization filtering",
        exclusions=_ROLE_RESOURCE_ASSIGNMENT_SURFACE_EXCLUSIONS,
    ),
    OperationDefinition(
        operation_id="roles.apps.assign",
        summary="Assign apps to a Role",
        target_kind=OperationTargetKind.RESOURCE,
        rest=RestOperationBinding(
            method="POST",
            path="/api/roles/{role_id}/apps",
            request_model="AssignAppsToRoleRequest",
        ),
        action_scopes=("roles.readwrite",),
        authorization_resolver="roles.readwrite in the selected boundary, exact Application boundary, and Solution-management guard",
        side_effects=(
            "verify each application exists before assignment",
            "reject solution-managed applications",
            "create role-app junction rows and emit an audit event",
        ),
        exclusions=_ROLE_RESOURCE_ASSIGNMENT_SURFACE_EXCLUSIONS,
    ),
    OperationDefinition(
        operation_id="roles.apps.bulk_remove",
        summary="Remove several apps from a Role",
        target_kind=OperationTargetKind.COLLECTION,
        rest=RestOperationBinding(
            method="DELETE",
            path="/api/roles/{role_id}/apps",
            request_model="UnassignAppsFromRoleRequest",
        ),
        action_scopes=("roles.readwrite",),
        authorization_resolver="roles.readwrite in the selected boundary, exact Application boundaries, and Solution-management guard",
        side_effects=(
            "reject solution-managed applications",
            "delete role-app junction rows and emit an audit event",
        ),
        exclusions=_ROLE_RESOURCE_ASSIGNMENT_SURFACE_EXCLUSIONS,
    ),
    OperationDefinition(
        operation_id="roles.workflows.list",
        summary="List workflows assigned to a Role",
        target_kind=OperationTargetKind.COLLECTION,
        rest=RestOperationBinding(
            method="GET",
            path="/api/roles/{role_id}/workflows",
            response_model="RoleWorkflowsResponse",
        ),
        action_scopes=("roles.read",),
        authorization_resolver="roles.read in the selected boundary and Workflow organization filtering",
        exclusions=_ROLE_RESOURCE_ASSIGNMENT_SURFACE_EXCLUSIONS,
    ),
    OperationDefinition(
        operation_id="roles.workflows.assign",
        summary="Assign workflows to a Role",
        target_kind=OperationTargetKind.RESOURCE,
        rest=RestOperationBinding(
            method="POST",
            path="/api/roles/{role_id}/workflows",
            request_model="AssignWorkflowsToRoleRequest",
        ),
        action_scopes=("roles.readwrite",),
        authorization_resolver="roles.readwrite in the selected boundary, exact Workflow boundary, and Solution-management guard",
        side_effects=(
            "verify each workflow exists before assignment",
            "reject solution-managed workflows",
            "create role-workflow junction rows and emit an audit event",
        ),
        exclusions=_ROLE_RESOURCE_ASSIGNMENT_SURFACE_EXCLUSIONS,
    ),
    OperationDefinition(
        operation_id="roles.workflows.bulk_remove",
        summary="Remove several workflows from a Role",
        target_kind=OperationTargetKind.COLLECTION,
        rest=RestOperationBinding(
            method="DELETE",
            path="/api/roles/{role_id}/workflows",
            request_model="UnassignWorkflowsFromRoleRequest",
        ),
        action_scopes=("roles.readwrite",),
        authorization_resolver="roles.readwrite in the selected boundary, exact Workflow boundaries, and Solution-management guard",
        side_effects=(
            "reject solution-managed workflows",
            "delete role-workflow junction rows and emit an audit event",
        ),
        exclusions=_ROLE_RESOURCE_ASSIGNMENT_SURFACE_EXCLUSIONS,
    ),
    OperationDefinition(
        operation_id="roles.knowledge.list",
        summary="List knowledge namespace assignments for a Role",
        target_kind=OperationTargetKind.COLLECTION,
        rest=RestOperationBinding(
            method="GET",
            path="/api/roles/{role_id}/knowledge",
            response_model="RoleKnowledgeResponse",
        ),
        action_scopes=("roles.read",),
        authorization_resolver="roles.read in the selected boundary and knowledge organization filtering",
        exclusions=_ROLE_RESOURCE_ASSIGNMENT_SURFACE_EXCLUSIONS,
    ),
    OperationDefinition(
        operation_id="roles.knowledge.assign",
        summary="Assign knowledge namespaces to a Role",
        target_kind=OperationTargetKind.RESOURCE,
        rest=RestOperationBinding(
            method="POST",
            path="/api/roles/{role_id}/knowledge",
            request_model="AssignKnowledgeToRoleRequest",
        ),
        action_scopes=("roles.readwrite",),
        authorization_resolver="roles.readwrite in the selected boundary and each knowledge namespace organization",
        audit_event="role.knowledge_assigned",
        side_effects=(
            "create knowledge namespace-role junction rows",
            "emit an audit event",
        ),
        exclusions=_ROLE_RESOURCE_ASSIGNMENT_SURFACE_EXCLUSIONS,
    ),
    OperationDefinition(
        operation_id="roles.knowledge.bulk_remove",
        summary="Remove several knowledge namespace assignments from a Role",
        target_kind=OperationTargetKind.COLLECTION,
        rest=RestOperationBinding(
            method="DELETE",
            path="/api/roles/{role_id}/knowledge",
            request_model="UnassignKnowledgeFromRoleRequest",
        ),
        action_scopes=("roles.readwrite",),
        authorization_resolver="roles.readwrite in the selected boundary and each persisted knowledge assignment organization",
        audit_event="role.knowledge_bulk_unassigned",
        side_effects=(
            "delete knowledge namespace-role junction rows",
            "emit an audit event",
        ),
        exclusions=_ROLE_RESOURCE_ASSIGNMENT_SURFACE_EXCLUSIONS,
    ),
    OperationDefinition(
        operation_id="users.list",
        summary="List users admitted by the active organization boundary",
        target_kind=OperationTargetKind.COLLECTION,
        rest=RestOperationBinding(
            method="GET",
            path="/api/users",
            response_model="list[UserPublic]",
        ),
        action_scopes=("organizations.read",),
        authorization_resolver="Organization-boundary user visibility",
        exclusions=_USER_ADMIN_SURFACE_EXCLUSIONS,
    ),
    OperationDefinition(
        operation_id="users.get",
        summary="Get one user admitted by the active organization boundary",
        target_kind=OperationTargetKind.RESOURCE,
        rest=RestOperationBinding(
            method="GET",
            path="/api/users/{user_id}",
            response_model="UserPublic",
        ),
        action_scopes=("organizations.read",),
        authorization_resolver="Exact organization-boundary user visibility",
        exclusions=_USER_ADMIN_SURFACE_EXCLUSIONS,
    ),
    OperationDefinition(
        operation_id="users.create",
        summary="Create a user in an admitted organization",
        target_kind=OperationTargetKind.COLLECTION,
        rest=RestOperationBinding(
            method="POST",
            path="/api/users",
            request_model="UserCreate",
            response_model="UserPublic",
        ),
        action_scopes=("organizations.readwrite",),
        authorization_resolver="Exact target organization boundary",
        audit_event="user.create",
        side_effects=(
            "persist the user identity",
            "provision the Organization Member Role assignment",
            "create a registration invite",
        ),
        exclusions=_USER_ADMIN_SURFACE_EXCLUSIONS,
    ),
    OperationDefinition(
        operation_id="users.update",
        summary="Update a user in an admitted organization",
        target_kind=OperationTargetKind.RESOURCE,
        rest=RestOperationBinding(
            method="PATCH",
            path="/api/users/{user_id}",
            request_model="UserUpdate",
            response_model="UserPublic",
        ),
        action_scopes=("organizations.readwrite",),
        authorization_resolver="Existing and destination organization boundaries",
        audit_event="user.update",
        side_effects=(
            "update mutable user profile and status fields",
            "synchronize the Organization Member Role when tenancy changes",
        ),
        exclusions=_USER_ADMIN_SURFACE_EXCLUSIONS,
    ),
    OperationDefinition(
        operation_id="users.delete",
        summary="Delete a user from an admitted organization",
        target_kind=OperationTargetKind.RESOURCE,
        rest=RestOperationBinding(
            method="DELETE",
            path="/api/users/{user_id}",
        ),
        action_scopes=("organizations.readwrite",),
        authorization_resolver="Exact organization-boundary user mutation",
        audit_event="user.delete",
        side_effects=(
            "protect the current actor and system identities",
            "delete the user and dependent assignment rows",
        ),
        exclusions=_USER_ADMIN_SURFACE_EXCLUSIONS,
    ),
    OperationDefinition(
        operation_id="users.bulk_update",
        summary="Apply one admitted user administration change to several users",
        target_kind=OperationTargetKind.COLLECTION,
        rest=RestOperationBinding(
            method="PATCH",
            path="/api/users/bulk",
            request_model="BulkUserOperation",
            response_model="BulkUserResponse",
        ),
        action_scopes=("organizations.readwrite",),
        authorization_resolver=(
            "Per-user source/destination organization checks plus Role-boundary checks for assignment replacement"
        ),
        audit_event="user.bulk_update",
        side_effects=(
            "move users, replace Role assignments, or change active state",
            "synchronize Organization Member assignments after moves",
            "invalidate affected user Role caches",
        ),
        exclusions=_USER_ADMIN_SURFACE_EXCLUSIONS,
    ),
    OperationDefinition(
        operation_id="users.invites.resend",
        summary="Replace and send one admitted user's registration invite",
        target_kind=OperationTargetKind.RESOURCE,
        rest=RestOperationBinding(
            method="POST",
            path="/api/users/{user_id}/invite/resend",
            response_model="CreateInviteResponse",
        ),
        action_scopes=("organizations.readwrite",),
        authorization_resolver="Exact organization-boundary user mutation",
        audit_event="user.invite_resend",
        side_effects=("replace the registration invite", "emit the invite event"),
        exclusions=_USER_ADMIN_SURFACE_EXCLUSIONS,
    ),
    OperationDefinition(
        operation_id="users.invites.send",
        summary="Send one admitted user's existing registration invite",
        target_kind=OperationTargetKind.RESOURCE,
        rest=RestOperationBinding(
            method="POST",
            path="/api/users/{user_id}/invite/send",
            request_model="SendInviteRequest",
            response_model="CreateInviteResponse",
        ),
        action_scopes=("organizations.readwrite",),
        authorization_resolver="Exact organization-boundary user mutation",
        audit_event="user.invite_send",
        side_effects=("emit the invite event",),
        exclusions=_USER_ADMIN_SURFACE_EXCLUSIONS,
    ),
    OperationDefinition(
        operation_id="users.invites.regenerate",
        summary="Replace one admitted user's registration invite link",
        target_kind=OperationTargetKind.RESOURCE,
        rest=RestOperationBinding(
            method="POST",
            path="/api/users/{user_id}/invite/regenerate",
            response_model="CreateInviteResponse",
        ),
        action_scopes=("organizations.readwrite",),
        authorization_resolver="Exact organization-boundary user mutation",
        audit_event="user.invite_regenerate",
        side_effects=("replace the registration invite",),
        exclusions=_USER_ADMIN_SURFACE_EXCLUSIONS,
    ),
    OperationDefinition(
        operation_id="users.invites.revoke",
        summary="Revoke one admitted user's registration invite",
        target_kind=OperationTargetKind.RESOURCE,
        rest=RestOperationBinding(
            method="DELETE",
            path="/api/users/{user_id}/invite",
        ),
        action_scopes=("organizations.readwrite",),
        authorization_resolver="Exact organization-boundary user mutation",
        audit_event="user.invite_revoke",
        side_effects=("revoke the active registration invite",),
        exclusions=_USER_ADMIN_SURFACE_EXCLUSIONS,
    ),
    OperationDefinition(
        operation_id="users.roles.list",
        summary="List a user's boundary-aware Role assignments",
        target_kind=OperationTargetKind.RESOURCE,
        rest=RestOperationBinding(
            method="GET",
            path="/api/users/{user_id}/roles",
            response_model="list[RoleAssignmentPublic]",
        ),
        action_scopes=("roles.read",),
        authorization_resolver="Organization-boundary user visibility",
        exclusions=_ROLE_ASSIGNMENT_SURFACE_EXCLUSIONS,
    ),
    OperationDefinition(
        operation_id="users.forms.list",
        summary="List Forms admitted by one user's resource Roles",
        target_kind=OperationTargetKind.RESOURCE,
        rest=RestOperationBinding(
            method="GET",
            path="/api/users/{user_id}/forms",
            response_model="UserFormsResponse",
        ),
        action_scopes=("roles.read",),
        authorization_resolver="Organization-boundary user visibility and resource Role projection",
        exclusions=_ROLE_ASSIGNMENT_SURFACE_EXCLUSIONS,
    ),
    OperationDefinition(
        operation_id="claims.list",
        summary="List Custom Claims in an org",
        target_kind=OperationTargetKind.COLLECTION,
        rest=RestOperationBinding(
            method="GET",
            path="/api/claims",
            response_model="ClaimsList",
        ),
        cli=CliOperationBinding(path=("claims", "list")),
        mcp=McpOperationBinding(name="bifrost_list_claims"),
        native_builder=True,
        action_scopes=("claims.read",),
        authorization_resolver="claims.read in the selected exact or Managed organizations boundary",
        exclusions={
            "manifest": "Manifests reconcile Custom Claim state; they do not perform collection reads.",
            "sdk": _CLAIM_SDK_EXCLUSION,
        },
    ),
    OperationDefinition(
        operation_id="claims.get",
        summary="Get one Custom Claim by name",
        target_kind=OperationTargetKind.RESOURCE,
        rest=RestOperationBinding(
            method="GET",
            path="/api/claims/{name}",
            response_model="CustomClaim",
        ),
        cli=CliOperationBinding(path=("claims", "get")),
        mcp=McpOperationBinding(name="bifrost_get_claim"),
        native_builder=True,
        action_scopes=("claims.read",),
        authorization_resolver="claims.read plus the exact Custom Claim organization boundary",
        exclusions={
            "manifest": "Manifests reconcile Custom Claim state; they do not perform resource reads.",
            "sdk": _CLAIM_SDK_EXCLUSION,
        },
    ),
    OperationDefinition(
        operation_id="claims.create",
        summary="Create a Custom Claim in an org",
        target_kind=OperationTargetKind.COLLECTION,
        rest=RestOperationBinding(
            method="POST",
            path="/api/claims",
            request_model="CustomClaimCreate",
            response_model="CustomClaim",
        ),
        cli=CliOperationBinding(path=("claims", "create")),
        mcp=McpOperationBinding(name="bifrost_create_claim"),
        native_builder=True,
        manifest=ManifestOperationBinding(entity="claims"),
        action_scopes=("claims.readwrite",),
        authorization_resolver="claims.readwrite plus the exact target organization boundary",
        audit_event="claim.create",
        side_effects=(
            "persist the Custom Claim definition",
            "validate the claim query's source table and referenced claim names",
            "reject the write if it introduces a claim dependency cycle",
        ),
        exclusions={"sdk": _CLAIM_SDK_EXCLUSION},
    ),
    OperationDefinition(
        operation_id="claims.update",
        summary="Update a Custom Claim by name",
        target_kind=OperationTargetKind.RESOURCE,
        rest=RestOperationBinding(
            method="PATCH",
            path="/api/claims/{name}",
            request_model="CustomClaimUpdate",
            response_model="CustomClaim",
        ),
        cli=CliOperationBinding(path=("claims", "update")),
        mcp=McpOperationBinding(name="bifrost_update_claim"),
        native_builder=True,
        manifest=ManifestOperationBinding(entity="claims"),
        action_scopes=("claims.readwrite",),
        authorization_resolver="claims.readwrite plus the persisted Custom Claim organization boundary",
        audit_event="claim.update",
        side_effects=(
            "update the Custom Claim definition",
            "validate the claim query's source table and referenced claim names",
            "reject the write if it introduces a claim dependency cycle",
        ),
        exclusions={"sdk": _CLAIM_SDK_EXCLUSION},
    ),
    OperationDefinition(
        operation_id="claims.delete",
        summary="Delete a Custom Claim by name",
        target_kind=OperationTargetKind.RESOURCE,
        rest=RestOperationBinding(
            method="DELETE",
            path="/api/claims/{name}",
        ),
        cli=CliOperationBinding(path=("claims", "delete")),
        mcp=McpOperationBinding(name="bifrost_delete_claim"),
        native_builder=True,
        manifest=ManifestOperationBinding(entity="claims", behavior="remove"),
        action_scopes=("claims.readwrite",),
        authorization_resolver="claims.readwrite plus the persisted Custom Claim organization boundary",
        audit_event="claim.delete",
        side_effects=(
            "reject the delete if a table policy still references the claim",
            "delete the Custom Claim definition",
        ),
        exclusions={"sdk": _CLAIM_SDK_EXCLUSION},
    ),
    OperationDefinition(
        operation_id="files.policies.list",
        summary="List file access policies for a location and scope",
        target_kind=OperationTargetKind.COLLECTION,
        rest=RestOperationBinding(
            method="GET",
            path="/api/files/policies",
            response_model="FilePolicyListResponse",
        ),
        cli=CliOperationBinding(path=("files", "policies", "list")),
        mcp=McpOperationBinding(name="bifrost_list_file_policies"),
        native_builder=True,
        action_scopes=("filepolicies.read",),
        authorization_resolver=(
            "filepolicies.read plus Platform, Managed organizations, or the "
            "exact Organization/Solution boundary"
        ),
        exclusions={
            "manifest": _FILE_POLICY_MANIFEST_EXCLUSION,
            "sdk": _FILE_POLICY_SDK_EXCLUSION,
        },
    ),
    OperationDefinition(
        operation_id="files.policies.get",
        summary="Get the exact file policy for a location/path prefix",
        target_kind=OperationTargetKind.RESOURCE,
        rest=RestOperationBinding(
            method="GET",
            path="/api/files/policies/{policy_path}",
            response_model="FilePolicyPublic",
        ),
        cli=CliOperationBinding(path=("files", "policies", "get")),
        mcp=McpOperationBinding(name="bifrost_get_file_policy"),
        native_builder=True,
        action_scopes=("filepolicies.read",),
        authorization_resolver=(
            "filepolicies.read plus Platform, Managed organizations, or the "
            "exact Organization/Solution boundary"
        ),
        exclusions={
            "manifest": _FILE_POLICY_MANIFEST_EXCLUSION,
            "sdk": _FILE_POLICY_SDK_EXCLUSION,
        },
    ),
    OperationDefinition(
        operation_id="files.policies.set",
        summary="Create or replace the file policy for a location/path prefix",
        target_kind=OperationTargetKind.RESOURCE,
        rest=RestOperationBinding(
            method="PUT",
            path="/api/files/policies/{policy_path}",
            request_model="FilePolicySetRequest",
            response_model="FilePolicyPublic",
        ),
        cli=CliOperationBinding(path=("files", "policies", "set")),
        mcp=McpOperationBinding(name="bifrost_set_file_policy"),
        native_builder=True,
        action_scopes=("filepolicies.readwrite",),
        authorization_resolver=(
            "filepolicies.readwrite plus the exact Platform or Organization boundary"
        ),
        audit_event="file_policy.set",
        side_effects=(
            "reject the write if the path targets a deploy-owned solution tier",
            "resolve and validate any $ref policy-rule references against the file action domain",
            "upsert the file policy row",
            "publish a file-policy-changed event to invalidate cached access decisions",
        ),
        exclusions={
            "manifest": _FILE_POLICY_MANIFEST_EXCLUSION,
            "sdk": _FILE_POLICY_SDK_EXCLUSION,
        },
    ),
    OperationDefinition(
        operation_id="files.policies.delete",
        summary="Delete the file policy for a location/path prefix",
        target_kind=OperationTargetKind.RESOURCE,
        rest=RestOperationBinding(
            method="DELETE",
            path="/api/files/policies/{policy_path}",
        ),
        cli=CliOperationBinding(path=("files", "policies", "delete")),
        mcp=McpOperationBinding(name="bifrost_delete_file_policy"),
        native_builder=True,
        action_scopes=("filepolicies.readwrite",),
        authorization_resolver=(
            "filepolicies.readwrite plus the exact Platform or Organization boundary"
        ),
        audit_event="file_policy.delete",
        side_effects=(
            "reject the delete if the path targets a deploy-owned solution tier",
            "delete the file policy row",
            "publish a file-policy-changed event to invalidate cached access decisions",
        ),
        exclusions={
            "manifest": _FILE_POLICY_MANIFEST_EXCLUSION,
            "sdk": _FILE_POLICY_SDK_EXCLUSION,
        },
    ),
    OperationDefinition(
        operation_id="files.policies.test",
        summary="Test effective file access",
        target_kind=OperationTargetKind.RESOURCE,
        rest=RestOperationBinding(
            method="POST",
            path="/api/files/policies/test",
            request_model="FilePolicyAccessTestRequest",
            response_model="FilePolicyAccessTestResponse",
        ),
        native_builder=False,
        action_scopes=("filepolicies.read",),
        authorization_resolver=(
            "filepolicies.read plus the exact Platform or Organization boundary; "
            "testing another user additionally requires delegated access"
        ),
        exclusions={
            "cli": "The effective-access diagnostic is currently UI-only.",
            "mcp": "The effective-access diagnostic is currently UI-only.",
            "native_builder": "The effective-access diagnostic is an administrative tool.",
            "manifest": "The diagnostic does not mutate file-policy state.",
            "sdk": _FILE_POLICY_SDK_EXCLUSION,
        },
    ),
    OperationDefinition(
        operation_id="files.structure.list",
        summary="List the structural contents of a managed file scope",
        target_kind=OperationTargetKind.COLLECTION,
        rest=RestOperationBinding(
            method="POST",
            path="/api/files/structure",
            request_model="FileStructureRequest",
            response_model="FileStructureResponse",
        ),
        native_builder=False,
        action_scopes=("filepolicies.read",),
        authorization_resolver=(
            "filepolicies.read plus Platform, Managed organizations, or the "
            "exact Organization boundary"
        ),
        exclusions={
            "cli": "The structural explorer is currently UI-only.",
            "mcp": "The structural explorer is currently UI-only.",
            "native_builder": "The structural explorer is an administrative tool.",
            "manifest": "The structural read does not mutate managed files.",
            "sdk": _FILE_POLICY_SDK_EXCLUSION,
        },
    ),
    OperationDefinition(
        operation_id="configs.list",
        summary="List Config values for a scope",
        target_kind=OperationTargetKind.COLLECTION,
        rest=RestOperationBinding(
            method="GET",
            path="/api/config",
            response_model="list[ConfigResponse]",
        ),
        cli=CliOperationBinding(path=("configs", "list")),
        mcp=McpOperationBinding(name="bifrost_list_configs"),
        native_builder=True,
        action_scopes=("configs.read",),
        authorization_resolver="configs.read in the selected exact, Managed organizations, or Platform boundary",
        side_effects=("mask secret-type values as [SECRET]",),
        exclusions={
            "manifest": _CONFIG_MANIFEST_EXCLUSION,
            "sdk": _CONFIG_SDK_EXCLUSION,
        },
    ),
    OperationDefinition(
        operation_id="configs.get",
        summary="Get one Config value by ID",
        target_kind=OperationTargetKind.RESOURCE,
        rest=RestOperationBinding(
            method="GET",
            path="/api/config/{config_id}",
            response_model="ConfigResponse",
        ),
        cli=CliOperationBinding(path=("configs", "get")),
        mcp=McpOperationBinding(name="bifrost_get_config"),
        native_builder=True,
        action_scopes=("configs.read",),
        authorization_resolver="configs.read plus the persisted Config organization boundary",
        side_effects=("mask a secret-type value as [SECRET]",),
        exclusions={
            "manifest": _CONFIG_MANIFEST_EXCLUSION,
            "sdk": _CONFIG_SDK_EXCLUSION,
        },
    ),
    OperationDefinition(
        operation_id="configs.create",
        summary="Set a Config value in a scope",
        target_kind=OperationTargetKind.COLLECTION,
        rest=RestOperationBinding(
            method="POST",
            path="/api/config",
            request_model="SetConfigRequest",
            response_model="ConfigResponse",
        ),
        cli=CliOperationBinding(path=("configs", "create")),
        mcp=McpOperationBinding(name="bifrost_create_config"),
        native_builder=True,
        action_scopes=("configs.readwrite",),
        authorization_resolver="configs.readwrite plus the selected exact Organization or Platform target",
        audit_event="config.create",
        side_effects=(
            "upsert the config row by (integration_id IS NULL, organization, key)",
            "encrypt the value before storage when the type is secret",
            "write the value through to the shared config cache",
        ),
        exclusions={
            "manifest": _CONFIG_MANIFEST_EXCLUSION,
            "sdk": _CONFIG_SDK_EXCLUSION,
        },
    ),
    OperationDefinition(
        operation_id="configs.update",
        summary="Update a Config value by ID",
        target_kind=OperationTargetKind.RESOURCE,
        rest=RestOperationBinding(
            method="PUT",
            path="/api/config/{config_id}",
            request_model="UpdateConfigRequest",
            response_model="ConfigResponse",
        ),
        cli=CliOperationBinding(path=("configs", "update")),
        mcp=McpOperationBinding(name="bifrost_update_config"),
        native_builder=True,
        action_scopes=("configs.readwrite",),
        authorization_resolver="configs.readwrite plus both the persisted and destination Config boundaries",
        audit_event="config.update",
        side_effects=(
            "update the config row, including its organization scope",
            "preserve the stored encrypted value when a secret's value is omitted",
            "invalidate the previous cache entry when the key or scope changed",
            "bump the global config version when the row crosses the global/org boundary",
            "write the new value through to the shared config cache",
        ),
        exclusions={
            "manifest": _CONFIG_MANIFEST_EXCLUSION,
            "sdk": _CONFIG_SDK_EXCLUSION,
        },
    ),
    OperationDefinition(
        operation_id="configs.delete",
        summary="Delete a Config value by ID",
        target_kind=OperationTargetKind.RESOURCE,
        rest=RestOperationBinding(
            method="DELETE",
            path="/api/config/{config_id}",
        ),
        cli=CliOperationBinding(path=("configs", "delete")),
        mcp=McpOperationBinding(name="bifrost_delete_config"),
        native_builder=True,
        action_scopes=("configs.readwrite",),
        authorization_resolver="configs.readwrite plus the persisted Config organization boundary",
        audit_event="config.delete",
        side_effects=(
            "delete the config row",
            "invalidate the cache entry for the deleted key",
        ),
        exclusions={
            "manifest": _CONFIG_MANIFEST_EXCLUSION,
            "sdk": _CONFIG_SDK_EXCLUSION,
        },
    ),
    OperationDefinition(
        operation_id="policy.rules.list",
        summary="List named Policy Rules in a scope",
        target_kind=OperationTargetKind.COLLECTION,
        rest=RestOperationBinding(
            method="GET",
            path="/api/policy-rules",
            response_model="list[PolicyRulePublic]",
        ),
        cli=CliOperationBinding(path=("policy-rules", "list")),
        mcp=McpOperationBinding(name="bifrost_list_policy_rules"),
        native_builder=True,
        action_scopes=("policyrules.read",),
        authorization_resolver="policyrules.read in the selected exact, Managed organizations, or Platform boundary",
        exclusions={
            "manifest": _POLICY_RULE_MANIFEST_EXCLUSION,
            "sdk": _POLICY_RULE_SDK_EXCLUSION,
        },
    ),
    OperationDefinition(
        operation_id="policy.rules.get",
        summary="Get one Policy Rule by domain and name",
        target_kind=OperationTargetKind.RESOURCE,
        rest=RestOperationBinding(
            method="GET",
            path="/api/policy-rules/{domain}/{name}",
            response_model="PolicyRulePublic",
        ),
        cli=CliOperationBinding(path=("policy-rules", "get")),
        mcp=McpOperationBinding(name="bifrost_get_policy_rule"),
        native_builder=True,
        action_scopes=("policyrules.read",),
        authorization_resolver="policyrules.read plus the concrete Policy Rule organization boundary",
        side_effects=(
            "resolve a solution-managed rule as readable; the solution-managed "
            "guard blocks writes only",
        ),
        exclusions={
            "manifest": _POLICY_RULE_MANIFEST_EXCLUSION,
            "sdk": _POLICY_RULE_SDK_EXCLUSION,
        },
    ),
    OperationDefinition(
        operation_id="policy.rules.create",
        summary="Create a named Policy Rule in a scope",
        target_kind=OperationTargetKind.COLLECTION,
        rest=RestOperationBinding(
            method="POST",
            path="/api/policy-rules",
            request_model="PolicyRuleCreate",
            response_model="PolicyRulePublic",
        ),
        cli=CliOperationBinding(path=("policy-rules", "create")),
        mcp=McpOperationBinding(name="bifrost_create_policy_rule"),
        native_builder=True,
        action_scopes=("policyrules.readwrite",),
        authorization_resolver="policyrules.readwrite plus the exact Organization or Platform target",
        audit_event="policy_rule.create",
        side_effects=(
            "validate the rule body against its domain's action vocabulary",
            "persist the policy rule under the (name, domain) natural key",
        ),
        exclusions={
            "manifest": _POLICY_RULE_MANIFEST_EXCLUSION,
            "sdk": _POLICY_RULE_SDK_EXCLUSION,
        },
    ),
    OperationDefinition(
        operation_id="policy.rules.update",
        summary="Update a Policy Rule by domain and name",
        target_kind=OperationTargetKind.RESOURCE,
        rest=RestOperationBinding(
            method="PUT",
            path="/api/policy-rules/{domain}/{name}",
            request_model="PolicyRuleUpdate",
            response_model="PolicyRulePublic",
        ),
        cli=CliOperationBinding(path=("policy-rules", "update")),
        mcp=McpOperationBinding(name="bifrost_update_policy_rule"),
        native_builder=True,
        action_scopes=("policyrules.readwrite",),
        authorization_resolver="policyrules.readwrite plus the persisted Policy Rule organization boundary",
        audit_event="policy_rule.update",
        side_effects=(
            "reject the write if the rule is solution-managed",
            "reject the write if the rule is a read-only built-in",
            "re-validate a replaced body against the rule's immutable domain",
            "record the referencing usage count and any rename in the audit entry",
        ),
        exclusions={
            "manifest": _POLICY_RULE_MANIFEST_EXCLUSION,
            "sdk": _POLICY_RULE_SDK_EXCLUSION,
        },
    ),
    OperationDefinition(
        operation_id="policy.rules.delete",
        summary="Delete a Policy Rule by domain and name",
        target_kind=OperationTargetKind.RESOURCE,
        rest=RestOperationBinding(
            method="DELETE",
            path="/api/policy-rules/{domain}/{name}",
        ),
        cli=CliOperationBinding(path=("policy-rules", "delete")),
        mcp=McpOperationBinding(name="bifrost_delete_policy_rule"),
        native_builder=True,
        action_scopes=("policyrules.readwrite",),
        authorization_resolver="policyrules.readwrite plus the persisted Policy Rule organization boundary",
        audit_event="policy_rule.delete",
        side_effects=(
            "reject the delete if the rule is solution-managed",
            "reject the delete if the rule is a read-only built-in",
            "reject the delete if any file policy or table still references the rule, "
            "returning those usages",
            "delete the policy rule",
        ),
        exclusions={
            "manifest": _POLICY_RULE_MANIFEST_EXCLUSION,
            "sdk": _POLICY_RULE_SDK_EXCLUSION,
        },
    ),
    OperationDefinition(
        operation_id="policy.rules.list_usages",
        summary="List the file policies and tables referencing a Policy Rule",
        target_kind=OperationTargetKind.RESOURCE,
        rest=RestOperationBinding(
            method="GET",
            path="/api/policy-rules/{domain}/{name}/usages",
            response_model="PolicyRuleUsagesPublic",
        ),
        cli=CliOperationBinding(path=("policy-rules", "list-usages")),
        mcp=McpOperationBinding(name="bifrost_list_policy_rule_usages"),
        native_builder=True,
        action_scopes=("policyrules.read",),
        authorization_resolver="policyrules.read plus the persisted Policy Rule organization boundary",
        exclusions={
            "manifest": _POLICY_RULE_MANIFEST_EXCLUSION,
            "sdk": _POLICY_RULE_SDK_EXCLUSION,
        },
    ),
    OperationDefinition(
        operation_id="organizations.list",
        summary="List Organizations",
        target_kind=OperationTargetKind.PLATFORM,
        rest=RestOperationBinding(
            method="GET",
            path="/api/organizations",
            response_model="list[OrganizationPublic]",
        ),
        cli=CliOperationBinding(path=("organizations", "list")),
        mcp=McpOperationBinding(name="bifrost_list_organizations"),
        action_scopes=("organizations.read",),
        authorization_resolver="events.read and Event Source visibility",
        exclusions={
            "native_builder": _ORGANIZATION_BUILDER_EXCLUSION,
            "manifest": "Organization records are deployment-local, not portable manifest content.",
            "sdk": _ORGANIZATION_SDK_EXCLUSION,
        },
    ),
    OperationDefinition(
        operation_id="organizations.get",
        summary="Get one Organization",
        target_kind=OperationTargetKind.RESOURCE,
        rest=RestOperationBinding(
            method="GET",
            path="/api/organizations/{org_id}",
            response_model="OrganizationPublic",
        ),
        cli=CliOperationBinding(path=("organizations", "get")),
        mcp=McpOperationBinding(name="bifrost_get_organization"),
        action_scopes=("organizations.read",),
        authorization_resolver="Platform-admin gate",
        exclusions={
            "native_builder": _ORGANIZATION_BUILDER_EXCLUSION,
            "manifest": "The read does not mutate deployment-local Organization state.",
            "sdk": _ORGANIZATION_SDK_EXCLUSION,
        },
    ),
    OperationDefinition(
        operation_id="organizations.create",
        summary="Create an Organization",
        target_kind=OperationTargetKind.PLATFORM,
        rest=RestOperationBinding(
            method="POST",
            path="/api/organizations",
            request_model="OrganizationCreate",
            response_model="OrganizationPublic",
        ),
        cli=CliOperationBinding(path=("organizations", "create")),
        mcp=McpOperationBinding(name="bifrost_create_organization"),
        action_scopes=("organizations.readwrite",),
        authorization_resolver="Platform-admin gate",
        audit_event="organization.create",
        side_effects=(
            "persist the deployment-local Organization record",
            "upsert the Organization cache",
        ),
        exclusions={
            "native_builder": _ORGANIZATION_BUILDER_EXCLUSION,
            "manifest": "Organizations are deployment-local and are not portable manifest content.",
            "sdk": _ORGANIZATION_SDK_EXCLUSION,
        },
    ),
    OperationDefinition(
        operation_id="organizations.update",
        summary="Update an Organization",
        target_kind=OperationTargetKind.RESOURCE,
        rest=RestOperationBinding(
            method="PATCH",
            path="/api/organizations/{org_id}",
            request_model="OrganizationUpdate",
            response_model="OrganizationPublic",
        ),
        cli=CliOperationBinding(path=("organizations", "update")),
        mcp=McpOperationBinding(name="bifrost_update_organization"),
        action_scopes=("organizations.readwrite",),
        authorization_resolver="Platform-admin and provider-Organization invariant",
        audit_event="organization.update",
        side_effects=("update the Organization cache",),
        exclusions={
            "native_builder": _ORGANIZATION_BUILDER_EXCLUSION,
            "manifest": "Organizations are deployment-local and are not portable manifest content.",
            "sdk": _ORGANIZATION_SDK_EXCLUSION,
        },
    ),
    OperationDefinition(
        operation_id="organizations.delete",
        summary="Disable an Organization",
        target_kind=OperationTargetKind.RESOURCE,
        rest=RestOperationBinding(
            method="DELETE",
            path="/api/organizations/{org_id}",
        ),
        cli=CliOperationBinding(path=("organizations", "delete")),
        mcp=McpOperationBinding(name="bifrost_delete_organization"),
        action_scopes=("organizations.readwrite",),
        authorization_resolver="Platform-admin and provider-Organization invariant",
        audit_event="organization.delete",
        side_effects=(
            "soft-disable the Organization",
            "invalidate the Organization cache",
        ),
        exclusions={
            "native_builder": _ORGANIZATION_BUILDER_EXCLUSION,
            "manifest": "Organizations are deployment-local and are not portable manifest content.",
            "sdk": _ORGANIZATION_SDK_EXCLUSION,
        },
    ),
    OperationDefinition(
        operation_id="events.sources.list",
        summary="List Event Sources",
        target_kind=OperationTargetKind.COLLECTION,
        rest=RestOperationBinding(
            method="GET",
            path="/api/events/sources",
            response_model="EventSourceListResponse",
        ),
        cli=CliOperationBinding(path=("events", "list-sources")),
        mcp=McpOperationBinding(name="bifrost_list_event_sources"),
        native_builder=True,
        action_scopes=("events.read",),
        authorization_resolver="Platform-admin gate and organization filter",
        exclusions={
            "manifest": "Manifests reconcile Event Source state; they do not perform collection reads.",
            "sdk": _EVENT_SDK_EXCLUSION,
        },
    ),
    OperationDefinition(
        operation_id="events.sources.get",
        summary="Get one Event Source",
        target_kind=OperationTargetKind.RESOURCE,
        rest=RestOperationBinding(
            method="GET",
            path="/api/events/sources/{source_id}",
            response_model="EventSourceResponse",
        ),
        cli=CliOperationBinding(path=("events", "get-source")),
        mcp=McpOperationBinding(name="bifrost_get_event_source"),
        native_builder=True,
        action_scopes=("events.read",),
        authorization_resolver="Platform-admin gate",
        exclusions={
            "manifest": "The read does not mutate the Event Source manifest.",
            "sdk": _EVENT_SDK_EXCLUSION,
        },
    ),
    OperationDefinition(
        operation_id="events.sources.create",
        summary="Create an Event Source in an allowed target",
        target_kind=OperationTargetKind.COLLECTION,
        rest=RestOperationBinding(
            method="POST",
            path="/api/events/sources",
            request_model="EventSourceCreate",
            response_model="EventSourceResponse",
        ),
        cli=CliOperationBinding(path=("events", "create-source")),
        mcp=McpOperationBinding(name="bifrost_create_event_source"),
        native_builder=True,
        manifest=ManifestOperationBinding(entity="events"),
        action_scopes=("events.readwrite",),
        authorization_resolver="events.readwrite and exact target boundary",
        audit_event="event_source.create",
        side_effects=(
            "persist type-specific Event Source configuration",
            "create an external webhook subscription when required",
            "write manifest change through RepoSyncWriter",
        ),
        exclusions={"sdk": _EVENT_SDK_EXCLUSION},
    ),
    OperationDefinition(
        operation_id="events.sources.update",
        summary="Update an Event Source",
        target_kind=OperationTargetKind.RESOURCE,
        rest=RestOperationBinding(
            method="PATCH",
            path="/api/events/sources/{source_id}",
            request_model="EventSourceUpdate",
            response_model="EventSourceResponse",
        ),
        cli=CliOperationBinding(path=("events", "update-source")),
        mcp=McpOperationBinding(name="bifrost_update_event_source"),
        native_builder=True,
        manifest=ManifestOperationBinding(entity="events"),
        action_scopes=("events.readwrite",),
        authorization_resolver="events.readwrite, exact resource boundary, and Solution guard",
        audit_event="event_source.update",
        side_effects=(
            "replace selected Event Source and type-specific fields",
            "preserve subscription scope invariants when retargeting",
            "write manifest change through RepoSyncWriter",
        ),
        exclusions={"sdk": _EVENT_SDK_EXCLUSION},
    ),
    OperationDefinition(
        operation_id="events.sources.delete",
        summary="Delete an Event Source and its dependent history",
        target_kind=OperationTargetKind.RESOURCE,
        rest=RestOperationBinding(
            method="DELETE",
            path="/api/events/sources/{source_id}",
        ),
        cli=CliOperationBinding(path=("events", "delete-source")),
        mcp=McpOperationBinding(name="bifrost_delete_event_source"),
        native_builder=True,
        manifest=ManifestOperationBinding(entity="events", behavior="remove"),
        action_scopes=("events.readwrite",),
        authorization_resolver="events.readwrite, exact resource boundary, and Solution guard",
        audit_event="event_source.delete",
        side_effects=(
            "unsubscribe external webhook state when applicable",
            "cascade-delete subscriptions, events, and deliveries",
            "remove the Event Source manifest entry through RepoSyncWriter",
        ),
        exclusions={"sdk": _EVENT_SDK_EXCLUSION},
    ),
    OperationDefinition(
        operation_id="events.subscriptions.list",
        summary="List subscriptions for an Event Source",
        target_kind=OperationTargetKind.COLLECTION,
        rest=RestOperationBinding(
            method="GET",
            path="/api/events/sources/{source_id}/subscriptions",
            response_model="EventSubscriptionListResponse",
        ),
        cli=CliOperationBinding(path=("events", "list-subscriptions")),
        mcp=McpOperationBinding(name="bifrost_list_event_subscriptions"),
        native_builder=True,
        action_scopes=("events.read",),
        authorization_resolver="events.read and parent Event Source visibility",
        exclusions={
            "manifest": "The read does not mutate Event Subscription manifest state.",
            "sdk": _EVENT_SDK_EXCLUSION,
        },
    ),
    OperationDefinition(
        operation_id="events.subscriptions.get",
        summary="Get one Event Subscription",
        target_kind=OperationTargetKind.RESOURCE,
        rest=RestOperationBinding(
            method="GET",
            path="/api/events/sources/{source_id}/subscriptions/{subscription_id}",
            response_model="EventSubscriptionResponse",
        ),
        cli=CliOperationBinding(path=("events", "get-subscription")),
        mcp=McpOperationBinding(name="bifrost_get_event_subscription"),
        native_builder=True,
        action_scopes=("events.read",),
        authorization_resolver="events.read and parent Event Source visibility",
        exclusions={
            "manifest": "The read does not mutate Event Subscription manifest state.",
            "sdk": _EVENT_SDK_EXCLUSION,
        },
    ),
    OperationDefinition(
        operation_id="events.subscriptions.create",
        summary="Create an Event Subscription",
        target_kind=OperationTargetKind.COLLECTION,
        rest=RestOperationBinding(
            method="POST",
            path="/api/events/sources/{source_id}/subscriptions",
            request_model="EventSubscriptionCreate",
            response_model="EventSubscriptionResponse",
        ),
        cli=CliOperationBinding(path=("events", "create-subscription")),
        mcp=McpOperationBinding(name="bifrost_create_event_subscription"),
        native_builder=True,
        manifest=ManifestOperationBinding(entity="events"),
        action_scopes=("events.readwrite",),
        authorization_resolver="events.readwrite, exact parent boundary, and target-resource access",
        audit_event="event_subscription.create",
        side_effects=(
            "persist a validated Workflow or Agent target",
            "write manifest change through RepoSyncWriter",
        ),
        exclusions={"sdk": _EVENT_SDK_EXCLUSION},
    ),
    OperationDefinition(
        operation_id="events.subscriptions.update",
        summary="Update an Event Subscription",
        target_kind=OperationTargetKind.RESOURCE,
        rest=RestOperationBinding(
            method="PATCH",
            path="/api/events/sources/{source_id}/subscriptions/{subscription_id}",
            request_model="EventSubscriptionUpdate",
            response_model="EventSubscriptionResponse",
        ),
        cli=CliOperationBinding(path=("events", "update-subscription")),
        mcp=McpOperationBinding(name="bifrost_update_event_subscription"),
        native_builder=True,
        manifest=ManifestOperationBinding(entity="events"),
        action_scopes=("events.readwrite",),
        authorization_resolver="events.readwrite, exact parent boundary, and Solution guard",
        audit_event="event_subscription.update",
        side_effects=(
            "replace selected filter, mapping, and activation fields",
            "write manifest change through RepoSyncWriter",
        ),
        exclusions={"sdk": _EVENT_SDK_EXCLUSION},
    ),
    OperationDefinition(
        operation_id="events.subscriptions.delete",
        summary="Delete an Event Subscription",
        target_kind=OperationTargetKind.RESOURCE,
        rest=RestOperationBinding(
            method="DELETE",
            path="/api/events/sources/{source_id}/subscriptions/{subscription_id}",
        ),
        cli=CliOperationBinding(path=("events", "delete-subscription")),
        mcp=McpOperationBinding(name="bifrost_delete_event_subscription"),
        native_builder=True,
        manifest=ManifestOperationBinding(entity="events"),
        action_scopes=("events.readwrite",),
        authorization_resolver="events.readwrite, exact parent boundary, and Solution guard",
        audit_event="event_subscription.delete",
        side_effects=(
            "delete the subscription and dependent deliveries",
            "write manifest change through RepoSyncWriter",
        ),
        exclusions={"sdk": _EVENT_SDK_EXCLUSION},
    ),
    OperationDefinition(
        operation_id="events.webhook_adapters.list",
        summary="List Event webhook adapters",
        target_kind=OperationTargetKind.COLLECTION,
        rest=RestOperationBinding(
            method="GET",
            path="/api/events/adapters",
            response_model="WebhookAdapterListResponse",
        ),
        cli=CliOperationBinding(path=("events", "list-webhook-adapters")),
        mcp=McpOperationBinding(name="bifrost_list_event_webhook_adapters"),
        native_builder=True,
        action_scopes=("events.read",),
        authorization_resolver="events.read at the selected boundary",
        exclusions={
            "manifest": "Webhook adapter discovery is runtime metadata, not portable state.",
            "sdk": _EVENT_SDK_EXCLUSION,
        },
    ),
    OperationDefinition(
        operation_id="workspace.files.list",
        summary="List source workspace files",
        target_kind=OperationTargetKind.WORKSPACE,
        rest=RestOperationBinding(
            method="POST",
            path="/api/files/list",
            request_model="FileListRequest",
            response_model="FileListResponse",
        ),
        cli=CliOperationBinding(path=("files", "list")),
        mcp=McpOperationBinding(name="bifrost_list_files"),
        native_builder=True,
        action_scopes=("repository.read",),
        authorization_resolver="repository.read at the explicit Platform boundary",
        exclusions={
            "manifest": "Workspace file listing observes source; it does not reconcile a manifest entity."
        },
    ),
    OperationDefinition(
        operation_id="workspace.files.search",
        summary="Search source workspace files",
        target_kind=OperationTargetKind.WORKSPACE,
        rest=RestOperationBinding(
            method="POST",
            path="/api/files/search",
            request_model="SearchRequest",
            response_model="SearchResponse",
        ),
        cli=CliOperationBinding(path=("files", "search")),
        mcp=McpOperationBinding(name="bifrost_search_files"),
        native_builder=True,
        action_scopes=("repository.read",),
        authorization_resolver="repository.read at the explicit Platform boundary",
        exclusions={
            "manifest": "Workspace search is a read operation, not manifest reconciliation.",
            "sdk": "Application runtimes do not search the global source workspace.",
        },
    ),
    OperationDefinition(
        operation_id="workspace.files.read",
        summary="Read one workspace file",
        target_kind=OperationTargetKind.WORKSPACE,
        rest=RestOperationBinding(
            method="POST",
            path="/api/files/read",
            request_model="FileReadRequest",
            response_model="FileReadResponse",
        ),
        cli=CliOperationBinding(path=("files", "read")),
        mcp=McpOperationBinding(name="bifrost_read_file"),
        native_builder=True,
        action_scopes=("repository.read",),
        authorization_resolver="repository.read at the explicit Platform boundary",
        exclusions={"manifest": "File reads do not reconcile portable state."},
    ),
    OperationDefinition(
        operation_id="workspace.files.stat",
        summary="Get conflict-safe workspace file metadata",
        target_kind=OperationTargetKind.WORKSPACE,
        rest=RestOperationBinding(
            method="POST",
            path="/api/files/stat",
            request_model="FileReadRequest",
            response_model="FileStatResponse",
        ),
        cli=CliOperationBinding(path=("files", "stat")),
        mcp=McpOperationBinding(name="bifrost_stat_file"),
        native_builder=True,
        action_scopes=("repository.read",),
        authorization_resolver="repository.read at the explicit Platform boundary",
        exclusions={
            "manifest": "File metadata reads do not reconcile portable state.",
            "sdk": "Application runtimes use read/exists rather than authoring versions.",
        },
    ),
    OperationDefinition(
        operation_id="workspace.files.exists",
        summary="Check whether a workspace file exists",
        target_kind=OperationTargetKind.WORKSPACE,
        rest=RestOperationBinding(
            method="POST",
            path="/api/files/exists",
            request_model="FileExistsRequest",
            response_model="FileExistsResponse",
        ),
        cli=CliOperationBinding(path=("files", "exists")),
        mcp=McpOperationBinding(name="bifrost_exists_file"),
        native_builder=True,
        action_scopes=("repository.read",),
        authorization_resolver="repository.read at the explicit Platform boundary",
        exclusions={
            "manifest": "Existence checks do not reconcile portable state.",
            "sdk": "The runtime SDK wire binding is tracked separately from source authoring.",
        },
    ),
    OperationDefinition(
        operation_id="workspace.files.write",
        summary="Create or replace one workspace file",
        target_kind=OperationTargetKind.WORKSPACE,
        rest=RestOperationBinding(
            method="POST",
            path="/api/files/write",
            request_model="FileWriteRequest",
        ),
        cli=CliOperationBinding(path=("files", "write")),
        mcp=McpOperationBinding(name="bifrost_write_file"),
        native_builder=True,
        action_scopes=("repository.readwrite",),
        authorization_resolver="repository.readwrite at Platform plus Solution-managed source guards",
        side_effects=(
            "write source and search index",
            "index platform entities and rebuild Application preview bundles",
            "publish file activity and cache invalidations",
        ),
        exclusions={
            "manifest": "Workspace writes may regenerate manifests through entity indexing; no one manifest entry owns the operation."
        },
    ),
    OperationDefinition(
        operation_id="workspace.files.patch",
        summary="Apply a conflict-safe unique-string workspace edit",
        target_kind=OperationTargetKind.WORKSPACE,
        rest=RestOperationBinding(
            method="POST",
            path="/api/files/patch",
            request_model="WorkspaceFilePatchRequest",
            response_model="WorkspaceFilePatchResponse",
        ),
        cli=CliOperationBinding(path=("files", "patch")),
        mcp=McpOperationBinding(name="bifrost_patch_file"),
        native_builder=True,
        action_scopes=("repository.readwrite",),
        authorization_resolver="repository.readwrite at Platform plus Solution-managed source guards",
        side_effects=(
            "atomically replace one unique source fragment",
            "index platform entities and rebuild Application preview bundles",
            "publish file activity and cache invalidations",
        ),
        exclusions={
            "manifest": "Workspace patches may regenerate manifests through entity indexing; no one manifest entry owns the operation.",
            "sdk": "Application runtimes cannot patch the global source workspace.",
        },
    ),
    OperationDefinition(
        operation_id="workspace.files.delete",
        summary="Delete one workspace file",
        target_kind=OperationTargetKind.WORKSPACE,
        rest=RestOperationBinding(
            method="POST",
            path="/api/files/delete",
            request_model="FileDeleteRequest",
        ),
        cli=CliOperationBinding(path=("files", "delete")),
        mcp=McpOperationBinding(name="bifrost_delete_file"),
        native_builder=True,
        action_scopes=("repository.readwrite",),
        authorization_resolver="repository.readwrite at Platform plus Solution-managed source guards",
        side_effects=(
            "delete source and search index metadata",
            "remove platform metadata and Application preview artifacts",
            "publish file activity and cache invalidations",
        ),
        exclusions={
            "manifest": "Workspace deletes may regenerate manifests through entity indexing; no one manifest entry owns the operation."
        },
    ),
    OperationDefinition(
        operation_id="workspace.files.pull",
        summary="Pull changed source workspace state and regenerated manifests",
        target_kind=OperationTargetKind.WORKSPACE,
        rest=RestOperationBinding(
            method="POST",
            path="/api/files/pull",
            request_model="FilePullRequest",
            response_model="FilePullResponse",
        ),
        action_scopes=("repository.read",),
        authorization_resolver="repository.read at the explicit Platform boundary",
        exclusions={
            "cli": "Workspace pull is an internal watch/sync transport and has no standalone CLI command.",
            "manifest": "Source-workspace pull is transport output, not portable manifest reconciliation.",
            "mcp": "Source-workspace pull is a CLI transport helper, not an MCP tool.",
            "native_builder": "Source-workspace pull is not a coding target.",
        },
    ),
    OperationDefinition(
        operation_id="workspace.files.manifest",
        summary="Regenerate manifest files from database state",
        target_kind=OperationTargetKind.WORKSPACE,
        rest=RestOperationBinding(
            method="GET",
            path="/api/files/manifest",
            response_model="dict[str, str]",
        ),
        action_scopes=("repository.read",),
        authorization_resolver="repository.read at the explicit Platform boundary",
        exclusions={
            "cli": "Manifest regeneration is surfaced through pull/sync rather than a standalone CLI command.",
            "mcp": "Manifest regeneration is a CLI transport helper, not an MCP tool.",
            "native_builder": "Manifest regeneration is not a coding target.",
            "manifest": "Manifest regeneration is read-only generated output, not a portable manifest entity.",
        },
    ),
    OperationDefinition(
        operation_id="workspace.files.watch",
        summary="Register, heartbeat, or deregister a CLI watch session",
        target_kind=OperationTargetKind.WORKSPACE,
        rest=RestOperationBinding(
            method="POST",
            path="/api/files/watch",
            request_model="WatchSessionRequest",
        ),
        action_scopes=("repository.read",),
        authorization_resolver="repository.read at the explicit Platform boundary",
        side_effects=(
            "store a short-lived watch-session lease in Redis",
            "publish watch-start and watch-stop activity events",
        ),
        exclusions={
            "cli": "Watch-session leases are managed inside CLI watch/sync and have no standalone command.",
            "manifest": "Watch sessions are runtime coordination, not portable state.",
            "mcp": "Watch-session management is a CLI/browser coordination helper, not an MCP tool.",
            "native_builder": "Watch-session management is not a coding target.",
        },
    ),
    OperationDefinition(
        operation_id="workspace.files.watchers",
        summary="List active CLI watch sessions",
        target_kind=OperationTargetKind.WORKSPACE,
        rest=RestOperationBinding(
            method="GET",
            path="/api/files/watchers",
        ),
        action_scopes=("repository.read",),
        authorization_resolver="repository.read at the explicit Platform boundary",
        exclusions={
            "cli": "Active watch-session inspection is UI-only.",
            "manifest": "Watch-session inspection is runtime state, not portable manifest content.",
            "mcp": "Watch-session inspection is intentionally excluded from MCP tools.",
            "native_builder": "Watch-session inspection is not a coding target.",
        },
    ),
    OperationDefinition(
        operation_id="workspace.files.editor.list",
        summary="List directory contents in the browser editor",
        target_kind=OperationTargetKind.WORKSPACE,
        rest=RestOperationBinding(
            method="GET",
            path="/api/files/editor",
            response_model="list[FileMetadata]",
        ),
        action_scopes=("repository.read",),
        authorization_resolver="repository.read at the explicit Platform boundary",
        exclusions={
            "cli": "Browser-editor listing is not a CLI command.",
            "manifest": "Editor browsing is a source-tree view, not portable manifest reconciliation.",
            "mcp": "Browser-editor listing is intentionally excluded from MCP tools.",
            "native_builder": "Browser-editor listing is not a coding target.",
        },
    ),
    OperationDefinition(
        operation_id="workspace.files.editor.read",
        summary="Read file content in the browser editor",
        target_kind=OperationTargetKind.WORKSPACE,
        rest=RestOperationBinding(
            method="GET",
            path="/api/files/editor/content",
            response_model="FileContentResponse",
        ),
        action_scopes=("repository.read",),
        authorization_resolver="repository.read at the explicit Platform boundary",
        exclusions={
            "cli": "Browser-editor file reads are not a CLI command.",
            "manifest": "Editor reads are source inspection, not portable manifest reconciliation.",
            "mcp": "Browser-editor file reads are intentionally excluded from MCP tools.",
            "native_builder": "Browser-editor file reads are not a coding target.",
        },
    ),
    OperationDefinition(
        operation_id="workspace.files.editor.write",
        summary="Write file content in the browser editor",
        target_kind=OperationTargetKind.WORKSPACE,
        rest=RestOperationBinding(
            method="PUT",
            path="/api/files/editor/content",
            request_model="FileContentRequest",
            response_model="FileContentResponse",
        ),
        action_scopes=("repository.readwrite",),
        authorization_resolver="repository.readwrite at Platform plus Solution-managed source guards",
        side_effects=(
            "write source and search index",
            "publish file activity and cache invalidations",
        ),
        exclusions={
            "cli": "Browser-editor file writes are not a CLI command.",
            "manifest": "Editor writes may regenerate manifests through entity indexing; no one manifest entry owns the operation.",
            "mcp": "Browser-editor file writes are intentionally excluded from MCP tools.",
            "native_builder": "Browser-editor file writes are not a coding target.",
        },
    ),
    OperationDefinition(
        operation_id="workspace.files.editor.folder.create",
        summary="Create a folder in the browser editor",
        target_kind=OperationTargetKind.WORKSPACE,
        rest=RestOperationBinding(
            method="POST",
            path="/api/files/editor/folder",
            response_model="FileMetadata",
        ),
        action_scopes=("repository.readwrite",),
        authorization_resolver="repository.readwrite at Platform plus Solution-managed source guards",
        side_effects=(
            "persist the folder in source storage",
            "publish file activity and cache invalidations",
        ),
        exclusions={
            "cli": "Browser-editor folder creation is not a CLI command.",
            "manifest": "Browser-editor folder creation is a source-tree mutation, not a portable manifest entity.",
            "mcp": "Browser-editor folder creation is intentionally excluded from MCP tools.",
            "native_builder": "Browser-editor folder creation is not a coding target.",
        },
    ),
    OperationDefinition(
        operation_id="workspace.files.editor.delete",
        summary="Delete a file or folder in the browser editor",
        target_kind=OperationTargetKind.WORKSPACE,
        rest=RestOperationBinding(
            method="DELETE",
            path="/api/files/editor",
        ),
        action_scopes=("repository.readwrite",),
        authorization_resolver="repository.readwrite at Platform plus Solution-managed source guards",
        side_effects=(
            "delete source files or folders",
            "publish file activity and cache invalidations",
        ),
        exclusions={
            "cli": "Browser-editor deletes are not a CLI command.",
            "manifest": "Browser-editor deletes may regenerate manifests through entity indexing; no one manifest entry owns the operation.",
            "mcp": "Browser-editor deletes are intentionally excluded from MCP tools.",
            "native_builder": "Browser-editor deletes are not a coding target.",
        },
    ),
    OperationDefinition(
        operation_id="workspace.files.editor.rename",
        summary="Rename or move a file or folder in the browser editor",
        target_kind=OperationTargetKind.WORKSPACE,
        rest=RestOperationBinding(
            method="POST",
            path="/api/files/editor/rename",
            response_model="FileMetadata",
        ),
        action_scopes=("repository.readwrite",),
        authorization_resolver="repository.readwrite at Platform plus Solution-managed source guards",
        side_effects=(
            "move source files or folders",
            "publish file activity and cache invalidations",
        ),
        exclusions={
            "cli": "Browser-editor renames are not a CLI command.",
            "manifest": "Browser-editor renames may regenerate manifests through entity indexing; no one manifest entry owns the operation.",
            "mcp": "Browser-editor renames are intentionally excluded from MCP tools.",
            "native_builder": "Browser-editor renames are not a coding target.",
        },
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


def _singularize(segment: str) -> str:
    """Singularize one CLI path segment.

    Only the two plural forms the CLI actually uses are handled: ``-ies``
    (policies -> policy) and a plain trailing ``s``. Anything else is already
    singular as far as this rule is concerned.
    """
    if segment.endswith("ies"):
        return f"{segment[:-3]}y"
    return segment[:-1] if segment.endswith("s") else segment


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

    # Shape is not enough. A well-formed scope with no definition in
    # AUTHORIZATION_SCOPE_CATALOG cannot be granted to a role, so enforcing it
    # would deny every caller except a superuser — a platform-wide outage that
    # reads as a passing test suite, because tests run as platform admin. Fail
    # at import instead, the way a malformed key already does.
    ungrantable = sorted(
        {
            scope
            for operation in materialized
            for scope in operation.action_scopes
            if get_authorization_scope(scope) is None
        }
    )
    if ungrantable:
        errors.append(
            "action scope(s) declared but not defined in "
            "AUTHORIZATION_SCOPE_CATALOG: " + ", ".join(ungrantable)
        )

    for operation in materialized:
        if operation.cli and operation.mcp:
            # A CLI path is (resource, *nested_resources, verb).  The resource
            # nearest the verb is the one the operation acts on, so it carries
            # the plural/singular rule; every ancestor is always singularized
            # and prefixes it: ("files", "policies", "list") -> file_policies.
            *resource_path, verb = operation.cli.path
            owning_resource = resource_path[-1].replace("-", "_")
            ancestors = [
                _singularize(segment.replace("-", "_"))
                for segment in resource_path[:-1]
            ]
            verb_parts = verb.replace("-", "_").split("_")
            action, subresource = verb_parts[0], verb_parts[1:]
            noun = (
                _singularize(owning_resource)
                if action not in {"list", "search"} or subresource
                else owning_resource
            )
            suffix = "_".join((*ancestors, noun, *subresource))
            expected = f"bifrost_{action}_{suffix}"
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
