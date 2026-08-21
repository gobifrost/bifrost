"""
Events Router

CRUD operations for event sources, subscriptions, and event history.
Supports webhooks as event sources with adapter-based configuration.
"""

import logging
from datetime import datetime, timedelta, timezone
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import joinedload

from src.core.auth import Context
from src.core.db_deps import DbSession
from src.core.log_safety import log_safe
from shared.event_deliveries import can_retry_delivery_status
from src.models.contracts.events import (
    CreateDeliveryRequest,
    DynamicValuesRequest,
    DynamicValuesResponse,
    EmitEventRequest,
    EmitEventResponse,
    EventDeliveryListResponse,
    EventDeliveryResponse,
    EventListResponse,
    EventResponse,
    EventSourceCreate,
    EventSourceListResponse,
    EventSourceResponse,
    EventSourceUpdate,
    EventSubscriptionCreate,
    EventSubscriptionListResponse,
    EventSubscriptionResponse,
    EventSubscriptionUpdate,
    RetryDeliveryRequest,
    RetryDeliveryResponse,
    ScheduleSourceResponse,
    TopicRegistryEntry,
    TopicsRegistryResponse,
    WebhookAdapterInfo,
    WebhookAdapterListResponse,
    WebhookSourceResponse,
)
from src.models.enums import EventDeliveryStatus, EventSourceType
from src.models.orm.events import (
    Event,
    EventDelivery,
    EventSource,
    EventSubscription,
    ScheduleSource,
    WebhookSource,
)
from src.models.orm.agents import Agent
from src.models.orm.integrations import Integration
from src.models.orm.organizations import Organization
from src.models.orm.workflows import Workflow
from src.repositories.agents import AgentRepository
from src.repositories.events import (
    EventDeliveryRepository,
    EventRepository,
    EventSourceRepository,
    EventSubscriptionRepository,
)
from src.core.cache import get_shared_redis
from src.services.events import emit_event
from src.services.events.registry import CURATED_TOPICS
from src.services.events.validation import validate_topic
from src.services.cron_parser import is_cron_expression_valid
from src.services.audit import emit_audit
from src.services.operation_catalog import operation_route
from src.services.authorization import (
    AuthorizationBoundaryKind,
    CurrentAuthorizationContext,
)
from src.services.repo_sync_writer import RepoSyncWriter
from src.services.solutions.guard import assert_not_solution_managed
from src.services.webhooks.registry import get_adapter_registry
from src.services.workflow_authorization import authorized_workflow_by_id

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/events", tags=["Events"])


def _build_callback_url(source_id: UUID) -> str:
    """Build callback URL path from event source ID."""
    return f"/api/hooks/{source_id}"


async def _get_rate_limited_count(source_id: str) -> int:
    """Read the 24h rate-limit hit counter for a webhook source from Redis."""
    r = await get_shared_redis()
    raw = await r.get(f"bifrost:rate_limit_hits:{source_id}")
    return int(raw) if raw else 0


async def _build_event_source_response(
    source: EventSource,
    db: DbSession,
) -> EventSourceResponse:
    """Build EventSourceResponse from ORM model with computed fields."""
    # Get subscription count
    sub_repo = EventSubscriptionRepository(db)
    subscription_count = await sub_repo.count_by_source(source.id, active_only=True)

    # Get event count in last 24 hours
    event_repo = EventRepository(db)
    event_count_24h = await event_repo.count_by_source(
        source.id,
        since=datetime.now(timezone.utc) - timedelta(hours=24),
    )

    # Build webhook response if applicable
    webhook_response = None
    if source.source_type == EventSourceType.WEBHOOK and source.webhook_source:
        ws = source.webhook_source
        webhook_response = WebhookSourceResponse(
            adapter_name=ws.adapter_name,
            integration_id=ws.integration_id,
            integration_name=ws.integration.name if ws.integration else None,
            config=ws.config or {},
            callback_url=_build_callback_url(source.id),
            external_id=ws.external_id,
            expires_at=ws.expires_at,
            rate_limit_per_minute=ws.rate_limit_per_minute,
            rate_limit_window_seconds=ws.rate_limit_window_seconds,
            rate_limit_enabled=ws.rate_limit_enabled,
            rate_limited_count_24h=await _get_rate_limited_count(str(source.id)),
        )

    # Build schedule response if applicable
    schedule_response = None
    if source.source_type == EventSourceType.SCHEDULE and source.schedule_source:
        ss = source.schedule_source
        schedule_response = ScheduleSourceResponse(
            cron_expression=ss.cron_expression,
            timezone=ss.timezone,
            enabled=ss.enabled,
            overlap_policy=ss.overlap_policy,
        )

    return EventSourceResponse(
        id=source.id,
        name=source.name,
        source_type=source.source_type,
        event_type=source.event_type,
        organization_id=source.organization_id,
        organization_name=source.organization.name if source.organization else None,
        is_active=source.is_active,
        error_message=source.error_message,
        subscription_count=subscription_count,
        event_count_24h=event_count_24h,
        created_by=source.created_by,
        created_at=source.created_at,
        updated_at=source.updated_at,
        webhook=webhook_response,
        schedule=schedule_response,
    )


async def _build_event_subscription_response(
    subscription: EventSubscription,
    db: DbSession,
) -> EventSubscriptionResponse:
    """Build EventSubscriptionResponse from ORM model with computed fields."""
    # Get delivery counts
    delivery_repo = EventDeliveryRepository(db)
    total_count = await delivery_repo.count_by_subscription(subscription.id)
    success_count = await delivery_repo.count_by_subscription(
        subscription.id, status=EventDeliveryStatus.SUCCESS
    )
    failed_count = await delivery_repo.count_by_subscription(
        subscription.id, status=EventDeliveryStatus.FAILED
    )

    return EventSubscriptionResponse(
        id=subscription.id,
        event_source_id=subscription.event_source_id,
        target_type=subscription.target_type,
        workflow_id=subscription.workflow_id,
        agent_id=subscription.agent_id,
        agent_name=subscription.agent.name if subscription.agent else None,
        workflow_name=subscription.workflow.name if subscription.workflow else None,
        event_type=subscription.event_type,
        filter_expression=subscription.filter_expression,
        input_mapping=subscription.input_mapping,
        is_active=subscription.is_active,
        delivery_count=total_count,
        success_count=success_count,
        failed_count=failed_count,
        created_by=subscription.created_by,
        created_at=subscription.created_at,
        updated_at=subscription.updated_at,
    )


async def _validate_target_organization(
    db: DbSession,
    organization_id: UUID | None,
) -> None:
    """Reject references to organizations that do not exist."""

    if organization_id is None:
        return
    if await db.get(Organization, organization_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organization not found",
        )


