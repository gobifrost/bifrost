"""Unit coverage for automatic App SDK upgrade settings storage."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from shared.app_sdk_upgrade_settings import (
    APP_SDK_UPGRADE_CONFIG_CATEGORY,
    APP_SDK_UPGRADE_CONFIG_KEY,
    AppSdkUpgradeSettingsService,
)
from shared.models import AppSdkUpgradeSettings
from src.models.orm import SystemConfig


def _execute_result(row: SystemConfig | None = None) -> MagicMock:
    result = MagicMock()
    result.scalars.return_value.first.return_value = row
    return result


@pytest.mark.asyncio
async def test_missing_setting_defaults_to_automatic_upgrades_disabled() -> None:
    db = MagicMock()
    db.execute = AsyncMock(return_value=_execute_result())

    assert await AppSdkUpgradeSettingsService(db).get_settings() == AppSdkUpgradeSettings(
        enabled=False
    )


@pytest.mark.asyncio
async def test_update_creates_global_system_config_for_automatic_upgrades() -> None:
    db = MagicMock()
    db.execute = AsyncMock(return_value=_execute_result())
    db.add = MagicMock()
    db.flush = AsyncMock()

    saved = await AppSdkUpgradeSettingsService(db).update_settings(
        AppSdkUpgradeSettings(enabled=True), updated_by="admin@example.com"
    )

    assert saved.enabled is True
    config = db.add.call_args.args[0]
    assert isinstance(config, SystemConfig)
    assert config.category == APP_SDK_UPGRADE_CONFIG_CATEGORY
    assert config.key == APP_SDK_UPGRADE_CONFIG_KEY
    assert config.value_json == {"enabled": True}
    assert config.organization_id is None
    assert config.created_by == "admin@example.com"
    assert config.updated_by == "admin@example.com"
    db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_update_replaces_existing_global_setting() -> None:
    config = SystemConfig(
        category=APP_SDK_UPGRADE_CONFIG_CATEGORY,
        key=APP_SDK_UPGRADE_CONFIG_KEY,
        value_json={"enabled": False},
        organization_id=None,
    )
    db = MagicMock()
    db.execute = AsyncMock(return_value=_execute_result(config))
    db.add = MagicMock()
    db.flush = AsyncMock()

    await AppSdkUpgradeSettingsService(db).update_settings(
        AppSdkUpgradeSettings(enabled=True), updated_by="admin@example.com"
    )

    assert config.value_json == {"enabled": True}
    assert config.updated_by == "admin@example.com"
    db.add.assert_not_called()
    db.flush.assert_awaited_once()
