"""
Manifest Generator — serializes current platform DB state to a Manifest.

Used for:
- First-time git connection (export platform state)
- Manual "export to manifest" operations
- Reconciliation verification
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.orm.agents import Agent, AgentDelegation, AgentRole, AgentTool
from src.models.orm.app_roles import AppRole
from src.models.orm.applications import Application
from src.models.orm.config import Config
from src.models.orm.custom_claims import CustomClaim
from src.models.orm.events import EventSource, EventSubscription, ScheduleSource, WebhookSource
from src.models.orm.external_mcp import (
    AgentMCPConnection,
    MCPConnection,
    MCPConnectionTool,
    MCPServer,
)
from src.models.orm.forms import Form, FormField, FormRole
from src.models.orm.integrations import Integration, IntegrationConfigSchema, IntegrationMapping
from src.models.orm.oauth import OAuthProvider
from src.models.orm.organizations import Organization
from src.models.orm.tables import Table
from src.models.orm.users import Role
from src.models.orm.workflow_roles import WorkflowRole
from src.models.orm.workflows import Workflow
from bifrost.manifest import (
    Manifest,
    ManifestAgent,
    ManifestApp,
    ManifestConfig,
    ManifestCustomClaim,
    ManifestEventSource,
    ManifestForm,
    ManifestIntegration,
    ManifestMCPConnection,
    ManifestMCPConnectionTool,
    ManifestMCPServer,
    ManifestOrganization,
    ManifestRole,
    ManifestTable,
    ManifestWorkflow,
)

logger = logging.getLogger(__name__)


# =============================================================================
# Per-entity serialization functions (ORM → Manifest Pydantic model)
#
# These are used by generate_manifest() and by the entity_change_hook to
# serialize individual entities for real-time change broadcasts.
# =============================================================================


def serialize_organization(org: Organization) -> ManifestOrganization:
    """Serialize an Organization ORM object to ManifestOrganization."""
    return ManifestOrganization.from_row(org)


def serialize_role(role: Role) -> ManifestRole:
    """Serialize a Role ORM object to ManifestRole."""
    return ManifestRole.from_row(role)


def serialize_workflow(wf: Workflow, roles: list[str] | None = None) -> ManifestWorkflow:
    """Serialize a Workflow ORM object to ManifestWorkflow."""
    return ManifestWorkflow.from_row(wf, roles=roles)


def _form_field_to_schema_dict(field: FormField) -> dict:
    """Render a FormField ORM row as a dict suitable for ``form_schema.fields``.

    Mirrors the YAML shape produced by the form indexer round-trip: keys are
    only included when set so we don't pollute the manifest with ``null``s.
    """
    out: dict = {"name": field.name, "type": field.type, "required": field.required}
    optional_fields: dict[str, object] = {
        "label": field.label,
        "placeholder": field.placeholder,
        "help_text": field.help_text,
        "default_value": field.default_value,
        "options": field.options,
        "data_provider_id": str(field.data_provider_id) if field.data_provider_id else None,
        "data_provider_inputs": field.data_provider_inputs,
        "visibility_expression": field.visibility_expression,
        "validation": field.validation,
        "allowed_types": field.allowed_types,
        "multiple": field.multiple,
        "max_size_mb": field.max_size_mb,
        "content": field.content,
        "allow_as_query_param": field.allow_as_query_param,
        "auto_fill": field.auto_fill,
    }
    for key, value in optional_fields.items():
        if value is not None:
            out[key] = value
    return out


def serialize_form(
    form: Form,
    roles: list[str] | None = None,
    fields: list[FormField] | None = None,
) -> ManifestForm:
    """Serialize a Form ORM object to ManifestForm with inline content.

    ``fields`` should be the FormField rows for this form, ordered by position.
    They are inlined into ``form_schema.fields`` so the form is fully described
    by the manifest entry — no companion ``forms/{uuid}.form.yaml`` needed.
    """
    return ManifestForm.from_row(form, roles=roles, fields=fields)


def serialize_agent(
    agent: Agent,
    roles: list[str] | None = None,
    tool_ids: "Sequence[str | UUID] | None" = None,
    delegated_agent_ids: "Sequence[str | UUID] | None" = None,
    mcp_connection_ids: "Sequence[str | UUID] | None" = None,
) -> ManifestAgent:
    """Serialize an Agent ORM object to ManifestAgent with inline content.

    ``tool_ids`` / ``delegated_agent_ids`` / ``mcp_connection_ids`` are passed
    in (rather than read from relationships) so the caller controls
    eager-loading and ordering — matching the pattern used for workflow/form
    roles.
    """
    return ManifestAgent.from_row(
        agent,
        roles=roles,
        tool_ids=tool_ids,
        delegated_agent_ids=delegated_agent_ids,
        mcp_connection_ids=mcp_connection_ids,
    )


def serialize_app(app: Application, roles: list[str] | None = None) -> ManifestApp:
    """Serialize an Application ORM object to ManifestApp."""
    return ManifestApp.from_row(app, roles=roles)


def serialize_integration(
    integ: Integration,
    config_schema: list[IntegrationConfigSchema] | None = None,
    oauth_provider: OAuthProvider | None = None,
    mappings: list[IntegrationMapping] | None = None,
) -> ManifestIntegration:
    """Serialize an Integration ORM object to ManifestIntegration."""
    return ManifestIntegration.from_row(
        integ,
        config_schema=config_schema,
        oauth_provider=oauth_provider,
        mappings=mappings,
    )


def serialize_config(cfg: Config) -> ManifestConfig:
    """Serialize a Config ORM object to ManifestConfig."""
    return ManifestConfig.from_row(cfg)


def serialize_custom_claim(claim: CustomClaim) -> ManifestCustomClaim:
    """Serialize a CustomClaim ORM object to ManifestCustomClaim."""
    return ManifestCustomClaim.from_row(claim)


def serialize_table(table: Table) -> ManifestTable:
    """Serialize a Table ORM object to ManifestTable."""
    return ManifestTable.from_row(table)


def serialize_event_source(
    es: EventSource,
    schedule: ScheduleSource | None = None,
    webhook: WebhookSource | None = None,
    subscriptions: list[EventSubscription] | None = None,
) -> ManifestEventSource:
    """Serialize an EventSource ORM object to ManifestEventSource."""
    return ManifestEventSource.from_row(es, schedule=schedule, webhook=webhook, subscriptions=subscriptions)


def serialize_mcp_connection_tool(
    tool: MCPConnectionTool,
) -> ManifestMCPConnectionTool:
    """Serialize an MCPConnectionTool ORM row to its manifest model."""
    return ManifestMCPConnectionTool.from_row(tool)


def serialize_mcp_connection(
    connection: MCPConnection,
    tools: list[MCPConnectionTool] | None = None,
) -> ManifestMCPConnection:
    """Serialize an MCPConnection ORM row to its manifest model.

    ``encrypted_client_secret`` is intentionally omitted — secrets are
    gitignored, the same treatment Config secrets get today.
    """
    return ManifestMCPConnection.from_row(connection, tools=tools)


def serialize_mcp_server(
    server: MCPServer,
    connections_by_id: dict[str, MCPConnection] | None = None,
    tools_by_connection: dict[str, list[MCPConnectionTool]] | None = None,
) -> ManifestMCPServer:
    """Serialize an MCPServer ORM row to its manifest model.

    Connections nested under the server keyed by connection UUID; each
    connection carries its own tool catalog inline.
    """
    return ManifestMCPServer.from_row(
        server,
        connections_by_id=connections_by_id,
        tools_by_connection=tools_by_connection,
    )


# =============================================================================
# Full manifest generation
# =============================================================================


async def generate_manifest(
    db: AsyncSession, solution_id: "UUID | None" = None
) -> Manifest:
    """
    Generate a Manifest from current DB state.

    Queries all active entities and builds a complete manifest
    with org bindings, role assignments, and runtime config.

    When ``solution_id`` is given, the solution-capable entities
    (workflows/forms/agents/apps/tables) are restricted to that one install —
    a per-scope Solution export must not cross-contaminate other tenants or the
    ad-hoc ``_repo/`` workspace (success-criteria §5). With no ``solution_id``
    the legacy full-dump behavior is unchanged.
    """
    def _scope(stmt, model):
        # Exporting ONE Solution → restrict to that install. Otherwise this is a
        # WORKSPACE (_repo/) manifest regen — exclude solution-managed rows so a
        # normal `.bifrost/` regen never serializes deploy-owned entities into the
        # workspace git/import flow (Codex #16). All no-solution_id callers
        # (files router, github_sync, repo_sync_writer, manifest_import) are
        # workspace-tier and want _repo/ only.
        if solution_id is not None:
            return stmt.where(model.solution_id == solution_id)
        return stmt.where(model.solution_id.is_(None))

    # Fetch all active workflows (sorted by name for deterministic manifest output)
    wf_result = await db.execute(
        _scope(
            select(Workflow).where(Workflow.is_active == True), Workflow  # noqa: E712
        ).order_by(Workflow.name)
    )
    workflows_list = wf_result.scalars().all()

    # Fetch all active forms (sorted by name)
    form_result = await db.execute(
        _scope(select(Form).where(Form.is_active == True), Form).order_by(Form.name)  # noqa: E712
    )
    forms_list = form_result.scalars().all()

    # Fetch all active agents (sorted by name)
    agent_result = await db.execute(
        _scope(select(Agent).where(Agent.is_active == True), Agent).order_by(Agent.name)  # noqa: E712
    )
    agents_list = agent_result.scalars().all()

    # Fetch all apps (sorted by name)
    app_result = await db.execute(_scope(select(Application), Application).order_by(Application.name))
    apps_list = app_result.scalars().all()

    # Fetch organizations (sorted by name)
    org_result = await db.execute(select(Organization).order_by(Organization.name))
    orgs_list = org_result.scalars().all()

    # Fetch roles (sorted by name)
    role_result = await db.execute(select(Role).order_by(Role.name))
    roles_list = role_result.scalars().all()

    # Fetch role assignments for all entity types
    wf_role_result = await db.execute(select(WorkflowRole))
    wf_roles_by_wf: dict[str, list[str]] = {}
    for wr in wf_role_result.scalars().all():
        wf_roles_by_wf.setdefault(str(wr.workflow_id), []).append(str(wr.role_id))

    form_role_result = await db.execute(select(FormRole))
    form_roles_by_form: dict[str, list[str]] = {}
    for fr in form_role_result.scalars().all():
        form_roles_by_form.setdefault(str(fr.form_id), []).append(str(fr.role_id))

    agent_role_result = await db.execute(select(AgentRole))
    agent_roles_by_agent: dict[str, list[str]] = {}
    for ar in agent_role_result.scalars().all():
        agent_roles_by_agent.setdefault(str(ar.agent_id), []).append(str(ar.role_id))

    app_role_result = await db.execute(select(AppRole))
    app_roles_by_app: dict[str, list[str]] = {}
    for apr in app_role_result.scalars().all():
        app_roles_by_app.setdefault(str(apr.app_id), []).append(str(apr.role_id))

    # Sort role lists for deterministic manifest output
    for roles in wf_roles_by_wf.values():
        roles.sort()
    for roles in form_roles_by_form.values():
        roles.sort()
    for roles in agent_roles_by_agent.values():
        roles.sort()
    for roles in app_roles_by_app.values():
        roles.sort()

    # Form fields (for inline form_schema)
    form_field_result = await db.execute(
        select(FormField).order_by(FormField.form_id, FormField.position)
    )
    fields_by_form: dict[str, list[FormField]] = {}
    for ff in form_field_result.scalars().all():
        fields_by_form.setdefault(str(ff.form_id), []).append(ff)

    # Agent tools (workflow UUIDs) and delegations (agent UUIDs).
    # Sorted for deterministic manifest output.
    agent_tool_result = await db.execute(select(AgentTool))
    tool_ids_by_agent: dict[str, list[str]] = {}
    for at in agent_tool_result.scalars().all():
        tool_ids_by_agent.setdefault(str(at.agent_id), []).append(str(at.workflow_id))

    agent_delegation_result = await db.execute(select(AgentDelegation))
    delegated_ids_by_agent: dict[str, list[str]] = {}
    for ad in agent_delegation_result.scalars().all():
        delegated_ids_by_agent.setdefault(str(ad.parent_agent_id), []).append(
            str(ad.child_agent_id)
        )

    # Agent MCP connection grants — sorted for deterministic manifest output.
    agent_mcp_result = await db.execute(select(AgentMCPConnection))
    mcp_ids_by_agent: dict[str, list[str]] = {}
    for amc in agent_mcp_result.scalars().all():
        mcp_ids_by_agent.setdefault(str(amc.agent_id), []).append(
            str(amc.connection_id)
        )

    for ids in tool_ids_by_agent.values():
        ids.sort()
    for ids in delegated_ids_by_agent.values():
        ids.sort()
    for ids in mcp_ids_by_agent.values():
        ids.sort()

    # ------------------------------------------------------------------
    # Integrations (with config_schema, oauth_provider, mappings)
    # ------------------------------------------------------------------
    integ_result = await db.execute(
        select(Integration)
        .where(Integration.is_deleted == False)  # noqa: E712
        .order_by(Integration.name)
    )
    integrations_list = integ_result.scalars().unique().all()

    # Config schema items (eager-loaded via selectin, but build a lookup anyway)
    config_schema_result = await db.execute(
        select(IntegrationConfigSchema).order_by(
            IntegrationConfigSchema.integration_id,
            IntegrationConfigSchema.position,
        )
    )
    config_schema_by_integ: dict[str, list[IntegrationConfigSchema]] = {}
    for cs in config_schema_result.scalars().all():
        config_schema_by_integ.setdefault(str(cs.integration_id), []).append(cs)

    # OAuth providers keyed by integration_id
    oauth_result = await db.execute(select(OAuthProvider))
    oauth_by_integ: dict[str, OAuthProvider] = {}
    for op in oauth_result.scalars().all():
        if op.integration_id:
            oauth_by_integ[str(op.integration_id)] = op

    # Integration mappings
    mapping_result = await db.execute(
        select(IntegrationMapping).order_by(
            IntegrationMapping.integration_id,
            IntegrationMapping.organization_id,
        )
    )
    mappings_by_integ: dict[str, list[IntegrationMapping]] = {}
    for im in mapping_result.scalars().all():
        mappings_by_integ.setdefault(str(im.integration_id), []).append(im)

    # ------------------------------------------------------------------
    # Configs (non-secret values, secrets redacted to None)
    # ------------------------------------------------------------------
    config_result = await db.execute(select(Config).order_by(Config.key))
    configs_list = config_result.scalars().all()

    # ------------------------------------------------------------------
    # Custom Claims
    # ------------------------------------------------------------------
    claim_result = await db.execute(
        select(CustomClaim).order_by(CustomClaim.organization_id, CustomClaim.name)
    )
    claims_list = claim_result.scalars().all()

    # ------------------------------------------------------------------
    # Tables
    # ------------------------------------------------------------------
    table_result = await db.execute(_scope(select(Table), Table).order_by(Table.name))
    tables_list = table_result.scalars().all()

    # ------------------------------------------------------------------
    # Event sources + subscriptions
    # ------------------------------------------------------------------
    event_source_result = await db.execute(
        select(EventSource)
        .where(EventSource.is_active == True)  # noqa: E712
        .order_by(EventSource.name)
    )
    event_sources_list = event_source_result.scalars().unique().all()

    # Schedule sources keyed by event_source_id
    schedule_result = await db.execute(select(ScheduleSource))
    schedule_by_source: dict[str, ScheduleSource] = {}
    for ss in schedule_result.scalars().all():
        schedule_by_source[str(ss.event_source_id)] = ss

    # Webhook sources keyed by event_source_id
    webhook_result = await db.execute(select(WebhookSource))
    webhook_by_source: dict[str, WebhookSource] = {}
    for ws in webhook_result.scalars().all():
        webhook_by_source[str(ws.event_source_id)] = ws

    # Subscriptions keyed by event_source_id
    sub_result = await db.execute(
        select(EventSubscription)
        .where(EventSubscription.is_active == True)  # noqa: E712
        .order_by(EventSubscription.event_source_id, EventSubscription.workflow_id)
    )
    subs_by_source: dict[str, list[EventSubscription]] = {}
    for sub in sub_result.scalars().all():
        subs_by_source.setdefault(str(sub.event_source_id), []).append(sub)

    # ------------------------------------------------------------------
    # External MCP servers, connections, and per-connection tool catalogs
    # ------------------------------------------------------------------
    mcp_server_result = await db.execute(
        select(MCPServer).order_by(MCPServer.name)
    )
    mcp_servers_list = mcp_server_result.scalars().unique().all()

    mcp_conn_result = await db.execute(
        select(MCPConnection).order_by(
            MCPConnection.server_id, MCPConnection.organization_id
        )
    )
    connections_by_server: dict[str, dict[str, MCPConnection]] = {}
    for conn in mcp_conn_result.scalars().all():
        connections_by_server.setdefault(str(conn.server_id), {})[
            str(conn.id)
        ] = conn

    mcp_tool_result = await db.execute(
        select(MCPConnectionTool).order_by(
            MCPConnectionTool.connection_id, MCPConnectionTool.tool_name
        )
    )
    tools_by_connection: dict[str, list[MCPConnectionTool]] = {}
    for tool in mcp_tool_result.scalars().all():
        tools_by_connection.setdefault(str(tool.connection_id), []).append(tool)

    # ------------------------------------------------------------------
    # Build manifest using per-entity serialization functions
    # ------------------------------------------------------------------
    manifest = Manifest(
        organizations=[serialize_organization(org) for org in orgs_list],
        roles=[serialize_role(role) for role in roles_list],
        workflows={
            str(wf.id): serialize_workflow(wf, wf_roles_by_wf.get(str(wf.id), []))
            for wf in workflows_list
        },
        integrations={
            str(integ.id): serialize_integration(
                integ,
                config_schema=config_schema_by_integ.get(str(integ.id), []),
                oauth_provider=oauth_by_integ.get(str(integ.id)),
                mappings=mappings_by_integ.get(str(integ.id), []),
            )
            for integ in integrations_list
        },
        configs={
            str(cfg.id): serialize_config(cfg)
            for cfg in configs_list
        },
        claims={
            str(claim.id): serialize_custom_claim(claim)
            for claim in claims_list
        },
        tables={
            str(table.id): serialize_table(table)
            for table in tables_list
        },
        events={
            str(es.id): serialize_event_source(
                es,
                schedule=schedule_by_source.get(str(es.id)),
                webhook=webhook_by_source.get(str(es.id)),
                subscriptions=subs_by_source.get(str(es.id), []),
            )
            for es in event_sources_list
        },
        forms={
            str(form.id): serialize_form(
                form,
                form_roles_by_form.get(str(form.id), []),
                fields=fields_by_form.get(str(form.id)),
            )
            for form in forms_list
        },
        agents={
            str(agent.id): serialize_agent(
                agent,
                agent_roles_by_agent.get(str(agent.id), []),
                tool_ids=tool_ids_by_agent.get(str(agent.id), []),
                delegated_agent_ids=delegated_ids_by_agent.get(str(agent.id), []),
                mcp_connection_ids=mcp_ids_by_agent.get(str(agent.id), []),
            )
            for agent in agents_list
        },
        apps={
            str(app.id): serialize_app(app, app_roles_by_app.get(str(app.id), []))
            for app in apps_list
        },
        mcp_servers={
            str(server.id): serialize_mcp_server(
                server,
                connections_by_id=connections_by_server.get(str(server.id), {}),
                tools_by_connection=tools_by_connection,
            )
            for server in mcp_servers_list
        },
    )

    logger.info(
        f"Generated manifest: {len(manifest.workflows)} workflows, "
        f"{len(manifest.forms)} forms, {len(manifest.agents)} agents, "
        f"{len(manifest.apps)} apps, {len(manifest.integrations)} integrations, "
        f"{len(manifest.configs)} configs, {len(manifest.claims)} claims, "
        f"{len(manifest.tables)} tables, "
        f"{len(manifest.events)} events, "
        f"{len(manifest.mcp_servers)} mcp_servers"
    )

    return manifest