async def _require_visible_event_organization(
    db: DbSession,
    authorization: CurrentAuthorizationContext,
    organization_id: UUID | None,
) -> None:
    """Admit a Global, exact-Organization, or managed-customer Event resource."""

    if authorization.has_capability("platform.superuser"):
        return
    boundary = authorization.selected_boundary
    if boundary.kind is AuthorizationBoundaryKind.PLATFORM:
        return
    if organization_id is None:
        return
    if (
        boundary.kind is AuthorizationBoundaryKind.ORGANIZATION
        and boundary.organization_id == organization_id
    ):
        return
    if boundary.kind is AuthorizationBoundaryKind.MANAGED_ORGANIZATIONS:
        is_provider = await db.scalar(
            select(Organization.is_provider).where(Organization.id == organization_id)
        )
        if is_provider is False:
            return
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="Event source not found",
    )


def _require_event_mutation_boundary(
    authorization: CurrentAuthorizationContext,
    organization_id: UUID | None,
) -> None:
    """Require an executable exact boundary for an Event mutation."""

    if (
        authorization.selected_boundary.kind
        is AuthorizationBoundaryKind.MANAGED_ORGANIZATIONS
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Select one organization or Global before changing an Event source",
        )
    authorization.require_resource_boundary(organization_id)


def _selected_event_organization(
    authorization: CurrentAuthorizationContext,
) -> UUID | None:
    boundary = authorization.selected_boundary
    if boundary.kind is AuthorizationBoundaryKind.MANAGED_ORGANIZATIONS:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Select one organization or Global before creating an Event source",
        )
    if boundary.kind is AuthorizationBoundaryKind.PLATFORM:
        return None
    return boundary.organization_id


async def _authorized_event_source_by_id(
    db: DbSession,
    authorization: CurrentAuthorizationContext,
    source_id: UUID,
) -> EventSource:
    """Return one Event Source visible in the selected authorization boundary."""

    source_repo = EventSourceRepository(db)
    source = await source_repo.get_by_id_with_details(source_id)
    if not source:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Event source not found",
        )
    await _require_visible_event_organization(db, authorization, source.organization_id)
    return source


async def _authorized_event_by_id(
    db: DbSession,
    authorization: CurrentAuthorizationContext,
    event_id: UUID,
) -> Event:
    """Return one Event visible through its parent Event Source."""

    result = await db.execute(
        select(Event)
        .options(joinedload(Event.event_source))
        .where(Event.id == event_id)
    )
    event = result.unique().scalar_one_or_none()
    if not event:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Event not found",
        )
    source = event.event_source
    if source is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Event source not found",
        )
    await _require_visible_event_organization(db, authorization, source.organization_id)
    return event


def _validate_subscription_scope(
    source_organization_id: UUID | None,
    target_organization_id: UUID | None,
) -> None:
    """Keep subscription targets within the Event Source visibility cascade."""

    if target_organization_id is None:
        return
    if source_organization_id != target_organization_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "Subscription target must be global or belong to the Event "
                "Source organization"
            ),
        )


async def _validate_subscription_target(
    db: DbSession,
    authorization: CurrentAuthorizationContext,
    source: EventSource,
    request: EventSubscriptionCreate,
) -> tuple[UUID, Workflow | Agent]:
    """Resolve and validate the one target selected by a subscription request."""

    if request.target_type == "workflow":
        if request.workflow_id is None or request.agent_id is not None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    "target_type='workflow' requires workflow_id and forbids agent_id"
                ),
            )
        authorization.require("workflows.read")
        target = await authorized_workflow_by_id(db, authorization, request.workflow_id)
        if target.type != "workflow" or not target.is_active or target.is_orphaned:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Subscription target must be an active Workflow",
            )
        target_id = request.workflow_id
    else:
        if request.agent_id is None or request.workflow_id is not None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="target_type='agent' requires agent_id and forbids workflow_id",
            )
        authorization.require("agents.read")
        boundary = authorization.selected_boundary
        agent_org_id = (
            boundary.organization_id
            if boundary.kind is AuthorizationBoundaryKind.ORGANIZATION
            else None
        )
        target = await AgentRepository(
            session=db,
            org_id=agent_org_id,
            user_id=authorization.requester.user_id,
            bypass_resource_roles=authorization.has_capability("platform.superuser"),
            is_external=authorization.requester.is_external,
        ).get_agent_with_access_check(request.agent_id)
        if target is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Agent not found",
            )
        if not target.is_active:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Subscription target must be an active Agent",
            )
        target_id = request.agent_id

    _validate_subscription_scope(source.organization_id, target.organization_id)

    target_column = (
        EventSubscription.workflow_id
        if request.target_type == "workflow"
        else EventSubscription.agent_id
    )
    duplicate = (
        await db.execute(
            select(EventSubscription.id).where(
                EventSubscription.event_source_id == source.id,
                EventSubscription.target_type == request.target_type,
                target_column == target_id,
            )
        )
    ).scalar_one_or_none()
    if duplicate is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An Event Subscription already exists for this target",
        )

    return target_id, target


async def _validate_rescoped_subscriptions(
    source: EventSource,
    organization_id: UUID | None,
) -> None:
    """Reject a source move that would strand an organization-scoped target."""

    for subscription in source.subscriptions:
        target = (
            subscription.agent
            if subscription.target_type == "agent"
            else subscription.workflow
        )
        if target is not None:
            _validate_subscription_scope(organization_id, target.organization_id)


def _validate_schedule_config(cron_expression: str, timezone_name: str) -> None:
    """Reject schedule values the scheduler cannot execute."""

    if not is_cron_expression_valid(cron_expression):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid CRON expression",
        )
    try:
        ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unknown timezone: {timezone_name}",
        ) from exc


# =============================================================================
# Webhook Adapters
# =============================================================================


@router.get(
    "/adapters",
    response_model=WebhookAdapterListResponse,
    summary="List available webhook adapters",
    description="List all available webhook adapters and their configuration schemas (Platform admin only).",
    **operation_route("events.webhook_adapters.list"),
)
async def list_adapters(
    authorization: CurrentAuthorizationContext,
) -> WebhookAdapterListResponse:
    """List all available webhook adapters."""
    authorization.require_operation("events.webhook_adapters.list")
    registry = get_adapter_registry()
    adapters_info = registry.list_adapters()

    return WebhookAdapterListResponse(
        adapters=[WebhookAdapterInfo(**info) for info in adapters_info]
    )


