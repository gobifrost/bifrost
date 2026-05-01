"""
Manifest Generator — serializes current platform DB state to a Manifest.

Used for:
- First-time git connection (export platform state)
- Manual "export to manifest" operations
- Reconciliation verification
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.orm.agents import Agent, AgentDelegation, AgentRole, AgentTool
from src.models.orm.app_roles import AppRole
from src.models.orm.applications import Application
from src.models.orm.config import Config
from src.models.orm.events import EventSource, EventSubscription, ScheduleSource, WebhookSource
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
    ManifestEventSource,
    ManifestEventSubscription,
    ManifestForm,
    ManifestIntegration,
    ManifestIntegrationConfigSchema,
    ManifestIntegrationMapping,
    ManifestOAuthProvider,
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
    return ManifestOrganization(id=str(org.id), name=org.name, is_active=org.is_active)


def serialize_role(role: Role) -> ManifestRole:
    """Serialize a Role ORM object to ManifestRole."""
    return ManifestRole(id=str(role.id), name=role.name)


def serialize_workflow(wf: Workflow, roles: list[str] | None = None) -> ManifestWorkflow:
    """Serialize a Workflow ORM object to ManifestWorkflow."""
    return ManifestWorkflow(
        id=str(wf.id),
        name=wf.name,
        path=wf.path,
        function_name=wf.function_name,
        type=wf.type or "workflow",
        description=wf.description,
        organization_id=str(wf.organization_id) if wf.organization_id else None,
        roles=roles or [],
        access_level=wf.access_level or "authenticated",
        endpoint_enabled=wf.endpoint_enabled or False,
        timeout_seconds=wf.timeout_seconds if wf.timeout_seconds is not None else 1800,
        public_endpoint=wf.public_endpoint or False,
        category=wf.category or "General",
        tags=wf.tags or [],
    )


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
    schema: dict | None = None
    if fields:
        schema = {"fields": [_form_field_to_schema_dict(f) for f in fields]}

    return ManifestForm(
        id=str(form.id),
        name=form.name,
        organization_id=str(form.organization_id) if form.organization_id else None,
        roles=roles or [],
        access_level=form.access_level.value if form.access_level else "role_based",
        description=form.description,
        workflow_id=form.workflow_id,
        launch_workflow_id=form.launch_workflow_id,
        default_launch_params=form.default_launch_params,
        allowed_query_params=form.allowed_query_params,
        form_schema=schema,
    )


def serialize_agent(
    agent: Agent,
    roles: list[str] | None = None,
    tool_ids: list[str] | None = None,
    delegated_agent_ids: list[str] | None = None,
) -> ManifestAgent:
    """Serialize an Agent ORM object to ManifestAgent with inline content.

    ``tool_ids`` / ``delegated_agent_ids`` are passed in (rather than read from
    relationships) so the caller controls eager-loading and ordering — matching
    the pattern used for workflow/form roles.
    """
    return ManifestAgent(
        id=str(agent.id),
        name=agent.name,
        organization_id=str(agent.organization_id) if agent.organization_id else None,
        roles=roles or [],
        access_level=agent.access_level.value if agent.access_level else "role_based",
        description=agent.description,
        system_prompt=agent.system_prompt,
        channels=list(agent.channels) if agent.channels else [],
        tool_ids=tool_ids or [],
        delegated_agent_ids=delegated_agent_ids or [],
        knowledge_sources=list(agent.knowledge_sources) if agent.knowledge_sources else [],
        system_tools=list(agent.system_tools) if agent.system_tools else [],
        llm_model=agent.llm_model,
        llm_max_tokens=agent.llm_max_tokens,
        max_iterations=agent.max_iterations,
        max_token_budget=agent.max_token_budget,
    )


def serialize_app(app: Application, roles: list[str] | None = None) -> ManifestApp:
    """Serialize an Application ORM object to ManifestApp."""
    return ManifestApp(
        id=str(app.id),
        path=app.repo_path.rstrip("/"),
        slug=app.slug,
        name=app.name,
        description=app.description,
        dependencies=app.dependencies or {},
        organization_id=str(app.organization_id) if app.organization_id else None,
        roles=roles or [],
        access_level=app.access_level if app.access_level else "authenticated",
    )


def serialize_integration(
    integ: Integration,
    config_schema: list[IntegrationConfigSchema] | None = None,
    oauth_provider: OAuthProvider | None = None,
    mappings: list[IntegrationMapping] | None = None,
) -> ManifestIntegration:
    """Serialize an Integration ORM object to ManifestIntegration."""
    return ManifestIntegration(
        id=str(integ.id),
        name=integ.name,
        entity_id=integ.entity_id,
        entity_id_name=integ.entity_id_name,
        default_entity_id=integ.default_entity_id,
        list_entities_data_provider_id=(
            str(integ.list_entities_data_provider_id)
            if integ.list_entities_data_provider_id else None
        ),
        config_schema=[
            ManifestIntegrationConfigSchema(
                key=cs.key,
                type=cs.type,
                required=cs.required,
                description=cs.description,
                options=cs.options,
                position=cs.position,
            )
            for cs in (config_schema or [])
        ],
        oauth_provider=(
            ManifestOAuthProvider(
                provider_name=oauth_provider.provider_name,
                display_name=oauth_provider.display_name,
                oauth_flow_type=oauth_provider.oauth_flow_type,
                client_id=oauth_provider.client_id or "__NEEDS_SETUP__",
                authorization_url=oauth_provider.authorization_url,
                token_url=oauth_provider.token_url,
                token_url_defaults=oauth_provider.token_url_defaults or None,
                scopes=oauth_provider.scopes or [],
                redirect_uri=oauth_provider.redirect_uri,
            )
            if oauth_provider else None
        ),
        mappings=[
            ManifestIntegrationMapping(
                organization_id=str(im.organization_id) if im.organization_id else None,
                entity_id=im.entity_id,
                entity_name=im.entity_name,
                oauth_token_id=str(im.oauth_token_id) if im.oauth_token_id else None,
            )
            for im in (mappings or [])
        ],
    )


def serialize_config(cfg: Config) -> ManifestConfig:
    """Serialize a Config ORM object to ManifestConfig."""
    from src.models.enums import ConfigType

    return ManifestConfig(
        id=str(cfg.id),
        integration_id=str(cfg.integration_id) if cfg.integration_id else None,
        key=cfg.key,
        config_type=cfg.config_type.value if cfg.config_type and hasattr(cfg.config_type, 'value') else (cfg.config_type or "string"),
        description=cfg.description,
        organization_id=str(cfg.organization_id) if cfg.organization_id else None,
        value=None if (cfg.config_type == ConfigType.SECRET or str(cfg.config_type) == "secret") else cfg.value,
    )


def serialize_table(table: Table) -> ManifestTable:
    """Serialize a Table ORM object to ManifestTable."""
    return ManifestTable(
        id=str(table.id),
        name=table.name,
        description=table.description,
        organization_id=str(table.organization_id) if table.organization_id else None,
        application_id=str(table.application_id) if table.application_id else None,
        **{"schema": table.schema},  # type: ignore[arg-type]  # alias for table_schema
    )


def serialize_event_source(
    es: EventSource,
    schedule: ScheduleSource | None = None,
    webhook: WebhookSource | None = None,
    subscriptions: list[EventSubscription] | None = None,
) -> ManifestEventSource:
    """Serialize an EventSource ORM object to ManifestEventSource."""
    cron_expression = schedule.cron_expression if schedule else None
    tz = schedule.timezone if schedule else None
    schedule_enabled = schedule.enabled if schedule else None
    overlap_policy = schedule.overlap_policy.value if schedule and schedule.overlap_policy else None

    adapter_name = webhook.adapter_name if webhook else None
    webhook_integration_id = str(webhook.integration_id) if webhook and webhook.integration_id else None
    webhook_config = webhook.config if webhook and webhook.config else None
    rate_limit_per_minute = webhook.rate_limit_per_minute if webhook else 60
    rate_limit_window_seconds = webhook.rate_limit_window_seconds if webhook else 60
    rate_limit_enabled = webhook.rate_limit_enabled if webhook else True

    return ManifestEventSource(
        id=str(es.id),
        name=es.name,
        source_type=es.source_type if isinstance(es.source_type, str) else es.source_type.value,
        organization_id=str(es.organization_id) if es.organization_id else None,
        is_active=es.is_active,
        cron_expression=cron_expression,
        timezone=tz,
        schedule_enabled=schedule_enabled,
        overlap_policy=overlap_policy,
        adapter_name=adapter_name,
        webhook_integration_id=webhook_integration_id,
        webhook_config=webhook_config,
        rate_limit_per_minute=rate_limit_per_minute,
        rate_limit_window_seconds=rate_limit_window_seconds,
        rate_limit_enabled=rate_limit_enabled,
        subscriptions=[
            ManifestEventSubscription(
                id=str(sub.id),
                target_type=sub.target_type or "workflow",
                workflow_id=str(sub.workflow_id) if sub.workflow_id else None,
                agent_id=str(sub.agent_id) if sub.agent_id else None,
                event_type=sub.event_type,
                filter_expression=sub.filter_expression,
                input_mapping=sub.input_mapping,
                is_active=sub.is_active,
            )
            for sub in (subscriptions or [])
        ],
    )


# =============================================================================
# Full manifest generation
# =============================================================================


async def generate_manifest(db: AsyncSession) -> Manifest:
    """
    Generate a Manifest from current DB state.

    Queries all active entities and builds a complete manifest
    with org bindings, role assignments, and runtime config.
    """
    # Fetch all active workflows (sorted by name for deterministic manifest output)
    wf_result = await db.execute(
        select(Workflow).where(Workflow.is_active == True).order_by(Workflow.name)  # noqa: E712
    )
    workflows_list = wf_result.scalars().all()

    # Fetch all active forms (sorted by name)
    form_result = await db.execute(
        select(Form).where(Form.is_active == True).order_by(Form.name)  # noqa: E712
    )
    forms_list = form_result.scalars().all()

    # Fetch all active agents (sorted by name)
    agent_result = await db.execute(
        select(Agent).where(Agent.is_active == True).order_by(Agent.name)  # noqa: E712
    )
    agents_list = agent_result.scalars().all()

    # Fetch all apps (sorted by name)
    app_result = await db.execute(select(Application).order_by(Application.name))
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

    for ids in tool_ids_by_agent.values():
        ids.sort()
    for ids in delegated_ids_by_agent.values():
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
    # Tables
    # ------------------------------------------------------------------
    table_result = await db.execute(select(Table).order_by(Table.name))
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
            )
            for agent in agents_list
        },
        apps={
            str(app.id): serialize_app(app, app_roles_by_app.get(str(app.id), []))
            for app in apps_list
        },
    )

    logger.info(
        f"Generated manifest: {len(manifest.workflows)} workflows, "
        f"{len(manifest.forms)} forms, {len(manifest.agents)} agents, "
        f"{len(manifest.apps)} apps, {len(manifest.integrations)} integrations, "
        f"{len(manifest.configs)} configs, {len(manifest.tables)} tables, "
        f"{len(manifest.events)} events"
    )

    return manifest
