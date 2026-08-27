from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from src.models.enums import EventDeliveryStatus, EventStatus
from src.repositories.events import EventDeliveryRepository


@pytest.mark.asyncio
async def test_update_event_status_completes_when_all_deliveries_are_skipped() -> None:
    session = AsyncMock()
    event = MagicMock()
    event.status = EventStatus.PROCESSING
    session.get.return_value = event

    count_result = MagicMock()
    count_result.all.return_value = [(EventDeliveryStatus.SKIPPED, 1)]
    session.execute.return_value = count_result

    await EventDeliveryRepository(session).update_event_status(uuid4())

    assert event.status == EventStatus.COMPLETED
    session.flush.assert_awaited_once()