@router.post(
    "/adapters/{adapter_name}/dynamic-values",
    response_model=DynamicValuesResponse,
    summary="Get dynamic values for adapter config",
    description="Fetch dynamic options for a config field with x-dynamic-values (Platform admin only).",
)
async def get_dynamic_values(
    adapter_name: str,
    request: DynamicValuesRequest,
    authorization: CurrentAuthorizationContext,
    db: DbSession,
) -> DynamicValuesResponse:
    """
    Fetch dynamic values for adapter configuration fields.

    This endpoint is called by the UI to populate dropdowns for config fields
    that have x-dynamic-values defined in their config_schema. Similar to
    Power Automate's x-ms-dynamic-values pattern.

    The adapter's get_dynamic_values method is called with:
    - operation: The operation name from x-dynamic-values.operation
    - integration: OAuth integration (if integration_id provided)
    - current_config: Values selected so far (for dependent fields)

    Returns a list of option objects that the UI uses to populate dropdowns.
    """
    from src.models.orm.integrations import Integration

    authorization.require("events.read")

    # Get adapter
    registry = get_adapter_registry()
    adapter = registry.get(adapter_name)

    if not adapter:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown adapter: {adapter_name}",
        )

    # Load integration if provided
    integration = None
    if request.integration_id:
        result = await db.execute(
            select(Integration).where(Integration.id == request.integration_id)
        )
        integration = result.scalar_one_or_none()

        if not integration:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Integration not found",
            )

    # Call adapter's get_dynamic_values
    try:
        items = await adapter.get_dynamic_values(
            operation=request.operation,
            integration=integration,
            current_config=request.current_config,
        )
        return DynamicValuesResponse(items=items)

    except NotImplementedError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except Exception as e:
        logger.error(
            f"Failed to get dynamic values for {log_safe(adapter_name)}/{log_safe(request.operation)}: {log_safe(e)}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch dynamic values: {e}",
        )


# =============================================================================
# Event Sources
# =============================================================================


@router.get(
    "/sources",
    response_model=EventSourceListResponse,
    summary="List event sources",
    description="List all event sources (Platform admin only).",
    **operation_route("events.sources.list"),
)
async def list_sources(
    authorization: CurrentAuthorizationContext,
    db: DbSession,
    source_type: EventSourceType | None = Query(
        None, description="Filter by source type"
    ),
    organization_id: UUID | None = Query(None, description="Filter by organization"),
    scope: str | None = Query(
        None, description="Filter scope: 'global' for global-only, omit for all"
    ),
    limit: int = Query(100, ge=1, le=1000, description="Max results"),
    offset: int = Query(0, ge=0, description="Skip results"),
) -> EventSourceListResponse:
    """
    List event sources (Platform admin only).

    Filtering:
    - No scope/organization_id: show ALL sources
    - scope=global: show only global (no org) sources
    - organization_id=<uuid>: show that org's sources + global
    """
    authorization.require_operation("events.sources.list")
    repo = EventSourceRepository(db)
    boundary = authorization.selected_boundary

    if scope == "global":
        # Global-only: filter to org_id IS NULL
        sources = await repo.get_by_organization(
            organization_id=None,
            source_type=source_type,
            include_global=True,
            limit=limit,
            offset=offset,
        )
        total = await repo.count_by_organization(
            organization_id=None,
            source_type=source_type,
            include_global=True,
        )
    elif boundary.kind is AuthorizationBoundaryKind.MANAGED_ORGANIZATIONS:
        if organization_id is not None:
            await _require_visible_event_organization(
                db, authorization, organization_id
            )
            sources = await repo.get_by_organization(
                organization_id=organization_id,
                source_type=source_type,
                include_global=True,
                limit=limit,
                offset=offset,
            )
            total = await repo.count_by_organization(
                organization_id=organization_id,
                source_type=source_type,
                include_global=True,
            )
        else:
            organization_ids = set(
                (
                    await db.execute(
                        select(Organization.id).where(
                            Organization.is_provider.is_(False)
                        )
                    )
                ).scalars()
            )
            sources = await repo.get_by_organizations(
                organization_ids,
                source_type=source_type,
                include_global=True,
                limit=limit,
                offset=offset,
            )
            total = await repo.count_by_organizations(
                organization_ids,
                source_type=source_type,
                include_global=True,
            )
    elif boundary.kind is AuthorizationBoundaryKind.ORGANIZATION:
        selected_org_id = boundary.organization_id
        if organization_id is not None and organization_id != selected_org_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="The organization filter does not match the selected boundary",
            )
        sources = await repo.get_by_organization(
            organization_id=selected_org_id,
            source_type=source_type,
            include_global=True,
            limit=limit,
            offset=offset,
        )
        total = await repo.count_by_organization(
            organization_id=selected_org_id,
            source_type=source_type,
            include_global=True,
        )
    elif organization_id:
        # Specific org + global
        sources = await repo.get_by_organization(
            organization_id=organization_id,
            source_type=source_type,
            include_global=True,
            limit=limit,
            offset=offset,
        )
        total = await repo.count_by_organization(
            organization_id=organization_id,
            source_type=source_type,
            include_global=True,
        )
    else:
        # No filter: show everything
        sources = await repo.get_all_sources(
            source_type=source_type,
            limit=limit,
            offset=offset,
        )
        total = await repo.count_all_sources(
            source_type=source_type,
        )

    items = [await _build_event_source_response(s, db) for s in sources]

    return EventSourceListResponse(items=items, total=total)


