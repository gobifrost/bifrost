"""RunRetentionSettingsService persistence and the missing-run 404 sentence."""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.contracts.run_retention import RunRetentionSettings
from src.services.run_retention.settings import RunRetentionSettingsService, run_not_found_suffix

pytestmark = pytest.mark.e2e


@pytest.mark.asyncio
async def test_missing_row_means_thirty_days_and_update_round_trips(db_session: AsyncSession) -> None:
    await db_session.execute(text("DELETE FROM system_configs WHERE category = 'run_retention'"))
    service = RunRetentionSettingsService(db_session)

    assert (await service.get_settings()).days == 30
    assert await run_not_found_suffix(db_session) == " Finished runs are removed after 30 days."

    await service.update_settings(RunRetentionSettings(days=90), updated_by="synthetic-admin")
    assert (await service.get_settings()).days == 90

    await service.update_settings(RunRetentionSettings(days=None), updated_by="synthetic-admin")
    assert (await service.get_settings()).days is None
    assert await run_not_found_suffix(db_session) == ""
