"""App source writes use the canonical Apps write authority helper."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from src.routers.app_code_files import get_application_for_write_or_404


@pytest.mark.asyncio
async def test_source_write_delegates_to_actual_app_organization_authority() -> None:
    app_id = uuid4()
    application = SimpleNamespace(id=app_id, organization_id=uuid4())
    ctx = SimpleNamespace()

    with patch(
        "src.routers.app_code_files._get_application_for_write_or_404",
        new=AsyncMock(return_value=application),
    ) as get_for_write:
        result = await get_application_for_write_or_404(ctx, app_id)

    assert result is application
    get_for_write.assert_awaited_once_with(ctx, app_id)