@router.post(
    "/sources",
    response_model=EventSourceResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create event source",
    description="Create a new event source (Platform admin only).",
    **operation_route("events.sources.create"),
)
async def create_source(
    request: EventSourceCreate,
    authorization: CurrentAuthorizationContext,
    db: DbSession,
) -> EventSourceResponse:
    """
    Create a new event source.

    For webhooks, this will:
    1. Generate a unique callback URL
    2. Call the adapter's subscribe method (if needed)
    3. Store the webhook configuration
    """
    authorization.require_operation("events.sources.create")
    now = datetime.now(timezone.utc)

    # Validate topic sources
    if request.source_type == EventSourceType.TOPIC:
        if not request.event_type:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="event_type is required for topic sources",
            )
        try:
            validate_topic(request.event_type)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            )
        if request.webhook is not None or request.schedule is not None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Topic sources cannot include webhook or schedule configuration",
            )
    elif request.event_type is not None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="event_type is only valid for topic sources",
        )
    if request.source_type == EventSourceType.WEBHOOK and request.schedule is not None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Webhook sources cannot include schedule configuration",
        )
    if request.source_type == EventSourceType.SCHEDULE and request.webhook is not None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Schedule sources cannot include webhook configuration",
        )

    target_org_id = (
        request.organization_id
        if "organization_id" in request.model_fields_set
        else _selected_event_organization(authorization)
    )
    await _validate_target_organization(db, target_org_id)
    _require_event_mutation_boundary(authorization, target_org_id)

    # Create base event source
    source = EventSource(
        name=request.name,
        source_type=request.source_type,
        event_type=request.event_type
        if request.source_type == EventSourceType.TOPIC
        else None,
        organization_id=target_org_id,
        is_active=True,
        created_by=authorization.requester.email,
        created_at=now,
        updated_at=now,
    )
    db.add(source)
    await db.flush()

    # Handle webhook-specific configuration
    if request.source_type == EventSourceType.WEBHOOK:
        if not request.webhook:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Webhook configuration required for webhook source type",
            )

        # Get adapter
        adapter_name = request.webhook.adapter_name
        adapter = get_adapter_registry().get(adapter_name)
        if not adapter:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unknown adapter: {adapter_name}",
            )

        # Validate integration if required
        integration = None
        if request.webhook.integration_id:
            integration = await db.get(Integration, request.webhook.integration_id)
            if integration is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Integration not found",
                )
        if adapter.requires_integration and integration is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Adapter '{adapter_name}' requires integration",
            )

        # Create webhook source record
        webhook_source = WebhookSource(
            event_source_id=source.id,
            adapter_name=adapter_name,
            integration_id=request.webhook.integration_id,
            config=request.webhook.config,
            rate_limit_per_minute=request.webhook.rate_limit_per_minute,
            rate_limit_window_seconds=request.webhook.rate_limit_window_seconds,
            rate_limit_enabled=request.webhook.rate_limit_enabled,
            created_at=now,
            updated_at=now,
        )

        # Call adapter subscribe (for external subscriptions)
        # Note: callback_url is a path - client will combine with origin
        callback_url = _build_callback_url(source.id)
        try:
            result = await adapter.subscribe(
                callback_url=callback_url,
                config=request.webhook.config,
                integration=integration,
            )

            webhook_source.external_id = result.external_id
            webhook_source.state = result.state
            webhook_source.expires_at = result.expires_at

        except Exception as e:
            logger.error(f"Failed to subscribe webhook: {e}", exc_info=True)
            source.error_message = str(e)

        db.add(webhook_source)
        await db.flush()

    # Handle schedule-specific configuration
    if request.source_type == EventSourceType.SCHEDULE:
        if not request.schedule:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Schedule configuration required for schedule source type",
            )
        _validate_schedule_config(
            request.schedule.cron_expression,
            request.schedule.timezone,
        )

        # Create schedule source record
        schedule_source = ScheduleSource(
            event_source_id=source.id,
            cron_expression=request.schedule.cron_expression,
            timezone=request.schedule.timezone,
            enabled=request.schedule.enabled,
            overlap_policy=request.schedule.overlap_policy,
            created_at=now,
            updated_at=now,
        )
        db.add(schedule_source)
        await db.flush()

    # Reload with relationships
    result = await db.execute(
        select(EventSource)
        .options(
            joinedload(EventSource.webhook_source).joinedload(
                WebhookSource.integration
            ),
            joinedload(EventSource.schedule_source),
            joinedload(EventSource.organization),
        )
        .where(EventSource.id == source.id)
    )
    source = result.unique().scalar_one()

    logger.info(f"Created event source {source.id}: {source.name}")

    await emit_audit(
        db,
        "event_source.create",
        resource_type="event_source",
        resource_id=source.id,
        details={
            "name": source.name,
            "source_type": source.source_type.value,
            "organization_id": (
                str(source.organization_id) if source.organization_id else None
            ),
        },
    )
    await RepoSyncWriter(db).regenerate_manifest()

    return await _build_event_source_response(source, db)


@router.get(
    "/sources/{source_id}",
    response_model=EventSourceResponse,
    summary="Get event source",
    description="Get a specific event source by ID (Platform admin only).",
    **operation_route("events.sources.get"),
)
async def get_source(
    source_id: UUID,
    authorization: CurrentAuthorizationContext,
    db: DbSession,
) -> EventSourceResponse:
    """Get event source by ID (Platform admin only)."""
    authorization.require_operation("events.sources.get")
    repo = EventSourceRepository(db)
    source = await repo.get_by_id_with_details(source_id)

    if not source:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Event source not found",
        )
    await _require_visible_event_organization(db, authorization, source.organization_id)

    return await _build_event_source_response(source, db)


@router.patch(
    "/sources/{source_id}",
    response_model=EventSourceResponse,
    summary="Update event source",
    description="Update an event source (Platform admin only).",
    **operation_route("events.sources.update"),
)
async def update_source(
    source_id: UUID,
    request: EventSourceUpdate,
    authorization: CurrentAuthorizationContext,
    db: DbSession,
) -> EventSourceResponse:
    """Update an event source."""
    authorization.require_operation("events.sources.update")
    repo = EventSourceRepository(db)
    source = await repo.get_by_id_with_details(source_id)

    if not source:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Event source not found",
        )
    _require_event_mutation_boundary(authorization, source.organization_id)

    # Solution-managed triggers are deploy-owned and read-only on the platform
    # (the deploy path is the only writer). Refuse with a clean 409 before
    # mutating, rather than letting the before_flush backstop raise a 500.
    assert_not_solution_managed(source)

    if request.webhook is not None and source.source_type != EventSourceType.WEBHOOK:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Webhook configuration is only valid for webhook sources",
        )
    if request.schedule is not None and source.source_type != EventSourceType.SCHEDULE:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Schedule configuration is only valid for schedule sources",
        )

    # Update basic fields
    if request.name is not None:
        source.name = request.name
    if request.is_active is not None:
        source.is_active = request.is_active
        # Clear error message when reactivating
        if request.is_active:
            source.error_message = None

    if "organization_id" in request.model_fields_set:
        await _validate_target_organization(db, request.organization_id)
        _require_event_mutation_boundary(authorization, request.organization_id)
        await _validate_rescoped_subscriptions(source, request.organization_id)
        source.organization_id = request.organization_id

    source.updated_at = datetime.now(timezone.utc)

    # Update webhook-specific fields
    if request.webhook and source.webhook_source:
        ws = source.webhook_source
        webhook_fields = request.webhook.model_fields_set
        if "config" in webhook_fields:
            ws.config = request.webhook.config
            # Sync secret to state (adapter reads from state, not config)
            if request.webhook.config.get("secret"):
                new_state = dict(ws.state or {})
                new_state["secret"] = request.webhook.config["secret"]
                ws.state = new_state
        desired_adapter_name = (
            request.webhook.adapter_name
            if "adapter_name" in webhook_fields
            else ws.adapter_name
        )
        desired_integration_id = (
            request.webhook.integration_id
            if "integration_id" in webhook_fields
            else ws.integration_id
        )
        adapter = get_adapter_registry().get(desired_adapter_name)
        if adapter is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Unknown adapter: {desired_adapter_name}",
            )
        integration = None
        if desired_integration_id is not None:
            integration = await db.get(Integration, desired_integration_id)
            if integration is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Integration not found",
                )
        if adapter.requires_integration and integration is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Adapter '{desired_adapter_name}' requires integration",
            )
        if webhook_fields.intersection({"adapter_name", "integration_id", "config"}):
            old_adapter = get_adapter_registry().get(ws.adapter_name)
            if old_adapter is not None:
                try:
                    await old_adapter.unsubscribe(
                        external_id=ws.external_id,
                        state=ws.state or {},
                        integration=ws.integration,
                    )
                except Exception as exc:
                    logger.warning(
                        "Failed to unsubscribe webhook during update: %s", exc
                    )
            try:
                subscribe_result = await adapter.subscribe(
                    callback_url=_build_callback_url(source.id),
                    config=ws.config or {},
                    integration=integration,
                )
                ws.external_id = subscribe_result.external_id
                ws.state = subscribe_result.state
                ws.expires_at = subscribe_result.expires_at
                source.error_message = None
            except Exception as exc:
                logger.error(
                    "Failed to update webhook subscription: %s", exc, exc_info=True
                )
                source.error_message = str(exc)
        ws.adapter_name = desired_adapter_name
        ws.integration_id = desired_integration_id
        if "rate_limit_per_minute" in request.webhook.model_fields_set:
            ws.rate_limit_per_minute = request.webhook.rate_limit_per_minute
        if "rate_limit_window_seconds" in request.webhook.model_fields_set:
            ws.rate_limit_window_seconds = request.webhook.rate_limit_window_seconds
        if "rate_limit_enabled" in request.webhook.model_fields_set:
            ws.rate_limit_enabled = request.webhook.rate_limit_enabled
        ws.updated_at = datetime.now(timezone.utc)

    # Update schedule-specific fields
    if request.schedule and source.schedule_source:
        ss = source.schedule_source
        next_cron = request.schedule.cron_expression or ss.cron_expression
        next_timezone = request.schedule.timezone or ss.timezone
        _validate_schedule_config(next_cron, next_timezone)
        if request.schedule.cron_expression is not None:
            ss.cron_expression = request.schedule.cron_expression
        if request.schedule.timezone is not None:
            ss.timezone = request.schedule.timezone
        if request.schedule.enabled is not None:
            ss.enabled = request.schedule.enabled
        if request.schedule.overlap_policy is not None:
            ss.overlap_policy = request.schedule.overlap_policy
        ss.updated_at = datetime.now(timezone.utc)

    await db.flush()

    # Reload with relationships
    result = await db.execute(
        select(EventSource)
        .options(
            joinedload(EventSource.webhook_source).joinedload(
                WebhookSource.integration
            ),
            joinedload(EventSource.schedule_source),
            joinedload(EventSource.organization),
        )
        .where(EventSource.id == source_id)
    )
    source = result.unique().scalar_one()

    logger.info(f"Updated event source {log_safe(source_id)}")

    await emit_audit(
        db,
        "event_source.update",
        resource_type="event_source",
        resource_id=source.id,
        details={
            "name": source.name,
            "fields": sorted(request.model_fields_set),
        },
    )
    await RepoSyncWriter(db).regenerate_manifest()

    return await _build_event_source_response(source, db)


