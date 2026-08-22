"""Shared event delivery status contracts."""

from src.models.enums import EventDeliveryStatus

RETRYABLE_DELIVERY_STATUSES = frozenset(
    (EventDeliveryStatus.FAILED, EventDeliveryStatus.SKIPPED)
)


def can_retry_delivery_status(status: EventDeliveryStatus) -> bool:
    """Return whether an event delivery status is eligible for manual retry."""
    return status in RETRYABLE_DELIVERY_STATUSES
