"""Canonical Bifrost operation identities and transport bindings."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from shared.authorization_scopes import is_valid_scope_key
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
        action_scopes=("forms.write",),
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
        action_scopes=("forms.write",),
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
        action_scopes=("forms.write",),
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
        authorization_resolver="Platform-admin gate and organization filter",
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
        authorization_resolver="Platform-admin gate",
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
        action_scopes=("tables.write",),
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
        action_scopes=("tables.write",),
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
        action_scopes=("tables.write",),
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
        action_scopes=("apps.write",),
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
        action_scopes=("apps.write",),
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
        action_scopes=("apps.write",),
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
        action_scopes=("apps.write",),
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
        action_scopes=("apps.publish",),
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
        action_scopes=("apps.write",),
        authorization_resolver="Application management and Solution-management guards",
        audit_event="app.replace",
        side_effects=("write the new source path to the Application manifest",),
        exclusions={"sdk": _APP_SDK_EXCLUSION},
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
        action_scopes=("events.write",),
        authorization_resolver="Platform-admin gate and target organization resolver",
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
        action_scopes=("events.write",),
        authorization_resolver="Platform-admin and Solution-management guards",
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
        action_scopes=("events.write",),
        authorization_resolver="Platform-admin and Solution-management guards",
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
        authorization_resolver="Platform-admin gate and parent Event Source lookup",
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
        authorization_resolver="Platform-admin gate and parent Event Source lookup",
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
        action_scopes=("events.write",),
        authorization_resolver="Platform-admin, parent-source, and target-resource validation",
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
        action_scopes=("events.write",),
        authorization_resolver="Platform-admin and Solution-management guards",
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
        action_scopes=("events.write",),
        authorization_resolver="Platform-admin and Solution-management guards",
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
        authorization_resolver="Platform-admin gate",
        exclusions={
            "manifest": "Webhook adapter discovery is runtime metadata, not portable state.",
            "sdk": _EVENT_SDK_EXCLUSION,
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
            verb_parts = verb.replace("-", "_").split("_")
            action, subresource = verb_parts[0], verb_parts[1:]
            noun = (
                resource[:-1]
                if resource.endswith("s")
                and (action not in {"list", "search"} or subresource)
                else resource
            )
            suffix = "_".join((noun, *subresource))
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