@router.delete(
    "/sources/{source_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete event source",
    description="Permanently delete an event source and all its subscriptions, events, and deliveries (Platform admin only).",
    **operation_route("events.sources.delete"),
)
async def delete_source(
    source_id: UUID,
    authorization: CurrentAuthorizationContext,
    db: DbSession,
) -> None:
    """
    Permanently delete an event source.

    This will:
    1. Call adapter unsubscribe (for external subscriptions)
    2. Delete the source and cascade to subscriptions, events, and deliveries
    """
    authorization.require_operation("events.sources.delete")
    repo = EventSourceRepository(db)
    source = await repo.get_by_id_with_details(source_id)

    if not source:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Event source not found",
        )
    _require_event_mutation_boundary(authorization, source.organization_id)

    # Solution-managed triggers are deploy-owned — uninstall removes them, not
    # this endpoint. Refuse with a clean 409 (the DELETE cascade would otherwise
    # strip a managed source's deploy-owned rows outside deploy).
    assert_not_solution_managed(source)

    # Call adapter unsubscribe for webhooks
    if source.source_type == EventSourceType.WEBHOOK and source.webhook_source:
        ws = source.webhook_source
        adapter = get_adapter_registry().get(ws.adapter_name)
        if adapter:
            try:
                await adapter.unsubscribe(
                    external_id=ws.external_id,
                    state=ws.state or {},
                    integration=ws.integration,
                )
            except Exception as e:
                logger.warning(f"Failed to unsubscribe webhook: {e}")

    source_name = source.name
    await db.delete(source)
    await db.flush()

    logger.info(f"Deleted event source {log_safe(source_id)}")
    await emit_audit(
        db,
        "event_source.delete",
        resource_type="event_source",
        resource_id=source_id,
        details={"name": source_name},
    )
    await RepoSyncWriter(db).regenerate_manifest()


# =============================================================================
# Event Subscriptions
# =============================================================================


@router.get(
    "/sources/{source_id}/subscriptions",
    response_model=EventSubscriptionListResponse,
    summary="List subscriptions",
    description="List subscriptions for an event source (Platform admin only).",
    **operation_route("events.subscriptions.list"),
)
async def list_subscriptions(
    source_id: UUID,
    authorization: CurrentAuthorizationContext,
    db: DbSession,
    limit: int = Query(100, ge=1, le=1000, description="Max results"),
    offset: int = Query(0, ge=0, description="Skip results"),
) -> EventSubscriptionListResponse:
    """List subscriptions for an event source (Platform admin only)."""
    authorization.require_operation("events.subscriptions.list")
    # Verify source exists
    source_repo = EventSourceRepository(db)
    source = await source_repo.get_by_id(source_id)

    if not source:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Event source not found",
        )
    await _require_visible_event_organization(db, authorization, source.organization_id)

    # Get subscriptions
    sub_repo = EventSubscriptionRepository(db)
    subscriptions = await sub_repo.get_by_source(source_id, active_only=False)

    total = await sub_repo.count_by_source(source_id, active_only=False)

    items = [await _build_event_subscription_response(s, db) for s in subscriptions]

    return EventSubscriptionListResponse(items=items, total=total)


@router.get(
    "/sources/{source_id}/subscriptions/{subscription_id}",
    response_model=EventSubscriptionResponse,
    summary="Get subscription",
    description="Get one subscription for an event source (Platform admin only).",
    **operation_route("events.subscriptions.get"),
)
async def get_subscription(
    source_id: UUID,
    subscription_id: UUID,
    authorization: CurrentAuthorizationContext,
    db: DbSession,
) -> EventSubscriptionResponse:
    """Get one Event Subscription under its parent Event Source."""

    authorization.require_operation("events.subscriptions.get")
    source = await EventSourceRepository(db).get_by_id(source_id)
    if source is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Event source not found",
        )
    await _require_visible_event_organization(db, authorization, source.organization_id)
    subscription = (
        (
            await db.execute(
                select(EventSubscription)
                .options(
                    joinedload(EventSubscription.workflow),
                    joinedload(EventSubscription.agent),
                )
                .where(
                    EventSubscription.id == subscription_id,
                    EventSubscription.event_source_id == source_id,
                )
            )
        )
        .unique()
        .scalar_one_or_none()
    )
    if subscription is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Subscription not found",
        )
    return await _build_event_subscription_response(subscription, db)


