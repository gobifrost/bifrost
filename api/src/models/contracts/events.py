"""
Event system contract models for Bifrost.

Defines request/response models for event sources, subscriptions, and deliveries.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from src.models.enums import EventSourceType, EventStatus, ScheduleOverlapPolicy


# ==================== WEBHOOK ADAPTER MODELS ====================


class WebhookAdapterInfo(BaseModel):
    """Information about an available webhook adapter."""

    name: str = Field(..., description="Unique adapter name (e.g., 'generic', 'microsoft_graph')")
    display_name: str = Field(..., description="Human-readable adapter name")
    description: str | None = Field(default=None, description="Adapter description")
    requires_integration: str | None = Field(
        default=None,
        description="Integration name required for this adapter (e.g., 'Microsoft')",
    )
    config_schema: dict[str, Any] = Field(
        default_factory=dict,
        description="JSON Schema for adapter configuration",
    )
    supports_renewal: bool = Field(
        default=False,
        description="Whether this adapter requires subscription renewal",
    )


class WebhookAdapterListResponse(BaseModel):
    """Response model for listing available webhook adapters."""

    adapters: list[WebhookAdapterInfo] = Field(
        ..., description="List of available webhook adapters"
    )


# ==================== EVENT SOURCE REQUEST MODELS ====================


class WebhookSourceConfig(BaseModel):
    """Webhook-specific configuration for creating an event source."""

    adapter_name: str | None = Field(
        default=None,
        description="Webhook adapter name (null for generic webhook)",
    )
    integration_id: UUID | None = Field(
        default=None,
        description="Integration ID for OAuth-based adapters",
    )
    config: dict[str, Any] = Field(
        default_factory=dict,
        description="Adapter-specific configuration",
    )
    rate_limit_per_minute: int | None = Field(
        default=60,
        description="Max events per window. Null disables.",
    )
    rate_limit_window_seconds: int = Field(
        default=60,
        description="Window in seconds.",
    )
    rate_limit_enabled: bool = Field(
        default=True,
        description="Per-source kill switch.",
    )


class ScheduleSourceConfig(BaseModel):
    """Schedule-specific configuration for creating an event source."""

    cron_expression: str = Field(
        ...,
        description="Cron expression for the schedule (e.g., '0 9 * * *' for daily at 9 AM)",
    )
    timezone: str = Field(
        default="UTC",
        description="Timezone for the schedule (e.g., 'America/New_York')",
    )
    enabled: bool = Field(
        default=True,
        description="Whether the schedule is enabled",
    )
    overlap_policy: ScheduleOverlapPolicy = Field(
        default=ScheduleOverlapPolicy.SKIP,
        description="Behavior when a schedule fires while a previous run is still active",
    )


class EventSourceCreate(BaseModel):
    """
    Request model for creating an event source.
    POST /api/events/sources
    """

    name: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Event source name",
    )
    source_type: EventSourceType = Field(
        ...,
        description="Event source type (webhook, schedule, topic)",
    )
    organization_id: UUID | None = Field(
        default=None,
        description="Organization ID (null for global sources)",
    )
    event_type: str | None = Field(
        default=None,
        description="Topic string for topic sources (e.g. 'user.invited'). Required when source_type='topic'.",
        max_length=100,
    )

    # Type-specific configuration
    webhook: WebhookSourceConfig | None = Field(
        default=None,
        description="Webhook configuration (required if source_type is webhook)",
    )
    schedule: ScheduleSourceConfig | None = Field(
        default=None,
        description="Schedule configuration (required if source_type is schedule)",
    )


class EventSourceUpdate(BaseModel):
    """
    Request model for updating an event source.
    PATCH /api/events/sources/{source_id}
    """

    name: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
        description="Event source name",
    )
    is_active: bool | None = Field(
        default=None,
        description="Whether the source is active",
    )
    organization_id: UUID | None = Field(
        default=None,
        description="Organization ID (null for global sources)",
    )

    # Webhook-specific updates
    webhook: WebhookSourceConfig | None = Field(
        default=None,
        description="Webhook configuration updates",
    )
    schedule: ScheduleSourceConfig | None = Field(
        default=None,
        description="Schedule configuration updates",
    )


# ==================== EVENT SUBSCRIPTION REQUEST MODELS ====================


class EventSubscriptionCreate(BaseModel):
    """
    Request model for creating an event subscription.
    POST /api/events/sources/{source_id}/subscriptions
    """

    target_type: str = Field(
        default="workflow",
        description="Target type: 'workflow' or 'agent'",
    )
    workflow_id: UUID | None = Field(
        default=None,
        description="Workflow ID (required when target_type='workflow')",
    )
    agent_id: UUID | None = Field(
        default=None,
        description="Agent ID (required when target_type='agent')",
    )
    event_type: str | None = Field(
        default=None,
        max_length=255,
        description="Optional event type filter (e.g., 'ticket.created')",
    )
    filter_expression: str | None = Field(
        default=None,
        description="Optional JSONPath filter expression (future use)",
    )
    input_mapping: dict[str, Any] | None = Field(
        default=None,
        description="Template mapping for workflow input parameters. Maps workflow params to static values or template expressions (e.g., {'report_type': 'daily', 'as_of_date': '{{ scheduled_time }}'})",
    )


class EventSubscriptionUpdate(BaseModel):
    """
    Request model for updating an event subscription.
    PATCH /api/events/sources/{source_id}/subscriptions/{subscription_id}
    """

    event_type: str | None = Field(
        default=None,
        max_length=255,
        description="Event type filter",
    )
    filter_expression: str | None = Field(
        default=None,
        description="JSONPath filter expression",
    )
    is_active: bool | None = Field(
        default=None,
        description="Whether the subscription is active",
    )
    input_mapping: dict[str, Any] | None = Field(
        default=None,
        description="Template mapping for workflow input parameters",
    )


# ==================== EVENT SOURCE RESPONSE MODELS ====================


class WebhookSourceResponse(BaseModel):
    """Webhook-specific details in event source response."""

    model_config = ConfigDict(from_attributes=True)

    adapter_name: str | None = Field(
        default=None,
        description="Webhook adapter name",
    )
    integration_id: UUID | None = Field(
        default=None,
        description="Integration ID for OAuth-based adapters",
    )
    integration_name: str | None = Field(
        default=None,
        description="Integration name (for display)",
    )
    config: dict[str, Any] = Field(
        default_factory=dict,
        description="Adapter configuration",
    )
    callback_url: str = Field(
        ...,
        description="Full callback URL for this webhook",
    )
    external_id: str | None = Field(
        default=None,
        description="External subscription ID (from external service)",
    )
    expires_at: datetime | None = Field(
        default=None,
        description="When the external subscription expires",
    )
    rate_limit_per_minute: int | None = Field(
        default=60,
        description="Max events per window. Null disables.",
    )
    rate_limit_window_seconds: int = Field(
        default=60,
        description="Window in seconds.",
    )
    rate_limit_enabled: bool = Field(
        default=True,
        description="Per-source kill switch.",
    )
    rate_limited_count_24h: int = Field(
        default=0,
        description="Number of rate-limit rejections in the last 24h (read-only).",
    )


class ScheduleSourceResponse(BaseModel):
    """Schedule-specific details in event source response."""

    model_config = ConfigDict(from_attributes=True)

    cron_expression: str = Field(
        ...,
        description="Cron expression for the schedule",
    )
    timezone: str = Field(
        ...,
        description="Timezone for the schedule",
    )
    enabled: bool = Field(
        ...,
        description="Whether the schedule is enabled",
    )
    overlap_policy: ScheduleOverlapPolicy = Field(
        default=ScheduleOverlapPolicy.SKIP,
        description="Behavior when a schedule fires while a previous run is still active",
    )


class EventSourceResponse(BaseModel):
    """
    Response model for a single event source.
    GET /api/events/sources/{source_id}
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID = Field(..., description="Event source ID")
    name: str = Field(..., description="Event source name")
    source_type: EventSourceType = Field(..., description="Source type")
    event_type: str | None = Field(
        default=None,
        description="Topic string for topic sources (e.g. 'user.invited')",
    )
    organization_id: UUID | None = Field(
        default=None,
        description="Organization ID (null for global)",
    )
    organization_name: str | None = Field(
        default=None,
        description="Organization name (for display)",
    )
    is_active: bool = Field(..., description="Whether the source is active")
    error_message: str | None = Field(
        default=None,
        description="Error message if source is in error state",
    )
    subscription_count: int = Field(
        default=0,
        description="Number of active subscriptions",
    )
    event_count_24h: int = Field(
        default=0,
        description="Number of events received in the last 24 hours",
    )
    created_by: str = Field(..., description="User who created the source")
    created_at: datetime = Field(..., description="Creation timestamp")
    updated_at: datetime = Field(..., description="Last update timestamp")

    # Type-specific details
    webhook: WebhookSourceResponse | None = Field(
        default=None,
        description="Webhook configuration details",
    )
    schedule: ScheduleSourceResponse | None = Field(
        default=None,
        description="Schedule configuration details",
    )


