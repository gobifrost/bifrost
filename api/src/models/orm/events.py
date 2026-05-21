"""
Event system ORM models.

Represents event sources (webhooks, schedules), subscriptions, events, and deliveries.
"""

from datetime import datetime, timezone
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import ENUM as PgEnum, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.enums import EventDeliveryStatus, EventSourceType, EventStatus, ScheduleOverlapPolicy
from src.models.orm.base import Base

if TYPE_CHECKING:
    from src.models.orm.executions import Execution
    from src.models.orm.integrations import Integration
    from src.models.orm.organizations import Organization
    from src.models.orm.workflows import Workflow


class EventSource(Base):
    """
    Event source registry.

    Represents a source of events that can trigger workflows.
    Can be a webhook endpoint, a schedule, or an internal event.
    """

    __tablename__ = "event_sources"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    source_type: Mapped[EventSourceType] = mapped_column(
        PgEnum(
            "webhook",
            "schedule",
            "topic",
            name="event_source_type",
            create_type=False,
        ),
        nullable=False,
    )

    # For topic sources: the validated topic string (e.g. "user.invited")
    event_type: Mapped[str | None] = mapped_column(String(100), default=None)

    # Scope: NULL = global, otherwise org-specific
    organization_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), default=None
    )

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    error_message: Mapped[str | None] = mapped_column(Text, default=None)

    # Audit
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), server_default=text("NOW()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=text("NOW()"),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    organization: Mapped["Organization | None"] = relationship(lazy="joined")
    webhook_source: Mapped["WebhookSource | None"] = relationship(
        back_populates="event_source",
        uselist=False,
        cascade="all, delete-orphan",
    )
    schedule_source: Mapped["ScheduleSource | None"] = relationship(
        back_populates="event_source",
        uselist=False,
        cascade="all, delete-orphan",
    )
    subscriptions: Mapped[list["EventSubscription"]] = relationship(
        back_populates="event_source",
        cascade="all, delete-orphan",
    )
    events: Mapped[list["Event"]] = relationship(
        back_populates="event_source",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("ix_event_sources_organization_id", "organization_id"),
        Index("ix_event_sources_source_type", "source_type"),
        Index("ix_event_sources_is_active", "is_active"),
    )


class ScheduleSource(Base):
    """
    Schedule-specific configuration for an event source.

    Contains cron expression and timezone for scheduled triggers.
    When a schedule fires, it creates an Event record and triggers
    all subscribed workflows via EventSubscription.
    """

    __tablename__ = "schedule_sources"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    event_source_id: Mapped[UUID] = mapped_column(
        ForeignKey("event_sources.id", ondelete="CASCADE", onupdate="CASCADE"), nullable=False, unique=True
    )

    # Schedule configuration
    cron_expression: Mapped[str] = mapped_column(String(100), nullable=False)
    timezone: Mapped[str] = mapped_column(String(50), default="UTC", nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    overlap_policy: Mapped[ScheduleOverlapPolicy] = mapped_column(
        Enum(
            ScheduleOverlapPolicy,
            name="schedule_overlap_policy",
            values_callable=lambda enum: [member.value for member in enum],
            create_type=False,
        ),
        default=ScheduleOverlapPolicy.SKIP,
        server_default="skip",
        nullable=False,
    )

    # Audit
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), server_default=text("NOW()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=text("NOW()"),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    event_source: Mapped["EventSource"] = relationship(back_populates="schedule_source")

    __table_args__ = (
        Index("ix_schedule_sources_event_source_id", "event_source_id"),
        Index("ix_schedule_sources_enabled", "enabled"),
    )


class WebhookSource(Base):
    """
    Webhook-specific configuration for an event source.

    Contains adapter configuration, external subscription state,
    and callback URL information.
    """

    __tablename__ = "webhook_sources"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    event_source_id: Mapped[UUID] = mapped_column(
        ForeignKey("event_sources.id", ondelete="CASCADE", onupdate="CASCADE"), nullable=False, unique=True
    )

    # Adapter configuration
    adapter_name: Mapped[str | None] = mapped_column(
        String(100), default=None
    )  # NULL = generic
    integration_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("integrations.id", ondelete="SET NULL", onupdate="CASCADE"), default=None
    )
    config: Mapped[dict] = mapped_column(JSONB, default=dict)

    # External subscription state (managed by adapter)
    external_id: Mapped[str | None] = mapped_column(
        String(255), default=None
    )  # Subscription ID from external service
    state: Mapped[dict] = mapped_column(
        JSONB, default=dict
    )  # Adapter-managed (secrets, tokens)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    # Audit
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), server_default=text("NOW()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=text("NOW()"),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # Rate limiting
    rate_limit_per_minute: Mapped[int | None] = mapped_column(Integer, default=60, nullable=True)
    rate_limit_window_seconds: Mapped[int] = mapped_column(Integer, default=60, nullable=False)
    rate_limit_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Relationships
    event_source: Mapped["EventSource"] = relationship(back_populates="webhook_source")
    integration: Mapped["Integration | None"] = relationship(lazy="joined")

    __table_args__ = (
        Index("ix_webhook_sources_adapter_name", "adapter_name"),
        Index(
            "ix_webhook_sources_expires_at",
            "expires_at",
            postgresql_where=text("expires_at IS NOT NULL"),
        ),
    )


class EventSubscription(Base):
    """
    Subscription linking an event source to a workflow.

    When an event arrives at a source, all active subscriptions
    are evaluated and matching workflows are triggered.
    """

    __tablename__ = "event_subscriptions"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    event_source_id: Mapped[UUID] = mapped_column(
        ForeignKey("event_sources.id", ondelete="CASCADE", onupdate="CASCADE"), nullable=False
    )
    workflow_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("workflows.id", ondelete="CASCADE", onupdate="CASCADE"), nullable=True
    )
    target_type: Mapped[str] = mapped_column(String(50), nullable=False, default="workflow")
    agent_id: Mapped[UUID | None] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), default=None)

    # Optional filtering
    event_type: Mapped[str | None] = mapped_column(
        String(255), default=None
    )  # e.g., "ticket.created"
    filter_expression: Mapped[str | None] = mapped_column(
        Text, default=None
    )  # JSONPath or simple expression (future)

    # Input mapping for workflow parameters
    # Maps event payload fields to workflow inputs
    # Example: {"report_type": "daily", "as_of_date": "{{ scheduled_time }}"}
    input_mapping: Mapped[dict | None] = mapped_column(JSONB, default=None)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    # Audit
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), server_default=text("NOW()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=text("NOW()"),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    event_source: Mapped["EventSource | None"] = relationship(back_populates="subscriptions")
    workflow: Mapped["Workflow | None"] = relationship(lazy="joined", foreign_keys=[workflow_id])
    agent = relationship("Agent", lazy="joined", foreign_keys=[agent_id])
    deliveries: Mapped[list["EventDelivery"]] = relationship(
        back_populates="subscription",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("ix_event_subscriptions_event_source_id", "event_source_id"),
        Index("ix_event_subscriptions_workflow_id", "workflow_id"),
        Index("ix_event_subscriptions_is_active", "is_active"),
        # Note: unique constraint on (event_source_id, workflow_id) removed
        # since workflow_id is now nullable (agent targets don't have workflow_id)
        Index("ix_event_subscriptions_agent_id", "agent_id"),
    )


class Event(Base):
    """
    Immutable event log.

    Records every event received from an event source.
    """

    __tablename__ = "events"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    event_source_id: Mapped[UUID] = mapped_column(
        ForeignKey("event_sources.id", ondelete="CASCADE", onupdate="CASCADE"), nullable=False
    )

    # Organization that owns this event (stamped at emit time)
    organization_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="SET NULL"), default=None
    )

    # Event metadata
    event_type: Mapped[str | None] = mapped_column(
        String(255), default=None
    )  # e.g., "ticket.created"
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), server_default=text("NOW()")
    )

    # Payload
    headers: Mapped[dict | None] = mapped_column(JSONB, default=None)
    data: Mapped[dict] = mapped_column(JSONB, nullable=False)

    # Source metadata
    source_ip: Mapped[str | None] = mapped_column(String(45), default=None)  # IPv6 max

    # Processing status
    status: Mapped[EventStatus] = mapped_column(
        PgEnum(
            "received",
            "processing",
            "completed",
            "failed",
            name="event_status",
            create_type=False,
        ),
        default=EventStatus.RECEIVED,
    )

    # Audit
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), server_default=text("NOW()")
    )

    # Relationships
    event_source: Mapped["EventSource | None"] = relationship(back_populates="events")
    deliveries: Mapped[list["EventDelivery"]] = relationship(
        back_populates="event",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("ix_events_event_source_id", "event_source_id"),
        Index("ix_events_received_at", "received_at"),
        Index("ix_events_status", "status"),
        Index("ix_events_event_type", "event_type"),
        # For cleanup job: events older than 30 days
        Index(
            "ix_events_created_at",
            "created_at",
        ),
    )