@router.post(
    "/sources/{source_id}/subscriptions",
    response_model=EventSubscriptionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create subscription",
    description="Create a subscription to an event source (Platform admin only).",
    **operation_route("events.subscriptions.create"),
)
async def create_subscription(
    source_id: UUID,
    request: EventSubscriptionCreate,
    authorization: CurrentAuthorizationContext,
    db: DbSession,
) -> EventSubscriptionResponse:
    """Create a subscription to an event source."""
    authorization.require_operation("events.subscriptions.create")
    now = datetime.now(timezone.utc)

    # Verify source exists
    source_repo = EventSourceRepository(db)
    source = await source_repo.get_by_id_with_details(source_id)

    if not source:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Event source not found",
        )
    _require_event_mutation_boundary(authorization, source.organization_id)

    assert_not_solution_managed(source)
    await _validate_subscription_target(db, authorization, source, request)

    subscription = EventSubscription(
        event_source_id=source_id,
        target_type=request.target_type,
        workflow_id=request.workflow_id,
        agent_id=request.agent_id,
        event_type=request.event_type,
        filter_expression=request.filter_expression,
        input_mapping=request.input_mapping,
        is_active=True,
        created_by=authorization.requester.email,
        created_at=now,
        updated_at=now,
    )
    db.add(subscription)
    await db.flush()

    # Reload with workflow and agent relationships
    result = await db.execute(
        select(EventSubscription)
        .options(
            joinedload(EventSubscription.workflow), joinedload(EventSubscription.agent)
        )
        .where(EventSubscription.id == subscription.id)
    )
    subscription = result.unique().scalar_one()

    logger.info(
        f"Created subscription {subscription.id} for source {log_safe(source_id)}"
    )

    await emit_audit(
        db,
        "event_subscription.create",
        resource_type="event_subscription",
        resource_id=subscription.id,
        details={
            "event_source_id": str(source_id),
            "target_type": subscription.target_type,
            "target_id": str(subscription.agent_id or subscription.workflow_id),
        },
    )
    await RepoSyncWriter(db).regenerate_manifest()

    return await _build_event_subscription_response(subscription, db)


@router.patch(
    "/sources/{source_id}/subscriptions/{subscription_id}",
    response_model=EventSubscriptionResponse,
    summary="Update subscription",
    description="Update an event subscription (Platform admin only).",
    **operation_route("events.subscriptions.update"),
)
async def update_subscription(
    source_id: UUID,
    subscription_id: UUID,
    request: EventSubscriptionUpdate,
    authorization: CurrentAuthorizationContext,
    db: DbSession,
) -> EventSubscriptionResponse:
    """Update an event subscription."""
    authorization.require_operation("events.subscriptions.update")
    # Verify source exists
    source_repo = EventSourceRepository(db)
    source = await source_repo.get_by_id(source_id)

    if not source:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Event source not found",
        )
    _require_event_mutation_boundary(authorization, source.organization_id)
    assert_not_solution_managed(source)

    # Get subscription
    result = await db.execute(
        select(EventSubscription)
        .options(
            joinedload(EventSubscription.workflow),
            joinedload(EventSubscription.agent),
        )
        .where(
            EventSubscription.id == subscription_id,
            EventSubscription.event_source_id == source_id,
        )
    )
    subscription = result.unique().scalar_one_or_none()

    if not subscription:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Subscription not found",
        )

    # Solution-managed subscriptions are deploy-owned, read-only here.
    assert_not_solution_managed(subscription)

    # Update fields - use model_fields_set to distinguish "not provided" from "set to null"
    if "event_type" in request.model_fields_set:
        subscription.event_type = request.event_type
    if "filter_expression" in request.model_fields_set:
        subscription.filter_expression = request.filter_expression
    if "is_active" in request.model_fields_set and request.is_active is not None:
        subscription.is_active = request.is_active
    if "input_mapping" in request.model_fields_set:
        subscription.input_mapping = request.input_mapping

    subscription.updated_at = datetime.now(timezone.utc)

    await db.flush()

    logger.info(f"Updated subscription {log_safe(subscription_id)}")

    await emit_audit(
        db,
        "event_subscription.update",
        resource_type="event_subscription",
        resource_id=subscription.id,
        details={
            "event_source_id": str(source_id),
            "fields": sorted(request.model_fields_set),
        },
    )
    await RepoSyncWriter(db).regenerate_manifest()

    return await _build_event_subscription_response(subscription, db)


@router.delete(
    "/sources/{source_id}/subscriptions/{subscription_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete subscription",
    description="Permanently delete an event subscription (Platform admin only).",
    **operation_route("events.subscriptions.delete"),
)
async def delete_subscription(
    source_id: UUID,
    subscription_id: UUID,
    authorization: CurrentAuthorizationContext,
    db: DbSession,
) -> None:
    """Permanently delete an event subscription."""
    authorization.require_operation("events.subscriptions.delete")
    # Verify source exists
    source_repo = EventSourceRepository(db)
    source = await source_repo.get_by_id(source_id)

    if not source:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Event source not found",
        )
    _require_event_mutation_boundary(authorization, source.organization_id)
    assert_not_solution_managed(source)

    # Get subscription
    result = await db.execute(
        select(EventSubscription).where(
            EventSubscription.id == subscription_id,
            EventSubscription.event_source_id == source_id,
        )
    )
    subscription = result.scalar_one_or_none()

    if not subscription:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Subscription not found",
        )

    # Solution-managed subscriptions are deploy-owned, read-only here.
    assert_not_solution_managed(subscription)

    target_type = subscription.target_type
    await db.delete(subscription)
    await db.flush()

    logger.info(f"Deleted subscription {log_safe(subscription_id)}")
    await emit_audit(
        db,
        "event_subscription.delete",
        resource_type="event_subscription",
        resource_id=subscription_id,
        details={
            "event_source_id": str(source_id),
            "target_type": target_type,
        },
    )
    await RepoSyncWriter(db).regenerate_manifest()


# =============================================================================
# Events
# =============================================================================


