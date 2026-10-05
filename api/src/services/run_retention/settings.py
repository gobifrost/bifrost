"""Platform-wide run retention policy, persisted in SystemConfig."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.contracts.run_retention import RunRetentionSettings
from src.models.orm import SystemConfig

RUN_RETENTION_CONFIG_CATEGORY = "run_retention"
RUN_RETENTION_CONFIG_KEY = "policy"
DEFAULT_RUN_RETENTION_SETTINGS = RunRetentionSettings()


class RunRetentionSettingsService:
    """Persist platform-wide run retention policy in SystemConfig."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_settings(self) -> RunRetentionSettings:
        result = await self.session.execute(
            select(SystemConfig).where(
                SystemConfig.category == RUN_RETENTION_CONFIG_CATEGORY,
                SystemConfig.key == RUN_RETENTION_CONFIG_KEY,
                SystemConfig.organization_id.is_(None),
            )
        )
        config = result.scalars().first()
        if not config or not config.value_json:
            return DEFAULT_RUN_RETENTION_SETTINGS.model_copy()
        return RunRetentionSettings.model_validate(config.value_json)

    async def update_settings(
        self,
        settings: RunRetentionSettings,
        *,
        updated_by: str | None,
    ) -> RunRetentionSettings:
        result = await self.session.execute(
            select(SystemConfig).where(
                SystemConfig.category == RUN_RETENTION_CONFIG_CATEGORY,
                SystemConfig.key == RUN_RETENTION_CONFIG_KEY,
                SystemConfig.organization_id.is_(None),
            )
        )
        config = result.scalars().first()
        now = datetime.now(timezone.utc)
        value = settings.model_dump()
        if config:
            config.value_json = value
            config.updated_at = now
            config.updated_by = updated_by
        else:
            self.session.add(
                SystemConfig(
                    id=uuid4(),
                    category=RUN_RETENTION_CONFIG_CATEGORY,
                    key=RUN_RETENTION_CONFIG_KEY,
                    value_json=value,
                    organization_id=None,
                    created_by=updated_by,
                    updated_by=updated_by,
                )
            )
        await self.session.flush()
        return settings


async def run_not_found_suffix(db: AsyncSession) -> str:
    """The retention sentence a missing-run 404 ends with; empty when runs are kept forever."""
    days = (await RunRetentionSettingsService(db).get_settings()).days
    return f" Finished runs are removed after {days} days." if days is not None else ""