class EventSourceListResponse(BaseModel):
    """
    Response model for listing event sources.
    GET /api/events/sources
    """

    items: list[EventSourceResponse] = Field(
        ..., description="List of event sources"
    )
    total: int = Field(..., description="Total number of sources")


# ==================== EVENT SUBSCRIPTION RESPONSE MODELS ====================


class EventSubscriptionResponse(BaseModel):
    """
    Response model for a single event subscription.
    GET /api/events/sources/{source_id}/subscriptions/{subscription_id}
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID = Field(..., description="Subscription ID")
    event_source_id: UUID = Field(..., description="Event source ID")
    target_type: str = Field(default="workflow", description="Target type")
    workflow_id: UUID | None = Field(default=None, description="Workflow ID")
    agent_id: UUID | None = Field(default=None, description="Agent ID")
    agent_name: str | None = Field(default=None, description="Agent name (for display)")
    workflow_name: str | None = Field(
        default=None,
        description="Workflow name (for display)",
    )
    event_type: str | None = Field(
        default=None,
        description="Event type filter",
    )
    filter_expression: str | None = Field(
        default=None,
        description="JSONPath filter expression",
    )
    is_active: bool = Field(..., description="Whether the subscription is active")
    input_mapping: dict[str, Any] | None = Field(
        default=None,
        description="Template mapping for workflow input parameters",
    )
    delivery_count: int = Field(
        default=0,
        description="Total number of deliveries",
    )
    success_count: int = Field(
        default=0,
        description="Number of successful deliveries",
    )
    failed_count: int = Field(
        default=0,
        description="Number of failed deliveries",
    )
    created_by: str = Field(..., description="User who created the subscription")
    created_at: datetime = Field(..., description="Creation timestamp")
    updated_at: datetime = Field(..., description="Last update timestamp")


class EventSubscriptionListResponse(BaseModel):
    """
    Response model for listing event subscriptions.
    GET /api/events/sources/{source_id}/subscriptions
    """

    items: list[EventSubscriptionResponse] = Field(
        ..., description="List of subscriptions"
    )
    total: int = Field(..., description="Total number of subscriptions")


# ==================== EVENT RESPONSE MODELS ====================


class EventResponse(BaseModel):
    """
    Response model for a single event.
    GET /api/events/{event_id}
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID = Field(..., description="Event ID")
    event_source_id: UUID = Field(..., description="Event source ID")
    event_source_name: str | None = Field(
        default=None,
        description="Event source name (for display)",
    )
    event_type: str | None = Field(
        default=None,
        description="Event type",
    )
    received_at: datetime = Field(..., description="When the event was received")
    headers: dict[str, Any] | None = Field(
        default=None,
        description="Request headers (for webhooks)",
    )
    data: dict[str, Any] = Field(..., description="Event payload")
    source_ip: str | None = Field(
        default=None,
        description="Source IP address",
    )
    status: EventStatus = Field(..., description="Processing status")
    delivery_count: int = Field(
        default=0,
        description="Number of delivery attempts",
    )
    success_count: int = Field(
        default=0,
        description="Number of successful deliveries",
    )
    failed_count: int = Field(
        default=0,
        description="Number of failed deliveries",
    )
    created_at: datetime = Field(..., description="Creation timestamp")


