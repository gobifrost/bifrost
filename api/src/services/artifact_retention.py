"""Retention settings and cleanup for canonical artifacts."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.contracts.artifact_retention import ArtifactRetentionSettings
from src.models.orm import Artifact, SystemConfig
from src.services.file_storage.service import get_file_storage_service

logger = logging.getLogger(__name__)

ARTIFACT_RETENTION_CONFIG_CATEGORY = "artifact_retention"
ARTIFACT_RETENTION_CONFIG_KEY = "chat"
DEFAULT_ARTIFACT_RETENTION_SETTINGS = ArtifactRetentionSettings()


class ArtifactRetentionSettingsService:
    """Persist platform-wide artifact retention policy in SystemConfig."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_settings(self) -> ArtifactRetentionSettings:
        result = await self.session.execute(
            select(SystemConfig).where(
                SystemConfig.category == ARTIFACT_RETENTION_CONFIG_CATEGORY,
                SystemConfig.key == ARTIFACT_RETENTION_CONFIG_KEY,
                SystemConfig.organization_id.is_(None),
            )
        )
        config = result.scalars().first()
        if not config or not config.value_json:
            return DEFAULT_ARTIFACT_RETENTION_SETTINGS.model_copy()
        return ArtifactRetentionSettings.model_validate(config.value_json)

    async def update_settings(
        self,
        settings: ArtifactRetentionSettings,
        *,
        updated_by: str | None,
    ) -> ArtifactRetentionSettings:
        result = await self.session.execute(
            select(SystemConfig).where(
                SystemConfig.category == ARTIFACT_RETENTION_CONFIG_CATEGORY,
                SystemConfig.key == ARTIFACT_RETENTION_CONFIG_KEY,
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
                    category=ARTIFACT_RETENTION_CONFIG_CATEGORY,
                    key=ARTIFACT_RETENTION_CONFIG_KEY,
                    value_json=value,
                    organization_id=None,
                    created_by=updated_by,
                    updated_by=updated_by,
                )
            )
        await self.session.flush()
        return settings


async def cleanup_expired_chat_artifacts(
    session: AsyncSession,
    *,
    now: datetime | None = None,
    batch_limit: int = 500,
) -> tuple[int, int]:
    """Delete expired Artifact objects and rows, cascading Chat bindings."""

    settings = await ArtifactRetentionSettingsService(session).get_settings()
    if not settings.enabled:
        return 0, 0

    cutoff = (now or datetime.now(timezone.utc)) - timedelta(
        days=settings.retention_days
    )
    result = await session.execute(
        select(Artifact)
        .where(Artifact.created_at < cutoff)
        .order_by(Artifact.created_at.asc(), Artifact.id.asc())
        .limit(batch_limit)
    )
    rows = list(result.scalars().all())
    if not rows:
        return 0, 0

    storage = get_file_storage_service(session)
    deleted = 0
    failed = 0
    for row in rows:
        try:
            await storage.delete_raw_from_s3(row.s3_key)
            await session.delete(row)
            deleted += 1
        except Exception:
            failed += 1
            logger.exception("Failed to delete expired Artifact %s", row.id)

    if deleted:
        await session.flush()
    return deleted, failed
