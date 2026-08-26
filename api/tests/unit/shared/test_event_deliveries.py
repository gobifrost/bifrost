from shared.event_deliveries import can_retry_delivery_status
from src.models.enums import EventDeliveryStatus


def test_only_failed_delivery_status_is_retryable() -> None:
    assert can_retry_delivery_status(EventDeliveryStatus.FAILED) is True
    assert can_retry_delivery_status(EventDeliveryStatus.SKIPPED) is False
    assert can_retry_delivery_status(EventDeliveryStatus.PENDING) is False
    assert can_retry_delivery_status(EventDeliveryStatus.QUEUED) is False
    assert can_retry_delivery_status(EventDeliveryStatus.SUCCESS) is False