class EventDelivery(Base):
    """
    Delivery tracking for an event to a specific subscription.

    Tracks the status of delivering an event to a workflow,
    including execution results and retry information.
    """

    __tablename__ = "event_deliveries"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    event_id: Mapped[UUID] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), nullable=False
    )
    event_subscription_id: Mapped[UUID] = mapped_column(
        ForeignKey("event_subscriptions.id", ondelete="CASCADE"), nullable=False
    )
    workflow_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("workflows.id", ondelete="CASCADE", onupdate="CASCADE"), nullable=True
    )

    # Execution reference (set when queued, before Execution record exists)
    # No FK constraint - Execution is created asynchronously by worker
    execution_id: Mapped[UUID | None] = mapped_column(default=None)

    # Agent run reference (set when agent run is queued)
    agent_run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="SET NULL"), default=None
    )

    # Delivery status
    status: Mapped[EventDeliveryStatus] = mapped_column(
        PgEnum(
            "pending",
            "queued",
            "success",
            "failed",
            "skipped",
            name="event_delivery_status",
            create_type=False,
        ),
        default=EventDeliveryStatus.PENDING,
    )
    error_message: Mapped[str | None] = mapped_column(Text, default=None)

    # Retry tracking (for future use)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    # Audit
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), server_default=text("NOW()")
    )

    # Relationships
    event: Mapped["Event"] = relationship(back_populates="deliveries")
    subscription: Mapped["EventSubscription"] = relationship(back_populates="deliveries")
    workflow: Mapped["Workflow"] = relationship(lazy="joined")
    # No FK constraint on execution_id, so specify foreign_keys explicitly
    execution: Mapped["Execution | None"] = relationship(
        lazy="joined",
        foreign_keys=[execution_id],
        primaryjoin="EventDelivery.execution_id == Execution.id",
    )
    agent_run = relationship("AgentRun", lazy="joined", foreign_keys=[agent_run_id])

    __table_args__ = (
        Index("ix_event_deliveries_event_id", "event_id"),
        Index("ix_event_deliveries_subscription_id", "event_subscription_id"),
        Index("ix_event_deliveries_workflow_id", "workflow_id"),
        Index("ix_event_deliveries_execution_id", "execution_id"),
        Index("ix_event_deliveries_agent_run_id", "agent_run_id"),
        Index("ix_event_deliveries_status", "status"),
        # For cleanup job
        Index("ix_event_deliveries_created_at", "created_at"),
    )