class EventListResponse(BaseModel):
    """
    Response model for listing events.
    GET /api/events/sources/{source_id}/events
    """

    items: list[EventResponse] = Field(
        ..., description="List of events"
    )
    total: int = Field(..., description="Total number of events")


# ==================== EVENT DELIVERY RESPONSE MODELS ====================


class EventDeliveryResponse(BaseModel):
    """
    Response model for a single event delivery.
    GET /api/events/{event_id}/deliveries/{delivery_id}

    Note: id and created_at are nullable to support "not_delivered" entries
    for subscriptions that didn't exist when the event arrived.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID | None = Field(
        default=None,
        description="Delivery ID (null for not_delivered entries)",
    )
    event_id: UUID = Field(..., description="Event ID")
    event_subscription_id: UUID = Field(..., description="Subscription ID")
    workflow_id: UUID | None = Field(default=None, description="Workflow ID")
    workflow_name: str | None = Field(
        default=None,
        description="Workflow name (for display)",
    )
    target_type: str = Field(default="workflow", description="Target type: 'workflow' or 'agent'")
    agent_id: UUID | None = Field(default=None, description="Agent ID (when target_type='agent')")
    agent_name: str | None = Field(default=None, description="Agent name (for display)")
    execution_id: UUID | None = Field(
        default=None,
        description="Execution ID (set when execution starts)",
    )
    agent_run_id: UUID | None = Field(
        default=None,
        description="Agent run ID (set when agent run is queued)",
    )
    status: str = Field(..., description="Delivery status")
    error_message: str | None = Field(
        default=None,
        description="Error message if failed",
    )
    attempt_count: int = Field(
        default=0,
        description="Number of delivery attempts",
    )
    next_retry_at: datetime | None = Field(
        default=None,
        description="Next retry time (for future retry support)",
    )
    completed_at: datetime | None = Field(
        default=None,
        description="When delivery completed",
    )
    created_at: datetime | None = Field(
        default=None,
        description="Creation timestamp (null for not_delivered entries)",
    )


class EventDeliveryListResponse(BaseModel):
    """
    Response model for listing event deliveries.
    GET /api/events/{event_id}/deliveries
    """

    items: list[EventDeliveryResponse] = Field(
        ..., description="List of deliveries"
    )
    total: int = Field(..., description="Total number of deliveries")


# ==================== WEBHOOK RECEIVER MODELS ====================


class WebhookReceivedResponse(BaseModel):
    """
    Response model for webhook received.
    POST /hooks/{callback_path}
    """

    event_id: UUID = Field(..., description="Created event ID")
    subscriptions: int = Field(..., description="Number of subscriptions triggered")
    status: str = Field(
        default="accepted",
        description="Processing status",
    )


# ==================== RETRY MODELS ====================


class RetryDeliveryRequest(BaseModel):
    """
    Request model for retrying a failed delivery.
    POST /api/events/deliveries/{delivery_id}/retry
    """

    pass  # No parameters needed for now


class CreateDeliveryRequest(BaseModel):
    """
    Request model for creating a delivery for an existing event.
    POST /api/events/{event_id}/deliveries

    This is used to retroactively send an event to a subscription
    that was added after the event arrived.
    """

    subscription_id: UUID = Field(
        ...,
        description="Subscription ID to create delivery for",
    )


class RetryDeliveryResponse(BaseModel):
    """
    Response model for retry delivery.
    POST /api/events/deliveries/{delivery_id}/retry
    """

    delivery_id: UUID = Field(..., description="Delivery ID")
    status: str = Field(..., description="New status after retry")
    message: str = Field(..., description="Result message")


# ==================== DYNAMIC VALUES MODELS ====================


class DynamicValuesRequest(BaseModel):
    """
    Request model for fetching dynamic values for adapter config fields.
    POST /api/events/adapters/{adapter_name}/dynamic-values
    """

    operation: str = Field(
        ...,
        description="The operation name from x-dynamic-values in config_schema",
    )
    integration_id: UUID | None = Field(
        default=None,
        description="Integration ID for OAuth-based operations",
    )
    current_config: dict[str, Any] = Field(
        default_factory=dict,
        description="Config values selected so far (for dependent fields)",
    )


class DynamicValuesResponse(BaseModel):
    """
    Response model for dynamic values.
    Returns list of options for populating dropdowns.
    """

    items: list[dict[str, Any]] = Field(
        ...,
        description="List of option objects with fields matching value_path/label_path",
    )


# ==================== TOPIC EMIT MODELS ====================


class EmitEventRequest(BaseModel):
    """Request body for POST /api/events/emit."""

    topic: str = Field(
        ...,
        description="Topic string, e.g. 'user.invited'. Validated: ^[a-z0-9_.]+$, must contain a dot.",
    )
    data: dict[str, Any] = Field(
        default_factory=dict,
        description="JSON-serializable event payload.",
    )
    scope: str | None = Field(
        default=None,
        description="Organization scope: org UUID string or None for GLOBAL.",
    )


class EmitEventResponse(BaseModel):
    """Response from POST /api/events/emit."""

    event_id: str = Field(..., description="UUID of the created Event row.")
    subscribers_notified: int = Field(
        ..., description="Number of subscriptions that will receive this event."
    )


class TopicRegistryEntry(BaseModel):
    """A curated topic with a human-readable description."""

    topic: str
    description: str


class TopicsRegistryResponse(BaseModel):
    """Response from GET /api/events/topics."""

    curated: list[TopicRegistryEntry] = Field(
        ..., description="Hand-curated topics with descriptions."
    )
    in_use: list[str] = Field(
        ..., description="Distinct topic strings currently in use as EventSources."
    )
