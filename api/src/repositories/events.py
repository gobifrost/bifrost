"""
Event Repository

Database operations for the event system:
- Event sources (webhook, schedule, topic)
- Event subscriptions (link sources to workflows)
- Events (immutable log of received events)
- Event deliveries (tracking delivery to each workflow)
"""

from datetime import datetime, timedelta, timezone
from typing import Sequence
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.orm import joinedload, selectinload

from src.models.enums import EventDeliveryStatus, EventSourceType, EventStatus
from src.models.orm.events import (
    Event,
    EventDelivery,
    EventSource,
    EventSubscription,
    WebhookSource,
)
from src.repositories.base import BaseRepository


class EventSourceRepository(BaseRepository[EventSource]):
    """Repository for event source operations."""

    model = EventSource

    async def get_by_id_with_details(self, source_id: UUID) -> EventSource | None:
        """Get event source with webhook and schedule details loaded."""
        result = await self.session.execute(
            select(EventSource)
            .options(
                joinedload(EventSource.webhook_source).joinedload(
                    WebhookSource.integration
                ),
                joinedload(EventSource.schedule_source),
                joinedload(EventSource.organization),
                selectinload(EventSource.subscriptions),
            )
            .where(EventSource.id == source_id)
        )
        return result.unique().scalar_one_or_none()

    async def get_all_sources(
        self,
        source_type: EventSourceType | None = None,
        active_only: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> Sequence[EventSource]:
        """Get all event sources (no org filter)."""
        stmt = (
            select(EventSource)
            .options(
                joinedload(EventSource.webhook_source),
                joinedload(EventSource.schedule_source),
                joinedload(EventSource.organization),
            )
        )

        if active_only:
            stmt = stmt.where(EventSource.is_active.is_(True))
        if source_type:
            stmt = stmt.where(EventSource.source_type == source_type)

        stmt = stmt.order_by(EventSource.name).limit(limit).offset(offset)

        result = await self.session.execute(stmt)
        return result.unique().scalars().all()

    async def count_all_sources(
        self,
        source_type: EventSourceType | None = None,
        active_only: bool = False,
    ) -> int:
        """Count all event sources (no org filter)."""
        stmt = select(func.count(EventSource.id))

        if active_only:
            stmt = stmt.where(EventSource.is_active.is_(True))
        if source_type:
            stmt = stmt.where(EventSource.source_type == source_type)

        result = await self.session.execute(stmt)
        return result.scalar() or 0

    async def get_by_organization(
        self,
        organization_id: UUID | None,
        source_type: EventSourceType | None = None,
        include_global: bool = True,
        active_only: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> Sequence[EventSource]:
        """
        Get event sources for an organization.

        Args:
            organization_id: Organization ID (None for global only)
            source_type: Filter by source type
            include_global: Include global sources in results
            active_only: If True, only return active sources
            limit: Max results
            offset: Skip results
        """
        stmt = (
            select(EventSource)
            .options(
                joinedload(EventSource.webhook_source),
                joinedload(EventSource.schedule_source),
                joinedload(EventSource.organization),
            )
        )

        if active_only:
            stmt = stmt.where(EventSource.is_active.is_(True))

        if organization_id and include_global:
            stmt = stmt.where(
                (EventSource.organization_id == organization_id)
                | (EventSource.organization_id.is_(None))
            )
        elif organization_id:
            stmt = stmt.where(EventSource.organization_id == organization_id)
        else:
            stmt = stmt.where(EventSource.organization_id.is_(None))

        if source_type:
            stmt = stmt.where(EventSource.source_type == source_type)

        stmt = stmt.order_by(EventSource.name).limit(limit).offset(offset)

        result = await self.session.execute(stmt)
        return result.unique().scalars().all()

    async def count_by_organization(
        self,
        organization_id: UUID | None,
        source_type: EventSourceType | None = None,
        include_global: bool = True,
        active_only: bool = False,
    ) -> int:
        """Count event sources for an organization."""
        stmt = select(func.count(EventSource.id))

        if active_only:
            stmt = stmt.where(EventSource.is_active.is_(True))

        if organization_id and include_global:
            stmt = stmt.where(
                (EventSource.organization_id == organization_id)
                | (EventSource.organization_id.is_(None))
            )
        elif organization_id:
            stmt = stmt.where(EventSource.organization_id == organization_id)
        else:
            stmt = stmt.where(EventSource.organization_id.is_(None))

        if source_type:
            stmt = stmt.where(EventSource.source_type == source_type)

        result = await self.session.execute(stmt)
        return result.scalar() or 0

    async def get_by_topic(self, topic: str) -> EventSource | None:
        """Get an active topic EventSource by its event_type string."""
        result = await self.session.execute(
            select(EventSource)
            .where(EventSource.source_type == EventSourceType.TOPIC)
            .where(EventSource.event_type == topic)
            .where(EventSource.is_active.is_(True))
        )
        return result.unique().scalar_one_or_none()

    async def get_distinct_topic_types(self) -> list[str]:
        """Return distinct event_type values for all active topic sources."""
        result = await self.session.execute(
            select(EventSource.event_type)
            .where(EventSource.source_type == EventSourceType.TOPIC)
            .where(EventSource.event_type.isnot(None))
            .distinct()
            .order_by(EventSource.event_type)
        )
        return [row[0] for row in result.all()]


class WebhookSourceRepository(BaseRepository[WebhookSource]):
    """Repository for webhook source operations."""

    model = WebhookSource

    async def get_by_event_source_id(self, event_source_id: UUID) -> WebhookSource | None:
        """Get webhook source by event source ID (for routing incoming webhooks)."""
        result = await self.session.execute(
            select(WebhookSource)
            .options(
                joinedload(WebhookSource.event_source).options(
                    selectinload(EventSource.subscriptions).joinedload(
                        EventSubscription.workflow
                    ),
                    joinedload(EventSource.organization),
                ),
                joinedload(WebhookSource.integration),
            )
            .where(WebhookSource.event_source_id == event_source_id)
        )
        return result.unique().scalar_one_or_none()

    async def get_expiring_soon(
        self,
        within_hours: int = 48,
    ) -> Sequence[WebhookSource]:
        """Get webhook sources expiring within the specified hours."""
        expiry_threshold = datetime.now(timezone.utc) + timedelta(hours=within_hours)

        result = await self.session.execute(
            select(WebhookSource)
            .options(
                joinedload(WebhookSource.event_source),
                joinedload(WebhookSource.integration),
            )
            .where(WebhookSource.expires_at.isnot(None))
            .where(WebhookSource.expires_at <= expiry_threshold)
            .join(EventSource)
            .where(EventSource.is_active.is_(True))
        )
        return result.unique().scalars().all()


class EventSubscriptionRepository(BaseRepository[EventSubscription]):
    """Repository for event subscription operations."""

    model = EventSubscription

    async def get_by_source(
        self,
        source_id: UUID,
        active_only: bool = True,
    ) -> Sequence[EventSubscription]:
        """Get subscriptions for an event source."""
        stmt = (
            select(EventSubscription)
            .options(joinedload(EventSubscription.workflow, innerjoin=False))
            .where(EventSubscription.event_source_id == source_id)
        )

        if active_only:
            stmt = stmt.where(EventSubscription.is_active.is_(True))

        result = await self.session.execute(stmt)
        return result.unique().scalars().all()

    async def get_active_for_event(
        self,
        source_id: UUID,
        event_type: str | None = None,
    ) -> Sequence[EventSubscription]:
        """
        Get active subscriptions that match an event.

        Args:
            source_id: Event source ID
            event_type: Optional event type for filtering
        """
        stmt = (
            select(EventSubscription)
            .options(joinedload(EventSubscription.workflow))
            .where(EventSubscription.event_source_id == source_id)
            .where(EventSubscription.is_active.is_(True))
        )

        # Filter by event type:
        # - Subscriptions with no event_type filter (NULL) match all events
        # - Subscriptions with event_type filter only match events of that type
        # - Events with no type only match subscriptions with no filter
        if event_type:
            # Event has a type: match subscriptions with no filter OR matching filter
            stmt = stmt.where(
                (EventSubscription.event_type.is_(None))
                | (EventSubscription.event_type == event_type)
            )
        else:
            # Event has no type: only match subscriptions with no filter
            stmt = stmt.where(EventSubscription.event_type.is_(None))

        result = await self.session.execute(stmt)
        return result.unique().scalars().all()

    async def count_by_source(self, source_id: UUID, active_only: bool = True) -> int:
        """Count subscriptions for an event source."""
        stmt = select(func.count(EventSubscription.id)).where(
            EventSubscription.event_source_id == source_id
        )
        if active_only:
            stmt = stmt.where(EventSubscription.is_active.is_(True))

        result = await self.session.execute(stmt)
        return result.scalar() or 0


class EventRepository(BaseRepository[Event]):
    """Repository for event log operations."""

    model = Event

    async def get_by_id(self, event_id: UUID) -> Event | None:
        """Get event by ID with event_source and schedule_source loaded."""
        result = await self.session.execute(
            select(Event)
            .options(
                joinedload(Event.event_source).joinedload(
                    EventSource.schedule_source
                ),
            )
            .where(Event.id == event_id)
        )
        return result.unique().scalar_one_or_none()

    async def get_by_source(
        self,
        source_id: UUID,
        status: EventStatus | None = None,
        event_type: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> Sequence[Event]:
        """Get events for an event source, ordered by received_at desc."""
        stmt = (
            select(Event)
            .where(Event.event_source_id == source_id)
        )

        # Apply filters
        if status:
            stmt = stmt.where(Event.status == status)
        if event_type:
            stmt = stmt.where(Event.event_type == event_type)
        if since:
            stmt = stmt.where(Event.received_at >= since)
        if until:
            stmt = stmt.where(Event.received_at <= until)

        stmt = stmt.order_by(Event.received_at.desc()).limit(limit).offset(offset)

        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def count_by_source(
        self,
        source_id: UUID,
        status: EventStatus | None = None,
        event_type: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> int:
        """Count events for an event source."""
        stmt = select(func.count(Event.id)).where(Event.event_source_id == source_id)

        # Apply filters
        if status:
            stmt = stmt.where(Event.status == status)
        if event_type:
            stmt = stmt.where(Event.event_type == event_type)
        if since:
            stmt = stmt.where(Event.received_at >= since)
        if until:
            stmt = stmt.where(Event.received_at <= until)

        result = await self.session.execute(stmt)
        return result.scalar() or 0

    async def get_old_events(
        self,
        older_than_days: int = 30,
        limit: int = 1000,
    ) -> Sequence[Event]:
        """Get events older than specified days (for cleanup)."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=older_than_days)

        result = await self.session.execute(
            select(Event)
            .where(Event.created_at < cutoff)
            .limit(limit)
        )
        return result.scalars().all()

    async def delete_old_events(self, older_than_days: int = 30) -> int:
        """Delete events older than specified days. Returns count deleted."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=older_than_days)

        result = await self.session.execute(
            delete(Event).where(Event.created_at < cutoff)
        )
        await self.session.flush()
        return result.rowcount or 0


class EventDeliveryRepository(BaseRepository[EventDelivery]):
    """Repository for event delivery tracking."""

    model = EventDelivery

    async def get_by_event(
        self,
        event_id: UUID,
    ) -> Sequence[EventDelivery]:
        """Get deliveries for an event."""
        result = await self.session.execute(
            select(EventDelivery)
            .options(
                joinedload(EventDelivery.workflow),
                joinedload(EventDelivery.execution),
                joinedload(EventDelivery.subscription),
            )
            .where(EventDelivery.event_id == event_id)
            .order_by(EventDelivery.created_at)
        )
        return result.unique().scalars().all()

    async def get_by_subscription(
        self,
        subscription_id: UUID,
        limit: int = 100,
        offset: int = 0,
    ) -> Sequence[EventDelivery]:
        """Get deliveries for a subscription."""
        result = await self.session.execute(
            select(EventDelivery)
            .options(
                joinedload(EventDelivery.event),
                joinedload(EventDelivery.execution),
            )
            .where(EventDelivery.event_subscription_id == subscription_id)
            .order_by(EventDelivery.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return result.unique().scalars().all()

    async def count_by_subscription(
        self,
        subscription_id: UUID,
        status: EventDeliveryStatus | None = None,
    ) -> int:
        """Count deliveries for a subscription."""
        stmt = select(func.count(EventDelivery.id)).where(
            EventDelivery.event_subscription_id == subscription_id
        )
        if status:
            stmt = stmt.where(EventDelivery.status == status)

        result = await self.session.execute(stmt)
        return result.scalar() or 0

    async def update_status(
        self,
        delivery_id: UUID,
        status: EventDeliveryStatus,
        execution_id: UUID | None = None,
        error_message: str | None = None,
    ) -> EventDelivery | None:
        """Update delivery status."""
        delivery = await self.get_by_id(delivery_id)
        if not delivery:
            return None

        delivery.status = status
        delivery.attempt_count += 1

        if execution_id:
            delivery.execution_id = execution_id

        if error_message:
            delivery.error_message = error_message

        if status in (EventDeliveryStatus.SUCCESS, EventDeliveryStatus.FAILED):
            delivery.completed_at = datetime.now(timezone.utc)

        await self.session.flush()
        await self.session.refresh(delivery)
        return delivery

    async def get_pending_for_retry(self, limit: int = 100) -> Sequence[EventDelivery]:
        """Get failed deliveries pending retry (for future use)."""
        result = await self.session.execute(
            select(EventDelivery)
            .options(
                joinedload(EventDelivery.event),
                joinedload(EventDelivery.subscription),
            )
            .where(EventDelivery.status == EventDeliveryStatus.FAILED)
            .where(EventDelivery.next_retry_at.isnot(None))
            .where(EventDelivery.next_retry_at <= datetime.now(timezone.utc))
            .limit(limit)
        )
        return result.unique().scalars().all()

    async def update_event_status(self, event_id: UUID) -> None:
        """
        Update event status based on delivery statuses.

        Sets event to:
        - COMPLETED if all deliveries succeeded
        - FAILED if any delivery failed
        - PROCESSING if any delivery is pending
        """
        event = await self.session.get(Event, event_id)
        if not event:
            return

        # Count deliveries by status
        count_result = await self.session.execute(
            select(EventDelivery.status, func.count(EventDelivery.id))
            .where(EventDelivery.event_id == event_id)
            .group_by(EventDelivery.status)
        )
        status_counts = dict(count_result.all())

        pending = status_counts.get(EventDeliveryStatus.PENDING, 0)
        queued = status_counts.get(EventDeliveryStatus.QUEUED, 0)
        failed = status_counts.get(EventDeliveryStatus.FAILED, 0)
        success = status_counts.get(EventDeliveryStatus.SUCCESS, 0)

        if pending > 0 or queued > 0:
            event.status = EventStatus.PROCESSING
        elif failed > 0:
            event.status = EventStatus.FAILED
        elif success > 0:
            event.status = EventStatus.COMPLETED
        # else: keep current status

        await self.session.flush()

    async def get_stuck_deliveries(self, timeout_minutes: int) -> Sequence[EventDelivery]:
        """
        Get deliveries stuck in PENDING or QUEUED status past timeout.

        A delivery is considered stuck if it's been in PENDING or QUEUED status
        for longer than timeout_minutes without completing. This catches:
        - PENDING: Processor crashed before queueing to RabbitMQ
        - QUEUED: Worker crashed or message was lost

        Args:
            timeout_minutes: Minutes after which a delivery is considered stuck

        Returns:
            List of stuck EventDelivery records
        """
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=timeout_minutes)

        result = await self.session.execute(
            select(EventDelivery)
            .options(
                joinedload(EventDelivery.event),
                joinedload(EventDelivery.workflow),
            )
            .where(
                EventDelivery.status.in_([
                    EventDeliveryStatus.PENDING,
                    EventDeliveryStatus.QUEUED,
                ])
            )
            .where(EventDelivery.created_at < cutoff)
        )
        return result.unique().scalars().all()