@router.get(
    "/sources/{source_id}/events",
    response_model=EventListResponse,
    summary="List events",
    description="List events for an event source with optional filters (Platform admin only).",
)
async def list_events(
    source_id: UUID,
    authorization: CurrentAuthorizationContext,
    db: DbSession,
    event_status: str | None = Query(
        None,
        alias="status",
        description="Filter by status (received, processing, completed, failed)",
    ),
    event_type: str | None = Query(None, description="Filter by event type"),
    since: datetime | None = Query(
        None, description="Filter events received after this time"
    ),
    until: datetime | None = Query(
        None, description="Filter events received before this time"
    ),
    limit: int = Query(100, ge=1, le=1000, description="Max results"),
    offset: int = Query(0, ge=0, description="Skip results"),
) -> EventListResponse:
    """List events for an event source with optional filters (Platform admin only)."""
    from src.models.enums import EventStatus

    authorization.require("events.read")
    source = await _authorized_event_source_by_id(db, authorization, source_id)

    # Parse status filter
    status_enum = None
    if event_status:
        try:
            status_enum = EventStatus(event_status)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid status: {event_status}. Valid values: received, processing, completed, failed",
            )

    # Strip timezone info from since/until - DB column is TIMESTAMP WITHOUT TIME ZONE
    since_naive = since.replace(tzinfo=None) if since and since.tzinfo else since
    until_naive = until.replace(tzinfo=None) if until and until.tzinfo else until

    # Get events with filters
    event_repo = EventRepository(db)
    events = await event_repo.get_by_source(
        source_id,
        status=status_enum,
        event_type=event_type,
        since=since_naive,
        until=until_naive,
        limit=limit,
        offset=offset,
    )
    total = await event_repo.count_by_source(
        source_id,
        status=status_enum,
        event_type=event_type,
        since=since_naive,
        until=until_naive,
    )

    items = []
    for event in events:
        # Get delivery counts
        delivery_repo = EventDeliveryRepository(db)
        deliveries = await delivery_repo.get_by_event(event.id)
        total_deliveries = len(deliveries)
        success_count = sum(
            1 for d in deliveries if d.status == EventDeliveryStatus.SUCCESS
        )
        failed_count = sum(
            1 for d in deliveries if d.status == EventDeliveryStatus.FAILED
        )

        items.append(
            EventResponse(
                id=event.id,
                event_source_id=event.event_source_id,
                event_source_name=source.name,
                event_type=event.event_type,
                received_at=event.received_at,
                headers=event.headers,
                data=event.data,
                source_ip=event.source_ip,
                status=event.status,
                delivery_count=total_deliveries,
                success_count=success_count,
                failed_count=failed_count,
                created_at=event.created_at,
            )
        )

    return EventListResponse(items=items, total=total)


@router.post(
    "/emit",
    response_model=EmitEventResponse,
    summary="Emit a topic event",
    description="Publish an event to a topic. All subscriptions on the matching topic source will be triggered.",
)
async def emit_topic_event(
    request: EmitEventRequest,
    ctx: Context,
    authorization: CurrentAuthorizationContext,
) -> EmitEventResponse:
    """Emit a topic event and return the event_id and subscriber count."""
    authorization.require("events.readwrite")
    try:
        validate_topic(request.topic)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )

    organization_id: UUID | None = None
    if request.scope:
        if request.scope == "GLOBAL":
            organization_id = None
        else:
            try:
                organization_id = UUID(request.scope)
            except ValueError:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid scope: must be a UUID or 'GLOBAL', got '{request.scope}'",
                )
        _require_event_mutation_boundary(authorization, organization_id)
    else:
        organization_id = _selected_event_organization(authorization)

    solution_id: UUID | None = None
    requested_solution = request.solution or ctx.solution_id
    if requested_solution:
        try:
            solution_id = UUID(str(requested_solution))
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid solution: must be a UUID, got '{requested_solution}'",
            )

    event_id, subscribers_notified = await emit_event(
        request.topic,
        request.data,
        organization_id=organization_id,
        solution_id=solution_id,
        triggered_by=str(authorization.effective_actor.user_id),
    )

    return EmitEventResponse(
        event_id=str(event_id),
        subscribers_notified=subscribers_notified,
    )


@router.get(
    "/topics",
    response_model=TopicsRegistryResponse,
    summary="List available topics",
    description="Returns curated topic suggestions and topics currently in use.",
)
async def list_topics(
    db: DbSession,
) -> TopicsRegistryResponse:
    """Return the curated topic registry plus topics currently in use."""
    source_repo = EventSourceRepository(db)
    in_use = await source_repo.get_distinct_topic_types()
    return TopicsRegistryResponse(
        curated=[TopicRegistryEntry(**entry) for entry in CURATED_TOPICS],
        in_use=in_use,
    )


@router.get(
    "/{event_id}",
    response_model=EventResponse,
    summary="Get event",
    description="Get a specific event by ID (Platform admin only).",
)
async def get_event(
    event_id: UUID,
    authorization: CurrentAuthorizationContext,
    db: DbSession,
) -> EventResponse:
    """Get event by ID (Platform admin only)."""
    authorization.require("events.read")
    event = await _authorized_event_by_id(db, authorization, event_id)

    source = event.event_source

    # Get delivery counts
    delivery_repo = EventDeliveryRepository(db)
    deliveries = await delivery_repo.get_by_event(event_id)
    total_deliveries = len(deliveries)
    success_count = sum(
        1 for d in deliveries if d.status == EventDeliveryStatus.SUCCESS
    )
    failed_count = sum(1 for d in deliveries if d.status == EventDeliveryStatus.FAILED)

    return EventResponse(
        id=event.id,
        event_source_id=event.event_source_id,
        event_source_name=source.name if source else None,
        event_type=event.event_type,
        received_at=event.received_at,
        headers=event.headers,
        data=event.data,
        source_ip=event.source_ip,
        status=event.status,
        delivery_count=total_deliveries,
        success_count=success_count,
        failed_count=failed_count,
        created_at=event.created_at,
    )


# =============================================================================
# Event Deliveries
# =============================================================================


