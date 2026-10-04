"""Audit retention settings contract and service."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from src.models.contracts.audit_retention import AuditRetentionSettings, AuditRetentionSettingsUpdate
from src.models.orm import SystemConfig
from src.services.audit_retention.settings import AuditRetentionSettingsService


def _execute_result(*rows):
    result = MagicMock()
    result.scalars.return_value.first.return_value = rows[0] if rows else None
    return result


@pytest.mark.asyncio
async def test_missing_row_means_new_install_defaults() -> None:
    db = MagicMock()
    db.execute = AsyncMock(return_value=_execute_result())
    assert await AuditRetentionSettingsService(db).get_settings() == AuditRetentionSettings(
        hot_days=90, archive_days=365
    )


@pytest.mark.asyncio
async def test_stored_unlimited_archive_round_trips() -> None:
    row = SystemConfig(
        category="audit_retention",
        key="policy",
        value_json={"hot_days": 90, "archive_days": None},
    )
    db = MagicMock()
    db.execute = AsyncMock(return_value=_execute_result(row))
    assert (await AuditRetentionSettingsService(db).get_settings()).archive_days is None


def test_archive_window_cannot_be_shorter_than_database_window() -> None:
    with pytest.raises(ValidationError):
        AuditRetentionSettings(hot_days=90, archive_days=30)
    assert AuditRetentionSettings(hot_days=90, archive_days=90).archive_days == 90
    assert AuditRetentionSettings(hot_days=1, archive_days=None).archive_days is None
    with pytest.raises(ValidationError):
        AuditRetentionSettings(hot_days=0, archive_days=None)


def test_update_requires_both_fields_and_the_same_window_rule() -> None:
    with pytest.raises(ValidationError):
        AuditRetentionSettingsUpdate.model_validate({"hot_days": 90})
    with pytest.raises(ValidationError):
        AuditRetentionSettingsUpdate(hot_days=90, archive_days=30)
    assert AuditRetentionSettingsUpdate(hot_days=90, archive_days=None).archive_days is None
