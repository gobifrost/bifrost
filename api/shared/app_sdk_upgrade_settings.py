"""Platform-wide settings for automatic App SDK upgrades."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.models import AppSdkUpgradeSettings
from src.models.orm import SystemConfig

APP_SDK_UPGRADE_CONFIG_CATEGORY = "app_sdk_upgrades"
APP_SDK_UPGRADE_CONFIG_KEY = "automatic"
DEFAULT_APP_SDK_UPGRADE_SETTINGS = AppSdkUpgradeSettings()


class AppSdkUpgradeSettingsService:
    """Persist automatic App SDK upgrade policy in SystemConfig."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_settings(self) -> AppSdkUpgradeSettings:
        result = await self.session.execute(
            select(SystemConfig).where(
                SystemConfig.category == APP_SDK_UPGRADE_CONFIG_CATEGORY,
                SystemConfig.key == APP_SDK_UPGRADE_CONFIG_KEY,
                SystemConfig.organization_id.is_(None),
            )
        )
        config = result.scalars().first()
        if not config or not config.value_json:
            return DEFAULT_APP_SDK_UPGRADE_SETTINGS.model_copy()
        return AppSdkUpgradeSettings.model_validate(config.value_json)

    async def update_settings(
        self,
        settings: AppSdkUpgradeSettings,
        *,
        updated_by: str | None,
    ) -> AppSdkUpgradeSettings:
        result = await self.session.execute(
            select(SystemConfig).where(
                SystemConfig.category == APP_SDK_UPGRADE_CONFIG_CATEGORY,
                SystemConfig.key == APP_SDK_UPGRADE_CONFIG_KEY,
                SystemConfig.organization_id.is_(None),
            )
        )
        config = result.scalars().first()
        value = settings.model_dump()
        if config:
            config.value_json = value
            config.updated_at = datetime.now(timezone.utc)
            config.updated_by = updated_by
        else:
            self.session.add(
                SystemConfig(
                    id=uuid4(),
                    category=APP_SDK_UPGRADE_CONFIG_CATEGORY,
                    key=APP_SDK_UPGRADE_CONFIG_KEY,
                    value_json=value,
                    organization_id=None,
                    created_by=updated_by,
                    updated_by=updated_by,
                )
            )
        await self.session.flush()
        return settings