@router.get(
    "/{event_id}/deliveries",
    response_model=EventDeliveryListResponse,
    summary="List deliveries",
    description="List deliveries for an event, including undelivered subscriptions (Platform admin only).",
)
async def list_deliveries(
    event_id: UUID,
    authorization: CurrentAuthorizationContext,
    db: DbSession,
) -> EventDeliveryListResponse:
    """
    List deliveries for an event (Platform admin only).

    Includes both existing deliveries AND subscriptions that were added after
    the event arrived (shown as "not_delivered" status with null id).
    """
    authorization.require("events.read")
    event = await _authorized_event_by_id(db, authorization, event_id)

    # Get existing deliveries
    delivery_repo = EventDeliveryRepository(db)
    deliveries = await delivery_repo.get_by_event(event_id)

    # Build set of subscription IDs that already have deliveries
    delivered_subscription_ids = {d.event_subscription_id for d in deliveries}

    items = []

    # Add existing deliveries
    for delivery in deliveries:
        sub = delivery.subscription
        target_type = (sub.target_type or "workflow") if sub else "workflow"
        agent = sub.agent if sub else None
        items.append(
            EventDeliveryResponse(
                id=delivery.id,
                event_id=delivery.event_id,
                event_subscription_id=delivery.event_subscription_id,
                workflow_id=delivery.workflow_id,
                workflow_name=delivery.workflow.name if delivery.workflow else None,
                target_type=target_type,
                agent_id=sub.agent_id if sub else None,
                agent_name=agent.name if agent else None,
                execution_id=delivery.execution_id,
                agent_run_id=delivery.agent_run_id,
                status=delivery.status.value
                if hasattr(delivery.status, "value")
                else delivery.status,
                error_message=delivery.error_message,
                attempt_count=delivery.attempt_count,
                next_retry_at=delivery.next_retry_at,
                completed_at=delivery.completed_at,
                created_at=delivery.created_at,
            )
        )

    # Get all active subscriptions for this event source that match the event type
    subscription_repo = EventSubscriptionRepository(db)
    all_subscriptions = await subscription_repo.get_active_for_event(
        source_id=event.event_source_id,
        event_type=event.event_type,
    )

    # Add "not_delivered" entries for subscriptions without deliveries
    for subscription in all_subscriptions:
        if subscription.id not in delivered_subscription_ids:
            sub_target_type = subscription.target_type or "workflow"
            sub_agent = subscription.agent
            items.append(
                EventDeliveryResponse(
                    id=None,  # No delivery exists
                    event_id=event_id,
                    event_subscription_id=subscription.id,
                    workflow_id=subscription.workflow_id,
                    workflow_name=subscription.workflow.name
                    if subscription.workflow
                    else None,
                    target_type=sub_target_type,
                    agent_id=subscription.agent_id,
                    agent_name=sub_agent.name if sub_agent else None,
                    execution_id=None,
                    agent_run_id=None,
                    status="not_delivered",
                    error_message=None,
                    attempt_count=0,
                    next_retry_at=None,
                    completed_at=None,
                    created_at=None,  # No delivery exists
                )
            )

    return EventDeliveryListResponse(items=items, total=len(items))


@router.post(
    "/{event_id}/deliveries",
    response_model=EventDeliveryResponse,
    summary="Create delivery",
    description="Create a delivery to send an existing event to a subscription (Platform admin only).",
    status_code=status.HTTP_201_CREATED,
)
async def create_delivery(
    event_id: UUID,
    request: CreateDeliveryRequest,
    authorization: CurrentAuthorizationContext,
    db: DbSession,
) -> EventDeliveryResponse:
    """
    Create a delivery for an existing event and subscription.

    This allows retroactively sending an event to a subscription that was
    added after the event originally arrived.
    """
    import uuid
    from src.services.events.processor import EventProcessor

    authorization.require("events.readwrite")
    event = await _authorized_event_by_id(db, authorization, event_id)
    if event.event_source is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Event source not found",
        )
    _require_event_mutation_boundary(authorization, event.event_source.organization_id)

    # Get subscription and verify it belongs to the same event source
    result = await db.execute(
        select(EventSubscription)
        .options(joinedload(EventSubscription.workflow))
        .where(EventSubscription.id == request.subscription_id)
    )
    subscription = result.unique().scalar_one_or_none()

    if not subscription:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Subscription not found",
        )

    if subscription.event_source_id != event.event_source_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Subscription does not belong to this event's source",
        )

    # Check if delivery already exists
    existing = await db.execute(
        select(EventDelivery).where(
            EventDelivery.event_id == event_id,
            EventDelivery.event_subscription_id == request.subscription_id,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Delivery already exists for this event and subscription",
        )

    # Create delivery record
    delivery = EventDelivery(
        id=uuid.uuid4(),
        event_id=event_id,
        event_subscription_id=subscription.id,
        workflow_id=subscription.workflow_id,
        status=EventDeliveryStatus.PENDING,
    )
    db.add(delivery)
    await db.flush()

    # Queue the execution
    processor = EventProcessor(db)
    try:
        await processor.queue_event_deliveries(event_id)
    except Exception as e:
        logger.error(f"Failed to queue delivery: {e}", exc_info=True)
        delivery.status = EventDeliveryStatus.FAILED
        delivery.error_message = str(e)
        await db.flush()

    logger.info(
        f"Created delivery {delivery.id} for event {log_safe(event_id)} subscription {subscription.id}"
    )

    return EventDeliveryResponse(
        id=delivery.id,
        event_id=delivery.event_id,
        event_subscription_id=delivery.event_subscription_id,
        workflow_id=delivery.workflow_id,
        workflow_name=subscription.workflow.name if subscription.workflow else None,
        execution_id=delivery.execution_id,
        status=delivery.status.value
        if hasattr(delivery.status, "value")
        else delivery.status,
        error_message=delivery.error_message,
        attempt_count=delivery.attempt_count,
        next_retry_at=delivery.next_retry_at,
        completed_at=delivery.completed_at,
        created_at=delivery.created_at,
    )


@router.post(
    "/deliveries/{delivery_id}/retry",
    response_model=RetryDeliveryResponse,
    summary="Retry delivery",
    description="Retry a failed delivery (Platform admin only).",
)
async def retry_delivery(
    delivery_id: UUID,
    db: DbSession,
    authorization: CurrentAuthorizationContext,
    request: RetryDeliveryRequest | None = None,
) -> RetryDeliveryResponse:
    """
    Retry a failed delivery.

    This will create a new workflow execution for the event.
    """
    from src.services.events.processor import EventProcessor

    authorization.require("events.readwrite")

    # Get delivery with event
    result = await db.execute(
        select(EventDelivery)
        .options(
            joinedload(EventDelivery.event).joinedload(Event.event_source),
            joinedload(EventDelivery.workflow),
        )
        .where(EventDelivery.id == delivery_id)
    )
    delivery = result.unique().scalar_one_or_none()

    if not delivery:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Delivery not found",
        )
    event = delivery.event
    if event is None or event.event_source is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Event not found",
        )
    await _require_visible_event_organization(
        db, authorization, event.event_source.organization_id
    )
    _require_event_mutation_boundary(authorization, event.event_source.organization_id)

    # Only retry failed deliveries
    if not can_retry_delivery_status(delivery.status):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot retry delivery with status: {delivery.status}",
        )

    # Reset delivery status to pending
    delivery.status = EventDeliveryStatus.PENDING
    delivery.error_message = None
    delivery.execution_id = None
    await db.flush()

    # Queue the execution
    processor = EventProcessor(db)
    try:
        await processor.queue_event_deliveries(delivery.event_id)
        message = "Delivery queued for retry"
    except Exception as e:
        logger.error(f"Failed to queue retry: {e}", exc_info=True)
        delivery.status = EventDeliveryStatus.FAILED
        delivery.error_message = str(e)
        await db.flush()
        message = f"Failed to queue retry: {e}"

    logger.info(f"Retried delivery {log_safe(delivery_id)}")

    return RetryDeliveryResponse(
        delivery_id=delivery_id,
        status=delivery.status.value,
        message=message,
    )
