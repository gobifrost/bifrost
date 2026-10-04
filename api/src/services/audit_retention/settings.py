"""Platform-wide audit retention policy, persisted in SystemConfig."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.contracts.audit_retention import AuditRetentionSettings
from src.models.orm import SystemConfig

AUDIT_RETENTION_CONFIG_CATEGORY = "audit_retention"
AUDIT_RETENTION_CONFIG_KEY = "policy"
DEFAULT_AUDIT_RETENTION_SETTINGS = AuditRetentionSettings()


class AuditRetentionSettingsService:
    """Persist platform-wide audit retention policy in SystemConfig."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_settings(self) -> AuditRetentionSettings:
        result = await self.session.execute(
            select(SystemConfig).where(
                SystemConfig.category == AUDIT_RETENTION_CONFIG_CATEGORY,
                SystemConfig.key == AUDIT_RETENTION_CONFIG_KEY,
                SystemConfig.organization_id.is_(None),
            )
        )
        config = result.scalars().first()
        if not config or not config.value_json:
            return DEFAULT_AUDIT_RETENTION_SETTINGS.model_copy()
        return AuditRetentionSettings.model_validate(config.value_json)

    async def update_settings(
        self,
        settings: AuditRetentionSettings,
        *,
        updated_by: str | None,
    ) -> AuditRetentionSettings:
        result = await self.session.execute(
            select(SystemConfig).where(
                SystemConfig.category == AUDIT_RETENTION_CONFIG_CATEGORY,
                SystemConfig.key == AUDIT_RETENTION_CONFIG_KEY,
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
                    category=AUDIT_RETENTION_CONFIG_CATEGORY,
                    key=AUDIT_RETENTION_CONFIG_KEY,
                    value_json=value,
                    organization_id=None,
                    created_by=updated_by,
                    updated_by=updated_by,
                )
            )
        await self.session.flush()
        return settings
