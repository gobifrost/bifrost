"""Minimal CLI-side mirror of event source / subscription DTOs."""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from bifrost.contracts.enums import EventSourceType, ScheduleOverlapPolicy


class WebhookSourceConfig(BaseModel):
    """Webhook-specific configuration (CLI mirror)."""

    adapter_name: str | None = Field(default=None)
    integration_id: UUID | None = Field(default=None)
    config: dict[str, Any] = Field(default_factory=dict)
    rate_limit_per_minute: int | None = Field(default=60, ge=1)
    rate_limit_window_seconds: int = Field(default=60, ge=1)
    rate_limit_enabled: bool = Field(default=True)


class ScheduleSourceConfig(BaseModel):
    """Schedule-specific configuration (CLI mirror)."""

    cron_expression: str
    timezone: str = Field(default="UTC")
    enabled: bool = Field(default=True)
    overlap_policy: ScheduleOverlapPolicy = Field(
        default=ScheduleOverlapPolicy.SKIP
    )


class ScheduleSourceUpdate(BaseModel):
    """Partial schedule configuration (CLI mirror)."""

    cron_expression: str | None = Field(default=None)
    timezone: str | None = Field(default=None)
    enabled: bool | None = Field(default=None)
    overlap_policy: ScheduleOverlapPolicy | None = Field(default=None)


class EventSourceCreate(BaseModel):
    """Request model for creating an event source (CLI mirror)."""

    name: str = Field(min_length=1, max_length=255)
    source_type: EventSourceType
    organization_id: UUID | None = Field(default=None)
    event_type: str | None = Field(default=None, max_length=100)
    webhook: WebhookSourceConfig | None = Field(default=None)
    schedule: ScheduleSourceConfig | None = Field(default=None)


class EventSourceUpdate(BaseModel):
    """Request model for updating an event source (CLI mirror)."""

    name: str | None = Field(default=None, min_length=1, max_length=255)
    is_active: bool | None = Field(default=None)
    organization_id: UUID | None = Field(default=None)
    webhook: WebhookSourceConfig | None = Field(default=None)
    schedule: ScheduleSourceUpdate | None = Field(default=None)


class EventSubscriptionCreate(BaseModel):
    """Request model for creating an event subscription (CLI mirror)."""

    target_type: Literal["workflow", "agent"] = Field(default="workflow")
    workflow_id: UUID | None = Field(default=None)
    agent_id: UUID | None = Field(default=None)
    event_type: str | None = Field(default=None, max_length=255)
    filter_expression: str | None = Field(default=None)
    input_mapping: dict[str, Any] | None = Field(default=None)


class EventSubscriptionUpdate(BaseModel):
    """Request model for updating an event subscription (CLI mirror)."""

    event_type: str | None = Field(default=None, max_length=255)
    filter_expression: str | None = Field(default=None)
    is_active: bool | None = Field(default=None)
    input_mapping: dict[str, Any] | None = Field(default=None)
